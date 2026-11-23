import json
from dataclasses import FrozenInstanceError

import pytest

from skein import db
from skein.admission import (
    DEFAULT_POLICY,
    POLICIES_KEY,
    Decision,
    Event,
    SourcePolicy,
    Verdict,
    decide,
    load_policies,
    policy_for,
    policy_from_dict,
    policy_to_dict,
)
from skein.store import kv_set


def test_mention_is_processed():
    d = decide(Event(source="forum_comment", origin_id="1", text="帮我看下这个", mentioned=True))
    assert d.verdict is Verdict.PROCESS
    assert d.reason == "explicit_mention"


def test_short_mention_is_recorded_not_processed():
    d = decide(Event(source="forum_comment", origin_id="2", text="@", mentioned=True))
    assert d.verdict is Verdict.RECORD
    assert d.reason == "mention_too_short"


def test_allowed_followup_is_processed():
    d = decide(
        Event(
            source="forum_comment",
            origin_id="3",
            text="继续说说",
            in_allowlist=True,
            replies_to_agent=True,
        )
    )
    assert d.verdict is Verdict.PROCESS
    assert d.reason == "allowed_followup"


def test_followup_outside_allowlist_is_ignored():
    d = decide(
        Event(source="forum_comment", origin_id="4", text="继续说说", replies_to_agent=True)
    )
    assert d.verdict is Verdict.IGNORE


def test_allowlisted_unmentioned_is_only_recorded():
    d = decide(Event(source="forum_comment", origin_id="5", text="随便聊聊", in_allowlist=True))
    assert d.verdict is Verdict.RECORD
    assert d.reason == "allowlisted_unmentioned"


def test_allowlisted_can_stop_recording():
    policy = SourcePolicy(record_unmentioned=False)
    d = decide(Event(source="forum_comment", origin_id="6", text="随便聊聊", in_allowlist=True), policy)
    assert d.verdict is Verdict.IGNORE


def test_allowlisted_without_mention_requirement_is_processed():
    policy = SourcePolicy(require_mention=False)
    d = decide(Event(source="forum_comment", origin_id="6b", text="继续讲讲", in_allowlist=True), policy)
    assert d.verdict is Verdict.PROCESS
    assert d.reason == "allowlisted_no_mention_required"


def test_stranger_is_ignored():
    d = decide(Event(source="forum_comment", origin_id="7", text="路人发言"))
    assert d.verdict is Verdict.IGNORE


def test_disabled_source_is_ignored():
    d = decide(Event(source="rss", origin_id="8", text="x", mentioned=True), SourcePolicy(accept=False))
    assert d.verdict is Verdict.IGNORE


def test_decision_is_frozen():
    d = Decision(Verdict.IGNORE, "x")
    with pytest.raises(FrozenInstanceError):
        d.reason = "y"


@pytest.fixture()
def conn(tmp_path):
    c = db.connect(tmp_path / "admission.db")
    db.migrate(c)
    yield c
    c.close()


def _defaults():
    return {
        "accept": True,
        "require_mention": True,
        "record_unmentioned": True,
        "min_text_chars": 2,
    }


def test_policy_from_dict_fills_missing_and_ignores_bad_values():
    policy = policy_from_dict(
        {
            "accept": False,
            "require_mention": 0,
            "record_unmentioned": "yes",
            "min_text_chars": True,
            "extra": 1,
        }
    )
    assert policy == SourcePolicy(accept=False)
    assert policy_from_dict(["nope"]) == DEFAULT_POLICY
    assert policy_from_dict({"min_text_chars": 201}) == DEFAULT_POLICY
    assert policy_from_dict({"min_text_chars": -1}) == DEFAULT_POLICY
    assert policy_from_dict({"min_text_chars": 0}) == SourcePolicy(min_text_chars=0)


def test_policy_dict_round_trip():
    policy = SourcePolicy(
        accept=False, require_mention=False, record_unmentioned=False, min_text_chars=200
    )
    assert policy_from_dict(policy_to_dict(policy)) == policy


def test_load_policies_without_row_is_default(conn):
    assert load_policies(conn) == {"default": _defaults(), "sources": {}}
    assert policy_for(conn, "forum_comment") == DEFAULT_POLICY


def test_load_policies_repairs_partial_and_skips_garbage(conn):
    kv_set(
        conn,
        POLICIES_KEY,
        json.dumps(
            {
                "default": {"accept": 1, "min_text_chars": 9},
                "sources": {
                    "forum_post": {"require_mention": False, "min_text_chars": "2"},
                    "Nope": {"accept": False},
                    "ok-name": "x",
                    "a" * 65: {"accept": False},
                },
                "extra": True,
            }
        ),
    )
    loaded = load_policies(conn)
    assert loaded["default"]["accept"] is True
    assert loaded["default"]["min_text_chars"] == 9
    assert loaded["sources"] == {
        "forum_post": {
            "accept": True,
            "require_mention": False,
            "record_unmentioned": True,
            "min_text_chars": 2,
        }
    }
    assert policy_for(conn, "forum_post").require_mention is False
    assert policy_for(conn, "forum_post").accept is True
    assert policy_for(conn, "missing").min_text_chars == 9


def test_load_policies_ignores_bad_json(conn):
    kv_set(conn, POLICIES_KEY, "{")
    assert load_policies(conn) == {"default": _defaults(), "sources": {}}
    kv_set(conn, POLICIES_KEY, "[]")
    assert policy_for(conn, "forum_post") == DEFAULT_POLICY
