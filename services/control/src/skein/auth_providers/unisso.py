"""UniSSO provider：M2M 密码校验 + 共享会话 Cookie。

协议（三个接口，都在 provider 的 base 地址下）：

- 登录  POST ``/api/oauth/password/verify``（JSON）→ ``user`` + ``sso_session`` + cookie 参数
- 回验  POST ``/api/oauth/session/verify``（form: session / client_id / client_secret）
- 登出  POST ``/api/oauth/session/revoke``（form，同上）

Cookie 的值不可自证，必须服务端回验。401/403 是「没登录/会话无效」，
429/5xx/超时是「上游暂不可用」——两者语义必须分开。
地址、client、Cookie 名与域全部来自配置，这里不设任何默认域名。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

from skein.auth import (
    AuthRejected,
    AuthUnavailable,
    IdentityProvider,
    Principal,
    principal_from_payload,
)

NAME = "unisso"
DEFAULT_COOKIE = "unisso_sid"
# 历史上用过的旧 Cookie 名：部署方可能还有浏览器带着它
LEGACY_COOKIES = ("sse_sso",)


@dataclass(frozen=True)
class UniSSOSettings:
    base: str
    client_id: str
    client_secret: str
    cookie_name: str = DEFAULT_COOKIE
    # None=按上游响应种；""=host-only；其他值=显式域
    cookie_domain: str | None = None
    timeout: float = 5.0

    @classmethod
    def from_config(cls, config: object) -> UniSSOSettings | None:
        """没配 client_id/client_secret 就是不启用。

        base 缺失不算「未配置」——是否启用只看凭据是否配齐，地址问题留到调用时暴露。
        """
        client_id = str(getattr(config, "sso_client_id", "") or "").strip()
        client_secret = str(getattr(config, "sso_client_secret", "") or "").strip()
        if not (client_id and client_secret):
            return None
        return cls(
            base=str(getattr(config, "sso_base", "") or "").strip().rstrip("/"),
            client_id=client_id,
            client_secret=client_secret,
            cookie_name=(
                str(getattr(config, "sso_cookie_name", "") or "").strip() or DEFAULT_COOKIE
            ),
            cookie_domain=getattr(config, "sso_cookie_domain", None),
            timeout=5.0,
        )


def _read_json(raw: bytes) -> dict[str, Any]:
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def _post(
    base: str,
    path: str,
    body: bytes,
    content_type: str,
    timeout: float,
) -> tuple[int, dict[str, Any]]:
    request = urllib.request.Request(
        base + path,
        data=body,
        headers={"Content-Type": content_type},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, _read_json(response.read())
    except urllib.error.HTTPError as error:
        return error.code, _read_json(error.read())
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise AuthUnavailable(f"{NAME}_unreachable: {error}") from error


class UniSSOProvider(IdentityProvider):
    # UniSSO 的 is_admin / is_super_admin 是权威的：上游收回标记，雁阵里的角色跟着降
    admin_claims = True

    def __init__(self, settings: UniSSOSettings) -> None:
        self._settings = settings
        self.name = NAME
        self.cookie_names = (settings.cookie_name, *LEGACY_COOKIES)

    def logout_cookie_domain(self) -> str | None:
        """登出清 Cookie 的域。

        显式配置优先。没配时按「SSO 与应用同父域、靠父域 Cookie 共享会话」推导：
        取 SSO 地址的 host，去掉最左一段（``sso.example.com`` → ``.example.com``）。
        这是 UniSSO 这类内接部署的惯例；别的部署用 ``SKEIN_COOKIE_DOMAIN`` 覆盖，
        ``host`` 表示 host-only。
        """
        if self._settings.cookie_domain is not None:
            return self._settings.cookie_domain or None
        host = urllib.parse.urlsplit(self._settings.base).hostname or ""
        parts = host.split(".")
        return "." + ".".join(parts[1:]) if len(parts) >= 3 else None

    def _form(self, session: str) -> bytes:
        return urllib.parse.urlencode(
            {
                "session": session,
                "client_id": self._settings.client_id,
                "client_secret": self._settings.client_secret,
            }
        ).encode()

    def login(self, email: str, password: str) -> tuple[Principal, dict[str, Any]]:
        body = json.dumps(
            {
                "client_id": self._settings.client_id,
                "client_secret": self._settings.client_secret,
                "email": email,
                "password": password,
            }
        ).encode()
        status, payload = _post(
            self._settings.base,
            "/api/oauth/password/verify",
            body,
            "application/json",
            self._settings.timeout,
        )
        if status == 200:
            principal = principal_from_payload(payload)
            session = str(payload.get("sso_session") or "")
            if principal is None or not session:
                raise AuthRejected("sso_bad_response", 502)
            return principal, {
                "session": session,
                "cookie_name": str(payload.get("sso_cookie_name") or "") or self._settings.cookie_name,
                "cookie_domain": str(payload.get("sso_cookie_domain") or ""),
                "cookie_max_age": int(payload.get("sso_max_age") or 604800),
            }
        if status == 401:
            raise AuthRejected("invalid_credentials", 401)
        if status == 403:
            raise AuthRejected("password_verify_forbidden", 403)
        if status == 429:
            raise AuthUnavailable("too_many_attempts")
        raise AuthUnavailable(f"{NAME}_http_{status}")

    def verify(self, session: str) -> Principal | None:
        status, payload = _post(
            self._settings.base,
            "/api/oauth/session/verify",
            self._form(session),
            "application/x-www-form-urlencoded",
            self._settings.timeout,
        )
        if status == 200:
            return principal_from_payload(payload)
        if status == 401:
            raise AuthRejected("invalid_sso", 401)
        if status == 403:
            raise AuthRejected("not_internal", 403)
        raise AuthUnavailable(f"{NAME}_http_{status}")

    def revoke(self, session: str) -> None:
        status, _ = _post(
            self._settings.base,
            "/api/oauth/session/revoke",
            self._form(session),
            "application/x-www-form-urlencoded",
            self._settings.timeout,
        )
        if status >= 500:
            raise AuthUnavailable(f"{NAME}_http_{status}")
        if status in (401, 403):
            raise AuthRejected("invalid_sso", 401)


def build(config: object) -> IdentityProvider | None:
    settings = UniSSOSettings.from_config(config)
    return UniSSOProvider(settings) if settings is not None else None
