import time

import pytest

from skein import db, prompts
from skein.watchdog_agent import observe


class FakeLLM:
    def __init__(self, response):
        self.response = response
        self.requests = []

    def complete(self, request):
        self.requests.append(request)
        return self.response


@pytest.fixture()
def conn(tmp_path):
    c = db.connect(tmp_path / "watchdog.db")
    db.migrate(c)
    yield c
    c.close()


def add_failed_run(conn, run_id="run-1"):
    now = time.time()
    conn.execute(
        "INSERT INTO runs (id, job, day, status, started_at, finished_at, detail)"
        " VALUES (?, 'daily', '2026-11-19', 'failed', ?, ?, 'exit 2')",
        (run_id, now - 10, now - 5),
    )


def test_observe_dedups_signals(conn):
    add_failed_run(conn)
    first = observe(conn)
    assert [item["kind"] for item in first] == ["run_failed"]
    assert observe(conn) == []
    audit = conn.execute(
        "SELECT COUNT(*) FROM audit WHERE actor = 'watchdog'"
    ).fetchone()[0]
    assert audit == 1


def test_auto_propose_enters_canary(conn):
    revision = prompts.propose(conn, "runtime/worker", "旧提示词", 0, "panel")
    prompts.promote(conn, "runtime/worker")
    assert revision == 1
    add_failed_run(conn)
    llm = FakeLLM({"text": "旧提示词，补充：失败先查日志再重试。"})
    observe(conn, auto_propose=True, llm=llm)
    pointer = conn.execute(
        "SELECT stable_revision, canary_revision FROM prompt_pointers WHERE part = 'runtime/worker'"
    ).fetchone()
    assert pointer["stable_revision"] >= 1
    assert pointer["canary_revision"] is not None


def test_auto_propose_does_not_write_without_llm(conn):
    prompts.propose(conn, "runtime/worker", "旧提示词", 0, "panel")
    prompts.promote(conn, "runtime/worker")
    add_failed_run(conn)
    observe(conn, auto_propose=True, llm=None)
    pointer = conn.execute(
        "SELECT canary_revision FROM prompt_pointers WHERE part = 'runtime/worker'"
    ).fetchone()
    assert pointer["canary_revision"] is None
