import time

import pytest

from skein import db, scheduler


@pytest.fixture()
def conn(tmp_path):
    c = db.connect(tmp_path / "sched.db")
    db.migrate(c)
    yield c
    c.close()


def make_ticket(conn, code="SK-1", platform=None):
    t = time.time()
    conn.execute(
        "INSERT INTO tickets (id, code, title, status, created_at, updated_at)"
        " VALUES (?, ?, 'x', 'staged', ?, ?)",
        (code, code, t, t),
    )
    return code


def test_upsert_and_list_online(conn):
    scheduler.upsert_node(conn, "n1", name="node-1", platforms=["forum"], max_workers=2)
    rows = scheduler.online(conn)
    assert [r["id"] for r in rows] == ["n1"]
    assert rows[0]["max_workers"] == 2


def test_stale_node_is_excluded(conn):
    scheduler.upsert_node(conn, "n1")
    conn.execute("UPDATE nodes SET last_seen = ? WHERE id = 'n1'", (time.time() - 120,))
    assert scheduler.online(conn) == []


def test_heartbeat_brings_node_back(conn):
    scheduler.upsert_node(conn, "n1")
    conn.execute("UPDATE nodes SET last_seen = ?, status = 'offline' WHERE id = 'n1'", (0,))
    scheduler.touch(conn, "n1")
    assert [r["id"] for r in scheduler.online(conn)] == ["n1"]


def test_capacity_is_respected(conn):
    scheduler.upsert_node(conn, "n1", max_workers=1)
    ticket = make_ticket(conn)
    assert scheduler.dispatch(conn, ticket) is not None
    assert scheduler.pick_node(conn) is None


def test_platform_filter(conn):
    scheduler.upsert_node(conn, "n1", platforms=["other-service"])
    assert scheduler.pick_node(conn, platform="forum") is None
    scheduler.upsert_node(conn, "n2", platforms=["forum"])
    assert scheduler.pick_node(conn, platform="forum") == "n2"


def test_wildcard_node_accepts_any_platform(conn):
    scheduler.upsert_node(conn, "n1", platforms=["*"])
    assert scheduler.pick_node(conn, platform="forum") == "n1"


def test_online_platforms_union_sorted(conn):
    scheduler.upsert_node(conn, "n1", platforms=["skein", "forum"])
    scheduler.upsert_node(conn, "n2", platforms=["forum", "mirror"])
    scheduler.upsert_node(conn, "n3", platforms="not-json")
    conn.execute("UPDATE nodes SET last_seen = ? WHERE id = 'n2'", (0,))
    assert scheduler.online_platforms(conn) == ["forum", "skein"]


def test_online_platforms_empty_without_nodes(conn):
    assert scheduler.online_platforms(conn) == []


def test_dispatch_sets_ticket_assigned(conn):
    scheduler.upsert_node(conn, "n1")
    ticket = make_ticket(conn)
    dispatch_id = scheduler.dispatch(conn, ticket)
    assert dispatch_id is not None
    row = conn.execute("SELECT ticket_id, node_id, state FROM dispatches").fetchone()
    assert tuple(row) == (ticket, "n1", "pending")
    status = conn.execute("SELECT status FROM tickets WHERE id = ?", (ticket,)).fetchone()[0]
    assert status == "assigned"


def test_dispatch_without_node_leaves_ticket_staged(conn):
    ticket = make_ticket(conn)
    assert scheduler.dispatch(conn, ticket) is None
    status = conn.execute("SELECT status FROM tickets WHERE id = ?", (ticket,)).fetchone()[0]
    assert status == "staged"


def test_update_dispatch(conn):
    scheduler.upsert_node(conn, "n1")
    ticket = make_ticket(conn)
    dispatch_id = scheduler.dispatch(conn, ticket)
    scheduler.update_dispatch(conn, dispatch_id, state="created", worker_id="w1", detail="ok")
    row = conn.execute("SELECT state, worker_id, detail FROM dispatches").fetchone()
    assert tuple(row) == ("created", "w1", "ok")
