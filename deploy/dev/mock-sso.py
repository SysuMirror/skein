"""本地联调用的假 UniSSO：实现 password/verify、session/verify、session/revoke 三个接口。

用来在没有真实 SSO 的机器上把「SSO 模式」的控制台完整跑起来，不碰任何生产凭据。

    python deploy/dev/mock-sso.py            # 监听 127.0.0.1:13099

账号（密码都是 right-pass）：
- admin@example.com     internal + 管理员   → 能进控制台
- member@example.com    internal、非管理员  → 只能进面向普通用户的入口
- outsider@example.com  非 internal          → 默认策略下被拒（not_internal）
"""

from __future__ import annotations

import json
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

CLIENT_ID = "dev-client"
CLIENT_SECRET = "dev-secret"
PASSWORD = "right-pass"
# 本地 http 调试用 host-only Cookie；响应里给空域即表示不带 Domain
COOKIE_NAME = "dev_sid"
COOKIE_DOMAIN = ""

USERS = {
    "admin@example.com": {
        "sub": "u-admin",
        "name": "管理员",
        "email": "admin@example.com",
        "internal": True,
        "is_admin": True,
        "picture": "",
    },
    "member@example.com": {
        "sub": "u-member",
        "name": "普通成员",
        "email": "member@example.com",
        "internal": True,
        "picture": "",
    },
    "outsider@example.com": {
        "sub": "u-outsider",
        "name": "外部用户",
        "email": "outsider@example.com",
        "internal": False,
        "picture": "",
    },
}

# session 值 → 登录的邮箱
SESSIONS: dict[str, str] = {}
_LOCK = threading.Lock()


def check_client(body: dict) -> bool:
    return body.get("client_id") == CLIENT_ID and body.get("client_secret") == CLIENT_SECRET


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args) -> None:  # noqa: D102
        pass

    def _send(self, status: int, payload: dict) -> None:
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length)
        path = self.path.split("?")[0]
        if path == "/api/oauth/password/verify":
            try:
                body = json.loads(raw)
            except ValueError:
                body = {}
            email = str(body.get("email") or "")
            user = USERS.get(email)
            if not check_client(body):
                self._send(403, {"detail": "password_verify_forbidden"})
            elif user is None or body.get("password") != PASSWORD:
                self._send(401, {"detail": "invalid_credentials"})
            else:
                session = "sess-" + secrets.token_urlsafe(12)
                with _LOCK:
                    SESSIONS[session] = email
                self._send(
                    200,
                    {
                        "ok": True,
                        "user": user,
                        "sso_session": session,
                        "sso_cookie_name": COOKIE_NAME,
                        "sso_cookie_domain": COOKIE_DOMAIN,
                        "sso_max_age": 604800,
                    },
                )
            return

        if path in ("/api/oauth/session/verify", "/api/oauth/session/revoke"):
            form = parse_qs(raw.decode())
            session = (form.get("session") or [""])[0]
            client = {
                "client_id": (form.get("client_id") or [""])[0],
                "client_secret": (form.get("client_secret") or [""])[0],
            }
            if not check_client(client):
                self._send(403, {"detail": "forbidden"})
                return
            with _LOCK:
                email = SESSIONS.get(session)
                if path.endswith("revoke"):
                    SESSIONS.pop(session, None)
            if email is None:
                self._send(401, {"detail": "invalid_sso"})
            elif path.endswith("verify"):
                self._send(200, {"ok": True, "user": USERS[email]})
            else:
                self._send(200, {"ok": True})
            return

        self._send(404, {"detail": "not_found"})


if __name__ == "__main__":
    server = ThreadingHTTPServer(("127.0.0.1", 13099), Handler)
    threading.current_thread().name = "mock-sso"
    print(f"mock SSO (unisso protocol) on 127.0.0.1:13099  accounts: {', '.join(USERS)}")
    server.serve_forever()
