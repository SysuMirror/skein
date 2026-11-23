"""登录鉴权：三种登录方式、会话 Cookie、限速，以及机器令牌与人的边界。

docs/authentication.md 里描述的每一种模式，这里各有一组用例。
"""

from __future__ import annotations

import pytest
from helpers import (
    A_PASSWORD,
    ADMIN,
    MEMBER,
    OUTSIDER,
    PASSWORD,
    SUPER,
    TOKEN,
    FakeProvider,
    cookie_of,
    machine,
    signed_in,
    standard_provider,
)

from skein import db
from skein.access import ADMIN as ROLE_ADMIN
from skein.access import OWNER as ROLE_OWNER
from skein.accounts import NO_ACCESS, effective_role, role_from_claims
from skein.auth import Principal
from skein.auth_providers import ConfigError, LoginMethod, build, resolve_method
from skein.auth_providers.unisso import UniSSOProvider, UniSSOSettings
from skein.config import Config
from skein.sessions import SessionStore

ALL_PERMISSIONS = [
    "read",
    "ticket.file",
    "ticket.operate",
    "ops.ingest",
    "agent.chat",
    "assistant.manage",
    "user.read",
    "user.manage",
]


def login_as(rig, email: str, password: str = PASSWORD, **extra):
    return rig.client.post("/api/session/login", json={"email": email, "password": password, **extra})


# ================= 令牌模式（零配置） =================


def test_unconfigured_deployment_is_token_mode(rigs):
    assert rigs().client.get("/api/session").json() == {"mode": "token", "provider": "token"}


def test_machine_token_still_opens_the_internal_api(rigs):
    client = rigs().client
    assert client.get("/internal/summary").status_code == 401
    refused = client.get("/internal/summary", headers=machine("wrong"))
    assert (refused.status_code, refused.json()["detail"]) == (401, "invalid_token")
    assert client.get("/internal/summary").json()["detail"] == "no_sso"
    assert client.get("/internal/summary", headers=machine()).status_code == 200


def test_token_login_trades_the_master_token_for_a_session_cookie(rigs):
    """登录页填主令牌，换回一个只属于这个浏览器的会话；之后请求不必再带令牌。"""
    rig = rigs()
    with rig.client as client:
        wrong = client.post("/api/session/login", json={"token": "nope"})
        assert (wrong.status_code, wrong.json()["detail"]) == (401, "invalid_token")
        missing = client.post("/api/session/login", json={"email": "a@example.com", "password": "x"})
        assert (missing.status_code, missing.json()["detail"]) == (400, "token_required")

        response = client.post("/api/session/login", json={"token": TOKEN})
        assert response.status_code == 200
        header = response.headers["set-cookie"]
        assert header.startswith("skein_session=")
        for attribute in ("HttpOnly", "SameSite=lax", "Path=/"):
            assert attribute in header
        assert "Domain=" not in header  # host-only：不依赖任何真实的 Cookie 域
        body = response.json()
        assert body["mode"] == "token"
        assert body["user"]["sub"] == "token-holder"
        assert body["user"]["role"] == "owner"

        session = cookie_of(response, "skein_session")
        probe = client.get("/api/session", headers=session).json()
        assert probe["user"]["role"] == "owner"
        assert probe["permissions"] == ALL_PERMISSIONS
        assert client.get("/internal/summary", headers=session).status_code == 200


def test_token_login_cookie_is_secure_unless_plain_http_is_configured(rigs):
    secure = rigs(cookie_secure=True).client.post("/api/session/login", json={"token": TOKEN})
    assert "Secure" in secure.headers["set-cookie"]
    plain = rigs(cookie_secure=False).client.post("/api/session/login", json={"token": TOKEN})
    assert "Secure" not in plain.headers["set-cookie"]


def test_agent_chat_needs_a_signed_in_person(rigs):
    """Agent 的操作要留痕到人：主令牌当请求头是机器，机器不能替人说话。"""
    rig = rigs(llm_base="http://llm.test", llm_key="key")
    with rig.client as client:
        refused = client.post("/internal/agent/chat", json={"message": "你好"}, headers=machine())
        assert (refused.status_code, refused.json()["detail"]) == (403, "sign_in_required")

        session = cookie_of(client.post("/api/session/login", json={"token": TOKEN}))
        chat = client.post(
            "/internal/agent/chat", json={"thread_id": "th-1", "message": "你好"}, headers=session
        )
        # 这里配了模型地址但连不上：回复是「模型调用失败」，200；关键是已经过了鉴权
        assert chat.status_code == 200
        thread = client.get("/internal/agent/thread/th-1", headers=session)
        assert thread.json()["actor"] == "token-holder"


