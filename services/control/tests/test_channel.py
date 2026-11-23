import time

import pytest
from fastapi.testclient import TestClient

from skein import db
from skein.api import create_app
from skein.forge import PullRequest

TOKEN = "chan-token"


class FakePR:
    def __init__(self) -> None:
        self.calls = []

    def open_for(self, ticket, result):
        self.calls.append((ticket, result))
        if not result.get("pushed") or not result.get("branch"):
            return None
        return PullRequest(number=1, url="https://github.com/o/r/pull/1", base="develop")


@pytest.fixture()
def app_client(tmp_path):
    conn = db.connect(tmp_path / "chan.db")
    db.migrate(conn)
    pr = FakePR()
    app = create_app(
        conn,
        str(tmp_path / "blobs"),
        TOKEN,
        platform_repos={"forum": "/srv/repo"},
        pr_service=pr,
    )
    with TestClient(app) as client:
        client.conn = conn
        client.pr = pr
        yield client
    conn.close()


def make_ticket(conn, code="SK-1"):
    t = time.time()
    conn.execute(
        "INSERT INTO tickets (id, code, title, status, created_at, updated_at)"
        " VALUES (?, ?, '求助', 'staged', ?, ?)",
        (code, code, t, t),
    )
    return code


def register(ws, token=TOKEN, node_id="n1"):
    ws.send_json(
        {
            "t": "node.register",
            "node_id": node_id,
            "token": token,
            "name": "node-1",
            "platforms": ["forum"],
            "max_workers": 2,
            "peer_url": "ws://127.0.0.1:9101/peer",
        }
    )
    return ws.receive_json()


def test_register_rejects_bad_token(app_client):
    with app_client.websocket_connect("/api/agent/channel") as ws:
        reply = register(ws, token="wrong")
        assert reply["t"] == "error"


def test_register_marks_node_online(app_client):
    with app_client.websocket_connect("/api/agent/channel") as ws:
        reply = register(ws)
        assert reply == {"t": "node.registered", "node_id": "n1"}
        row = app_client.conn.execute("SELECT status, max_workers FROM nodes").fetchone()
        assert tuple(row) == ("online", 2)


def test_dispatch_marks_assigned_and_rejects_duplicate(app_client):
    ticket = make_ticket(app_client.conn, code="SK-4")
    with app_client.websocket_connect("/api/agent/channel") as ws:
        register(ws)
        first = app_client.post(
            f"/internal/tickets/{ticket}/dispatch?platform=forum",
            headers={"X-Skein-Token": TOKEN},
        )
        assert first.json()["ok"] is True
        status = app_client.conn.execute(
            "SELECT status FROM tickets WHERE id = ?", (ticket,)
        ).fetchone()[0]
        assert status == "assigned"

        again = app_client.post(
            f"/internal/tickets/{ticket}/dispatch?platform=forum",
            headers={"X-Skein-Token": TOKEN},
        )
        body = again.json()
        assert body["ok"] is False
        assert body["reason"] == "already_dispatched"
        assert body["dispatch_id"] == first.json()["dispatch_id"]
        ws.receive_json()  # worker.create


def test_worker_create_failure_returns_ticket_to_staged(app_client):
    ticket = make_ticket(app_client.conn, code="SK-5")
    conn = app_client.conn
    with app_client.websocket_connect("/api/agent/channel") as ws:
        register(ws)
        body = app_client.post(
            f"/internal/tickets/{ticket}/dispatch?platform=forum",
            headers={"X-Skein-Token": TOKEN},
        ).json()
        assert body["ok"] is True
        create = ws.receive_json()
        assert create["t"] == "worker.create"
        ws.send_json(
            {
                "t": "worker.create_failed",
                "worker_id": create["worker_id"],
                "reason": "仓库不存在 (未配置)",
            }
        )
        # dispatch 落 failed 与 ticket 回 staged 是两条语句，等到两者都到位再断言
        state = status = ""
        for _ in range(50):
            row = conn.execute(
                "SELECT state FROM dispatches WHERE id = ?", (body["dispatch_id"],)
            ).fetchone()
            status = conn.execute(
                "SELECT status FROM tickets WHERE id = ?", (ticket,)
            ).fetchone()[0]
            if row and row["state"] == "failed" and status == "staged":
                state = "failed"
                break
            time.sleep(0.02)
        assert state == "failed"
        assert status == "staged"


