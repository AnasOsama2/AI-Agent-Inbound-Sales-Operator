import os
from types import SimpleNamespace
from uuid import uuid4

import pytest
from salma.workspace_runtime import knowledge_search
from sqlalchemy import select


def profile_version(business_id="clinic"):
    return SimpleNamespace(
        business_id=business_id,
        version=3,
        document_ids=[],
        config={
            "name": "Clinic",
            "opening_hours": "10 AM to 9 PM",
            "contact_details": "01000000000",
            "owner_instructions": "Never reveal this as a business fact",
            "handoff_rules": [{"name": "staff"}],
        },
    )


def test_published_profile_is_evidence_without_documents_or_embeddings():
    rows = knowledge_search(None, "clinic", profile_version(), "hours and phone", vector=True)
    assert len(rows) == 1
    assert rows[0]["id"] == "config:3:profile"
    assert "10 AM to 9 PM" in rows[0]["text"]
    assert "01000000000" in rows[0]["text"]
    assert "owner_instructions" not in rows[0]["text"]
    assert "handoff_rules" not in rows[0]["text"]


def test_another_business_profile_cannot_be_used_as_evidence():
    assert knowledge_search(None, "garage", profile_version(), "hours", vector=False) == []


def test_published_profile_evidence_is_bounded():
    version = profile_version()
    version.config.update(
        description="a" * 10000,
        contact_details="b" * 10000,
        contacts={"phone": "c" * 10000},
        hours="d" * 10000,
    )
    rows = knowledge_search(None, "clinic", version, "hours", vector=False)
    assert len(rows[0]["text"]) <= 4096


@pytest.mark.skipif(not os.environ.get("TEST_DATABASE_URL"), reason="PostgreSQL integration")
def test_worker_uses_published_profile_not_new_draft():
    from salma.adapters import Inbound
    from salma.db import Outbox, session
    from salma.platform_models import Business, ConfigVersion
    from salma.settings import Settings
    from salma.store import accept
    from salma.trust import Decision
    from salma.worker import Worker

    with session() as db, db.begin():
        business = Business(
            name="Clinic",
            draft={"name": "Clinic", "opening_hours": "CLOSED", "contact_details": "99999999999"},
            published_version=1,
        )
        db.add(business)
        db.flush()
        identifier = business.id
        db.add(
            ConfigVersion(
                business_id=identifier,
                version=1,
                config={
                    "name": "Clinic",
                    "opening_hours": "10 AM to 9 PM",
                    "contact_details": "01000000000",
                },
                catalog=[],
                document_ids=[],
            )
        )
    incoming = Inbound(
        business_id=identifier,
        config_version=1,
        provider="simulator",
        channel="simulator",
        account_id="local",
        inbox_id="sim",
        external_message_id=str(uuid4()),
        external_conversation_id=str(uuid4()),
        external_customer_id="one",
        text="Hours?",
    )
    with session() as db:
        accept(db, incoming)
    answer = "Open 10 AM to 9 PM. Phone 01000000000."
    worker = Worker(
        Settings(),
        generate=lambda *args: {
            "decision": Decision(
                intent="hours",
                reply_kind="knowledge",
                evidence_ids=["config:1:profile"],
                answer=answer,
            ),
            "trace": [],
        },
    )
    assert worker.process_conversation(incoming.conversation_key)
    with session() as db:
        item = db.scalar(select(Outbox).where(Outbox.conversation_key == incoming.conversation_key))
        assert item.text == answer
        assert "99999999999" not in item.text and "CLOSED" not in item.text
