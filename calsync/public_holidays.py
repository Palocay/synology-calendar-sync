"""Public holidays (and German observances) -> Synology Calendar, no ICS feed needed.

Public holidays come from the `holidays` library (maintained, offline, per
state). Observances such as Volkstrauertag or Muttertag are not public holidays
and are therefore computed here.
"""
from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta

import holidays as holidays_lib

from .common import TZ, Reminder, Settings, env_bool, env_int, env_str, parse_reminders, sync_allday

PREFIX = "synology-calendar-sync:holidays:"

log = logging.getLogger("holidays")

TEXTS = {
    "de": {
        "nationwide": "Gesetzlicher Feiertag (bundesweit)",
        "states": "Gesetzlicher Feiertag in: {}",
        "partly": "Teilweise Feiertag (in Gemeinden mit überwiegend katholischer Bevölkerung) in: {}",
        "observance": "Gedenk- bzw. Aktionstag, kein gesetzlicher Feiertag",
    },
    "en": {
        "nationwide": "Public holiday (nationwide)",
        "states": "Public holiday in: {}",
        "partly": "Partly a public holiday (predominantly Catholic municipalities) in: {}",
        "observance": "Observance, not a public holiday",
    },
}


# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class HolidayConfig:
    calendar: str
    country: str
    state: str | None
    language: str
    observances: bool
    years_ahead: int
    reminders: tuple[Reminder, ...]
    interval: str


def load_config() -> HolidayConfig | None:
    """None if HOLIDAYS_CALENDAR is not set."""
    calendar = env_str("HOLIDAYS_CALENDAR")
    if not calendar:
        return None
    country = env_str("HOLIDAYS_COUNTRY", default="DE").upper()
    try:
        cls = type(holidays_lib.country_holidays(country))
    except NotImplementedError as exc:
        raise RuntimeError(f"HOLIDAYS_COUNTRY {country!r} is not supported.") from exc

    state = env_str("HOLIDAYS_STATE") or None
    if state:
        names = state_names(cls)
        by_name = {name.casefold(): code for code, name in names.items()}
        code = state.upper() if state.upper() in names else by_name.get(state.casefold())
        if not code:
            raise RuntimeError(f"Unknown HOLIDAYS_STATE {state!r}. Valid: {', '.join(sorted(names))}")
        state = code

    return HolidayConfig(
        calendar=calendar,
        country=country,
        state=state,
        language=env_str("HOLIDAYS_LANGUAGE", default=getattr(cls, "default_language", None) or "en"),
        observances=env_bool("HOLIDAYS_OBSERVANCES", True),
        years_ahead=env_int("HOLIDAYS_YEARS_AHEAD", 3, 1, 10),
        reminders=parse_reminders(env_str("HOLIDAYS_REMINDER"), "HOLIDAYS_REMINDER"),
        interval=env_str("HOLIDAYS_INTERVAL", default="24h"),
    )


def state_names(cls) -> dict[str, str]:
    """State code -> readable name, e.g. 'BY' -> 'Bayern'."""
    aliases = getattr(cls, "subdivisions_aliases", {}) or {}
    names = {code: name for name, code in aliases.items() if len(code) <= 3}
    if not names:
        names = {code: code for code in getattr(cls, "subdivisions", ())}
    return names


# --------------------------------------------------------------------------- #
# German observances (not public holidays, so not in the holidays library)
# --------------------------------------------------------------------------- #
def easter(year: int) -> date:
    """Gregorian Easter Sunday (anonymous Gregorian algorithm)."""
    a, b, c = year % 19, year // 100, year % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = (h + l - 7 * m + 114) % 31 + 1
    return date(year, month, day)


def sunday_on_or_before(d: date) -> date:
    return d - timedelta(days=(d.weekday() + 1) % 7)


def first_sunday(year: int, month: int) -> date:
    d = date(year, month, 1)
    return d + timedelta(days=(6 - d.weekday()) % 7)


def last_sunday(year: int, month: int, last_day: int) -> date:
    return sunday_on_or_before(date(year, month, last_day))


