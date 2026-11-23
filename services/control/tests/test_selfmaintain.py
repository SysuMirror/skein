import time

import pytest
from fastapi.testclient import TestClient

from skein import db, store
from skein.api import create_app
from skein.watchdog_agent import (
    auto_count_today,
    daily_review,
    signal_ticket,
    ticket_signals,
)

TOKEN = "sm-token"


class FakeClient:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def create(self, **kwargs) -> dict:
        self.calls.append(kwargs)
        return {"ticket_id": "t-x", "created": True, "dispatch": None}


@pytest.fixture()
def conn(tmp_path):
    c = db.connect(tmp_path / "sm.db")
    db.migrate(c)
    yield c
    c.close()


@pytest.fixture()
def client(conn, tmp_path):
    app = create_app(conn, str(tmp_path / "blobs"), TOKEN)
    with TestClient(app) as test:
        test.conn = conn
        yield test


def auth() -> dict[str, str]:
    return {"X-Skein-Token": TOKEN}


def test_create_ticket_is_idempotent(client):
    body = {
        "title": "[自维护] 测试",
        "summary": "信号详情",
        "platform": "skein",
        "dedup_key": "auto:test:1",
        "dispatch": False,
    }
    first = client.post("/internal/tickets", json=body, headers=auth()).json()
    assert first["created"] is True
    second = client.post("/internal/tickets", json=body, headers=auth()).json()
    assert second["created"] is False
    assert second["ticket_id"] == first["ticket_id"]
    row = client.conn.execute(
        "SELECT title, summary, status FROM tickets WHERE id = ?", (first["ticket_id"],)
    ).fetchone()
    assert tuple(row) == ("[自维护] 测试", "信号详情", "staged")


def test_create_ticket_dispatch_without_node(client):
    body = {"title": "x", "platform": "skein", "dispatch": True, "dedup_key": "auto:test:2"}
    result = client.post("/internal/tickets", json=body, headers=auth()).json()
    # 没有在线节点、控制面也没配平台映射 → 明确要求平台而不是发空 repo
    assert result["dispatch"] == {
        "ok": False,
        "reason": "platform_required",
        "platforms": [],
    }
    status = client.conn.execute(
        "SELECT status FROM tickets WHERE id = ?", (result["ticket_id"],)
    ).fetchone()[0]
    assert status == "staged"


def test_create_ticket_requires_title(client):
    response = client.post("/internal/tickets", json={"title": "  "}, headers=auth())
    assert response.status_code == 400


def test_signal_ticket_text():
    title, summary = signal_ticket(
        {"kind": "run_failed", "job": "digest", "day": "2026-11-19", "id": "r1"}
    )
    assert "作业失败" in title and "digest" in title
    assert "digest" in summary and "定位根因" in summary
    node_title, _ = signal_ticket(
        {"kind": "node_offline_with_work", "node_id": "n1", "ticket_id": "t1", "id": "s1"}
    )
    assert "节点离线" in node_title


def test_ticket_signals_skips_existing_dedup(conn):
    store.create_ticket(conn, "old", None, "auto:signal:run_failed:1")
    client = FakeClient()
    result = ticket_signals(
        conn,
        client,
        [
            {"kind": "run_failed", "job": "a", "day": "d", "id": "1"},
            {"kind": "run_failed", "job": "b", "day": "d", "id": "2"},
        ],
        daily_cap=5,
        dispatch=False,
    )
    assert len(result) == 1
    assert client.calls[0]["dedup_key"] == "auto:signal:run_failed:2"
    assert client.calls[0]["platform"] == "skein"


def test_ticket_signals_respects_daily_cap(conn):
    now = time.time()
    for i in range(2):
        store.create_ticket(conn, f"auto-{i}", None, f"auto:signal:x:{i}")
    assert auto_count_today(conn, now) == 2
    client = FakeClient()
    ticket_signals(
        conn,
        client,
        [{"kind": "dispatch_timeout", "ticket_id": "t", "node_id": "n", "id": "z"}],
        daily_cap=2,
        now=now,
        dispatch=True,
    )
    assert client.calls == []


def test_daily_review_once_per_day(conn):
    client = FakeClient()
    now = time.mktime((2026, 11, 19, 9, 30, 0, 0, 0, -1))
    first = daily_review(conn, client, hour=9, dispatch=False, now=now)
    assert first is not None
    assert client.calls[0]["dedup_key"] == "auto:daily:2026-11-19"
    store.create_ticket(conn, "daily", None, "auto:daily:2026-11-19")
    assert daily_review(conn, client, hour=9, dispatch=False, now=now) is None
    assert daily_review(conn, client, hour=10, dispatch=False, now=now) is None
    assert len(client.calls) == 1


def test_daily_review_disabled(conn):
    assert daily_review(conn, FakeClient(), hour=-1, dispatch=False) is None
