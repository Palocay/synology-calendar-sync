"""ICS / iCal subscriptions (any calendar URL) -> Synology Calendar.

Synology Calendar can only import .ics files once. This module fetches a feed
on a schedule and mirrors its events into a Synology calendar: new events are
created, changed events updated, removed events deleted.
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta

import requests
from icalendar import Calendar
from icalendar.prop import vText

from .common import (
    APP_VERSION, TZ, UTC, Desired, Settings, deletion_blocked, env_int, env_str,
    get_calendar, own_events, prop_text, write_changes,
)

PREFIX = "synology-calendar-sync:ics:"
HASH_PROP = "X-SCS-HASH"


# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Feed:
    number: int
    name: str
    url: str
    calendar: str
    interval: str
    past_days: int | None
    username: str
    password: str

    @property
    def key(self) -> str:
        """Stable ID of the feed, part of every event UID it creates."""
        return hashlib.sha256(self.url.encode("utf-8")).hexdigest()[:10]

    @property
    def prefix(self) -> str:
        return f"{PREFIX}{self.key}:"


def load_feeds(default_interval: str) -> list[Feed]:
    """ICS_1_URL, ICS_1_CALENDAR, ICS_1_INTERVAL, ... ICS_2_URL, ..."""
    numbers = sorted(int(m.group(1)) for name in os.environ
                     if (m := re.fullmatch(r"ICS_(\d+)_URL", name)) and os.environ[name].strip())
    feeds = []
    for n in numbers:
        p = f"ICS_{n}_"
        url = env_str(p + "URL")
        if url.lower().startswith("webcal://"):
            url = "https://" + url[len("webcal://"):]
        calendar = env_str(p + "CALENDAR")
        if not calendar:
            raise RuntimeError(f"{p}CALENDAR is missing (target calendar for {p}URL).")
        past = env_str(p + "PAST_DAYS")
        feeds.append(Feed(
            number=n,
            name=env_str(p + "NAME", default=calendar),
            url=url,
            calendar=calendar,
            interval=env_str(p + "INTERVAL", default=default_interval),
            past_days=env_int(p + "PAST_DAYS", 0, 0, 36500) if past else None,
            username=env_str(p + "USERNAME"),
            password=env_str(p + "PASSWORD"),
        ))
    return feeds


# --------------------------------------------------------------------------- #
# Feed processing
# --------------------------------------------------------------------------- #
def fetch_feed(feed: Feed, settings: Settings) -> Calendar:
    response = requests.get(
        feed.url,
        headers={"User-Agent": f"synology-calendar-sync/{APP_VERSION}"},
        auth=(feed.username, feed.password) if feed.username else None,
        timeout=60,
        verify=settings.verify_tls,
    )
    if response.status_code != 200:
        raise RuntimeError(f"Download failed: HTTP {response.status_code}")
    try:
        return Calendar.from_ical(response.content)
    except Exception as exc:
        raise RuntimeError(f"Not a valid iCalendar file: {exc}") from exc


def _as_date(value) -> date | None:
    dt = getattr(value, "dt", None)
    if isinstance(dt, datetime):
        return (dt.astimezone(TZ) if dt.tzinfo else dt).date()
    if isinstance(dt, date):
        return dt
    return None


def end_date(comp) -> date | None:
    end = _as_date(comp.get("DTEND"))
    if end:
        return end
    start = _as_date(comp.get("DTSTART"))
    duration = getattr(comp.get("DURATION"), "dt", None)
    if start and isinstance(duration, timedelta):
        return start + duration
    return start


def is_recurring(comps) -> bool:
    return any("RRULE" in c or "RDATE" in c for c in comps)


def content_hash(comps) -> str:
    """Hash of the source events without DTSTAMP (many feeds change it on every download)."""
    h = hashlib.sha256(b"v1\n")
    for comp in sorted(comps, key=lambda c: prop_text(c.get("RECURRENCE-ID"))):
        for line in comp.to_ical().splitlines():
            if not line.upper().startswith(b"DTSTAMP"):
                h.update(line + b"\n")
    return h.hexdigest()[:32]


def strip_text_values(comp) -> None:
    """Remove leading/trailing whitespace from text properties (e.g. "Training ").
    Otherwise the caldav library fixes it itself and logs a long warning."""
    for sub in comp.walk():
        for name, value in list(sub.items()):
            values = value if isinstance(value, list) else [value]
            cleaned = []
            for v in values:
                if isinstance(v, vText) and str(v) != str(v).strip():
                    new = vText(str(v).strip())
                    new.params = v.params
                    v = new
                cleaned.append(v)
            if any(a is not b for a, b in zip(cleaned, values)):
                sub[name] = cleaned if isinstance(value, list) else cleaned[0]


def group_events(cal: Calendar, log: logging.Logger) -> dict[str, list]:
    """Group VEVENTs by UID: master event plus its modified occurrences."""
    groups: dict[str, list] = {}
    for comp in cal.walk("VEVENT"):
        uid = str(comp.get("UID", "")).strip()
        if not uid:
            uid = "no-uid-" + hashlib.sha256(comp.to_ical()).hexdigest()[:24]
        groups.setdefault(uid, []).append(comp)

    for uid, comps in groups.items():
        masters = [c for c in comps if c.get("RECURRENCE-ID") is None]
        if len(masters) > 1:
            log.debug("Feed contains %d events with UID %s, keeping the first.", len(masters), uid)
            groups[uid] = [masters[0]] + [c for c in comps if c.get("RECURRENCE-ID") is not None]
    return groups


def build_objects(feed: Feed, cal: Calendar, today: date,
                  log: logging.Logger) -> dict[str, tuple[str, str, str]]:
    """uid -> (hash, iCalendar text, label) for every event series in the feed."""
    timezones = list(cal.walk("VTIMEZONE"))
    cutoff = today - timedelta(days=feed.past_days) if feed.past_days is not None else None
    now = datetime.now(tz=UTC).replace(microsecond=0)
    result = {}

    for source_uid, comps in group_events(cal, log).items():
        if cutoff and not is_recurring(comps):
            ends = [end_date(c) for c in comps]
            if all(e is not None and e < cutoff for e in ends):
                continue

        digest = content_hash(comps)
        uid = feed.prefix + hashlib.sha256(source_uid.encode("utf-8")).hexdigest()[:24]

        out = Calendar()
        out.add("prodid", "-//synology-calendar-sync//EN")
        out.add("version", "2.0")
        for tz in timezones:
            out.add_component(tz)
        for comp in comps:
            strip_text_values(comp)
            comp.pop("UID", None)
            comp.add("uid", uid)
            if "DTSTAMP" not in comp:
                comp.add("dtstamp", now)
            comp.pop(HASH_PROP, None)
            comp.add(HASH_PROP, digest)
            out.add_component(comp)

        label = f"{prop_text(comps[0].get('SUMMARY')) or '(no title)'} ({_as_date(comps[0].get('DTSTART'))})"
        result[uid] = (digest, out.to_ical().decode("utf-8"), label)
    return result


# --------------------------------------------------------------------------- #
# Run
# --------------------------------------------------------------------------- #
def run(feed: Feed, settings: Settings) -> None:
    log = logging.getLogger(f"ics:{feed.name}")
    started = time.monotonic()

    cal = fetch_feed(feed, settings)
    objects = build_objects(feed, cal, datetime.now(TZ).date(), log)
    desired = {
        uid: Desired(key=digest, render=lambda created, text=text: text, label=label)
        for uid, (digest, text, label) in objects.items()
    }

    client, calendar = get_calendar(settings, feed.calendar)
    try:
        existing = {
            uid: (item, str(vevent.get(HASH_PROP, "")), None)
            for uid, (item, vevent) in own_events(calendar, feed.prefix).items()
        }
        counts = write_changes(
            log, calendar, settings, desired, existing,
            block_reason=lambda stale: deletion_blocked(
                set(stale), set(existing), len(desired),
                settings.max_delete_percent, always_allowed=5, unit="events"),
        )
    finally:
        client.close()

    log.info("Done: %d events - %s (%.1fs)%s",
             len(desired), counts.summary(), time.monotonic() - started,
             " [DRY RUN - nothing written]" if settings.dry_run else "")
