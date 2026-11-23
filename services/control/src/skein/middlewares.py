"""纯 ASGI 中间件：不缓冲请求、不另起任务组，异常能原样传到接口这一层。

（Starlette 的 ``@app.middleware("http")`` 会把下游放进单独的任务组，从 ``receive`` 里
抛出的异常传不到接口——请求体超限要回 413 就得走这里的方式。）
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import HTTPException

UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def _header(scope: Any, name: str) -> str:
    for key, value in scope.get("headers") or []:
        if (key.decode("latin-1") if isinstance(key, bytes) else str(key)).lower() == name:
            return value.decode("latin-1") if isinstance(value, bytes) else str(value)
    return ""


async def _reply(send: Any, status: int, body: dict) -> None:
    raw = json.dumps(body, ensure_ascii=False).encode()
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json; charset=utf-8"),
                (b"content-length", str(len(raw)).encode()),
                (b"cache-control", b"no-store"),
            ],
        }
    )
    await send({"type": "http.response.body", "body": raw})


class BodyLimit:
    """给一段路径加请求体上限：超过就回 413，不把请求体读进内存。

    带了 Content-Length 且已超限的，直接拒绝、不往下走；没带（分块传输）的，边读边数，
    超限的那一刻在读请求体的地方抛 ``HTTPException(413)``。
    """

    def __init__(self, app: Any, limit: int, under: str, exempt: str = "", status: int = 413) -> None:
        self.app = app
        self.limit = limit
        self.under = under
        self.exempt = exempt
        self.status = status

    def _limited(self, scope: Any) -> bool:
        if scope["type"] != "http":
            return False
        path = str(scope.get("path") or "")
        return path.startswith(self.under) and not (self.exempt and path.startswith(self.exempt))

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if not self._limited(scope):
            await self.app(scope, receive, send)
            return
        declared = _header(scope, "content-length")
        if declared.isdigit() and int(declared) > self.limit:
            await _reply(
                send,
                self.status,
                {"detail": self._detail()},
            )
            return
        seen = 0

        async def counted() -> Any:
            nonlocal seen
            message = await receive()
            if message["type"] == "http.request":
                seen += len(message.get("body") or b"")
                if seen > self.limit:
                    # FastAPI 读请求体时对 HTTPException 原样上抛，由它的异常处理器回 413
                    raise HTTPException(status_code=self.status, detail=self._detail())
            return message

        await self.app(scope, counted, send)

    def _detail(self) -> dict:
        return {"code": "payload_too_large", "message": f"request body exceeds {self.limit} bytes"}


class SameSiteOnly:
    """带着指定 Cookie 的写请求，只许同站发起：浏览器标明是跨站的就直接拒。

    SameSite=Lax 的 Cookie 本来就不会随跨站请求发出，这是给不支持 SameSite 的旧浏览器
    兜底。只在请求确实带着这个 Cookie 时才管，别的请求原样放行。
    """

    def __init__(self, app: Any, cookie_name: str, enabled: bool = True) -> None:
        self.app = app
        self.cookie_name = cookie_name
        self.enabled = enabled

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if (
            self.enabled
            and scope["type"] == "http"
            and scope.get("method") in UNSAFE_METHODS
            and _header(scope, "sec-fetch-site") == "cross-site"
            and self._carries_cookie(_header(scope, "cookie"))
        ):
            await _reply(send, 403, {"detail": "cross_site_request"})
            return
        await self.app(scope, receive, send)

    def _carries_cookie(self, header: str) -> bool:
        return any(
            part.strip().startswith(f"{self.cookie_name}=") for part in header.split(";")
        )
