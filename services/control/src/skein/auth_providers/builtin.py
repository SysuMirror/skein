"""内建登录方式：令牌模式（token）与本地账号（local）。

两者的会话都存在雁阵自己的 ``login_sessions`` 表里（见 ``skein.sessions``），
所以 verify / revoke 不碰任何上游。令牌模式只在没配任何其他登录方式时启用：
一旦有了给人用的登录系统，主令牌就只代表机器，不再能换成人的会话。
"""

from __future__ import annotations

from typing import Any

from skein import passwords
from skein.accounts import (
    LOCAL_PROVIDER,
    TOKEN_HOLDER,
    TOKEN_PROVIDER,
    Directory,
    normalize_email,
    token_identity,
)
from skein.auth import (
    AuthRejected,
    IdentityProvider,
    Principal,
    token_matches,
)
from skein.sessions import SessionStore

TOKEN = TOKEN_PROVIDER
LOCAL = LOCAL_PROVIDER
DEFAULT_COOKIE = "skein_session"


class SkeinSessionProvider(IdentityProvider):
    """会话存在雁阵自己表里的 provider 共用的 verify / revoke。"""

    admin_claims = False
    # 查库即可，不必缓存：这样登出、改口令、停用账号都是即时生效
    cached = False

    def __init__(self, sessions: SessionStore, cookie_name: str = DEFAULT_COOKIE) -> None:
        self._sessions = sessions
        self.cookie_names = (cookie_name,)

    def verify(self, session: str) -> Principal | None:
        row = self._sessions.resolve(session)
        if row is None or row["provider"] != self.name:
            return None
        return self._identity(row["user_sub"])

    def revoke(self, session: str) -> None:
        self._sessions.revoke(session)

    def _issue(self, user_sub: str) -> dict[str, Any]:
        """签发会话并给出种 Cookie 要用的信息。"""
        return {
            "session": self._sessions.issue(user_sub, self.name),
            "cookie_name": self.cookie_names[0],
            "cookie_max_age": self._sessions.ttl_s,
        }

    def _identity(self, user_sub: str) -> Principal | None:
        raise NotImplementedError


class TokenProvider(SkeinSessionProvider):
    """零配置：持有主令牌的人在控制台登录页填同一个令牌，换一个只属于浏览器的会话。

    这样主令牌不必存在浏览器的 localStorage 里、也不必随每个请求发出去。
    """

    name = TOKEN
    mode = "token"

    def __init__(self, master_token: str, sessions: SessionStore, cookie_name: str = DEFAULT_COOKIE) -> None:
        super().__init__(sessions, cookie_name)
        self._token = master_token

    def login(self, identifier: str, secret: str = "") -> tuple[Principal, dict[str, Any]]:
        if not self._token or not token_matches(identifier, self._token):
            raise AuthRejected("invalid_token", 401)
        return token_identity(), self._issue(TOKEN_HOLDER)

    def _identity(self, user_sub: str) -> Principal | None:
        return token_identity() if user_sub == TOKEN_HOLDER else None


class LocalProvider(SkeinSessionProvider):
    """本地账号：邮箱 + 口令，账号由 owner 用命令行建（``python -m skein user create``）。"""

    name = LOCAL
    mode = "local"

    def __init__(
        self,
        directory: Directory,
        sessions: SessionStore,
        cookie_name: str = DEFAULT_COOKIE,
    ) -> None:
        super().__init__(sessions, cookie_name)
        self._directory = directory

    def login(self, identifier: str, secret: str) -> tuple[Principal, dict[str, Any]]:
        user = self._directory.local_account(identifier)
        # 账号不存在也照样算一次散列：响应耗时一致，不泄露「这个邮箱有没有账号」
        if user is None:
            passwords.burn()
            raise AuthRejected("invalid_credentials", 401)
        stored = self._directory.password_hash(user.sub)
        if not stored or not passwords.verify_password(secret, stored):
            raise AuthRejected("invalid_credentials", 401)
        if user.disabled:
            raise AuthRejected("account_disabled", 403)
        return self._identity_of(user), self._issue(user.sub)

    def _identity(self, user_sub: str) -> Principal | None:
        user = self._directory.get(user_sub)
        if user is None or not user.has_password or user.disabled:
            return None
        return self._identity_of(user)

    def _identity_of(self, user) -> Principal:
        # provider 眼里的主体号是邮箱（本地身份 = 邮箱）；本地账号当然算内部成员
        identity = self._directory.principal_for(user, provider=LOCAL)
        return Principal(
            sub=normalize_email(user.email),
            name=identity.name,
            email=identity.email,
            internal=True,
            picture=identity.picture,
            admin=identity.admin,
            super_admin=identity.super_admin,
        )
