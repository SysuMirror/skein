"""登录鉴权：与登录协议无关的核心。

谁能进来分两步，分开是为了让「换一种登录方式」不碰授权：

1. **认人**（:class:`IdentityProvider` 只做这一步）：把请求里的 Cookie 交给 provider，
   换回一个 :class:`Principal`——「这是谁」，外加上游报告的属性。
2. **放行**（:class:`SessionVerifier` + ``skein.access``）：身份对应雁阵里的哪个人、
   什么角色，由 :class:`~skein.accounts.Directory` 查账号表决定；某个请求能不能做，
   只看权限矩阵。

请求头里带主令牌 ``SKEIN_TOKEN`` 的是**机器**：不走 provider，直接得到一个机器主体。
机器拥有内部接口的全部权限，但不能代替人做必须留痕到人的事。

登录方式有三种，由配置决定（见 ``skein.auth_providers``）：不配任何登录系统是
**令牌模式**（token），配了本地账号是 **local**，配了上游登录系统是 **sso**。
"""

from __future__ import annotations

import hmac
import threading
import time
from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Protocol

from skein import access
from skein.access import ASSISTANT, MACHINE, SESSION

VERIFY_TTL = 60.0
# 上游不可达时短暂记住失败：既不当成未登录，也不让每个请求都去撞一个挂掉的上游
UNAVAILABLE_TTL = 5.0
CACHE_MAX = 512

# 入口级别：console=控制台与内部接口（要雁阵账号）；user=只要认出是谁的入口（如聊天代理的身份头）
LEVEL_CONSOLE = "console"
LEVEL_USER = "user"
# 历史别名：早期部署把面向用户的入口叫 market，外部调用方可能还在用
LEVEL_MARKET = "market"
USER_LEVELS = (LEVEL_USER, LEVEL_MARKET)
# 旧名字，保留给既有调用方
LEVEL_ADMIN = LEVEL_CONSOLE

CURRENT_PRINCIPAL: ContextVar[Principal | None] = ContextVar("skein_principal", default=None)

MACHINE_SUB = "skein:machine"


class AuthUnavailable(Exception):
    """登录上游不可达 / 限流 / 5xx：不能当成未登录。"""


class AuthRejected(Exception):
    """会话或凭据确实无效，或主体不满足要求：可以放心拒绝。"""

    def __init__(self, code: str, status: int = 401) -> None:
        super().__init__(code)
        self.code = code
        self.status = status


def token_matches(presented: object, expected: str) -> bool:
    """恒定时间比较令牌。编码后比较，非 ASCII 输入也不会抛异常。"""
    if not isinstance(presented, str):
        return False
    return hmac.compare_digest(presented.encode(), (expected or "").encode())


@dataclass(frozen=True)
class Principal:
    """一个被认出来的主体。

    前半段字段是 provider 报告的身份与属性（``internal`` / ``admin`` / ``super_admin``）：
    它们用来说明「上游怎么看这个人」，只用于两处——第一次见到时映射角色、给聊天上游
    签身份头。**授权不看它们**，只看 ``kind`` + ``role``。
    """

    sub: str
    name: str = ""
    email: str = ""
    internal: bool = False
    picture: str = ""
    admin: bool = False
    super_admin: bool = False
    roles: tuple[str, ...] = ()
    # 授权信息：由雁阵的账号模型给出
    kind: str = SESSION
    role: str | None = None
    provider: str = ""

    @property
    def label(self) -> str:
        return self.name or self.email or self.sub

    @property
    def is_machine(self) -> bool:
        return self.kind == MACHINE

    @property
    def is_signed_in(self) -> bool:
        return self.kind == SESSION

    def allows(self, permission: str) -> bool:
        return access.allows(self.kind, self.role, permission)

    def permissions(self) -> list[str]:
        return access.permissions_of(self.kind, self.role)

    def as_dict(self) -> dict[str, Any]:
        return {
            "sub": self.sub,
            "name": self.name,
            "email": self.email,
            "internal": self.internal,
            "picture": self.picture,
            "admin": self.admin,
            "super_admin": self.super_admin,
            "roles": list(self.roles),
            "role": self.role,
        }

    @classmethod
    def machine(cls) -> Principal:
        """请求头里带主令牌的调用方：控制面的自动化身份。"""
        return cls(sub=MACHINE_SUB, name="machine", kind=MACHINE, role="machine")


