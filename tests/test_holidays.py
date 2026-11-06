from datetime import date

import pytest

from calsync import public_holidays as ph


@pytest.mark.parametrize("year, expected", [
    (2024, date(2024, 3, 31)), (2025, date(2025, 4, 20)),
    (2026, date(2026, 4, 5)), (2027, date(2027, 3, 28)),
])
def test_easter(year, expected):
    assert ph.easter(year) == expected


def test_german_observances_2026():
    obs = dict((name, d) for d, name in ph.german_observances(2026))
    assert obs["Aschermittwoch"] == date(2026, 2, 18)
    assert obs["Muttertag"] == date(2026, 5, 10)
    assert obs["Volkstrauertag"] == date(2026, 11, 15)
    assert obs["Buß- und Bettag"] == date(2026, 11, 18)
    assert obs["Totensonntag"] == date(2026, 11, 22)
    assert obs["1. Advent"] == date(2026, 11, 29)
    assert obs["4. Advent"] == date(2026, 12, 20)
    assert obs["Beginn der Sommerzeit"] == date(2026, 3, 29)
    assert obs["Ende der Sommerzeit"] == date(2026, 10, 25)


def config(**overrides):
    values = dict(calendar="Feiertage", country="DE", state=None, language="de",
                  observances=True, years_ahead=1, reminders=(), interval="24h")
    values.update(overrides)
    return ph.HolidayConfig(**values)


def by_name(events):
    return {e.summary: e for e in events}


def test_nationwide_and_regional_holidays():
    events = by_name(ph.holiday_events(config(observances=False), range(2026, 2027)))
    assert events["Tag der Deutschen Einheit"].description == "Gesetzlicher Feiertag (bundesweit)"
    assert "Bayern" in events["Allerheiligen"].description
    assert "Berlin" not in events["Allerheiligen"].description
    assert "Teilweise" in events["Fronleichnam"].description          # SN/TH: catholic municipalities
    assert all(e.category == "Holiday" for e in events.values())


def test_state_filter():
    events = by_name(ph.holiday_events(config(state="BY", observances=False), range(2026, 2027)))
    assert "Allerheiligen" in events
    assert "Reformationstag" not in events                           # not a holiday in Bavaria


def test_observances_without_duplicates():
    events = ph.holiday_events(config(), range(2026, 2027))
    names = [e.summary for e in events]
    assert "Volkstrauertag" in names
    assert names.count("Reformationstag") == 1                       # holiday in some states, not twice
    reformation = by_name(events)["Reformationstag"]
    assert reformation.category == "Holiday"


def test_observance_shown_when_filtered_out():
    events = by_name(ph.holiday_events(config(state="BY"), range(2026, 2027)))
    assert events["Reformationstag"].category == "Observance"


def test_uids_are_stable_and_unique():
    first = [e.uid for e in ph.holiday_events(config(), range(2026, 2028))]
    assert len(first) == len(set(first))
    assert first == [e.uid for e in ph.holiday_events(config(), range(2026, 2028))]


def test_load_config(monkeypatch):
    monkeypatch.setenv("HOLIDAYS_CALENDAR", "Feiertage")
    monkeypatch.setenv("HOLIDAYS_STATE", "Bayern")
    cfg = ph.load_config()
    assert (cfg.country, cfg.state, cfg.language, cfg.observances) == ("DE", "BY", "de", True)


def test_disabled_without_calendar(monkeypatch):
    monkeypatch.delenv("HOLIDAYS_CALENDAR", raising=False)
    assert ph.load_config() is None
