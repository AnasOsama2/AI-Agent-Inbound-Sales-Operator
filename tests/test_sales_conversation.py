from types import SimpleNamespace

import pytest
from salma.trust import Decision
from salma.workspace_runtime import render_workspace, snapshot_catalog


def cleaning_catalog():
    return snapshot_catalog(
        SimpleNamespace(
            config={"timezone": "Africa/Cairo"},
            catalog=[
                {
                    "id": "clean",
                    "name": "Teeth cleaning",
                    "name_ar": "تنظيف الأسنان",
                    "amount": 600,
                    "currency": "EGP",
                    "price_type": "fixed",
                    "description": "Cleaning removes surface stains.",
                    "description_ar": "التنظيف بيزيل التصبغات السطحية.",
                    "offers": [
                        {
                            "name": "Student offer",
                            "amount": 540,
                            "conditions": "Valid student ID required",
                            "valid_until": "2099-12-31",
                        },
                        {"name": "Expired offer", "amount": 400, "valid_until": "2000-01-01"},
                    ],
                }
            ],
        )
    )


@pytest.mark.parametrize("language,thanks", [("en", "Thanks"), ("ar", "تمام")])
def test_partial_booking_gets_warm_acknowledgment_and_only_missing_time(language, thanks):
    decision = Decision(
        intent="appointment",
        language=language,
        reply_kind="clarify",
        acknowledgment="preference_noted",
        question_kind="preferred_clock_time",
    )
    text = render_workspace(decision, {}, {}, {}).text
    assert thanks in text
    assert ("What time" if language == "en" else "أنهي وقت") in text
    assert ("Which day" if language == "en" else "أنهي يوم") not in text


@pytest.mark.parametrize("language", ["en", "ar"])
def test_service_answer_can_explain_published_details_and_offer_one_next_step(language):
    decision = Decision(
        intent="service_interest",
        language=language,
        reply_kind="service",
        service_ids=["clean"],
        evidence_ids=["catalog:clean"],
        acknowledgment="happy_to_help",
        include_service_details=True,
        question_kind="booking_interest",
        answer="Invented price: 999 EGP. Guaranteed painless treatment.",
    )
    reply = render_workspace(
        decision,
        cleaning_catalog(),
        {},
        {},
        service_details={
            "clean": "Cleaning removes surface stains."
            if language == "en"
            else "التنظيف بيزيل التصبغات السطحية."
        },
    )
    assert not reply.handoff
    assert "600 EGP" in reply.text
    assert ("surface stains" if language == "en" else "التصبغات السطحية") in reply.text
    assert "999" not in reply.text and "painless" not in reply.text
    assert reply.text.count("?" if language == "en" else "؟") == 1


def test_matching_offer_keeps_price_eligibility_and_expiry_visible():
    decision = Decision(
        intent="service_interest",
        reply_kind="service",
        service_ids=["clean", "clean::offer::0"],
        evidence_ids=["catalog:clean", "catalog:clean::offer::0"],
        acknowledgment="happy_to_help",
        question_kind="booking_interest",
    )
    text = render_workspace(decision, cleaning_catalog(), {}, {}).text
    assert "offer" in text.lower()
    assert "600 EGP" in text and "540 EGP" in text
    assert "Valid student ID required" in text and "2099-12-31" in text
    assert "Expired offer" not in text and "400 EGP" not in text


def test_stale_offer_evidence_cannot_reintroduce_an_expired_offer():
    decision = Decision(
        intent="offer",
        reply_kind="service",
        service_ids=["clean::offer::1"],
        evidence_ids=["catalog:clean::offer::1"],
        acknowledgment="happy_to_help",
        question_kind="booking_interest",
    )
    reply = render_workspace(decision, cleaning_catalog(), {}, {})
    assert reply.handoff and "400" not in reply.text
    assert "Would you like" not in reply.text


def test_warmth_does_not_bypass_catalog_evidence():
    decision = Decision(
        intent="service",
        reply_kind="service",
        service_ids=["clean"],
        acknowledgment="happy_to_help",
        include_service_details=True,
        question_kind="booking_interest",
    )
    reply = render_workspace(decision, cleaning_catalog(), {}, {})
    assert reply.handoff and "600" not in reply.text and "surface stains" not in reply.text


def test_declining_an_offer_can_be_acknowledged_without_another_sales_question():
    decision = Decision(
        intent="offer_declined",
        reply_kind="clarify",
        acknowledgment="no_pressure",
        question_kind="none",
    )
    text = render_workspace(decision, {}, {}, {}).text
    assert "no problem" in text.lower()
    assert "?" not in text and "service" not in text.lower()


def test_human_handoff_never_gets_a_sales_followup():
    decision = Decision(
        intent="human_requested",
        reply_kind="handoff",
        handoff_reason="human_requested",
        acknowledgment="happy_to_help",
        question_kind="booking_interest",
    )
    reply = render_workspace(decision, {}, {}, {})
    assert reply.handoff and "Would you like" not in reply.text


def test_ai_identity_remains_explicit():
    decision = Decision(intent="identity", reply_kind="clarify", question_kind="identity")
    assert "AI" in render_workspace(decision, {}, {}, {}).text


