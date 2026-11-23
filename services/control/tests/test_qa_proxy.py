"""可选集成（聊天代理）：不配不挂路由；配了只透传，身份头只由雁阵自己签。"""

from fastapi.testclient import TestClient
from helpers import FakeProvider
from helpers import make_app as build_app

from skein.auth import Principal
from skein.qa_proxy import verify_user_header


class FakeResponse:
    def __init__(self, status: int, body: bytes, content_type: str = "application/json"):
        self.status_code = status
        self.headers = {"content-type": content_type}
        self._body = body

    async def aiter_raw(self):
        yield self._body

    async def aclose(self):
        return None


class FakeClient:
    """记录上游请求，返回固定响应；替代 httpx.MockTransport（它不支持流式）。"""

    def __init__(self):
        self.requests = []

    def build_request(self, method, url, headers=None, content=None):
        self.requests.append(
            {
                "method": method,
                "url": url,
                "headers": dict(headers or {}),
                "body": (content or b"").decode(),
            }
        )
        return self.requests[-1]

    async def send(self, request, stream=True):
        return FakeResponse(200, b'{"ok": true}')


def make_app(tmp_path, sso=False, internal=True, admin=True, user_secret=""):
    """挂上聊天代理。sso=True 时再挂一个替身登录系统：只有 u1（会话 sess-ok）一个人。"""
    upstream = FakeClient()
    provider = None
    if sso:
        provider = FakeProvider()
        provider.add(Principal(sub="u1", name="张三", internal=internal, admin=admin), "sess-ok")
    rig = build_app(
        tmp_path,
        provider,
        name="qa.db",
        qa_chat_upstream="http://chat.test",
        qa_client=upstream,
        qa_user_secret=user_secret,
    )
    return rig.client.app, rig.conn, upstream


def test_chat_proxies_credentials_and_strips_cookie(tmp_path):
    app, conn, upstream = make_app(tmp_path)
    with TestClient(app) as client:
        response = client.get(
            "/api/v1/agent/suggestions",
            headers={"Authorization": "Bearer user-token", "Cookie": "fake_sid=secret"},
        )
    assert response.status_code == 200
    assert response.json() == {"ok": True}
    sent = upstream.requests[-1]
    assert sent["url"] == "http://chat.test/api/v1/agent/suggestions"
    assert sent["headers"].get("authorization") == "Bearer user-token"
    assert "cookie" not in {k.lower() for k in sent["headers"]}
    conn.close()


def test_chat_attaches_identity_header_for_a_signed_in_person(tmp_path):
    """已登录的人（哪怕没有雁阵账号）：代理给上游带一个短时签名的身份头。"""
    app, conn, upstream = make_app(
        tmp_path, sso=True, internal=True, admin=False, user_secret="user-secret-123456"
    )
    with TestClient(app) as client:
        response = client.get("/api/v1/agent/x", headers={"Cookie": "fake_sid=sess-ok"})
    assert response.status_code == 200
    sent = upstream.requests[-1]["headers"]
    assert "cookie" not in {k.lower() for k in sent}
    data = verify_user_header(sent["x-skein-user"], "user-secret-123456")
    assert data["sub"] == "u1"
    assert data["internal"] is True
    assert data["admin"] is False
    conn.close()


def test_chat_does_not_attach_identity_when_the_login_system_is_down(tmp_path):
    """登录系统挂了：聊天照样可用，只是按匿名处理，而不是把人当成已登出或报错。"""
    provider = FakeProvider()
    provider.add(Principal(sub="u1", name="张三", internal=True, admin=True), "sess-ok")
    provider.down = True
    upstream = FakeClient()
    rig = build_app(
        tmp_path,
        provider,
        name="qa-down.db",
        qa_chat_upstream="http://chat.test",
        qa_client=upstream,
        qa_user_secret="user-secret-123456",
    )
    with rig.client as client:
        response = client.get("/api/v1/agent/x", headers={"Cookie": "fake_sid=sess-ok"})
    assert response.status_code == 200
    assert "x-skein-user" not in {k.lower() for k in upstream.requests[-1]["headers"]}
    rig.conn.close()


def test_chat_strips_client_supplied_identity_header(tmp_path):
    app, conn, upstream = make_app(tmp_path)
    with TestClient(app) as client:
        response = client.get(
            "/api/v1/agent/x",
            headers={"X-Skein-User": "forged", "Authorization": "Bearer user-token"},
        )
    assert response.status_code == 200
    assert "x-skein-user" not in {k.lower() for k in upstream.requests[-1]["headers"]}
    conn.close()


def test_chat_records_event_per_message_in_one_thread(tmp_path):
    app, conn, _ = make_app(tmp_path, sso=True, internal=True, admin=False)
    headers = {"Cookie": "fake_sid=sess-ok"}
    with TestClient(app) as client:
        first = client.post(
            "/api/v1/agent/task", json={"task": "第一个问题", "session_id": "s1"}, headers=headers
        )
        second = client.post(
            "/api/v1/agent/task", json={"task": "第二个问题", "session_id": "s1"}, headers=headers
        )
    assert first.status_code == 200
    assert second.status_code == 200
    events = conn.execute(
        "SELECT origin, origin_id, thread_id, verdict FROM events ORDER BY rowid"
    ).fetchall()
    assert len(events) == 2
    assert {event["origin"] for event in events} == {"chat"}
    assert {event["verdict"] for event in events} == {"record"}
    assert events[0]["thread_id"] == events[1]["thread_id"]
    thread = conn.execute(
        "SELECT origin, origin_id FROM threads WHERE id = ?", (events[0]["thread_id"],)
    ).fetchone()
    assert (thread["origin"], thread["origin_id"]) == ("chat", "s1")
    conn.close()


def test_chat_forwards_method_body_and_query(tmp_path):
    app, conn, upstream = make_app(tmp_path)
    with TestClient(app) as client:
        response = client.post("/task/sse?topic=x", json={"question": "在吗"})
    assert response.status_code == 200
    sent = upstream.requests[-1]
    assert sent["method"] == "POST"
    assert sent["url"] == "http://chat.test/task/sse?topic=x"
    assert "在吗" in sent["body"]
    conn.close()


def test_disabled_upstream_exposes_no_routes(tmp_path):
    """没配聊天上游 = 可选集成不挂载：路由根本不存在，而不是存在但报 503。"""
    rig = build_app(tmp_path, None, name="qa-none.db")
    app = rig.client.app
    paths = {getattr(route, "path", "") for route in app.routes}
    assert not any(p.startswith(("/api/v1/agent", "/task")) for p in paths)
    with rig.client as client:
        assert client.get("/api/v1/agent/x").status_code == 404
        assert client.post("/task/x", json={"task": "hi"}).status_code == 404
    rig.conn.close()
