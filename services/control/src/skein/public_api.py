"""公共 API（``/api/v1``）：外部助手接入雁阵的正门。

助手拿自己的凭证（``Authorization: Bearer skein_at_…``）能做的只有三件事：提交一个请求、
按来源系统的唯一编号幂等重试、查自己提交的工单。主令牌在这里无效，助手凭证在
``/internal/*`` 上也无效——两边的身份不通用。

约定（面向接入方的说明见 docs/assistants.md）：

- 幂等：同一把凭证下 ``(source, external_id)`` 唯一标识一个请求。重试返回原来那张工单
  （200，``created: false``）；同一个编号换了标题是调用方的错误，返回 409。
- 限速：每把凭证每分钟 ``rate_limit`` 次（滑动窗口，进程内计）。超了 429，带 ``Retry-After``。
- 大小：请求体最多 64 KiB；字段各有长度上限，超了 413 / 422。
- 错误体统一是 ``{"detail": {"code": "...", "message": "..."}}``，code 是稳定的机器可读值。
"""

from __future__ import annotations

import logging
import re
import sqlite3
import time
from typing import Any, Literal, NamedTuple

from fastapi import APIRouter, Depends, FastAPI, Request, Response
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator

from skein import db, llm_trace, store
from skein.access import API_TICKET_CREATE, API_TICKET_READ, API_TRACE_WRITE, ASSISTANT
from skein.assistants import Assistant, AssistantStore
from skein.auth import Principal
from skein.middlewares import BodyLimit
from skein.ratelimit import RateLimiter

log = logging.getLogger("skein.public_api")

PREFIX = "/api/v1"
# 可选集成（聊天代理）占着 /api/v1/agent/*：历史路径，公共 API 不碰它
EXEMPT_PREFIX = "/api/v1/agent/"
MAX_BODY_BYTES = 64 * 1024
DEFAULT_LIST_LIMIT = 50
MAX_LIST_LIMIT = 200
DEFAULT_RATE_LIMIT = 60
TRACE_RATE_PER_MINUTE = 600

_SLUG = re.compile(r"[a-z0-9][a-z0-9_.:-]{0,63}")


class ApiError(Exception):
    """公共 API 的错误：状态码 + 稳定的机器可读 code。"""

    def __init__(
        self, status: int, code: str, message: str, headers: dict[str, str] | None = None
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.headers = headers or {}


def bearer(request: Request) -> str:
    value = request.headers.get("authorization") or ""
    scheme, _, credential = value.strip().partition(" ")
    return credential.strip() if scheme.lower() == "bearer" else ""


def error_body(status: int, code: str, message: str, **extra: Any) -> JSONResponse:
    detail: dict[str, Any] = {"code": code, "message": message}
    detail.update(extra)
    return JSONResponse(status_code=status, content={"detail": detail})


def rate_headers(limit: int, retry_after: float) -> dict[str, str]:
    """限速响应头：Retry-After 给人看，X-RateLimit-* 给客户端程序看。"""
    return {
        "Retry-After": str(int(retry_after) + 1),
        "X-RateLimit-Limit": str(limit),
        "X-RateLimit-Remaining": "0",
        "X-RateLimit-Reset": str(int(time.time() + retry_after) + 1),
    }


class TicketCreateIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=20000)
    # 来源系统（如 forum、chat、ci）与请求在它那里的唯一编号：两者合起来是幂等键
    source: str = Field(min_length=1, max_length=64)
    external_id: str = Field(min_length=1, max_length=256)
    # 提出人在来源系统里的编号。雁阵不验证这个人，由助手担保
    requester: str = Field(default="", max_length=128)
    severity: Literal["low", "normal", "high", "critical"] = "normal"
    category: str = Field(default="", max_length=32)
    labels: list[str] = Field(default_factory=list, max_length=20)
    links: list[str] = Field(default_factory=list, max_length=10)

    @field_validator("source")
    @classmethod
    def _source(cls, value: str) -> str:
        slug = value.strip().lower()
        if not _SLUG.fullmatch(slug):
            raise ValueError(
                "lowercase letters, digits and _ . : - only; must start with a letter or digit"
            )
        return slug

    @field_validator("category")
    @classmethod
    def _category(cls, value: str) -> str:
        slug = value.strip().lower()
        if slug and not _SLUG.fullmatch(slug):
            raise ValueError("lowercase letters, digits and _ . : - only")
        return slug

    @field_validator("labels")
    @classmethod
    def _labels(cls, values: list[str]) -> list[str]:
        cleaned: list[str] = []
        for value in values:
            item = value.strip()[:64]
            if item and item not in cleaned:
                cleaned.append(item)
        return cleaned

    @field_validator("links")
    @classmethod
    def _links(cls, values: list[str]) -> list[str]:
        for value in values:
            if not value.startswith(("http://", "https://")) or len(value) > 2048:
                raise ValueError("links must be http(s) URLs of at most 2048 characters")
        return values