def _flag(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes")
    return bool(value)


def principal_from_payload(payload: Any) -> Principal | None:
    """从 ``{"user": {...}}`` 形态的响应里取身份；没有 user.sub 返回 None。

    认 ``is_admin`` / ``admin`` / roles 里的 ``admin``，以及对应的 super_admin。
    """
    user = payload.get("user") if isinstance(payload, dict) else None
    if not isinstance(user, dict) or not user.get("sub"):
        return None
    raw_roles = user.get("roles") or []
    roles = tuple(str(role) for role in raw_roles) if isinstance(raw_roles, list) else ()
    super_admin = _flag(user.get("is_super_admin")) or "super_admin" in roles
    admin = (
        _flag(user.get("is_admin"))
        or _flag(user.get("admin"))
        or "admin" in roles
        or super_admin
    )
    return Principal(
        sub=str(user["sub"]),
        name=str(user.get("name") or ""),
        email=str(user.get("email") or ""),
        internal=_flag(user.get("internal")),
        picture=str(user.get("picture") or ""),
        admin=admin,
        super_admin=super_admin,
        roles=roles,
    )


class IdentityProvider(Protocol):
    """一种登录协议。控制面只依赖这几个成员。

    ``login``   标识 + 口令换 (Principal, 会话信息)。Principal 是 provider 眼里的身份
                （``sub`` 是 provider 侧的主体号）。会话信息是给浏览器种 Cookie 用的
                字典，至少含 ``session``；可带 ``cookie_name`` / ``cookie_domain`` /
                ``cookie_max_age`` 覆盖默认值。
    ``verify``  会话值 → Principal；无效返回 None。
    ``revoke``  注销会话。登出时要「即使上游不可达也清本地」，所以这里不抛。

    可选成员：

    ``cached``            回验要联网的 provider 缺省缓存身份 60 秒。会话存在本地的 provider
                          把它设为 False：每次直接查库，登出、改口令、停用账号即时生效。
    ``admin_claims``      provider 是否权威地报告管理员标记。为真时，admin / owner 还要求
                          上游此刻也给了这个资格（见 ``skein.accounts.effective_role``）。
    ``logout_cookie_domain``  登出清 Cookie 时用哪个域。控制面按「显式配置 → 本次登录
                          实际种的域 → 这个值」的顺序取；不提供就只清 host-only。
    """

    name: str
    cookie_names: tuple[str, ...]

    def login(self, identifier: str, secret: str) -> tuple[Principal, dict[str, Any]]: ...
    def verify(self, session: str) -> Principal | None: ...
    def revoke(self, session: str) -> None: ...


@dataclass(frozen=True)
class AccessPolicy:
    """入口的准入条件，与协议无关，由部署方配置。

    ``require_internal``：是否要求主体是上游登录系统认定的「内部成员」。缺省开，
    保持历史行为；面向公开用户的部署应显式关掉。本地账号与令牌模式自带内部成员身份，
    不受它影响。
    """

    require_internal: bool = True

    def check(self, principal: Principal, level: str = LEVEL_CONSOLE) -> None:
        if self.require_internal and not principal.internal:
            raise AuthRejected("not_internal", 403)


class SessionVerifier:
    """Cookie → provider 回验 → 身份（缓存 ``VERIFY_TTL`` 秒）→ 账号与角色。

    缓存的是「会话对应的身份」，不是授权结论——每次请求都重新查账号、过矩阵，
    所以改角色、停用账号都是即时生效的。
    """

    def __init__(
        self,
        provider: IdentityProvider,
        policy: AccessPolicy | None = None,
        verify: Callable[[str], Principal | None] | None = None,
        ttl: float = VERIFY_TTL,
        directory: Any | None = None,
        cookie_secure: bool = True,
        cookie_domain_override: str | None = None,
        cookie_max_age: int = 7 * 86400,
    ) -> None:
        self.provider = provider
        self.policy = policy if policy is not None else AccessPolicy()
        self._verify = verify if verify is not None else provider.verify
        self._ttl = ttl
        self._directory = directory
        # 种会话 Cookie 的属性：Secure 缺省开；域可以显式覆盖（None = 按 provider 的来）
        self.cookie_secure = cookie_secure
        # 会话 Cookie 的有效期（秒）：provider 没在登录响应里另行给出时用它
        self.cookie_max_age = cookie_max_age
        self._cookie_domain_override = cookie_domain_override
        # 回验要联网的 provider 才缓存身份；会话在自己库里的 provider（cached=False）
        # 每次直接查库，登出、改口令、停用账号都是即时生效
        self._cacheable = bool(getattr(provider, "cached", True))
        self._lock = threading.Lock()
        self._cache: dict[str, tuple[Any, float]] = {}
        # 登录时上游给的 Cookie 域：登出清 Cookie 要用同一个域，不能靠猜或硬编码
        self._cookie_domain: str | None = None

    @property
    def mode(self) -> str:
        """登录方式：token / local / sso。"""
        return getattr(self.provider, "mode", "sso")

    @property
    def cookie_names(self) -> tuple[str, ...]:
        return self.provider.cookie_names

    @property
    def cookie_domain(self) -> str | None:
        """最近一次登录实际种的 Cookie 域；没登录过返回 None（清 host-only）。"""
        return self._cookie_domain

    def cookie_domain_for(self, from_response: str | None = None) -> str | None:
        """种 Cookie 用的域：显式覆盖优先，其次登录响应里的值。None 表示 host-only。"""
        if self._cookie_domain_override is not None:
            return self._cookie_domain_override or None
        return from_response or None

    def logout_cookie_domain(self) -> str | None:
        """登出清 Cookie 用的域：显式覆盖 → 本次登录记录 → provider 推导。

        两者都没有就返回 None（清 host-only）。
        """
        if self._cookie_domain_override is not None:
            return self._cookie_domain_override or None
        if self._cookie_domain is not None:
            return self._cookie_domain
        getter = getattr(self.provider, "logout_cookie_domain", None)
        return getter() if callable(getter) else None

    def note_login(self, session: str, cookie_domain: str | None) -> None:
        with self._lock:
            self._cookie_domain = cookie_domain
            self._cache.pop(session, None)

    def cookie_value(self, cookies: dict[str, str]) -> str | None:
        for name in self.cookie_names:
            value = cookies.get(name)
            if value:
                return value
        return None

    # ---------------- 认人 ----------------

    def authenticate(self, cookies: dict[str, str]) -> Principal:
        """只认人：请求带没带一个 provider 认可的会话。"""
        session = self.cookie_value(cookies)
        if not session:
            raise AuthRejected("no_sso", 401)
        if not self._cacheable:
            principal = self._verify(session)
            if principal is None:
                raise AuthRejected("invalid_sso", 401)
            return principal
        now = time.time()
        with self._lock:
            hit = self._cache.get(session)
        if hit and hit[1] > now:
            cached = hit[0]
            if isinstance(cached, (AuthRejected, AuthUnavailable)):
                raise cached
            if isinstance(cached, Principal):
                return cached
        try:
            principal = self._verify(session)
        except AuthRejected as error:
            self._remember(session, error, now, self._ttl)
            raise
        except AuthUnavailable as error:
            self._remember(session, error, now, UNAVAILABLE_TTL)
            raise
        if principal is None:
            error = AuthRejected("invalid_sso", 401)
            self._remember(session, error, now, self._ttl)
            raise error
        self._remember(session, principal, now, self._ttl)
        return principal

    # ---------------- 放行 ----------------

    def identify(self, cookies: dict[str, str]) -> Principal:
        """认出是谁即可（聊天代理给上游签身份头用）：不要求有雁阵账号。"""
        principal = self.authenticate(cookies)
        self.policy.check(principal, LEVEL_USER)
        return principal

    def verify(self, cookies: dict[str, str], level: str = LEVEL_CONSOLE) -> Principal:
        if level in USER_LEVELS:
            return self.identify(cookies)
        return self.check(self.authenticate(cookies), level)

    def check(self, principal: Principal, level: str = LEVEL_CONSOLE) -> Principal:
        """登录刚拿到身份、或每个请求验完会话之后，过同一道门。"""
        self.policy.check(principal, level)
        if level in USER_LEVELS:
            return principal
        return self.admit(principal)

    def admit(self, identity: Principal) -> Principal:
        """身份 → 雁阵里的人（账号、角色）。没挂账号目录就只认身份。"""
        if self._directory is None:
            return identity
        return self._directory.admit(
            identity,
            self.provider.name,
            admin_claims=bool(getattr(self.provider, "admin_claims", False)),
        )

    def login(
        self, identifier: str, secret: str, level: str = LEVEL_CONSOLE
    ) -> tuple[Principal, dict[str, Any]]:
        """标识 + 口令 → (可授权的主体, 给浏览器种 Cookie 的会话信息)。"""
        principal, session = self.provider.login(identifier, secret)
        return self.check(principal, level), session

    def revoke(self, session: str) -> None:
        """登出。上游不可达或拒绝也要把本地清干净，所以这里吞掉 provider 的异常。"""
        try:
            self.provider.revoke(session)
        except (AuthUnavailable, AuthRejected):
            pass
        self.invalidate(session)

    # ---------------- 内部 ----------------

    def _remember(self, session: str, value: Any, now: float, ttl: float) -> None:
        with self._lock:
            if len(self._cache) >= CACHE_MAX:
                for key in list(self._cache)[: CACHE_MAX // 4]:
                    self._cache.pop(key, None)
            self._cache[session] = (value, now + ttl)

    def invalidate(self, session: str) -> None:
        with self._lock:
            self._cache.pop(session, None)


__all__ = [
    "ASSISTANT",
    "CURRENT_PRINCIPAL",
    "LEVEL_ADMIN",
    "LEVEL_CONSOLE",
    "LEVEL_MARKET",
    "LEVEL_USER",
    "MACHINE",
    "SESSION",
    "USER_LEVELS",
    "AccessPolicy",
    "AuthRejected",
    "AuthUnavailable",
    "IdentityProvider",
    "Principal",
    "SessionVerifier",
    "principal_from_payload",
    "token_matches",
]
