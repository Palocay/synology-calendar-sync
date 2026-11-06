"""Shared helpers: configuration, logging, schedule, CalDAV access, change detection."""
from __future__ import annotations

import logging
import math
import os
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime, time as dt_time, timedelta, timezone
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo

from caldav import get_davclient
from icalendar import Alarm, Calendar, Event

APP_NAME = "synology-calendar-sync"
APP_VERSION = os.getenv("APP_VERSION", "dev")
TZ = ZoneInfo(os.getenv("TZ") or "Europe/Berlin")
UTC = timezone.utc


# --------------------------------------------------------------------------- #
# Environment
# --------------------------------------------------------------------------- #
def env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def env_str(*names: str, default: str = "") -> str:
    """First non-empty value of the given variable names (old names as fallback)."""
    for name in names:
        value = (os.getenv(name) or "").strip()
        if value:
            return value
    return default


def env_int(name: str, default: int, low: int, high: int) -> int:
    raw = env_str(name)
    try:
        value = int(raw) if raw else default
    except ValueError as exc:
        raise RuntimeError(f"{name} must be a number, got {raw!r}") from exc
    return max(low, min(value, high))


def required_env(name: str) -> str:
    value = env_str(name)
    if not value:
        raise RuntimeError(f"Environment variable {name} is missing.")
    return value


def secret_env(name: str) -> str:
    """Read NAME, or the file given in NAME_FILE (Docker secrets)."""
    file_path = env_str(f"{name}_FILE")
    if file_path:
        try:
            value = Path(file_path).read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise RuntimeError(f"Cannot read {name}_FILE ({file_path}): {exc}") from exc
    else:
        value = env_str(name)
    if not value:
        raise RuntimeError(f"Environment variable {name} (or {name}_FILE) is missing.")
    return value


@dataclass(frozen=True)
class Settings:
    """Settings shared by all jobs."""
    caldav_url: str
    username: str
    password: str
    verify_tls: bool
    dry_run: bool
    delete_stale: bool
    max_delete_percent: int


def load_settings() -> Settings:
    return Settings(
        caldav_url=required_env("CALDAV_URL"),
        username=secret_env("DAV_USERNAME"),
        password=secret_env("DAV_PASSWORD"),
        verify_tls=env_bool("VERIFY_TLS", True),
        dry_run=env_bool("DRY_RUN", True),
        delete_stale=env_bool("DELETE_STALE_EVENTS", True),
        max_delete_percent=env_int("MAX_DELETE_PERCENT", 50, 0, 100),
    )


def setup_logging() -> None:
    level = getattr(logging, env_str("LOG_LEVEL", default="INFO").upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        stream=sys.stdout,
    )
    # Third-party libraries only speak up when something is actually wrong.
    for noisy in ("caldav", "urllib3", "niquests", "requests"):
        logging.getLogger(noisy).setLevel(max(level, logging.WARNING))


# --------------------------------------------------------------------------- #
# Schedule
# --------------------------------------------------------------------------- #
def parse_hhmm(raw: str, setting: str = "SCHEDULE_TIME") -> int:
    m = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*", raw or "")
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
        raise RuntimeError(f"Invalid {setting}: {raw!r} (expected HH:MM)")
    return int(m.group(1)) * 60 + int(m.group(2))


def parse_interval(raw: str, setting: str = "SCHEDULE_INTERVAL") -> int:
    """'4h', '4', '90m', '1h30m' -> minutes. No unit means hours."""
    raw = (raw or "24h").strip().lower().replace(" ", "")
    if re.fullmatch(r"\d+(\.\d+)?", raw):
        minutes = float(raw) * 60
    else:
        m = re.fullmatch(r"(?:(\d+(?:\.\d+)?)h)?(?:(\d+)m(?:in)?)?", raw)
        if not m or not any(m.groups()):
            raise RuntimeError(f"Invalid {setting}: {raw!r} (e.g. 4h, 30m, 24h)")
        minutes = float(m.group(1) or 0) * 60 + int(m.group(2) or 0)
    minutes = int(minutes)
    if minutes < 5:
        raise RuntimeError(f"{setting} must be at least 5 minutes.")
    if minutes > 1440:
        logging.getLogger("scheduler").warning("%s > 24h is not supported, using 24h.", setting)
        minutes = 1440
    return minutes


