from fastapi.testclient import TestClient

from skein import db
from skein.api import create_app

TOKEN = "intake-token"


def make_client(tmp_path):
    conn = db.connect(tmp_path / "intake.db")
    db.migrate(conn)
    app = create_app(conn, str(tmp_path / "b"), TOKEN)
    return TestClient(app), conn


def test_intake_creates_and_dedups(tmp_path):
    client, conn = make_client(tmp_path)
    body = {
        "source": "forum_post",
        "thread_id": "42",
        "message_id": "42",
        "requester": "forum:278",
        "title": "登录坏了",
        "summary": "点登录没反应",
        "category": "bug",
        "severity": "high",
    }
    with client:
        first = client.post(
            "/internal/intake", json=body, headers={"X-Skein-Token": TOKEN}
        )
        second = client.post(
            "/internal/intake", json=body, headers={"X-Skein-Token": TOKEN}
        )
    assert first.status_code == 200
    assert first.json()["created"] is True
    assert first.json()["public_status"] == "received"
    assert second.json()["created"] is False
    assert second.json()["ticket_id"] == first.json()["ticket_id"]
    row = conn.execute(
        "SELECT origin, origin_id, requester, public_status, status FROM tickets"
    ).fetchone()
    assert row["origin"] == "forum_post"
    assert row["origin_id"] == "42"
    assert row["requester"] == "forum:278"
    assert row["public_status"] == "received"
    assert row["status"] == "staged"
    conn.close()


def test_comment_followup_merges_active_ticket(tmp_path):
    client, conn = make_client(tmp_path)
    with client:
        post = client.post(
            "/internal/intake",
            json={
                "source": "forum_post",
                "thread_id": "42",
                "message_id": "42",
                "requester": "forum:278",
                "title": "图片裂了",
                "summary": "上传后列表裂图",
            },
            headers={"X-Skein-Token": TOKEN},
        ).json()
        comment = client.post(
            "/internal/intake",
            json={
                "source": "forum_comment",
                "thread_id": "42",
                "message_id": "7",
                "requester": "forum:278",
                "title": "补充",
                "summary": "控制台还报 500",
            },
            headers={"X-Skein-Token": TOKEN},
        ).json()
    assert comment["ticket_id"] == post["ticket_id"]
    assert comment["code"] == post["code"]
    assert comment["created"] is False
    assert comment["followup"] is True
    rows = conn.execute("SELECT summary FROM tickets").fetchall()
    assert len(rows) == 1
    assert "控制台还报 500" in rows[0]["summary"]
    conn.close()


def test_comment_after_done_creates_new_ticket(tmp_path):
    client, conn = make_client(tmp_path)
    with client:
        post = client.post(
            "/internal/intake",
            json={
                "source": "forum_post",
                "thread_id": "42",
                "message_id": "42",
                "requester": "forum:278",
                "title": "图片裂了",
                "summary": "上传后列表裂图",
            },
            headers={"X-Skein-Token": TOKEN},
        ).json()
        client.post(
            f"/internal/tickets/{post['ticket_id']}/review",
            json={"decision": "approve"},
            headers={"X-Skein-Token": TOKEN},
        )
        reopened = client.post(
            "/internal/intake",
            json={
                "source": "forum_comment",
                "thread_id": "42",
                "message_id": "8",
                "requester": "forum:278",
                "title": "还是坏的",
                "summary": "修复后仍然裂图",
            },
            headers={"X-Skein-Token": TOKEN},
        ).json()
    assert reopened["created"] is True
    assert reopened["ticket_id"] != post["ticket_id"]
    conn.close()


def test_ccomment_creates_then_merges(tmp_path):
    client, conn = make_client(tmp_path)
    with client:
        first = client.post(
            "/internal/intake",
            json={
                "source": "forum_ccomment",
                "thread_id": "42",
                "parent_id": "7",
                "message_id": "9",
                "requester": "forum:278",
                "title": "楼中楼补充",
                "summary": "还是报错",
            },
            headers={"X-Skein-Token": TOKEN},
        )
        second = client.post(
            "/internal/intake",
            json={
                "source": "forum_ccomment",
                "thread_id": "42",
                "parent_id": "7",
                "message_id": "10",
                "requester": "forum:278",
                "title": "再补充",
                "summary": "仍然不行",
            },
            headers={"X-Skein-Token": TOKEN},
        )
    assert first.status_code == 200
    assert first.json()["created"] is True
    row = conn.execute("SELECT origin, origin_id FROM tickets").fetchone()
    assert row["origin"] == "forum_ccomment"
    assert row["origin_id"] == "42:7:9"
    assert second.json()["created"] is False
    assert second.json()["followup"] is True
    assert second.json()["ticket_id"] == first.json()["ticket_id"]
    assert conn.execute("SELECT count(*) FROM tickets").fetchone()[0] == 1
    conn.close()


def test_ccomment_requires_parent(tmp_path):
    client, conn = make_client(tmp_path)
    with client:
        response = client.post(
            "/internal/intake",
            json={
                "source": "forum_ccomment",
                "thread_id": "42",
                "message_id": "9",
                "title": "楼中楼补充",
            },
            headers={"X-Skein-Token": TOKEN},
        )
    assert response.status_code == 400
    conn.close()


def test_review_moves_public_status(tmp_path):
    client, conn = make_client(tmp_path)
    with client:
        created = client.post(
            "/internal/intake",
            json={
                "source": "forum_comment",
                "thread_id": "42",
                "message_id": "7",
                "requester": "forum:278",
                "title": "图片打不开",
                "summary": "上传后裂图",
            },
            headers={"X-Skein-Token": TOKEN},
        ).json()
        review = client.post(
            f"/internal/tickets/{created['ticket_id']}/review",
            json={"decision": "approve"},
            headers={"X-Skein-Token": TOKEN},
        )
    assert review.status_code == 200
    row = conn.execute(
        "SELECT status, public_status FROM tickets WHERE id = ?",
        (created["ticket_id"],),
    ).fetchone()
    assert row["status"] == "done"
    assert row["public_status"] == "fixed"
    conn.close()
