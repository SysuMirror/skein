"""命令行：``python -m skein [serve | user … | assistant … | version]``。

不带参数就是启动控制面（systemd 单元用的就是它）。管理命令不直接写库——控制面是唯一的
写进程——而是作为 HTTP 客户端调用控制面自己的管理接口，用主令牌认证：

    SKEIN_CONTROL_URL（缺省 http://127.0.0.1:7800） + SKEIN_TOKEN

在服务器上最省事的做法是 ``deploy/scripts/skein-cli``：它会先读入 /etc/skein/env。
口令一律不进命令行参数（会留在 shell 历史与进程列表里）：用 ``--password-stdin`` 从
标准输入读，或在终端里交互输入。
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any

from skein.access import ROLES
from skein.assistants import new_key

DEFAULT_URL = "http://127.0.0.1:7800"
_TIMEOUT_S = 15.0
MIN_PASSWORD = 12


class ClientError(Exception):
    """命令行能预计的失败：配置缺了、控制面拒绝、网络不通。"""


class Control:
    """控制面管理接口的最小客户端（标准库，无第三方依赖）。"""

    def __init__(self, base: str, token: str) -> None:
        self._base = base.rstrip("/")
        self._token = token
        # 不走系统代理：控制面地址通常是本机或内网
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({})).open

    def call(self, method: str, path: str, body: dict | None = None) -> Any:
        data = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
        request = urllib.request.Request(
            self._base + path,
            data=data,
            method=method,
            headers={
                "Content-Type": "application/json",
                "X-Skein-Token": self._token,
                "User-Agent": "skein-cli",
            },
        )
        try:
            with self._opener(request, timeout=_TIMEOUT_S) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            raise ClientError(_detail(exc.code, exc.read())) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            reason = getattr(exc, "reason", exc)
            raise ClientError(f"cannot reach the control plane at {self._base}: {reason}") from exc
        return json.loads(raw.decode() or "{}") if raw else {}


def _detail(status: int, raw: bytes) -> str:
    try:
        detail = json.loads(raw.decode()).get("detail")
    except (ValueError, AttributeError):
        detail = None
    if isinstance(detail, dict):
        return f"HTTP {status}: {detail.get('message') or detail.get('code') or detail}"
    return f"HTTP {status}: {detail}" if detail else f"HTTP {status}"


def _client() -> Control:
    token = os.environ.get("SKEIN_TOKEN", "")
    if not token:
        raise ClientError("SKEIN_TOKEN is not set (it authenticates you to the control plane)")
    return Control(os.environ.get("SKEIN_CONTROL_URL") or DEFAULT_URL, token)


def _emit(data: Any) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))


def _read_password(stdin: bool) -> str:
    """口令只从标准输入或终端读，永不进参数。"""
    if stdin:
        value = sys.stdin.readline().rstrip("\r\n")
    elif sys.stdin.isatty():
        value = getpass.getpass(f"Password (at least {MIN_PASSWORD} characters): ")
        if value != getpass.getpass("Repeat password: "):
            raise ClientError("the two passwords do not match")
    else:
        raise ClientError("no terminal for a password prompt; use --password-stdin")
    if not value:
        raise ClientError("empty password")
    return value


def _resolve(client: Control, kind: str, ref: str) -> str:
    """命令行里可以写 id、名字（助手）或邮箱（本地账号）；统一换成 id。"""
    id_key, prefix = ("sub", "usr_") if kind == "users" else ("id", "ast_")
    if ref.startswith(prefix):
        return ref
    wanted = ref.strip().lower()
    for row in client.call("GET", f"/internal/{kind}")[kind]:
        known = (str(row.get(id_key) or ""), str(row.get("email") or ""), str(row.get("name") or ""))
        if wanted in {value.lower() for value in known}:
            return str(row[id_key])
    raise ClientError(f"no such {'user' if kind == 'users' else 'assistant'}: {ref}")


# ---------------- user ----------------


def user_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="skein user", description="Manage who can sign in and what they may do."
    )
    sub = parser.add_subparsers(dest="verb", required=True)
    sub.add_parser("list", help="List users, their roles and linked login identities")

    create = sub.add_parser(
        "create", help="Create a user (a local account when a password is given)"
    )
    create.add_argument("--email", required=True, help="Sign-in email; unique among local accounts")
    create.add_argument("--name", default="", help="Display name")
    create.add_argument("--role", default="member", choices=ROLES, help="Default: member")
    create.add_argument(
        "--password-stdin", action="store_true", help="Read the password from stdin"
    )
    create.add_argument(
        "--provider",
        default="",
        help="Pre-provision an external identity instead of a local account (e.g. unisso)",
    )
    create.add_argument("--subject", default="", help="Subject id at that provider")

    for verb, help_text in (
        ("role", "Change a user's role"),
        ("passwd", "Reset a local account's password (signs the user out everywhere)"),
        ("disable", "Disable an account and sign it out everywhere"),
        ("enable", "Re-enable a disabled account"),
    ):
        command = sub.add_parser(verb, help=help_text)
        command.add_argument("user", help="sub (usr_…) or the email of a local account")
        if verb == "role":
            command.add_argument("role", choices=ROLES)
        if verb == "passwd":
            command.add_argument(
                "--password-stdin", action="store_true", help="Read the password from stdin"
            )

    link = sub.add_parser(
        "link",
        help="Attach an external login identity to an existing user (accounts are never merged by email)",
    )
    link.add_argument("user", help="sub (usr_…)")
    link.add_argument("--provider", required=True)
    link.add_argument("--subject", required=True)
    return parser


def _user(client: Control, args: argparse.Namespace) -> Any:
    verb = args.verb
    if verb == "list":
        return client.call("GET", "/internal/users")["users"]
    if verb == "create":
        if bool(args.provider) != bool(args.subject):
            raise ClientError("--provider and --subject go together")
        password = "" if args.provider else _read_password(args.password_stdin)
        if not args.provider and not password:
            raise ClientError("a local account needs a password (--password-stdin or interactive)")
        return client.call(
            "POST",
            "/internal/users",
            {
                "email": args.email,
                "name": args.name,
                "role": args.role,
                "password": password,
                "provider": args.provider,
                "subject": args.subject,
            },
        )
    sub = _resolve(client, "users", args.user)
    if verb == "role":
        return client.call("PATCH", f"/internal/users/{sub}", {"role": args.role})
    if verb == "passwd":
        return client.call(
            "POST", f"/internal/users/{sub}/password", {"password": _read_password(args.password_stdin)}
        )
    if verb in ("disable", "enable"):
        return client.call("PATCH", f"/internal/users/{sub}", {"disabled": verb == "disable"})
    if verb == "link":
        return client.call(
            "POST",
            f"/internal/users/{sub}/identities",
            {"provider": args.provider, "subject": args.subject},
        )
    raise ClientError(f"unknown command: {verb}")  # pragma: no cover


# ---------------- assistant ----------------


def assistant_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="skein assistant",
        description="Manage assistant credentials for the public API (/api/v1).",
    )
    sub = parser.add_subparsers(dest="verb", required=True)
    sub.add_parser("list", help="List assistant credentials (never shows keys)")
    sub.add_parser("key", help="Print a fresh key offline, for declaring one in SKEIN_ASSISTANTS")

    issue = sub.add_parser("issue", help="Issue a credential; the key is printed exactly once")
    issue.add_argument("--name", required=True, help="Lowercase letters, digits and hyphens")
    issue.add_argument("--purpose", default="", help="What this assistant is for")
    issue.add_argument(
        "--callback-url", default="", help="Where ticket status callbacks are POSTed (optional)"
    )

    for verb, help_text in (
        ("rotate", "Replace the key; the old one stops working immediately"),
        ("revoke", "Revoke a credential (kept for the audit trail)"),
    ):
        command = sub.add_parser(verb, help=help_text)
        command.add_argument("assistant", help="Name or id (ast_…)")

    callback = sub.add_parser(
        "callback",
        help="Set or clear the callback URL; prints a new signing secret when one is generated",
    )
    callback.add_argument("assistant", help="Name or id (ast_…)")
    callback.add_argument("--url", default="", help="https://… to set; omit to clear")
    callback.add_argument("--new-secret", action="store_true", help="Rotate the signing secret")
    return parser


def _assistant(client: Control, args: argparse.Namespace) -> Any:
    verb = args.verb
    if verb == "list":
        return client.call("GET", "/internal/assistants")["assistants"]
    if verb == "issue":
        return client.call(
            "POST",
            "/internal/assistants",
            {"name": args.name, "purpose": args.purpose, "callback_url": args.callback_url},
        )
    path = f"/internal/assistants/{_resolve(client, 'assistants', args.assistant)}"
    if verb == "rotate":
        return client.call("POST", f"{path}/rotate")
    if verb == "revoke":
        return client.call("DELETE", path)
    if verb == "callback":
        return client.call(
            "PATCH",
            path,
            {"callback_url": args.url, "new_callback_secret": args.new_secret},
        )
    raise ClientError(f"unknown command: {verb}")  # pragma: no cover


# ---------------- 入口 ----------------


def run(
    argv: list[str],
    *,
    out: Callable[[Any], None] = _emit,
    err: Callable[[str], None] | None = None,
) -> int:
    """执行一条管理命令。返回进程退出码；失败原因写到 stderr。"""
    fail = err or (lambda message: print(f"error: {message}", file=sys.stderr))
    try:
        if argv[:1] not in (["user"], ["assistant"]):
            raise ClientError("expected 'user …' or 'assistant …' (run: python -m skein --help)")
        if argv[:2] == ["assistant", "key"]:
            out(new_key())
            return 0
        parser, action = (
            (user_parser(), _user) if argv[0] == "user" else (assistant_parser(), _assistant)
        )
        out(action(_client(), parser.parse_args(argv[1:])))
    except ClientError as exc:
        fail(str(exc))
        return 1
    except KeyError as exc:  # 控制面回了不认识的形状
        fail(f"unexpected reply from the control plane (missing {exc})")
        return 1
    return 0
