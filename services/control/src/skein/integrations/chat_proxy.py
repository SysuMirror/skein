"""聊天代理（可选集成）：把一个外部聊天服务挂到控制面域名后面。

启用条件：配置了 ``SKEIN_QA_CHAT_UPSTREAM``。不配则不挂任何路由。

挂载的路由：

- ``/api/v1/agent/*``、``/task/*`` → 原样转发到上游。调用方自己的鉴权凭据透传，
  Cookie 不转发；控制台已登录的人额外带短时签名的 ``x-skein-user`` 头
  （需要 ``SKEIN_ASSERTION_KEY``）。
- 聊天里真正发起任务的消息会作为 ``source=chat`` 的事件留痕，走与其他来源
  相同的准入策略。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse

from skein import store
from skein.admission import Event, decide, policy_for
from skein.auth import LEVEL_USER, Principal, SessionVerifier
from skein.qa_proxy import QAProxy, sign_user_header

QA_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]
# 只把真正发起任务的消息留痕；bootstrap/close/resume 不算
_TASK_PATHS = ("task", "task/sse", "sse")


class ChatProxyIntegration:
    name = "chat_proxy"

    def __init__(
        self,
        upstream: str,
        client: Any | None = None,
        user_secret: str = "",
        verifier: SessionVerifier | None = None,
        record: sqlite3.Connection | None = None,
    ) -> None:
        self._proxy = QAProxy(upstream, client)
        self._secret = user_secret
        self._verifier = verifier
        self._conn = record

    @property
    def enabled(self) -> bool:
        return self._proxy.enabled_chat

    async def _principal(self, request: Request) -> Principal | None:
        if self._verifier is None:
            return None
        try:
            return await asyncio.to_thread(
                self._verifier.verify, request.cookies, LEVEL_USER
            )
        except Exception:  # noqa: BLE001 - 没登录/上游不可达都按匿名处理，交给上游鉴权
            return None

    async def _identity_headers(self, request: Request) -> dict[str, str]:
        if not self._secret:
            return {}
        principal = await self._principal(request)
        if principal is None:
            return {}
        return {"x-skein-user": sign_user_header(principal, self._secret)}

    async def _record_event(self, request: Request, path: str) -> None:
        if self._conn is None or path not in _TASK_PATHS:
            return
        principal = await self._principal(request)
        if principal is None:
            return
        try:
            raw = await request.body()
            payload = json.loads(raw) if raw else {}
        except (json.JSONDecodeError, UnicodeDecodeError):
            return
        if not isinstance(payload, dict):
            return
        text = str(
            payload.get("task") or payload.get("message") or payload.get("prompt") or ""
        ).strip()
        if not text:
            return
        session_id = (
            str(payload.get("session_id") or payload.get("thread_id") or "").strip()[:128]
            or f"user:{principal.sub}"
        )
        digest = hashlib.sha256(text.encode()).hexdigest()[:16]
        event = Event(
            source="chat",
            origin_id=f"{session_id}#{digest}",
            text=text,
            actor=principal.sub,
            mentioned=False,
            in_allowlist=True,
            parent_id=session_id,
        )
        decision = decide(event, policy_for(self._conn, "chat"))
        thread_origin, thread_key = store.thread_identity(event)
        thread_id = store.ensure_thread(self._conn, thread_origin, thread_key)
        store.record_event(
            self._conn,
            event.source,
            event.origin_id,
            thread_id,
            {
                "text": text,
                "actor": principal.sub,
                "session_id": session_id,
                "admin": principal.admin,
            },
            decision.verdict.value,
        )

    def install(self, app: FastAPI) -> None:
        proxy = self._proxy

        @app.api_route("/api/v1/agent/{path:path}", methods=QA_METHODS)
        async def chat_agent(path: str, request: Request) -> StreamingResponse:
            await self._record_event(request, path)
            return await proxy.forward(
                request,
                proxy.chat_upstream,
                f"api/v1/agent/{path}",
                await self._identity_headers(request),
            )

        @app.api_route("/task/{path:path}", methods=QA_METHODS)
        async def chat_task(path: str, request: Request) -> StreamingResponse:
            await self._record_event(request, path)
            return await proxy.forward(
                request,
                proxy.chat_upstream,
                f"task/{path}",
                await self._identity_headers(request),
            )
