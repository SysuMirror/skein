import asyncio

import pytest
from fastapi.testclient import TestClient

from skein import db
from skein.api import create_app
from skein.ui_api import StreamState, snapshot, stream_frames

TOKEN = "stream-token"


@pytest.fixture()
def client(tmp_path):
    conn = db.connect(tmp_path / "stream.db")
    db.migrate(conn)
    app = create_app(conn, str(tmp_path / "blobs"), TOKEN)
    with TestClient(app) as c:
        c.conn = conn
        yield c
    conn.close()


def auth():
    return {"X-Skein-Token": TOKEN}


def test_snapshot_has_every_section(client):
    snap = snapshot(client.conn)
    assert set(snap) == {"summary", "signals", "tickets", "topology", "events"}
    assert snap["summary"]["tickets"]["total"] == 0
    assert snap["tickets"] == []
    assert snap["events"] == []


def test_stream_requires_token(client):
    assert client.get("/internal/stream").status_code == 401


def test_stream_frames_emit_retry_then_snapshot(client):
    calls = {"n": 0}

    async def disconnected():
        calls["n"] += 1
        return calls["n"] > 1

    async def collect():
        return [
            frame
            async for frame in stream_frames(
                client.conn, is_disconnected=disconnected, interval=0.01
            )
        ]

    frames = asyncio.run(collect())
    assert frames[0] == "retry: 3000\n\n"
    assert frames[1].startswith("event: snapshot\ndata: {")


def test_stream_state_dedupes_and_pings():
    state = StreamState(interval=2.0, ping_after=15.0)
    snap = {
        "summary": {"tickets": {"total": 1}},
        "signals": [{"kind": "dispatch_timeout", "age_s": 5}],
        "tickets": [],
        "topology": {},
        "events": [],
    }
    assert state.frame(snap, 100.0).startswith("event: snapshot")
    assert state.frame(snap, 102.0) == ""

    drifting = dict(snap, signals=[{"kind": "dispatch_timeout", "age_s": 900}])
    assert state.frame(drifting, 104.0) == ""

    assert state.frame(snap, 116.0).startswith("event: ping")
    changed = dict(snap, summary={"tickets": {"total": 2}})
    assert state.frame(changed, 118.0).startswith("event: snapshot")
