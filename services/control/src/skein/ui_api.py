"""控制台查询：工单、事件、会话、拓扑、审计，以及准入策略。

payload 与 detail 能解析成 JSON 就解析，失败则保留原字符串。时间仍是 Unix 秒。
读接口不写库。准入策略的 PUT 写 kv 和审计。不引用 api，避免和 create_app 循环依赖。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.params import Depends as DependsParam
from fastapi.responses import StreamingResponse

from skein import watchdog
from skein.session_store import blob_path
from skein.summary import summary as build_summary

_TICKET_FIELDS = (
    "id",
    "code",
    "thread_id",
    "title",
    "summary",
    "status",
    "dedup_key",
    "created_at",
    "updated_at",
)
_THREAD_FIELDS = ("id", "origin", "origin_id", "created_at")
_ASSIGNMENT_FIELDS = (
    "ticket_id",
    "worker_id",
    "state",
    "lease_until",
    "result",
    "updated_at",
)
_DISPATCH_FIELDS = (
    "id",
    "ticket_id",
    "node_id",
    "worker_id",
    "state",
    "platform",
    "attempt",
    "detail",
    "pr_url",
    "pr_base",
    "updated_at",
)
_SESSION_FIELDS = ("id", "ticket_id", "node_id", "pinned", "created_at", "updated_at")
_EVENT_FIELDS = (
    "id",
    "origin",
    "origin_id",
    "thread_id",
    "verdict",
    "created_at",
    "payload",
)
_TURN_FIELDS = (
    "session_id",
    "turn_index",
    "sha256",
    "bytes",
    "head_sha",
    "branch",
    "created_at",
)
_NODE_FIELDS = ("id", "name", "host", "platforms", "status", "max_workers", "last_seen")

_JSON_PAYLOAD = frozenset({"payload"})
_JSON_DETAIL = frozenset({"detail"})

_EVENT_CAP = 500
_TOPOLOGY_TICKETS = 200
_TOPOLOGY_DISPATCHES = 500
_TOPOLOGY_SESSIONS = 500


def _parse_json(value: object) -> object:
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def _pick(
    row: sqlite3.Row,
    fields: tuple[str, ...],
    json_fields: frozenset[str] | None = None,
) -> dict:
    parsed = json_fields or frozenset()
    out: dict = {}
    for name in fields:
        value = row[name]
        if name in parsed:
            value = _parse_json(value)
        out[name] = value
    return out


def _rows(
    conn: sqlite3.Connection,
    sql: str,
    params: tuple,
    fields: tuple[str, ...],
    json_fields: frozenset[str] | None = None,
) -> list[dict]:
    return [_pick(row, fields, json_fields) for row in conn.execute(sql, params)]


def _first(
    conn: sqlite3.Connection,
    sql: str,
    params: tuple,
    fields: tuple[str, ...],
    json_fields: frozenset[str] | None = None,
) -> dict | None:
    row = conn.execute(sql, params).fetchone()
    if row is None:
        return None
    return _pick(row, fields, json_fields)


def _nonneg(limit: int) -> int:
    # SQLite 把 LIMIT -1 当成「不限制」。
    return max(0, limit)


def _cap(limit: int, cap: int) -> int:
    return min(_nonneg(limit), cap)


def _events(
    conn: sqlite3.Connection,
    *,
    since: float | None = None,
    verdict: str | None = None,
    origin: str | None = None,
    thread_id: str | None = None,
    limit: int | None = None,
    newest_first: bool = True,
) -> list[dict]:
    sql = (
        "SELECT id, origin, origin_id, thread_id, verdict, created_at, payload"
        " FROM events"
    )
    clauses: list[str] = []
    params: list[object] = []
    if since is not None:
        clauses.append("created_at > ?")
        params.append(since)
    if verdict:
        clauses.append("verdict = ?")
        params.append(verdict)
    if origin:
        clauses.append("origin = ?")
        params.append(origin)
    if thread_id is not None:
        clauses.append("thread_id = ?")
        params.append(thread_id)
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    direction = "DESC" if newest_first else "ASC"
    sql += f" ORDER BY created_at {direction}, id {direction}"
    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)
    return _rows(conn, sql, tuple(params), _EVENT_FIELDS, _JSON_PAYLOAD)


def _sessions(
    conn: sqlite3.Connection,
    *,
    session_id: str | None = None,
    node_id: str | None = None,
    ticket_id: str | None = None,
    limit: int | None = None,
    with_last: bool = True,
) -> list[dict]:
    # COUNT(t.turn_index) 在没有轮次时是 0；COUNT(*) 会把左连接的空行算成 1。
    sql = (
        "SELECT s.id, s.ticket_id, s.node_id, s.pinned, s.created_at, s.updated_at,"
        " COUNT(t.turn_index) AS turn_count, MAX(t.created_at) AS last_turn_at"
        " FROM sessions AS s"
        " LEFT JOIN session_turns AS t ON t.session_id = s.id"
    )
    clauses: list[str] = []
    params: list[object] = []
    if session_id:
        clauses.append("s.id = ?")
        params.append(session_id)
    if node_id:
        clauses.append("s.node_id = ?")
        params.append(node_id)
    if ticket_id:
        clauses.append("s.ticket_id = ?")
        params.append(ticket_id)
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " GROUP BY s.id ORDER BY s.updated_at DESC, s.id"
    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)
    items = []
    for row in conn.execute(sql, tuple(params)):
        item = _pick(row, _SESSION_FIELDS)
        item["turn_count"] = int(row["turn_count"])
        if with_last:
            item["last_turn_at"] = row["last_turn_at"]
        items.append(item)
    return items


def _turn_text(root: Path, sha256: str) -> str | None:
    try:
        data = blob_path(root, sha256).read_bytes()
    except OSError:
        return None
    return data.decode("utf-8", errors="replace")


_TICKET_LIST_FIELDS = (
    "id",
    "code",
    "title",
    "status",
    "thread_id",
    "origin",
    "requester",
    "public_status",
    "updated_at",
)

_STREAM_INTERVAL = 2.0
_STREAM_PING = 15.0
_STREAM_TICKETS = 100
_STREAM_EVENTS = 20


def ticket_rows(
    conn: sqlite3.Connection, *, status: str | None = None, limit: int = 100
) -> list[dict]:
    sql = (
        "SELECT id, code, title, status, thread_id, origin, requester,"
        " public_status, updated_at FROM tickets"
    )
    params: list[object] = []
    if status:
        sql += " WHERE status = ?"
        params.append(status)
    sql += " ORDER BY updated_at DESC LIMIT ?"
    params.append(_nonneg(limit))
    return _rows(conn, sql, tuple(params), _TICKET_LIST_FIELDS)


def _topology_view(conn: sqlite3.Connection) -> dict:
    nodes = _rows(
        conn,
        "SELECT id, name, host, platforms, status, max_workers, last_seen"
        " FROM nodes ORDER BY id",
        (),
        _NODE_FIELDS,
    )
    tickets = _rows(
        conn,
        "SELECT id, code, thread_id, title, summary, status, dedup_key, created_at, updated_at"
        " FROM tickets ORDER BY updated_at DESC, id DESC LIMIT ?",
        (_TOPOLOGY_TICKETS,),
        _TICKET_FIELDS,
    )
    dispatches = _rows(
        conn,
        "SELECT id, ticket_id, node_id, worker_id, state, platform, attempt, detail,"
        " pr_url, pr_base, updated_at"
        " FROM dispatches ORDER BY updated_at DESC, id DESC LIMIT ?",
        (_TOPOLOGY_DISPATCHES,),
        _DISPATCH_FIELDS,
        _JSON_DETAIL,
    )
    return {
        "nodes": nodes,
        "tickets": tickets,
        "dispatches": dispatches,
        "sessions": _sessions(conn, limit=_TOPOLOGY_SESSIONS),
    }


def snapshot(conn: sqlite3.Connection) -> dict:
    """控制台首屏要的全部数据，一次读齐。"""
    return {
        "summary": build_summary(conn),
        "signals": watchdog.signals(conn),
        "tickets": ticket_rows(conn, limit=_STREAM_TICKETS),
        "topology": _topology_view(conn),
        "events": _events(conn, limit=_STREAM_EVENTS),
    }


def _digest_source(snap: dict) -> str:
    # age_s 每个 tick 都在涨，它由已有时间戳推得，不该触发推送。
    stable = dict(snap)
    stable["signals"] = [
        {key: value for key, value in item.items() if key != "age_s"}
        for item in snap.get("signals", [])
    ]
    return json.dumps(stable, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class StreamState:
    """把快照压成 SSE 帧：内容变了发 snapshot，否则按间隔发 ping。"""

    def __init__(
        self, interval: float = _STREAM_INTERVAL, ping_after: float = _STREAM_PING
    ) -> None:
        self.interval = interval
        self.ping_after = ping_after
        self.digest = ""
        self.last_sent = 0.0

    def frame(self, snap: dict, now: float) -> str:
        body = json.dumps(snap, ensure_ascii=False, separators=(",", ":"))
        digest = hashlib.sha256(_digest_source(snap).encode()).hexdigest()
        if digest != self.digest:
            self.digest = digest
            self.last_sent = now
            return f"event: snapshot\ndata: {body}\n\n"
        if now - self.last_sent >= self.ping_after:
            self.last_sent = now
            return f"event: ping\ndata: {int(now)}\n\n"
        return ""


async def stream_frames(
    conn: sqlite3.Connection,
    *,
    is_disconnected: Callable[[], Awaitable[bool]] | None = None,
    interval: float = _STREAM_INTERVAL,
) -> AsyncIterator[str]:
    state = StreamState(interval=interval)
    yield "retry: 3000\n\n"
    while True:
        if is_disconnected is not None and await is_disconnected():
            return
        snap = await asyncio.to_thread(snapshot, conn)
        frame = state.frame(snap, time.time())
        if frame:
            yield frame
        await asyncio.sleep(state.interval)


def create_ui_router(
    conn: sqlite3.Connection,
    blob_root: str | Path,
    read: Any,
    test_envs: dict[str, str] | None = None,
) -> APIRouter:
    """挂上控制台查询接口。调用方负责 include_router。

    ``read`` 是 create_app 里的权限依赖（``require(READ)``）：这些接口都只读，谁能进控制台
    谁就能调。test_envs：平台 → 测试环境链接，随工单详情下发，交付卡片据此出链接。
    """
    router = APIRouter(dependencies=[read if isinstance(read, DependsParam) else Depends(read)])
    root = Path(blob_root)
    envs = test_envs or {}

    @router.get("/internal/tickets/{ticket_id}")
    def get_ticket(ticket_id: str) -> dict:
        ticket = _first(
            conn,
            "SELECT id, code, thread_id, title, summary, status, dedup_key, created_at, updated_at"
            " FROM tickets WHERE id = ?",
            (ticket_id,),
            _TICKET_FIELDS,
        )
        if ticket is None:
            raise HTTPException(status_code=404, detail="ticket not found")
        thread = None
        if ticket["thread_id"]:
            thread = _first(
                conn,
                "SELECT id, origin, origin_id, created_at FROM threads WHERE id = ?",
                (ticket["thread_id"],),
                _THREAD_FIELDS,
            )
        assignments = _rows(
            conn,
            "SELECT ticket_id, worker_id, state, lease_until, result, updated_at"
            " FROM assignments WHERE ticket_id = ?"
            " ORDER BY updated_at DESC, worker_id",
            (ticket_id,),
            _ASSIGNMENT_FIELDS,
        )
        dispatches = _rows(
            conn,
            "SELECT id, ticket_id, node_id, worker_id, state, platform, attempt, detail,"
            " pr_url, pr_base, updated_at"
            " FROM dispatches WHERE ticket_id = ? ORDER BY updated_at DESC, id",
            (ticket_id,),
            _DISPATCH_FIELDS,
            _JSON_DETAIL,
        )
        return {
            "ticket": ticket,
            "thread": thread,
            "assignments": assignments,
            "dispatches": dispatches,
            "sessions": _sessions(conn, ticket_id=ticket_id, with_last=False),
            "test_envs": envs,
        }

    @router.get("/internal/events")
    def list_events(
        since: float | None = None,
        limit: int = 100,
        verdict: str = "",
        origin: str = "",
    ) -> dict:
        return {
            "events": _events(
                conn,
                since=since,
                verdict=verdict or None,
                origin=origin or None,
                limit=_cap(limit, _EVENT_CAP),
            )
        }

    @router.get("/internal/threads/{thread_id}")
    def get_thread(thread_id: str) -> dict:
        thread = _first(
            conn,
            "SELECT id, origin, origin_id, created_at FROM threads WHERE id = ?",
            (thread_id,),
            _THREAD_FIELDS,
        )
        if thread is None:
            raise HTTPException(status_code=404, detail="thread not found")
        tickets = _rows(
            conn,
            "SELECT id, code, thread_id, title, summary, status, dedup_key, created_at, updated_at"
            " FROM tickets WHERE thread_id = ? ORDER BY created_at ASC, id",
            (thread_id,),
            _TICKET_FIELDS,
        )
        return {
            "thread": thread,
            "events": _events(conn, thread_id=thread_id, newest_first=False),
            "tickets": tickets,
        }

    @router.get("/internal/sessions")
    def list_sessions(node_id: str = "", ticket_id: str = "", limit: int = 100) -> dict:
        return {
            "sessions": _sessions(
                conn,
                node_id=node_id or None,
                ticket_id=ticket_id or None,
                limit=_nonneg(limit),
            )
        }

    @router.get("/internal/sessions/{session_id}/turns")
    def list_turns(session_id: str, include_text: bool = False) -> dict:
        found = _sessions(conn, session_id=session_id)
        if not found:
            raise HTTPException(status_code=404, detail="session not found")
        turns = []
        rows = conn.execute(
            "SELECT session_id, turn_index, sha256, bytes, head_sha, branch, created_at"
            " FROM session_turns WHERE session_id = ? ORDER BY turn_index",
            (session_id,),
        )
        for row in rows:
            item = _pick(row, _TURN_FIELDS)
            if include_text:
                text = _turn_text(root, row["sha256"])
                if text is not None:
                    item["text"] = text
            turns.append(item)
        return {"session": found[0], "turns": turns}

    @router.get("/internal/topology")
    def topology() -> dict:
        return _topology_view(conn)

    @router.get("/internal/stream")
    async def stream(request: Request) -> StreamingResponse:
        """控制台实时快照：数据变了才推，没变就按时发心跳。"""
        return StreamingResponse(
            stream_frames(conn, is_disconnected=request.is_disconnected),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "X-Accel-Buffering": "no",
            },
        )

    return router
