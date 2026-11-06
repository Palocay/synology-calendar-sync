"""Birthdays from Synology Contacts (CardDAV) -> Synology Calendar."""
from __future__ import annotations

import hashlib
import logging
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date, datetime

import requests
import vobject
from requests.auth import HTTPBasicAuth

from .common import (
    APP_VERSION, TZ, Reminder, Settings, env_bool, env_int, env_str, make_allday_ical,
    parse_reminders, sync_allday,
)

PREFIX = "synology-calendar-sync:birthday:"
DESCRIPTION = "Synced automatically from Synology Contacts."
DAV_NS = "DAV:"
CARD_NS = "urn:ietf:params:xml:ns:carddav"

log = logging.getLogger("birthdays")


# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class BirthdayConfig:
    carddav_url: str
    calendar: str
    years_ahead: int
    reminders: tuple[Reminder, ...]
    interval: str


def load_config() -> BirthdayConfig | None:
    """None if the birthday sync is disabled (no CARDDAV_URL or BIRTHDAYS=false)."""
    carddav_url = env_str("CARDDAV_URL")
    if not carddav_url or not env_bool("BIRTHDAYS", True):
        return None
    return BirthdayConfig(
        carddav_url=carddav_url,
        calendar=env_str("BIRTHDAY_CALENDAR", default="Birthdays"),
        years_ahead=env_int("BIRTHDAY_YEARS_AHEAD", 10, 1, 20),
        reminders=parse_reminders(env_str("BIRTHDAY_REMINDER"), "BIRTHDAY_REMINDER"),
        interval=env_str("BIRTHDAY_INTERVAL", "SCHEDULE_INTERVAL", default="24h"),
    )


# --------------------------------------------------------------------------- #
# Contacts
# --------------------------------------------------------------------------- #
def parse_vcard_date(value):
    """Return (month, day, year_or_None) from common vCard date forms."""
    if value is None:
        return None
    if isinstance(value, datetime):
        value = value.date()
    if isinstance(value, date):
        return value.month, value.day, value.year

    raw = str(value).strip()
    if not raw:
        return None

    # YYYY-MM-DD, YYYYMMDD (optionally with a time suffix).
    m = re.match(r"^(\d{4})-?(\d{2})-?(\d{2})", raw)
    if m:
        year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if not (1 <= month <= 12 and 1 <= day <= 31):
            log.warning("Skipping invalid date: %r", raw)
            return None
        # Apple/iOS often stores "year unknown" as 1604.
        return month, day, (None if year <= 1604 else year)

    # Without a year: --MM-DD, --MMDD.
    m = re.fullmatch(r"--(\d{2})-?(\d{2})", raw)
    if m:
        return int(m.group(1)), int(m.group(2)), None

    log.warning("Skipping unknown date format: %r", raw)
    return None


def contact_digest(vcard, full_name: str) -> str:
    try:
        raw_uid = str(vcard.uid.value).strip()
    except Exception:
        raw_uid = ""
    return hashlib.sha256((raw_uid or full_name).encode("utf-8")).hexdigest()[:24]


def get_contact_name(vcard) -> str:
    try:
        fn = str(vcard.fn.value).strip()
        if fn:
            return fn
    except Exception:
        pass
    try:
        n = vcard.n.value
        parts = [str(getattr(n, f, "")).strip() for f in ("given", "family")]
        name = " ".join(p for p in parts if p)
        if name:
            return name
    except Exception:
        pass
    return "Unnamed contact"


