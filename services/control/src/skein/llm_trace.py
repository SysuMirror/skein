"""LLM 调用留痕：控制面自己与外部服务回报的每一次模型调用。"""

from __future__ import annotations

import json
import sqlite3
import time
from typing import Any

from skein import db

MAX_TEXT = 16000


def _clip(value: Any) -> str:
    if value is None:
        return ""
    text = (
        value
        if isinstance(value, str)
        else json.dumps(value, ensure_ascii=False, default=str)
    )
    return text[:MAX_TEXT]


def _int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def record(
    conn: sqlite3.Connection,
    *,
    session_id: str = "",
    actor: str = "",
    source: str = "",
    model: str = "",
    prompt: Any = "",
    response: Any = "",
    prompt_tokens: Any = 0,
    completion_tokens: Any = 0,
    latency_ms: Any = 0,
    status: str = "ok",
    error: Any = "",
) -> str:
    call_id = db.new_id()
    conn.execute(
        "INSERT INTO llm_calls"
        " (id, session_id, actor, source, model, prompt, response,"
        "  prompt_tokens, completion_tokens, latency_ms, status, error, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            call_id,
            str(session_id or "")[:128],
            str(actor or "")[:128],
            str(source or "")[:32],
            str(model or "")[:128],
            _clip(prompt),
            _clip(response),
            _int(prompt_tokens),
            _int(completion_tokens),
            _int(latency_ms),
            str(status or "ok")[:16],
            _clip(error),
            time.time(),
        ),
    )
    return call_id


def calls(
    conn: sqlite3.Connection,
    *,
    session_id: str | None = None,
    source: str | None = None,
    limit: int = 200,
) -> list[dict]:
    clauses: list[str] = []
    params: list[Any] = []
    if session_id:
        clauses.append("session_id = ?")
        params.append(session_id)
    if source:
        clauses.append("source = ?")
        params.append(source)
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    rows = conn.execute(
        "SELECT id, session_id, actor, source, model, prompt, response,"
        " prompt_tokens, completion_tokens, latency_ms, status, error, created_at"
        f" FROM llm_calls{where} ORDER BY created_at DESC LIMIT ?",
        (*params, max(1, min(int(limit), 500))),
    ).fetchall()
    return [dict(row) for row in rows]


def sessions(conn: sqlite3.Connection, *, limit: int = 100) -> list[dict]:
    rows = conn.execute(
        "SELECT session_id, actor, source, COUNT(*) AS calls,"
        " SUM(prompt_tokens + completion_tokens) AS tokens,"
        " MIN(created_at) AS first_at, MAX(created_at) AS last_at,"
        " (SELECT model FROM llm_calls c2 WHERE c2.session_id = c.session_id"
        "  ORDER BY created_at DESC LIMIT 1) AS model,"
        " (SELECT status FROM llm_calls c3 WHERE c3.session_id = c.session_id"
        "  ORDER BY created_at DESC LIMIT 1) AS last_status"
        " FROM llm_calls c WHERE session_id != ''"
        " GROUP BY session_id, actor, source ORDER BY last_at DESC LIMIT ?",
        (max(1, min(int(limit), 500)),),
    ).fetchall()
    return [dict(row) for row in rows]
