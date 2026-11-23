import asyncio
import time
from types import SimpleNamespace

from helpers import TOKEN, make_app, standard_provider

from skein import db, store
from skein.agent import Agent


class FakeHub:
    async def dispatch_ticket(self, ticket_id, platform=None):
        return {"ok": True, "ticket_id": ticket_id, "platform": platform}

    async def cancel_ticket(self, ticket_id):
        return True


def make_conn(tmp_path):
    conn = db.connect(tmp_path / "agent.db")
    db.migrate(conn)
    return conn


def test_agent_requires_an_admin_account(tmp_path):
    """Agent 接口只对有雁阵账号的管理员开放：没登录 401；机器令牌、没有账号的人 403。"""
    provider = standard_provider()
    rig = make_app(
        tmp_path, provider, name="agent-acl.db", llm_base="http://llm.test", llm_key="key"
    )
    with rig.client as client:
        assert client.get("/internal/agent/thread/x").status_code == 401
        assert client.get("/internal/agent/thread/x", headers={"X-Skein-Token": TOKEN}).status_code == 403
        # u2 是内部成员但没有雁阵账号
        assert client.get("/internal/agent/thread/x", headers={"Cookie": "fake_sid=s-u2"}).status_code == 403
    rig.conn.close()


def test_agent_thread_loads_for_admin(tmp_path):
    rig = make_app(
        tmp_path, standard_provider(), name="agent-ok.db", llm_base="http://llm.test", llm_key="key"
    )
    with rig.client as client:
        response = client.get("/internal/agent/thread/x", headers={"Cookie": "fake_sid=s-u1"})
    assert response.status_code == 200
    assert response.json()["messages"] == []
    rig.conn.close()


def test_agent_tools_read_domain(tmp_path):
    conn = make_conn(tmp_path)
    now = time.time()
    conn.execute(
        "INSERT INTO tickets (id, code, title, status, created_at, updated_at)"
        " VALUES ('t1', 'SK-1', '登录坏了', 'review', ?, ?)",
        (now, now),
    )
    conn.execute(
        "INSERT INTO nodes (id, name, status, max_workers, last_seen)"
        " VALUES ('n1', '节点', 'online', 2, ?)",
        (now,),
    )
    agent = Agent(conn, "", "", "", FakeHub())
    principal = SimpleNamespace(label="管理员", sub="u1")
    tickets = asyncio.run(agent._tool("list_tickets", {}, principal))
    assert tickets[0]["code"] == "SK-1"
    detail = asyncio.run(agent._tool("ticket_detail", {"ticket_id": "SK-1"}, principal))
    assert detail["title"] == "登录坏了"
    nodes = asyncio.run(agent._tool("list_nodes", {}, principal))
    assert nodes[0]["status"] == "online"
    dispatched = asyncio.run(
        agent._tool("dispatch_ticket", {"ticket_id": "t1"}, principal)
    )
    assert dispatched["ok"] is True
    conn.close()


def test_agent_resolves_ticket_code_and_reports_missing(tmp_path):
    conn = make_conn(tmp_path)
    now = time.time()
    conn.execute(
        "INSERT INTO tickets (id, code, title, status, created_at, updated_at)"
        " VALUES ('t1', 'SK-0003', '评论长文', 'staged', ?, ?)",
        (now, now),
    )
    agent = Agent(conn, "", "", "", FakeHub())
    principal = SimpleNamespace(label="管理员", sub="u1")
    # 人类编号（不区分大小写、可带前导 #）应解析成内部 id 再派单
    dispatched = asyncio.run(
        agent._tool(
            "dispatch_ticket",
            {"ticket_id": "#sk-0003", "platform": "market-server"},
            principal,
        )
    )
    assert dispatched["ok"] is True
    assert dispatched["ticket_id"] == "t1"
    detail = asyncio.run(agent._tool("ticket_detail", {"ticket_id": "sk-0003"}, principal))
    assert detail["title"] == "评论长文"
    missing = asyncio.run(agent._tool("dispatch_ticket", {"ticket_id": "SK-9999"}, principal))
    assert missing == {"error": "ticket_not_found: SK-9999"}
    cancelled = asyncio.run(agent._tool("cancel_ticket", {"ticket_id": "NOPE"}, principal))
    assert cancelled == {"error": "ticket_not_found: NOPE"}
    conn.close()


def test_agent_chat_saves_history(tmp_path):
    conn = make_conn(tmp_path)
    agent = Agent(conn, "", "", "m", FakeHub())
    principal = SimpleNamespace(label="管理员", sub="u1")

    async def fake_llm(principal, thread_id, messages):
        return {"role": "assistant", "content": "现在有 1 张待验收。"}

    agent._llm = fake_llm
    result = asyncio.run(agent.chat(principal, "th1", "现在有什么要我处理？"))
    assert result["reply"] == "现在有 1 张待验收。"
    rows = conn.execute(
        "SELECT role, content FROM agent_messages WHERE thread_id='th1' ORDER BY rowid"
    ).fetchall()
    assert [(row["role"], row["content"]) for row in rows] == [
        ("user", "现在有什么要我处理？"),
        ("assistant", "现在有 1 张待验收。"),
    ]
    conn.close()


def test_agent_tool_loop_executes_tool(tmp_path):
    conn = make_conn(tmp_path)
    store.create_ticket(conn, "测试单", None, None, "")
    agent = Agent(conn, "", "", "m", FakeHub())
    principal = SimpleNamespace(label="管理员", sub="u1")
    calls = {"n": 0}

    async def fake_llm(principal, thread_id, messages):
        calls["n"] += 1
        if calls["n"] == 1:
            return {
                "role": "assistant",
                "content": '{"action":"tool","tool":"list_tickets","args":{}}',
            }
        return {"role": "assistant", "content": "有一张。"}

    agent._llm = fake_llm
    result = asyncio.run(agent.chat(principal, "th2", "有几张单？"))
    assert result["reply"] == "有一张。"
    assert result["tools"] == ["list_tickets"]
    audit = conn.execute(
        "SELECT action FROM audit WHERE action='agent.tool' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert audit["action"] == "agent.tool"
    conn.close()
