import os
from uuid import uuid4

import httpx
import pytest
from salma.adapters import Inbound
from salma.db import Conversation, Message, Outbox, session
from salma.settings import Settings
from salma.store import accept, ownership
from salma.trust import Decision
from salma.worker import Worker
from sqlalchemy import select

pytestmark = pytest.mark.skipif(
    not os.environ.get("TEST_DATABASE_URL"), reason="PostgreSQL integration"
)


def incoming(provider="simulator", text="consultation price"):
    key = str(uuid4())
    return Inbound(
        provider=provider,
        channel="simulator",
        account_id="1",
        inbox_id="2",
        external_message_id=str(uuid4()),
        external_conversation_id=key,
        external_customer_id=key,
        text=text,
    )


def test_takeover_during_generation_suppresses_outbox():
    message = incoming()
    with session() as db:
        accept(db, message)

    def generate(*args):
        with session() as db:
            ownership(db, message.conversation_key, "takeover")
        return {
            "decision": Decision(
                intent="price",
                reply_kind="service",
                service_ids=["consultation"],
                evidence_ids=["catalog:consultation"],
            ),
            "trace": [],
        }

    worker = Worker(Settings(), generate=generate)
    assert worker.process_conversation(message.conversation_key)
    with session() as db:
        assert (
            db.scalar(select(Outbox).where(Outbox.conversation_key == message.conversation_key))
            is None
        )
        assert (
            db.scalar(
                select(Message.status).where(Message.conversation_key == message.conversation_key)
            )
            == "suppressed"
        )


def test_duplicate_processing_creates_only_one_reply():
    message = incoming()
    with session() as db:
        accept(db, message)
    worker = Worker(Settings())
    assert worker.process_conversation(message.conversation_key)
    assert not worker.process_conversation(message.conversation_key)
    worker.deliver_conversation(message.conversation_key)
    with session() as db:
        items = db.scalars(
            select(Outbox).where(Outbox.conversation_key == message.conversation_key)
        ).all()
        assert len(items) == 1 and items[0].status == "simulated"
        assert "500 EGP" in items[0].text


def test_ambiguous_send_is_not_retried():
    message = incoming(provider="chatwoot")
    with session() as db:
        accept(db, message)

    def send(*args):
        raise httpx.ReadTimeout("uncertain delivery")

    worker = Worker(Settings(live_enabled=True), send=send)
    worker.process_conversation(message.conversation_key)
    assert worker.deliver_conversation(message.conversation_key)
    assert not worker.deliver_conversation(message.conversation_key)
    with session() as db:
        item = db.scalar(select(Outbox).where(Outbox.conversation_key == message.conversation_key))
        assert item.status == "ambiguous"


def test_restart_during_send_blocks_followup_until_staff_reconciliation():
    message = incoming()
    with session() as db:
        accept(db, message)
    worker = Worker(Settings())
    worker.process_conversation(message.conversation_key)
    with session() as db:
        item = db.scalar(select(Outbox).where(Outbox.conversation_key == message.conversation_key))
        item.status = "sending"
        db.commit()
        accept(
            db, message.model_copy(update={"external_message_id": str(uuid4()), "text": "hello"})
        )
    worker.deliver_conversation(message.conversation_key)
    with session() as db:
        assert db.get(Conversation, message.conversation_key).ownership == "HANDOFF_PENDING"
    worker.process_conversation(message.conversation_key)
    with session() as db:
        assert (
            len(
                db.scalars(
                    select(Outbox).where(Outbox.conversation_key == message.conversation_key)
                ).all()
            )
            == 1
        )


def test_delivery_confirmation_during_send_remains_delivered_in_history():
    from threading import Event as ThreadEvent
    from threading import Thread

    from salma.api import outgoing_event

    message = incoming(provider="chatwoot")
    with session() as db:
        accept(db, message)
    completed = ThreadEvent()
    errors = []
    threads = []
    config = Settings(live_enabled=True, chatwoot_inboxes={2: "whatsapp"})

    def send(conversation, item):
        def callback():
            try:
                with session() as db:
                    outgoing_event(
                        db,
                        {
                            "event": "message_updated",
                            "id": 998,
                            "account": {"id": 1},
                            "inbox": {"id": 2},
                            "conversation": {"id": message.external_conversation_id},
                            "content_attributes": {"salma_outbox_id": item.id},
                            "status": "delivered",
                        },
                        config,
                    )
            except Exception as exc:
                errors.append(exc)
            finally:
                completed.set()

        thread = Thread(target=callback)
        threads.append(thread)
        thread.start()
        completed.wait(0.2)
        return {"id": 998, "status": "sent"}

    worker = Worker(config, send=send)
    worker.process_conversation(message.conversation_key)
    worker.deliver_conversation(message.conversation_key)
    threads[0].join(timeout=3)
    assert completed.is_set() and not errors
    with session() as db:
        assert (
            db.scalar(
                select(Outbox.status).where(Outbox.conversation_key == message.conversation_key)
            )
            == "delivered"
        )
        assert (
            db.scalar(
                select(Message.status).where(
                    Message.conversation_key == message.conversation_key,
                    Message.direction == "outgoing",
                )
            )
            == "delivered"
        )


def test_rapid_messages_are_processed_in_order_across_worker_instances():
    message = incoming(text="consultation")
    with session() as db:
        accept(db, message)
        accept(
            db,
            message.model_copy(
                update={"external_message_id": str(uuid4()), "text": "I want a human"}
            ),
        )
        accept(
            db, message.model_copy(update={"external_message_id": str(uuid4()), "text": "hello"})
        )
    first, second = Worker(Settings()), Worker(Settings())
    assert first.process_conversation(message.conversation_key)
    assert not second.process_conversation(message.conversation_key)
    second.deliver_conversation(message.conversation_key)
    assert second.process_conversation(message.conversation_key)
    second.deliver_conversation(message.conversation_key)
    first.process_conversation(message.conversation_key)
    with session() as db:
        rows = db.scalars(
            select(Message)
            .where(
                Message.conversation_key == message.conversation_key,
                Message.direction == "incoming",
            )
            .order_by(Message.id)
        ).all()
        assert [row.status for row in rows] == ["processed", "processed", "suppressed"]
        replies = db.scalars(
            select(Outbox)
            .where(Outbox.conversation_key == message.conversation_key)
            .order_by(Outbox.id)
        ).all()
        assert len(replies) == 2 and "500 EGP" in replies[0].text and replies[1].kind == "handoff"


def test_handoff_acknowledged_once_then_ai_stays_silent():
    message = incoming(text="I want a human")
    with session() as db:
        accept(db, message)
    worker = Worker(Settings())
    worker.process_conversation(message.conversation_key)
    worker.deliver_conversation(message.conversation_key)
    with session() as db:
        accept(
            db, message.model_copy(update={"external_message_id": str(uuid4()), "text": "hello"})
        )
    worker.process_conversation(message.conversation_key)
    with session() as db:
        assert db.get(Conversation, message.conversation_key).ownership == "HANDOFF_PENDING"
        assert (
            len(
                db.scalars(
                    select(Outbox).where(Outbox.conversation_key == message.conversation_key)
                ).all()
            )
            == 1
        )
