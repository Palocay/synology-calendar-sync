from datetime import date, datetime, timedelta

import pytest
import vobject

from calsync import birthdays as b

VCARD = """BEGIN:VCARD
VERSION:3.0
UID:abc-123
FN:Jane Doe
N:Doe;Jane;;;
BDAY:1991-05-15
END:VCARD
"""


@pytest.mark.parametrize("raw, expected", [
    ("1991-05-15", (5, 15, 1991)),
    ("19910515", (5, 15, 1991)),
    ("1991-05-15T00:00:00Z", (5, 15, 1991)),
    ("--05-15", (5, 15, None)),
    ("--0515", (5, 15, None)),
    ("1604-05-15", (5, 15, None)),   # Apple: year unknown
    (date(1991, 5, 15), (5, 15, 1991)),
    ("", None),
    (None, None),
    ("garbage", None),
    ("1991-13-40", None),
])
def test_parse_vcard_date(raw, expected):
    assert b.parse_vcard_date(raw) == expected


def test_leap_day_in_non_leap_year():
    assert b.date_for_year(2, 29, 2027) == date(2027, 2, 28)
    assert b.date_for_year(2, 29, 2028) == date(2028, 2, 29)


@pytest.mark.parametrize("raw, expected", [
    ("", []),
    ("1d 09:00", [timedelta(hours=-15)]),
    ("08:00", [timedelta(hours=8)]),
    ("0d 08:00", [timedelta(hours=8)]),
    ("2d", [timedelta(days=-2)]),
    ("1d 09:00, 08:00", [timedelta(hours=-15), timedelta(hours=8)]),
])
def test_parse_reminders(raw, expected):
    assert b.parse_reminders(raw) == expected


@pytest.mark.parametrize("raw", ["tomorrow", "1d 25:00", "09:99"])
def test_parse_reminders_invalid(raw):
    with pytest.raises(RuntimeError):
        b.parse_reminders(raw)


def test_make_title():
    assert b.make_title("Jane", 35) == "🎂 Jane (35)"
    assert b.make_title("Jane", None) == "🎂 Jane"


def test_birthday_from_vcard():
    bday = b.birthday_from_vcard(vobject.readOne(VCARD))
    assert (bday.name, bday.month, bday.day, bday.birth_year) == ("Jane Doe", 5, 15, 1991)


def test_contact_without_birthday():
    assert b.birthday_from_vcard(vobject.readOne(VCARD.replace("BDAY:1991-05-15\n", ""))) is None


def test_uid_prefix():
    card = vobject.readOne(VCARD)
    digest = b.contact_digest(card, "Jane Doe")
    assert b.birthday_from_vcard(card).series_uid == f"synology-calendar-sync:birthday:{digest}"


def test_birthday_events():
    bday = b.birthday_from_vcard(vobject.readOne(VCARD))
    events = b.birthday_events(bday, range(2026, 2028))
    assert [e.summary for e in events] == ["🎂 Jane Doe (35)", "🎂 Jane Doe (36)"]
    assert [e.uid.rsplit(":", 1)[1] for e in events] == ["2026", "2027"]


def test_no_events_before_birth_year():
    bday = b.Birthday("x", "Baby", 1, 1, 2027)
    assert [e.start.year for e in b.birthday_events(bday, range(2026, 2029))] == [2027, 2028]


def _event(reminders=()):
    return b.BirthdayEvent(uid="synology-birthday-sync:abc:2026", summary="🎂 Jane (35)",
                           start=date(2026, 5, 15), reminders=tuple(reminders))


def test_ical_has_dtstamp():
    assert "DTSTAMP:" in b.make_ical(_event())


def test_fingerprint_ignores_timestamps():
    first = b.fingerprint(b.first_vevent(b.make_ical(_event())))
    second = b.fingerprint(b.first_vevent(b.make_ical(_event(), created=datetime(2020, 1, 1))))
    assert first == second


def test_fingerprint_detects_reminder_change():
    without = b.fingerprint(b.first_vevent(b.make_ical(_event())))
    with_alarm = b.fingerprint(b.first_vevent(b.make_ical(_event([timedelta(hours=-15)]))))
    assert without != with_alarm


def test_alarm_trigger():
    ical = b.make_ical(_event([timedelta(hours=-15)]))
    assert "BEGIN:VALARM" in ical
    assert "TRIGGER:-PT15H" in ical


def test_load_config(monkeypatch):
    monkeypatch.setenv("CARDDAV_URL", "https://nas/carddav/")
    monkeypatch.setenv("BIRTHDAY_CALENDAR", "Geburtstage")
    monkeypatch.setenv("BIRTHDAY_REMINDER", "1d 09:00")
    monkeypatch.setenv("BIRTHDAY_YEARS_AHEAD", "5")
    cfg = b.load_config()
    assert cfg.calendar == "Geburtstage"
    assert cfg.reminders == (timedelta(hours=-15),)
    assert cfg.years_ahead == 5


def test_disabled_without_carddav(monkeypatch):
    monkeypatch.delenv("CARDDAV_URL", raising=False)
    assert b.load_config() is None