def german_observances(year: int) -> list[tuple[date, str]]:
    e = easter(year)
    advent4 = sunday_on_or_before(date(year, 12, 24))
    advent1 = advent4 - timedelta(days=21)
    return [
        (date(year, 2, 14), "Valentinstag"),
        (e - timedelta(days=52), "Weiberfastnacht"),
        (e - timedelta(days=48), "Rosenmontag"),
        (e - timedelta(days=47), "Faschingsdienstag"),
        (e - timedelta(days=46), "Aschermittwoch"),
        (date(year, 3, 8), "Frauentag"),
        (last_sunday(year, 3, 31), "Beginn der Sommerzeit"),
        (e - timedelta(days=3), "Gründonnerstag"),
        (e - timedelta(days=1), "Karsamstag"),
        (e, "Ostersonntag"),
        (first_sunday(year, 5) + timedelta(days=7), "Muttertag"),
        (e + timedelta(days=49), "Pfingstsonntag"),
        (date(year, 9, 20), "Weltkindertag"),
        (first_sunday(year, 10), "Erntedankfest"),
        (last_sunday(year, 10, 31), "Ende der Sommerzeit"),
        (date(year, 10, 31), "Halloween"),
        (date(year, 10, 31), "Reformationstag"),
        (date(year, 11, 2), "Allerseelen"),
        (date(year, 11, 11), "Martinstag"),
        (advent1 - timedelta(days=14), "Volkstrauertag"),
        (advent1 - timedelta(days=11), "Buß- und Bettag"),
        (advent1 - timedelta(days=7), "Totensonntag"),
        (advent1, "1. Advent"),
        (advent1 + timedelta(days=7), "2. Advent"),
        (date(year, 12, 6), "Nikolaus"),
        (advent1 + timedelta(days=14), "3. Advent"),
        (advent4, "4. Advent"),
        (date(year, 12, 24), "Heiligabend"),
        (date(year, 12, 31), "Silvester"),
    ]


# --------------------------------------------------------------------------- #
# Building the list
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class HolidayEvent:
    uid: str
    summary: str
    start: date
    category: str
    description: str


def _entries(h) -> list[tuple[date, str]]:
    return [(d, name) for d in sorted(h) for name in h.get_list(d)]


def holiday_events(cfg: HolidayConfig, years: range) -> list[HolidayEvent]:
    cls = type(holidays_lib.country_holidays(cfg.country))
    states = state_names(cls)
    has_catholic = "catholic" in getattr(cls, "supported_categories", ())
    texts = TEXTS["de" if cfg.language.lower().startswith("de") else "en"]
    events: list[HolidayEvent] = []

    for year in years:
        def get(**kwargs):
            return holidays_lib.country_holidays(cfg.country, years=year, language=cfg.language, **kwargs)

        nationwide = set(_entries(get()))
        public: dict[tuple[date, str], set[str]] = {}
        partly: dict[tuple[date, str], set[str]] = {}
        for code in states:
            for entry in _entries(get(subdiv=code)):
                if entry not in nationwide:
                    public.setdefault(entry, set()).add(code)
            if has_catholic:
                for entry in _entries(get(subdiv=code, categories=("catholic",))):
                    partly.setdefault(entry, set()).add(code)

        def names(codes: set[str]) -> str:
            return ", ".join(sorted(states[c] for c in codes))

        for entry in sorted(nationwide | set(public) | set(partly)):
            pub, part = public.get(entry, set()), partly.get(entry, set())
            if cfg.state and entry not in nationwide and cfg.state not in pub | part:
                continue
            lines = []
            if entry in nationwide:
                lines.append(texts["nationwide"])
            if pub:
                lines.append(texts["states"].format(names(pub)))
            if part:
                lines.append(texts["partly"].format(names(part)))
            events.append(_event(cfg, entry, "Holiday", "\n".join(lines)))

        if cfg.observances and cfg.country == "DE":
            known = nationwide | set(public) | set(partly)
            for entry in german_observances(year):
                # Skip if it is a public holiday somewhere (and therefore listed already),
                # unless a state filter removed it - then show it as an observance.
                if entry in known and (not cfg.state or entry in nationwide
                                       or cfg.state in public.get(entry, set()) | partly.get(entry, set())):
                    continue
                events.append(_event(cfg, entry, "Observance", texts["observance"]))

    return events


def _event(cfg: HolidayConfig, entry: tuple[date, str], category: str, description: str) -> HolidayEvent:
    day, name = entry
    name_hash = hashlib.sha256(name.encode("utf-8")).hexdigest()[:10]
    return HolidayEvent(
        uid=f"{PREFIX}{cfg.country}:{day:%Y%m%d}:{name_hash}",
        summary=name,
        start=day,
        category=category,
        description=description,
    )


# --------------------------------------------------------------------------- #
# Run
# --------------------------------------------------------------------------- #
def run(cfg: HolidayConfig, settings: Settings) -> None:
    started = time.monotonic()
    today = datetime.now(TZ).date()
    years = range(today.year, today.year + cfg.years_ahead)
    holidays = holiday_events(cfg, years)
    events = [(h.uid, h.summary, h.start, h.category, h.description, cfg.reminders) for h in holidays]

    counts = sync_allday(
        log, settings, cfg.calendar, f"{PREFIX}{cfg.country}:", events,
        window_start=date(today.year, 1, 1),
        window_end=date(today.year + cfg.years_ahead + 1, 1, 1),
        source_count=len(events), always_allowed=5, unit="events",
    )
    log.info("Done: %d holidays, %d events - %s (%.1fs)%s",
             sum(h.category == "Holiday" for h in holidays), len(events), counts.summary(),
             time.monotonic() - started, " [DRY RUN - nothing written]" if settings.dry_run else "")
