import json

import pytest


def test_redacts_nested_credentials_and_contact_details_without_losing_evidence():
    from salma.observability import redact

    result = redact(
        {
            "api_key": "secret-value",
            "Authorization": "Bearer private",
            "reply": "Email demo@example.com or +20 100 555 0123; 600 EGP",
            "evidence": [{"id": "doc:abc:1", "text": "Dr. Example treats children"}],
            "content": "lsv2_pt_fake0123456789_abcdef sk-secret123456789012345678901234",
        "arguments": '{"name":"Private Name","api_key":"nested-private","phone":"123"}',
        }
    )
    encoded = json.dumps(result)
    assert "secret-value" not in encoded and "Bearer private" not in encoded
    assert "demo@example.com" not in encoded and "+20 100 555 0123" not in encoded
    assert "lsv2_pt_fake" not in encoded and "sk-secret" not in encoded
    assert "Private Name" not in encoded and "nested-private" not in encoded
    assert result["evidence"][0]["id"] == "doc:abc:1"
    assert "600 EGP" in result["reply"]


def test_flow_records_rejected_draft_validation_and_errors_in_order(caplog):
    from salma.observability import flow, phase

    with caplog.at_level("INFO", logger="salma.flow"):
        with flow({"message_id": 7, "correlation_id": "test-flow"}, enabled=False) as context:
            with phase("structured_decision") as step:
                step.update(draft={"answer": "Unverified claim"})
            with phase("answer_validation") as step:
                step.update(supported=False, reason="Claim absent from evidence")
            with pytest.raises(ValueError):
                with phase("tool_lookup"):
                    raise ValueError("sensitive exception content")
    assert [r["stage"] for r in context.records] == [
        "structured_decision",
        "answer_validation",
        "tool_lookup",
    ]
    assert context.records[0]["outputs"]["draft"]["answer"] == "Unverified claim"
    assert context.records[1]["outputs"]["supported"] is False
    assert context.records[2]["error"] == "ValueError"
    assert all(r["duration_ms"] >= 0 for r in context.records)
    assert "test-flow" in caplog.text
    assert "sensitive exception content" not in caplog.text


def test_monitoring_failures_do_not_break_customer_flow(monkeypatch):
    from salma import observability as obs

    def broken(*args, **kwargs):
        raise RuntimeError("monitor down")

    monkeypatch.setattr(obs, "open_remote_trace", broken)
    with obs.flow({"message_id": 8}, enabled=True) as context:
        with obs.phase("retrieval") as step:
            step["evidence_ids"] = ["doc:1"]
    retrieval = next(record for record in context.records if record["stage"] == "retrieval")
    assert retrieval["outputs"]["evidence_ids"] == ["doc:1"]


def test_trace_failure_preserves_the_original_application_exception(monkeypatch):
    from salma import observability as obs

    class Broken:
        def __enter__(self):
            raise RuntimeError("remote unavailable")

    monkeypatch.setattr(obs, "open_remote_trace", lambda *a, **k: Broken())
    with pytest.raises(LookupError):
        with obs.flow({}, enabled=True):
            with obs.phase("failing"):
                raise LookupError("original")


def test_langsmith_is_disabled_without_explicit_configuration(monkeypatch):
    from salma.observability import flow

    monkeypatch.delenv("LANGSMITH_TRACING", raising=False)
    with flow({"message_id": 9}) as context:
        assert context.remote is None


def test_system_monitoring_requires_operator_before_reading_stats(monkeypatch):
    from fastapi import HTTPException
    from salma import monitoring

    def denied(request):
        raise HTTPException(403, "Platform operator required")

    monkeypatch.setattr(monitoring, "require_operator", denied)
    with pytest.raises(HTTPException) as exc:
        monitoring.system_status(object())
    assert exc.value.status_code == 403


def test_stale_worker_heartbeat_is_unhealthy():
    from salma.monitoring import worker_health

    assert worker_health(None, now=100)["status"] == "unavailable"
    assert worker_health({"seen_at": 98, "completed_at": 98}, now=100)["status"] == "ok"
    assert worker_health({"seen_at": 1, "completed_at": 1}, now=100)["status"] == "stale"
    assert worker_health({"seen_at": 99, "completed_at": 1}, now=200)["status"] == "stale"


def test_rejected_knowledge_draft_and_explanation_survive_validation():
    from salma.observability import flow
    from salma.trust import Decision
    from salma.workspace_runtime import validate_knowledge_answer

    draft = Decision(
        intent="faq",
        reply_kind="knowledge",
        answer="Dr. Invented is available today",
        evidence_ids=["doc:test"],
    )

    def completion(messages, *, schema):
        assert "reason" in schema["required"]
        return {"content": json.dumps({"supported": False, "reason": "No availability evidence"})}

    with flow({}, enabled=False) as context:
        assert not validate_knowledge_answer(completion, draft, {"doc:test": "General doctor list"})
    record = context.records[0]
    assert record["inputs"]["draft"]["answer"] == draft.answer
    assert record["outputs"] == {"supported": False, "reason": "No availability evidence"}


def test_pipeline_stats_keep_workspaces_separate_and_include_old_backlog():
    from datetime import timedelta

    from salma.db import Base, Conversation, Customer, Event, Message, now
    from salma.monitoring import pipeline_stats
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        for business in ("own", "other"):
            db.add(Customer(key=business, business_id=business, profile={}))
            db.add(
                Conversation(
                    key=business,
                    business_id=business,
                    customer_key=business,
                    provider="simulator",
                    channel="test",
                    account_id="1",
                    inbox_id="1",
                    external_id=business,
                )
            )
            db.add(
                Message(
                    conversation_key=business,
                    business_id=business,
                    external_id="m",
                    direction="incoming",
                    text="Synthetic",
                    correlation_id=business,
                    timestamp=now() - timedelta(days=3),
                )
            )
            db.add(
                Event(
                    conversation_key=business,
                    business_id=business,
                    correlation_id=business,
                    kind="decision_validated",
                    data={
                        "duration_ms": 1200,
                        "guardrail": None,
                        "decision": {"handoff_reason": "unsupported_answer"},
                        "usage": [{"total_tokens": 70}],
                        "error": None,
                    },
                )
            )
        db.commit()
        stats = pipeline_stats(db, "own")
        assert stats["sample_size"] == 1
        assert stats["pending_messages"] == 1
        assert stats["incoming"] == {}
        assert stats["total_tokens"] == 70
        assert stats["handoff_rate"] == 1
        assert stats["handoffs"] == {"unsupported_answer": 1}
        assert stats["average_latency_ms"] == 1200
    engine.dispose()
