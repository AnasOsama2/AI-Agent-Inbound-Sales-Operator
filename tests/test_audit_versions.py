import os
from uuid import uuid4

import pytest
from salma.adapters import Inbound
from salma.db import Conversation, Event, Message, Outbox, event, session
from salma.platform_models import Business
from salma.store import accept
from sqlalchemy import select

pytestmark = pytest.mark.skipif(
    not os.environ.get("TEST_DATABASE_URL"), reason="PostgreSQL integration"
)


def setup_conversation(version=1):
    with session() as db, db.begin():
        business = Business(name="Audit test", draft={}, published_version=version)
        db.add(business)
        db.flush()
        identifier = business.id
    incoming = Inbound(
        business_id=identifier,
        config_version=version,
        provider="simulator",
        channel="simulator",
        account_id="local",
        inbox_id="sim",
        external_message_id=str(uuid4()),
        external_conversation_id=str(uuid4()),
        external_customer_id="one",
        text="Hello",
    )
    with session() as db:
        message_id, _ = accept(db, incoming)
    return incoming, message_id


def test_generation_audit_uses_source_message_version_after_conversation_changes():
    incoming, message_id = setup_conversation()
    with session() as db, db.begin():
        db.get(Conversation, incoming.conversation_key).config_version = 2
        source = db.get(Message, message_id)
        event(db, incoming.conversation_key, "test_generation", {}, source.correlation_id)
    with session() as db:
        row = db.scalar(
            select(Event).where(
                Event.conversation_key == incoming.conversation_key, Event.kind == "test_generation"
            )
        )
        assert row.data["business_id"] == incoming.business_id
        assert row.data["config_version"] == 1


def test_delivery_audit_prefers_scoped_outbox_version_over_message_and_caller_data():
    incoming, message_id = setup_conversation()
    with session() as db, db.begin():
        item = Outbox(
            business_id=incoming.business_id,
            config_version=3,
            message_id=message_id,
            conversation_key=incoming.conversation_key,
            text="Reply",
        )
        db.add(item)
        db.flush()
        event(
            db,
            incoming.conversation_key,
            "test_delivery",
            {
                "outbox_id": item.id,
                "message_id": message_id,
                "business_id": "forged",
                "config_version": 999,
            },
            str(uuid4()),
        )
    with session() as db:
        row = db.scalar(
            select(Event).where(
                Event.conversation_key == incoming.conversation_key, Event.kind == "test_delivery"
            )
        )
        assert row.data["business_id"] == incoming.business_id
        assert row.data["config_version"] == 3


def test_other_conversation_ids_never_supply_audit_version():
    first, first_message = setup_conversation(1)
    other, other_message = setup_conversation(33)
    with session() as db, db.begin():
        item = Outbox(
            business_id=other.business_id,
            config_version=44,
            message_id=other_message,
            conversation_key=other.conversation_key,
            text="Other business",
        )
        db.add(item)
        db.flush()
        event(
            db,
            first.conversation_key,
            "test_scoped",
            {"outbox_id": item.id, "message_id": other_message},
            db.get(Message, first_message).correlation_id,
        )
    with session() as db:
        row = db.scalar(
            select(Event).where(
                Event.conversation_key == first.conversation_key, Event.kind == "test_scoped"
            )
        )
        assert row.data["business_id"] == first.business_id
        assert row.data["config_version"] == 1


def test_explicit_message_precedes_correlation_and_unknown_correlation_uses_conversation():
    incoming, first_id = setup_conversation(1)
    with session() as db:
        second_id, _ = accept(
            db,
            incoming.model_copy(update={"config_version": 2, "external_message_id": str(uuid4())}),
        )
        db.get(Conversation, incoming.conversation_key).config_version = 3
        event(
            db,
            incoming.conversation_key,
            "test_message",
            {"message_id": second_id},
            db.get(Message, first_id).correlation_id,
        )
        event(db, incoming.conversation_key, "test_fallback", {}, str(uuid4()))
        db.commit()
    with session() as db:
        rows = db.scalars(
            select(Event).where(
                Event.conversation_key == incoming.conversation_key,
                Event.kind.in_(["test_message", "test_fallback"]),
            )
        ).all()
        assert {row.kind: row.data["config_version"] for row in rows} == {
            "test_message": 2,
            "test_fallback": 3,
        }