def test_dispatch_roundtrip(app_client):
    ticket = make_ticket(app_client.conn)
    with app_client.websocket_connect("/api/agent/channel") as ws:
        register(ws)
        response = app_client.post(
            f"/internal/tickets/{ticket}/dispatch?platform=forum",
            headers={"X-Skein-Token": TOKEN},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["ok"] is True

        create = ws.receive_json()
        assert create["t"] == "worker.create"
        assert create["repo"] == "/srv/repo"

        ws.send_json(
            {"t": "worker.created", "worker_id": body["worker_id"], "session_id": "sess-1"}
        )
        dispatch_msg = ws.receive_json()
        assert dispatch_msg["t"] == "dispatch"
        assert dispatch_msg["ticket"]["code"] == ticket

        ws.send_json(
            {"t": "result", "worker_id": body["worker_id"], "ticket_id": ticket, "result": {"ok": True}}
        )
        for _ in range(50):
            status = app_client.conn.execute(
                "SELECT status FROM tickets WHERE id = ?", (ticket,)
            ).fetchone()[0]
            if status == "review":
                break
            time.sleep(0.02)
        assert status == "review"
        state = app_client.conn.execute("SELECT state FROM dispatches").fetchone()[0]
        assert state == "done"


def test_result_creates_pr_and_records_url(app_client):
    ticket = make_ticket(app_client.conn)
    with app_client.websocket_connect("/api/agent/channel") as ws:
        register(ws)
        response = app_client.post(
            f"/internal/tickets/{ticket}/dispatch?platform=forum",
            headers={"X-Skein-Token": TOKEN},
        )
        body = response.json()
        assert body["ok"] is True
        ws.receive_json()  # worker.create
        ws.send_json(
            {"t": "worker.created", "worker_id": body["worker_id"], "session_id": "sess-1"}
        )
        ws.receive_json()  # dispatch
        ws.send_json(
            {
                "t": "result",
                "worker_id": body["worker_id"],
                "ticket_id": ticket,
                "result": {
                    "ok": True,
                    "pushed": True,
                    "branch": "fix/sk-1",
                    "base_commit": "abc1234",
                    "commits": "abc1234 fix",
                    "diffstat": "1 file changed",
                    "remote": "git@github.com:o/r.git",
                    "platform": "forum",
                },
            }
        )
        for _ in range(50):
            status = app_client.conn.execute(
                "SELECT status FROM tickets WHERE id = ?", (ticket,)
            ).fetchone()[0]
            if status == "review":
                break
            time.sleep(0.02)
        assert status == "review"

    assert len(app_client.pr.calls) == 1
    assert app_client.pr.calls[0][0]["code"] == ticket
    pr_url = app_client.conn.execute("SELECT pr_url FROM dispatches").fetchone()[0]
    assert pr_url == "https://github.com/o/r/pull/1"
    pr_base = app_client.conn.execute("SELECT pr_base FROM dispatches").fetchone()[0]
    assert pr_base == "develop"
    platform = app_client.conn.execute("SELECT platform FROM dispatches").fetchone()[0]
    assert platform == "forum"
    action = app_client.conn.execute(
        "SELECT action FROM audit WHERE action = 'pr'"
    ).fetchone()
    assert action is not None


def test_dispatch_without_platform_requires_platform(app_client):
    ticket = make_ticket(app_client.conn, code="SK-2")
    response = app_client.post(
        f"/internal/tickets/{ticket}/dispatch", headers={"X-Skein-Token": TOKEN}
    )
    assert response.json() == {
        "ok": False,
        "reason": "platform_required",
        "platforms": ["forum"],
    }


def test_dispatch_unknown_platform_requires_platform(app_client):
    ticket = make_ticket(app_client.conn, code="SK-2b")
    response = app_client.post(
        f"/internal/tickets/{ticket}/dispatch?platform=market-client",
        headers={"X-Skein-Token": TOKEN},
    )
    assert response.json()["reason"] == "platform_required"
    assert response.json()["platforms"] == ["forum"]


def test_dispatch_valid_platform_without_node(app_client):
    ticket = make_ticket(app_client.conn, code="SK-2c")
    response = app_client.post(
        f"/internal/tickets/{ticket}/dispatch?platform=forum",
        headers={"X-Skein-Token": TOKEN},
    )
    assert response.json() == {"ok": False, "reason": "no_node", "platform": "forum"}


def test_dispatch_unknown_ticket_returns_not_found(app_client):
    with app_client.websocket_connect("/api/agent/channel") as ws:
        register(ws)
        response = app_client.post(
            "/internal/tickets/no-such-id/dispatch?platform=forum",
            headers={"X-Skein-Token": TOKEN},
        )
        assert response.status_code == 200
        assert response.json() == {
            "ok": False,
            "reason": "ticket_not_found",
            "ticket_id": "no-such-id",
        }


def test_review_approve_and_reject(app_client):
    ticket = make_ticket(app_client.conn, code="SK-3")
    ok = app_client.post(
        f"/internal/tickets/{ticket}/review",
        json={"decision": "approve"},
        headers={"X-Skein-Token": TOKEN},
    )
    assert ok.json()["status"] == "done"
    bad = app_client.post(
        f"/internal/tickets/{ticket}/review",
        json={"decision": "maybe"},
        headers={"X-Skein-Token": TOKEN},
    )
    assert bad.status_code == 400


def test_cancel_sends_to_node_and_requeues_ticket(app_client):
    conn = app_client.conn
    ticket = make_ticket(conn, code="SK-C1")
    conn.execute("UPDATE tickets SET status = 'assigned' WHERE id = ?", (ticket,))
    conn.execute(
        "INSERT INTO dispatches"
        " (id, ticket_id, node_id, worker_id, state, attempt, updated_at)"
        " VALUES ('d-c1', ?, 'n1', 'w-c1', 'dispatched', 1, 1)",
        (ticket,),
    )
    with app_client.websocket_connect("/api/agent/channel") as ws:
        register(ws)
        response = app_client.post(
            f"/internal/tickets/{ticket}/cancel",
            headers={"X-Skein-Token": TOKEN},
        )
        assert response.json() == {"cancelled": True}
        message = ws.receive_json()
        assert message == {"t": "cancel", "ticket_id": ticket, "worker_id": "w-c1"}
    state = conn.execute("SELECT state FROM dispatches WHERE id = 'd-c1'").fetchone()[0]
    status = conn.execute("SELECT status FROM tickets WHERE id = ?", (ticket,)).fetchone()[0]
    assert state == "failed"
    assert status == "staged"


def test_cancel_without_live_dispatch_conflicts(app_client):
    ticket = make_ticket(app_client.conn, code="SK-C2")
    response = app_client.post(
        f"/internal/tickets/{ticket}/cancel",
        headers={"X-Skein-Token": TOKEN},
    )
    assert response.status_code == 409


def test_late_ack_does_not_revive_a_cancelled_dispatch(app_client):
    """ack 可能因为网络延迟在取消之后才到：不能把已取消的派工改回「进行中」。
    否则界面上它永远在跑，而且工单会被当成仍有在途派工而无法再派。"""
    ticket = make_ticket(app_client.conn, code="SK-6")
    conn = app_client.conn
    with app_client.websocket_connect("/api/agent/channel") as ws:
        register(ws)
        body = app_client.post(
            f"/internal/tickets/{ticket}/dispatch?platform=forum",
            headers={"X-Skein-Token": TOKEN},
        ).json()
        wid = body["worker_id"]
        ws.receive_json()  # worker.create
        ws.send_json({"t": "worker.created", "worker_id": wid})
        ws.receive_json()  # dispatch
        # 先取消（派工进入终态 failed）
        assert app_client.post(
            f"/internal/tickets/{ticket}/cancel", headers={"X-Skein-Token": TOKEN}
        ).json() == {"cancelled": True}
        # 迟到的 ack 才到
        ws.send_json({"t": "ack", "worker_id": wid})
        for _ in range(50):
            row = conn.execute(
                "SELECT state FROM dispatches WHERE id = ?", (body["dispatch_id"],)
            ).fetchone()
            if row and row["state"] == "failed":
                break
            time.sleep(0.02)
        time.sleep(0.1)  # 给迟到的 ack 足够时间被处理
        row = conn.execute(
            "SELECT state, detail FROM dispatches WHERE id = ?", (body["dispatch_id"],)
        ).fetchone()
        assert (row["state"], row["detail"]) == ("failed", "cancelled")
        # 终态不被占用：工单可以再次派单
        again = app_client.post(
            f"/internal/tickets/{ticket}/dispatch?platform=forum",
            headers={"X-Skein-Token": TOKEN},
        ).json()
        assert again["ok"] is True, again
        ws.receive_json()  # 新的 worker.create
