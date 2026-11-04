import hashlib
import hmac
import json

import httpx
import pytest
from sqlalchemy import select

from app import alerts
from app.jobs import queue, worker
from app.main import app, ensure_admin
from app.models import CommentEvent, Job, WebhookEvent
from tests.conftest import ACCOUNT_IG_ID
from tests.factories import FOLLOW_GATE_FLOW, comment_trigger


@pytest.fixture
async def client():
    await ensure_admin()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
async def authed(client):
    response = await client.post(
        "/api/auth/login", json={"email": "admin@test.dev", "password": "password123"}
    )
    assert response.status_code == 200
    return client


def signed(body: dict) -> tuple[bytes, dict[str, str]]:
    raw = json.dumps(body).encode()
    signature = "sha256=" + hmac.new(b"ig-secret", raw, hashlib.sha256).hexdigest()
    return raw, {"X-Hub-Signature-256": signature, "Content-Type": "application/json"}


async def test_auth(client):
    assert (await client.get("/api/automations")).status_code == 401
    bad = await client.post("/api/auth/login", json={"email": "admin@test.dev", "password": "x"})
    assert bad.status_code == 401
    ok = await client.post(
        "/api/auth/login", json={"email": "ADMIN@test.dev", "password": "password123"}
    )
    assert ok.status_code == 200
    assert (await client.get("/api/auth/me")).json()["email"] == "admin@test.dev"
    await client.post("/api/auth/logout")
    client.cookies.clear()
    assert (await client.get("/api/auth/me")).status_code == 401


async def test_webhook_verification(client):
    params = {"hub.mode": "subscribe", "hub.verify_token": "verify-me", "hub.challenge": "123"}
    response = await client.get("/webhooks/instagram", params=params)
    assert (response.status_code, response.text) == (200, "123")
    params["hub.verify_token"] = "wrong"
    assert (await client.get("/webhooks/instagram", params=params)).status_code == 403


async def test_automation_crud(authed, account):
    body = {
        "account_id": account.id,
        "name": "Reel link",
        "is_active": True,
        "trigger": comment_trigger(),
        "flow": FOLLOW_GATE_FLOW,
    }
    created = await authed.post("/api/automations", json=body)
    assert created.status_code == 201, created.text
    automation = created.json()
    assert automation["trigger_type"] == "comment"

    broken = {**body, "flow": {"start_step_id": "nope", "steps": FOLLOW_GATE_FLOW["steps"]}}
    invalid = await authed.post("/api/automations", json=broken)
    assert invalid.status_code == 422
    assert "Start step does not exist" in json.dumps(invalid.json())

    body["name"] = "Renamed"
    updated = await authed.put(f"/api/automations/{automation['id']}", json=body)
    assert updated.json()["name"] == "Renamed"

    toggled = await authed.patch(
        f"/api/automations/{automation['id']}/active", json={"is_active": False}
    )
    assert toggled.json()["is_active"] is False

    copy = await authed.post(f"/api/automations/{automation['id']}/duplicate")
    assert copy.json()["name"] == "Renamed (copy)"

    listed = await authed.get("/api/automations", params={"account_id": account.id})
    assert len(listed.json()) == 2

    stats = await authed.get(f"/api/automations/{automation['id']}/stats")
    assert stats.json() == {"triggered": 0, "comments_matched": 0, "dms_sent": 0, "runs": {}}

    assert (await authed.delete(f"/api/automations/{automation['id']}")).status_code == 204
    assert (await authed.get(f"/api/automations/{automation['id']}")).status_code == 404


