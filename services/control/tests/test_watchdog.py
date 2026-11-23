import time

import pytest

from skein import db, watchdog


@pytest.fixture()
def conn(tmp_path):
    c = db.connect(tmp_path / "watchdog.db")
    db.migrate(c)
    yield c
    c.close()


def seed_ticket(conn, code="SK-1"):
    t = time.time()
    conn.execute(
        "INSERT INTO tickets (id, code, title, status, created_at, updated_at)"
        " VALUES (?, ?, 'x', 'assigned', ?, ?)",
        (code, code, t, t),
    )
    return code


def test_clean_state_has_no_signals(conn):
    assert watchdog.signals(conn) == []


def test_dispatch_timeout_is_reported(conn):
    ticket = seed_ticket(conn)
    conn.execute(
        "INSERT INTO dispatches (id, ticket_id, node_id, state, updated_at)"
        " VALUES ('d1', ?, 'n1', 'dispatched', ?)",
        (ticket, time.time() - 7200),
    )
    found = watchdog.signals(conn, dispatch_timeout_s=3600)
    assert [s["kind"] for s in found] == ["dispatch_timeout"]
    assert found[0]["ticket_id"] == ticket


def test_failed_run_is_reported(conn):
    now = time.time()
    conn.execute(
        "INSERT INTO runs (id, job, day, status, started_at) VALUES ('r1', 'daily', '2026-11-18', 'failed', ?)",
        (now,),
    )
    kinds = {s["kind"] for s in watchdog.signals(conn)}
    assert kinds == {"run_failed"}


def test_offline_node_with_work(conn):
    ticket = seed_ticket(conn)
    conn.execute(
        "INSERT INTO nodes (id, status, last_seen) VALUES ('n1', 'offline', ?)", (time.time(),)
    )
    conn.execute(
        "INSERT INTO dispatches (id, ticket_id, node_id, state, updated_at)"
        " VALUES ('d1', ?, 'n1', 'dispatched', ?)",
        (ticket, time.time()),
    )
    found = watchdog.signals(conn)
    assert [s["kind"] for s in found] == ["node_offline_with_work"]
