"""可观测性：Prometheus 指标、进程内日志与 JSON 日志格式。"""

from __future__ import annotations

import io
import json
import logging

import pytest
from helpers import machine

from skein import logbuf


@pytest.fixture(autouse=True)
def fresh_log_buffer(monkeypatch):
    """每个用例都从一个空的环形缓冲开始，且不沿用别的用例装过的 handler。"""
    monkeypatch.setattr(logbuf, "_installed", False)
    monkeypatch.setattr(logbuf, "_records", logbuf.deque(maxlen=logbuf.MAX_RECORDS))
    root = logging.getLogger()
    handlers = list(root.handlers)
    level = root.level
    yield
    root.handlers = handlers
    root.setLevel(level)


def test_metrics_are_counts_only(rigs):
    rig = rigs()
    with rig.client as client:
        client.post(
            "/internal/tickets",
            json={"title": "这个标题不该出现在指标里"},
            headers=machine(),
        )
        response = client.get("/metrics")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    text = response.text
    assert 'skein_tickets{status="staged"} 1' in text
    assert "# TYPE skein_nodes gauge" in text  # 一个节点都没有：只有说明，没有样本
    assert "skein_uptime_seconds " in text
    assert "skein_events_total 0" in text
    assert "这个标题" not in text


def test_metrics_reflect_node_and_dispatch_state(rigs):
    rig = rigs()
    now = rig.conn.execute("SELECT ?", (0,)).fetchone()[0]
    rig.conn.execute(
        "INSERT INTO nodes (id, name, status, max_workers, last_seen)"
        " VALUES ('n1', 'node', 'online', 2, ?)",
        (now,),
    )
    rig.conn.execute(
        "INSERT INTO tickets (id, code, title, status, created_at, updated_at)"
        " VALUES ('t1', 'SK-1', 'x', 'assigned', ?, ?)",
        (now, now),
    )
    rig.conn.execute(
        "INSERT INTO dispatches (id, ticket_id, node_id, state, updated_at)"
        " VALUES ('d1', 't1', 'n1', 'dispatched', ?)",
        (now,),
    )
    text = rig.client.get("/metrics").text
    assert 'skein_nodes{status="online"} 1' in text
    assert 'skein_tickets{status="assigned"} 1' in text
    assert 'skein_dispatches{state="dispatched"} 1' in text


def test_logs_endpoint_needs_read_and_returns_recent_records(rigs):
    rig = rigs()
    logbuf.install()
    logging.getLogger("skein.test").warning("hello %s", "world")
    assert rig.client.get("/internal/logs").status_code == 401
    body = rig.client.get("/internal/logs", headers=machine()).json()
    assert any(
        record["message"] == "hello world" and record["component"] == "skein.test"
        for record in body["records"]
    )
    filtered = rig.client.get(
        "/internal/logs?component=skein.test&level=warning", headers=machine()
    ).json()
    assert [record["level"] for record in filtered["records"]] == ["warning"]


def test_member_can_read_logs_but_outsiders_cannot(rigs):
    from helpers import MEMBER, signed_in, standard_provider

    rig = rigs(standard_provider())
    rig.directory.create(
        email=MEMBER.email, name=MEMBER.name, role="member", provider="fake", subject=MEMBER.sub
    )
    assert rig.client.get("/internal/logs", headers=signed_in("u2")).status_code == 200
    assert rig.client.get("/internal/logs", headers=signed_in("u3")).status_code == 403


def test_json_log_format_emits_one_object_per_line():
    stream = io.StringIO()
    logbuf.install(fmt="json", stream=stream)
    logging.getLogger("skein.json").info("hello")
    line = stream.getvalue().strip()
    record = json.loads(line)
    assert record["level"] == "info"
    assert record["component"] == "skein.json"
    assert record["message"] == "hello"
    assert record["at"].endswith("+00:00")  # UTC，带毫秒
    # 同一行也进了环形缓冲，/internal/logs 看得到
    assert any(item["message"] == "hello" for item in logbuf.recent())


def test_text_log_format_keeps_the_ring_only():
    stream = io.StringIO()
    logbuf.install(fmt="text", stream=stream)
    logging.getLogger("skein.text").info("hello")
    assert stream.getvalue() == ""  # 文本格式交给进程原本的输出，这里不接管
    assert any(item["component"] == "skein.text" for item in logbuf.recent())
