"""控制面的 HTTP 接口：健康检查、登录会话、工单与外部入口、Agent、transcript 与留痕。

与具体部署环境无关。只在某些部署才需要的能力（聊天代理等）放在
``skein.integrations``，由配置决定是否挂载；给外部助手用的公共 API 在 ``skein.public_api``。

每个接口用 ``require(权限)`` 声明自己要什么权限，权限与角色的对应关系只在
``skein.access`` 里定义。
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import logging
import sqlite3
import time
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request, WebSocket
from fastapi.responses import JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from skein import __version__, access, db, llm_trace, logbuf, metrics, public_api, store, watchdog
from skein.access import OPS_INGEST, READ, TICKET_FILE, TICKET_OPERATE
from skein.accounts import Directory
from skein.agent import DEFAULT_AGENT_MODEL, Agent
from skein.assistants import AssistantStore, valid_key_shape
from skein.assistants_api import create_manage_router
from skein.auth import (
    CURRENT_PRINCIPAL,
    LEVEL_CONSOLE,
    LEVEL_USER,
    USER_LEVELS,
    AuthRejected,
    AuthUnavailable,
    Principal,
    SessionVerifier,
    token_matches,
)
from skein.auth_providers import MinimalConfig
from skein.auth_providers import build as build_login
from skein.channel import ChannelHub
from skein.integrations import install_all
from skein.integrations.chat_proxy import ChatProxyIntegration
from skein.middlewares import SameSiteOnly
from skein.notify import Notifier
from skein.ratelimit import RateLimiter
from skein.session_store import SessionStore, TurnConflict
from skein.summary import summary as build_summary
from skein.ui_api import create_ui_router, ticket_rows

log = logging.getLogger("skein.api")

# 登录限速：每个来源 IP 每分钟 10 次（成功失败都算）；本地账号另有按账号的失败次数上限
LOGIN_PER_MINUTE = 10
LOGIN_FAILURES = 20
LOGIN_FAILURE_WINDOW_S = 900


class TurnIn(BaseModel):
    session_id: str
    turn_index: int = Field(ge=0)
    data: str
    head_sha: str | None = None
    branch: str | None = None


class ReviewIn(BaseModel):
    decision: str


class LoginIn(BaseModel):
    email: str = ""
    password: str = ""
    # 令牌模式下填这个（与 SKEIN_TOKEN 相同），换一个浏览器会话
    token: str = ""
    # console=控制台（要有雁阵账号）；market=只要认出是谁的入口
    surface: str = "console"


class TicketIn(BaseModel):
    title: str
    summary: str = ""
    platform: str = ""
    dedup_key: str = ""
    dispatch: bool = False


class LLMCallIn(BaseModel):
    session_id: str = ""
    actor: str = ""
    source: str = ""
    model: str = ""
    prompt: str = ""
    response: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: int = 0
    status: str = "ok"
    error: str = ""


class AgentChatIn(BaseModel):
    thread_id: str = ""
    message: str


class IntakeIn(BaseModel):
    source: str
    thread_id: str
    message_id: str = ""
    parent_id: str = ""
    requester: str = ""
    title: str
    summary: str = ""
    category: str = "bug"
    severity: str = "normal"
    # 只是写进工单摘要备注的标签，不参与逻辑；部署方可按自己的可见性口径传值
    visibility: str = "market"
    dedup_key: str = ""


def create_app(
    conn: sqlite3.Connection,
    blob_root: str,
    token: str,
    *,
    platform_repos: dict[str, str] | None = None,
    pr_service: Any | None = None,
    test_envs: dict[str, str] | None = None,
    login: SessionVerifier | None = None,
    directory: Directory | None = None,
    assistants: AssistantStore | None = None,
    notifier: Notifier | None = None,
    api_rate_limit: int = public_api.DEFAULT_RATE_LIMIT,
    api_trace: bool = False,
    public_url: str = "",
    docs: bool = False,
    qa_chat_upstream: str = "",
    qa_client: Any | None = None,
    qa_user_secret: str = "",
    llm_base: str = "",
    llm_key: str = "",
    agent_model: str = DEFAULT_AGENT_MODEL,
    model_client: Any | None = None,
    console_dir: str | None = None,
) -> FastAPI:
    """``docs=True`` 才会暴露 /docs 与 /openapi.json（缺省不暴露：内部接口的清单不必对外）。"""
    app = FastAPI(
        title="skein-control",
        version=__version__,
        docs_url="/docs" if docs else None,
        redoc_url=None,
        openapi_url="/openapi.json" if docs else None,
    )
    sessions = SessionStore(conn, blob_root)
    hub = ChannelHub(conn, token, platform_repos or {}, pr_service=pr_service)
    agent = (
        Agent(conn, llm_base, llm_key, agent_model, hub, client=model_client)
        if llm_base and llm_key
        else None
    )
    # 没给登录配置就是零配置的令牌模式
    if login is None:
        login, directory, _ = build_login(MinimalConfig(token), conn)
    if directory is None:
        directory = Directory(conn)
    if assistants is None:
        assistants = AssistantStore(conn)
    if notifier is None:
        notifier = Notifier(conn, assistants.callback_target, public_url=public_url)
    login_limiter = RateLimiter(LOGIN_PER_MINUTE, 60)
    account_limiter = RateLimiter(LOGIN_FAILURES, LOGIN_FAILURE_WINDOW_S)

    async def _verify_in_thread(call: Any, *args: Any) -> Principal:
        """在线程里跑 provider 的网络调用，并把拒绝翻译成 HTTP 状态码。"""
        try:
            return await asyncio.to_thread(call, *args)
        except AuthRejected as error:
            raise HTTPException(status_code=error.status, detail=error.code) from error
        except AuthUnavailable as error:
            raise HTTPException(status_code=503, detail="sso_unavailable") from error

    async def authenticate(
        request: Request, x_skein_token: str = Header(default="")
    ) -> Principal:
        # 助手凭证只对公共 API 有效：拿它来敲内部接口，明确告诉调用方走错了门
        if valid_key_shape(public_api.bearer(request)):
            raise HTTPException(status_code=403, detail="assistant_key_not_allowed")
        # 机器走主令牌，人走登录会话。带了主令牌却不对：明确说令牌无效，而不是「没登录」
        if x_skein_token:
            if token and token_matches(x_skein_token, token):
                return Principal.machine()
            raise HTTPException(status_code=401, detail="invalid_token")
        return await _verify_in_thread(login.verify, request.cookies, LEVEL_CONSOLE)

    def require(*permissions: str, human: bool = False) -> Any:
        """路由依赖：先认请求，再按权限矩阵放行。接口可以把返回值当主体用。

        ``human=True``：这件事必须由登录的人来做，机器拿着主令牌也不行
        （矩阵里标了 signed-in only 的权限）。
        """

        async def dependency(
            request: Request, x_skein_token: str = Header(default="")
        ) -> Principal:
            principal = await authenticate(request, x_skein_token)
            if not all(principal.allows(permission) for permission in permissions):
                detail = "sign_in_required" if human and principal.is_machine else "forbidden"
                raise HTTPException(status_code=403, detail=detail)
            CURRENT_PRINCIPAL.set(principal)
            return principal

        return Depends(dependency)

    # Agent 的操作要留痕到具体的人：机器拿着主令牌也不能替人说话
    signed_in = require(access.AGENT_CHAT, human=True)
    # 人建的单留痕到人；接口里要读主体，所以单独起个名字
    file_ticket = require(TICKET_FILE)

    def session_payload(principal: Principal) -> dict:
        return {
            "mode": login.mode,
            "provider": login.provider.name,
            "user": principal.as_dict(),
            # kind 是旧字段：admin / market，给只关心「是不是管理员」的调用方
            "kind": "admin" if principal.admin else "market",
            "permissions": principal.permissions(),
        }

    @app.get("/health/live")
    def live() -> dict:
        return {"status": "ok"}

    @app.get("/health/ready")
    def ready() -> dict:
        conn.execute("SELECT 1").fetchone()
        return {"status": "ready"}

    @app.get("/metrics")
    def prometheus() -> PlainTextResponse:
        """Prometheus 文本格式的计数指标。只有数字，不含任何工单内容，所以不要认证。"""
        return PlainTextResponse(metrics.render(conn), media_type="text/plain; version=0.0.4")

    @app.get("/api/session")
    async def session_probe(request: Request, surface: str = "console") -> dict:
        """控制台启动时打这个：决定出登录页还是直接进。

        没带会话也行：告诉前端这个部署用哪种登录方式（token / local / sso），由它出对应的
        登录表单。带着会话时再验身份——被拒的（不是内部成员、没有账号、账号停用）照常拒绝。
        """
        level = LEVEL_USER if surface in USER_LEVELS else LEVEL_CONSOLE
        signed_out = {"mode": login.mode, "provider": login.provider.name}
        if login.cookie_value(request.cookies) is None:
            return signed_out
        try:
            principal = await _verify_in_thread(login.verify, request.cookies, level)
        except HTTPException as exc:
            # 会话无效 / 已过期：当作没登录，控制台出登录表单
            if exc.status_code == 401:
                return signed_out
            raise
        CURRENT_PRINCIPAL.set(principal)
        return session_payload(principal)

    @app.post("/api/session/login")
    async def session_login(request: Request, body: LoginIn) -> JSONResponse:
        client_ip = request.client.host if request.client else "?"
        allowed, retry_after = login_limiter.hit(client_ip)
        if not allowed:
            raise HTTPException(
                status_code=429,
                detail="too_many_attempts",
                headers={"Retry-After": str(int(retry_after) + 1)},
            )
        level = LEVEL_USER if body.surface in USER_LEVELS else LEVEL_CONSOLE
        if login.mode == "token":
            if not body.token:
                raise HTTPException(status_code=400, detail="token_required")
            identifier, secret = body.token, ""
        else:
            identifier, secret = body.email.strip(), body.password
        # 本地账号按「同一个邮箱连续失败」限速：撞库换 IP 也绕不过去
        locked = account_limiter.retry_after(identifier) if login.mode == "local" else 0.0
        if locked > 0:
            raise HTTPException(
                status_code=429,
                detail="too_many_attempts",
                headers={"Retry-After": str(int(locked) + 1)},
            )
        try:
            principal, session = await asyncio.to_thread(
                login.login, identifier, secret, level
            )
        except AuthRejected as error:
            if login.mode == "local":
                account_limiter.hit(identifier)
            raise HTTPException(status_code=error.status, detail=error.code) from error
        except AuthUnavailable as error:
            raise HTTPException(status_code=503, detail="sso_unavailable") from error
        db.record_audit(
            conn, principal.label, "session.login", {"provider": login.provider.name, "sub": principal.sub}
        )
        response = JSONResponse(session_payload(principal))
        session_value = str(session.get("session") or "")
        if session_value:
            domain = login.cookie_domain_for(str(session.get("cookie_domain") or "") or None)
            response.set_cookie(
                key=str(session.get("cookie_name") or login.cookie_names[0]),
                value=session_value,
                domain=domain,
                max_age=int(session.get("cookie_max_age") or login.cookie_max_age),
                path="/",
                httponly=True,
                secure=login.cookie_secure,
                samesite="lax",
            )
            # 记住实际种的域：登出时要用同一个域清
            login.note_login(session_value, domain)
        CURRENT_PRINCIPAL.set(principal)
        return response

    @app.post("/api/session/logout")
    async def session_logout(request: Request) -> JSONResponse:
        session = login.cookie_value(request.cookies)
        if session:
            await asyncio.to_thread(login.revoke, session)
        response = JSONResponse({"ok": True})
        # 清 Cookie 的域要和种的时候一致：显式配置 → 本次登录记录 → provider 推导
        domain = login.logout_cookie_domain()
        for name in login.cookie_names:
            response.delete_cookie(key=name, domain=domain, path="/")
        return response

    # 雁阵自己发的会话 Cookie 只许同站使用（见 middlewares.SameSiteOnly）
    app.add_middleware(SameSiteOnly, cookie_name=login.cookie_names[0], enabled=login.mode != "sso")

    app.include_router(create_ui_router(conn, blob_root, require(READ), test_envs=test_envs))
    app.include_router(create_manage_router(directory, assistants, require))
    public_api.install(
        app,
        conn,
        assistants,
        rate_limit=api_rate_limit,
        trace_enabled=api_trace,
        public_url=public_url,
    )

    # ---- 可选集成：不配就不挂路由 ----
    chat_proxy = ChatProxyIntegration(
        upstream=qa_chat_upstream,
        client=qa_client,
        user_secret=qa_user_secret,
        verifier=login,
        record=conn,
    )
    mounted = install_all(app, [chat_proxy])
    log.info("integrations mounted: %s", ", ".join(mounted) or "(none)")

    @app.get("/internal/summary", dependencies=[require(READ)])
    def internal_summary() -> dict:
        return build_summary(conn)

    @app.get("/internal/logs", dependencies=[require(READ)])
    def internal_logs(limit: int = 200, level: str = "", component: str = "") -> dict:
        """最近的控制面日志（进程内环形缓冲，重启即空）。"""
        return {
            "records": logbuf.recent(
                limit=min(max(0, limit), 1000),
                level=level or None,
                component=component or None,
            )
        }

    @app.post("/internal/llm/calls", dependencies=[require(OPS_INGEST)])
    def record_llm_call(body: LLMCallIn) -> dict:
        call_id = llm_trace.record(conn, **body.model_dump())
        return {"ok": True, "id": call_id}

    @app.get("/internal/llm/calls", dependencies=[require(READ)])
    def list_llm_calls(
        session_id: str | None = None, source: str | None = None, limit: int = 200
    ) -> dict:
        return {
            "calls": llm_trace.calls(
                conn, session_id=session_id, source=source, limit=limit
            )
        }

    @app.get("/internal/llm/sessions", dependencies=[require(READ)])
    def list_llm_sessions(limit: int = 100) -> dict:
        return {"sessions": llm_trace.sessions(conn, limit=limit)}

    @app.post("/internal/agent/chat")
    async def agent_chat(body: AgentChatIn, principal: Principal = signed_in) -> dict:
        if agent is None:
            raise HTTPException(status_code=503, detail="agent_not_configured")
        message = body.message.strip()
        if not message:
            raise HTTPException(status_code=400, detail="message_required")
        thread_id = body.thread_id.strip()[:64] or (
            f"agent-{principal.sub[:12]}-{int(time.time())}"
        )
        result = await agent.chat(principal, thread_id, message)
        return {"thread_id": thread_id, **result}

    @app.get("/internal/agent/thread/{thread_id}")
    async def agent_thread(thread_id: str, principal: Principal = signed_in) -> dict:
        key = thread_id[:64]
        rows = conn.execute(
            "SELECT role, content, created_at FROM agent_messages"
            " WHERE thread_id = ? ORDER BY rowid",
            (key,),
        ).fetchall()
        return {"thread_id": key, "actor": principal.sub, "messages": [dict(row) for row in rows]}

    @app.get("/internal/signals", dependencies=[require(READ)])
    def list_signals() -> dict:
        return {"signals": watchdog.signals(conn)}

    @app.get("/internal/tickets", dependencies=[require(READ)])
    def list_tickets(status: str | None = None, limit: int = 100) -> dict:
        return {"tickets": ticket_rows(conn, status=status, limit=limit)}

    @app.post("/internal/intake", dependencies=[require(TICKET_OPERATE)])
    def intake(body: IntakeIn) -> dict:
        """外部入口建单：某个来源的一帖/一评/一段对话 → 工单；dedup 幂等。

        这是雁阵的核心入口之一：巡检、Watchdog、内部系统都从这里把「有人提出了一个
        需求/缺陷」变成工单。同一线索里的后续消息会追加到在途工单。

        它要主令牌。不该拿到主令牌的外部助手请走公共 API（``/api/v1/tickets``）。
        """
        title = body.title.strip()[:120]
        if not title:
            raise HTTPException(status_code=400, detail="title required")
        source = body.source.strip() or "forum_post"
        thread_key = body.thread_id.strip()
        if not thread_key:
            raise HTTPException(status_code=400, detail="thread required")
        if source == "forum_ccomment" and (
            not body.parent_id.strip() or not body.message_id.strip()
        ):
            raise HTTPException(status_code=400, detail="parent and message required")
        material = (
            f"{source}|{thread_key}|{body.message_id}|{body.requester}"
            f"|{title}|{body.summary}"
        )
        dedup = body.dedup_key.strip() or (
            "intake:" + hashlib.sha256(material.encode()).hexdigest()[:32]
        )
        existing = store.find_ticket_by_dedup(conn, dedup)
        if existing:
            row = conn.execute(
                "SELECT code, status, public_status FROM tickets WHERE id = ?",
                (existing,),
            ).fetchone()
            return {
                "ticket_id": existing,
                "code": row["code"] if row else "",
                "status": row["status"] if row else "",
                "public_status": row["public_status"] if row else "",
                "created": False,
            }
        thread_origin = "chat" if source == "chat" else "forum"
        thread_id = store.ensure_thread(conn, thread_origin, thread_key)
        if source in ("forum_comment", "forum_ccomment"):
            active = conn.execute(
                "SELECT id, code, status, public_status FROM tickets"
                " WHERE thread_id = ? AND status NOT IN ('done','rejected','cancelled')"
                " ORDER BY created_at DESC LIMIT 1",
                (thread_id,),
            ).fetchone()
            if active is not None:
                addition = (
                    f"\n\n[followup message={body.message_id or '-'}"
                    f" requester={body.requester}] {body.summary.strip() or title}"
                )
                conn.execute(
                    "UPDATE tickets SET summary = substr(summary || ?, -16000),"
                    " updated_at = ? WHERE id = ?",
                    (addition, time.time(), active["id"]),
                )
                db.record_audit(
                    conn,
                    "intake",
                    "ticket.followup",
                    {
                        "ticket_id": active["id"],
                        "thread": thread_key,
                        "message": body.message_id,
                        "parent": body.parent_id,
                        "requester": body.requester,
                    },
                )
                return {
                    "ticket_id": active["id"],
                    "code": active["code"],
                    "status": active["status"],
                    "public_status": active["public_status"],
                    "created": False,
                    "followup": True,
                }
        origin_id = thread_key
        if source == "forum_comment" and body.message_id:
            origin_id = f"{thread_key}:{body.message_id}"
        elif source == "forum_ccomment" and body.message_id:
            origin_id = f"{thread_key}:{body.parent_id.strip()}:{body.message_id}"
        note = body.summary.strip()
        note += (
            f"\n\n[category={body.category} severity={body.severity}"
            f" visibility={body.visibility} requester={body.requester}]"
        )
        ticket_id = store.create_ticket(
            conn, title, thread_id=thread_id, dedup_key=dedup, summary=note
        )
        conn.execute(
            "UPDATE tickets SET origin = ?, origin_id = ?, requester = ?,"
            " public_status = 'received', updated_at = ? WHERE id = ?",
            (
                source,
                origin_id,
                body.requester,
                time.time(),
                ticket_id,
            ),
        )
        db.record_audit(
            conn,
            "intake",
            "ticket.intake",
            {
                "ticket_id": ticket_id,
                "source": source,
                "thread": thread_key,
                "message": body.message_id,
                "parent": body.parent_id,
                "requester": body.requester,
                "category": body.category,
                "severity": body.severity,
            },
        )
        row = conn.execute(
            "SELECT code, status, public_status FROM tickets WHERE id = ?",
            (ticket_id,),
        ).fetchone()
        return {
            "ticket_id": ticket_id,
            "code": row["code"] if row else "",
            "status": row["status"] if row else "staged",
            "public_status": row["public_status"] if row else "received",
            "created": True,
        }

    @app.get("/internal/nodes", dependencies=[require(READ)])
    def list_nodes() -> dict:
        rows = conn.execute(
            "SELECT id, name, host, platforms, status, max_workers, last_seen FROM nodes"
            " ORDER BY id"
        ).fetchall()
        return {"nodes": [dict(row) for row in rows]}

    @app.post("/internal/tickets/{ticket_id}/cancel", dependencies=[require(TICKET_OPERATE)])
    async def cancel_ticket(ticket_id: str) -> dict:
        if not await hub.cancel_ticket(ticket_id):
            raise HTTPException(status_code=409, detail="no_live_dispatch")
        return {"cancelled": True}

    @app.websocket("/api/agent/channel")
    async def agent_channel(ws: WebSocket) -> None:
        await hub.serve(ws)

    @app.post("/internal/tickets/{ticket_id}/dispatch", dependencies=[require(TICKET_OPERATE)])
    async def dispatch_ticket(ticket_id: str, platform: str | None = None) -> dict:
        result = await hub.dispatch_ticket(ticket_id, platform)
        if result.get("ok"):
            notifier.ticket_status(ticket_id, "working")
        return result

    @app.post("/internal/tickets")
    async def create_ticket(body: TicketIn, principal: Principal = file_ticket) -> dict:
        """建单：dedup_key 幂等，可选直接派。外部控制回路（Watchdog、巡检）与控制台都用它。"""
        title = body.title.strip()
        if not title:
            raise HTTPException(status_code=400, detail="title required")
        if body.dispatch and not principal.allows(TICKET_OPERATE):
            raise HTTPException(status_code=403, detail="forbidden")
        if body.dedup_key:
            existing = store.find_ticket_by_dedup(conn, body.dedup_key)
            if existing:
                return {"ticket_id": existing, "created": False, "dispatch": None}
        ticket_id = store.create_ticket(
            conn, title, None, body.dedup_key or None, body.summary.strip()
        )
        # 机器建的单沿用旧称谓；人建的单留痕到人
        actor = principal.label if principal.is_signed_in else "auto"
        db.record_audit(
            conn,
            actor,
            "ticket",
            {"ticket_id": ticket_id, "title": title[:120], "dedup_key": body.dedup_key},
        )
        result = None
        if body.dispatch and body.platform:
            result = await hub.dispatch_ticket(ticket_id, body.platform)
            if result.get("ok"):
                notifier.ticket_status(ticket_id, "working")
        return {"ticket_id": ticket_id, "created": True, "dispatch": result}

    @app.post("/internal/tickets/{ticket_id}/review", dependencies=[require(TICKET_OPERATE)])
    def review_ticket(ticket_id: str, body: ReviewIn) -> dict:
        if body.decision not in ("approve", "reject"):
            raise HTTPException(status_code=400, detail="decision must be approve or reject")
        status = "done" if body.decision == "approve" else "rejected"
        conn.execute(
            "UPDATE tickets SET status = ?, updated_at = ? WHERE id = ?",
            (status, time.time(), ticket_id),
        )
        principal = CURRENT_PRINCIPAL.get()
        db.record_audit(
            conn,
            principal.label if principal else "human",
            "review",
            {"ticket_id": ticket_id, "decision": body.decision},
        )
        notifier.ticket_status(ticket_id, status)
        return {"ticket_id": ticket_id, "status": status}

    @app.post("/internal/worker/turn", dependencies=[require(OPS_INGEST)])
    def upload_turn(body: TurnIn) -> dict:
        try:
            data = base64.b64decode(body.data, validate=True)
        except (binascii.Error, ValueError):
            raise HTTPException(status_code=400, detail="invalid base64")
        try:
            sessions.open(body.session_id)
            turn = sessions.append_turn(
                body.session_id, body.turn_index, data, body.head_sha, body.branch
            )
        except TurnConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        return {"session_id": turn.session_id, "turn_index": turn.turn_index, "sha256": turn.sha256}

    # 源码部署时控制台就在 services/console；pip 安装或容器里用 SKEIN_CONSOLE_DIR 指过去
    console_root = (
        Path(console_dir) if console_dir else Path(__file__).resolve().parents[3] / "console"
    )
    dist_dir = console_root / "dist"
    if dist_dir.is_dir():
        app.mount(
            "/console",
            StaticFiles(directory=str(dist_dir), html=True),
            name="console",
        )

        @app.get("/")
        def root() -> RedirectResponse:
            return RedirectResponse(url="/console/")

    return app


__all__ = [
    "AgentChatIn",
    "IntakeIn",
    "LLMCallIn",
    "LoginIn",
    "ReviewIn",
    "TicketIn",
    "TurnIn",
    "create_app",
]
