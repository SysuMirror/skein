import base64

import pytest
from fastapi.testclient import TestClient

from skein import db
from skein.api import create_app

TOKEN = "test-token"


@pytest.fixture()
def client(tmp_path):
    conn = db.connect(tmp_path / "api.db")
    db.migrate(conn)
    app = create_app(conn, str(tmp_path / "blobs"), TOKEN)
    with TestClient(app) as c:
        yield c
    conn.close()


def auth(token=TOKEN):
    return {"X-Skein-Token": token}


def test_health_needs_no_auth(client):
    assert client.get("/health/live").json() == {"status": "ok"}


def test_turn_requires_token(client):
    response = client.post("/internal/worker/turn", json={"session_id": "s", "turn_index": 0, "data": ""})
    assert response.status_code == 401


def test_turn_upload_and_conflict(client):
    body = {"session_id": "s1", "turn_index": 0, "data": base64.b64encode(b"one").decode()}
    first = client.post("/internal/worker/turn", json=body, headers=auth())
    assert first.status_code == 200
    again = client.post("/internal/worker/turn", json=body, headers=auth())
    assert again.status_code == 200
    other = dict(body, data=base64.b64encode(b"two").decode())
    assert client.post("/internal/worker/turn", json=other, headers=auth()).status_code == 409


def test_turn_rejects_bad_base64(client):
    body = {"session_id": "s2", "turn_index": 0, "data": "***"}
    assert client.post("/internal/worker/turn", json=body, headers=auth()).status_code == 400


def test_summary_requires_auth(client):
    assert client.get("/internal/summary").status_code == 401


def test_a_websocket_implementation_is_installed():
    """uvicorn 只带 HTTP；节点通道是 WebSocket，必须有一个 WS 实现被装上。"""
    import importlib.util

    assert importlib.util.find_spec("websockets") or importlib.util.find_spec("wsproto")