def test_logout_ends_the_session(rigs):
    rig = rigs()
    with rig.client as client:
        session = cookie_of(client.post("/api/session/login", json={"token": TOKEN}))
        assert client.get("/internal/summary", headers=session).status_code == 200
        logout = client.post("/api/session/logout", headers=session)
        assert logout.json() == {"ok": True}
        assert "skein_session=" in logout.headers["set-cookie"]
        # 同一个 Cookie 值再也换不回身份：会话行已删，不只是让浏览器忘掉
        assert client.get("/internal/summary", headers=session).status_code == 401
        assert client.post("/api/session/logout").json() == {"ok": True}


def test_cross_site_writes_with_a_session_cookie_are_refused(rigs):
    """会话 Cookie 只许同站使用：浏览器标明是跨站发起的写请求直接拒。"""
    rig = rigs()
    with rig.client as client:
        session = cookie_of(client.post("/api/session/login", json={"token": TOKEN}))
        body = {"title": "跨站表单想建的单"}
        blocked = client.post(
            "/internal/tickets", json=body, headers={**session, "Sec-Fetch-Site": "cross-site"}
        )
        assert (blocked.status_code, blocked.json()["detail"]) == (403, "cross_site_request")
        allowed = client.post(
            "/internal/tickets", json=body, headers={**session, "Sec-Fetch-Site": "same-origin"}
        )
        assert allowed.status_code == 200


# ================= 外部登录系统（SSO 模式） =================


def test_probe_treats_an_expired_session_as_signed_out(rigs):
    """会话过期（或换了实例）时探测不报错：控制台出登录表单，而不是卡在空白页。"""
    rig = rigs(standard_provider())
    response = rig.client.get("/api/session", headers={"Cookie": "fake_sid=stale"})
    assert response.status_code == 200
    assert response.json() == {"mode": "sso", "provider": "fake"}


def test_probe_tells_the_console_which_login_form_to_show(rigs):
    """没带会话时探测不报错，只汇报登录方式：控制台据此出令牌还是邮箱+口令的表单。"""
    assert rigs().client.get("/api/session").json() == {"mode": "token", "provider": "token"}
    sso = rigs(standard_provider()).client.get("/api/session")
    assert sso.json() == {"mode": "sso", "provider": "fake"}
    local = rigs("local").client.get("/api/session")
    assert local.json() == {"mode": "local", "provider": "local"}


def test_sso_login_sets_the_provider_cookie(rigs):
    rig = rigs(standard_provider())
    response = login_as(rig, ADMIN.email)
    assert response.status_code == 200
    body = response.json()
    assert (body["mode"], body["provider"]) == ("sso", "fake")
    assert body["user"]["name"] == "张三"
    assert body["user"]["role"] == "admin"
    assert body["kind"] == "admin"
    header = response.headers["set-cookie"]
    assert header.startswith("fake_sid=s-u1")
    assert "HttpOnly" in header
    with rig.client as client:
        assert client.get("/internal/summary", headers=cookie_of(response)).status_code == 200


def test_sso_wrong_password_is_401(rigs):
    response = login_as(rigs(standard_provider()), ADMIN.email, "nope")
    assert (response.status_code, response.json()["detail"]) == (401, "invalid_credentials")


def test_super_admin_maps_to_owner(rigs):
    assert login_as(rigs(standard_provider()), SUPER.email).json()["user"]["role"] == "owner"


def test_first_sight_admin_is_provisioned_so_an_upgrade_keeps_its_admins(rigs):
    """升级后账号表是空的：已有的管理员会话第一次被验到时自动建号，没人被锁在门外。"""
    rig = rigs(standard_provider())
    with rig.client as client:
        assert client.get("/internal/summary", headers=signed_in("u1")).status_code == 200
    assert [(user["email"], user["role"]) for user in rig.users()] == [(ADMIN.email, "admin")]
    assert rig.audit("user.provisioned")[0]["detail"]["role"] == "admin"


def test_outsider_is_refused_while_internal_is_required(rigs):
    strict = rigs(standard_provider())
    refused = login_as(strict, OUTSIDER.email)
    assert (refused.status_code, refused.json()["detail"]) == (403, "not_internal")
    probe = strict.client.get("/api/session", headers=signed_in("u3"))
    assert (probe.status_code, probe.json()["detail"]) == (403, "not_internal")
    surface = strict.client.get("/api/session?surface=market", headers=signed_in("u3"))
    assert (surface.status_code, surface.json()["detail"]) == (403, "not_internal")


def test_outsider_passes_when_internal_is_not_required(rigs):
    """不要求内部成员的部署：外部用户认得出身份；但没有管理员标记也没有账号，仍进不了控制台。"""
    rig = rigs(standard_provider(), require_internal=False)
    surface = rig.client.get("/api/session?surface=market", headers=signed_in("u3"))
    assert surface.status_code == 200
    assert surface.json()["kind"] == "market"
    console = rig.client.get("/api/session", headers=signed_in("u3"))
    assert (console.status_code, console.json()["detail"]) == (403, "not_admin")


