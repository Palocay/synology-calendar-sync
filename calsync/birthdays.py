"""Birthdays from Synology Contacts (CardDAV) -> Synology Calendar."""
from __future__ import annotations

import hashlib
import logging
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date, datetime, timedelta

import requests
import vobject
from icalendar import Alarm, Calendar, Event
from requests.auth import HTTPBasicAuth

from .common import (
    APP_VERSION, TZ, UTC, Desired, Settings, deletion_blocked, env_bool, env_int,
    env_str, get_calendar, own_events, prop_text, first_vevent, write_changes,
)

# Kept from the original project name so existing events are recognized.
PREFIX = "synology-birthday-sync:"
DAV_NS = "DAV:"
CARD_NS = "urn:ietf:params:xml:ns:carddav"
DESCRIPTION = "Synced automatically from Synology Contacts."

# Fields compared to decide whether an event has changed. Timestamps are
# deliberately excluded, otherwise every event would change on every run.
COMPARE_FIELDS = ("SUMMARY", "DTSTART", "DTEND", "TRANSP", "CATEGORIES", "DESCRIPTION")

log = logging.getLogger("birthdays")


# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class BirthdayConfig:
    carddav_url: str
    calendar: str
    years_ahead: int
    reminders: tuple[timedelta, ...]
    interval: str


def parse_reminders(raw: str) -> list[timedelta]:
    """'1d 09:00' -> day before at 09:00, '0d 08:00' or '08:00' -> same day,
    '2d' -> two days before at 00:00. Several values separated by commas.
    Returned as offsets relative to the start of the all-day event."""
    result = []
    for part in (raw or "").split(","):
        part = part.strip().lower()
        if not part:
            continue
        m = re.fullmatch(r"(?:(\d{1,2})\s*d)?\s*(?:(\d{1,2}):(\d{2}))?", part)
        if not m or not any(m.groups()):
            raise RuntimeError(f"Invalid BIRTHDAY_REMINDER value: {part!r} (e.g. '1d 09:00', '08:00')")
        days = int(m.group(1) or 0)
        hours, minutes = int(m.group(2) or 0), int(m.group(3) or 0)
        if hours > 23 or minutes > 59:
            raise RuntimeError(f"Invalid time in BIRTHDAY_REMINDER: {part!r}")
        result.append(timedelta(days=-days, hours=hours, minutes=minutes))
    return sorted(set(result))


def load_config() -> BirthdayConfig | None:
    """None if the birthday sync is disabled (no CARDDAV_URL or BIRTHDAYS=false)."""
    carddav_url = env_str("CARDDAV_URL")
    if not carddav_url or not env_bool("BIRTHDAYS", True):
        return None
    return BirthdayConfig(
        carddav_url=carddav_url,
        # Old variable names are still accepted as fallback.
        calendar=env_str("BIRTHDAY_CALENDAR", "CALENDAR_NAME", default="Birthdays"),
        years_ahead=env_int("BIRTHDAY_YEARS_AHEAD", env_int("YEARS_AHEAD", 10, 1, 20), 1, 20),
        reminders=tuple(parse_reminders(env_str("BIRTHDAY_REMINDER", "REMINDER"))),
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
    reminders: tuple[timedelta, ...]


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
    now = datetime.now(tz=UTC).replace(microsecond=0)
    event = Event()
    event.add("uid", ev.uid)
    event.add("dtstamp", now)          # mandatory per RFC 5545
    event.add("summary", ev.summary)
    event.add("dtstart", ev.start)
    event.add("dtend", ev.start + timedelta(days=1))
    event.add("transp", "TRANSPARENT")
    event.add("categories", ["Birthday"])
    event.add("description", DESCRIPTION)
    event.add("created", created or now)
    event.add("last-modified", now)
    for offset in ev.reminders:
        alarm = Alarm()
        alarm.add("action", "DISPLAY")
        alarm.add("description", ev.summary)
        alarm.add("trigger", offset)
        event.add_component(alarm)

    cal = Calendar()
    cal.add("prodid", "-//synology-calendar-sync//EN")
    cal.add("version", "2.0")
    cal.add_component(event)
    return cal.to_ical().decode("utf-8")


def _alarm_triggers(vevent) -> tuple:
    triggers = []
    for alarm in vevent.walk("VALARM"):
        trigger = alarm.get("TRIGGER")
        dt = getattr(trigger, "dt", None)
        if isinstance(dt, timedelta):
            triggers.append(int(dt.total_seconds()))
        elif trigger is not None:
            triggers.append(prop_text(trigger))
    return tuple(sorted(triggers, key=str))


def fingerprint(vevent) -> tuple:
    """Content-based comparison key, independent of timestamps, line folding,
    property order or fields added by the server."""
    if vevent is None:
        return ()
    return tuple(prop_text(vevent.get(f)) for f in COMPARE_FIELDS) + (_alarm_triggers(vevent),)


# --------------------------------------------------------------------------- #
# Run
# --------------------------------------------------------------------------- #
def run(cfg: BirthdayConfig, settings: Settings) -> None:
    started = time.monotonic()
    vcards = fetch_vcards(cfg.carddav_url, settings)
    log.debug("Received %d vCards.", len(vcards))

    today = datetime.now(TZ).date()
    years = range(today.year, today.year + cfg.years_ahead)

    desired: dict[str, Desired] = {}
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
            desired[ev.uid] = Desired(
                key=fingerprint(first_vevent(make_ical(ev))),
                render=lambda created, ev=ev: make_ical(ev, created),
                label=f"{ev.summary} ({ev.start})",
            )

    client, calendar = get_calendar(settings, cfg.calendar)
    try:
        found = own_events(calendar, PREFIX,
                           start=date(today.year, 1, 1),
                           end=date(today.year + cfg.years_ahead + 1, 1, 1))
        existing = {}
        for uid, (item, vevent) in found.items():
            created = vevent.get("created")
            existing[uid] = (item, fingerprint(vevent), created.dt if created is not None else None)

        def series(uid: str) -> str:
            return uid.rsplit(":", 1)[0]

        counts = write_changes(
            log, calendar, settings, desired, existing,
            block_reason=lambda stale: deletion_blocked(
                {series(u) for u in stale}, {series(u) for u in existing}, len(seen),
                settings.max_delete_percent, always_allowed=2, unit="contacts"),
        )
    finally:
        client.close()

    log.info("Done: %d birthdays, %d events - %s (%.1fs)%s",
             len(seen), len(desired), counts.summary(), time.monotonic() - started,
             " [DRY RUN - nothing written]" if settings.dry_run else "")
