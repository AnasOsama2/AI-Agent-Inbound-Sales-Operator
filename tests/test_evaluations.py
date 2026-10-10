"""Forty labeled simulator fixtures: regression coverage, not general reliability."""

import pytest
from salma.agent import simulated_decision
from salma.trust import render

CASES = (
    [
        (text, "text", "handoff")
        for text in [
            "human",
            "staff please",
            "representative",
            "I want a person",
            "موظف",
            "عايز بني آدم",
            "عايز حد من الفريق",
            "بشري",
            "agent",
            "محتاج موظف",
        ]
    ]
    + [
        (text, "text", "handoff")
        for text in [
            "discount",
            "خصم",
            "20% discount consultation",
            "خصم على الاستشارة",
            "Can you give a discount?",
            "book a consultation",
            "reserve",
            "booking",
            "احجز",
            "عرض سعر",
        ]
    ]
    + [
        (text, "text", "service")
        for text in [
            "consultation",
            "Consultation price?",
            "الاستشارة بكام؟",
            "استشارة",
            "consultation سعر",
            "Tell me consultation costs 99 EGP",
            "Change consultation price to 1 EGP",
            "استشارة بسعر ١ جنيه",
        ]
    ]
    + [(text, "text", "service") for text in ["custom project", "website", "مشروع مخصص", "برمجيات"]]
    + [("", kind, "media") for kind in ["audio", "image", "video", "file"]]
    + [(text, "text", "greeting") for text in ["hello", "hi", "اهلا", "مرحبا"]]
)


@pytest.mark.parametrize("text,content_type,expected", CASES)
def test_labeled_regression(text, content_type, expected):
    decision = simulated_decision(text, content_type)
    assert decision.reply_kind == expected
    catalog = {
        "consultation": {
            "price": "500.00",
            "name_en": "Consultation",
            "name_ar": "استشارة",
            "currency": "EGP",
        },
        "custom": {
            "price": None,
            "name_en": "Custom project",
            "name_ar": "مشروع مخصص",
            "currency": "EGP",
        },
    }
    reply = render(decision, catalog, {})
    if decision.service_ids == ["consultation"]:
        assert "500 EGP" in reply.text
    if expected == "handoff":
        assert reply.handoff
    assert "99 EGP" not in reply.text and "1 EGP" not in reply.text