def daily_slots(start_min: int, interval_min: int) -> list[int]:
    """Fixed times per day, e.g. 04:30 + 4h -> 00:30, 04:30, 08:30, ... 20:30."""
    count = math.ceil(1440 / interval_min)
    return sorted({(start_min + k * interval_min) % 1440 for k in range(count)})


def format_slots(slots: list[int]) -> str:
    if len(slots) > 8:
        return f"{len(slots)}x daily from {slots[0] // 60:02d}:{slots[0] % 60:02d}"
    return ", ".join(f"{s // 60:02d}:{s % 60:02d}" for s in slots)


def next_run(now: datetime, slots: list[int]) -> datetime:
    for offset in range(2):
        day = now.date() + timedelta(days=offset)
        for slot in slots:
            candidate = datetime.combine(day, dt_time(slot // 60, slot % 60), tzinfo=TZ)
            if candidate.astimezone(UTC) > now.astimezone(UTC):
                return candidate
    raise AssertionError("unreachable")


def sleep_until(target: datetime) -> None:
    # Sleep in chunks: robust against NAS standby and clock adjustments.
    # Compare in UTC so daylight saving time changes are handled correctly.
    target_utc = target.astimezone(UTC)
    while True:
        remaining = (target_utc - datetime.now(UTC)).total_seconds()
        if remaining <= 0:
            return
        time.sleep(min(remaining, 300))


# --------------------------------------------------------------------------- #
# iCalendar helpers
# --------------------------------------------------------------------------- #
def prop_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, list):  # e.g. multiple CATEGORIES lines
        return ",".join(sorted(prop_text(v) for v in value))
    try:
        raw = value.to_ical()
        raw = raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)
    except Exception:
        raw = str(value)
    return " ".join(raw.split())


def first_vevent(ical_text: str):
    try:
        cal = Calendar.from_ical(ical_text)
    except Exception:
        return None
    for comp in cal.walk("VEVENT"):
        return comp
    return None


def event_vevent(event):
    """Return the first VEVENT of a caldav object."""
    try:
        for item in event.get_icalendar_instance().walk("VEVENT"):
            return item
    except Exception:
        pass
    try:
        return first_vevent(event.data)
    except Exception:
        return None


# --------------------------------------------------------------------------- #
# CalDAV
# --------------------------------------------------------------------------- #
def get_calendar(settings: Settings, calendar_name: str):
    client = get_davclient(
        url=settings.caldav_url,
        username=settings.username,
        password=settings.password,
        timeout=45,
    )
    try:
        names = []
        for calendar in client.principal().get_calendars():
            try:
                display_name = calendar.get_display_name()
            except Exception:
                display_name = getattr(calendar, "name", None)
            names.append(str(display_name))
            if str(display_name).strip().casefold() == calendar_name.casefold():
                return client, calendar
    except Exception:
        client.close()
        raise
    client.close()
    raise RuntimeError(
        f"Calendar {calendar_name!r} not found. Available calendars: "
        + (", ".join(names) if names else "(none)")
    )


def own_events(calendar, prefix: str, **search) -> dict[str, tuple]:
    """All events in the calendar whose UID starts with prefix: uid -> (item, vevent)."""
    result = {}
    for item in calendar.search(event=True, expand=False, **search):
        vevent = event_vevent(item)
        uid = str(vevent.get("uid", "")) if vevent is not None else ""
        if uid.startswith(prefix):
            result[uid] = (item, vevent)
    return result


