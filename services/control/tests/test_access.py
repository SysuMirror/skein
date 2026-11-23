"""权限矩阵：每个格子都钉死，文档里的矩阵与代码同源。"""

from pathlib import Path

import pytest

from skein import access

ALL_CONSOLE = tuple(access.ROLE_GRANTS)


def test_roles_are_ordered_member_subset_admin_subset_owner():
    assert access.rank(access.MEMBER) < access.rank(access.ADMIN) < access.rank(access.OWNER)
    assert access.rank(None) < access.rank(access.MEMBER)
    assert access.rank("nonsense") < 0


@pytest.mark.parametrize("permission", ALL_CONSOLE)
def test_higher_roles_inherit_every_lower_permission(permission):
    minimum = access.min_role(permission)
    for role in access.ROLES:
        expected = access.rank(role) >= access.rank(minimum)
        assert access.allows(access.SESSION, role, permission) is expected


def test_member_matrix():
    yes = {access.READ, access.TICKET_FILE}
    for permission in ALL_CONSOLE:
        assert access.allows(access.SESSION, access.MEMBER, permission) is (permission in yes)


def test_admin_matrix():
    no = {access.USER_MANAGE}
    for permission in ALL_CONSOLE:
        assert access.allows(access.SESSION, access.ADMIN, permission) is (permission not in no)


def test_owner_matrix_has_everything():
    for permission in ALL_CONSOLE:
        assert access.allows(access.SESSION, access.OWNER, permission)


def test_machine_has_all_but_human_only():
    for permission in ALL_CONSOLE:
        expected = permission not in access.HUMAN_ONLY
        assert access.allows(access.MACHINE, None, permission) is expected
    assert access.HUMAN_ONLY == {access.AGENT_CHAT}


def test_unknown_kind_or_permission_is_denied():
    assert access.allows("root", access.OWNER, access.READ) is False
    assert access.allows(access.SESSION, access.OWNER, "god.mode") is False
    assert access.allows(access.MACHINE, None, access.API_TICKET_CREATE) is False


def test_assistant_only_reaches_the_public_api():
    for permission in (access.API_TICKET_CREATE, access.API_TICKET_READ, access.API_TRACE_WRITE):
        assert access.allows(access.ASSISTANT, None, permission)
    for permission in ALL_CONSOLE:
        assert not access.allows(access.ASSISTANT, None, permission)
    # 机器与人也不能走助手的权限
    assert not access.allows(access.MACHINE, None, access.API_TICKET_CREATE)
    assert not access.allows(access.SESSION, access.OWNER, access.API_TICKET_CREATE)


def test_check_raises_for_denied():
    with pytest.raises(access.NotAllowed):
        access.check(access.SESSION, access.MEMBER, access.TICKET_OPERATE)
    access.check(access.SESSION, access.MEMBER, access.READ)


def test_every_permission_has_a_description():
    assert set(access.DESCRIPTIONS) == set(access.ROLE_GRANTS)


def test_matrix_table_lists_every_permission():
    table = access.matrix_table()
    assert table.count("\n") == len(access.ROLE_GRANTS) + 1
    assert "| `read` | yes | yes | yes | yes |" in table
    assert "| `agent.chat` | no | yes | yes | signed-in only |" in table
    assert "| `user.manage` | no | no | yes | yes |" in table


def test_doc_matrix_matches_code():
    """docs/authentication.md 里的矩阵是这张表生成的：标记之间必须与代码一字不差。"""
    doc = Path(__file__).resolve().parents[3] / "docs" / "authentication.md"
    text = doc.read_text(encoding="utf-8")
    begin = text.index("<!-- matrix:start -->") + len("<!-- matrix:start -->")
    end = text.index("<!-- matrix:end -->")
    assert text[begin:end].strip() == access.matrix_table()
