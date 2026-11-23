"""命令行：管理命令是控制面的 HTTP 客户端，用主令牌认证；口令不走参数。"""

from __future__ import annotations

import io
import json
import socket
import sys
import threading
import time

import pytest
import uvicorn
from helpers import A_PASSWORD, TOKEN, make_app

import skein.__main__ as entry
from skein import __version__, cli


def free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def start(app, port: int) -> tuple[uvicorn.Server, threading.Thread]:
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error", lifespan="off")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 15
    while not server.started and time.time() < deadline:
        time.sleep(0.02)
    assert server.started
    return server, thread


def stop(server: uvicorn.Server, thread: threading.Thread) -> None:
    server.should_exit = True
    thread.join(timeout=10)


@pytest.fixture()
def control(tmp_path):
    """起一个真的控制面（本地账号模式），给命令行当服务端；返回它的地址。"""
    rig = make_app(tmp_path, "local", name="cli.db")
    port = free_port()
    server, thread = start(rig.client.app, port)
    yield f"http://127.0.0.1:{port}"
    stop(server, thread)
    rig.conn.close()


class Printer:
    """收集命令行的输出与失败原因（不带默认写到 stderr 时的 "error: " 前缀）。``last()`` 取最后一条。"""

    def __init__(self):
        self.lines: list = []
        self.errors: list = []

    def out(self, data) -> None:
        self.lines.append(data)

    def err(self, message: str) -> None:
        self.errors.append(message)

    def last(self):
        return self.lines[-1]


def run(printer: Printer, *args: str) -> int:
    return cli.run(list(args), out=printer.out, err=printer.err)


def test_user_and_assistant_commands_talk_to_the_control_plane(control, monkeypatch):
    monkeypatch.setenv("SKEIN_TOKEN", TOKEN)
    monkeypatch.setenv("SKEIN_CONTROL_URL", control)
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    printer = Printer()
    create_owner = ("user", "create", "--email", "owner@example.com", "--role", "owner", "--password-stdin")

    # 离线生成一把形状正确的钥匙，给 SKEIN_ASSISTANTS 声明用
    assert run(printer, "assistant", "key") == 0
    assert printer.last().startswith("skein_at_")

    # 口令只从标准输入或终端读，不进命令行参数
    assert run(printer, *create_owner) == 1
    assert printer.errors == ["empty password"]
    monkeypatch.setattr(sys, "stdin", io.StringIO(A_PASSWORD + "\n"))
    assert run(printer, *create_owner) == 0
    assert printer.last()["role"] == "owner"

    assert run(printer, "user", "list") == 0
    assert [user["email"] for user in printer.last()] == ["owner@example.com"]

    # 最后一个 owner 不能降级、不能停用：命令行把控制面的拒绝原样带回来
    assert run(printer, "user", "role", "owner@example.com", "member") == 1
    assert "last owner" in printer.errors[-1]
    assert run(printer, "user", "disable", "owner@example.com") == 1
    assert "last owner" in printer.errors[-1]

    monkeypatch.setattr(sys, "stdin", io.StringIO(A_PASSWORD + "\n"))
    assert run(printer, "user", "create", "--email", "second@example.com", "--role", "owner", "--password-stdin") == 0
    assert run(printer, "user", "role", "owner@example.com", "member") == 0
    assert printer.last()["role"] == "member"
    monkeypatch.setattr(sys, "stdin", io.StringIO("another-long-password\n"))
    assert run(printer, "user", "passwd", "owner@example.com", "--password-stdin") == 0
    assert run(printer, "user", "disable", "nobody@example.com") == 1
    assert "no such user" in printer.errors[-1]

    assert run(printer, "assistant", "issue", "--name", "Forum-Bot", "--purpose", "论坛前厅") == 0
    issued = printer.last()
    assert issued["assistant"]["name"] == "forum-bot"
    assert issued["key"].startswith("skein_at_")
    assert run(printer, "assistant", "list") == 0
    assert "key" not in json.dumps(printer.last())
    assert run(printer, "assistant", "rotate", "forum-bot") == 0
    rotated = printer.last()
    assert rotated["key"] != issued["key"]
    assert rotated["assistant"]["id"] == issued["assistant"]["id"]
    assert run(printer, "assistant", "callback", "forum-bot", "--url", "https://bot.example.com/hook") == 0
    assert printer.last()["callback_secret"].startswith("whsec_")
    assert run(printer, "assistant", "revoke", "forum-bot") == 0
    assert printer.last()["assistant"]["revoked_at"] is not None
    assert run(printer, "assistant", "rotate", "forum-bot") == 1
    assert "revoked" in printer.errors[-1]


def test_cli_needs_the_master_token(monkeypatch):
    monkeypatch.delenv("SKEIN_TOKEN", raising=False)
    printer = Printer()
    assert run(printer, "user", "list") == 1
    assert printer.errors == [
        "SKEIN_TOKEN is not set (it authenticates you to the control plane)"
    ]


def test_cli_reports_what_the_control_plane_said(control, monkeypatch):
    monkeypatch.setenv("SKEIN_TOKEN", "not-the-token")
    monkeypatch.setenv("SKEIN_CONTROL_URL", control)
    printer = Printer()
    assert run(printer, "user", "list") == 1
    assert printer.errors == ["HTTP 401: invalid_token"]


def test_cli_reports_an_unreachable_control_plane(monkeypatch):
    monkeypatch.setenv("SKEIN_TOKEN", TOKEN)
    monkeypatch.setenv("SKEIN_CONTROL_URL", f"http://127.0.0.1:{free_port()}")
    printer = Printer()
    assert run(printer, "assistant", "list") == 1
    assert printer.errors[-1].startswith("cannot reach the control plane at ")


def test_entry_point_dispatches(capsys):
    entry.main(["version"])
    assert capsys.readouterr().out.strip() == f"skein {__version__}"
    entry.main(["--help"])
    assert "python -m skein" in capsys.readouterr().out
    with pytest.raises(SystemExit) as excinfo:
        entry.main(["wat"])
    assert excinfo.value.code == 2
    assert "unknown command: wat" in capsys.readouterr().err
