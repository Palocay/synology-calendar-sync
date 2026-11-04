from datetime import datetime

import pytest

from calsync import common


@pytest.mark.parametrize("raw, minutes", [
    ("4h", 240), ("4", 240), ("90m", 90), ("30m", 30), ("1h30m", 90), ("24h", 1440), ("48h", 1440),
])
def test_parse_interval(raw, minutes):
    assert common.parse_interval(raw) == minutes


@pytest.mark.parametrize("raw", ["abc", "2m", "-1h"])
def test_parse_interval_invalid(raw):
    with pytest.raises(RuntimeError):
        common.parse_interval(raw)


def test_daily_slots():
    slots = common.daily_slots(common.parse_hhmm("04:30"), 240)
    assert common.format_slots(slots) == "00:30, 04:30, 08:30, 12:30, 16:30, 20:30"


def test_next_run_same_day_and_rollover():
    slots = common.daily_slots(270, 240)
    now = datetime(2026, 11, 3, 16, 4, tzinfo=common.TZ)
    assert common.next_run(now, slots) == datetime(2026, 11, 3, 16, 30, tzinfo=common.TZ)
    now = datetime(2026, 11, 3, 23, 59, tzinfo=common.TZ)
    assert common.next_run(now, slots) == datetime(2026, 11, 4, 0, 30, tzinfo=common.TZ)


def test_env_str_fallback(monkeypatch):
    monkeypatch.delenv("NEW_NAME", raising=False)
    monkeypatch.setenv("OLD_NAME", "old")
    assert common.env_str("NEW_NAME", "OLD_NAME") == "old"
    monkeypatch.setenv("NEW_NAME", "new")
    assert common.env_str("NEW_NAME", "OLD_NAME") == "new"


def test_deletion_allowed_for_few():
    assert common.deletion_blocked({1, 2}, set(range(10)), 8, 50, 2, "contacts") is None


def test_deletion_blocked_when_source_empty():
    assert common.deletion_blocked(set(range(10)), set(range(10)), 0, 50, 2, "contacts")


def test_deletion_blocked_above_threshold():
    assert common.deletion_blocked(set(range(6)), set(range(10)), 4, 50, 2, "contacts")
    assert common.deletion_blocked(set(range(5)), set(range(10)), 5, 50, 2, "contacts") is None


def test_deletion_threshold_disabled():
    assert common.deletion_blocked(set(range(9)), set(range(10)), 1, 100, 2, "contacts") is None
