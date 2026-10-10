import os
from uuid import uuid4

import pytest
from salma.adapters import Inbound
from salma.db import Conversation, Knowledge, Message, Outbox, session
from salma.platform_models import Business, ConfigVersion
from salma.settings import Settings
from salma.store import accept
from salma.trust import Decision
from salma.worker import Worker
from salma.workspace_runtime import knowledge_search, snapshot_catalog
from sqlalchemy import select

pytestmark = pytest.mark.skipif(
    not os.environ.get("TEST_DATABASE_URL"), reason="PostgreSQL integration"
)


def workspace(name="Clinic", amount=300):
    with session() as db:
        business = Business(name=name, draft={"name": name}, published_version=1)
        db.add(business)
        db.flush()
        version = ConfigVersion(
            business_id=business.id,
            version=1,
            config={"name": name},
            catalog=[
                {
                    "id": "exam",
                    "name": name + " exam",
                    "amount": amount,
                    "currency": "EGP",
                    "price_type": "fixed",
                }
            ],
            document_ids=[],
        )
        db.add(version)
        db.commit()
        return business, version


def incoming(business, text="Price?", external=None):
    message = Inbound(
        business_id=business.id,
        config_version=business.published_version,
        provider="simulator",
        channel="simulator",
        account_id="local",
        inbox_id="sim",
        external_message_id=str(uuid4()),
        external_conversation_id=external or str(uuid4()),
        external_customer_id="customer",
        text=text,
    )
    with session() as db:
        accept(db, message)
    return message


def answer(*args):
    return {
        "decision": Decision(
            intent="price",
            reply_kind="service",
            service_ids=["exam"],
            evidence_ids=["catalog:exam"],
        ),
        "trace": [],
    }


def test_business_customer_keys_and_tool_catalog_are_isolated():
    first, v1 = workspace("Clinic", 300)
    second, v2 = workspace("Garage", 900)
    m1 = incoming(first, external="same")
    m2 = incoming(second, external="same")
    assert m1.customer_key != m2.customer_key and m1.conversation_key != m2.conversation_key
    worker = Worker(Settings(), generate=answer)
    assert (
        worker.execute_tool(
            m1.conversation_key, "price", "service_lookup", {"service_id": "exam"}, version=v1
        )["price"]
        == 300
    )
    assert (
        worker.execute_tool(
            m2.conversation_key, "price", "service_lookup", {"service_id": "exam"}, version=v2
        )["price"]
        == 900
    )
    worker.process_conversation(m1.conversation_key)
    worker.process_conversation(m2.conversation_key)
    with session() as db:
        replies = db.scalars(
            select(Outbox).where(
                Outbox.conversation_key.in_([m1.conversation_key, m2.conversation_key])
            )
        ).all()
        assert {row.business_id for row in replies} == {first.id, second.id}
        assert any("300 EGP" in row.text for row in replies) and any(
            "900 EGP" in row.text for row in replies
        )


def test_config_change_during_generation_suppresses_reply():
    business, _ = workspace()
    message = incoming(business)

    def generate(*args):
        with session() as db:
            db.get(Business, business.id).published_version = 2
            db.commit()
        return answer()

    Worker(Settings(), generate=generate).process_conversation(message.conversation_key)
    with session() as db:
        assert not db.scalar(
            select(Outbox).where(Outbox.conversation_key == message.conversation_key)
        )
        assert (
            db.scalar(
                select(Message.status).where(Message.conversation_key == message.conversation_key)
            )
            == "suppressed"
        )


def test_config_change_before_dispatch_suppresses_pending_reply():
    business, _ = workspace()
    message = incoming(business)
    worker = Worker(Settings(), generate=answer)
    worker.process_conversation(message.conversation_key)
    with session() as db:
        db.get(Business, business.id).published_version = 2
        db.commit()
    worker.deliver_conversation(message.conversation_key)
    with session() as db:
        assert (
            db.scalar(
                select(Outbox.status).where(Outbox.conversation_key == message.conversation_key)
            )
            == "suppressed"
        )


def test_opt_out_acknowledges_once_and_start_is_explicit():
    business, _ = workspace()
    message = incoming(business, "STOP")
    worker = Worker(Settings(), generate=answer)
    worker.process_conversation(message.conversation_key)
    worker.deliver_conversation(message.conversation_key)
    with session() as db:
        accept(
            db, message.model_copy(update={"external_message_id": str(uuid4()), "text": "price"})
        )
    worker.process_conversation(message.conversation_key)
    with session() as db:
        assert db.get(Conversation, message.conversation_key).opted_out
        assert (
            len(
                db.scalars(
                    select(Outbox).where(Outbox.conversation_key == message.conversation_key)
                ).all()
            )
            == 1
        )
        accept(
            db, message.model_copy(update={"external_message_id": str(uuid4()), "text": "START"})
        )
    worker.process_conversation(message.conversation_key)
    with session() as db:
        assert not db.get(Conversation, message.conversation_key).opted_out