class TraceIn(BaseModel):
    session_id: str = Field(default="", max_length=128)
    source: str = Field(default="", max_length=32)
    model: str = Field(default="", max_length=128)
    prompt: str = Field(default="", max_length=16000)
    response: str = Field(default="", max_length=16000)
    prompt_tokens: int = Field(default=0, ge=0)
    completion_tokens: int = Field(default=0, ge=0)
    latency_ms: int = Field(default=0, ge=0)
    status: str = Field(default="ok", max_length=16)
    error: str = Field(default="", max_length=16000)


def dedup_key(assistant_id: str, source: str, external_id: str) -> str:
    """幂等键：凭证 + 来源 + 来源系统里的编号。放在 tickets.dedup_key 上，靠唯一索引兜底。"""
    return f"assist:{assistant_id}:{source}:{external_id}"


def _summary(body: TicketCreateIn) -> str:
    """描述正文 + 一行结构化备注。写法与 /internal/intake 一致，在控制台里直接可读。"""
    fields = [
        f"source={body.source}",
        f"external_id={body.external_id}",
        f"requester={body.requester or '-'}",
        f"category={body.category or '-'}",
        f"severity={body.severity}",
    ]
    if body.labels:
        fields.append("labels=" + ",".join(body.labels))
    if body.links:
        fields.append("links=" + " ".join(body.links))
    return body.description.strip() + "\n\n[" + " ".join(fields) + "]"


_TICKET_FIELDS = (
    "id, code, title, status, public_status, origin, origin_id, requester, created_at, updated_at"
)


