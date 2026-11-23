"""助手凭证与公共 API：签发/轮换/吊销、幂等、限速、大小上限、与主令牌互不相通、状态回调。"""

from __future__ import annotations

import hashlib
import json
import time

import pytest
from helpers import TOKEN, machine, make_app, signed_in, standard_provider

from skein import db, notify
from skein.assistants import AssistantStore
from skein.notify import Notifier

REQUEST = {
    "title": "登录页在手机上错位",
    "description": "iPhone 上点登录没反应",
    "source": "forum",
    "external_id": "post:42",
    "requester": "forum:278",
    "category": "bug",
    "severity": "high",
    "labels": ["login", "mobile"],
    "links": ["https://forum.example.com/t/42"],
}


def bearer(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


def issue(rig, name: str = "forum-bot", **extra) -> dict:
    response = rig.client.post(
        "/internal/assistants", json={"name": name, **extra}, headers=machine()
    )
    assert response.status_code == 201, response.text
    return response.json()


def submit(client, key: str, **overrides):
    return client.post("/api/v1/tickets", json={**REQUEST, **overrides}, headers=bearer(key))


# ================= 凭证库 =================


def test_issue_returns_the_key_once_and_stores_only_its_hash(rigs):
    rig = rigs()
    issued = issue(rig, purpose="论坛前厅助手")
    key = issued["key"]
    assert key.startswith("skein_at_") and len(key) > 40
    stored = rig.conn.execute("SELECT key_hash FROM assistants").fetchone()["key_hash"]
    assert stored == hashlib.sha256(key.encode()).hexdigest()
    listed = rig.client.get("/internal/assistants", headers=machine()).json()["assistants"]
    assert listed[0]["name"] == "forum-bot"
    assert listed[0]["purpose"] == "论坛前厅助手"
    assert "key" not in listed[0] and key not in json.dumps(listed)
    assert rig.audit("assistant.issue")[0]["actor"] == "machine"


def test_rotate_invalidates_the_old_key_and_keeps_everything_else(rigs):
    rig = rigs()
    issued = issue(rig, purpose="看门")
    old = issued["key"]
    assert submit(rig.client, old).status_code == 201
    rotated = rig.client.post(f"/internal/assistants/{issued['assistant']['id']}/rotate", headers=machine())
    assert rotated.status_code == 200
    new = rotated.json()["key"]
    assert new != old
    assert submit(rig.client, old).status_code == 401
    assert submit(rig.client, new).status_code == 200  # 同一个请求：幂等重试
    assistant = rotated.json()["assistant"]
    assert (assistant["purpose"], assistant["id"]) == ("看门", issued["assistant"]["id"])


def test_revoke_blocks_the_key_and_its_callback(rigs):
    rig = rigs()
    issued = issue(rig, callback_url="https://bot.example.com/hooks/skein")
    assert issued["callback_secret"].startswith("whsec_")
    store = AssistantStore(rig.conn)
    assert store.callback_target(issued["assistant"]["id"]) is not None
    revoked = rig.client.delete(f"/internal/assistants/{issued['assistant']['id']}", headers=machine())
    assert revoked.status_code == 200
    assert revoked.json()["assistant"]["revoked_at"] is not None
    assert submit(rig.client, issued["key"]).status_code == 401
    assert store.callback_target(issued["assistant"]["id"]) is None
    # 已吊销的凭证不能再轮换；行保留，名字不回收
    again = rig.client.post(f"/internal/assistants/{issued['assistant']['id']}/rotate", headers=machine())
    assert again.status_code == 400


@pytest.mark.parametrize(
    ("body", "fragment"),
    (
        ({"name": "Bad Name"}, "name must be"),
        ({"name": "a"}, "name must be"),
        ({"name": "cb", "callback_url": "ftp://x.example.com/hook"}, "http(s) URL"),
    ),
)
def test_bad_input_is_a_clear_400(rigs, body, fragment):
    rig = rigs()
    response = rig.client.post("/internal/assistants", json=body, headers=machine())
    assert response.status_code == 400
    assert fragment in response.json()["detail"]


def test_unknown_assistant_is_404(rigs):
    response = rigs().client.post("/internal/assistants/nobody/rotate", headers=machine())
    assert response.status_code == 404


def test_duplicate_name_is_refused(rigs):
    rig = rigs()
    issue(rig, name="ci")
    response = rig.client.post("/internal/assistants", json={"name": "ci"}, headers=machine())
    assert response.status_code == 400
    assert "already exists" in response.json()["detail"]


def test_declared_credentials_follow_the_environment(rigs):
    """环境文件里声明的凭证以声明为准：新增、换钥匙、撤掉声明即吊销；不动接口签发的。"""
    rig = rigs()
    via_api = issue(rig, name="from-console")
    store = AssistantStore(rig.conn)
    first = "skein_at_" + "a" * 43
    second = "skein_at_" + "b" * 43

    assert store.declare([{"name": "from-env", "key": first, "purpose": "巡检"}]) == ["from-env"]
    assert store.authenticate(first).name == "from-env"
    # 同一份声明再跑一遍：什么都不变
    assert store.declare([{"name": "from-env", "key": first, "purpose": "巡检"}]) == []
    # 换了钥匙 = 轮换
    assert store.declare([{"name": "from-env", "key": second, "purpose": "巡检"}]) == ["from-env"]
    assert store.authenticate(first) is None
    assert store.authenticate(second).name == "from-env"
    # 撤掉声明 = 吊销
    assert store.declare([]) == []
    assert store.authenticate(second) is None
    # 名字撞上接口签发的凭证：不动它
    assert store.declare([{"name": "from-console", "key": first}]) == []
    assert store.authenticate(via_api["key"]).name == "from-console"
    # 声明里没有钥匙、或钥匙形状不对：跳过并留警告，不签发一把没人知道的钥匙
    assert store.declare([{"name": "keyless"}, {"name": "bad", "key": "short"}]) == []


def test_callback_can_be_set_cleared_and_rekeyed(rigs):
    rig = rigs()
    issued = issue(rig)
    path = f"/internal/assistants/{issued['assistant']['id']}"
    first = rig.client.patch(path, json={"callback_url": "https://bot.example.com/hook"}, headers=machine())
    assert first.json()["callback_secret"].startswith("whsec_")
    # 再改地址但不换钥匙：不返回新密钥（沿用原来的）
    same = rig.client.patch(path, json={"callback_url": "https://bot.example.com/hook2"}, headers=machine())
    assert "callback_secret" not in same.json()
    rekeyed = rig.client.patch(
        path, json={"callback_url": "https://bot.example.com/hook2", "new_callback_secret": True}, headers=machine()
    )
    assert rekeyed.json()["callback_secret"] != first.json()["callback_secret"]
    cleared = rig.client.patch(path, json={"callback_url": ""}, headers=machine())
    assert (cleared.json()["assistant"]["callback_url"], cleared.json()["assistant"]["callback_secret_set"]) == ("", False)


# ================= 公共 API：提交请求 =================


def test_submit_creates_a_ticket_attributed_to_the_assistant(rigs):
    rig = rigs(public_url="https://skein.example.com")
    issued = issue(rig)
    response = submit(rig.client, issued["key"])
    assert response.status_code == 201
    ticket = response.json()["ticket"]
    assert response.json()["created"] is True
    assert ticket["code"].startswith("SK-")
    assert ticket["status"] == "staged"
    assert ticket["public_status"] == "received"
    assert ticket["url"] == f"https://skein.example.com/console/#/tickets/{ticket['id']}"
    assert ticket["source"] == "forum" and ticket["external_id"] == "post:42"
    row = rig.conn.execute("SELECT submitted_by, requester, summary FROM tickets").fetchone()
    assert row["submitted_by"] == issued["assistant"]["id"]
    assert row["requester"] == "forum:278"
    # 结构化字段写进摘要备注，与 /internal/intake 的写法一致：控制台里直接可读
    for fragment in ("category=bug", "severity=high", "labels=login,mobile", "iPhone 上点登录没反应"):
        assert fragment in row["summary"]
    audit = rig.audit("ticket.api_create")[0]
    assert audit["actor"] == "assistant:forum-bot"
    assert audit["detail"]["external_id"] == "post:42"


def test_ticket_url_is_omitted_without_a_public_url(rigs):
    rig = rigs()
    key = issue(rig)["key"]
    assert submit(rig.client, key).json()["ticket"]["url"] is None


def test_retry_returns_the_original_ticket(rigs):
    rig = rigs()
    key = issue(rig)["key"]
    first = submit(rig.client, key).json()
    again = submit(rig.client, key)
    assert again.status_code == 200
    assert again.json() == {"created": False, "ticket": first["ticket"]}


def test_same_external_id_with_another_title_is_a_conflict(rigs):
    rig = rigs()
    key = issue(rig)["key"]
    assert submit(rig.client, key).status_code == 201
    clash = submit(rig.client, key, title="完全不同的另一件事")
    assert clash.status_code == 409
    assert clash.json()["detail"]["code"] == "idempotency_conflict"
    assert "use a new external_id" in clash.json()["detail"]["message"]


def test_idempotency_is_scoped_per_credential_and_source(rigs):
    rig = rigs()
    one, two = issue(rig, "one")["key"], issue(rig, "two")["key"]
    a = submit(rig.client, one).json()["ticket"]
    b = submit(rig.client, two).json()["ticket"]
    c = submit(rig.client, one, source="chat").json()["ticket"]
    assert len({a["id"], b["id"], c["id"]}) == 3


def test_assistant_sees_only_its_own_tickets(rigs):
    rig = rigs()
    mine, theirs = issue(rig, "mine")["key"], issue(rig, "theirs")["key"]
    first = submit(rig.client, mine).json()["ticket"]
    submit(rig.client, mine, external_id="post:43")
    hidden = submit(rig.client, theirs, external_id="post:44").json()["ticket"]

    listed = rig.client.get("/api/v1/tickets", headers=bearer(mine)).json()["tickets"]
    assert [item["external_id"] for item in listed] == ["post:43", "post:42"]  # 新的在前
    by_id = rig.client.get(f"/api/v1/tickets/{first['id']}", headers=bearer(mine))
    assert by_id.status_code == 200
    by_code = rig.client.get(f"/api/v1/tickets/{first['code']}", headers=bearer(mine))
    assert by_code.json()["ticket"]["id"] == first["id"]
    # 别人的工单与不存在的工单没有区别
    for ref in (hidden["id"], "no-such-ticket"):
        response = rig.client.get(f"/api/v1/tickets/{ref}", headers=bearer(mine))
        assert (response.status_code, response.json()["detail"]["code"]) == (404, "not_found")


def test_me_describes_the_credential(rigs):
    rig = rigs()
    issued = issue(rig)
    me = rig.client.get("/api/v1/me", headers=bearer(issued["key"])).json()
    assert me["assistant"]["name"] == "forum-bot"
    assert me["scopes"] == ["api.ticket.create", "api.ticket.read"]
    assert "key" not in json.dumps(me)


def test_status_follows_review(rigs):
    rig = rigs()
    key = issue(rig)["key"]
    ticket = submit(rig.client, key).json()["ticket"]
    rig.client.post(
        f"/internal/tickets/{ticket['id']}/review", json={"decision": "approve"}, headers=machine()
    )
    seen = rig.client.get(f"/api/v1/tickets/{ticket['id']}", headers=bearer(key)).json()["ticket"]
    assert (seen["status"], seen["public_status"]) == ("done", "fixed")


# ================= 两边的身份互不相通 =================


def test_master_token_is_rejected_on_the_public_api(rigs):
    rig = rigs()
    issue(rig)
    for headers in (machine(), bearer(TOKEN), {}):
        response = rig.client.post("/api/v1/tickets", json=REQUEST, headers=headers)
        assert response.status_code == 401
        assert response.json()["detail"]["code"] == "invalid_key"


def test_a_signed_in_person_cannot_use_the_public_api_either(rigs):
    rig = rigs(standard_provider())
    response = rig.client.post("/api/v1/tickets", json=REQUEST, headers=signed_in("u1"))
    assert response.status_code == 401


def test_assistant_key_cannot_touch_the_internal_api(rigs):
    """提权检查：助手凭证拿去派工、验收、取消、管人、管凭证，一律 403。"""
    rig = rigs()
    key = issue(rig)["key"]
    ticket = submit(rig.client, key).json()["ticket"]
    refused = [
        rig.client.post(f"/internal/tickets/{ticket['id']}/dispatch?platform=forum", headers=bearer(key)),
        rig.client.post(
            f"/internal/tickets/{ticket['id']}/review", json={"decision": "approve"}, headers=bearer(key)
        ),
        rig.client.post(f"/internal/tickets/{ticket['id']}/cancel", headers=bearer(key)),
        rig.client.post(
            "/internal/tickets", json={"title": "想自己派单", "dispatch": True, "platform": "x"}, headers=bearer(key)
        ),
        rig.client.post("/internal/intake", json={"source": "x", "thread_id": "1", "title": "y"}, headers=bearer(key)),
        rig.client.get("/internal/tickets", headers=bearer(key)),
        rig.client.get("/internal/users", headers=bearer(key)),
        rig.client.get("/internal/assistants", headers=bearer(key)),
        rig.client.post("/internal/assistants", json={"name": "sneaky"}, headers=bearer(key)),
        rig.client.post("/internal/agent/chat", json={"message": "hi"}, headers=bearer(key)),
    ]
    assert [(response.status_code, response.json()["detail"]) for response in refused] == [
        (403, "assistant_key_not_allowed")
    ] * len(refused)
    # 公共 API 的登录探测也不会把它当成别的什么身份
    assert rig.client.get("/api/session", headers=bearer(key)).json() == {"mode": "token", "provider": "token"}


# ================= 限速、大小、参数 =================


def test_rate_limit_is_per_credential(rigs):
    rig = rigs(api_rate_limit=3)
    limited, other = issue(rig, "limited")["key"], issue(rig, "other")["key"]
    for _ in range(3):
        assert submit(rig.client, limited).status_code in (200, 201)
    fourth = submit(rig.client, limited)
    assert fourth.status_code == 429
    assert fourth.json()["detail"]["code"] == "rate_limited"
    assert "Retry-After" in fourth.headers
    assert fourth.headers["X-RateLimit-Remaining"] == "0"
    assert int(fourth.headers["X-RateLimit-Limit"]) == 3
    assert submit(rig.client, other, external_id="post:9").status_code == 201


def test_rate_limit_headers_on_success(rigs):
    rig = rigs(api_rate_limit=5)
    key = issue(rig)["key"]
    first = submit(rig.client, key)
    assert first.headers["X-RateLimit-Limit"] == "5"
    assert first.headers["X-RateLimit-Remaining"] == "4"
    assert int(first.headers["X-RateLimit-Reset"]) > time.time()


def test_oversized_body_is_refused_before_it_is_parsed(rigs):
    rig = rigs()
    key = issue(rig)["key"]
    declared = rig.client.post(
        "/api/v1/tickets", content=b"x" * (64 * 1024 + 1), headers={**bearer(key), "Content-Type": "application/json"}
    )
    assert (declared.status_code, declared.json()["detail"]["code"]) == (413, "payload_too_large")


def test_oversized_body_without_content_length_is_refused_while_reading(rigs):
    rig = rigs()
    key = issue(rig)["key"]

    def chunks():
        for _ in range(20):
            yield b"x" * 8192

    response = rig.client.post(
        "/api/v1/tickets", content=chunks(), headers={**bearer(key), "Content-Type": "application/json"}
    )
    assert (response.status_code, response.json()["detail"]["code"]) == (413, "payload_too_large")


def test_invalid_fields_are_reported(rigs):
    rig = rigs()
    key = issue(rig)["key"]
    response = submit(rig.client, key, title="", source="Forum Post!", external_id="", severity="huge")
    assert response.status_code == 422
    body = response.json()["detail"]
    assert body["code"] == "invalid_request"
    assert {field["field"] for field in body["fields"]} == {
        "body.title",
        "body.source",
        "body.external_id",
        "body.severity",
    }


def test_validation_errors_elsewhere_keep_the_default_shape(rigs):
    """422 的统一错误体只管公共 API；内部接口保持 FastAPI 默认格式。"""
    rig = rigs()
    response = rig.client.post("/internal/tickets", json={"summary": "no title"}, headers=machine())
    assert response.status_code == 422
    assert isinstance(response.json()["detail"], list)


# ================= 可选：留痕上报 =================


def test_trace_ingestion_is_off_unless_the_deployment_turns_it_on(rigs):
    off = rigs()
    key = issue(off)["key"]
    assert off.client.post("/api/v1/llm/calls", json={"model": "m"}, headers=bearer(key)).status_code == 404

    on = rigs(api_trace=True)
    key = issue(on)["key"]
    me = on.client.get("/api/v1/me", headers=bearer(key)).json()
    assert me["scopes"] == ["api.ticket.create", "api.ticket.read", "api.trace.write"]
    trace = {"session_id": "chat-1", "source": "frontdesk", "model": "m", "prompt": "问题", "response": "回答", "prompt_tokens": 12}
    created = on.client.post("/api/v1/llm/calls", json={**trace, "actor": "冒充别人"}, headers=bearer(key))
    assert created.status_code == 201
    row = on.conn.execute("SELECT actor, source, prompt_tokens FROM llm_calls").fetchone()
    # 是谁报的由凭证决定，调用方说了不算
    assert (row["actor"], row["source"], row["prompt_tokens"]) == ("assistant:forum-bot", "frontdesk", 12)


# ================= 状态回调 =================


def test_review_posts_a_signed_callback_to_the_assistant(tmp_path):
    """工单验收后，给提交它的助手发一个带签名的回调；对方不必持有雁阵的任何令牌。"""
    posted: list[tuple[str, dict, str]] = []
    owner = db.connect(tmp_path / "callback.db")
    db.migrate(owner)
    store = AssistantStore(owner)
    rig = make_app(
        tmp_path,
        name="callback.db",
        assistants=store,
        notifier=Notifier(
            owner,
            store.callback_target,
            poster=lambda url, payload, secret: posted.append((url, payload, secret)),
            public_url="https://skein.example.com",
        ),
    )
    try:
        issued = issue(rig, callback_url="https://bot.example.com/internal/skein/notify")
        secret = issued["callback_secret"]
        ticket = submit(rig.client, issued["key"]).json()["ticket"]
        assert posted == []  # 提交本身不触发回调

        rig.client.post(
            f"/internal/tickets/{ticket['id']}/review", json={"decision": "approve"}, headers=machine()
        )
        assert len(posted) == 1
        url, payload, used = posted[0]
        assert url == "https://bot.example.com/internal/skein/notify"
        assert used == secret
        assert payload["event"] == "ticket.status"
        assert payload["ticket"] == {
            "id": ticket["id"],
            "code": ticket["code"],
            "status": "done",
            "public_status": "fixed",
            "title": REQUEST["title"],
            "source": "forum",
            "external_id": "post:42",
            "requester": "forum:278",
            "url": f"https://skein.example.com/console/#/tickets/{ticket['id']}",
        }

        # 公开状态没变就不重复发
        rig.client.post(
            f"/internal/tickets/{ticket['id']}/review", json={"decision": "approve"}, headers=machine()
        )
        assert len(posted) == 1
    finally:
        owner.close()


def test_no_callback_for_tickets_without_an_assistant_or_a_callback_url(tmp_path):
    """没配回调的助手、以及不是助手提交的工单：公开状态照常更新，只是没有回调可发。"""
    posted: list = []
    owner = db.connect(tmp_path / "quiet.db")
    db.migrate(owner)
    store = AssistantStore(owner)
    rig = make_app(
        tmp_path,
        name="quiet.db",
        assistants=store,
        notifier=Notifier(
            owner,
            store.callback_target,
            poster=lambda *sent: posted.append(sent),
        ),
    )
    try:
        quiet = issue(rig, "quiet")["key"]
        filed = submit(rig.client, quiet).json()["ticket"]["id"]
        scanned = rig.client.post(
            "/internal/tickets", json={"title": "巡检"}, headers=machine()
        ).json()["ticket_id"]
        for ticket_id in (filed, scanned):
            rig.client.post(
                f"/internal/tickets/{ticket_id}/review",
                json={"decision": "approve"},
                headers=machine(),
            )
        assert posted == []
        statuses = [row["public_status"] for row in owner.execute("SELECT public_status FROM tickets")]
        assert statuses == ["fixed", "fixed"]
    finally:
        owner.close()


def test_signature_roundtrip():
    secret = "whsec_" + "s" * 43
    body = json.dumps({"event": "ticket.status"}).encode()
    timestamp = int(time.time())
    signature = notify.sign(timestamp, body, secret)
    assert signature.startswith("v1=") and len(signature) == 67
    assert notify.verify(timestamp, body, secret, signature)
    assert not notify.verify(timestamp, body, secret + "x", signature)  # 换了密钥
    assert not notify.verify(timestamp, body + b" ", secret, signature)  # 改了正文
    assert not notify.verify(timestamp - notify.TIMESTAMP_TOLERANCE_S - 1, body, secret, signature)  # 旧的回调
    assert not notify.verify(timestamp, body, "", signature)


def test_webhook_channel_sends_signed_headers(rigs):
    seen: dict = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return b"{}"

    def fake_urlopen(request, timeout):
        seen["url"] = request.full_url
        seen["headers"] = {key.lower(): value for key, value in request.header_items()}
        seen["body"] = request.data
        return Response()

    channel = notify.WebhookChannel(urlopen=fake_urlopen)
    channel.send("https://bot.example.com/hook", {"event": "ticket.status", "x": 1}, "whsec_k")
    timestamp = int(seen["headers"]["x-skein-webhook-timestamp"])
    assert seen["headers"]["x-skein-event"] == "ticket.status"
    assert notify.verify(timestamp, seen["body"], "whsec_k", seen["headers"]["x-skein-webhook-signature"])
    assert json.loads(seen["body"]) == {"event": "ticket.status", "x": 1}


# ================= 接口文档 =================


def test_openapi_is_not_exposed_unless_enabled(rigs):
    assert rigs().client.get("/openapi.json").status_code == 404
    assert rigs().client.get("/docs").status_code == 404
    schema = rigs(docs=True).client.get("/openapi.json").json()
    assert "/api/v1/tickets" in schema["paths"]
    assert "/api/v1/me" in schema["paths"]
