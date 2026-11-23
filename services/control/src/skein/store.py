"""领域库读写。所有写操作都走这里，宿主不直接拼 SQL。"""

from __future__ import annotations

import json
import sqlite3
import time

from skein import db
from skein.admission import Event


def thread_identity(event: Event) -> tuple[str, str]:
    """帖子与它的评论属于同一条 Thread；同一聊天会话也归一条 Thread。"""
    if event.source == "forum_comment":
        return "forum_post", event.parent_id or event.origin_id
    if event.source == "chat":
        return "chat", event.parent_id or event.origin_id
    return event.source, event.origin_id


def kv_get(conn: sqlite3.Connection, key: str, default: str | None = None) -> str | None:
    row = conn.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def kv_set(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO kv (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


def ensure_thread(conn: sqlite3.Connection, origin: str, origin_id: str) -> str:
    row = conn.execute(
        "SELECT id FROM threads WHERE origin = ? AND origin_id = ?", (origin, origin_id)
    ).fetchone()
    if row:
        return row["id"]
    thread_id = db.new_id()
    conn.execute(
        "INSERT INTO threads (id, origin, origin_id, created_at) VALUES (?, ?, ?, ?)",
        (thread_id, origin, origin_id, time.time()),
    )
    return thread_id


def record_event(
    conn: sqlite3.Connection,
    origin: str,
    origin_id: str,
    thread_id: str | None,
    payload: dict,
    verdict: str,
) -> str:
    event_id = db.new_id()
    conn.execute(
        "INSERT OR IGNORE INTO events"
        " (id, origin, origin_id, thread_id, payload, verdict, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (event_id, origin, origin_id, thread_id, json.dumps(payload, ensure_ascii=False), verdict, time.time()),
    )
    return event_id


def event_seen(conn: sqlite3.Connection, origin: str, origin_id: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM events WHERE origin = ? AND origin_id = ?", (origin, origin_id)
    ).fetchone()
    return row is not None


def create_ticket(
    conn: sqlite3.Connection,
    title: str,
    thread_id: str | None = None,
    dedup_key: str | None = None,
    summary: str = "",
) -> str:
    ticket_id = db.new_id()
    code = "SK-" + ticket_id[:8].upper()
    t = time.time()
    conn.execute(
        "INSERT INTO tickets (id, code, thread_id, title, summary, status, dedup_key, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?, 'staged', ?, ?, ?)",
        (ticket_id, code, thread_id, title, summary, dedup_key, t, t),
    )
    return ticket_id


def find_ticket_by_dedup(conn: sqlite3.Connection, dedup_key: str) -> str | None:
    row = conn.execute("SELECT id FROM tickets WHERE dedup_key = ?", (dedup_key,)).fetchone()
    return row["id"] if row else None


def assign(conn: sqlite3.Connection, ticket_id: str, worker_id: str) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO assignments (ticket_id, worker_id, state, updated_at)"
        " VALUES (?, ?, 'assigned', ?)",
        (ticket_id, worker_id, time.time()),
    )
    conn.execute(
        "UPDATE tickets SET status = 'assigned', updated_at = ? WHERE id = ? AND status = 'staged'",
        (time.time(), ticket_id),
    )
