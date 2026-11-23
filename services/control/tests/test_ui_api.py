
import pytest
from fastapi import FastAPI, Header, HTTPException
from fastapi.testclient import TestClient

from skein import db
from skein.session_store import put_blob
from skein.ui_api import create_ui_router

TOKEN = "test-token"
MISSING_SHA = "d" * 64
TURN_TEXT = "第一轮\n"


def auth_stub(x_skein_token: str = Header(default="")) -> None:
    if x_skein_token != TOKEN:
        raise HTTPException(status_code=401, detail="unauthorized")


def headers(token: str = TOKEN) -> dict:
    return {"X-Skein-Token": token}


def seed(conn, blob_root) -> dict:
    text_sha, text_size = put_blob(blob_root, TURN_TEXT.encode())
    bad_sha, bad_size = put_blob(blob_root, b"\xff\xfe")
    conn.execute(
        "INSERT INTO threads (id, origin, origin_id, created_at) VALUES (?, ?, ?, ?)",
        ("th1", "forum", "post-1", 1000),
    )
    conn.executemany(
        "INSERT INTO tickets"
        " (id, code, thread_id, title, status, dedup_key, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("tk1", "T-1", "th1", "修登录", "staged", "dedup-1", 1000, 1500),
            ("tk2", "T-2", None, "无线程", "open", None, 1100, 1600),
        ],
    )
    conn.executemany(
        "INSERT INTO assignments"
        " (ticket_id, worker_id, state, lease_until, result, updated_at)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        [
            ("tk1", "w1", "assigned", 2000, '{"ok":true}', 1400),
            ("tk1", "w2", "done", None, None, 1500),
        ],
    )
    conn.executemany(
        "INSERT INTO dispatches"
        " (id, ticket_id, node_id, worker_id, state, attempt, detail, updated_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("d1", "tk1", "n1", "w1", "pending", 1, '{"step":1}', 1400),
            ("d2", "tk1", "n1", "w1", "failed", 2, "not-json", 1550),
            ("d3", "tk2", "n2", None, "pending", 1, None, 1600),
        ],
    )
    conn.executemany(
        "INSERT INTO sessions (id, ticket_id, node_id, pinned, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        [
            ("s1", "tk1", "n1", 1, 1200, 1800),
            ("s2", "tk1", "n2", 0, 1300, 1900),
            ("s3", "tk2", "n1", 1, 1400, 1700),
        ],
    )
    conn.executemany(
        "INSERT INTO session_turns"
        " (session_id, turn_index, sha256, bytes, head_sha, branch, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            ("s1", 0, text_sha, text_size, "abc", "fix/login", 1500),
            ("s1", 1, MISSING_SHA, 4, None, None, 1600),
            ("s1", 2, bad_sha, bad_size, None, None, 1400),
            ("s3", 0, text_sha, text_size, None, None, 1650),
        ],
    )
    conn.executemany(
        "INSERT INTO events"
        " (id, origin, origin_id, thread_id, payload, verdict, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            ("e1", "forum", "o1", "th1", '{"msg":"hi"}', "record", 1000),
            ("e2", "forum", "o2", "th1", "not-json", "drop", 2000),
            ("e3", "chat", "o3", "th1", '{"msg":"later"}', "record", 3000),
            ("e4", "other", "o4", None, '{"msg":"mid"}', "record", 2500),
        ],
    )
    conn.executemany(
        "INSERT INTO nodes"
        " (id, name, host, platforms, peer_url, status, max_workers, last_seen)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("n1", "edge-a", "10.0.0.1", '["forum"]', "", "online", 3, 1800),
            ("n2", "edge-b", "10.0.0.2", "not-json", "", "offline", 1, 900),
        ],
    )
    conn.execute(
        "INSERT INTO audit (actor, action, detail, created_at) VALUES (?, ?, ?, ?)",
        ("human", "review", '{"ticket_id":"tk1"}', 1700),
    )
    return {"text": text_sha, "bad": bad_sha}


@pytest.fixture()
def client(tmp_path):
    conn = db.connect(tmp_path / "ui.db")
    db.migrate(conn)
    blob_root = tmp_path / "blobs"
    shas = seed(conn, blob_root)
    app = FastAPI()
    app.include_router(
        create_ui_router(conn, blob_root, auth_stub, test_envs={"forum": "https://t.example"})
    )
    with TestClient(app) as test_client:
        test_client.shas = shas
        test_client.conn = conn
        yield test_client
    conn.close()


