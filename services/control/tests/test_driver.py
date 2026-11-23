from datetime import datetime

import pytest

from skein import db, runs
from skein.driver import Driver, cron_matches, parse_schedules


@pytest.fixture()
def conn(tmp_path):
    c = db.connect(tmp_path / "driver.db")
    db.migrate(c)
    yield c
    c.close()


class FakeRunner:
    def __init__(self, ok=True):
        self.ok = ok
        self.calls = []

    def __call__(self, command, timeout):
        self.calls.append((command, timeout))
        return self.ok, "done" if self.ok else "boom"


def test_parse_schedules_skips_invalid():
    schedules = parse_schedules('[{"job":"daily","at":"09:15"},{"job":"","at":"10:00"},{"job":"x","at":"bad"}]')
    assert [(s.job, s.at) for s in schedules] == [("daily", "09:15")]


def test_due_jobs_matches_minute():
    driver = Driver(conn=None, schedules=parse_schedules('[{"job":"daily","at":"09:15"}]'), commands={})
    assert driver.due_jobs(datetime(2026, 11, 18, 9, 15)) == ["daily"]
    assert driver.due_jobs(datetime(2026, 11, 18, 9, 16)) == []


def test_parse_cron_schedule():
    schedules = parse_schedules(
        '[{"job":"ingest","cron":"7 */2 * * *"},{"job":"star","cron":"25 9 * * 6"},'
        '{"job":"bad","cron":"x y"}]'
    )
    assert [(s.job, s.cron) for s in schedules] == [
        ("ingest", "7 */2 * * *"),
        ("star", "25 9 * * 6"),
    ]


def test_cron_every_two_hours():
    assert cron_matches("7 */2 * * *", datetime(2026, 11, 19, 10, 7))
    assert cron_matches("7 */2 * * *", datetime(2026, 11, 19, 0, 7))
    assert not cron_matches("7 */2 * * *", datetime(2026, 11, 19, 10, 8))
    assert not cron_matches("7 */2 * * *", datetime(2026, 11, 19, 11, 7))


def test_cron_weekday_and_sunday_aliases():
    assert cron_matches("25 9 * * 6", datetime(2026, 11, 21, 9, 25))
    assert not cron_matches("25 9 * * 6", datetime(2026, 11, 20, 9, 25))
    assert cron_matches("0 10 * * 0", datetime(2026, 11, 22, 10, 0))
    assert cron_matches("0 10 * * 7", datetime(2026, 11, 22, 10, 0))


def test_cron_ranges_and_due_jobs():
    driver = Driver(
        conn=None,
        schedules=parse_schedules('[{"job":"ingest","cron":"7 */2 * * *"}]'),
        commands={},
    )
    assert driver.due_jobs(datetime(2026, 11, 19, 14, 7)) == ["ingest"]
    assert driver.due_jobs(datetime(2026, 11, 19, 14, 8)) == []
    assert cron_matches("0 9-10 * * 1-5", datetime(2026, 11, 19, 9, 0))
    assert not cron_matches("0 9-10 * * 1-5", datetime(2026, 11, 21, 9, 0))


def test_tick_runs_command_once_per_slot(conn):
    runner = FakeRunner()
    driver = Driver(
        conn,
        parse_schedules('[{"job":"daily","at":"09:15"}]'),
        {"daily": "python main.py daily"},
        runner=runner,
        inline=True,
    )
    now = datetime(2026, 11, 18, 9, 15)
    assert len(driver.tick(now)) == 1
    assert runner.calls == [("python main.py daily", 4 * 3600)]
    run = runs.by_day(conn, "daily", "2026-11-18T09:15")
    assert run.status == "succeeded"

    assert driver.tick(now) == []
    assert len(runner.calls) == 1


def test_tick_allows_two_slots_same_day(conn):
    runner = FakeRunner()
    driver = Driver(
        conn,
        parse_schedules('[{"job":"ingest","cron":"7 */2 * * *"}]'),
        {"ingest": "true"},
        runner=runner,
        inline=True,
    )
    assert len(driver.tick(datetime(2026, 11, 18, 0, 7))) == 1
    assert len(driver.tick(datetime(2026, 11, 18, 2, 7))) == 1
    assert len(runner.calls) == 2
    assert runs.by_day(conn, "ingest", "2026-11-18T00:07").status == "succeeded"
    assert runs.by_day(conn, "ingest", "2026-11-18T02:07").status == "succeeded"


def test_missing_command_is_skipped(conn):
    driver = Driver(
        conn,
        parse_schedules('[{"job":"curator","at":"10:20"}]'),
        {},
        runner=FakeRunner(),
        inline=True,
    )
    assert driver.tick(datetime(2026, 11, 18, 10, 20)) == []
    run = runs.by_day(conn, "curator", "2026-11-18T10:20")
    assert run.status == "skipped"


def test_failed_command_marks_failed(conn):
    driver = Driver(
        conn,
        parse_schedules('[{"job":"daily","at":"09:15"}]'),
        {"daily": "false"},
        runner=FakeRunner(ok=False),
        inline=True,
    )
    driver.tick(datetime(2026, 11, 18, 9, 15))
    assert runs.by_day(conn, "daily", "2026-11-18T09:15").status == "failed"
