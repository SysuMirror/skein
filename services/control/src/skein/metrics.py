"""Prometheus 文本格式的指标：只有计数，不暴露任何正文。

给运维接监控用（``GET /metrics``，不需要认证）。要什么内容都只在这里加计数，
不要把工单标题之类的正文带进去。
"""

from __future__ import annotations

import sqlite3
import time

_START = time.monotonic()


def _escape(value: object) -> str:
    return str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _series(lines: list[str], name: str, description: str, rows: list[tuple[object, object]], label: str) -> None:
    lines.append(f"# HELP {name} {description}")
    lines.append(f"# TYPE {name} gauge")
    for key, value in rows:
        lines.append(f'{name}{{{label}="{_escape(key)}"}} {int(value)}')


def _total(lines: list[str], name: str, description: str, value: int) -> None:
    lines.append(f"# HELP {name} {description}")
    lines.append(f"# TYPE {name} gauge")
    lines.append(f"{name} {int(value)}")


def render(conn: sqlite3.Connection) -> str:
    lines: list[str] = []
    _series(
        lines,
        "skein_tickets",
        "Tickets by status.",
        list(conn.execute("SELECT status, COUNT(*) FROM tickets GROUP BY status")),
        "status",
    )
    _series(
        lines,
        "skein_nodes",
        "Nodes by status.",
        list(conn.execute("SELECT status, COUNT(*) FROM nodes GROUP BY status")),
        "status",
    )
    _series(
        lines,
        "skein_dispatches",
        "Dispatches by state.",
        list(conn.execute("SELECT state, COUNT(*) FROM dispatches GROUP BY state")),
        "state",
    )
    for name, description, query in (
        ("skein_events_total", "Events recorded.", "SELECT COUNT(*) FROM events"),
        ("skein_threads_total", "Threads recorded.", "SELECT COUNT(*) FROM threads"),
        ("skein_sessions_total", "Worker sessions recorded.", "SELECT COUNT(*) FROM sessions"),
        ("skein_llm_calls_total", "Model calls recorded.", "SELECT COUNT(*) FROM llm_calls"),
        ("skein_audit_total", "Audit records written.", "SELECT COUNT(*) FROM audit"),
        ("skein_assistants", "Assistant credentials in use.", "SELECT COUNT(*) FROM assistants WHERE revoked_at IS NULL"),
    ):
        _total(lines, name, description, int(conn.execute(query).fetchone()[0]))
    lines.append("# HELP skein_uptime_seconds Seconds since the control plane started.")
    lines.append("# TYPE skein_uptime_seconds counter")
    lines.append(f"skein_uptime_seconds {int(time.monotonic() - _START)}")
    return "\n".join(lines) + "\n"