def test_missing_token_is_unauthorized(client):
    paths = (
        "/internal/tickets/tk1",
        "/internal/events",
        "/internal/threads/th1",
        "/internal/sessions",
        "/internal/sessions/s1/turns",
        "/internal/topology",
    )
    for path in paths:
        assert client.get(path).status_code == 401
    assert client.get("/internal/events", headers=headers("nope")).status_code == 401


def test_ticket_detail_and_missing(client):
    missing = client.get("/internal/tickets/missing", headers=headers())
    assert missing.status_code == 404

    body = client.get("/internal/tickets/tk1", headers=headers()).json()
    assert body["ticket"] == {
        "id": "tk1",
        "code": "T-1",
        "thread_id": "th1",
        "title": "修登录",
        "summary": "",
        "status": "staged",
        "dedup_key": "dedup-1",
        "created_at": 1000,
        "updated_at": 1500,
    }
    assert isinstance(body["ticket"]["created_at"], (int, float))
    assert body["thread"] == {
        "id": "th1",
        "origin": "forum",
        "origin_id": "post-1",
        "created_at": 1000,
    }
    assert body["assignments"] == [
        {
            "ticket_id": "tk1",
            "worker_id": "w2",
            "state": "done",
            "lease_until": None,
            "result": None,
            "updated_at": 1500,
        },
        {
            "ticket_id": "tk1",
            "worker_id": "w1",
            "state": "assigned",
            "lease_until": 2000,
            "result": '{"ok":true}',
            "updated_at": 1400,
        },
    ]
    assert body["dispatches"][0]["detail"] == "not-json"
    assert body["dispatches"][1]["detail"] == {"step": 1}
    assert body["dispatches"][1]["platform"] == ""
    assert body["dispatches"][1]["pr_base"] == ""
    assert body["test_envs"] == {"forum": "https://t.example"}
    assert [item["id"] for item in body["sessions"]] == ["s2", "s1"]
    assert body["sessions"][0]["turn_count"] == 0
    assert body["sessions"][1]["turn_count"] == 3
    assert "last_turn_at" not in body["sessions"][0]

    orphan = client.get("/internal/tickets/tk2", headers=headers()).json()
    assert orphan["thread"] is None
    assert orphan["dispatches"][0]["detail"] is None


def test_events_parse_payload_and_since(client):
    listed = client.get("/internal/events", headers=headers()).json()["events"]
    assert [item["id"] for item in listed] == ["e3", "e4", "e2", "e1"]
    assert listed[0]["payload"] == {"msg": "later"}
    assert listed[2]["payload"] == "not-json"

    since = client.get("/internal/events", params={"since": 2000}, headers=headers()).json()
    assert [item["id"] for item in since["events"]] == ["e3", "e4"]

    dropped = client.get("/internal/events", params={"verdict": "drop"}, headers=headers()).json()
    assert [item["id"] for item in dropped["events"]] == ["e2"]

    chat = client.get("/internal/events", params={"origin": "chat"}, headers=headers()).json()
    assert [item["id"] for item in chat["events"]] == ["e3"]

    one = client.get("/internal/events", params={"limit": 1}, headers=headers()).json()
    assert [item["id"] for item in one["events"]] == ["e3"]


def test_events_limit_caps_at_500(client):
    client.conn.executemany(
        "INSERT INTO events (id, origin, origin_id, thread_id, payload, verdict, created_at)"
        " VALUES (?, 'bulk', ?, NULL, '{}', 'record', ?)",
        [(f"bulk-e{i}", f"b{i}", 10_000 + i) for i in range(520)],
    )
    body = client.get("/internal/events", params={"limit": 1000}, headers=headers()).json()
    assert len(body["events"]) == 500
    assert body["events"][0]["id"] == "bulk-e519"
    assert body["events"][0]["payload"] == {}


def test_thread_detail_and_missing(client):
    assert client.get("/internal/threads/missing", headers=headers()).status_code == 404
    body = client.get("/internal/threads/th1", headers=headers()).json()
    assert body["thread"]["id"] == "th1"
    assert [item["id"] for item in body["events"]] == ["e1", "e2", "e3"]
    assert body["events"][0]["payload"] == {"msg": "hi"}
    assert [item["id"] for item in body["tickets"]] == ["tk1"]


