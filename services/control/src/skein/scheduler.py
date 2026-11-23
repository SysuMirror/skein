"""节点注册与派单选择。

节点通过反向拨入注册；派单只看「在线、有心跳、平台匹配、未满员」。
"""

from __future__ import annotations

import json
import sqlite3
import time

from skein import db

ONLINE_WINDOW_S = 60.0
ACTIVE_STATES = ("pending", "created", "dispatched")


def upsert_node(
    conn: sqlite3.Connection,
    node_id: str,
    name: str = "",
    host: str = "",
    platforms: list[str] | None = None,
    peer_url: str = "",
    max_workers: int = 3,
) -> None:
    conn.execute(
        "INSERT INTO nodes (id, name, host, platforms, peer_url, status, max_workers, last_seen)"
        " VALUES (?, ?, ?, ?, ?, 'online', ?, ?)"
        " ON CONFLICT(id) DO UPDATE SET name=excluded.name, host=excluded.host,"
        " platforms=excluded.platforms, peer_url=excluded.peer_url, status='online',"
        " max_workers=excluded.max_workers, last_seen=excluded.last_seen",
        (node_id, name, host, json.dumps(platforms or []), peer_url, max_workers, time.time()),
    )


def touch(conn: sqlite3.Connection, node_id: str) -> None:
    conn.execute(
        "UPDATE nodes SET last_seen = ?, status = 'online' WHERE id = ?", (time.time(), node_id)
    )


def set_status(conn: sqlite3.Connection, node_id: str, status: str) -> None:
    conn.execute("UPDATE nodes SET status = ? WHERE id = ?", (status, node_id))


def online(conn: sqlite3.Connection, now: float | None = None) -> list[sqlite3.Row]:
    cutoff = (now if now is not None else time.time()) - ONLINE_WINDOW_S
    return list(
        conn.execute(
            "SELECT id, name, host, platforms, peer_url, max_workers, last_seen"
            " FROM nodes WHERE status = 'online' AND last_seen >= ?",
            (cutoff,),
        )
    )


def active_count(conn: sqlite3.Connection, node_id: str) -> int:
    marks = ",".join("?" for _ in ACTIVE_STATES)
    row = conn.execute(
        f"SELECT COUNT(*) FROM dispatches WHERE node_id = ? AND state IN ({marks})",
        (node_id, *ACTIVE_STATES),
    ).fetchone()
    return int(row[0])


def _accepts(platforms_json: str, platform: str | None) -> bool:
    if platform is None:
        return True
    try:
        platforms = json.loads(platforms_json)
    except json.JSONDecodeError:
        return False
    return not platforms or "*" in platforms or platform in platforms


def online_platforms(conn: sqlite3.Connection, now: float | None = None) -> list[str]:
    """在线节点注册的平台并集（派单可选项，排序去重）。"""
    found: set[str] = set()
    for row in online(conn, now):
        try:
            values = json.loads(row["platforms"] or "[]")
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(values, list):
            found.update(str(item) for item in values if item)
    return sorted(found)


def pick_node(
    conn: sqlite3.Connection, platform: str | None = None, now: float | None = None
) -> str | None:
    candidates = [
        row
        for row in online(conn, now)
        if _accepts(row["platforms"], platform)
        and active_count(conn, row["id"]) < row["max_workers"]
    ]
    if not candidates:
        return None
    candidates.sort(key=lambda row: (active_count(conn, row["id"]), row["id"]))
    return candidates[0]["id"]


def open_dispatch(
    conn: sqlite3.Connection, ticket_id: str, node_id: str, platform: str = ""
) -> str:
    dispatch_id = db.new_id()
    conn.execute(
        "INSERT INTO dispatches (id, ticket_id, node_id, state, platform, updated_at)"
        " VALUES (?, ?, ?, 'pending', ?, ?)",
        (dispatch_id, ticket_id, node_id, platform, time.time()),
    )
    return dispatch_id


# 派工的终态：到了这里就不再被「进行中」的状态更新改回去
FINAL_STATES = ("done", "failed")


def update_dispatch(
    conn: sqlite3.Connection,
    dispatch_id: str,
    state: str | None = None,
    worker_id: str | None = None,
    detail: str | None = None,
    keep_final: bool = False,
) -> None:
    """更新派工记录。

    ``keep_final=True`` 用于 ack / progress 这类「进行中」的回执：消息可能因为网络
    延迟在派工已经结束（完成 / 取消 / 失败）之后才到，这时不能把终态改回进行中——
    否则一张已取消的工单在界面上永远显示「在跑」，而且会被当成仍有在途派工而无法再派。
    """
    sets = ["updated_at = ?"]
    params: list[object] = [time.time()]
    if state is not None:
        sets.append("state = ?")
        params.append(state)
    if worker_id is not None:
        sets.append("worker_id = ?")
        params.append(worker_id)
    if detail is not None:
        sets.append("detail = ?")
        params.append(detail)
    sql = f"UPDATE dispatches SET {', '.join(sets)} WHERE id = ?"
    if keep_final:
        sql += f" AND state NOT IN ({','.join('?' for _ in FINAL_STATES)})"
        params.extend(FINAL_STATES)
    params.append(dispatch_id)
    conn.execute(sql, tuple(params))


def dispatch(conn: sqlite3.Connection, ticket_id: str, platform: str | None = None) -> str | None:
    """给工单挑一个节点并开派单；没有可用节点时返回 None，工单保持原状。"""
    node_id = pick_node(conn, platform)
    if node_id is None:
        return None
    dispatch_id = open_dispatch(conn, ticket_id, node_id)
    conn.execute(
        "UPDATE tickets SET status = 'assigned', updated_at = ? WHERE id = ? AND status = 'staged'",
        (time.time(), ticket_id),
    )
    return dispatch_id