def test_person_without_an_account_cannot_open_the_console(rigs):
    rig = rigs(standard_provider())
    for path in ("/api/session", "/internal/summary", "/internal/tickets"):
        response = rig.client.get(path, headers=signed_in("u2"))
        assert (response.status_code, response.json()["detail"]) == (403, "not_admin"), path
    refused = login_as(rig, MEMBER.email)
    assert (refused.status_code, refused.json()["detail"]) == (403, "not_admin")
    assert rig.users() == []


def test_person_without_an_account_can_still_be_identified_for_chat(rigs):
    """聊天代理只要认出是谁：内部普通成员不需要雁阵账号，照样拿得到带身份的代理。"""
    rig = rigs(standard_provider())
    surface = rig.client.get("/api/session?surface=market", headers=signed_in("u2"))
    assert surface.status_code == 200
    assert surface.json()["kind"] == "market"
    login = login_as(rig, MEMBER.email, surface="market")
    assert (login.status_code, login.json()["kind"]) == (200, "market")
    assert login.headers["set-cookie"].startswith("fake_sid=s-u2")


def test_auto_role_provisions_unknown_identities_as_members(rigs):
    rig = rigs(standard_provider(), auto_role="member")
    response = rig.client.get("/api/session", headers=signed_in("u2"))
    assert response.status_code == 200
    assert response.json()["user"]["role"] == "member"


def test_member_reads_and_files_but_cannot_operate(rigs):
    """矩阵的 member 一行：能看、能建单；派工、验收、取消、入口建单、Agent、管理都是 403。"""
    rig = rigs(standard_provider())
    rig.directory.create(
        email=MEMBER.email, name=MEMBER.name, role="member", provider="fake", subject=MEMBER.sub
    )
    with rig.client as client:
        session = signed_in("u2")
        assert client.get("/api/session", headers=session).json()["permissions"] == [
            "read",
            "ticket.file",
        ]
        assert client.get("/internal/summary", headers=session).status_code == 200
        filed = client.post("/internal/tickets", json={"title": "成员提的单"}, headers=session)
        assert filed.status_code == 200
        ticket_id = filed.json()["ticket_id"]

        refused = [
            client.post(
                "/internal/tickets",
                json={"title": "想顺手派出去", "dispatch": True, "platform": "forum"},
                headers=session,
            ),
            client.post(f"/internal/tickets/{ticket_id}/dispatch?platform=forum", headers=session),
            client.post(
                f"/internal/tickets/{ticket_id}/review", json={"decision": "approve"}, headers=session
            ),
            client.post(f"/internal/tickets/{ticket_id}/cancel", headers=session),
            client.post(
                "/internal/intake",
                json={"source": "forum_post", "thread_id": "1", "title": "入口建单"},
                headers=session,
            ),
            client.post("/internal/agent/chat", json={"message": "hi"}, headers=session),
            client.get("/internal/agent/thread/th-1", headers=session),
            client.post(
                "/internal/worker/turn",
                json={"session_id": "s", "turn_index": 0, "data": ""},
                headers=session,
            ),
            client.get("/internal/users", headers=session),
            client.get("/internal/assistants", headers=session),
        ]
        assert [(response.status_code, response.json()["detail"]) for response in refused] == [
            (403, "forbidden")
        ] * len(refused)
        # 被拒的派单没有留下任何痕迹
        detail = client.get(f"/internal/tickets/{ticket_id}", headers=session).json()
        assert detail["ticket"]["status"] == "staged"
        assert detail["dispatches"] == []


def test_admin_reads_users_and_manages_assistants_but_not_users(rigs):
    rig = rigs(standard_provider())
    with rig.client as client:
        session = signed_in("u1")
        assert client.get("/api/session", headers=session).json()["permissions"] == [
            name for name in ALL_PERMISSIONS if name != "user.manage"
        ]
        assert client.get("/internal/users", headers=session).status_code == 200
        issued = client.post(
            "/internal/assistants", json={"name": "forum-bot"}, headers=session
        )
        assert issued.status_code == 201
        refused = client.post(
            "/internal/users", json={"email": "new@example.com", "password": A_PASSWORD}, headers=session
        )
        assert (refused.status_code, refused.json()["detail"]) == (403, "forbidden")