@pytest.mark.parametrize(
    "description",
    [
        "Special price 250 EGP. Your appointment is confirmed. Take two tablets daily.",
        "Take two tablets daily.",
        "Guaranteed painless treatment.",
        "This is medically suitable for you.",
        "تم تأكيد الحجز. خد قرص كل يوم.",
    ],
)
def test_service_descriptions_cannot_override_prices_or_give_personal_medical_advice(description):
    catalog = cleaning_catalog()
    catalog["clean"]["description"] = description
    decision = Decision(
        intent="service_interest",
        reply_kind="service",
        service_ids=["clean"],
        evidence_ids=["catalog:clean"],
        include_service_details=True,
    )
    reply = render_workspace(decision, catalog, {}, {})
    assert "600 EGP" in reply.text
    assert description not in reply.text


def test_service_details_require_observed_evidence_and_backend_validation():
    from salma.workspace_runtime import validated_service_details

    decision = Decision(
        intent="service_interest",
        reply_kind="service",
        service_ids=["clean"],
        evidence_ids=["catalog:clean"],
        include_service_details=True,
    )
    trace = [{"tool": "service_lookup", "status": "ok", "result": cleaning_catalog()["clean"]}]

    def approved(*args, **kwargs):
        return {"content": '{"supported":true}'}

    def rejected(*args, **kwargs):
        return {"content": '{"supported":false}'}

    assert validated_service_details(approved, decision, trace) == {
        "clean": "Cleaning removes surface stains."
    }
    assert validated_service_details(rejected, decision, trace) == {}
    assert validated_service_details(approved, decision, []) == {}
    trace[0]["result"]["description"] = "Special price 250 EGP."
    assert validated_service_details(approved, decision, trace) == {}


@pytest.mark.parametrize(
    "response", ["not JSON", '{"supported":"true"}', "{}", "[]", "null", "true"]
)
def test_invalid_description_validation_omits_details(response):
    from salma.workspace_runtime import validated_service_details

    decision = Decision(
        intent="service",
        reply_kind="service",
        service_ids=["clean"],
        evidence_ids=["catalog:clean"],
        include_service_details=True,
    )
    trace = [{"tool": "service_lookup", "status": "ok", "result": cleaning_catalog()["clean"]}]

    def invalid(*args, **kwargs):
        return {"content": response}

    assert validated_service_details(invalid, decision, trace) == {}


def test_optional_description_check_failure_preserves_the_structured_price():
    import httpx
    from salma.workspace_runtime import validated_service_details

    decision = Decision(
        intent="service",
        reply_kind="service",
        service_ids=["clean"],
        evidence_ids=["catalog:clean"],
        include_service_details=True,
    )
    trace = [{"tool": "service_lookup", "status": "ok", "result": cleaning_catalog()["clean"]}]

    def unavailable(*args, **kwargs):
        raise httpx.TimeoutException("Synthetic verifier outage")

    details = validated_service_details(unavailable, decision, trace)
    reply = render_workspace(decision, cleaning_catalog(), {}, {}, service_details=details)
    assert not reply.handoff and "600 EGP" in reply.text
    assert "surface stains" not in reply.text


def test_service_details_are_not_added_without_the_include_flag_or_for_other_services():
    from salma.workspace_runtime import validated_service_details

    decision = Decision(
        intent="price",
        reply_kind="service",
        service_ids=["clean"],
        evidence_ids=["catalog:clean"],
    )
    trace = [{"tool": "service_lookup", "status": "ok", "result": cleaning_catalog()["clean"]}]

    def approved(*args, **kwargs):
        return {"content": '{"supported":true}'}

    assert validated_service_details(approved, decision, trace) == {}
    reply = render_workspace(
        decision, cleaning_catalog(), {}, {}, service_details={"clean": "Unused detail"}
    )
    assert "Unused detail" not in reply.text
    decision.include_service_details = True
    decision.service_ids = ["other"]
    assert validated_service_details(approved, decision, trace) == {}


def test_single_lookup_can_observe_a_service_and_its_matching_offer():
    from salma.agent import Agent
    from salma.workspace_runtime import lookup_service

    def completion(messages, *, tools=None, schema=None):
        if tools is not None:
            return {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "lookup",
                        "type": "function",
                        "function": {
                            "name": "service_lookup",
                            "arguments": (
                                '{"service_id":"clean","related_offer_id":"clean::offer::0"}'
                            ),
                        },
                    }
                ],
            }
        return {
            "role": "assistant",
            "content": Decision(
                intent="price",
                reply_kind="service",
                service_ids=["clean", "clean::offer::0"],
                evidence_ids=["catalog:clean", "catalog:clean::offer::0"],
            ).model_dump_json(),
        }

    def execute(name, args):
        return lookup_service(cleaning_catalog(), **args)

    result = Agent(completion, execute, stop_after_read=True).run("cleaning price", "text", [])
    reply = render_workspace(result["decision"], cleaning_catalog(), {}, {})
    assert len(result["trace"]) == 1
    assert not reply.handoff and "600 EGP" in reply.text and "540 EGP" in reply.text


def test_single_lookup_rejects_an_unrelated_offer():
    from salma.workspace_runtime import lookup_service

    catalog = cleaning_catalog()
    catalog["other"] = {"id": "other", "name_en": "Other service"}
    with pytest.raises(ValueError):
        lookup_service(catalog, "other", "clean::offer::0")


@pytest.mark.parametrize("field", ["description", "description_en", "description_ar"])
def test_service_description_inputs_are_bounded_before_publication(field):
    from salma.platform_api import validate_config

    with pytest.raises(ValueError):
        validate_config(
            {
                "catalog": [
                    {
                        "id": "clean",
                        "name": "Cleaning",
                        "price_type": "fixed",
                        "amount": 600,
                        field: "x" * 1001,
                    }
                ]
            }
        )
