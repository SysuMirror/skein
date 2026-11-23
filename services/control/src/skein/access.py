"""权限矩阵：谁能做什么，只在这一处定义。

主体有三种，各走一条规则：

- **登录的人**（``kind="session"``）看角色。角色是全序的 member < admin < owner，
  每个权限只写明「拥有它的最低角色」，高角色自然包含低角色的全部权限。
- **机器**（``kind="machine"``，请求头里带主令牌 ``SKEIN_TOKEN``）是控制面的自动化身份：
  内部接口的全部权限都有，唯独不能代替人做「必须留痕到某个人」的事。
- **助手**（``kind="assistant"``，持助手凭证）只能走公共 API，能力是固定的一组。

要改权限只改这张表。``docs/authentication.md`` 里的矩阵由 :func:`matrix_table` 生成，
测试会核对两者一致。
"""

from __future__ import annotations

OWNER = "owner"
ADMIN = "admin"
MEMBER = "member"
ROLES = (OWNER, ADMIN, MEMBER)
_RANK = {MEMBER: 0, ADMIN: 1, OWNER: 2}

# 主体的三种身份
MACHINE = "machine"
SESSION = "session"
ASSISTANT = "assistant"

# ---- 控制台与内部接口（/internal/*）的权限 ----
READ = "read"
TICKET_FILE = "ticket.file"
TICKET_OPERATE = "ticket.operate"
OPS_INGEST = "ops.ingest"
AGENT_CHAT = "agent.chat"
ASSISTANT_MANAGE = "assistant.manage"
USER_READ = "user.read"
USER_MANAGE = "user.manage"

# ---- 公共 API（/api/v1/*）的权限 ----
API_TICKET_CREATE = "api.ticket.create"
API_TICKET_READ = "api.ticket.read"
API_TRACE_WRITE = "api.trace.write"

# 权限 → 拥有它的最低角色。顺序即文档里的顺序。
ROLE_GRANTS: dict[str, str] = {
    READ: MEMBER,
    TICKET_FILE: MEMBER,
    TICKET_OPERATE: ADMIN,
    OPS_INGEST: ADMIN,
    AGENT_CHAT: ADMIN,
    ASSISTANT_MANAGE: ADMIN,
    USER_READ: ADMIN,
    USER_MANAGE: OWNER,
}
# 必须由登录的人来做的事：机器拿着主令牌也不行
HUMAN_ONLY = frozenset({AGENT_CHAT})
# 助手凭证的固定能力；api.trace.write 另需部署方显式打开留痕上报
ASSISTANT_GRANTS = frozenset({API_TICKET_CREATE, API_TICKET_READ, API_TRACE_WRITE})

# 文档用的一句话说明（与 ROLE_GRANTS 的键一一对应）
DESCRIPTIONS = {
    READ: "Read everything the console shows: tickets, threads, events, sessions, nodes, signals, model-call traces",
    TICKET_FILE: "File a ticket (without dispatching it)",
    TICKET_OPERATE: "Dispatch, review or cancel a ticket; submit through `/internal/intake`",
    OPS_INGEST: "Upload worker transcript turns and model-call records",
    AGENT_CHAT: "Talk to the console Agent (its actions are attributed to a person)",
    ASSISTANT_MANAGE: "Issue, rotate and revoke assistant credentials",
    USER_READ: "List users and their roles",
    USER_MANAGE: "Create users, set roles and passwords, disable accounts, link login identities",
}


class NotAllowed(Exception):
    """主体没有这项权限。"""


def rank(role: str | None) -> int:
    return _RANK.get(role or "", -1)


def at_least(role: str | None, minimum: str) -> bool:
    return rank(role) >= _RANK[minimum]


def allows(kind: str, role: str | None, permission: str) -> bool:
    """矩阵本体：主体种类 + 角色 → 能不能做。"""
    if kind == MACHINE:
        return permission in ROLE_GRANTS and permission not in HUMAN_ONLY
    if kind == ASSISTANT:
        return permission in ASSISTANT_GRANTS
    if kind == SESSION:
        minimum = ROLE_GRANTS.get(permission)
        return minimum is not None and at_least(role, minimum)
    return False


def check(kind: str, role: str | None, permission: str) -> None:
    if not allows(kind, role, permission):
        raise NotAllowed(permission)


def permissions_of(kind: str, role: str | None) -> list[str]:
    """某个主体拥有的全部权限（按定义顺序），给控制台决定显隐用。"""
    if kind == ASSISTANT:
        return sorted(ASSISTANT_GRANTS)
    return [name for name in ROLE_GRANTS if allows(kind, role, name)]


def min_role(permission: str) -> str:
    return ROLE_GRANTS[permission]


def matrix_table() -> str:
    """Markdown 表：行是权限，列是各角色与机器令牌。"""
    mark = {True: "yes", False: "no"}
    lines = [
        "| Permission | member | admin | owner | machine token | What it covers |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for name in ROLE_GRANTS:
        cells = [mark[allows(SESSION, role, name)] for role in (MEMBER, ADMIN, OWNER)]
        machine = "signed-in only" if name in HUMAN_ONLY else "yes"
        lines.append(f"| `{name}` | {' | '.join(cells)} | {machine} | {DESCRIPTIONS[name]} |")
    return "\n".join(lines)
