"""Session 权威：索引行在领域库，正文是内容寻址文件。

transcript 按轮追加。同一 (session_id, turn_index) 重复上传且哈希相同是幂等；
哈希不同说明会话被另一台节点写过，必须拒绝，不能悄悄覆盖。
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path


class TurnConflict(RuntimeError):
    pass


@dataclass(frozen=True)
class Turn:
    session_id: str
    turn_index: int
    sha256: str
    bytes: int
    head_sha: str | None
    branch: str | None


def blob_path(root: Path, sha256: str) -> Path:
    return Path(root) / sha256[:2] / sha256[2:]


def put_blob(root: Path, data: bytes) -> tuple[str, int]:
    sha = hashlib.sha256(data).hexdigest()
    path = blob_path(root, sha)
    if path.exists():
        return sha, path.stat().st_size
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)
    return sha, len(data)


def get_blob(root: Path, sha256: str) -> bytes:
    return blob_path(root, sha256).read_bytes()


class SessionStore:
    def __init__(self, conn: sqlite3.Connection, root: str | Path) -> None:
        self._conn = conn
        self._root = Path(root)

    def open(self, session_id: str, ticket_id: str | None = None, node_id: str | None = None) -> None:
        t = time.time()
        self._conn.execute(
            "INSERT OR IGNORE INTO sessions (id, ticket_id, node_id, pinned, created_at, updated_at)"
            " VALUES (?, ?, ?, 1, ?, ?)",
            (session_id, ticket_id, node_id, t, t),
        )

    def append_turn(
        self,
        session_id: str,
        turn_index: int,
        data: bytes,
        head_sha: str | None = None,
        branch: str | None = None,
    ) -> Turn:
        sha, size = put_blob(self._root, data)
        row = self._conn.execute(
            "SELECT sha256 FROM session_turns WHERE session_id = ? AND turn_index = ?",
            (session_id, turn_index),
        ).fetchone()
        if row is not None:
            if row["sha256"] == sha:
                return self._turn(session_id, turn_index)
            raise TurnConflict(
                f"turn {turn_index} of session {session_id} already has {row['sha256'][:12]}"
            )
        self._conn.execute(
            "INSERT INTO session_turns"
            " (session_id, turn_index, sha256, bytes, head_sha, branch, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (session_id, turn_index, sha, size, head_sha, branch, time.time()),
        )
        self._conn.execute(
            "UPDATE sessions SET updated_at = ? WHERE id = ?", (time.time(), session_id)
        )
        return Turn(session_id, turn_index, sha, size, head_sha, branch)

    def turns(self, session_id: str) -> list[Turn]:
        rows = self._conn.execute(
            "SELECT session_id, turn_index, sha256, bytes, head_sha, branch"
            " FROM session_turns WHERE session_id = ? ORDER BY turn_index",
            (session_id,),
        ).fetchall()
        return [
            Turn(r["session_id"], r["turn_index"], r["sha256"], r["bytes"], r["head_sha"], r["branch"])
            for r in rows
        ]

    def pin(self, session_id: str, node_id: str) -> None:
        self._conn.execute(
            "UPDATE sessions SET node_id = ?, pinned = 1, updated_at = ? WHERE id = ?",
            (node_id, time.time(), session_id),
        )

    def unpin(self, session_id: str) -> None:
        self._conn.execute(
            "UPDATE sessions SET pinned = 0, updated_at = ? WHERE id = ?",
            (time.time(), session_id),
        )

    def is_pinned(self, session_id: str) -> bool:
        row = self._conn.execute(
            "SELECT pinned FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
        return bool(row and row["pinned"])

    def _turn(self, session_id: str, turn_index: int) -> Turn:
        row = self._conn.execute(
            "SELECT session_id, turn_index, sha256, bytes, head_sha, branch"
            " FROM session_turns WHERE session_id = ? AND turn_index = ?",
            (session_id, turn_index),
        ).fetchone()
        return Turn(
            row["session_id"], row["turn_index"], row["sha256"], row["bytes"],
            row["head_sha"], row["branch"],
        )
