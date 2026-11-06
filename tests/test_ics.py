import logging
from datetime import date

from icalendar import Calendar

from calsync import common, ics

LOG = logging.getLogger("test")

FEED = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//EN
BEGIN:VTIMEZONE
TZID:Europe/Berlin
BEGIN:STANDARD
DTSTART:19701025T030000
TZOFFSETFROM:+0200
TZOFFSETTO:+0100
RRULE:FREQ=YEARLY;BYMONTH=10;BYDAY=-1SU
END:STANDARD
END:VTIMEZONE
BEGIN:VEVENT
UID:old@example.com
DTSTAMP:20260101T000000Z
DTSTART;VALUE=DATE:20250101
DTEND;VALUE=DATE:20250102
SUMMARY:Old event
END:VEVENT
BEGIN:VEVENT
UID:waste@example.com
DTSTAMP:20260101T000000Z
DTSTART;VALUE=DATE:20261110
DTEND;VALUE=DATE:20261111
SUMMARY:Waste collection
END:VEVENT
BEGIN:VEVENT
UID:weekly@example.com
DTSTAMP:20260101T000000Z
DTSTART;TZID=Europe/Berlin:20240101T100000
DTEND;TZID=Europe/Berlin:20240101T110000
RRULE:FREQ=WEEKLY
SUMMARY:Weekly meeting
END:VEVENT
BEGIN:VEVENT
UID:weekly@example.com
DTSTAMP:20260101T000000Z
RECURRENCE-ID;TZID=Europe/Berlin:20240108T100000
DTSTART;TZID=Europe/Berlin:20240108T120000
DTEND;TZID=Europe/Berlin:20240108T130000
SUMMARY:Weekly meeting (moved)
END:VEVENT
END:VCALENDAR
"""


def feed(**overrides):
    values = dict(number=1, name="Test", url="https://example.com/cal.ics", calendar="Test",
                  interval="1h", past_days=None, username="", password="", reminders=())
    values.update(overrides)
    return ics.Feed(**values)


def build(text=FEED, **overrides):
    return ics.build_objects(feed(**overrides), Calendar.from_ical(text), date(2026, 11, 4), LOG)


def test_all_series_are_mirrored():
    objects = build()
    assert len(objects) == 3
    assert all(uid.startswith(feed().prefix) for uid in objects)


def test_recurring_event_with_override_stays_together():
    weekly = [text for _, text, label in build().values() if "Weekly meeting" in label][0]
    cal = Calendar.from_ical(weekly)
    assert len(list(cal.walk("VEVENT"))) == 2
    assert len(list(cal.walk("VTIMEZONE"))) == 1
    assert len({str(c["UID"]) for c in cal.walk("VEVENT")}) == 1


def test_past_days_filter_keeps_recurring():
    labels = [label for _, _, label in build(past_days=30).values()]
    assert not any("Old event" in l for l in labels)
    assert any("Weekly meeting" in l for l in labels)
    assert any("Waste collection" in l for l in labels)


def test_hash_ignores_dtstamp_but_detects_changes():
    first = {u: h for u, (h, _, _) in build().items()}
    restamped = {u: h for u, (h, _, _) in build(FEED.replace("DTSTAMP:20260101T000000Z",
                                                                "DTSTAMP:20261104T120000Z")).items()}
    assert first == restamped
    changed = {u: h for u, (h, _, _) in build(FEED.replace("Waste collection", "Paper collection")).items()}
    assert first != changed


def test_hash_is_written_to_event():
    for digest, text, _ in build().values():
        assert f"{ics.HASH_PROP}:{digest}" in text


def test_uids_are_stable_and_feed_specific():
    a = set(build())
    assert a == set(build())
    assert a.isdisjoint(set(build(url="https://example.com/other.ics")))


def test_load_feeds(monkeypatch):
    monkeypatch.setenv("ICS_1_URL", "webcal://example.com/a.ics")
    monkeypatch.setenv("ICS_1_CALENDAR", "Waste")
    monkeypatch.setenv("ICS_2_URL", "https://example.com/b.ics")
    monkeypatch.setenv("ICS_2_CALENDAR", "Holidays")
    monkeypatch.setenv("ICS_2_INTERVAL", "24h")
    monkeypatch.setenv("ICS_2_PAST_DAYS", "30")
    feeds = ics.load_feeds("4h")
    assert [(f.url, f.calendar, f.interval, f.past_days) for f in feeds] == [
        ("https://example.com/a.ics", "Waste", "4h", None),
        ("https://example.com/b.ics", "Holidays", "24h", 30),
    ]


def test_trailing_whitespace_is_removed():
    feed_text = FEED.replace("SUMMARY:Waste collection", "SUMMARY:Waste collection ")
    text = [t for _, t, label in build(feed_text).values() if "Waste collection" in label][0]
    assert "SUMMARY:Waste collection\r\n" in text


def test_reminders_replace_feed_alarms():
    feed_text = FEED.replace("SUMMARY:Waste collection\n",
                             "SUMMARY:Waste collection\nBEGIN:VALARM\nACTION:DISPLAY\n"
                             "TRIGGER:-PT5M\nEND:VALARM\n")
    objects = build(feed_text, reminders=common.parse_reminders("1d 18:00"))
    waste = [t for _, t, label in objects.values() if "Waste collection" in label][0]
    assert "TRIGGER:-PT5M" not in waste
    assert "TRIGGER:-PT6H" in waste            # all-day: day before at 18:00


def test_reminder_for_timed_event():
    objects = build(reminders=common.parse_reminders("30m"))
    weekly = [t for _, t, label in objects.values() if "Weekly meeting" in label][0]
    assert weekly.count("TRIGGER:-PT30M") == 2   # master and moved occurrence


def test_reminder_change_updates_hash():
    without = {u: h for u, (h, _, _) in build().items()}
    with_reminder = {u: h for u, (h, _, _) in build(reminders=common.parse_reminders("30m")).items()}
    assert without.keys() == with_reminder.keys()
    assert all(without[u] != with_reminder[u] for u in without)
