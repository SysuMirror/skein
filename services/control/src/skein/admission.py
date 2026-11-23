"""程序层准入：确定性规则，不调用模型。

结果是三档：忽略、只记账、处理。处理分两种来源——明确点名，
或允许名单内的人回复了 Agent 自己的上一条。后者是推断，不扩大范围。

策略按来源存在 kv（admission:policies）。没有这一行，或字段缺失、
类型不对时，用 DEFAULT_POLICY 补齐。decide 仍是纯函数。
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from enum import Enum
from typing import TypedDict

from skein import db


class Verdict(str, Enum):
    IGNORE = "ignore"
    RECORD = "record"
    PROCESS = "process"


@dataclass(frozen=True)
class Event:
    source: str
    origin_id: str
    text: str = ""
    actor: str | None = None
    mentioned: bool = False
    in_allowlist: bool = False
    replies_to_agent: bool = False
    parent_id: str | None = None


@dataclass(frozen=True)
class SourcePolicy:
    accept: bool = True
    require_mention: bool = True
    record_unmentioned: bool = True
    min_text_chars: int = 2


@dataclass(frozen=True)
class Decision:
    verdict: Verdict
    reason: str


DEFAULT_POLICY = SourcePolicy()
POLICIES_KEY = "admission:policies"

_BOOL_FIELDS = ("accept", "require_mention", "record_unmentioned")
_POLICY_FIELDS = frozenset((*_BOOL_FIELDS, "min_text_chars"))
_SOURCE_NAME = re.compile(r"[a-z0-9:_-]{1,64}")
_MIN_TEXT_CHARS = 200


class PolicyDict(TypedDict):
    accept: bool
    require_mention: bool
    record_unmentioned: bool
    min_text_chars: int


class PoliciesDict(TypedDict):
    default: PolicyDict
    sources: dict[str, PolicyDict]


def decide(event: Event, policy: SourcePolicy = DEFAULT_POLICY) -> Decision:
    if not policy.accept:
        return Decision(Verdict.IGNORE, "source_disabled")

    text_len = len(event.text.strip())

    if event.mentioned:
        if text_len < policy.min_text_chars:
            return Decision(Verdict.RECORD, "mention_too_short")
        return Decision(Verdict.PROCESS, "explicit_mention")

    if event.replies_to_agent and event.in_allowlist:
        if text_len < policy.min_text_chars:
            return Decision(Verdict.RECORD, "reply_too_short")
        return Decision(Verdict.PROCESS, "allowed_followup")

    if event.in_allowlist:
        if not policy.require_mention:
            if text_len < policy.min_text_chars:
                return Decision(Verdict.RECORD, "allowed_too_short")
            return Decision(Verdict.PROCESS, "allowlisted_no_mention_required")
        if policy.record_unmentioned:
            return Decision(Verdict.RECORD, "allowlisted_unmentioned")
        return Decision(Verdict.IGNORE, "unmentioned_not_recorded")

    return Decision(Verdict.IGNORE, "not_allowlisted")


def policy_to_dict(policy: SourcePolicy) -> PolicyDict:
    return {
        "accept": policy.accept,
        "require_mention": policy.require_mention,
        "record_unmentioned": policy.record_unmentioned,
        "min_text_chars": policy.min_text_chars,
    }


def policy_from_dict(data: object) -> SourcePolicy:
    """缺字段、类型不对或超出 0–200 时，该字段用 DEFAULT_POLICY。非对象则整份用默认。"""
    accept = DEFAULT_POLICY.accept
    require_mention = DEFAULT_POLICY.require_mention
    record_unmentioned = DEFAULT_POLICY.record_unmentioned
    min_text_chars = DEFAULT_POLICY.min_text_chars
    if isinstance(data, dict):
        seen_accept = data.get("accept")
        if isinstance(seen_accept, bool):
            accept = seen_accept
        seen_mention = data.get("require_mention")
        if isinstance(seen_mention, bool):
            require_mention = seen_mention
        seen_record = data.get("record_unmentioned")
        if isinstance(seen_record, bool):
            record_unmentioned = seen_record
        seen_min = data.get("min_text_chars")
        if (
            isinstance(seen_min, int)
            and not isinstance(seen_min, bool)
            and 0 <= seen_min <= _MIN_TEXT_CHARS
        ):
            min_text_chars = seen_min
    return SourcePolicy(
        accept=accept,
        require_mention=require_mention,
        record_unmentioned=record_unmentioned,
        min_text_chars=min_text_chars,
    )


def load_policies(conn: sqlite3.Connection) -> PoliciesDict:
    """读 kv。没有行、JSON 非法或结构不对时回退默认，并补齐四个字段。"""
    from skein import store

    parsed: object = None
    raw = store.kv_get(conn, POLICIES_KEY)
    if raw:
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = None

    default = policy_to_dict(DEFAULT_POLICY)
    sources: dict[str, PolicyDict] = {}
    if isinstance(parsed, dict):
        seen_default = parsed.get("default")
        if isinstance(seen_default, dict):
            default = policy_to_dict(policy_from_dict(seen_default))
        seen_sources = parsed.get("sources")
        if isinstance(seen_sources, dict):
            for name, item in seen_sources.items():
                if not isinstance(name, str) or _SOURCE_NAME.fullmatch(name) is None:
                    continue
                if not isinstance(item, dict):
                    continue
                sources[name] = policy_to_dict(policy_from_dict(item))
    return {"default": default, "sources": sources}


def policy_for(conn: sqlite3.Connection, source: str) -> SourcePolicy:
    """该来源有一条配置就用它，否则用文档里的 default。"""
    policies = load_policies(conn)
    return policy_from_dict(policies["sources"].get(source, policies["default"]))


def _parse_policy(body: object, where: str) -> PolicyDict:
    if not isinstance(body, dict):
        raise TypeError(f"{where} must be an object")
    unknown = sorted(set(body) - _POLICY_FIELDS)
    if unknown:
        raise ValueError(f"{where} has unknown field: {unknown[0]}")
    filled = policy_to_dict(DEFAULT_POLICY)
    for name in _BOOL_FIELDS:
        if name not in body:
            continue
        value = body[name]
        if not isinstance(value, bool):
            raise TypeError(f"{where}.{name} must be a bool")
        filled[name] = value
    if "min_text_chars" in body:
        raw = body["min_text_chars"]
        if isinstance(raw, bool) or not isinstance(raw, int) or not 0 <= raw <= _MIN_TEXT_CHARS:
            raise ValueError(f"{where}.min_text_chars must be an integer from 0 to 200")
        filled["min_text_chars"] = raw
    return filled


def parse_policies(body: object) -> PoliciesDict:
    """校验 PUT 正文。缺的策略字段用默认值补齐；多出来的字段、错类型、非法来源名拒绝。"""
    if not isinstance(body, dict):
        raise TypeError("admission body must be an object")
    unknown = sorted(set(body) - {"default", "sources"})
    if unknown:
        raise ValueError(f"unknown field: {unknown[0]}")
    if "default" not in body or "sources" not in body:
        raise ValueError("default and sources are required")
    default = _parse_policy(body["default"], "default")
    raw_sources = body["sources"]
    if not isinstance(raw_sources, dict):
        raise TypeError("sources must be an object")
    sources: dict[str, PolicyDict] = {}
    for name, item in raw_sources.items():
        if not isinstance(name, str) or _SOURCE_NAME.fullmatch(name) is None:
            raise ValueError(f"invalid source name: {name!r}")
        sources[name] = _parse_policy(item, name)
    return {"default": default, "sources": sources}


def save_policies(conn: sqlite3.Connection, body: object) -> PoliciesDict:
    """校验后整份写入 kv，并记一条 human / admission.update 审计。"""
    from skein import store

    parsed = parse_policies(body)
    before = load_policies(conn)
    payload = json.dumps(parsed, ensure_ascii=False)
    conn.execute("BEGIN IMMEDIATE")
    try:
        store.kv_set(conn, POLICIES_KEY, payload)
        db.record_audit(
            conn,
            "human",
            "admission.update",
            {"before": before, "after": parsed},
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return parsed
