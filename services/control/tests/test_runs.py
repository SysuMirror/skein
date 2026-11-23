import time

import pytest

from skein import db, runs


@pytest.fixture()
def conn(tmp_path):
    c = db.connect(tmp_path / "runs.db")
    db.migrate(c)
    yield c
    c.close()


def test_start_is_unique_per_job_and_day(conn):
    first = runs.start(conn, "daily", "2026-11-18")
    assert first is not None
    assert runs.start(conn, "daily", "2026-11-18") is None
    assert runs.start(conn, "daily", "2026-11-19") is not None


def test_finish_updates_status(conn):
    run_id = runs.start(conn, "daily", "2026-11-18")
    runs.finish(conn, run_id, "succeeded", "ok")
    run = runs.get(conn, run_id)
    assert run.status == "succeeded"
    assert run.detail == "ok"


def test_by_day_and_recent(conn):
    a = runs.start(conn, "daily", "2026-11-18")
    runs.start(conn, "curator", "2026-11-18")
    assert runs.by_day(conn, "daily", "2026-11-18").id == a
    assert len(runs.recent(conn, 10)) == 2


def test_stale_lists_only_old_running(conn):
    old = runs.start(conn, "daily", "2026-11-17")
    conn.execute("UPDATE runs SET started_at = ? WHERE id = ?", (time.time() - 7200, old))
    fresh = runs.start(conn, "curator", "2026-11-18")
    stale = runs.stale(conn, 3600)
    assert [r.id for r in stale] == [old]
    assert fresh not in [r.id for r in stale]