async def test_webhook_to_dm_end_to_end(authed, account, fake_ig, session):
    await authed.post(
        "/api/automations",
        json={
            "account_id": account.id,
            "name": "Reel link",
            "is_active": True,
            "trigger": comment_trigger(),
            "flow": FOLLOW_GATE_FLOW,
        },
    )
    event = {
        "object": "instagram",
        "entry": [
            {
                "id": ACCOUNT_IG_ID,
                "time": 1700000000,
                "changes": [
                    {
                        "field": "comments",
                        "value": {
                            "id": "1800000001",
                            "text": "LINK",
                            "from": {"id": "555", "username": "jane"},
                            "media": {"id": "m1", "media_product_type": "REELS"},
                        },
                    }
                ],
            }
        ],
    }
    raw, headers = signed(event)
    bad = await authed.post(
        "/webhooks/instagram", content=raw, headers={**headers, "X-Hub-Signature-256": "sha256=0"}
    )
    assert bad.status_code == 403

    response = await authed.post("/webhooks/instagram", content=raw, headers=headers)
    assert (response.status_code, response.text) == (200, "EVENT_RECEIVED")

    # Worker: webhook job -> private reply; then the public reply job it enqueued
    assert await worker.run_due_jobs() == 1
    assert await worker.run_due_jobs() == 1
    assert fake_ig.sent[0][0] == {"comment_id": "1800000001"}
    assert fake_ig.public_replies == [("1800000001", "Check your DMs!")]

    stored = await session.scalar(select(WebhookEvent))
    assert stored.processed_at is not None and stored.error is None
    row = await session.scalar(select(CommentEvent))
    assert (row.status, row.dm_sent, row.public_reply_id) == ("matched", True, "reply_1800000001")

    # Meta retries the same delivery -> nothing new happens
    await authed.post("/webhooks/instagram", content=raw, headers=headers)
    await worker.run_due_jobs()
    assert len(fake_ig.sent) == 1

    stats = (await authed.get("/api/stats", params={"account_id": account.id})).json()
    assert stats["comments_24h"] == 1
    assert stats["dms_sent_24h"] == 1
    assert len(stats["daily"]) == 14

    contacts = (await authed.get("/api/contacts", params={"q": "@jan"})).json()
    assert contacts["total"] == 1
    contact_id = contacts["items"][0]["id"]
    messages = (await authed.get(f"/api/contacts/{contact_id}/messages")).json()
    assert [m["kind"] for m in messages] == ["private_reply"]
    runs = (await authed.get(f"/api/contacts/{contact_id}/runs")).json()
    assert runs[0]["status"] == "waiting_button"
    assert runs[0]["automation_name"] == "Reel link"

    csv_response = await authed.get("/api/contacts/export.csv")
    assert csv_response.text.splitlines()[1].startswith("jane,")

    comments = (await authed.get("/api/activity/comments")).json()
    assert comments["items"][0]["automation_name"] == "Reel link"
    assert (await authed.get("/api/activity/webhooks")).json()[0]["error"] is None


async def test_job_retry_and_give_up(session):
    job = await queue.enqueue(session, "nonexistent", {}, max_attempts=2)
    await session.commit()

    await worker.run_due_jobs()
    await session.refresh(job)
    assert (job.status, job.attempts) == ("pending", 1)
    assert "Unknown job type" in job.last_error

    job.run_at = job.created_at  # make it due again now
    await session.commit()
    await worker.run_due_jobs()
    await session.refresh(job)
    assert (job.status, job.attempts) == ("failed", 2)
    failed = await session.scalars(select(Job).where(Job.status == "failed"))
    assert len(list(failed)) == 1


async def test_login_rate_limit(client):
    from app.api import auth

    auth._failed_logins.clear()
    for _ in range(auth.MAX_FAILED_LOGINS):
        response = await client.post(
            "/api/auth/login", json={"email": "admin@test.dev", "password": "wrong"}
        )
        assert response.status_code == 401
    blocked = await client.post(
        "/api/auth/login", json={"email": "admin@test.dev", "password": "password123"}
    )
    assert blocked.status_code == 429
    auth._failed_logins.clear()


async def test_token_refresh_schedule(account, session, monkeypatch):
    from datetime import UTC, datetime, timedelta

    calls = []

    async def fake_refresh(token):
        calls.append(token)
        return {"access_token": "new-token", "expires_in": 5184000}

    monkeypatch.setattr(worker, "refresh_long_lived_token", fake_refresh)

    # Refreshed today -> left alone
    account.token_refreshed_at = datetime.now(UTC)
    await session.commit()
    await worker.refresh_tokens()
    assert calls == []

    # A week old -> refreshed, expiry pushed out ~60 days
    account.token_refreshed_at = datetime.now(UTC) - timedelta(days=8)
    await session.commit()
    await worker.refresh_tokens()
    assert calls == ["token"]
    await session.refresh(account)
    assert account.token_expires_at > datetime.now(UTC) + timedelta(days=59)


