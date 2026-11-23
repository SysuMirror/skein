from fastapi.testclient import TestClient

from skein import db, llm_trace
from skein.api import create_app

TOKEN = "trace-token"


def test_record_calls_and_sessions(tmp_path):
    conn = db.connect(tmp_path / "trace.db")
    db.migrate(conn)
    llm_trace.record(
        conn,
        session_id="s1",
        actor="u1",
        source="chat",
        model="m1",
        prompt="p1",
        response="r1",
        prompt_tokens=3,
        completion_tokens=5,
        latency_ms=10,
    )
    llm_trace.record(
        conn,
        session_id="s1",
        actor="u1",
        source="chat",
        model="m1",
        prompt="p2",
        response="r2",
        prompt_tokens=1,
        completion_tokens=2,
    )
    llm_trace.record(
        conn,
        session_id="s2",
        actor="u2",
        source="ops",
        model="m2",
        prompt="p3",
        response="r3",
    )
    assert len(llm_trace.calls(conn, session_id="s1")) == 2
    assert len(llm_trace.calls(conn, source="ops")) == 1
    sessions = {row["session_id"]: row for row in llm_trace.sessions(conn)}
    assert sessions["s1"]["calls"] == 2
    assert sessions["s1"]["tokens"] == 11
    assert sessions["s2"]["source"] == "ops"
    conn.close()


def test_llm_api_requires_token_and_lists(tmp_path):
    conn = db.connect(tmp_path / "api.db")
    db.migrate(conn)
    app = create_app(conn, str(tmp_path / "b"), TOKEN)
    with TestClient(app) as client:
        assert client.get("/internal/llm/sessions").status_code == 401
        created = client.post(
            "/internal/llm/calls",
            headers={"X-Skein-Token": TOKEN},
            json={
                "session_id": "s1",
                "actor": "u1",
                "source": "chat",
                "model": "m1",
                "prompt": "hello",
                "response": "world",
                "prompt_tokens": 2,
                "completion_tokens": 3,
                "latency_ms": 7,
            },
        )
        assert created.status_code == 200
        sessions = client.get(
            "/internal/llm/sessions", headers={"X-Skein-Token": TOKEN}
        ).json()
        assert sessions["sessions"][0]["session_id"] == "s1"
        calls = client.get(
            "/internal/llm/calls?session_id=s1", headers={"X-Skein-Token": TOKEN}
        ).json()
        assert calls["calls"][0]["prompt"] == "hello"
    conn.close()


