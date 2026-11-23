"""登录方式：每种协议一个模块，在这里登记与选择。

当前启用的登录方式由配置决定，规则只有三条（见 :func:`resolve_method`）：

1. 配了 ``SKEIN_SSO_CLIENT_ID`` + ``SKEIN_SSO_CLIENT_SECRET``：用 ``SKEIN_SSO_PROVIDER``
   指定的外部登录系统（缺省 ``unisso``）。
2. 否则 ``SKEIN_LOCAL_LOGIN=1``：本地账号。
3. 否则是零配置的令牌模式。

同一时间只启用一种给人用的登录方式。1 和 2 同时配置是冲突，启动时就报错说清楚，
而不是悄悄挑一个。账号的数据模型（一个人可以挂多种身份）并不受这个限制，日后要支持
「外部登录系统 + 本地应急账号」并存，需要加的只是登录页的选择与这里的校验。

新增一种外部登录系统：

1. 在本包下新建模块，实现 ``skein.auth.IdentityProvider`` 协议
   （``name``、``cookie_names``、``login``、``verify``、``revoke``）；
2. 提供一个 ``build(config) -> IdentityProvider | None``：从配置构造，未配置返回 None；
3. 在下面的 ``PROVIDERS`` 里登记名字。

控制面核心（``skein.api`` / ``skein.auth``）只认 ``IdentityProvider``，不需要改。
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from skein.accounts import Directory
from skein.auth import AccessPolicy, IdentityProvider, SessionVerifier
from skein.auth_providers import builtin, unisso
from skein.sessions import SESSION_TTL_S, SessionStore


class ProviderBuilder(Protocol):
    def __call__(self, config: object) -> IdentityProvider | None: ...


# 外部登录系统（协议需要上游服务）
PROVIDERS: dict[str, ProviderBuilder] = {
    unisso.NAME: unisso.build,
}
# 全部可写的登录方式名，给报错信息用
KNOWN = (*PROVIDERS, builtin.TOKEN, builtin.LOCAL)


def build_provider(name: str, config: object) -> IdentityProvider | None:
    """按名字构造外部登录系统的 provider；名字未登记抛 ValueError，未配置返回 None。"""
    key = (name or "").strip().lower()
    builder: Callable[[object], IdentityProvider | None] | None = PROVIDERS.get(key)
    if builder is None:
        raise ValueError(
            f"unknown SSO provider {key!r}; available: {', '.join(sorted(PROVIDERS))}"
        )
    return builder(config)


class ConfigError(ValueError):
    """配置自相矛盾：启动时直接失败，并说清楚是哪几个变量打架。"""


@dataclass(frozen=True)
class LoginMethod:
    """当前启用的登录方式。``mode`` 是 token / local / sso 三大类，``provider`` 是具体名字。"""

    mode: str
    provider: str

    @property
    def human_login(self) -> bool:
        """有给人用的登录系统时，主令牌只代表机器。"""
        return self.mode != "token"


def _get(config: object, name: str, default: str = "") -> str:
    return str(getattr(config, name, default) or "").strip()


def _flag(config: object, name: str, default: bool = False) -> bool:
    """配置里的开关既可能是 bool 也可能是环境变量里的字符串。"""
    value = getattr(config, name, None)
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes")
    return bool(value)


def resolve_method(config: object) -> LoginMethod:
    sso_name = (_get(config, "sso_provider") or unisso.NAME).lower()
    has_sso = bool(_get(config, "sso_client_id") and _get(config, "sso_client_secret"))
    if sso_name not in PROVIDERS:
        raise ConfigError(
            f"SKEIN_SSO_PROVIDER={sso_name!r} is not registered; available: {', '.join(sorted(PROVIDERS))}"
        )
    if has_sso and _flag(config, "local_login"):
        raise ConfigError(
            "SKEIN_SSO_CLIENT_ID/SKEIN_SSO_CLIENT_SECRET and SKEIN_LOCAL_LOGIN=1 are both set;"
            " enable exactly one human login method (SSO or local accounts)"
        )
    if has_sso:
        return LoginMethod("sso", sso_name)
    if _flag(config, "local_login"):
        return LoginMethod("local", builtin.LOCAL)
    return LoginMethod("token", builtin.TOKEN)


@dataclass(frozen=True)
class MinimalConfig:
    """只有主令牌的配置。库调用方（测试、嵌入）不传任何登录设置时用它：零配置的令牌模式。"""

    token: str


def build(
    config: object, conn: sqlite3.Connection
) -> tuple[SessionVerifier, Directory, SessionStore]:
    """按配置装好登录：账号目录、会话库、provider 与回验器。"""
    method = resolve_method(config)
    hours = float(_get(config, "session_ttl_hours") or SESSION_TTL_S / 3600)
    sessions = SessionStore(conn, ttl_s=int(hours * 3600))
    directory = Directory(conn, auto_role=_get(config, "auth_auto_role"), sessions=sessions)
    cookie_name = _get(config, "session_cookie_name") or builtin.DEFAULT_COOKIE
    secure = _flag(config, "session_cookie_secure", default=True)
    if method.mode == "sso":
        provider = build_provider(method.provider, config)
        assert provider is not None  # resolve_method 已经确认凭据齐全
        policy = AccessPolicy(require_internal=_flag(config, "sso_require_internal", default=True))
    elif method.mode == "local":
        provider = builtin.LocalProvider(directory, sessions, cookie_name)
        policy = AccessPolicy(require_internal=False)
    else:
        provider = builtin.TokenProvider(_get(config, "token"), sessions, cookie_name)
        policy = AccessPolicy(require_internal=False)
    verifier = SessionVerifier(
        provider,
        policy,
        directory=directory,
        cookie_secure=secure,
        cookie_domain_override=getattr(config, "sso_cookie_domain", None),
        cookie_max_age=sessions.ttl_s,
    )
    return verifier, directory, sessions


__all__ = [
    "KNOWN",
    "PROVIDERS",
    "ConfigError",
    "LoginMethod",
    "MinimalConfig",
    "build",
    "build_provider",
    "resolve_method",
]
