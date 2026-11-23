import pytest

from skein import db
from skein.session_store import SessionStore, TurnConflict, blob_path, get_blob, put_blob


@pytest.fixture()
def store(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    db.migrate(conn)
    yield SessionStore(conn, tmp_path / "blobs")
    conn.close()


def test_blob_is_content_addressed(tmp_path):
    sha1, size1 = put_blob(tmp_path, b"hello")
    sha2, size2 = put_blob(tmp_path, b"hello")
    assert sha1 == sha2
    assert size1 == size2 == 5
    assert blob_path(tmp_path, sha1).exists()
    assert get_blob(tmp_path, sha1) == b"hello"


def test_append_turn_writes_blob_and_row(store, tmp_path):
    store.open("s1", ticket_id=None, node_id="n1")
    turn = store.append_turn("s1", 0, b'{"turn":0}', head_sha="abc", branch="fix/x")
    assert turn.turn_index == 0
    assert get_blob(tmp_path / "blobs", turn.sha256) == b'{"turn":0}'
    assert [t.turn_index for t in store.turns("s1")] == [0]


def test_same_turn_same_bytes_is_idempotent(store):
    store.open("s2")
    a = store.append_turn("s2", 0, b"same")
    b = store.append_turn("s2", 0, b"same")
    assert a == b
    assert len(store.turns("s2")) == 1


def test_same_turn_different_bytes_conflicts(store):
    store.open("s3")
    store.append_turn("s3", 0, b"one")
    with pytest.raises(TurnConflict):
        store.append_turn("s3", 0, b"two")
    assert len(store.turns("s3")) == 1


def test_turns_are_ordered(store):
    store.open("s4")
    store.append_turn("s4", 2, b"c")
    store.append_turn("s4", 0, b"a")
    store.append_turn("s4", 1, b"b")
    assert [t.turn_index for t in store.turns("s4")] == [0, 1, 2]


def test_pin_and_unpin(store):
    store.open("s5", node_id="n1")
    assert store.is_pinned("s5")
    store.unpin("s5")
    assert not store.is_pinned("s5")
    store.pin("s5", "n2")
    assert store.is_pinned("s5")