def test_owner_manages_users(rigs):
    rig = rigs(standard_provider())
    with rig.client as client:
        session = signed_in("u9")
        assert client.get("/api/session", headers=session).json()["permissions"] == ALL_PERMISSIONS
        created = client.post(
            "/internal/users",
            json={"email": "new@example.com", "role": "member", "password": A_PASSWORD},
            headers=session,
        )
        assert created.status_code == 201
        sub = created.json()["sub"]
        promoted = client.patch(f"/internal/users/{sub}", json={"role": "admin"}, headers=session)
        assert promoted.json()["role"] == "admin"
        reset = client.post(
            f"/internal/users/{sub}/password", json={"password": "another-long-password"}, headers=session
        )
        assert reset.status_code == 200
        disabled = client.patch(f"/internal/users/{sub}", json={"disabled": True}, headers=session)
        assert disabled.json()["disabled"] is True
        assert client.post(f"/internal/users/{sub}/password", json={"password": "x"}, headers=session).status_code == 400


def test_review_records_the_signed_in_person(rigs):
    rig = rigs(standard_provider())
    with rig.client as client:
        session = signed_in("u1")
        ticket = client.post("/internal/tickets", json={"title": "待验收"}, headers=session).json()
        review = client.post(
            f"/internal/tickets/{ticket['ticket_id']}/review",
            json={"decision": "approve"},
            headers=session,
        )
        assert review.status_code == 200
    assert rig.audit("review")[0]["actor"] == "张三"


def test_machine_actions_are_audited_as_machine(rigs):
    rig = rigs()
    with rig.client as client:
        ticket = client.post("/internal/tickets", json={"title": "巡检发现"}, headers=machine()).json()
        client.post(
            f"/internal/tickets/{ticket['ticket_id']}/review",
            json={"decision": "approve"},
            headers=machine(),
        )
    assert rig.audit("review")[0]["actor"] == "machine"


def test_machine_cannot_speak_for_a_person_in_sso_mode(rigs):
    rig = rigs(standard_provider(), llm_base="http://llm.test", llm_key="key")
    refused = rig.client.post("/internal/agent/chat", json={"message": "x"}, headers=machine())
    assert (refused.status_code, refused.json()["detail"]) == (403, "sign_in_required")


def test_machine_token_still_works_alongside_sso(rigs):
    assert rigs(standard_provider()).client.get("/internal/summary", headers=machine()).status_code == 200


def test_provider_outage_is_not_a_logout(rigs):
    """上游挂了：已认出的会话继续有效（缓存），没见过的会话得到 503——绝不当成未登录。"""
    provider = standard_provider()
    rig = rigs(provider)
    with rig.client as client:
        assert client.get("/internal/summary", headers=signed_in("u1")).status_code == 200
        provider.down = True
        # 缓存里的身份照用：不把人踢回登录页
        assert client.get("/internal/summary", headers=signed_in("u1")).status_code == 200
        unknown = client.get("/internal/summary", headers=signed_in("u2"))
        assert (unknown.status_code, unknown.json()["detail"]) == (503, "sso_unavailable")
        assert login_as(rig, ADMIN.email).status_code == 503
        # 上游恢复后，登出即使撞上再次故障，也要把本地清干净
        provider.down = False
        session = cookie_of(login_as(rig, ADMIN.email))
        provider.down = True
        assert client.post("/api/session/logout", headers=session).status_code == 200


def test_logout_revokes_and_clears(rigs):
    provider = standard_provider()
    rig = rigs(provider)
    with rig.client as client:
        session = cookie_of(login_as(rig, ADMIN.email))
        logout = client.post("/api/session/logout", headers=session)
        assert logout.json() == {"ok": True}
        assert provider.revoked == ["s-u1"]
        assert "fake_sid=" in logout.headers["set-cookie"]
        assert client.get("/internal/summary", headers=session).status_code == 401


def test_logout_clears_cookie_on_the_configured_domain(rigs):
    rig = rigs(standard_provider(), cookie_domain=".example.com")
    with rig.client as client:
        login_as(rig, ADMIN.email)
        header = client.post("/api/session/logout", headers=signed_in("u1")).headers["set-cookie"]
    assert "Domain=.example.com" in header


def unisso(**settings):
    """不注入替身：用真实的 UniSSO provider。这些用例不联网——不带 Cookie 的登出不调上游。"""
    return UniSSOProvider(
        UniSSOSettings(base="https://sso.example.com", client_id="c", client_secret="s", **settings)
    )


def test_unisso_logout_domain_follows_its_own_address(rigs):
    """SSO 与应用同父域的部署：登出必须用同一个域清，域从 SSO 地址推导，不靠写死。"""
    header = rigs(unisso()).client.post("/api/session/logout").headers["set-cookie"]
    assert "Domain=.example.com" in header
    assert "unisso_sid=" in header  # 连历史上用过的旧名一起清
    assert "sse_sso=" in header


def test_unisso_logout_domain_explicit_override_wins(rigs):
    header = rigs(
        unisso(cookie_domain=".accounts.example.net"), cookie_domain=".accounts.example.net"
    ).client.post("/api/session/logout").headers["set-cookie"]
    assert "Domain=.accounts.example.net" in header


