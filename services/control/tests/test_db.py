import sqlite3

import pytest

from skein import db


@pytest.fixture()
def conn(tmp_path):
    c = db.connect(tmp_path / "test.db")
    db.migrate(c)
    yield c
    c.close()


def test_migration_is_idempotent(conn):
    assert db.migrate(conn) == db.SCHEMA_VERSION
    assert db.migrate(conn) == db.SCHEMA_VERSION


def test_events_unique_per_origin(conn):
    row = (db.new_id(), "forum_comment", "42", None, "{}", "record", db.now())
    conn.execute(
        "INSERT INTO events (id, origin, origin_id, thread_id, payload, verdict, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        row,
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO events (id, origin, origin_id, thread_id, payload, verdict, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (db.new_id(), "forum_comment", "42", None, "{}", "record", db.now()),
        )


def test_ticket_code_unique(conn):
    t = db.now()
    conn.execute(
        "INSERT INTO tickets (id, code, title, status, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        ("t1", "YZ-1", "a", "staged", t, t),
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO tickets (id, code, title, status, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            ("t2", "YZ-1", "b", "staged", t, t),
        )


def test_dedup_key_unique(conn):
    t = db.now()
    conn.execute(
        "INSERT INTO tickets (id, code, title, status, dedup_key, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("t8", "SK-8", "a", "staged", "forum:c1", t, t),
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO tickets (id, code, title, status, dedup_key, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("t9", "SK-9", "b", "staged", "forum:c1", t, t),
        )


def test_foreign_keys_are_enforced(conn):
    t = db.now()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO tickets (id, code, thread_id, title, status, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("t3", "YZ-2", "missing-thread", "x", "staged", t, t),
        )


def test_runs_unique_per_job_and_day(conn):
    t = db.now()
    conn.execute(
        "INSERT INTO runs (id, job, day, status, started_at) VALUES (?, ?, ?, ?, ?)",
        ("r1", "daily", "2026-11-18", "running", t),
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO runs (id, job, day, status, started_at) VALUES (?, ?, ?, ?, ?)",
            ("r2", "daily", "2026-11-18", "running", t),
        )


def test_audit_appends(conn):
    db.record_audit(conn, "router", "dispatch", {"ticket": "YZ-1"})
    db.record_audit(conn, "watchdog", "prompt_propose", {"part": "runtime/work"})
    rows = conn.execute("SELECT actor, action FROM audit ORDER BY id").fetchall()
    assert [tuple(r) for r in rows] == [
        ("router", "dispatch"),
        ("watchdog", "prompt_propose"),
    ]


def test_upgrade_from_the_previous_schema_keeps_data(tmp_path):
    """从 schema 12 的库升级：老数据原样保留，新的表与列补齐，重复执行无副作用。"""
    conn = db.connect(tmp_path / "old.db")
    db.migrate(conn)
    # 退回 schema 12 的形状：去掉身份层的东西，把版本号改回去
    conn.executescript(
        "DROP TABLE assistants;"
        " DROP TABLE login_sessions;"
        " DROP TABLE identities;"
        " DROP TABLE users;"
        " ALTER TABLE tickets DROP COLUMN submitted_by;"
        " ALTER TABLE dispatches DROP COLUMN pr_base;"
        f" PRAGMA user_version = {db.SCHEMA_VERSION - 1};"
    )
    t = db.now()
    conn.execute(
        "INSERT INTO tickets (id, code, title, status, created_at, updated_at)"
        " VALUES ('t-old', 'SK-OLD', '升级前就有的工单', 'staged', ?, ?)",
        (t, t),
    )
    conn.execute("INSERT INTO audit (actor, action, detail, created_at) VALUES ('a', 'b', '{}', ?)", (t,))

    assert db.migrate(conn) == db.SCHEMA_VERSION
    assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
    assert conn.execute("SELECT title FROM tickets WHERE id = 't-old'").fetchone()[0] == "升级前就有的工单"
    columns = {row[1] for row in conn.execute("PRAGMA table_info(tickets)")}
    assert "submitted_by" in columns
    assert conn.execute("SELECT submitted_by FROM tickets WHERE id = 't-old'").fetchone()[0] == ""
    dispatch_columns = {row[1] for row in conn.execute("PRAGMA table_info(dispatches)")}
    assert "pr_base" in dispatch_columns
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {"users", "identities", "login_sessions", "assistants"} <= tables
    assert conn.execute("SELECT COUNT(*) FROM audit").fetchone()[0] == 1
    # 再跑一遍什么都不改
    assert db.migrate(conn) == db.SCHEMA_VERSION
    assert conn.execute("SELECT COUNT(*) FROM audit").fetchone()[0] == 1
    conn.close()


def test_a_newer_database_is_left_alone(tmp_path):
    """比当前代码更新的库：不改它的版本号（新代码不该动它不认识的库）。"""
    conn = db.connect(tmp_path / "newer.db")
    db.migrate(conn)
    conn.execute(f"PRAGMA user_version = {db.SCHEMA_VERSION + 1}")
    assert db.migrate(conn) == db.SCHEMA_VERSION + 1
    conn.close()
