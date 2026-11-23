"""测试里共用的小工具：建库、装 app、各种登录方式的替身。"""

from __future__ import annotations

import json
from dataclasses import dataclass

from fastapi.testclient import TestClient

from skein import db
from skein.accounts import Directory
from skein.api import create_app
from skein.auth import (
    AccessPolicy,
    AuthRejected,
    AuthUnavailable,
    Principal,
    SessionVerifier,
)
from skein.auth_providers.builtin import LocalProvider, TokenProvider
from skein.sessions import SessionStore

TOKEN = "unit-token"
# 一组固定的人：内部管理员、内部普通成员、外部用户、超级管理员
ADMIN = Principal(sub="u1", name="张三", email="a@example.com", internal=True, admin=True)
MEMBER = Principal(sub="u2", name="李四", email="b@example.com", internal=True, admin=False)
OUTSIDER = Principal(sub="u3", name="王五", email="x@example.com", internal=False)
SUPER = Principal(sub="u9", name="老板", email="root@example.com", internal=True, super_admin=True)
PASSWORD = "correct-horse-battery"
A_PASSWORD = "a-long-enough-password"


class FakeProvider:
    """一种外部登录协议的替身：会话表里查得到就算已登录，不联网。"""

    admin_claims = False

    def __init__(self, name: str = "fake", *, admin_claims: bool = False) -> None:
        self.name = name
        self.admin_claims = admin_claims
        self.cookie_names = (f"{name}_sid",)
        self.sessions: dict[str, Principal] = {}
        self.passwords: dict[str, str] = {}
        self.revoked: list[str] = []
        self.logins: list[tuple[str, str]] = []
        self.down = False

    def add(self, principal: Principal, session: str, password: str = PASSWORD) -> str:
        self.sessions[session] = principal
        self.passwords[principal.email] = password
        return session

    def login(self, identifier: str, secret: str) -> tuple[Principal, dict]:
        self.logins.append((identifier, secret))
        if self.down:
            raise AuthUnavailable("down")
        session = next(
            (key for key, who in self.sessions.items() if who.email == identifier), None
        )
        if session is None or self.passwords.get(identifier) != secret:
            raise AuthRejected("invalid_credentials", 401)
        return self.sessions[session], {
            "session": session,
            "cookie_name": self.cookie_names[0],
            "cookie_domain": "",
            "cookie_max_age": 3600,
        }

    def verify(self, session: str) -> Principal | None:
        if self.down:
            raise AuthUnavailable("down")
        return self.sessions.get(session)

    def revoke(self, session: str) -> None:
        if self.down:
            raise AuthUnavailable("down")
        self.revoked.append(session)
        self.sessions.pop(session, None)


def standard_provider(name: str = "fake", **flags) -> FakeProvider:
    """带齐四个人的替身：会话名是 s- 加 sub（s-u1、s-u2、s-u3、s-u9）。"""
    provider = FakeProvider(name, **flags)
    for person in (ADMIN, MEMBER, OUTSIDER, SUPER):
        provider.add(person, f"s-{person.sub}")
    return provider


@dataclass
class Rig:
    """一个装好的控制面，连同测试要伸手进去的几样东西。"""

    client: TestClient
    conn: object
    login: SessionVerifier
    directory: Directory

    @property
    def provider(self):
        return self.login.provider

    def audit(self, action: str) -> list[dict]:
        rows = self.conn.execute("SELECT * FROM audit WHERE action = ? ORDER BY id", (action,))
        return [{**dict(row), "detail": json.loads(row["detail"])} for row in rows]

    def users(self) -> list[dict]:
        return [user.as_dict() for user in self.directory.list()]


def make_login(
    conn,
    provider=None,
    *,
    require_internal: bool = True,
    auto_role: str = "",
    cookie_secure: bool = False,
    cookie_domain: str | None = None,
    cache_ttl: float = 60.0,
) -> tuple[SessionVerifier, Directory]:
    """装好一个登录配置。``provider=None`` 是零配置的令牌模式，"local" 是本地账号。"""
    sessions = SessionStore(conn, ttl_s=3600)
    directory = Directory(conn, auto_role=auto_role, sessions=sessions)
    if provider is None or provider == "local":
        built = (
            LocalProvider(directory, sessions)
            if provider == "local"
            else TokenProvider(TOKEN, sessions)
        )
    else:
        built = provider
    login = SessionVerifier(
        built,
        AccessPolicy(require_internal=require_internal),
        directory=directory,
        cookie_secure=cookie_secure,
        cookie_domain_override=cookie_domain,
        ttl=cache_ttl,
    )
    return login, directory


def make_app(tmp_path, provider=None, *, name: str = "app.db", **kwargs) -> Rig:
    """建库、装 app。登录相关的参数交给 make_login，其余交给 create_app。"""
    conn = db.connect(tmp_path / name)
    db.migrate(conn)
    login_keys = ("require_internal", "auto_role", "cookie_secure", "cookie_domain", "cache_ttl")
    login, directory = make_login(
        conn, provider, **{key: kwargs.pop(key) for key in login_keys if key in kwargs}
    )
    app = create_app(
        conn,
        str(tmp_path / "blobs"),
        kwargs.pop("token", TOKEN),
        login=login,
        directory=directory,
        **kwargs,
    )
    return Rig(TestClient(app), conn, login, directory)


def cookie_of(response, name: str | None = None) -> dict[str, str]:
    """从 Set-Cookie 里取出会话，之后手动带（httpx 不会替别的域保存 Cookie）。"""
    header = response.headers.get("set-cookie", "")
    first = header.split(";", 1)[0]
    assert "=" in first, header
    if name is not None:
        assert first.startswith(f"{name}="), header
    return {"Cookie": first}


def machine(token: str = TOKEN) -> dict[str, str]:
    return {"X-Skein-Token": token}


def signed_in(sub: str) -> dict[str, str]:
    """standard_provider 里某个人的会话 Cookie。"""
    return {"Cookie": f"fake_sid=s-{sub}"}
