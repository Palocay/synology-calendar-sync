# synology-calendar-sync

Adds two things Synology Calendar is missing:

- **Birthday calendar** – reads the birthdays from **Synology Contacts** (CardDAV) and creates all-day events like `🎂 Jane Doe (35)`, with optional reminders.
- **ICS subscriptions** – Synology Calendar can only import `.ics` files once. This tool subscribes to any iCal URL (waste collection, school holidays, sports fixtures, other calendars …) and keeps a Synology calendar in sync with it.

Runs as a small Docker container on a schedule. It only touches events it created itself and has protection against mass deletion. Images for `amd64` and `arm64`.

## Requirements

1. Create the target calendar(s) in **Synology Calendar**, e.g. `Birthdays`, `Waste collection`.
2. The **CalDAV URL**: `https://<nas>:5001/caldav/`
3. For birthdays: the **CardDAV URL** of your address book.

### Which DSM account?

The tool works with your normal DSM account. Recommended however is a dedicated **service user**:

1. DSM → Control Panel → User → create e.g. `calendar-sync`, allow only *Synology Calendar* and *Synology Contacts*.
2. In Synology Contacts, share your address book with this user (read-only is enough). Take the CardDAV URL from this user's Contacts settings.
3. Log in as this user and create the target calendars in Synology Calendar. Share them **read-only** with your own account.

This way the password in the container cannot access your files, and you cannot accidentally edit synced events.

## Installation (Container Manager / Dockhand / Portainer)

1. Use the [`docker-compose.yml`](docker-compose.yml) from this repository.
2. Set `DAV_USERNAME`, `DAV_PASSWORD`, `CALDAV_URL` and `CARDDAV_URL`, either as stack variables in your UI or in a `.env` file next to the compose file.
3. Add your ICS subscriptions (see below).
4. Start with `DRY_RUN: "true"` and check the log. If everything looks right, set it to `"false"` and redeploy.

No volumes are needed.

## Configuration

### General

| Variable | Default | Description |
|---|---|---|
| `DAV_USERNAME` / `DAV_PASSWORD` | – | DSM credentials |
| `DAV_USERNAME_FILE` / `DAV_PASSWORD_FILE` | – | alternatively: path to a file containing the value (Docker secrets) |
| `CALDAV_URL` | – | CalDAV base URL |
| `DRY_RUN` | `true` | only show what would happen, write nothing |
| `SCHEDULE_TIME` | `04:00` | first run of the day |
| `SCHEDULE_INTERVAL` | `24h` | default time between runs, e.g. `4h`, `30m` |
| `RUN_ON_START` | `true` | run all jobs when the container starts |
| `DELETE_STALE_EVENTS` | `true` | delete own events that disappeared from the source |
| `MAX_DELETE_PERCENT` | `50` | deletion protection, see below |
| `VERIFY_TLS` | `true` | verify TLS certificates (`false` for self-signed) |
| `LOG_LEVEL` | `INFO` | `DEBUG` lists every event |
| `TZ` | `Europe/Berlin` | time zone |

Runs happen at fixed times: `SCHEDULE_TIME: "04:00"` with `4h` means 00:00, 04:00, 08:00, … each job can have its own interval.

### Birthdays

Enabled when `CARDDAV_URL` is set.

| Variable | Default | Description |
|---|---|---|
| `CARDDAV_URL` | – | address book URL |
| `BIRTHDAY_CALENDAR` | `Birthdays` | target calendar |
| `BIRTHDAY_REMINDER` | *(none)* | reminder(s), see below |
| `BIRTHDAY_YEARS_AHEAD` | `10` | years to create in advance (1–20) |
| `BIRTHDAY_INTERVAL` | `SCHEDULE_INTERVAL` | time between runs |
| `BIRTHDAYS` | `true` | `false` disables birthdays even if `CARDDAV_URL` is set |

Events are named `🎂 Name (age)`, or `🎂 Name` if the birth year is unknown. A birthday on 29 February is shown on 28 February in non-leap years.

`BIRTHDAY_REMINDER` is relative to the birthday. Several reminders are separated by commas:

| Value | Reminder |
|---|---|
| `1d 09:00` | the day before at 09:00 |
| `08:00` | on the day at 08:00 |
| `7d` | one week before at 00:00 |
| `7d 18:00, 08:00` | one week before at 18:00 and on the day at 08:00 |

### ICS subscriptions

Number the subscriptions `1`, `2`, `3`, …

| Variable | Required | Description |
|---|---|---|
| `ICS_1_URL` | yes | URL of the calendar (`https://` or `webcal://`) |
| `ICS_1_CALENDAR` | yes | target calendar in Synology Calendar |
| `ICS_1_INTERVAL` | no | time between runs (default `SCHEDULE_INTERVAL`) |
| `ICS_1_NAME` | no | name in the log (default: calendar name) |
| `ICS_1_PAST_DAYS` | no | skip events that ended more than *n* days ago (default: keep all) |
| `ICS_1_USERNAME` / `ICS_1_PASSWORD` | no | if the URL requires HTTP basic auth |

Example:

```yaml
ICS_1_URL: "https://example.com/waste.ics"
ICS_1_CALENDAR: "Waste collection"
ICS_1_INTERVAL: "24h"

ICS_2_URL: "webcal://example.com/team.ics"
ICS_2_CALENDAR: "Football"
ICS_2_INTERVAL: "1h"
ICS_2_PAST_DAYS: "30"
```

Events, recurring events and their exceptions are copied as they are, including time zones. Several subscriptions can share one calendar. Changing the URL of a subscription recreates its events.

## Log output

Each job writes one line per run:

```
[birthdays] Done: 42 birthdays, 420 events - 0 created, 1 updated, 419 unchanged, 0 deleted (2.3s)
[ics:Waste collection] Done: 87 events - 2 created, 0 updated, 85 unchanged, 0 deleted (1.1s)
```

## Stale events and deletion protection

Own events that no longer exist in the source (contact deleted, birthday removed, event removed from the ICS feed) are deleted when `DELETE_STALE_EVENTS` is `true`. Events not created by this tool are never touched. Birthdays of previous years are kept.

**Deletion protection:** deleting is skipped (with a warning in the log) when

- the source returns nothing at all (server problem, wrong URL, missing permissions), or
- a run would remove more than `MAX_DELETE_PERCENT` of the job's events. Small changes (up to 2 contacts or 5 ICS events) are always allowed.

If a large cleanup is intended, set `MAX_DELETE_PERCENT: "100"` for one run.

## Health check

The container turns *healthy* after the first successful run and *unhealthy* if any job has not succeeded for two of its intervals (+10 minutes).

## Support

If this project is useful to you, you can [sponsor it on GitHub](https://github.com/sponsors/Palocay) or [buy me a coffee](https://ko-fi.com/palocay).