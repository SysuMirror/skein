"""助手凭证：外部助手（论坛机器人、聊天助手、巡检脚本）接入雁阵用的身份。

与主令牌完全是两回事：

- 一个助手一把凭证，互不影响；泄露了只吊销这一把。
- 凭证只能走公共 API（``/api/v1``）：建单、幂等重试、查自己提交的工单。派工、验收、
  取消、看别人的工单，一概不行——不是靠调用方自觉，是权限矩阵里没有。
- 表里只存密钥的 SHA-256。密钥是 256 位的随机串，没有可穷举的低熵空间，所以不需要
  再加盐或用慢散列；比较用恒定时间函数。

密钥只在签发 / 轮换那一刻出现一次，``skein_at_`` 开头，便于识别、也便于被密钥扫描器
认出。状态回调的签名密钥另算一把（``whsec_`` 开头）：发回调时要拿来签名，所以原样存；
它的作用仅限于「冒充雁阵向这一个回调地址发通知」，不与 API 密钥同等对待。
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import re
import secrets
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urlsplit

from skein import db
from skein.notify import Target

log = logging.getLogger("skein.assistants")

KEY_PREFIX = "skein_at_"
SECRET_PREFIX = "whsec_"
ID_PREFIX = "ast_"
KEY_RANDOM_CHARS = 43  # token_urlsafe(32)
# 助手名：小写字母数字与连字符。审计留痕、命令行、教程里都用它
NAME_PATTERN = re.compile(r"[a-z0-9][a-z0-9-]{1,63}")
MAX_CALLBACK_URL = 2048
# last_used_at 的刷新间隔：只为排障看「最近一次使用」
_TOUCH_EVERY_S = 60.0

_FIELDS = (
    "id, name, purpose, key_hash, callback_url, callback_secret, source, created_by,"
    " created_at, last_used_at, revoked_at, revoked_by, rotated_at"
)


def new_key() -> str:
    return KEY_PREFIX + secrets.token_urlsafe(32)


def generate_secret() -> str:
    """回调的签名密钥：发回调时要用，所以它会原样存进库。"""
    return SECRET_PREFIX + secrets.token_urlsafe(32)


def valid_key_shape(presented: object) -> bool:
    return (
        isinstance(presented, str)
        and presented.startswith(KEY_PREFIX)
        and len(presented) <= 128
    )


def _digest(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def validate_name(name: object) -> str:
    value = str(name or "").strip().lower()
    if not NAME_PATTERN.fullmatch(value):
        raise ValueError(
            "name must be 2-64 characters: lowercase letters, digits and hyphens"
            " (a letter or digit first)"
        )
    return value


def validate_callback_url(url: str) -> str:
    value = (url or "").strip()
    if not value:
        return ""
    if len(value) > MAX_CALLBACK_URL:
        raise ValueError(f"callback_url is too long (max {MAX_CALLBACK_URL})")
    parts = urlsplit(value)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError("callback_url must be an http(s) URL")
    return value


@dataclass(frozen=True)
class Assistant:
    id: str
    name: str
    purpose: str
    callback_url: str
    has_callback_secret: bool
    source: str
    created_by: str
    created_at: float
    last_used_at: float | None
    revoked_at: float | None
    rotated_at: float | None

    @property
    def label(self) -> str:
        """审计里的称谓。"""
        return f"assistant:{self.name}"

    def as_dict(self) -> dict:
        """对外形态：不含任何密钥。"""
        return {
            "id": self.id,
            "name": self.name,
            "purpose": self.purpose,
            "callback_url": self.callback_url,
            "callback_secret_set": self.has_callback_secret,
            "source": self.source,
            "created_by": self.created_by,
            "created_at": self.created_at,
            "last_used_at": self.last_used_at,
            "revoked_at": self.revoked_at,
            "rotated_at": self.rotated_at,
        }


@dataclass(frozen=True)
class Issued:
    """签发 / 轮换 / 设回调的结果：凭证本身，加上只出现这一次的密钥。"""

    assistant: Assistant
    key: str = ""
    callback_secret: str = ""


def _assistant(row: sqlite3.Row) -> Assistant:
    return Assistant(
        id=row["id"],
        name=row["name"],
        purpose=row["purpose"],
        callback_url=row["callback_url"],
        has_callback_secret=bool(row["callback_secret"]),
        source=row["source"],
        created_by=row["created_by"],
        created_at=row["created_at"],
        last_used_at=row["last_used_at"],
        revoked_at=row["revoked_at"],
        rotated_at=row["rotated_at"],
    )


class AssistantStore:
    def __init__(self, conn: sqlite3.Connection, clock: Callable[[], float] = time.time) -> None:
        self._conn = conn
        self._clock = clock

    # ---------------- 认证 ----------------

    def authenticate(self, presented: object) -> Assistant | None:
        """请求头里的密钥 → 助手。不存在、已吊销、格式不对都返回 None。"""
        if not valid_key_shape(presented):
            return None
        digest = _digest(presented)
        row = self._conn.execute(
            f"SELECT {_FIELDS} FROM assistants WHERE key_hash = ? AND revoked_at IS NULL",
            (digest,),
        ).fetchone()
        # 按散列查到就已相等；恒定时间再比一次是惯例，不为攻击者省哪怕一次比较
        if row is None or not hmac.compare_digest(row["key_hash"], digest):
            return None
        now = self._clock()
        if row["last_used_at"] is None or now - row["last_used_at"] >= _TOUCH_EVERY_S:
            self._conn.execute(
                "UPDATE assistants SET last_used_at = ? WHERE id = ?", (now, row["id"])
            )
        return _assistant(row)

    # ---------------- 查 ----------------

    def get(self, ref: str) -> Assistant | None:
        """按 id 或名字找（名字不区分大小写）。"""
        row = self._conn.execute(
            f"SELECT {_FIELDS} FROM assistants WHERE id = ? OR name = ? LIMIT 1",
            (str(ref or "").strip(), str(ref or "").strip().lower()),
        ).fetchone()
        return _assistant(row) if row else None

    def list(self) -> list[Assistant]:
        rows = self._conn.execute(
            f"SELECT {_FIELDS} FROM assistants ORDER BY created_at, id"
        ).fetchall()
        return [_assistant(row) for row in rows]

    def callback_target(self, assistant_id: str) -> Target | None:
        """这张工单是哪个助手提交的、回调发往哪里。没配回调或凭证已吊销返回 None。"""
        row = self._conn.execute(
            "SELECT callback_url, callback_secret FROM assistants"
            " WHERE id = ? AND revoked_at IS NULL AND callback_url <> ''",
            (assistant_id,),
        ).fetchone()
        return Target(url=row["callback_url"], secret=row["callback_secret"]) if row else None

    # ---------------- 签发 / 轮换 / 吊销 ----------------

    def issue(
        self,
        name: str,
        purpose: str = "",
        callback_url: str = "",
        *,
        actor: str = "skein",
        source: str = "api",
        key: str = "",
    ) -> Issued:
        """签发一把新凭证。``key`` 留空则自己生成；环境文件里声明的凭证由调用方传入。"""
        name = validate_name(name)
        callback_url = validate_callback_url(callback_url)
        if self.get(name) is not None:
            raise ValueError(f"an assistant named {name!r} already exists")
        if source == "env" and not key:
            raise ValueError("a credential declared in SKEIN_ASSISTANTS must carry its key")
        key = key or new_key()
        if not valid_key_shape(key) or len(key) < len(KEY_PREFIX) + KEY_RANDOM_CHARS:
            raise ValueError(
                f"keys must start with {KEY_PREFIX} and carry at least 32 random characters"
            )
        secret = generate_secret() if callback_url else ""
        assistant_id = ID_PREFIX + secrets.token_hex(4)
        self._conn.execute(
            "INSERT INTO assistants"
            " (id, name, purpose, key_hash, callback_url, callback_secret, source, created_by,"
            "  created_at, last_used_at, revoked_at, rotated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL)",
            (
                assistant_id,
                name,
                str(purpose or "").strip()[:500],
                _digest(key),
                callback_url,
                secret,
                source,
                actor,
                self._clock(),
            ),
        )
        db.record_audit(
            self._conn, actor, "assistant.issue", {"id": assistant_id, "name": name, "source": source}
        )
        assistant = self.get(assistant_id)
        assert assistant is not None
        return Issued(assistant, key, secret)

    def rotate(self, ref: str, *, actor: str = "skein") -> Issued:
        """换一把密钥：旧的立刻失效，名字、用途、回调都不变。"""
        assistant = self._need_active(ref)
        key = new_key()
        self._conn.execute(
            "UPDATE assistants SET key_hash = ?, rotated_at = ? WHERE id = ?",
            (_digest(key), self._clock(), assistant.id),
        )
        db.record_audit(
            self._conn, actor, "assistant.rotate", {"id": assistant.id, "name": assistant.name}
        )
        return Issued(self._refresh(assistant), key)

    def revoke(self, ref: str, *, actor: str = "skein") -> Assistant:
        """吊销。行保留（可追溯）；密钥与回调从此都不再有效。"""
        assistant = self._need_active(ref)
        self._revoke(assistant, actor)
        return self._refresh(assistant)

    # ---------------- 用途 ----------------

    def set_purpose(self, ref: str, purpose: str, *, actor: str = "skein") -> Assistant:
        """改用途说明（给人看的，不影响权限）。"""
        assistant = self._need_active(ref)
        self._conn.execute(
            "UPDATE assistants SET purpose = ? WHERE id = ?",
            (str(purpose or "").strip()[:500], assistant.id),
        )
        db.record_audit(
            self._conn, actor, "assistant.purpose", {"id": assistant.id, "name": assistant.name}
        )
        return self._refresh(assistant)

    # ---------------- 回调 ----------------

    def set_callback(
        self, ref: str, url: str, *, new_secret: bool = False, actor: str = "skein"
    ) -> Issued:
        """设置（或清空）回调地址。清空地址会连带清掉签名密钥；第一次配地址、或
        ``new_secret=True`` 时生成新的签名密钥，随返回值给出一次。"""
        assistant = self._need_active(ref)
        url = validate_callback_url(url)
        secret = ""
        generated = False
        if url:
            if new_secret or not assistant.has_callback_secret:
                secret, generated = generate_secret(), True
            else:
                row = self._conn.execute(
                    "SELECT callback_secret FROM assistants WHERE id = ?", (assistant.id,)
                ).fetchone()
                secret = str(row["callback_secret"]) if row else ""
        self._conn.execute(
            "UPDATE assistants SET callback_url = ?, callback_secret = ? WHERE id = ?",
            (url, secret, assistant.id),
        )
        db.record_audit(
            self._conn,
            actor,
            "assistant.callback",
            {
                "id": assistant.id,
                "name": assistant.name,
                "callback_url": url,
                "new_secret": generated,
            },
        )
        return Issued(self._refresh(assistant), callback_secret=secret if generated else "")

    # ---------------- 环境文件里声明的凭证 ----------------

    def declare(self, items: list[dict]) -> list[str]:
        """把 ``SKEIN_ASSISTANTS`` 里声明的凭证同步进库：声明为准。

        - 名字对应 ``source='env'`` 的行：密钥或用途变了就更新（轮换 = 改环境文件再重启）。
        - ``source='env'`` 但这次没再声明的：吊销。
        - 名字撞上 ``source='api'`` 的行（控制台 / 命令行签发的）：不动，记一条警告。
        """
        wanted: dict[str, dict] = {}
        for item in items:
            if isinstance(item, dict):
                name = str(item.get("name") or "").strip().lower()
                if name:
                    wanted[name] = item
        applied: list[str] = []
        for row in self.list():
            item = wanted.get(row.name)
            if row.source != "env":
                if item is not None:
                    log.warning(
                        "SKEIN_ASSISTANTS: %s was issued through the API; the declaration is ignored",
                        row.name,
                    )
                continue
            if item is None:
                self._revoke(row, "env")
                log.info("assistant %s revoked: no longer declared in SKEIN_ASSISTANTS", row.name)
            elif self._declaration_changes(row, item):
                self._apply_declaration(row, item)
                applied.append(row.name)
        existing = {assistant.name for assistant in self.list() if assistant.source == "env"}
        for name, item in wanted.items():
            if name in existing:
                continue
            try:
                self.issue(
                    name,
                    str(item.get("purpose") or ""),
                    str(item.get("callback_url") or ""),
                    actor="env",
                    source="env",
                    key=str(item.get("key") or ""),
                )
            except ValueError as exc:
                log.warning("SKEIN_ASSISTANTS: %s skipped: %s", name, exc)
            else:
                applied.append(name)
        return applied

    # ---------------- 内部 ----------------

    def _need_active(self, ref: str) -> Assistant:
        assistant = self.get(ref)
        if assistant is None:
            raise LookupError(f"no such assistant: {ref}")
        if assistant.revoked_at is not None:
            raise ValueError(f"assistant {assistant.name!r} is revoked; issue a new one instead")
        return assistant

    def _refresh(self, assistant: Assistant) -> Assistant:
        found = self.get(assistant.id)
        assert found is not None
        return found

    def _revoke(self, assistant: Assistant, actor: str) -> None:
        self._conn.execute(
            "UPDATE assistants SET revoked_at = ?, revoked_by = ?, callback_secret = ''"
            " WHERE id = ?",
            (self._clock(), actor, assistant.id),
        )
        db.record_audit(
            self._conn, actor, "assistant.revoke", {"id": assistant.id, "name": assistant.name}
        )

    def _declaration_changes(self, assistant: Assistant, item: dict) -> bool:
        key = str(item.get("key") or "")
        if key:
            row = self._conn.execute(
                "SELECT key_hash FROM assistants WHERE id = ?", (assistant.id,)
            ).fetchone()
            if row is None or not hmac.compare_digest(row["key_hash"], _digest(key)):
                return True
        return str(item.get("purpose") or "").strip()[:500] != assistant.purpose

    def _apply_declaration(self, assistant: Assistant, item: dict) -> None:
        key = str(item.get("key") or "")
        purpose = str(item.get("purpose") or "").strip()[:500]
        if key:
            self._conn.execute(
                "UPDATE assistants SET key_hash = ?, purpose = ?, rotated_at = ? WHERE id = ?",
                (_digest(key), purpose, self._clock(), assistant.id),
            )
        else:
            self._conn.execute(
                "UPDATE assistants SET purpose = ? WHERE id = ?", (purpose, assistant.id)
            )
        db.record_audit(
            self._conn, "env", "assistant.rotate", {"id": assistant.id, "name": assistant.name}
        )