def fetch_vcards(url: str, settings: Settings):
    body = f"""<?xml version="1.0" encoding="utf-8" ?>
    <card:addressbook-query xmlns:d="{DAV_NS}" xmlns:card="{CARD_NS}">
      <d:prop><d:getetag/><card:address-data/></d:prop>
    </card:addressbook-query>"""
    response = requests.request(
        "REPORT", url,
        data=body.encode("utf-8"),
        headers={
            "Depth": "1",
            "Content-Type": "application/xml; charset=utf-8",
            "Accept": "application/xml",
            "User-Agent": f"synology-calendar-sync/{APP_VERSION}",
        },
        auth=HTTPBasicAuth(settings.username, settings.password),
        timeout=45,
        verify=settings.verify_tls,
    )
    if response.status_code not in (200, 207):
        raise RuntimeError(f"CardDAV REPORT failed: HTTP {response.status_code}: {response.text[:500]}")

    vcards = []
    for node in ET.fromstring(response.content).iter():
        if (node.tag.endswith("}address-data") or node.tag == "address-data") and (node.text or "").strip():
            try:
                vcards.append(vobject.readOne(node.text))
            except Exception as exc:
                log.warning("Could not parse a vCard: %s", exc)
    return vcards


# --------------------------------------------------------------------------- #
# Events
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Birthday:
    series_uid: str      # event UID without the year
    name: str
    month: int
    day: int
    birth_year: int | None


@dataclass(frozen=True)
class BirthdayEvent:
    uid: str
    summary: str
    start: date
    reminders: tuple[Reminder, ...]


def date_for_year(month: int, day: int, year: int) -> date:
    # 29 February is observed on 28 February in non-leap years.
    try:
        return date(year, month, day)
    except ValueError:
        if month == 2 and day == 29:
            return date(year, 2, 28)
        raise


def make_title(name: str, age: int | None) -> str:
    return f"🎂 {name} ({age})" if age is not None else f"🎂 {name}"


def birthday_from_vcard(vcard) -> Birthday | None:
    prop = getattr(vcard, "bday", None)
    parsed = parse_vcard_date(getattr(prop, "value", None))
    if not parsed:
        return None
    name = get_contact_name(vcard)
    return Birthday(f"{PREFIX}{contact_digest(vcard, name)}", name, *parsed)


def birthday_events(bday: Birthday, years: range, reminders=()) -> list[BirthdayEvent]:
    events = []
    for year in years:
        # No events before the year of birth.
        if bday.birth_year and year < bday.birth_year:
            continue
        age = year - bday.birth_year if bday.birth_year else None
        events.append(BirthdayEvent(
            uid=f"{bday.series_uid}:{year}",
            summary=make_title(bday.name, age),
            start=date_for_year(bday.month, bday.day, year),
            reminders=tuple(reminders),
        ))
    return events


def make_ical(ev: BirthdayEvent, created: datetime | None = None) -> str:
    return make_allday_ical(ev.uid, ev.summary, ev.start, "Birthday", DESCRIPTION,
                            ev.reminders, created)


# --------------------------------------------------------------------------- #
# Run
# --------------------------------------------------------------------------- #
def run(cfg: BirthdayConfig, settings: Settings) -> None:
    started = time.monotonic()
    vcards = fetch_vcards(cfg.carddav_url, settings)
    log.debug("Received %d vCards.", len(vcards))

    today = datetime.now(TZ).date()
    years = range(today.year, today.year + cfg.years_ahead)

    events = []
    seen: set[str] = set()
    for vcard in vcards:
        bday = birthday_from_vcard(vcard)
        if not bday:
            continue
        if bday.series_uid in seen:
            log.warning("Duplicate contact UID, skipping %s", bday.name)
            continue
        seen.add(bday.series_uid)
        for ev in birthday_events(bday, years, cfg.reminders):
            events.append((ev.uid, ev.summary, ev.start, "Birthday", DESCRIPTION, ev.reminders))

    counts = sync_allday(
        log, settings, cfg.calendar, PREFIX, events,
        window_start=date(today.year, 1, 1),
        window_end=date(today.year + cfg.years_ahead + 1, 1, 1),
        source_count=len(seen), always_allowed=2, unit="contacts",
    )
    log.info("Done: %d birthdays, %d events - %s (%.1fs)%s",
             len(seen), len(events), counts.summary(), time.monotonic() - started,
             " [DRY RUN - nothing written]" if settings.dry_run else "")
