from types import SimpleNamespace

import pytest
from salma.trust import Decision
from salma.workspace_runtime import platform_handoff, render_workspace, snapshot_catalog


@pytest.mark.parametrize(
    "question,language,expected",
    [
        (
            "preferred_clock_time",
            "en",
            "What time would you prefer? Staff will confirm availability.",
        ),
        ("preferred_day", "en", "Which day would you prefer? Staff will confirm availability."),
        ("preferred_clock_time", "ar", "أنهي وقت مناسب ليك؟ الفريق هيأكد الموعد."),
        ("preferred_day", "ar", "أنهي يوم مناسب ليك؟ الفريق هيأكد الموعد."),
    ],
)
def test_appointment_followup_asks_only_for_the_missing_preference(question, language, expected):
    decision = Decision(
        intent="appointment", reply_kind="clarify", question_kind=question, language=language
    )
    assert render_workspace(decision, {}, {}, {}).text == expected


def test_partial_appointment_preferences_are_preserved_in_customer_profile():
    from salma.agent import ProfileArgs
    from salma.db import Customer
    from salma.store import update_customer
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    engine = create_engine("sqlite://")
    Customer.__table__.create(engine)
    with Session(engine) as db:
        db.add(Customer(key="appointment", profile={"name": "Customer"}))
        db.flush()
        day = ProfileArgs(preferred_day="tomorrow").model_dump(exclude_none=True)
        update_customer(db, "appointment", day)
        clock = ProfileArgs(preferred_clock_time="4 PM").model_dump(exclude_none=True)
        result = update_customer(db, "appointment", clock)
        assert result == {
            "name": "Customer",
            "preferred_day": "tomorrow",
            "preferred_clock_time": "4 PM",
        }
    engine.dispose()


def test_identity_and_published_offer_are_not_human_requests():
    assert platform_handoff("Are you a real person?") is None
    assert platform_handoff("Are you human") is None
    assert platform_handoff("I do not want a human") is None
    assert platform_handoff("I don't want to speak to a human") is None
    assert platform_handoff("مش عايز اكلم موظف") is None
    assert platform_handoff("Human please") == "human_requested"
    assert platform_handoff("Do you offer a student discount?") is None
    assert platform_handoff("What is your booking policy?") is None
    assert platform_handoff("I want to speak to a human") == "human_requested"
    assert platform_handoff("عايز اكلم موظف") == "human_requested"


def test_prompt_offer_index_uses_the_business_timezone(monkeypatch):
    from datetime import date

    from salma import workspace_runtime

    class Clock:
        @staticmethod
        def now(tz):
            return SimpleNamespace(
                date=lambda: (
                    date(2026, 10, 8) if tz.key == "America/Los_Angeles" else date(2026, 10, 9)
                )
            )

    monkeypatch.setattr(workspace_runtime, "datetime", Clock)
    prompt = workspace_runtime.build_prompt(
        {
            "timezone": "America/Los_Angeles",
            "catalog": [
                {
                    "id": "exam",
                    "name": "Exam",
                    "amount": 300,
                    "offers": [{"name": "Today only", "amount": 250, "valid_until": "2026-10-08"}],
                }
            ],
        }
    )
    assert "Today only" in prompt


def test_numbers_in_grounded_facts_are_allowed_but_money_is_structured():
    d = Decision(
        intent="hours", reply_kind="knowledge", evidence_ids=["doc"], answer="Open 10 AM to 9 PM."
    )
    result = render_workspace(d, {}, {"doc": "Open 10 AM to 9 PM."}, {})
    assert not result.handoff and "10" in result.text
    d.answer = "We have 3 dentists."
    assert not render_workspace(d, {}, {"doc": "We have 3 dentists."}, {}).handoff
    d.answer = "Open from 10 AM to 8 PM."
    assert not render_workspace(d, {}, {"doc": "Open 10:00 to 20:00."}, {}).handoff
    d.answer = "The price is 999 EGP."
    assert render_workspace(d, {}, {"doc": "Open 10 AM to 9 PM."}, {}).handoff


def test_starting_price_never_becomes_final_and_does_not_force_handoff():
    d = Decision(
        intent="price",
        reply_kind="service",
        service_ids=["implant"],
        evidence_ids=["catalog:implant"],
    )
    result = render_workspace(
        d,
        {
            "implant": {
                "name_en": "Implant",
                "name_ar": "زرعة",
                "price": "14000",
                "currency": "EGP",
                "price_kind": "starting_from",
                "conditions": "Final price after exam",
            }
        },
        {},
        {},
    )
    assert not result.handoff and "Starting from 14000 EGP" in result.text and "exam" in result.text


def test_configured_handoff_acknowledgment_and_unknown_rule_validation():
    config = {
        "handoff_rules": [
            {
                "id": "custom",
                "name": "Custom",
                "condition": "Negotiation",
                "destination": "Owner",
                "acknowledgment": "Our owner will review your request.",
                "enabled": True,
            }
        ]
    }
    d = Decision(
        intent="negotiate", reply_kind="handoff", handoff_reason="custom", rule_id="custom"
    )
    result = render_workspace(d, {}, {}, config)
    assert result.handoff and result.text == "Our owner will review your request."
    d.rule_id = "invented"
    assert render_workspace(d, {}, {}, config).reason == "invalid_handoff_rule"


def test_published_offers_are_structured_and_expired_offers_are_hidden():
    result = snapshot_catalog(
        SimpleNamespace(
            catalog=[
                {
                    "id": "clean",
                    "name": "Cleaning",
                    "amount": 600,
                    "price_type": "fixed",
                    "currency": "EGP",
                    "offers": [
                        {"name": "Student", "amount": 540, "conditions": "Valid student ID"},
                        {"name": "Old", "amount": 400, "valid_until": "2000-01-01"},
                    ],
                }
            ],
            config={"timezone": "Africa/Cairo"},
        )
    )
    assert result["clean::offer::0"]["price"] == 540
    assert "clean::offer::1" not in result


def test_unverified_booking_commitment_is_blocked():
    decision = Decision(
        intent="booking",
        reply_kind="knowledge",
        evidence_ids=["doc"],
        answer="Your appointment is confirmed for tomorrow.",
    )
    assert (
        render_workspace(decision, {}, {"doc": "Appointments require staff approval."}, {}).reason
        == "unsupported_commitment"
    )


def test_freeform_price_and_clarification_cannot_bypass_platform_boundary():
    for answer in [
        "Consultation costs five hundred pounds.",
        "The total is USD 999.",
    ]:
        decision = Decision(
            intent="faq", reply_kind="knowledge", evidence_ids=["doc"], answer=answer
        )
        assert render_workspace(decision, {}, {"doc": "Consultations available."}, {}).handoff
    decision = Decision(
        intent="unclear", reply_kind="clarify", answer="Take two tablets every day."
    )
    assert "tablets" not in render_workspace(decision, {}, {}, {}).text