# --------------------------------------------------------------------------- #
# Writing changes
# --------------------------------------------------------------------------- #
@dataclass
class Counts:
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    deleted: int = 0
    failed: int = 0

    def summary(self) -> str:
        text = (f"{self.created} created, {self.updated} updated, "
                f"{self.unchanged} unchanged, {self.deleted} deleted")
        if self.failed:
            text += f", {self.failed} errors"
        return text


def deletion_blocked(stale: set, existing: set, source_count: int, max_percent: int,
                     always_allowed: int, unit: str) -> str | None:
    """Return a reason to skip deleting, or None if deleting is fine.

    Protects against wiping a calendar when the source is temporarily empty
    or unreachable, or the wrong source is configured."""
    if not stale:
        return None
    if source_count == 0:
        return "the source returned no entries at all"
    if len(stale) <= always_allowed or max_percent >= 100:
        return None
    percent = 100 * len(stale) / max(1, len(existing))
    if percent > max_percent:
        return (f"it would remove {len(stale)} of {len(existing)} {unit} "
                f"({percent:.0f}% > MAX_DELETE_PERCENT={max_percent}%)")
    return None


@dataclass
class Desired:
    key: object                              # comparison key
    render: Callable[[object], str]          # created -> iCalendar text
    label: str                               # for log messages


def write_changes(log: logging.Logger, calendar, settings: Settings,
                  desired: dict[str, Desired],
                  existing: dict[str, tuple],     # uid -> (item, current_key, created)
                  block_reason: Callable[[list[str]], str | None]) -> Counts:
    counts = Counts()
    for uid, want in desired.items():
        try:
            if uid in existing:
                item, current_key, created = existing[uid]
                if current_key == want.key:
                    counts.unchanged += 1
                    continue
                log.debug("Update: %s", want.label)
                if not settings.dry_run:
                    item.data = want.render(created)
                    item.save()
                counts.updated += 1
            else:
                log.debug("Create: %s", want.label)
                if not settings.dry_run:
                    calendar.add_event(want.render(None))
                counts.created += 1
        except Exception as exc:
            counts.failed += 1
            log.error("Could not write %s: %s", want.label, exc)

    stale = [uid for uid in existing if uid not in desired]
    if not stale:
        return counts
    if not settings.delete_stale:
        log.warning("%d own events are stale. Set DELETE_STALE_EVENTS=true to clean them up.",
                    len(stale))
        return counts
    reason = block_reason(stale)
    if reason:
        log.warning("Deletion protection: skipped deleting %d stale events because %s. "
                    "If this is intended, set MAX_DELETE_PERCENT=100 for one run.",
                    len(stale), reason)
        return counts
    for uid in stale:
        log.debug("Delete: %s", uid)
        if not settings.dry_run:
            try:
                existing[uid][0].delete()
            except Exception as exc:
                counts.failed += 1
                log.error("Could not delete %s: %s", uid, exc)
                continue
        counts.deleted += 1
    return counts


# --------------------------------------------------------------------------- #
# Reminders
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Reminder:
    """Either a clock time relative to the event's day ('1d 09:00' = day before
    at 09:00) or a duration before the start ('30m', '2h', '1d')."""
    days: int = 0
    clock_min: int | None = None      # minutes after midnight, or None
    before: timedelta | None = None   # duration before start, or None

    def offset(self, start) -> timedelta:
        """Alarm offset relative to the event start (date or datetime)."""
        if self.before is not None:
            return -self.before
        start_of_day = 0
        if isinstance(start, datetime):
            start_of_day = start.hour * 60 + start.minute
        return timedelta(days=-self.days, minutes=self.clock_min - start_of_day)


