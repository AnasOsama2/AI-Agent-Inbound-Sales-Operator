import os
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from salma.adapters import Inbound
from salma.db import Conversation, Customer, Message, session
from salma.platform_models import Business, ConfigVersion
from salma.settings import Settings
from salma.store import accept
from salma.trust import Decision
from salma.worker import Worker

pytestmark = pytest.mark.skipif(
    not os.environ.get("TEST_DATABASE_URL"), reason="PostgreSQL integration"
)


def conversation_with_profile(name="Clinic"):
    with session() as db, db.begin():
        business = Business(name=name, draft={}, published_version=1)
        db.add(business)
        db.flush()
        business_id = business.id
        db.add(
            ConfigVersion(
                business_id=business_id,
                version=1,
                config={"name": name},
                catalog=[],
                document_ids=[],
            )
        )
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
        text="I need an appointment on Monday",
    )
    with session() as db:
        message_id, _ = accept(db, incoming)
        source = db.get(Message, message_id)
        source.status = "processed"
        db.get(Customer, incoming.customer_key).profile = {
            "name": "Mona",
            "phone": "01000000000",
            "requirements": "x" * 2000,
        }
        db.get(Conversation, incoming.conversation_key).lead = {
            "requirements": "y" * 4000,
            "service_interest": ["exam"],
        }
        db.add(
            Message(
                business_id=business_id,
                conversation_key=incoming.conversation_key,
                external_id=str(uuid4()),
                direction="outgoing",
                text="What time works for you?",
                status="delivered",
                correlation_id=source.correlation_id,
                config_version=1,
            )
        )
        db.commit()
        accept(
            db,
            incoming.model_copy(
                update={"external_message_id": str(uuid4()), "text": "Please connect me to a human"}
            ),
        )
    return incoming


def handoff_worker():
    return Worker(
        Settings(),
        generate=lambda *args: {
            "decision": Decision(
                intent="handoff", reply_kind="handoff", handoff_reason="human_requested"
            ),
            "trace": [],
        },
    )


def test_handoff_carries_bounded_customer_lead_and_prior_context():
    incoming = conversation_with_profile()
    assert handoff_worker().process_conversation(incoming.conversation_key)
    with session() as db:
        summary = db.get(Conversation, incoming.conversation_key).handoff
        assert summary["customer_details"]["name"] == "Mona"
        assert summary["customer_details"]["phone"] == "01000000000"
        assert len(summary["customer_details"]["requirements"]) <= 500
        assert len(summary["lead_details"]["requirements"]) <= 1000
        assert "Monday" in summary["context"][0]["content"]
        assert "What time" in summary["context"][1]["content"]
        assert "human" in summary["context"][-1]["content"]
        assert len(summary["context"]) <= 7


def test_owner_detail_has_customer_before_handoff_and_rejects_foreign_profile(monkeypatch):
    from salma import conversation_api

    first = conversation_with_profile()
    other = conversation_with_profile("Garage")
    monkeypatch.setattr(conversation_api, "require_business", lambda *args: None)
    app = FastAPI()
    app.include_router(conversation_api.router)
    with TestClient(app) as client:
        url = f"/api/businesses/{first.business_id}/conversations/{first.conversation_key}"
        response = client.get(url)
        assert response.status_code == 200
        assert response.json()["customer"] == {
            "key": first.customer_key,
            "profile": {"name": "Mona", "phone": "01000000000", "requirements": "x" * 2000},
        }
        with session() as db, db.begin():
            db.get(Customer, other.customer_key).profile = {"name": "Private other customer"}
            db.get(Conversation, first.conversation_key).customer_key = other.customer_key
        response = client.get(url)
        assert response.json()["customer"] is None
        assert "Private other customer" not in response.text
    assert handoff_worker().process_conversation(first.conversation_key)
    with session() as db:
        summary = db.get(Conversation, first.conversation_key).handoff
        assert summary["customer_details"] == {}
