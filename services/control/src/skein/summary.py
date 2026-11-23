"""控制面概览：给面板和排障用，只读。"""

from __future__ import annotations

import sqlite3


def _group(conn: sqlite3.Connection, sql: str) -> dict[str, int]:
    return {str(row[0]): int(row[1]) for row in conn.execute(sql)}


def summary(conn: sqlite3.Connection) -> dict:
    return {
        "events": {
            "total": conn.execute("SELECT COUNT(*) FROM events").fetchone()[0],
            "by_verdict": _group(conn, "SELECT verdict, COUNT(*) FROM events GROUP BY verdict"),
        },
        "threads": conn.execute("SELECT COUNT(*) FROM threads").fetchone()[0],
        "tickets": {
            "total": conn.execute("SELECT COUNT(*) FROM tickets").fetchone()[0],
            "by_status": _group(conn, "SELECT status, COUNT(*) FROM tickets GROUP BY status"),
        },
        "sessions": conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0],
        "runs": {
            "by_status": _group(conn, "SELECT status, COUNT(*) FROM runs GROUP BY status"),
        },
    }
