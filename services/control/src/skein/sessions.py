"""雁阵自己发的登录会话（本地账号、令牌模式）。

令牌是一个 256 位的随机串，只在下发那一刻以明文存在；表里存它的 SHA-256。
每次请求按散列查行：查得到、没过期才算登录。会话不依赖任何上游，所以上游登录系统
故障不会把已登录的人踢出去；过期、登出、改口令、停用账号都即时生效。
"""

from __future__ import annotations

import hashlib
import secrets
import sqlite3
import time
from collections.abc import Callable

SESSION_TTL_S = 7 * 86400
# last_seen_at 的更新频率：只为排障看「最近活跃」，不值得每个请求都写库
_SEEN_EVERY_S = 60.0
_PURGE_BATCH = 500


def _digest(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


class SessionStore:
    def __init__(
        self,
        conn: sqlite3.Connection,
        ttl_s: int = SESSION_TTL_S,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._conn = conn
        self.ttl_s = max(60, int(ttl_s))
        self._clock = clock

    def issue(self, user_sub: str, provider: str) -> str:
        """给 user_sub 签发一个新会话，返回只出现这一次的明文令牌。"""
        raw = "ss_" + secrets.token_urlsafe(32)
        now = self._clock()
        self._conn.execute(
            "INSERT INTO login_sessions"
            " (id, user_sub, provider, created_at, expires_at, last_seen_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (_digest(raw), user_sub, provider, now, now + self.ttl_s, now),
        )
        self.purge()
        return raw

    def resolve(self, raw: object) -> sqlite3.Row | None:
        """明文令牌 → 会话行；无效、过期、已吊销都返回 None。"""
        if not isinstance(raw, str) or not raw or len(raw) > 256:
            return None
        row = self._conn.execute(
            "SELECT id, user_sub, provider, created_at, expires_at, last_seen_at"
            " FROM login_sessions WHERE id = ?",
            (_digest(raw),),
        ).fetchone()
        if row is None:
            return None
        now = self._clock()
        if now >= row["expires_at"]:
            self._conn.execute("DELETE FROM login_sessions WHERE id = ?", (row["id"],))
            return None
        if now - row["last_seen_at"] >= _SEEN_EVERY_S:
            self._conn.execute(
                "UPDATE login_sessions SET last_seen_at = ? WHERE id = ?", (now, row["id"])
            )
        return row

    def revoke(self, raw: object) -> bool:
        """登出：删掉这一行。返回是否真的删了会话。"""
        if not isinstance(raw, str) or not raw:
            return False
        cursor = self._conn.execute(
            "DELETE FROM login_sessions WHERE id = ?", (_digest(raw),)
        )
        return cursor.rowcount > 0

    def revoke_user(self, user_sub: str) -> int:
        """改口令、停用账号、换角色之后调用：把这个人的会话全部作废。"""
        cursor = self._conn.execute(
            "DELETE FROM login_sessions WHERE user_sub = ?", (user_sub,)
        )
        return cursor.rowcount

    def purge(self) -> int:
        """清掉过期会话（分批，避免登录时做一次大删除）。"""
        total = 0
        while True:
            cursor = self._conn.execute(
                "DELETE FROM login_sessions WHERE expires_at <= ?"
                " AND id IN (SELECT id FROM login_sessions WHERE expires_at <= ? LIMIT ?)",
                (self._clock(), self._clock(), _PURGE_BATCH),
            )
            total += cursor.rowcount
            if cursor.rowcount < _PURGE_BATCH:
                return total