def test_unisso_logout_host_only_when_configured(rigs):
    header = rigs(unisso(cookie_domain=""), cookie_domain="").client.post(
        "/api/session/logout"
    ).headers["set-cookie"]
    assert "unisso_sid=" in header and "Domain=" not in header


def test_unisso_login_sets_the_cookie_on_the_domain_sso_reports(rigs, monkeypatch):
    """会话 Cookie 的域以上游登录响应为准（SKEIN_COOKIE_DOMAIN 没显式配置时）。"""
    def fake_login(self, identifier, secret):
        return Principal(sub="42", name="张三", email=identifier, internal=True, admin=True), {
            "session": "sso-session-value",
            "cookie_name": "unisso_sid",
            "cookie_domain": ".example.com",
            "cookie_max_age": 604800,
        }

    monkeypatch.setattr(UniSSOProvider, "login", fake_login)
    rig = rigs(unisso(), cookie_secure=True)
    response = login_as(rig, "a@example.com", "pw")
    assert response.status_code == 200
    header = response.headers["set-cookie"]
    assert "unisso_sid=sso-session-value" in header
    assert "Domain=.example.com" in header and "Max-Age=604800" in header
    for attribute in ("HttpOnly", "Secure", "SameSite=lax"):
        assert attribute in header


# ================= 上游的管理员标记是权威的时候 =================


def plain(sub: str, email: str) -> Principal:
    """上游报上来的、不带任何管理员标记的身份。"""
    return Principal(sub=sub, email=email, internal=True)


def test_effective_role_table():
    """effective_role 的全部格子：member 只由雁阵授予；admin / owner 还要求上游也这么说。"""
    cases = {
        # (雁阵记的, 上游此刻给的) → 现在按哪个放行
        ("member", "none"): "member",
        ("member", "admin"): "member",
        ("member", "owner"): "member",
        ("admin", "none"): "none",
        ("admin", "admin"): "admin",
        ("admin", "owner"): "admin",
        ("owner", "none"): "none",
        ("owner", "admin"): "admin",
        ("owner", "owner"): "owner",
    }
    for (stored, upstream), expected in cases.items():
        assert effective_role(stored, upstream) == expected, (stored, upstream)
    assert role_from_claims(Principal(sub="x")) == NO_ACCESS
    assert role_from_claims(Principal(sub="x", admin=True)) == ROLE_ADMIN
    assert role_from_claims(Principal(sub="x", admin=True, super_admin=True)) == ROLE_OWNER


def test_losing_the_upstream_admin_flag_revokes_console_access(rigs):
    """与历史行为一致：上游一撤管理员标记，下一次核实就进不了控制台——不是降成只读的 member。"""
    provider = standard_provider(admin_claims=True)
    # 身份缓存默认 60 秒；这里不缓存，模拟缓存到期后再次向上游核实
    rig = rigs(provider, cache_ttl=0)
    with rig.client as client:
        assert client.get("/internal/summary", headers=signed_in("u1")).status_code == 200
        provider.sessions["s-u1"] = plain("u1", ADMIN.email)
        refused = client.get("/internal/summary", headers=signed_in("u1"))
        assert (refused.status_code, refused.json()["detail"]) == (403, "not_admin")
        # 账号没有被改写：上游恢复标记，权限自动回来，不需要谁来手工修复
        provider.sessions["s-u1"] = ADMIN
        assert client.get("/internal/summary", headers=signed_in("u1")).status_code == 200
    assert rig.users()[0]["role"] == "admin"


def test_a_cached_identity_outlives_an_upstream_change_for_at_most_a_minute(rigs):
    provider = standard_provider(admin_claims=True)
    rig = rigs(provider)
    with rig.client as client:
        assert client.get("/internal/summary", headers=signed_in("u1")).status_code == 200
        provider.sessions["s-u1"] = plain("u1", ADMIN.email)
        # 缓存期内还是原来的身份——既有行为
        assert client.get("/internal/summary", headers=signed_in("u1")).status_code == 200


def test_super_admin_demoted_upstream_drops_to_admin(rigs):
    """上游从超级管理员降为管理员：这里跟着从 owner 降到 admin，而不是掉出控制台。"""
    provider = standard_provider(admin_claims=True)
    rig = rigs(provider, cache_ttl=0)
    with rig.client as client:
        before = client.get("/api/session", headers=signed_in("u9")).json()["user"]["role"]
        provider.sessions["s-u9"] = Principal(
            sub="u9", name="老板", email=SUPER.email, internal=True, admin=True
        )
        after = client.get("/api/session", headers=signed_in("u9")).json()["user"]["role"]
    assert (before, after) == ("owner", "admin")


