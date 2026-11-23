"""对外通知：工单的状态变了，告诉提交它的那个系统。

通道是可插拔的（见 :class:`Channel`），内建一个：带签名的 HTTP 回调。签名用的是
每个助手自己的回调密钥，所以收到回调的一方不必持有雁阵的任何令牌——这也是雁阵
不再把主令牌交给外部系统的那条路。

新增一种通道：实现 ``send``，在 ``CHANNELS`` 里登记，再用 ``SKEIN_NOTIFY_CHANNELS``
选上它。
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import sqlite3
import threading
import time
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

log = logging.getLogger("skein.notify")

# 回调里时间戳的有效期：挡住原样重放的旧回调
TIMESTAMP_TOLERANCE_S = 300
# 工单内部状态 → 对外公开的状态
PUBLIC_STATUS = {"working": "working", "done": "fixed", "rejected": "need_info"}

Poster = Callable[[str, dict, str], None]


@dataclass(frozen=True)
class Target:
    """回调发往哪里、用什么密钥签。"""

    url: str
    secret: str = ""


class Channel(Protocol):
    """一种通知通道。"""

    name: str

    def send(self, url: str, payload: dict, secret: str) -> None: ...


def sign(timestamp: int, body: bytes, secret: str) -> str:
    """回调签名：``v1=<hex(HMAC-SHA256(secret, "<timestamp>.<body>"))>``。"""
    message = f"{timestamp}.".encode() + body
    return "v1=" + hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def verify(timestamp: int, body: bytes, secret: str, signature: str, now: float | None = None) -> bool:
    """收到回调的一方这样验签。时间戳超期或签名不对都算失败。"""
    current = time.time() if now is None else now
    if abs(current - timestamp) > TIMESTAMP_TOLERANCE_S:
        return False
    if not secret or not signature.startswith("v1="):
        return False
    return hmac.compare_digest(sign(timestamp, body, secret), signature)


class WebhookChannel:
    """POST 一个 JSON，带时间戳与签名头。只发一次，失败只记日志（见文档的「不做重试」）。"""

    name = "webhook"

    def __init__(self, timeout: float = 3.0, urlopen: Any = None) -> None:
        self._timeout = timeout
        # 不走系统代理：回调地址通常是内网或本机
        self._urlopen = urlopen

    def send(self, url: str, payload: dict, secret: str = "") -> None:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
        headers = {
            "Content-Type": "application/json",
            "X-Skein-Event": str(payload.get("event") or ""),
            "User-Agent": "skein-control",
        }
        if secret:
            timestamp = int(time.time())
            headers["X-Skein-Webhook-Timestamp"] = str(timestamp)
            headers["X-Skein-Webhook-Signature"] = sign(timestamp, body, secret)
        request = urllib.request.Request(url, data=body, headers=headers, method="POST")
        open_url = self._urlopen if self._urlopen is not None else _open_without_proxy()
        with open_url(request, timeout=self._timeout) as response:
            response.read()


_default_open: Any = None


def _open_without_proxy() -> Any:
    """构造 opener 会加载系统根证书，比较慢：进程里只建一次，第一次发回调时再建。"""
    global _default_open
    if _default_open is None:
        _default_open = urllib.request.build_opener(urllib.request.ProxyHandler({})).open
    return _default_open


CHANNELS: dict[str, type[WebhookChannel]] = {"webhook": WebhookChannel}


class Notifier:
    """工单状态变化 → 公开状态 + 回调。

    谁该收到回调由 ``resolve`` 回答（助手凭证那一侧提供）：传入提交这张工单的助手 id，
    返回回调地址与密钥；没有回调对象就只更新公开状态。
    """

    def __init__(
        self,
        conn: sqlite3.Connection,
        resolve: Callable[[str], Target | None],
        channel: Channel | None = None,
        poster: Poster | None = None,
        public_url: str = "",
    ) -> None:
        self._conn = conn
        self._resolve = resolve
        self._channel = channel if channel is not None else WebhookChannel()
        # 回调在后台线程里发：不能让一个慢的下游拖住「验收」这个请求
        self._poster = poster or self._in_background
        self._public_url = public_url.rstrip("/")

    def ticket_status(self, ticket_id: str, status: str) -> str | None:
        """工单进入 ``status``（working / done / rejected）。返回它现在的公开状态。"""
        public = PUBLIC_STATUS.get(status)
        if not public:
            return None
        row = self._conn.execute(
            "SELECT code, title, origin, origin_id, requester, public_status, submitted_by"
            " FROM tickets WHERE id = ?",
            (ticket_id,),
        ).fetchone()
        if row is None or row["public_status"] == public:
            return row["public_status"] if row is not None else None
        self._conn.execute(
            "UPDATE tickets SET public_status = ?, updated_at = ? WHERE id = ?",
            (public, time.time(), ticket_id),
        )
        submitted_by = str(row["submitted_by"] or "")
        target = self._resolve(submitted_by) if submitted_by else None
        if target is not None and target.url:
            self._poster(
                target.url,
                {
                    "event": "ticket.status",
                    "ticket": {
                        "id": ticket_id,
                        "code": row["code"],
                        "status": status,
                        "public_status": public,
                        "title": row["title"],
                        "source": row["origin"],
                        "external_id": row["origin_id"],
                        "requester": row["requester"],
                        "url": self.ticket_url(ticket_id),
                    },
                },
                target.secret,
            )
        return public

    def ticket_url(self, ticket_id: str) -> str:
        """工单在控制台里的地址；没配对外地址就是空串。"""
        return f"{self._public_url}/console/#/tickets/{ticket_id}" if self._public_url else ""

    def _in_background(self, url: str, payload: dict, secret: str) -> None:
        def run() -> None:
            try:
                self._channel.send(url, payload, secret)
            except (OSError, ValueError) as exc:
                log.warning("webhook %s %s failed: %s", payload.get("event"), url, exc)

        threading.Thread(target=run, daemon=True).start()
