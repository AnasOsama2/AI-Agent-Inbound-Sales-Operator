"""Recheck committed state after durable dispatch intent releases database locks."""

import os
from uuid import uuid4

import pytest
from salma.adapters import Inbound
from salma.db import Conversation, Message, Outbox, session
from salma.platform_models import Business
from salma.settings import Settings
from salma.worker import Worker
from sqlalchemy import select

pytestmark = pytest.mark.skipif(
    not os.environ.get("TEST_DATABASE_URL"), reason="PostgreSQL integration"
)


@pytest.mark.parametrize("change", ["publish", "takeover", "optout"])
def test_state_change_between_dispatch_intent_and_final_check_suppresses_reply(monkeypatch, change):
    import salma.worker as worker_module
    from salma.store import accept, ownership

    with session() as db, db.begin():
        business = Business(name="Dispatch Boundary", draft={}, published_version=1)
        db.add(business)
        db.flush()
        business_id = business.id
    incoming = Inbound(
        business_id=business_id,
        config_version=1,
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
        db.get(Message, message_id).status = "processed"
        db.add(
            Outbox(
                business_id=business_id,
                config_version=1,
                message_id=message_id,
                conversation_key=incoming.conversation_key,
                text="Old reply",
                kind="reply",
            )
        )
        db.commit()
    original_session = session
    changed = []

    def dispatch_session():
        db = original_session()
        # Retain the entity, as a long-lived expire_on_commit=False session can do.
        # Refreshing the SQL SELECT alone does not refresh this identity-map object.
        db.info["business"] = db.get(Business, business_id)
        original_commit = db.commit

        def commit_then_change():
            original_commit()
            if changed:
                return
            changed.append(change)
            with original_session() as other:
                if change == "publish":
                    other.get(Business, business_id).published_version = 2
                    other.commit()
                elif change == "takeover":
                    ownership(other, incoming.conversation_key, "takeover")
                else:
                    accept(
                        other,
                        incoming.model_copy(
                            update={"external_message_id": str(uuid4()), "text": "STOP"}
                        ),
                    )

        db.commit = commit_then_change
        return db

    monkeypatch.setattr(worker_module, "session", dispatch_session)
    assert Worker(Settings()).deliver_conversation(incoming.conversation_key)
    assert changed == [change]
    with original_session() as db:
        item = db.scalar(select(Outbox).where(Outbox.message_id == message_id))
        assert item.status == "suppressed"
        if change == "publish":
            assert "Configuration changed" in item.error
        elif change == "takeover":
            assert db.get(Conversation, incoming.conversation_key).ownership == "HUMAN_ACTIVE"
        else:
            assert db.get(Conversation, incoming.conversation_key).opted_out