def test_member_is_granted_by_skein_alone(rigs):
    """member 是雁阵自己的授予：上游没说他是什么管理员，也不影响。"""
    rig = rigs(standard_provider(admin_claims=True))
    rig.directory.create(
        email=MEMBER.email, name=MEMBER.name, role="member", provider="fake", subject=MEMBER.sub
    )
    with rig.client as client:
        response = client.get("/api/session", headers=signed_in("u2"))
        assert (response.status_code, response.json()["user"]["role"]) == (200, "member")
        assert client.get("/internal/summary", headers=signed_in("u2")).status_code == 200


def test_skein_can_lower_a_role_below_what_the_provider_grants(rigs):
    """owner 在雁阵里把人降为 member：上游还说他是管理员，也不会被重新抬起来。"""
    rig = rigs(standard_provider(admin_claims=True), cache_ttl=0)
    with rig.client as client:
        assert client.get("/internal/summary", headers=signed_in("u1")).status_code == 200
        sub = rig.users()[0]["sub"]
        demoted = client.patch(f"/internal/users/{sub}", json={"role": "member"}, headers=machine())
        assert demoted.status_code == 200
        for _ in range(3):
            probe = client.get("/api/session", headers=signed_in("u1"))
            assert probe.json()["user"]["role"] == "member"
        # member 能读，但不能再做只有管理员能做的事
        assert client.get("/internal/summary", headers=signed_in("u1")).status_code == 200
        assert client.get("/internal/users", headers=signed_in("u1")).status_code == 403
    assert rig.users()[0]["role"] == "member"


def test_promoting_within_what_the_provider_grants_sticks(rigs):
    """上游是超级管理员（映射 owner）、雁阵里先被降为 admin：owner 把他提回 owner，稳定。"""
    rig = rigs(standard_provider(admin_claims=True))
    with rig.client as client:
        sub = client.get("/api/session", headers=signed_in("u9")).json()["user"]["sub"]
        client.patch(f"/internal/users/{sub}", json={"role": "admin"}, headers=machine())
        client.patch(f"/internal/users/{sub}", json={"role": "owner"}, headers=machine())
        for _ in range(2):
            probe = client.get("/api/session", headers=signed_in("u9"))
            assert probe.json()["user"]["role"] == "owner"


def test_the_upstream_flag_does_not_limit_providers_without_admin_claims(rigs):
    """不权威报告管理员标记的 provider：雁阵记的角色就是全部。"""
    rig = rigs(standard_provider())
    with rig.client as client:
        sub = client.get("/api/session", headers=signed_in("u1")).json()["user"]["sub"]
        client.patch(f"/internal/users/{sub}", json={"role": "owner"}, headers=machine())
        assert client.get("/api/session", headers=signed_in("u1")).json()["user"]["role"] == "owner"


def test_disabled_account_is_refused(rigs):
    rig = rigs(standard_provider())
    with rig.client as client:
        assert client.get("/internal/summary", headers=signed_in("u1")).status_code == 200
        sub = rig.users()[0]["sub"]
        client.patch(f"/internal/users/{sub}", json={"disabled": True}, headers=machine())
        refused = client.get("/internal/summary", headers=signed_in("u1"))
        assert (refused.status_code, refused.json()["detail"]) == (403, "account_disabled")


# ================= 账号绝不按邮箱合并 =================


def test_accounts_are_never_merged_by_email(rigs):
    """两种登录方式报上来同一个邮箱：是两个人。要挂到同一个人身上只能由 owner 显式 link。"""
    shared = Principal(sub="same", name="同邮箱", email="shared@example.com", internal=True, admin=True)
    one, two = FakeProvider("one"), FakeProvider("two")
    one.add(shared, "s1")
    two.add(shared, "s1")
    first = rigs(one, name="shared.db")
    second = rigs(two, name="shared.db")

    login_as(first, shared.email)
    login_as(second, shared.email)

    users = first.users()  # 两个 app 共用同一个库文件
    assert len(users) == 2
    assert len({user["sub"] for user in users}) == 2
    assert {user["email"] for user in users} == {"shared@example.com"}
    # 显式 link：把「two」这个身份挂到第三个账号上是不行的——它已经属于第二个人
    third = first.directory.create(email="third@example.com", role="member")
    with pytest.raises(ValueError, match="already linked"):
        first.directory.link(third.sub, "two", "same")
    # 挂一个还没被占用的身份就可以
    first.directory.link(third.sub, "three", "abc")
    assert [row["provider"] for row in first.directory.identities(third.sub)] == ["three"]


# ================= 限速 =================


def test_login_rate_limit_per_ip(rigs, monkeypatch):
    from skein import api

    monkeypatch.setattr(api, "LOGIN_PER_MINUTE", 3)
    rig = rigs(standard_provider())
    for _ in range(3):
        assert login_as(rig, ADMIN.email, "nope").status_code == 401
    fourth = login_as(rig, ADMIN.email, PASSWORD)
    assert (fourth.status_code, fourth.json()["detail"]) == (429, "too_many_attempts")
    assert "Retry-After" in fourth.headers


