"""运行记录：定时作业按 (job, 计划分钟) 唯一，重复触发不重复执行。"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass

from skein import db


@dataclass(frozen=True)
class Run:
    id: str
    job: str
    day: str
    status: str
    detail: str | None


def start(conn: sqlite3.Connection, job: str, day: str) -> str | None:
    """同一天同一作业只允许一条。已存在返回 None。"""
    run_id = db.new_id()
    try:
        conn.execute(
            "INSERT INTO runs (id, job, day, status, started_at) VALUES (?, ?, ?, 'running', ?)",
            (run_id, job, day, time.time()),
        )
    except sqlite3.IntegrityError:
        return None
    return run_id


def finish(conn: sqlite3.Connection, run_id: str, status: str, detail: str | None = None) -> None:
    conn.execute(
        "UPDATE runs SET status = ?, finished_at = ?, detail = ? WHERE id = ?",
        (status, time.time(), detail, run_id),
    )


def get(conn: sqlite3.Connection, run_id: str) -> Run | None:
    row = conn.execute(
        "SELECT id, job, day, status, detail FROM runs WHERE id = ?", (run_id,)
    ).fetchone()
    return Run(row["id"], row["job"], row["day"], row["status"], row["detail"]) if row else None


def by_day(conn: sqlite3.Connection, job: str, day: str) -> Run | None:
    row = conn.execute(
        "SELECT id, job, day, status, detail FROM runs WHERE job = ? AND day = ?", (job, day)
    ).fetchone()
    return Run(row["id"], row["job"], row["day"], row["status"], row["detail"]) if row else None


def recent(conn: sqlite3.Connection, limit: int = 20) -> list[Run]:
    rows = conn.execute(
        "SELECT id, job, day, status, detail FROM runs ORDER BY started_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [Run(r["id"], r["job"], r["day"], r["status"], r["detail"]) for r in rows]


def stale(conn: sqlite3.Connection, older_than_s: float, now: float | None = None) -> list[Run]:
    cutoff = (now if now is not None else time.time()) - older_than_s
    rows = conn.execute(
        "SELECT id, job, day, status, detail FROM runs WHERE status = 'running' AND started_at < ?",
        (cutoff,),
    ).fetchall()
    return [Run(r["id"], r["job"], r["day"], r["status"], r["detail"]) for r in rows]
