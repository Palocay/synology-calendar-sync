"""Entry point: builds the jobs and runs them on their schedules."""
from __future__ import annotations

import argparse
import logging
import signal
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

from . import birthdays, ics, public_holidays
from .common import (
    APP_VERSION, TZ, daily_slots, env_bool, env_str, format_slots, load_settings,
    next_run, parse_hhmm, parse_interval, setup_logging, sleep_until,
)

HEALTH_FILE = Path("/tmp/synology-calendar-sync.health")
log = logging.getLogger("scheduler")


@dataclass
class Job:
    name: str
    interval_min: int
    slots: list[int]
    run: Callable[[], None]


def build_jobs() -> list[Job]:
    settings = load_settings()
    start_min = parse_hhmm(env_str("SCHEDULE_TIME", default="04:30"))
    default_interval = env_str("SCHEDULE_INTERVAL", default="24h")
    jobs = []

    bcfg = birthdays.load_config()
    if bcfg:
        minutes = parse_interval(bcfg.interval, "BIRTHDAY_INTERVAL")
        jobs.append(Job("birthdays", minutes, daily_slots(start_min, minutes),
                        lambda: birthdays.run(bcfg, settings)))

    hcfg = public_holidays.load_config()
    if hcfg:
        minutes = parse_interval(hcfg.interval, "HOLIDAYS_INTERVAL")
        jobs.append(Job("holidays", minutes, daily_slots(start_min, minutes),
                        lambda: public_holidays.run(hcfg, settings)))

    for feed in ics.load_feeds(default_interval):
        minutes = parse_interval(feed.interval, f"ICS_{feed.number}_INTERVAL")
        jobs.append(Job(f"ics:{feed.name}", minutes, daily_slots(start_min, minutes),
                        lambda feed=feed: ics.run(feed, settings)))

    if not jobs:
        raise RuntimeError("Nothing to do: set CARDDAV_URL (birthdays), HOLIDAYS_CALENDAR "
                           "and/or ICS_1_URL (subscriptions).")
    if settings.dry_run:
        log.warning("DRY_RUN is active - nothing will be written. Set DRY_RUN=false when ready.")
    return jobs


# --------------------------------------------------------------------------- #
# Health
# --------------------------------------------------------------------------- #
_deadlines: dict[str, float] = {}


def _write_health() -> None:
    try:
        HEALTH_FILE.write_text(str(int(min(_deadlines.values()))), encoding="utf-8")
    except (OSError, ValueError):
        pass


def mark_healthy(job: Job) -> None:
    """Each job must succeed at least once every two intervals (+10 min)."""
    _deadlines[job.name] = time.time() + job.interval_min * 60 * 2 + 600
    _write_health()


def healthcheck() -> int:
    try:
        return 0 if time.time() <= float(HEALTH_FILE.read_text(encoding="utf-8")) else 1
    except Exception:
        return 1


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def run_job(job: Job) -> None:
    try:
        job.run()
        mark_healthy(job)
    except Exception as exc:
        # Full traceback only at DEBUG level, otherwise a single line.
        logger = logging.getLogger(job.name)
        if logger.isEnabledFor(logging.DEBUG):
            logger.exception("Sync failed.")
        else:
            logger.error("Sync failed: %s: %s", type(exc).__name__, exc)


def main() -> None:
    parser = argparse.ArgumentParser(description="Birthdays and ICS subscriptions for Synology Calendar.")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--once", action="store_true", help="Run all jobs once and exit (default)")
    group.add_argument("--loop", action="store_true", help="Run continuously on a schedule")
    group.add_argument("--healthcheck", action="store_true", help="Status for Docker HEALTHCHECK")
    args = parser.parse_args()

    if args.healthcheck:
        sys.exit(healthcheck())

    setup_logging()
    # Exit cleanly on "docker stop" (Python as PID 1 ignores SIGTERM otherwise).
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))

    try:
        jobs = build_jobs()
    except RuntimeError as exc:
        log.error("Configuration error: %s", exc)
        sys.exit(2)

    if not args.loop:
        for job in jobs:
            run_job(job)
        return

    log.info("synology-calendar-sync %s (%s)", APP_VERSION, TZ.key)
    for job in jobs:
        log.info("Job %s: %s", job.name, format_slots(job.slots))
        # Start the health clock, so a job that never succeeds turns the container unhealthy.
        _deadlines[job.name] = time.time() + job.interval_min * 60 * 2 + 600
    _write_health()

    if env_bool("RUN_ON_START", True):
        for job in jobs:
            run_job(job)

    while True:
        now = datetime.now(TZ)
        upcoming = [(next_run(now, job.slots), job) for job in jobs]
        target = min(t for t, _ in upcoming)
        due = [job for t, job in upcoming if t == target]
        log.debug("Next run: %s (%s)", target.strftime("%Y-%m-%d %H:%M"),
                  ", ".join(j.name for j in due))
        sleep_until(target)
        for job in due:
            run_job(job)


if __name__ == "__main__":
    main()