# ================= 配置：三种登录方式只启用一种 =================


def test_login_method_resolution():
    assert resolve_method(Config.from_env({})) == LoginMethod("token", "token")
    assert resolve_method(Config.from_env({"SKEIN_LOCAL_LOGIN": "1"})) == LoginMethod(
        "local", "local"
    )
    sso = Config.from_env({"SKEIN_SSO_CLIENT_ID": "c", "SKEIN_SSO_CLIENT_SECRET": "s"})
    assert resolve_method(sso) == LoginMethod("sso", "unisso")


def test_unknown_provider_is_a_config_error():
    cfg = Config.from_env(
        {"SKEIN_SSO_PROVIDER": "nope", "SKEIN_SSO_CLIENT_ID": "c", "SKEIN_SSO_CLIENT_SECRET": "s"}
    )
    with pytest.raises(ConfigError, match="nope"):
        resolve_method(cfg)


def test_sso_and_local_login_together_is_a_config_error():
    cfg = Config.from_env(
        {
            "SKEIN_SSO_CLIENT_ID": "c",
            "SKEIN_SSO_CLIENT_SECRET": "s",
            "SKEIN_LOCAL_LOGIN": "1",
        }
    )
    with pytest.raises(ConfigError, match="SKEIN_LOCAL_LOGIN") as excinfo:
        resolve_method(cfg)
    assert "SKEIN_SSO_CLIENT_ID" in str(excinfo.value)
    assert "exactly one" in str(excinfo.value)


def test_env_key_names_are_unchanged():
    """老部署不改环境文件：启用 UniSSO 的仍是那三个键，缺省仍要求内部成员。"""
    cfg = Config.from_env(
        {
            "SKEIN_SSO_BASE": "https://sso.example.com/",
            "SKEIN_SSO_CLIENT_ID": "cid",
            "SKEIN_SSO_CLIENT_SECRET": "sec",
        }
    )
    assert resolve_method(cfg) == LoginMethod("sso", "unisso")
    assert cfg.sso_require_internal is True
    assert cfg.local_login is False
    assert cfg.auth_auto_role == ""


def test_invalid_auto_role_is_rejected(tmp_path):
    from skein.accounts import Directory

    conn = db.connect(tmp_path / "c.db")
    with pytest.raises(ValueError, match="auto_role"):
        Directory(conn, auto_role="admin")
    conn.close()


def test_build_refuses_a_conflicting_config(tmp_path):
    cfg = Config.from_env(
        {"SKEIN_LOCAL_LOGIN": "1", "SKEIN_SSO_CLIENT_ID": "c", "SKEIN_SSO_CLIENT_SECRET": "s"}
    )
    conn = db.connect(tmp_path / "c.db")
    db.migrate(conn)
    try:
        with pytest.raises(ConfigError):
            build(cfg, conn)
    finally:
        conn.close()


def test_config_has_no_hardcoded_login_defaults():
    cfg = Config.from_env({})
    assert cfg.sso_base == ""
    assert cfg.sso_client_id == ""
    assert cfg.sso_client_secret == ""
    assert cfg.sso_cookie_name == ""
    assert cfg.sso_cookie_domain is None
    assert cfg.session_cookie_name == ""
    assert cfg.session_cookie_secure is True
    assert cfg.auth_auto_role == ""


def test_main_fails_clearly_on_conflicting_config(monkeypatch, tmp_path):
    """启动时配置自相矛盾：一句话说清是谁和谁打架，而不是一段 traceback。"""
    import skein.__main__ as entry

    env = {
        "SKEIN_LOCAL_LOGIN": "1",
        "SKEIN_SSO_CLIENT_ID": "c",
        "SKEIN_SSO_CLIENT_SECRET": "s",
        "SKEIN_DB": str(tmp_path / "x.db"),
        "SKEIN_BLOB_ROOT": str(tmp_path / "b"),
    }
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    with pytest.raises(SystemExit, match="SKEIN_LOCAL_LOGIN"):
        entry.build()


# ================= 本地账号（local 模式） =================


def create_local(rig, email: str, role: str, password: str = A_PASSWORD) -> str:
    created = rig.client.post(
        "/internal/users",
        json={"email": email, "role": role, "password": password},
        headers=machine(),
    )
    assert created.status_code == 201, created.text
    return created.json()["sub"]


def test_local_mode_has_no_users_until_someone_creates_one(rigs):
    rig = rigs("local")
    probe = rig.client.get("/api/session")
    assert probe.json()["mode"] == "local"
    refused = login_as(rig, "nobody@example.com", "whatever-password")
    # 账号不存在与口令错误是同一个回应：不泄露账号是否存在
    assert (refused.status_code, refused.json()["detail"]) == (401, "invalid_credentials")


