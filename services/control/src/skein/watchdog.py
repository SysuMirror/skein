"""Watchdog 的信号：只统计领域库里已有真值的事实，不做模型判断。

每条信号都可以直接指向一行数据，供人复核或作为提示词修订的 signal_id。
"""

from __future__ import annotations

import sqlite3
import time


def signals(
    conn: sqlite3.Connection,
    dispatch_timeout_s: float = 3600,
    run_lookback_s: float = 86400,
    now: float | None = None,
) -> list[dict]:
    now = now if now is not None else time.time()
    found: list[dict] = []

    cutoff = now - dispatch_timeout_s
    for row in conn.execute(
        "SELECT id, ticket_id, node_id, updated_at FROM dispatches"
        " WHERE state IN ('dispatched', 'created', 'pending') AND updated_at < ?",
        (cutoff,),
    ):
        found.append(
            {
                "kind": "dispatch_timeout",
                "id": row["id"],
                "ticket_id": row["ticket_id"],
                "node_id": row["node_id"],
                "age_s": int(now - row["updated_at"]),
            }
        )

    since = now - run_lookback_s
    for row in conn.execute(
        "SELECT id, job, day, detail FROM runs WHERE status = 'failed' AND started_at >= ?", (since,)
    ):
        found.append(
            {"kind": "run_failed", "id": row["id"], "job": row["job"], "day": row["day"],
             "detail": row["detail"]}
        )

    for row in conn.execute(
        "SELECT d.id, d.ticket_id, d.node_id FROM dispatches d"
        " JOIN nodes n ON n.id = d.node_id"
        " WHERE d.state IN ('dispatched', 'created', 'pending')"
        "   AND (n.status = 'offline' OR n.last_seen < ?)",
        (now - 60,),
    ):
        found.append(
            {"kind": "node_offline_with_work", "id": row["id"], "ticket_id": row["ticket_id"],
             "node_id": row["node_id"]}
        )

    return found
