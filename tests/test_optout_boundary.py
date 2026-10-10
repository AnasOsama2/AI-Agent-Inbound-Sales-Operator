"""Consent takes effect when inbound messages are persisted, before agent execution."""

import hashlib
import os
from uuid import uuid4

import pytest
from fastapi import HTTPException
from salma.adapters import Inbound
from salma.db import Conversation, Message, Outbox, session
from salma.platform_models import Business, Channel
from salma.store import accept
from sqlalchemy import select

pytestmark = pytest.mark.skipif(
    not os.environ.get("TEST_DATABASE_URL"), reason="PostgreSQL integration"
)


def create_incoming(text="Hello"):
    with session() as db, db.begin():
        business = Business(name="Consent Test", draft={}, published_version=1)
        db.add(business)
        db.flush()
        business_id = business.id
    return Inbound(
        business_id=business_id,
        config_version=1,
        provider="simulator",
        channel="simulator",
        account_id="local",
        inbox_id="sim",
        external_message_id=str(uuid4()),
        external_conversation_id=str(uuid4()),
        external_customer_id="customer",
        text=text,
    )


def persist(incoming, text, identifier=None):
    with session() as db:
        return accept(
            db,
            incoming.model_copy(
                update={"text": text, "external_message_id": identifier or str(uuid4())}
            ),
        )


def test_stop_atomically_suppresses_pending_reply_even_with_old_config_version():
    incoming = create_incoming()
    first_id, _ = persist(incoming, "Hello")
    with session() as db, db.begin():
        db.add(
            Outbox(
                business_id=incoming.business_id,
                config_version=1,
                message_id=first_id,
                conversation_key=incoming.conversation_key,
                text="Old reply",
                kind="reply",
            )
        )
        db.get(Business, incoming.business_id).published_version = 2
    stop_id, created = persist(incoming, "STOP")
    assert created
    with session() as db:
        assert db.get(Conversation, incoming.conversation_key).opted_out
        assert db.get(Message, stop_id).decision == {"control": "optout", "acknowledge": True}
        assert db.scalar(select(Outbox.status).where(Outbox.message_id == first_id)) == "suppressed"


def test_duplicate_stop_after_start_does_not_change_newer_consent():
    incoming = create_incoming()
    stop_id = str(uuid4())
    persisted_id, _ = persist(incoming, "STOP", stop_id)
    persist(incoming, "START")
    duplicate_id, created = persist(incoming, "STOP", stop_id)
    assert duplicate_id == persisted_id and not created
    with session() as db:
        assert not db.get(Conversation, incoming.conversation_key).opted_out


def test_stop_received_during_generation_prevents_generated_reply():
    from salma.settings import Settings
    from salma.trust import Decision
    from salma.worker import Worker

    incoming = create_incoming()
    first_id, _ = persist(incoming, "Hello")

    def generate(*args):
        persist(incoming, "STOP")
        return {"decision": Decision(intent="greeting", reply_kind="greeting"), "trace": []}

    worker = Worker(Settings(), generate=generate)
    assert worker.process_conversation(incoming.conversation_key)
    with session() as db:
        assert db.get(Message, first_id).status == "suppressed"
        assert not db.scalar(select(Outbox.id).where(Outbox.message_id == first_id))


def test_duplicate_start_does_not_undo_a_later_stop():
    incoming = create_incoming()
    start_id = str(uuid4())
    persist(incoming, "START", start_id)
    persist(incoming, "STOP")
    _, created = persist(incoming, "START", start_id)
    assert not created
    with session() as db:
        assert db.get(Conversation, incoming.conversation_key).opted_out


def test_new_stop_while_already_opted_out_does_not_acknowledge_twice():
    incoming = create_incoming()
    persist(incoming, "STOP")
    second_id, _ = persist(incoming, "unsubscribe")
    with session() as db:
        assert db.get(Message, second_id).decision == {"control": "optout", "acknowledge": False}


def test_start_does_not_resume_human_ownership():
    incoming = create_incoming()
    persist(incoming, "STOP")
    with session() as db, db.begin():
        db.get(Conversation, incoming.conversation_key).ownership = "HUMAN_ACTIVE"
    start_id, _ = persist(incoming, "START")
    with session() as db:
        conversation = db.get(Conversation, incoming.conversation_key)
        assert not conversation.opted_out
        assert conversation.ownership == "HUMAN_ACTIVE"
        assert db.get(Message, start_id).decision["control"] == "optin"


def test_only_exact_text_controls_apply():
    incoming = create_incoming()
    persist(incoming, "Can you stop by tomorrow?")
    with session() as db:
        assert not db.get(Conversation, incoming.conversation_key).opted_out
    persist(incoming, "توقف!")
    with session() as db:
        assert db.get(Conversation, incoming.conversation_key).opted_out
    persist(incoming, "ابدأ")
    with session() as db:
        assert not db.get(Conversation, incoming.conversation_key).opted_out


def test_authenticated_paused_webhooks_can_record_human_ownership():
    from salma.workspace_services import resolve_channel_webhook

    incoming = create_incoming()
    identifier, secret = str(uuid4()), str(uuid4())
    with session() as db, db.begin():
        db.add(
            Channel(
                id=identifier,
                business_id=incoming.business_id,
                kind="whatsapp",
                name="Sales",
                account_id=str(int(uuid4().hex[:8], 16)),
                inbox_id="1",
                credentials="encrypted",
                webhook_secret_hash=hashlib.sha256(secret.encode()).hexdigest(),
                live_enabled=False,
            )
        )
    with pytest.raises(HTTPException) as paused:
        resolve_channel_webhook(identifier, secret)
    assert paused.value.status_code == 503
    assert (
        resolve_channel_webhook(identifier, secret, allow_paused=True).business_id
        == incoming.business_id
    )
    with pytest.raises(HTTPException) as bad_secret:
        resolve_channel_webhook(identifier, "invalid", allow_paused=True)
    assert bad_secret.value.status_code == 401
