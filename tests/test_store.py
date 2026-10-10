import os
from uuid import uuid4

import pytest
from salma.adapters import Inbound
from salma.db import Conversation, Message, session
from salma.store import accept, archive_draft, ownership, update_customer
from sqlalchemy import select

pytestmark = pytest.mark.skipif(
    not os.environ.get("TEST_DATABASE_URL"), reason="PostgreSQL integration"
)


@pytest.fixture
def incoming():
    return Inbound(
        provider="simulator",
        channel="simulator",
        account_id="local",
        inbox_id="sim",
        external_message_id=str(uuid4()),
        external_conversation_id=str(uuid4()),
        external_customer_id=str(uuid4()),
        text="hello",
    )


def test_duplicate_is_persisted_once(incoming):
    with session() as db:
        first = accept(db, incoming)
        second = accept(db, incoming)
        assert first[0] == second[0]
        assert first[1] is True and second[1] is False
        assert (
            len(
                db.scalars(
                    select(Message).where(Message.conversation_key == incoming.conversation_key)
                ).all()
            )
            == 1
        )


def test_identity_cannot_change_on_existing_conversation(incoming):
    with session() as db:
        accept(db, incoming)
        forged = incoming.model_copy(update={"external_customer_id": "another"})
        with pytest.raises(ValueError):
            accept(db, forged)


def test_only_explicit_staff_resume_reactivates_closed_conversation(incoming):
    with session() as db:
        accept(db, incoming)
        ownership(db, incoming.conversation_key, "takeover")
        ownership(db, incoming.conversation_key, "close")
        accept(db, incoming.model_copy(update={"external_message_id": str(uuid4())}))
        assert db.get(Conversation, incoming.conversation_key).ownership == "CLOSED"
        ownership(db, incoming.conversation_key, "resume")
        assert db.get(Conversation, incoming.conversation_key).ownership == "AI_ACTIVE"


def test_scoped_customer_update_and_confirmed_archive(incoming):
    with session() as db:
        accept(db, incoming)
        with pytest.raises(ValueError):
            update_customer(db, incoming.customer_key, {"price": 1})
        update_customer(db, incoming.customer_key, {"name": "Sara"})
        with pytest.raises(ValueError):
            archive_draft(db, incoming.conversation_key, "hello")