def _view(row: sqlite3.Row, public_url: str) -> dict:
    return {
        "id": row["id"],
        "code": row["code"],
        "title": row["title"],
        "status": row["status"],
        "public_status": row["public_status"],
        "source": row["origin"],
        "external_id": row["origin_id"],
        "requester": row["requester"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "url": f"{public_url}/console/#/tickets/{row['id']}" if public_url else None,
    }


def install(
    app: FastAPI,
    conn: sqlite3.Connection,
    assistants: AssistantStore,
    *,
    rate_limit: int = DEFAULT_RATE_LIMIT,
    trace_enabled: bool = False,
    public_url: str = "",
) -> APIRouter:
    """挂上公共 API。返回路由器，便于测试检查路径。"""
    public_url = public_url.rstrip("/")
    limiter = RateLimiter(rate_limit, 60)
    trace_limiter = RateLimiter(TRACE_RATE_PER_MINUTE, 60)
    scopes = [API_TICKET_CREATE, API_TICKET_READ] + ([API_TRACE_WRITE] if trace_enabled else [])

    @app.exception_handler(ApiError)
    async def _api_error(_: Request, error: ApiError) -> JSONResponse:
        return JSONResponse(
            status_code=error.status,
            content={"detail": {"code": error.code, "message": error.message}},
            headers=error.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, error: RequestValidationError) -> JSONResponse:
        """公共 API 的参数错误统一成 {code, message, fields}；其他路径保持 FastAPI 默认格式。"""
        path = request.url.path
        if not path.startswith(PREFIX + "/") or path.startswith(EXEMPT_PREFIX):
            return await request_validation_exception_handler(request, error)
        fields = [
            {"field": ".".join(str(part) for part in item["loc"]), "message": item["msg"]}
            for item in error.errors()
        ]
        return error_body(
            422,
            "invalid_request",
            "the request body is missing or has invalid fields",
            fields=fields,
        )

    class Identified(NamedTuple):
        principal: Principal
        assistant: Assistant
        limit: int
        remaining: int
        reset_at: int

    async def authenticate(request: Request) -> Identified:
        """认凭证 → 限速 → 主体。只认 Authorization: Bearer，不看 Cookie，也不看 X-Skein-Token。"""
        assistant = assistants.authenticate(bearer(request))
        if assistant is None:
            raise ApiError(401, "invalid_key", "missing, unknown or revoked assistant key")
        allowed, retry_after = limiter.hit(assistant.id)
        if not allowed:
            raise ApiError(
                429,
                "rate_limited",
                f"at most {rate_limit} requests per minute per credential",
                headers=rate_headers(rate_limit, retry_after),
            )
        remaining, reset_in = limiter.snapshot(assistant.id)
        return Identified(
            Principal(sub=assistant.id, name=assistant.name, kind=ASSISTANT, provider="assistants"),
            assistant,
            rate_limit,
            remaining,
            int(time.time() + reset_in) + 1,
        )

    def _stamp(who: Identified, response: Response) -> None:
        response.headers["X-RateLimit-Limit"] = str(who.limit)
        response.headers["X-RateLimit-Remaining"] = str(who.remaining)
        response.headers["X-RateLimit-Reset"] = str(who.reset_at)

    router = APIRouter(prefix=PREFIX)

    @router.get("/me")
    def me(response: Response, who: Identified = Depends(authenticate)) -> dict:
        _stamp(who, response)
        return {"assistant": who.assistant.as_dict(), "scopes": scopes, "api": "v1"}

    @router.post("/tickets")
    def create_ticket(
        response: Response, body: TicketCreateIn, who: Identified = Depends(authenticate)
    ) -> dict:
        """提交一个请求。201 = 新建；200 = 幂等重试，返回原来那张工单。"""
        _stamp(who, response)
        assistant = who.assistant
        title = body.title.strip()
        if not title:
            raise ApiError(422, "invalid_request", "title must not be blank")
        dedup = dedup_key(assistant.id, body.source, body.external_id)
        ticket_id = store.find_ticket_by_dedup(conn, dedup)
        if ticket_id is not None:
            return _replay(conn, ticket_id, title, public_url, response)
        try:
            created = store.create_ticket(conn, title, None, dedup, _summary(body))
        except sqlite3.IntegrityError:
            # 同一个请求的两份并发提交：后到的那份按重试处理
            created = None
            ticket_id = store.find_ticket_by_dedup(conn, dedup)
        if created is not None:
            conn.execute(
                "UPDATE tickets SET origin = ?, origin_id = ?, requester = ?,"
                " submitted_by = ?, public_status = 'received', updated_at = ?"
                " WHERE id = ?",
                (
                    body.source,
                    body.external_id,
                    body.requester,
                    assistant.id,
                    time.time(),
                    created,
                ),
            )
            db.record_audit(
                conn,
                assistant.label,
                "ticket.api_create",
                {
                    "ticket_id": created,
                    "source": body.source,
                    "external_id": body.external_id,
                    "requester": body.requester,
                },
            )
            response.status_code = 201
            return {"created": True, "ticket": _by_id(conn, created, public_url)}
        assert ticket_id is not None
        return _replay(conn, ticket_id, title, public_url, response)

    @router.get("/tickets")
    def list_tickets(
        response: Response,
        status: str = "",
        limit: int = DEFAULT_LIST_LIMIT,
        who: Identified = Depends(authenticate),
    ) -> dict:
        """这个助手自己提交的工单，新的在前。``status`` 可按雁阵状态过滤。"""
        _stamp(who, response)
        sql = f"SELECT {_TICKET_FIELDS} FROM tickets WHERE submitted_by = ?"
        params: list[Any] = [who.assistant.id]
        if status:
            sql += " AND status = ?"
            params.append(status)
        sql += " ORDER BY created_at DESC, id LIMIT ?"
        params.append(max(1, min(limit, MAX_LIST_LIMIT)))
        return {"tickets": [_view(row, public_url) for row in conn.execute(sql, params)]}

    @router.get("/tickets/{ref}")
    def get_ticket(
        response: Response, ref: str, who: Identified = Depends(authenticate)
    ) -> dict:
        """按 id 或编号查一张自己的工单。"""
        _stamp(who, response)
        row = conn.execute(
            f"SELECT {_TICKET_FIELDS} FROM tickets"
            " WHERE submitted_by = ? AND (id = ? OR upper(code) = upper(?))",
            (who.assistant.id, ref, ref.lstrip("#")),
        ).fetchone()
        if row is None:
            # 「别人的」与「不存在的」对助手来说没有区别：都是查不到
            raise ApiError(404, "not_found", "no such ticket among the ones you submitted")
        return {"ticket": _view(row, public_url)}

    if trace_enabled:

        @router.post("/llm/calls", status_code=201)
        def record_trace(
            response: Response, body: TraceIn, who: Identified = Depends(authenticate)
        ) -> dict:
            """回报一次模型调用，进留痕（需要部署方打开 SKEIN_API_TRACE）。"""
            _stamp(who, response)
            allowed, retry_after = trace_limiter.hit(who.assistant.id)
            if not allowed:
                raise ApiError(
                    429,
                    "rate_limited",
                    f"at most {TRACE_RATE_PER_MINUTE} trace records per minute per credential",
                    headers=rate_headers(TRACE_RATE_PER_MINUTE, retry_after),
                )
            call_id = llm_trace.record(
                conn,
                session_id=body.session_id,
                # 是谁报的由凭证决定；调用方给的 source 只是它自己的细分
                actor=who.assistant.label,
                source=(body.source or who.assistant.name)[:32],
                model=body.model,
                prompt=body.prompt,
                response=body.response,
                prompt_tokens=body.prompt_tokens,
                completion_tokens=body.completion_tokens,
                latency_ms=body.latency_ms,
                status=body.status,
                error=body.error,
            )
            return {"ok": True, "id": call_id}

    app.include_router(router)
    app.add_middleware(BodyLimit, limit=MAX_BODY_BYTES, under=PREFIX + "/", exempt=EXEMPT_PREFIX)
    return router


def _by_id(conn: sqlite3.Connection, ticket_id: str, public_url: str) -> dict:
    row = conn.execute(
        f"SELECT {_TICKET_FIELDS} FROM tickets WHERE id = ?", (ticket_id,)
    ).fetchone()
    assert row is not None
    return _view(row, public_url)


def _replay(
    conn: sqlite3.Connection, ticket_id: str, title: str, public_url: str, response: Response
) -> dict:
    row = conn.execute("SELECT title FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
    assert row is not None
    original = str(row["title"]).strip()
    if original != title:
        raise ApiError(
            409,
            "idempotency_conflict",
            "this (source, external_id) was already used for a different request"
            f" ({original!r}); use a new external_id",
        )
    response.status_code = 200
    return {"created": False, "ticket": _by_id(conn, ticket_id, public_url)}


__all__ = [
    "MAX_BODY_BYTES",
    "PREFIX",
    "ApiError",
    "TicketCreateIn",
    "bearer",
    "dedup_key",
    "install",
]