def test_local_login_and_role_changes_take_effect_immediately(rigs):
    rig = rigs("local")
    with rig.client as client:
        sub = create_local(rig, "Admin@Example.com", "admin")
        # 邮箱不区分大小写
        response = login_as(rig, "admin@example.com", A_PASSWORD)
        assert response.status_code == 200
        assert response.json()["mode"] == "local"
        assert response.headers["set-cookie"].startswith("skein_session=")
        session = cookie_of(response, "skein_session")
        assert client.get("/api/session", headers=session).json()["user"]["role"] == "admin"
        # 改角色不用重新登录：每个请求都现查账号
        assert client.patch(
            f"/internal/users/{sub}", json={"role": "member"}, headers=machine()
        ).status_code == 200
        assert client.get("/api/session", headers=session).json()["user"]["role"] == "member"
        assert client.get("/internal/users", headers=session).status_code == 403
        # 令牌模式的登录在 local 模式下不可用：拿主令牌来只是「凭据不对」
        assert client.post("/api/session/login", json={"token": TOKEN}).status_code == 401


def test_password_reset_and_disable_sign_the_user_out(rigs):
    rig = rigs("local")
    with rig.client as client:
        sub = create_local(rig, "admin@example.com", "admin")
        session = cookie_of(login_as(rig, "admin@example.com", A_PASSWORD))
        assert client.get("/internal/summary", headers=session).status_code == 200
        client.post(
            f"/internal/users/{sub}/password",
            json={"password": "a-different-password"},
            headers=machine(),
        )
        assert client.get("/internal/summary", headers=session).status_code == 401

        session = cookie_of(login_as(rig, "admin@example.com", "a-different-password"))
        client.patch(f"/internal/users/{sub}", json={"disabled": True}, headers=machine())
        assert client.get("/internal/summary", headers=session).status_code == 401
        refused = login_as(rig, "admin@example.com", "a-different-password")
        assert (refused.status_code, refused.json()["detail"]) == (403, "account_disabled")


def test_weak_passwords_are_refused(rigs):
    rig = rigs("local")
    refused = rig.client.post(
        "/internal/users",
        json={"email": "weak@example.com", "role": "member", "password": "short"},
        headers=machine(),
    )
    assert refused.status_code == 400
    assert "at least 12 characters" in refused.json()["detail"]


def test_account_lockout_after_repeated_failures(rigs, monkeypatch):
    """按账号限速：同一个邮箱连续失败到上限就锁一阵，换 IP 也绕不过去。"""
    from skein import api

    monkeypatch.setattr(api, "LOGIN_PER_MINUTE", 1000)
    monkeypatch.setattr(api, "LOGIN_FAILURES", 3)
    rig = rigs("local")
    create_local(rig, "admin@example.com", "admin")
    for _ in range(3):
        assert login_as(rig, "admin@example.com", "wrong-password-1").status_code == 401
    locked = login_as(rig, "admin@example.com")
    assert (locked.status_code, locked.json()["detail"]) == (429, "too_many_attempts")
    assert "Retry-After" in locked.headers
    # 别的账号不受影响
    assert login_as(rig, "someone-else@example.com").status_code == 401


def test_sessions_expire(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    db.migrate(conn)
    clock = {"now": 1000.0}
    store = SessionStore(conn, ttl_s=3600, clock=lambda: clock["now"])
    raw = store.issue("usr_test", "local")
    assert store.resolve(raw)["user_sub"] == "usr_test"
    clock["now"] += 3601
    assert store.resolve(raw) is None
    assert conn.execute("SELECT COUNT(*) FROM login_sessions").fetchone()[0] == 0
    conn.close()


def test_only_the_plain_token_is_stored_never_the_session_value(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    db.migrate(conn)
    store = SessionStore(conn, ttl_s=3600)
    raw = store.issue("usr_test", "local")
    stored = conn.execute("SELECT id FROM login_sessions").fetchone()[0]
    assert raw not in stored and len(stored) == 64  # SHA-256 的十六进制
    conn.close()


# ================= 加一种登录方式不用改核心 =================


def test_new_provider_works_without_touching_core(rigs):
    rig = rigs(standard_provider())
    with rig.client as client:
        login = login_as(rig, ADMIN.email)
        assert login.status_code == 200
        assert "fake_sid=s-u1" in login.headers["set-cookie"]
        assert client.get("/internal/summary", headers=cookie_of(login)).status_code == 200
        # 登出清的是这个 provider 的 Cookie
        header = client.post("/api/session/logout", headers=signed_in("u1")).headers["set-cookie"]
        assert "fake_sid=" in header and "Domain=" not in header
