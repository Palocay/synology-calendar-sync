# Changelog

## 1.1.3

- Fix logo on Docker Hub

## 1.1.2

- Docker image is now also published on Docker Hub

## 1.1.1

- New logo and redesigned README with badges, quick start and feature overview
- Docker image: icon label (`net.unraid.docker.icon`) for container managers that support it
- Docker image: updated description

## 1.1.0

- New: public holidays without ICS feed (`HOLIDAYS_CALENDAR`, `HOLIDAYS_COUNTRY`, `HOLIDAYS_STATE`), including German observances like Volkstrauertag or Muttertag (`HOLIDAYS_OBSERVANCES`)
- New: reminders for ICS subscriptions (`ICS_n_REMINDER`), replacing the feed's own reminders
- Reminders also accept durations before the start, e.g. `30m` or `2h`

## 1.0.3

- **Breaking:** new UID prefix for birthday events. Delete and recreate the birthday calendar before updating, otherwise existing birthdays appear twice.
- Removed old variable names `CALENDAR_NAME`, `REMINDER` and `YEARS_AHEAD`; use `BIRTHDAY_CALENDAR`, `BIRTHDAY_REMINDER` and `BIRTHDAY_YEARS_AHEAD`

## 1.0.2

- `latest` now points to the newest release, `main` to the newest commit

## 1.0.1

- ICS: remove leading/trailing whitespace from text fields (e.g. titles), avoids "Ical data was modified" warnings

## 1.0.0

First release.

- Birthday calendar from Synology Contacts (CardDAV) with optional reminders
- ICS subscriptions: mirror any iCal URL into a Synology calendar, with its own interval per subscription
- Fixed daily schedule (`SCHEDULE_TIME` + interval)
- Content-based change detection: unchanged events are not rewritten
- Stale events are deleted, with deletion protection (`MAX_DELETE_PERCENT`)
- `DAV_USERNAME_FILE` / `DAV_PASSWORD_FILE` for Docker secrets
- Docker health check, runs as non-root user, no volumes needed
- Multi-arch images (amd64, arm64), tests in CI, Dependabot
