"""Cron expressions generated for the Cloud Scheduler jobs of each site."""
from datetime import datetime, timezone

import pytest

from app.services.cloud_scheduler_service import _to_cron

WEEK = 7 * 24 * 60
# Monday 2026-06-01 16:30 UTC = 17:30 in Lisbon (summer time)
MONDAY_1630_UTC = datetime(2026, 6, 1, 16, 30, tzinfo=timezone.utc)


def test_weekly_interval_runs_on_the_weekday_of_start_at_not_every_day():
    assert _to_cron(WEEK, MONDAY_1630_UTC) == "30 16 * * 1"


def test_start_at_is_read_in_the_schedule_timezone():
    # Same instant, but the job is evaluated in Europe/Lisbon, so the cron must say 17:30
    assert _to_cron(WEEK, MONDAY_1630_UTC, "Europe/Lisbon") == "30 17 * * 1"


def test_weekday_follows_the_timezone_across_midnight():
    # Sunday 2026-05-31 23:30 UTC is already Monday 00:30 in Lisbon
    sunday_late = datetime(2026, 5, 31, 23, 30, tzinfo=timezone.utc)
    assert _to_cron(WEEK, sunday_late, "UTC") == "30 23 * * 0"
    assert _to_cron(WEEK, sunday_late, "Europe/Lisbon") == "30 0 * * 1"


def test_sunday_is_zero_in_cron():
    sunday = datetime(2026, 5, 31, 1, 0, tzinfo=timezone.utc)
    assert _to_cron(WEEK, sunday, "UTC") == "0 1 * * 0"


def test_longer_multiples_of_a_week_still_run_weekly():
    assert _to_cron(2 * WEEK, MONDAY_1630_UTC, "UTC") == "30 16 * * 1"


def test_weekly_without_start_at_defaults_to_sunday_midnight():
    assert _to_cron(WEEK, None) == "0 0 * * 0"


def test_daily_interval_unchanged():
    assert _to_cron(1440, MONDAY_1630_UTC) == "30 16 * * *"
    assert _to_cron(1440, MONDAY_1630_UTC, "Europe/Lisbon") == "30 17 * * *"


def test_two_days_is_still_daily_cron_for_back_compat():
    assert _to_cron(2880, MONDAY_1630_UTC) == "30 16 * * *"


@pytest.mark.parametrize(
    ("minutes", "expected"),
    [(15, "*/15 * * * *"), (60, "0 * * * *"), (120, "0 */2 * * *"), (90, "0 */2 * * *")],
)
def test_sub_daily_intervals_unchanged(minutes, expected):
    assert _to_cron(minutes, None) == expected


def test_unknown_timezone_falls_back_to_the_stored_value():
    assert _to_cron(WEEK, MONDAY_1630_UTC, "Mars/Olympus") == "30 16 * * 1"


def test_naive_start_at_is_treated_as_utc_when_a_timezone_is_given():
    naive = datetime(2026, 6, 1, 16, 30)
    assert _to_cron(WEEK, naive, "Europe/Lisbon") == "30 17 * * 1"
