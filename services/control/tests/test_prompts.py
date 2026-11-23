import pytest

from skein import db, prompts


@pytest.fixture()
def conn(tmp_path):
    c = db.connect(tmp_path / "prompts.db")
    db.migrate(c)
    yield c
    c.close()


def test_non_editable_part_is_rejected(conn):
    with pytest.raises(prompts.PromptError):
        prompts.propose(conn, "runtime/orchestrator", "x", 0, "watchdog")


def test_base_revision_must_match(conn):
    prompts.propose(conn, "runtime/worker", "第一版", 0, "panel")
    prompts.promote(conn, "runtime/worker")
    with pytest.raises(prompts.PromptConflict):
        prompts.propose(conn, "runtime/worker", "第二版", 0, "watchdog")


def test_large_change_is_rejected(conn):
    with pytest.raises(prompts.PromptError):
        prompts.propose(conn, "runtime/worker", "字" * 3000, 0, "watchdog")


def test_propose_sets_canary_not_stable(conn):
    revision = prompts.propose(conn, "runtime/work", "新提示", 0, "watchdog", "signal-1")
    state = prompts.state(conn, "runtime/work")
    assert state["stable_revision"] == 0
    assert state["canary_revision"] == revision
    assert prompts.current(conn, "runtime/work", "stable") == ""
    assert prompts.current(conn, "runtime/work", "canary") == "新提示"


def test_promote_moves_stable(conn):
    prompts.propose(conn, "runtime/work", "新提示", 0, "watchdog")
    revision = prompts.promote(conn, "runtime/work")
    state = prompts.state(conn, "runtime/work")
    assert state["stable_revision"] == revision
    assert state["canary_revision"] is None
    assert prompts.current(conn, "runtime/work") == "新提示"


def test_rollback_twice_freezes(conn):
    prompts.propose(conn, "runtime/work", "A", 0, "watchdog")
    prompts.rollback(conn, "runtime/work")
    prompts.propose(conn, "runtime/work", "B", 0, "watchdog")
    prompts.rollback(conn, "runtime/work")
    state = prompts.state(conn, "runtime/work")
    assert state["frozen_until"] is not None
    with pytest.raises(prompts.PromptFrozen):
        prompts.propose(conn, "runtime/work", "C", 0, "watchdog")


def test_human_can_unfreeze(conn):
    prompts.propose(conn, "runtime/work", "A", 0, "watchdog")
    prompts.rollback(conn, "runtime/work")
    prompts.propose(conn, "runtime/work", "B", 0, "watchdog")
    prompts.rollback(conn, "runtime/work")
    prompts.unfreeze(conn, "runtime/work")
    assert prompts.state(conn, "runtime/work")["frozen_until"] is None
