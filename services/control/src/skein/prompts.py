"""提示词修订：稳定指针与金丝雀指针分离，Watchdog 只能动三个键。

安全约束（对应 docs/architecture.md 裁决二）：
- 可改范围写死在代码里；
- 乐观锁：base_revision 必须等于当前稳定修订；
- 单次改动字符数上限；
- 7 天内回退两次即冻结，只有人工来源能解冻。
"""

from __future__ import annotations

import difflib
import sqlite3
import time

from skein import db

EDITABLE_PARTS = ("runtime/worker", "runtime/work", "runtime/handoff/worker")
MAX_CHANGE_CHARS = 2000
FREEZE_WINDOW_S = 7 * 86400
ROLLBACK_LIMIT = 2


class PromptError(RuntimeError):
    pass


class PromptConflict(PromptError):
    pass


class PromptFrozen(PromptError):
    pass


def _pointer(conn: sqlite3.Connection, part: str) -> sqlite3.Row:
    row = conn.execute(
        "SELECT part, stable_revision, canary_revision, frozen_until FROM prompt_pointers WHERE part = ?",
        (part,),
    ).fetchone()
    if row is None:
        conn.execute(
            "INSERT OR IGNORE INTO prompt_pointers (part, stable_revision) VALUES (?, 0)", (part,)
        )
        row = conn.execute(
            "SELECT part, stable_revision, canary_revision, frozen_until FROM prompt_pointers WHERE part = ?",
            (part,),
        ).fetchone()
    return row


def _text(conn: sqlite3.Connection, revision: int) -> str:
    if revision == 0:
        return ""
    row = conn.execute("SELECT text FROM prompt_revisions WHERE id = ?", (revision,)).fetchone()
    return row["text"] if row else ""


def current(conn: sqlite3.Connection, part: str, channel: str = "stable") -> str:
    pointer = _pointer(conn, part)
    if channel == "canary" and pointer["canary_revision"]:
        return _text(conn, pointer["canary_revision"])
    return _text(conn, pointer["stable_revision"])


def _changed_chars(old: str, new: str) -> int:
    matcher = difflib.SequenceMatcher(None, old, new)
    return len(old) + len(new) - 2 * sum(block.size for block in matcher.get_matching_blocks())


def _rollbacks_last_week(conn: sqlite3.Connection, part: str) -> int:
    cutoff = time.time() - FREEZE_WINDOW_S
    row = conn.execute(
        "SELECT COUNT(*) FROM audit WHERE actor = 'watchdog' AND action = 'prompt_rollback'"
        " AND detail LIKE ? AND created_at >= ?",
        (f'%"{part}"%', cutoff),
    ).fetchone()
    return int(row[0])


def propose(
    conn: sqlite3.Connection,
    part: str,
    text: str,
    base_revision: int,
    source: str,
    signal_id: str | None = None,
) -> int:
    if part not in EDITABLE_PARTS:
        raise PromptError(f"part {part} is not editable")
    pointer = _pointer(conn, part)
    if pointer["frozen_until"] and pointer["frozen_until"] > time.time():
        raise PromptFrozen(f"{part} frozen until {pointer['frozen_until']}")
    if base_revision != pointer["stable_revision"]:
        raise PromptConflict(
            f"base {base_revision} != stable {pointer['stable_revision']}"
        )
    if _changed_chars(_text(conn, pointer["stable_revision"]), text) > MAX_CHANGE_CHARS:
        raise PromptError("change is too large")

    cursor = conn.execute(
        "INSERT INTO prompt_revisions (part, base_revision, text, source, signal_id, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (part, base_revision, text, source, signal_id, time.time()),
    )
    revision = int(cursor.lastrowid)
    conn.execute(
        "UPDATE prompt_pointers SET canary_revision = ? WHERE part = ?", (revision, part)
    )
    db.record_audit(
        conn, source, "prompt_propose", {"part": part, "revision": revision, "signal_id": signal_id}
    )
    return revision


def promote(conn: sqlite3.Connection, part: str) -> int:
    pointer = _pointer(conn, part)
    canary = pointer["canary_revision"]
    if not canary:
        raise PromptError("no canary revision")
    cursor = conn.execute(
        "INSERT INTO prompt_revisions (part, base_revision, text, source, signal_id, created_at)"
        " VALUES (?, ?, ?, 'watchdog-promote', NULL, ?)",
        (part, pointer["stable_revision"], _text(conn, canary), time.time()),
    )
    revision = int(cursor.lastrowid)
    conn.execute(
        "UPDATE prompt_pointers SET stable_revision = ?, canary_revision = NULL WHERE part = ?",
        (revision, part),
    )
    db.record_audit(conn, "watchdog", "prompt_promote", {"part": part, "revision": revision})
    return revision


def rollback(conn: sqlite3.Connection, part: str) -> None:
    pointer = _pointer(conn, part)
    conn.execute("UPDATE prompt_pointers SET canary_revision = NULL WHERE part = ?", (part,))
    db.record_audit(conn, "watchdog", "prompt_rollback", {"part": part})
    if _rollbacks_last_week(conn, part) >= ROLLBACK_LIMIT:
        until = time.time() + FREEZE_WINDOW_S
        conn.execute("UPDATE prompt_pointers SET frozen_until = ? WHERE part = ?", (until, part))
        db.record_audit(conn, "watchdog", "prompt_freeze", {"part": part, "until": until})
    _ = pointer


def unfreeze(conn: sqlite3.Connection, part: str) -> None:
    conn.execute("UPDATE prompt_pointers SET frozen_until = NULL WHERE part = ?", (part,))
    db.record_audit(conn, "human", "prompt_unfreeze", {"part": part})


def state(conn: sqlite3.Connection, part: str) -> dict:
    pointer = _pointer(conn, part)
    return {
        "part": part,
        "stable_revision": pointer["stable_revision"],
        "canary_revision": pointer["canary_revision"],
        "frozen_until": pointer["frozen_until"],
        "editable": part in EDITABLE_PARTS,
    }
