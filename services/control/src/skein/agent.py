"""Skein 控制台 Agent：管理员用自然语言查/操作雁阵。"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from dataclasses import asdict, dataclass
from typing import Any, Protocol

import httpx

from skein import db, llm_trace, runs, watchdog
from skein.summary import summary as build_summary
from skein.ui_api import ticket_rows

log = logging.getLogger("skein.agent")

DEFAULT_AGENT_MODEL = "deepseek-v4-flash-vision-exp"


class ModelError(RuntimeError):
    """模型网关没给出可用的回复（网络错误、非 2xx、响应结构不对）。"""


@dataclass(frozen=True)
class ChatResult:
    content: str
    prompt_tokens: int = 0
    completion_tokens: int = 0


class ChatModel(Protocol):
    """模型提供方的最小接口：一组消息 → 一段回复。

    换一家模型 = 实现这一个方法，然后把它作为 ``client`` 传给 :class:`Agent`
    （``create_app(model_client=...)``）。缺省是 :class:`OpenAICompatModel`。
    """

    async def complete(self, model: str, messages: list[dict]) -> ChatResult: ...


class OpenAICompatModel:
    """OpenAI 兼容的 ``/chat/completions`` 网关（任何提供这个协议的服务都能接）。"""

    def __init__(self, base: str, key: str, timeout: float = 90.0) -> None:
        self._base = base.rstrip("/")
        self._key = key
        self._timeout = timeout

    async def complete(self, model: str, messages: list[dict]) -> ChatResult:
        payload = {"model": model, "messages": messages, "temperature": 0.2, "max_tokens": 1200}
        try:
            async with httpx.AsyncClient(timeout=self._timeout, trust_env=False) as client:
                response = await client.post(
                    f"{self._base}/chat/completions",
                    headers={"Authorization": f"Bearer {self._key}"},
                    json=payload,
                )
            response.raise_for_status()
            data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise ModelError(str(exc)) from exc
        choices = data.get("choices") or []
        reply = choices[0].get("message") or {} if choices and isinstance(choices[0], dict) else {}
        usage = data.get("usage") or {}
        return ChatResult(
            content=str(reply.get("content") or ""),
            prompt_tokens=_tokens(usage, "prompt_tokens", "input_tokens"),
            completion_tokens=_tokens(usage, "completion_tokens", "output_tokens"),
        )

PLAN_SYSTEM = """你是雁阵（Skein）控制台 Agent 的规划器，服务对象是维护者/管理员。
规则：
- 只读为主；只有管理员明确要求时才选择 dispatch/cancel。
- 不自动合入、不自动验收、不删资源。
- 不确定就不调用工具。

可用工具：
- summary()：雁阵概览
- list_tickets(status?, limit?)：工单列表
- ticket_detail(ticket_id)：工单详情（ID 或 SK-xxxx）
- list_nodes()：执行节点
- list_runs(limit?)：定时作业运行
- list_signals()：Watchdog 信号
- llm_sessions(limit?)：会话与模型调用汇总
- dispatch_ticket(ticket_id, platform?)：派工（仅明确要求）；ticket_id 可传编号如 SK-0003
- cancel_ticket(ticket_id)：取消派工（仅明确要求）；ticket_id 可传编号如 SK-0003

