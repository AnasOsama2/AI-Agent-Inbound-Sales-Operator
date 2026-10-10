from types import SimpleNamespace

import pytest
from salma.evolution_channel import normalize_evolution


def channel():
    return SimpleNamespace(
        id="channel-one",
        business_id="business-one",
        account_id="evolution",
        evolution_instance="salma-one",
        details={},
    )


def payload(**changes):
    return {
        "event": "messages.upsert",
        "instance": "salma-one",
        "data": {
            "key": {
                "id": "message-one",
                "remoteJid": "201000000000@s.whatsapp.net",
                "fromMe": False,
            },
            "message": {"conversation": "مواعيد العمل؟"},
            "messageTimestamp": 1791662400,
        },
        **changes,
    }


def test_evolution_identity_scoped_and_wrapped_text_supported():
    incoming = normalize_evolution(payload(), channel(), 3)
    assert incoming.provider == "evolution"
    assert incoming.business_id == "business-one" and incoming.config_version == 3
    assert incoming.inbox_id == "channel-one"
    assert incoming.text == "مواعيد العمل؟"
    data = payload()
    data["data"]["message"] = {
        "ephemeralMessage": {"message": {"extendedTextMessage": {"text": "hello"}}}
    }
    assert normalize_evolution(data, channel(), 3).text == "hello"


def test_evolution_rejects_wrong_instance_and_ignores_history_groups_echoes():
    with pytest.raises(ValueError):
        normalize_evolution(payload(instance="another-tenant"), channel(), 3)
    assert normalize_evolution(payload(event="messages.set"), channel(), 3) is None
    data = payload()
    data["data"]["key"]["fromMe"] = True
    assert normalize_evolution(data, channel(), 3) is None
    data = payload()
    data["data"]["key"]["remoteJid"] = "group@g.us"
    assert normalize_evolution(data, channel(), 3) is None
    data = payload()
    data["data"]["type"] = "append"
    assert normalize_evolution(data, channel(), 3) is None


def test_evolution_cutover_ignores_historical_messages():
    row = channel()
    row.details = {"direct_since": 1791662500}
    assert normalize_evolution(payload(), row, 3) is None


@pytest.fixture
def direct_client():
    import hashlib
    import os
    from uuid import uuid4

    from fastapi.testclient import TestClient
    from salma.api import app
    from salma.db import session
    from salma.platform_models import Business, Channel, ConfigVersion

    if not os.environ.get("TEST_DATABASE_URL"):
        pytest.skip("PostgreSQL integration")
    with session() as db, db.begin():
        business = Business(name="Direct WhatsApp", draft={}, published_version=1)
        db.add(business)
        db.flush()
        config = {"name": "Direct WhatsApp", "languages": ["English", "Arabic"]}
        db.add(
            ConfigVersion(
                business_id=business.id, version=1, config=config, catalog=[], document_ids=[]
            )
        )
        row = Channel(
            id=str(uuid4()),
            business_id=business.id,
            kind="whatsapp",
            name="Direct",
            account_id="evolution",
            inbox_id=str(uuid4()),
            evolution_instance="test-" + uuid4().hex,
            credentials="not-used-by-webhook",
            webhook_secret_hash=hashlib.sha256(b"test-secret").hexdigest(),
            live_enabled=True,
            status="ready",
            details={"transport": "evolution"},
        )
        db.add(row)
        db.flush()
        context = {"business": business.id, "channel": row.id, "instance": row.evolution_instance}
    with TestClient(app) as client:
        yield client, context


def direct_payload(context, **changes):
    value = payload(instance=context["instance"])
    value["data"]["message"]["conversation"] = "hello"
    value["data"]["key"]["id"] = changes.pop("id", "incoming-one")
    value["data"]["key"]["fromMe"] = changes.pop("from_me", False)
    value.update(changes)
    return value


def test_direct_webhook_deduplication_delivery_echo_and_human_takeover(direct_client):
    from salma.db import Conversation, Message, session
    from salma.settings import Settings
    from salma.store import ownership
    from salma.trust import Decision
    from salma.worker import Worker

    client, ctx = direct_client
    route = f"/webhooks/evolution/{ctx['channel']}/test-secret"
    assert client.post(route + "wrong", json=direct_payload(ctx)).status_code == 401
    assert (
        client.post(route, json=direct_payload(ctx, instance="foreign-instance")).status_code == 422
    )
    first = client.post(route, json=direct_payload(ctx, business_id="foreign-business"))
    assert first.status_code == 202, first.text
    assert client.post(route, json=direct_payload(ctx)).json()["created"] is False
    with session() as db:
        message = db.get(Message, first.json()["message_id"])
        assert message.business_id == ctx["business"]
        key = message.conversation_key
    sent = []
    worker = Worker(
        Settings(agent_mode="simulator"),
        generate=lambda *a: {
            "decision": Decision(intent="greeting", language="en", reply_kind="greeting"),
            "trace": [],
            "usage": [],
        },
        send=lambda conv, item: sent.append(conv.provider) or {"id": "bot-one"},
    )
    assert worker.process_conversation(key)
    assert worker.deliver_conversation(key)
    assert not worker.deliver_conversation(key)
    assert sent == ["evolution"]
    assert client.post(route, json=direct_payload(ctx, id="bot-one", from_me=True)).json()[
        "bot_echo"
    ]
    with session() as db:
        assert db.get(Conversation, key).ownership == "AI_ACTIVE"
    human = direct_payload(ctx, id="staff-one", from_me=True)
    assert client.post(route, json=human).json()["human_takeover"]
    with session() as db:
        assert db.get(Conversation, key).ownership == "HUMAN_ACTIVE"
        ownership(db, key, "resume")
    assert not client.post(route, json=human).json()["human_takeover"]
    with session() as db:
        assert db.get(Conversation, key).ownership == "AI_ACTIVE"


def test_direct_paused_channel_and_old_configuration_never_send(direct_client):
    from salma.db import Message, Outbox, session
    from salma.platform_models import Business, Channel, ConfigVersion
    from salma.settings import Settings
    from salma.trust import Decision
    from salma.worker import Worker

    client, ctx = direct_client
    route = f"/webhooks/evolution/{ctx['channel']}/test-secret"
    with session() as db, db.begin():
        db.get(Channel, ctx["channel"]).live_enabled = False
    first = client.post(route, json=direct_payload(ctx)).json()
    with session() as db:
        m = db.get(Message, first["message_id"])
        assert m.status == "suppressed"
        key = m.conversation_key
        db.get(Channel, ctx["channel"]).live_enabled = True
        db.commit()
    second = client.post(route, json=direct_payload(ctx, id="incoming-two")).json()
    sent = []
    worker = Worker(
        Settings(agent_mode="simulator"),
        generate=lambda *a: {
            "decision": Decision(intent="greeting", language="en", reply_kind="greeting"),
            "trace": [],
            "usage": [],
        },
        send=lambda *a: sent.append(True) or {"id": "bot"},
    )
    assert worker.process_conversation(key)
    with session() as db, db.begin():
        db.get(Business, ctx["business"]).published_version = 2
        db.add(
            ConfigVersion(
                business_id=ctx["business"], version=2, config={}, catalog=[], document_ids=[]
            )
        )
    assert worker.deliver_conversation(key)
    assert not sent
    with session() as db:
        from sqlalchemy import select

        item = db.scalar(select(Outbox).where(Outbox.message_id == second["message_id"]))
        assert item.status == "suppressed"