def parse_reminders(raw: str, setting: str = "REMINDER") -> tuple[Reminder, ...]:
    """Comma separated list.
    With a time:    '1d 09:00' (day before at 09:00), '08:00' (on the day at 08:00)
    Without a time: '30m', '2h', '1d', '1d12h' (that long before the start)"""
    result = []
    for part in (raw or "").split(","):
        part = part.strip().lower()
        if not part:
            continue
        m = re.fullmatch(r"(?:(\d{1,2})\s*d)?\s*(\d{1,2}):(\d{2})", part)
        if m:
            hours, minutes = int(m.group(2)), int(m.group(3))
            if hours > 23 or minutes > 59:
                raise RuntimeError(f"Invalid time in {setting}: {part!r}")
            result.append(Reminder(days=int(m.group(1) or 0), clock_min=hours * 60 + minutes))
            continue
        m = re.fullmatch(r"(?:(\d+)\s*d)?\s*(?:(\d+)\s*h)?\s*(?:(\d+)\s*m(?:in)?)?", part)
        if m and any(m.groups()):
            result.append(Reminder(before=timedelta(days=int(m.group(1) or 0),
                                                    hours=int(m.group(2) or 0),
                                                    minutes=int(m.group(3) or 0))))
            continue
        raise RuntimeError(f"Invalid {setting} value: {part!r} (e.g. '1d 09:00', '08:00', '30m')")
    return tuple(dict.fromkeys(result))


def make_alarm(offset: timedelta, summary: str):
    alarm = Alarm()
    alarm.add("action", "DISPLAY")
    alarm.add("description", summary)
    alarm.add("trigger", offset)
    return alarm


# --------------------------------------------------------------------------- #
# All-day events (birthdays, holidays)
# --------------------------------------------------------------------------- #
# Fields compared to decide whether an event has changed. Timestamps are
# deliberately excluded, otherwise every event would change on every run.
COMPARE_FIELDS = ("SUMMARY", "DTSTART", "DTEND", "TRANSP", "CATEGORIES", "DESCRIPTION")


def make_allday_ical(uid: str, summary: str, start, category: str, description: str,
                     reminders=(), created: datetime | None = None) -> str:
    now = datetime.now(tz=UTC).replace(microsecond=0)
    event = Event()
    event.add("uid", uid)
    event.add("dtstamp", now)          # mandatory per RFC 5545
    event.add("summary", summary)
    event.add("dtstart", start)
    event.add("dtend", start + timedelta(days=1))
    event.add("transp", "TRANSPARENT")
    event.add("categories", [category])
    if description:
        event.add("description", description)
    event.add("created", created or now)
    event.add("last-modified", now)
    for reminder in reminders:
        event.add_component(make_alarm(reminder.offset(start), summary))

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


def sync_allday(log: logging.Logger, settings: Settings, calendar_name: str, prefix: str,
                events: list[tuple[str, str, object, str, str, tuple]],
                window_start, window_end, source_count: int,
                always_allowed: int, unit: str) -> Counts:
    """Write all-day events (uid, summary, start, category, description, reminders)
    into a calendar. Own events before window_start are never touched."""
    desired: dict[str, Desired] = {}
    for uid, summary, start, category, description, reminders in events:
        args = (uid, summary, start, category, description, reminders)
        desired[uid] = Desired(
            key=fingerprint(first_vevent(make_allday_ical(*args))),
            render=lambda created, args=args: make_allday_ical(*args, created=created),
            label=f"{summary} ({start})",
        )

    client, calendar = get_calendar(settings, calendar_name)
    try:
        existing = {}
        for uid, (item, vevent) in own_events(calendar, prefix, start=window_start, end=window_end).items():
            created = vevent.get("created")
            existing[uid] = (item, fingerprint(vevent), created.dt if created is not None else None)
        return write_changes(
            log, calendar, settings, desired, existing,
            block_reason=lambda stale: deletion_blocked(
                {series_of(u) for u in stale} if unit == "contacts" else set(stale),
                {series_of(u) for u in existing} if unit == "contacts" else set(existing),
                source_count, settings.max_delete_percent, always_allowed, unit),
        )
    finally:
        client.close()


def series_of(uid: str) -> str:
    """UID without the trailing ':<year>'."""
    return uid.rsplit(":", 1)[0]