只输出一个 JSON 对象，不要 Markdown、不要解释、不要输出工具结果：
{"tools":[{"name":"工具名","args":{...}}]}
不需要工具就输出：{"tools":[]}
"""

FINAL_SYSTEM = """你是雁阵（Skein）控制台 Agent，服务对象是维护者/管理员。
根据给出的工具结果，用中文简洁回答：先结论后依据，不编造，不确定就说不知道。
不要输出 JSON、工具调用或原始工具结果；不要假装自己改了代码。
"""

MAX_TOOL_RESULT = 8000


def _clip(value: Any, limit: int = MAX_TOOL_RESULT) -> str:
    if value is None:
        return ""
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    return text[:limit]


def _tokens(usage: Any, *names: str) -> int:
    if not isinstance(usage, dict):
        return 0
    for name in names:
        try:
            value = int(usage.get(name) or 0)
        except (TypeError, ValueError):
            continue
        if value > 0:
            return value
    return 0


def _parse_action(text: str) -> dict | None:
    raw = text.strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.lower().startswith("json"):
            raw = raw[4:].lstrip()
    try:
        data = json.loads(raw)
    except ValueError:
        start, end = raw.find("{"), raw.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            data = json.loads(raw[start : end + 1])
        except ValueError:
            return None
    return data if isinstance(data, dict) else None


def _parse_plan(text: str) -> list[dict]:
    data = _parse_action(text) or {}
    items = data.get("tools")
    if isinstance(items, list):
        return [item for item in items if isinstance(item, dict)]
    if data.get("tool"):
        return [{"name": data.get("tool"), "args": data.get("args") or {}}]
    return []


class Agent:
    def __init__(
        self,
        conn: sqlite3.Connection,
        base: str,
        key: str,
        model: str,
        hub: Any,
        timeout: float = 90.0,
        client: ChatModel | None = None,
    ) -> None:
        self._conn = conn
        self._model = model
        self._hub = hub
        # 模型出口是可替换的：没给就用 OpenAI 兼容网关（需要 base + key）
        self._client = client or (
            OpenAICompatModel(base, key, timeout) if base and key else None
        )

    # ---------------- 对话 ----------------

    async def chat(self, principal: Any, thread_id: str, message: str) -> dict:
        history = self._history(thread_id)
        self._save(thread_id, "user", message)
        history_messages = [
            {"role": item["role"], "content": item["content"]} for item in history
        ]
        plan_system = PLAN_SYSTEM
        platforms = self._platform_hint()
        if platforms:
            plan_system += (
                "\n当前可派平台：" + "、".join(platforms)
                + "。dispatch_ticket 必须带 platform 且只能取以上值；"
                "用户没说派哪个平台时不要猜，直接用 FINAL 回复追问（列出上面的平台）。"
            )
        plan_messages = [
            {"role": "system", "content": plan_system},
            *history_messages,
            {"role": "user", "content": message},
        ]
        plan = await self._llm(principal, thread_id, plan_messages)
        actions = _parse_plan(str(plan.get("content") or "")) if plan else []
        tools_used: list[str] = []
        results: list[dict] = []
        for action in actions[:4]:
            name = str(action.get("name") or action.get("tool") or "")
            args = action.get("args") or {}
            if not isinstance(args, dict):
                args = {}
            result = await self._tool(name, args, principal)
            tools_used.append(name)
            results.append({"tool": name, "result": result})

        final_messages = [
            {"role": "system", "content": FINAL_SYSTEM},
            *history_messages,
            {"role": "user", "content": message},
        ]
        if results:
            final_messages.append(
                {
                    "role": "user",
                    "content": "工具结果（已执行）：\n"
                    + _clip(json.dumps(results, ensure_ascii=False), 12000),
                }
            )
        else:
            final_messages.append(
                {
                    "role": "user",
                    "content": "本轮没有调用工具；如果问题需要实时数据，请说明需要什么信息。",
                }
            )
        final = await self._llm(principal, thread_id, final_messages)
        if final is None:
            reply = "模型调用失败，稍后再试。"
        else:
            reply = str(final.get("content") or "").strip() or "我没查到可总结的内容。"
        self._save(thread_id, "assistant", reply)
        return {"reply": reply, "tools": tools_used}

    async def _llm(
        self, principal: Any, thread_id: str, messages: list[dict]
    ) -> dict | None:
        """问一次模型，并把这次调用记进留痕（成功失败都记）。"""
        started = time.monotonic()
        trace = {
            "session_id": thread_id,
            "actor": str(getattr(principal, "sub", "") or ""),
            "source": "skein-agent",
            "model": self._model,
            "prompt": _clip(json.dumps(messages, ensure_ascii=False)),
            "latency_ms": 0,
        }
        try:
            if self._client is None:
                raise ModelError("no model configured")
            result = await self._client.complete(self._model, messages)
        except ModelError as exc:
            trace["latency_ms"] = int((time.monotonic() - started) * 1000)
            llm_trace.record(self._conn, **trace, status="error", error=str(exc))
            log.warning("agent llm failed: %s", exc)
            return None
        trace["latency_ms"] = int((time.monotonic() - started) * 1000)
        llm_trace.record(
            self._conn,
            **trace,
            response=_clip(result.content),
            prompt_tokens=result.prompt_tokens,
            completion_tokens=result.completion_tokens,
            status="ok",
        )
        return {"role": "assistant", "content": result.content}

    def _platform_hint(self) -> list[str]:
        """可派平台列表（来自 hub 的仓库映射 ∪ 在线节点）。取不到就空着，不挡对话。"""
        try:
            return list(self._hub.available_platforms())
        except Exception:  # noqa: BLE001 - 测试桩/旧 hub 没有该方法
            try:
                from skein import scheduler

                return scheduler.online_platforms(self._conn)
            except Exception:  # noqa: BLE001
                return []

    def _resolve_ticket_id(self, ref: str) -> str | None:
        """解析工单引用 → 内部 id。先按 id 精确查，再按 code（忽略大小写、去前导 #）。"""
        value = str(ref or "").strip().lstrip("#").strip()
        if not value:
            return None
        row = self._conn.execute("SELECT id FROM tickets WHERE id = ?", (value,)).fetchone()
        if row is not None:
            return str(row["id"])
        row = self._conn.execute(
            "SELECT id FROM tickets WHERE upper(code) = upper(?)"
            " ORDER BY created_at DESC LIMIT 1",
            (value,),
        ).fetchone()
        return str(row["id"]) if row else None

    # ---------------- 工具 ----------------

    async def _tool(self, name: str, args: dict, principal: Any) -> Any:
        db.record_audit(
            self._conn,
            str(getattr(principal, "label", "") or "admin"),
            "agent.tool",
            {"tool": name, "args": args},
        )
        try:
            if name == "summary":
                return build_summary(self._conn)
            if name == "list_tickets":
                return ticket_rows(
                    self._conn,
                    status=str(args.get("status") or "") or None,
                    limit=int(args.get("limit") or 50),
                )
            if name == "ticket_detail":
                ref = str(args.get("ticket_id") or "")
                ticket_id = self._resolve_ticket_id(ref)
                if ticket_id is None:
                    return {"error": f"ticket_not_found: {ref}"}
                return self._ticket_detail(ticket_id)
            if name == "list_nodes":
                return [
                    dict(row)
                    for row in self._conn.execute(
                        "SELECT id, name, host, platforms, status, max_workers, last_seen"
                        " FROM nodes ORDER BY id"
                    )
                ]
            if name == "list_runs":
                return [asdict(item) for item in runs.recent(self._conn, int(args.get("limit") or 20))]
            if name == "list_signals":
                return watchdog.signals(self._conn)
            if name == "llm_sessions":
                return llm_trace.sessions(self._conn, limit=int(args.get("limit") or 50))
            if name == "dispatch_ticket":
                ref = str(args.get("ticket_id") or "")
                ticket_id = self._resolve_ticket_id(ref)
                if ticket_id is None:
                    return {"error": f"ticket_not_found: {ref}"}
                platform = str(args.get("platform") or "") or None
                return await self._hub.dispatch_ticket(ticket_id, platform)
            if name == "cancel_ticket":
                ref = str(args.get("ticket_id") or "")
                ticket_id = self._resolve_ticket_id(ref)
                if ticket_id is None:
                    return {"error": f"ticket_not_found: {ref}"}
                return {"cancelled": await self._hub.cancel_ticket(ticket_id)}
            return {"error": f"unknown tool: {name}"}
        except Exception as exc:  # noqa: BLE001 —— 工具错误交回模型
            return {"error": f"{type(exc).__name__}: {exc}"}

    def _ticket_detail(self, ticket_id: str) -> dict:
        row = self._conn.execute(
            "SELECT id, code, thread_id, title, summary, status, dedup_key,"
            " origin, origin_id, requester, public_status, created_at, updated_at FROM tickets"
            " WHERE id = ? OR code = ? ORDER BY created_at DESC LIMIT 1",
            (ticket_id, ticket_id),
        ).fetchone()
        if row is None:
            return {"error": "ticket_not_found"}
        detail = dict(row)
        detail["assignments"] = [
            dict(item)
            for item in self._conn.execute(
                "SELECT worker_id, state, result, updated_at FROM assignments"
                " WHERE ticket_id = ? ORDER BY updated_at DESC",
                (row["id"],),
            )
        ]
        detail["dispatches"] = [
            dict(item)
            for item in self._conn.execute(
                "SELECT id, node_id, worker_id, state, detail, updated_at FROM dispatches"
                " WHERE ticket_id = ? ORDER BY updated_at DESC",
                (row["id"],),
            )
        ]
        return detail

    # ---------------- 历史 ----------------

    def _history(self, thread_id: str) -> list[dict]:
        return [
            dict(row)
            for row in self._conn.execute(
                "SELECT role, content, created_at FROM agent_messages"
                " WHERE thread_id = ? ORDER BY rowid DESC LIMIT 20",
                (thread_id,),
            )
        ][::-1]

    def _save(self, thread_id: str, role: str, content: str) -> None:
        self._conn.execute(
            "INSERT INTO agent_messages (id, thread_id, role, content, created_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (db.new_id(), thread_id, role, content[:16000], time.time()),
        )
