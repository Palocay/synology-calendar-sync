# Changelog

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
