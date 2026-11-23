"""账号目录：建号、改角色、口令、停用、挂身份的规则，以及「最后一个 owner」的保护。"""

from __future__ import annotations

import pytest
from helpers import A_PASSWORD

from skein import db
from skein.accounts import Directory


@pytest.fixture()
def directory(tmp_path):
    conn = db.connect(tmp_path / "accounts.db")
    db.migrate(conn)
    yield Directory(conn)
    conn.close()


def test_create_local_account_and_find_it_by_email(directory):
    alice = directory.create(email="Alice@Example.com", name="Alice", role="admin", password=A_PASSWORD)
    assert alice.sub.startswith("usr_") and len(alice.sub) == 16
    assert (alice.email, alice.role, alice.has_password, alice.disabled) == (
        "alice@example.com",
        "admin",
        True,
        False,
    )
    assert directory.find("alice@example.com").sub == alice.sub
    assert directory.find("ALICE@example.com ").sub == alice.sub
    assert directory.find(alice.sub).sub == alice.sub
    assert directory.local_account("alice@example.com").sub == alice.sub
    identities = directory.identities(alice.sub)
    assert [(row["provider"], row["subject"], row["last_login_at"]) for row in identities] == [
        ("local", "alice@example.com", None)
    ]


def test_the_stored_password_is_a_hash(directory):
    directory.create(email="alice@example.com", role="member", password=A_PASSWORD)
    stored = directory.password_hash(directory.find("alice@example.com").sub)
    assert stored.startswith("scrypt$") and A_PASSWORD not in stored


def test_two_local_accounts_cannot_share_an_email(directory):
    directory.create(email="alice@example.com", role="member", password=A_PASSWORD)
    with pytest.raises(ValueError, match="already exists"):
        directory.create(email="Alice@example.com", role="member", password=A_PASSWORD)
    assert len(directory.list()) == 1


def test_external_identity_is_reserved_without_a_password(directory):
    bob = directory.create(email="bob@example.com", role="member", provider="unisso", subject="42")
    assert bob.has_password is False
    assert [row["provider"] for row in directory.identities(bob.sub)] == ["unisso"]
    assert directory.local_account("bob@example.com") is None
    with pytest.raises(ValueError, match="already linked"):
        directory.create(email="bob2@example.com", role="member", provider="unisso", subject="42")


def test_provider_and_subject_go_together(directory):
    with pytest.raises(ValueError, match="go together"):
        directory.create(email="x@example.com", role="member", provider="unisso")
    with pytest.raises(ValueError, match="go together"):
        directory.create(email="x@example.com", role="member", subject="42")


def test_bad_role_is_refused(directory):
    with pytest.raises(ValueError, match="role must be one of"):
        directory.create(email="x@example.com", role="root")


def test_set_role_and_audit(directory, tmp_path):
    conn = db.connect(tmp_path / "accounts.db")
    try:
        user = directory.create(email="alice@example.com", role="member", password=A_PASSWORD)
        assert directory.set_role(user.sub, "owner").role == "owner"
        row = conn.execute("SELECT actor, action, detail FROM audit ORDER BY id DESC LIMIT 1").fetchone()
        assert (row["actor"], row["action"]) == ("skein", "user.role")
        assert '"from": "member"' in row["detail"] and '"to": "owner"' in row["detail"]
        with pytest.raises(LookupError, match="no such user"):
            directory.set_role("usr_nope", "admin")
    finally:
        conn.close()


def test_the_last_owner_cannot_be_demoted_or_disabled(directory):
    owner = directory.create(email="owner@example.com", role="owner", password=A_PASSWORD)
    with pytest.raises(ValueError, match="last owner"):
        directory.set_role(owner.sub, "admin")
    with pytest.raises(ValueError, match="last owner"):
        directory.set_disabled(owner.sub, True)
    assert directory.get(owner.sub).role == "owner"
    # 再有一个 owner 之后就可以了
    directory.create(email="owner2@example.com", role="owner", password=A_PASSWORD)
    assert directory.set_role(owner.sub, "member").role == "member"
    assert directory.set_disabled(owner.sub, True).disabled is True
    assert directory.set_disabled(owner.sub, False).disabled is False


def test_a_disabled_owner_does_not_count_as_an_owner(directory):
    first = directory.create(email="a@example.com", role="owner", password=A_PASSWORD)
    directory.create(email="b@example.com", role="owner", password=A_PASSWORD)
    directory.set_disabled(first.sub, True)
    with pytest.raises(ValueError, match="last owner"):
        directory.set_disabled("b@example.com", True)


def test_password_reset_only_applies_to_local_accounts(directory):
    directory.create(email="alice@example.com", role="member", password=A_PASSWORD)
    bob = directory.create(email="bob@example.com", role="member", provider="unisso", subject="1")
    with pytest.raises(ValueError, match="not a local account"):
        directory.set_password(bob.sub, A_PASSWORD)
    # 外部账号的邮箱不唯一，不能拿来定位：只能用 sub，报错里会说清楚
    with pytest.raises(LookupError, match="no local account"):
        directory.set_password("bob@example.com", A_PASSWORD)
    with pytest.raises(ValueError, match="at least"):
        directory.set_password("alice@example.com", "short")


def test_link_attaches_an_identity_explicitly_and_never_twice(directory):
    alice = directory.create(email="alice@example.com", role="member", password=A_PASSWORD)
    directory.link(alice.sub, "unisso", "42", actor="owner")
    directory.link(alice.sub, "oidc", "acct:x", actor="owner")
    assert [row["provider"] for row in directory.identities(alice.sub)] == ["local", "oidc", "unisso"]
    with pytest.raises(ValueError, match="already linked"):
        directory.link(alice.sub, "unisso", "42")
    with pytest.raises(ValueError, match="required"):
        directory.link(alice.sub, "", "42")
    with pytest.raises(ValueError, match="not an account"):
        directory.link(alice.sub, "token", "token-holder")
