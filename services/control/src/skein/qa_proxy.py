"""反向代理 + 身份签名头：把一个外部聊天服务挂到控制面域名后面。

- ``/api/v1/agent/*``、``/task/*`` → 上游：调用方自己的鉴权凭据原样透传，
  用户级鉴权仍由上游负责；Cookie 不转发，避免把控制台的登录会话带给上游。
- 控制台已登录的人，额外带一个短时签名的 ``x-skein-user`` 头，让上游认得他是谁。

上游地址为空时不挂载任何路由（见 ``skein.integrations.chat_proxy``）。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from collections.abc import AsyncIterator

import httpx
from fastapi import HTTPException, Request
from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask

from skein.auth import Principal

HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
    "host",
}
# 浏览器不得伪造上游身份头；只允许 Skein 自己签。
STRIP_HEADERS = HOP_HEADERS | {"x-skein-user"}


def sign_user_header(principal: Principal, secret: str, ttl: int = 60) -> str:
    """给 chat 上游的短时身份头：v1.<base64url(payload)>.<hex hmac>。"""
    payload = {
        "sub": principal.sub,
        "internal": bool(principal.internal),
        "admin": bool(principal.admin),
        "super_admin": bool(principal.super_admin),
        "email": principal.email,
        "name": principal.name,
        "exp": int(time.time()) + max(ttl, 1),
    }
    raw = json.dumps(
        payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode()
    body = base64.urlsafe_b64encode(raw).decode().rstrip("=")
    sig = hmac.new(
        secret.encode(), f"v1.{body}".encode(), hashlib.sha256
    ).hexdigest()
    return f"v1.{body}.{sig}"


def verify_user_header(value: str, secret: str, now: float | None = None) -> dict | None:
    """校验身份头；无效/过期/缺字段返回 None。"""
    try:
        version, body, sig = value.split(".")
        if version != "v1":
            return None
        expected = hmac.new(
            secret.encode(), f"v1.{body}".encode(), hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(sig, expected):
            return None
        padding = "=" * (-len(body) % 4)
        data = json.loads(base64.urlsafe_b64decode(body + padding))
    except (ValueError, TypeError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or not data.get("sub"):
        return None
    try:
        if int(data.get("exp", 0)) < int(now if now is not None else time.time()):
            return None
    except (TypeError, ValueError):
        return None
    return data


class QAProxy:
    def __init__(
        self,
        chat_upstream: str = "",
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.chat_upstream = chat_upstream.rstrip("/")
        self._client = client

    @property
    def _http(self) -> httpx.AsyncClient:
        """第一次转发时才建：构造客户端要加载系统根证书，没配聊天上游的部署不必付这笔开销。"""
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(connect=5.0, read=None, write=30.0, pool=5.0),
                follow_redirects=False,
                # 上游通常在本机或内网：禁系统代理（HTTP(S)_PROXY 会把 127.0.0.1 也带走）。
                trust_env=False,
            )
        return self._client

    @property
    def enabled_chat(self) -> bool:
        return bool(self.chat_upstream)

    async def forward(
        self,
        request: Request,
        upstream: str,
        path: str,
        extra_headers: dict[str, str] | None = None,
    ) -> StreamingResponse:
        url = f"{upstream}/{path.lstrip('/')}"
        if request.url.query:
            url += f"?{request.url.query}"
        headers = {
            key: value
            for key, value in request.headers.items()
            if key.lower() not in STRIP_HEADERS and key.lower() != "cookie"
        }
        if extra_headers:
            headers.update(extra_headers)
        body = await request.body()
        http = self._http
        outgoing = http.build_request(
            request.method, url, headers=headers, content=body or None
        )
        try:
            response = await http.send(outgoing, stream=True)
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail=f"qa upstream error: {exc}") from exc
        response_headers = {
            key: value
            for key, value in response.headers.items()
            if key.lower() not in HOP_HEADERS
            and key.lower() not in ("content-length", "content-encoding")
        }
        return StreamingResponse(
            self._stream(response),
            status_code=response.status_code,
            headers=response_headers,
            background=BackgroundTask(response.aclose),
        )

    @staticmethod
    async def _stream(response: httpx.Response) -> AsyncIterator[bytes]:
        async for chunk in response.aiter_raw():
            yield chunk