async def test_instagram_app_settings_from_dashboard(authed):
    # Falls back to .env (tests set INSTAGRAM_APP_SECRET but no app id)
    initial = (await authed.get("/api/settings/instagram")).json()
    assert initial == {
        "instagram_app_id": "",
        "instagram_app_secret_set": True,
        "meta_app_secret_set": False,
        "configured": False,
    }
    config = (await authed.get("/api/config")).json()
    assert config["webhook_verify_token"] == "verify-me"
    assert config["instagram_app_configured"] is False
    assert (await authed.get("/api/instagram/oauth/start")).status_code == 400

    bad = await authed.put("/api/settings/instagram", json={"instagram_app_id": "abc"})
    assert bad.status_code == 422
    assert "digits only" in json.dumps(bad.json())

    saved = await authed.put(
        "/api/settings/instagram",
        json={"instagram_app_id": "1234567890", "instagram_app_secret": "NewSecret0123456789abcd"},
    )
    assert saved.json()["configured"] is True
    start = (await authed.get("/api/instagram/oauth/start")).json()
    assert "client_id=1234567890" in start["url"]

    # Webhooks are now verified with the dashboard secret, not the .env one
    body = {"object": "instagram", "entry": []}
    raw = json.dumps(body).encode()
    new_sig = "sha256=" + hmac.new(b"NewSecret0123456789abcd", raw, hashlib.sha256).hexdigest()
    ok = await authed.post(
        "/webhooks/instagram", content=raw, headers={"X-Hub-Signature-256": new_sig}
    )
    assert ok.status_code == 200
    _, old_headers = signed(body)
    old = await authed.post("/webhooks/instagram", content=raw, headers=old_headers)
    assert old.status_code == 403

    # Clearing the dashboard value falls back to .env again; the app id is untouched
    cleared = await authed.put("/api/settings/instagram", json={"instagram_app_secret": ""})
    assert cleared.json()["instagram_app_id"] == "1234567890"
    again = await authed.post("/webhooks/instagram", content=raw, headers=old_headers)
    assert again.status_code == 200


async def test_settings_require_login(client):
    assert (await client.get("/api/settings/instagram")).status_code == 401
    assert (await client.put("/api/settings/instagram", json={})).status_code == 401


async def test_email_settings_from_dashboard(authed, monkeypatch):
    initial = (await authed.get("/api/settings/email")).json()
    assert initial["configured"] is False
    assert (initial["smtp_host"], initial["smtp_port"]) == ("smtp.gmail.com", 587)
    not_ready = await authed.post("/api/settings/email/test")
    assert not_ready.status_code == 400

    bad = await authed.put("/api/settings/email", json={"smtp_username": "not-an-email"})
    assert bad.status_code == 422

    saved = await authed.put(
        "/api/settings/email",
        json={"smtp_username": "me@gmail.com", "smtp_password": "abcd efgh ijkl mnop"},
    )
    out = saved.json()
    assert out["configured"] is True
    assert out["alert_email_to"] == "me@gmail.com"  # defaults to the sending address
    assert "smtp_password" not in out and out["smtp_password_set"] is True

    sent = []

    async def fake_send(config, subject, body):
        sent.append((config.password, config.to, subject))

    monkeypatch.setattr(alerts, "send_email", fake_send)
    tested = await authed.post("/api/settings/email/test")
    assert tested.json() == {"sent_to": "me@gmail.com"}
    assert sent == [("abcdefghijklmnop", "me@gmail.com", "Test email")]


async def test_automation_waiting_for_next_post(authed, account):
    body = {
        "account_id": account.id,
        "name": "Replica",
        "is_active": True,
        "trigger": comment_trigger(keywords=["clone"], next_post_word=" clone "),
        "flow": FOLLOW_GATE_FLOW,
    }
    created = (await authed.post("/api/automations", json=body)).json()
    trigger = created["trigger"]
    assert trigger["next_post_word"] == "clone"
    assert trigger["waiting_since"]

    # Saving again from the editor (which sends waiting_since back) keeps the start time
    body["trigger"] = trigger
    updated = (await authed.put(f"/api/automations/{created['id']}", json=body)).json()
    assert updated["trigger"]["waiting_since"] == trigger["waiting_since"]
