import pytest
from salma.adapters import normalize_chatwoot
from salma.trust import Decision, calculate, render, score


def payload(**changes):
    return {
        "event": "message_created",
        "id": 10,
        "account": {"id": 1},
        "inbox": {"id": 2},
        "conversation": {"id": 3},
        "sender": {"id": 4, "type": "contact"},
        "message_type": "incoming",
        "content": "hello",
        "content_type": "text",
        "private": False,
        "created_at": "2026-10-07T10:00:00Z",
        **changes,
    }


def test_normalization_scopes_identity_and_ignores_notes():
    message = normalize_chatwoot(payload(), 1, {2: "whatsapp"})
    assert message.customer_key == "chatwoot:1:2:4"
    assert message.conversation_key == "chatwoot:1:2:3"
    assert normalize_chatwoot(payload(private=True), 1, {2: "whatsapp"}) is None
    assert normalize_chatwoot(payload(message_type="outgoing"), 1, {2: "whatsapp"}) is None


def test_normalization_rejects_wrong_account_and_unapproved_inbox():
    with pytest.raises(ValueError):
        normalize_chatwoot(payload(), 99, {2: "whatsapp"})
    with pytest.raises(ValueError):
        normalize_chatwoot(payload(), 1, {9: "whatsapp"})


def test_evolution_connection_notice_is_not_a_customer_message():
    notice = payload(
        content="🚀 Connection successfully established!",
        sender={"id": 4, "type": "contact", "name": "EvolutionAPI"},
    )
    assert normalize_chatwoot(notice, 1, {2: "whatsapp"}) is None
    assert normalize_chatwoot({**notice, "source_id": "real-whatsapp-message"}, 1, {2: "whatsapp"})
    assert normalize_chatwoot({**notice, "content": "What are your hours?"}, 1, {2: "whatsapp"})
    assert normalize_chatwoot(
        {**notice, "sender": {"id": 4, "type": "contact", "name": "Customer"}}, 1, {2: "whatsapp"}
    )


def test_price_rendered_from_catalog_not_model():
    decision = Decision(
        intent="price",
        reply_kind="service",
        service_ids=["consultation"],
        evidence_ids=["catalog:consultation"],
    )
    reply = render(
        decision,
        {
            "consultation": {
                "name_en": "Consultation",
                "name_ar": "استشارة",
                "price": "500.00",
                "currency": "EGP",
            }
        },
        {},
    )
    assert "500 EGP" in reply.text
    assert reply.handoff is False


def test_quote_only_and_missing_evidence_cannot_invent_price():
    catalog = {
        "custom": {
            "name_en": "Custom project",
            "name_ar": "مشروع مخصص",
            "price": None,
            "currency": "EGP",
        }
    }
    reply = render(
        Decision(
            intent="price",
            reply_kind="service",
            service_ids=["custom"],
            evidence_ids=["catalog:custom"],
        ),
        catalog,
        {},
    )
    assert reply.handoff and "quote" in reply.text.lower()
    reply = render(
        Decision(intent="price", reply_kind="service", service_ids=["custom"]), catalog, {}
    )
    assert reply.handoff and reply.reason == "missing_evidence"


def test_unknown_evidence_and_monetary_knowledge_are_blocked():
    reply = render(
        Decision(intent="business", reply_kind="knowledge", evidence_ids=["fake"]), {}, {}
    )
    assert reply.handoff
    reply = render(
        Decision(intent="business", reply_kind="knowledge", evidence_ids=["doc:1"]),
        {},
        {"doc:1": "Our price is 2 EGP"},
    )
    assert reply.handoff and reply.reason == "monetary_knowledge"


def test_calculator_accepts_catalog_values_and_rejects_code():
    assert calculate("consultation", 2, {"consultation": {"price": "500.00"}}) == "1000.00"
    with pytest.raises(ValueError):
        calculate("__import__('os')", 2, {})
    with pytest.raises(ValueError):
        calculate("consultation", -1, {"consultation": {"price": "500"}})


def test_repeated_signals_do_not_raise_score_and_missing_weights_are_explicit():
    assert score({"budget", "budget"}, {"budget": 10}, {"warm": 10, "hot": 20}) == (10, "warm")
    assert score({"budget"}, {}, {}) == (None, "unconfigured")
