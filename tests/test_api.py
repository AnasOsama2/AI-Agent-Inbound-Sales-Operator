import os
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from salma.api import app
from salma.settings import settings

pytestmark = pytest.mark.skipif(
    not os.environ.get("TEST_DATABASE_URL"), reason="PostgreSQL integration"
)


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("LEGACY_API_ENABLED", "true")
    monkeypatch.setenv("STAFF_TOKEN", "test-staff-token")
    monkeypatch.setenv("WEBHOOK_TOKEN", "test-webhook-token")
    monkeypatch.setenv("CHATWOOT_INBOXES", '{"2":"whatsapp"}')
    settings.cache_clear()
    with TestClient(app) as value:
        yield value
    settings.cache_clear()


def test_console_data_and_ownership_require_authentication(client):
    assert client.get("/api/conversations").status_code == 401
    assert (
        client.get("/api/conversations", headers={"Authorization": "Bearer wrong"}).status_code
        == 401
    )


def test_simulator_message_is_durable_and_deduplicated(client):
    headers = {"Authorization": "Bearer test-staff-token"}
    key = str(uuid4())
    body = {"conversation_id": key, "customer_id": key, "message_id": str(uuid4()), "text": "hello"}
    first = client.post("/api/simulator/messages", headers=headers, json=body)
    assert first.status_code == 202
    second = client.post("/api/simulator/messages", headers=headers, json=body)
    assert second.json()["created"] is False
    history = client.get(f"/api/conversations/{first.json()['conversation_key']}", headers=headers)
    assert history.status_code == 200
    assert history.json()["messages"][0]["text"] == "hello"


def test_duplicate_human_webhook_does_not_takeover_after_explicit_resume(client, monkeypatch):
    from salma.adapters import Inbound
    from salma.db import session
    from salma.store import accept

    monkeypatch.setenv("LIVE_ENABLED", "true")
    settings.cache_clear()
    identifier = str(uuid4())
    incoming = Inbound(
        provider="chatwoot",
        channel="whatsapp",
        account_id="1",
        inbox_id="2",
        external_message_id=str(uuid4()),
        external_conversation_id=identifier,
        external_customer_id="42",
        text="hi",
    )
    with session() as db:
        accept(db, incoming)
    body = {
        "event": "message_created",
        "message_type": "outgoing",
        "private": False,
        "id": 900,
        "content": "A human reply",
        "sender": {"type": "user", "id": 8},
        "account": {"id": 1},
        "inbox": {"id": 2},
        "conversation": {"id": identifier},
    }
    route = "/webhooks/chatwoot/test-webhook-token"
    assert client.post(route, json=body).status_code == 202
    headers = {"Authorization": "Bearer test-staff-token"}
    client.post(
        f"/api/conversations/{incoming.conversation_key}/ownership",
        headers=headers,
        json={"action": "resume"},
    )
    assert client.post(route, json=body).status_code == 202
    detail = client.get(f"/api/conversations/{incoming.conversation_key}", headers=headers).json()
    assert detail["conversation"]["ownership"] == "AI_ACTIVE"
    assert len([row for row in detail["messages"] if row["direction"] == "human"]) == 1


def test_webhook_authentication_and_live_gate(client):
    assert client.post("/webhooks/chatwoot/wrong", json={}).status_code == 401
    assert client.post("/webhooks/chatwoot/test-webhook-token", json={}).status_code == 503