def test_retrieval_uses_only_snapshot_documents_and_business():
    first, version = workspace()
    second, _ = workspace()
    document = str(uuid4())
    other = str(uuid4())
    with session() as db:
        stored = db.get(ConfigVersion, version.id)
        stored.document_ids = [document]
        version.document_ids = [document]
        for bid, doc in [(first.id, document), (first.id, other), (second.id, document)]:
            db.add(
                Knowledge(
                    id=str(uuid4()),
                    business_id=bid,
                    document_id=doc,
                    document="Facts",
                    page=1,
                    language="ar",
                    text="parking available",
                    embedding=None,
                )
            )
        db.commit()
        rows = knowledge_search(db, first.id, version, "parking", vector=False)
        document_rows = [row for row in rows if not row["id"].startswith("config:")]
        assert len(document_rows) == 1
        assert document_rows[0]["text"] == "parking available"
        assert [row["id"] for row in rows if row["id"].startswith("config:")] == [
            "config:1:profile"
        ]
    assert snapshot_catalog(version)["exam"]["price"] == 300


def test_generation_searches_published_facts_before_model_reasoning(monkeypatch):
    from types import SimpleNamespace

    from salma import workspace_runtime

    business, version = workspace()
    with session() as db:
        db.get(ConfigVersion, version.id).config = {"name": "Clinic", "hours": "10 AM to 9 PM"}
        db.get(Business, business.id).draft = {"hours": "Draft 24 hours"}
        db.commit()
    captured = {}

    class Completion:
        usage = []

        def __init__(self, _):
            pass

    class FakeAgent:
        def __init__(self, *_, **kwargs):
            pass

        def run(self, *_, **kwargs):
            captured.update(kwargs)
            return {
                "decision": Decision(intent="clarify", reply_kind="clarify"),
                "trace": kwargs["initial_trace"],
            }

    monkeypatch.setattr(workspace_runtime, "GroqCompletion", Completion)
    monkeypatch.setattr(workspace_runtime, "Agent", FakeAgent)
    result = workspace_runtime.generate(
        Worker(Settings(agent_mode="groq")),
        SimpleNamespace(text="What are your hours?", content_type="text", config_version=1),
        SimpleNamespace(business_id=business.id, key="unused"),
        [],
    )
    assert captured["initial_evidence"][0]["id"] == "config:1:profile"
    assert "10 AM" in captured["initial_evidence"][0]["text"]
    assert "Draft 24" not in captured["initial_evidence"][0]["text"]
    assert result["trace"][0]["tool"] == "knowledge_search"


def test_factual_validator_rejects_unsupported_currencyless_price(monkeypatch):
    from types import SimpleNamespace

    from salma import workspace_runtime

    business, _ = workspace()

    class Completion:
        usage = []

        def __init__(self, _):
            pass

        def __call__(self, messages, **kwargs):
            assert "Consultation is 500." in messages[-1]["content"]
            assert "Reject prices" in messages[0]["content"]
            return {"content": '{"supported": false}'}

    class FakeAgent:
        def __init__(self, *_, **kwargs):
            pass

        def run(self, *_, **kwargs):
            return {
                "decision": Decision(
                    intent="price",
                    reply_kind="knowledge",
                    evidence_ids=["config:1:profile"],
                    answer="Consultation is 500.",
                ),
                "trace": kwargs["initial_trace"],
            }

    monkeypatch.setattr(workspace_runtime, "GroqCompletion", Completion)
    monkeypatch.setattr(workspace_runtime, "Agent", FakeAgent)
    result = workspace_runtime.generate(
        Worker(Settings(agent_mode="groq")),
        SimpleNamespace(text="Consultation?", content_type="text", config_version=1),
        SimpleNamespace(business_id=business.id, key="unused"),
        [],
    )
    assert result["decision"].handoff_reason == "unsupported_answer"


def test_explicit_human_handoff_uses_published_rule_ack_and_destination():
    business, version = workspace()
    with session() as db:
        db.get(ConfigVersion, version.id).config = {
            "name": "Clinic",
            "handoff_rules": [
                {
                    "id": "reception",
                    "name": "Reception",
                    "destination": "Front desk",
                    "acknowledgment": "Our front desk will help.",
                    "examples": ["I want to speak with a human"],
                    "enabled": True,
                }
            ],
        }
        db.commit()
    message = incoming(business, "I want a human")
    worker = Worker(Settings(agent_mode="groq", groq_api_key=""))
    worker.process_conversation(message.conversation_key)
    with session() as db:
        conversation = db.get(Conversation, message.conversation_key)
        item = db.scalar(select(Outbox).where(Outbox.conversation_key == message.conversation_key))
        assert conversation.ownership == "HANDOFF_PENDING"
        assert conversation.handoff["rule_id"] == "reception"
        assert conversation.handoff["destination"] == "Front desk"
        assert item.text == "Our front desk will help."
