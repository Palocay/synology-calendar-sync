<div align="center">

<img src="https://raw.githubusercontent.com/Palocay/synology-calendar-sync/main/assets/logo.png" alt="synology-calendar-sync logo" width="128">

# synology-calendar-sync

**Birthdays, public holidays and iCal subscriptions for Synology Calendar**

[![Release](https://img.shields.io/github/v/release/Palocay/synology-calendar-sync?logo=github)](https://github.com/Palocay/synology-calendar-sync/releases)
[![Build](https://img.shields.io/github/actions/workflow/status/Palocay/synology-calendar-sync/docker-publish.yml?branch=main&label=build&logo=githubactions&logoColor=white)](https://github.com/Palocay/synology-calendar-sync/actions)
[![Docker image](https://img.shields.io/badge/ghcr.io-synology--calendar--sync-2496ED?logo=docker&logoColor=white)](https://github.com/Palocay/synology-calendar-sync/pkgs/container/synology-calendar-sync)
[![Docker Hub](https://img.shields.io/docker/pulls/palocay/synology-calendar-sync?logo=docker&logoColor=white&label=Docker%20Hub)](https://hub.docker.com/r/palocay/synology-calendar-sync)
[![Platforms](https://img.shields.io/badge/platform-amd64%20%7C%20arm64-lightgrey)](#-installation)
[![Python](https://img.shields.io/badge/python-3.12-3776AB?logo=python&logoColor=white)](requirements.txt)
[![License](https://img.shields.io/github/license/Palocay/synology-calendar-sync)](LICENSE)

[![Sponsor](https://img.shields.io/badge/sponsor-GitHub-EA4AAA?logo=githubsponsors&logoColor=white)](https://github.com/sponsors/Palocay)
[![Ko-fi](https://img.shields.io/badge/buy%20me%20a%20coffee-Ko--fi-FF5E5B?logo=kofi&logoColor=white)](https://ko-fi.com/palocay)

</div>

---

## ✨ Features

- 🎂 **Birthday calendar**: reads the birthdays from **Synology Contacts** (CardDAV) and creates all-day events like `🎂 Jane Doe (35)`, with optional reminders.
- 🎉 **Public holidays**: holidays for all German states (or any other country), including observances like Volkstrauertag or Muttertag. No ICS feed needed.
- 🔄 **ICS subscriptions**: Synology Calendar can only import `.ics` files once. This tool subscribes to any iCal URL (waste collection, school holidays, sports fixtures, other calendars …) and keeps a Synology calendar in sync with it.
- 🔔 **Reminders** for birthdays, holidays and subscriptions.
- 🛡️ **Safe**: only touches events it created itself, with protection against mass deletion.
- 🐳 **Small Docker container** for `amd64` and `arm64`, no volumes needed.

## 🚀 Quick start

```yaml
services:
  synology-calendar-sync:
    image: ghcr.io/palocay/synology-calendar-sync:latest
    container_name: synology-calendar-sync
    environment:
      TZ: Europe/Berlin
      DAV_USERNAME: your-dsm-user
      DAV_PASSWORD: your-password
      CALDAV_URL: https://nas.example.com:5001/caldav/
      DRY_RUN: "true"                     # check the log first, then set to "false"
      CARDDAV_URL: https://nas.example.com:5001/carddav/...
      BIRTHDAY_CALENDAR: "Birthdays"
      HOLIDAYS_CALENDAR: "Holidays"
    restart: unless-stopped
```

The full example with all options is in [`docker-compose.yml`](docker-compose.yml).

## 📋 Requirements

1. Create the target calendar(s) in **Synology Calendar**, e.g. `Birthdays`, `Holidays`, `Waste collection`.
2. The **CalDAV URL**: `https://<nas>:5001/caldav/`
3. For birthdays: the **CardDAV URL** of your address book.

### 👤 Which DSM account?

The tool works with your normal DSM account. A dedicated **service user** is recommended though:

1. DSM → Control Panel → User → create e.g. `calendar-sync`, allow only *Synology Calendar* and *Synology Contacts*.
2. In Synology Contacts, share your address book with this user (read-only is enough). Take the CardDAV URL from this user's Contacts settings.
3. Log in as this user and create the target calendars in Synology Calendar. Share them **read-only** with your own account.

This way the password in the container cannot access your files, and you cannot accidentally edit synced events.

## 📦 Installation

1. Use the [`docker-compose.yml`](docker-compose.yml) from this repository.
2. Set `DAV_USERNAME`, `DAV_PASSWORD`, `CALDAV_URL` and `CARDDAV_URL`, either as stack variables in your UI or in a `.env` file next to the compose file.
3. Add your holidays and ICS subscriptions (see below).
4. Start with `DRY_RUN: "true"` and check the log. If everything looks right, set it to `"false"` and redeploy.

## ⚙️ Configuration

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

Runs happen at fixed times: `SCHEDULE_TIME: "04:00"` with `4h` means 00:00, 04:00, 08:00, … Each job can have its own interval.

### 🎂 Birthdays

Enabled when `CARDDAV_URL` is set.

| Variable | Default | Description |
|---|---|---|
| `CARDDAV_URL` | – | address book URL |
| `BIRTHDAY_CALENDAR` | `Birthdays` | target calendar |
| `BIRTHDAY_REMINDER` | *(none)* | reminder(s), see [Reminders](#-reminders) |
| `BIRTHDAY_YEARS_AHEAD` | `10` | years to create in advance (1–20) |
| `BIRTHDAY_INTERVAL` | `SCHEDULE_INTERVAL` | time between runs |
| `BIRTHDAYS` | `true` | `false` disables birthdays even if `CARDDAV_URL` is set |

Events are named `🎂 Name (age)`, or `🎂 Name` if the birth year is unknown. A birthday on 29 February is shown on 28 February in non-leap years.

### 🎉 Public holidays

Enabled when `HOLIDAYS_CALENDAR` is set. Holidays come from the [holidays](https://github.com/vacanza/holidays) library, so no ICS feed is needed.

| Variable | Default | Description |
|---|---|---|
| `HOLIDAYS_CALENDAR` | – | target calendar |
| `HOLIDAYS_COUNTRY` | `DE` | country code, e.g. `AT`, `CH`, `US` |
| `HOLIDAYS_STATE` | *(all)* | only holidays valid in this state, e.g. `BY` or `Bayern` |
| `HOLIDAYS_OBSERVANCES` | `true` | German observances (see below) |
| `HOLIDAYS_REMINDER` | *(none)* | reminder(s), see [Reminders](#-reminders) |
| `HOLIDAYS_YEARS_AHEAD` | `3` | years to create in advance (1–10) |
| `HOLIDAYS_INTERVAL` | `24h` | time between runs |
| `HOLIDAYS_LANGUAGE` | country default | language of the holiday names, e.g. `en_US` |

Without `HOLIDAYS_STATE`, holidays of all states are added and the description says where they apply, e.g. *Gesetzlicher Feiertag in: Bayern, Baden-Württemberg, …* or *Gesetzlicher Feiertag (bundesweit)*.

**Observances** (Germany only) are days that are not public holidays: Valentinstag, Rosenmontag, Aschermittwoch, Muttertag, Beginn/Ende der Sommerzeit, Erntedankfest, Halloween, Martinstag, Volkstrauertag, Totensonntag, Advent Sundays, Nikolaus, Heiligabend, Silvester and others. Their description says *Gedenk- bzw. Aktionstag, kein gesetzlicher Feiertag*.

### 🔄 ICS subscriptions

Number the subscriptions `1`, `2`, `3`, …

| Variable | Required | Description |
|---|---|---|
| `ICS_1_URL` | yes | URL of the calendar (`https://` or `webcal://`) |
| `ICS_1_CALENDAR` | yes | target calendar in Synology Calendar |
| `ICS_1_INTERVAL` | no | time between runs (default `SCHEDULE_INTERVAL`) |
| `ICS_1_NAME` | no | name in the log (default: calendar name) |
| `ICS_1_PAST_DAYS` | no | skip events that ended more than *n* days ago (default: keep all) |
| `ICS_1_REMINDER` | no | reminder(s), replaces the feed's own reminders, see [Reminders](#-reminders) |
| `ICS_1_USERNAME` / `ICS_1_PASSWORD` | no | if the URL requires HTTP basic auth |

Example:

```yaml
ICS_1_URL: "https://example.com/waste.ics"
ICS_1_CALENDAR: "Waste collection"
ICS_1_INTERVAL: "24h"
ICS_1_REMINDER: "1d 18:00"

ICS_2_URL: "webcal://example.com/team.ics"
ICS_2_CALENDAR: "Football"
ICS_2_INTERVAL: "1h"
ICS_2_PAST_DAYS: "30"
```

Events, recurring events and their exceptions are copied as they are, including time zones. Several subscriptions can share one calendar. Changing the URL of a subscription recreates its events.

### 🔔 Reminders

`BIRTHDAY_REMINDER`, `HOLIDAYS_REMINDER` and `ICS_n_REMINDER` use the same format. Several reminders are separated by commas.

| Value | Reminder |
|---|---|
| `1d 09:00` | the day before at 09:00 |
| `08:00` | on the day at 08:00 |
| `7d 18:00, 08:00` | one week before at 18:00 and on the day at 08:00 |
| `30m`, `2h` | 30 minutes / 2 hours before the start |
| `1d` | 24 hours before the start (all-day events: the day before at 00:00) |

Changing a reminder setting updates all events of that job on the next run.

## 📝 Log output

Each job writes one line per run:

```
[birthdays] Done: 42 birthdays, 420 events - 0 created, 1 updated, 419 unchanged, 0 deleted (2.3s)
[holidays] Done: 63 holidays, 156 events - 0 created, 0 updated, 156 unchanged, 0 deleted (0.9s)
[ics:Waste collection] Done: 87 events - 2 created, 0 updated, 85 unchanged, 0 deleted (1.1s)
```

## 🛡️ Stale events and deletion protection

Own events that no longer exist in the source (contact deleted, birthday removed, holiday no longer in range, event removed from the ICS feed) are deleted when `DELETE_STALE_EVENTS` is `true`. Events not created by this tool are never touched. Birthdays and holidays of previous years are kept.

**Deletion protection:** deleting is skipped (with a warning in the log) when

- the source returns nothing at all (server problem, wrong URL, missing permissions), or
- a run would remove more than `MAX_DELETE_PERCENT` of the job's events. Small changes (up to 2 contacts or 5 ICS events) are always allowed.

If a large cleanup is intended, set `MAX_DELETE_PERCENT: "100"` for one run.

## 💚 Health check

The container turns *healthy* after the first successful run and *unhealthy* if any job has not succeeded for two of its intervals (+10 minutes).

## ☕ Support

If this project is useful to you, you can [sponsor it on GitHub](https://github.com/sponsors/Palocay) or [buy me a coffee](https://ko-fi.com/palocay).