def test_sessions_aggregate_and_turns_text(client):
    listed = client.get("/internal/sessions", headers=headers()).json()["sessions"]
    by_id = {item["id"]: item for item in listed}
    assert [item["id"] for item in listed] == ["s2", "s1", "s3"]
    assert by_id["s1"]["turn_count"] == 3
    assert by_id["s1"]["last_turn_at"] == 1600
    assert by_id["s2"]["turn_count"] == 0
    assert by_id["s2"]["last_turn_at"] is None

    filtered = client.get(
        "/internal/sessions",
        params={"node_id": "n1", "ticket_id": "tk1"},
        headers=headers(),
    ).json()
    assert [item["id"] for item in filtered["sessions"]] == ["s1"]

    bare = client.get("/internal/sessions/s1/turns", headers=headers()).json()
    assert bare["session"]["id"] == "s1"
    assert bare["session"]["turn_count"] == 3
    assert bare["session"]["last_turn_at"] == 1600
    assert [item["turn_index"] for item in bare["turns"]] == [0, 1, 2]
    assert all("text" not in item for item in bare["turns"])
    assert bare["turns"][0]["head_sha"] == "abc"
    assert bare["turns"][0]["branch"] == "fix/login"

    with_text = client.get(
        "/internal/sessions/s1/turns",
        params={"include_text": "true"},
        headers=headers(),
    ).json()["turns"]
    assert with_text[0]["text"] == TURN_TEXT
    assert with_text[0]["sha256"] == client.shas["text"]
    assert "text" not in with_text[1]
    assert with_text[2]["text"] == "\ufffd\ufffd"

    as_one = client.get(
        "/internal/sessions/s1/turns",
        params={"include_text": "1"},
        headers=headers(),
    ).json()["turns"]
    assert as_one[0]["text"] == TURN_TEXT

    assert client.get("/internal/sessions/missing/turns", headers=headers()).status_code == 404


def test_topology_has_five_keys(client):
    body = client.get("/internal/topology", headers=headers()).json()
    assert set(body) == {"nodes", "tickets", "dispatches", "sessions"}
    assert [item["id"] for item in body["nodes"]] == ["n1", "n2"]
    assert body["nodes"][0]["platforms"] == '["forum"]'
    assert set(body["nodes"][0]) == {
        "id",
        "name",
        "host",
        "platforms",
        "status",
        "max_workers",
        "last_seen",
    }
    assert {item["id"] for item in body["tickets"]} == {"tk1", "tk2"}
    parsed = next(item for item in body["dispatches"] if item["id"] == "d1")
    assert parsed["detail"] == {"step": 1}
    assert [item["id"] for item in body["sessions"]] == ["s2", "s1", "s3"]
    assert body["sessions"][0]["last_turn_at"] is None
    assert body["sessions"][1]["last_turn_at"] == 1600


def test_topology_windows(client):
    extra = client.conn
    extra.executemany(
        "INSERT INTO tickets"
        " (id, code, thread_id, title, status, dedup_key, created_at, updated_at)"
        " VALUES (?, ?, NULL, '', 'staged', NULL, 1, ?)",
        [(f"bulk-t{i}", f"B-{i}", 20_000 + i) for i in range(210)],
    )
    extra.executemany(
        "INSERT INTO dispatches"
        " (id, ticket_id, node_id, worker_id, state, attempt, detail, updated_at)"
        " VALUES (?, 'tk1', 'n1', NULL, 'pending', 1, '{}', ?)",
        [(f"bulk-d{i}", 30_000 + i) for i in range(510)],
    )
    extra.executemany(
        "INSERT INTO sessions (id, ticket_id, node_id, pinned, created_at, updated_at)"
        " VALUES (?, NULL, 'n1', 1, 1, ?)",
        [(f"bulk-s{i}", 40_000 + i) for i in range(510)],
    )
    body = client.get("/internal/topology", headers=headers()).json()
    assert len(body["tickets"]) == 200
    assert body["tickets"][0]["id"] == "bulk-t209"
    assert "tk1" not in {item["id"] for item in body["tickets"]}
    assert len(body["dispatches"]) == 500
    assert body["dispatches"][0]["id"] == "bulk-d509"
    assert len(body["sessions"]) == 500
    assert "s1" not in {item["id"] for item in body["sessions"]}

