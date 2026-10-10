from salma.agent import Agent, simulated_decision
from salma.trust import Decision


def test_human_request_overrides_model_service_reply():
    decision = simulated_decision("I want a human and consultation price", "text")
    assert decision.reply_kind == "handoff"


def test_arabic_price_and_media_are_supported():
    decision = simulated_decision("الاستشارة بكام؟", "text")
    assert decision.language == "ar" and decision.service_ids == ["consultation"]
    assert simulated_decision("", "audio").reply_kind == "media"


def test_tool_loop_is_bounded_and_decision_is_separate():
    calls = []

    def completion(messages, *, tools=None, schema=None):
        calls.append({"tools": tools is not None, "schema": schema is not None})
        if tools is not None:
            return {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": f"t{len(calls)}",
                        "type": "function",
                        "function": {
                            "name": "service_lookup",
                            "arguments": '{"service_id":"consultation"}',
                        },
                    }
                ],
            }
        return {
            "role": "assistant",
            "content": Decision(
                intent="price",
                reply_kind="service",
                service_ids=["consultation"],
                evidence_ids=["catalog:consultation"],
            ).model_dump_json(),
        }

    def tool(name, arguments):
        return {"id": "consultation", "name_en": "Consultation", "price": "500.00"}

    result = Agent(completion, tool, max_rounds=2).run("price", "text", [])
    assert len(calls) == 3
    assert calls[-1] == {"tools": False, "schema": True}
    assert result["decision"].service_ids == ["consultation"]
    assert len(result["trace"]) == 2


def test_decision_cannot_reference_evidence_not_retrieved():
    def completion(messages, *, tools=None, schema=None):
        if tools:
            return {"role": "assistant", "content": "done"}
        return {
            "role": "assistant",
            "content": Decision(
                intent="price",
                reply_kind="service",
                service_ids=["consultation"],
                evidence_ids=["catalog:consultation"],
            ).model_dump_json(),
        }

    result = Agent(completion, lambda *_: {}, max_rounds=1).run("price", "text", [])
    assert result["decision"].reply_kind == "handoff"
    assert result["decision"].handoff_reason == "unobserved_evidence"


def groq_transport(monkeypatch, responses):
    import httpx
    from salma import agent
    from salma.agent import GroqCompletion
    from salma.settings import Settings

    calls, sleeps = [], []
    original = httpx.Client
    monkeypatch.setattr(agent.ssl, "create_default_context", lambda: None)

    def handle(request):
        calls.append(request)
        return responses[min(len(calls) - 1, len(responses) - 1)]

    monkeypatch.setattr(
        agent.httpx, "Client", lambda **kwargs: original(transport=httpx.MockTransport(handle))
    )
    monkeypatch.setattr(agent.time, "sleep", sleeps.append)
    completion = GroqCompletion(Settings(groq_api_key="test-key"))
    return completion, calls, sleeps


def test_groq_rate_limit_retries_retry_after_without_exposing_reasoning(monkeypatch):
    import httpx

    completion, calls, sleeps = groq_transport(
        monkeypatch,
        [
            httpx.Response(429, headers={"Retry-After": "3"}),
            httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": "done",
                                "reasoning": "private",
                            }
                        }
                    ],
                    "usage": {"total_tokens": 10},
                },
            ),
        ],
    )
    assert completion([{"role": "user", "content": "hi"}]) == {
        "role": "assistant",
        "content": "done",
    }
    assert len(calls) == 2 and sleeps == [3]
    assert completion.usage == [{"total_tokens": 10}]


def test_groq_auth_failure_is_not_retried(monkeypatch):
    import httpx
    import pytest

    completion, calls, sleeps = groq_transport(monkeypatch, [httpx.Response(401)])
    with pytest.raises(httpx.HTTPStatusError):
        completion([])
    assert len(calls) == 1 and sleeps == []


def test_groq_overlong_retry_is_capped_and_total_wait_is_bounded(monkeypatch):
    import httpx
    import pytest

    completion, calls, sleeps = groq_transport(
        monkeypatch, [httpx.Response(429, headers={"Retry-After": "999999"})]
    )
    with pytest.raises(httpx.HTTPStatusError):
        completion([])
    assert len(calls) <= 3 and max(sleeps) <= 60 and sum(sleeps) <= 90


def test_groq_missing_retry_header_has_only_two_retries(monkeypatch):
    import httpx
    import pytest

    completion, calls, sleeps = groq_transport(monkeypatch, [httpx.Response(429)])
    with pytest.raises(httpx.HTTPStatusError):
        completion([])
    assert len(calls) == 3 and len(sleeps) == 2


def test_groq_oversleep_does_not_issue_request_outside_budget(monkeypatch):
    import httpx
    import pytest
    from salma import agent

    completion, calls, sleeps = groq_transport(
        monkeypatch, [httpx.Response(429, headers={"Retry-After": "3"})]
    )
    clock = [0]
    monkeypatch.setattr(agent.time, "monotonic", lambda: clock[0])

    def oversleep(delay):
        sleeps.append(delay)
        clock[0] = 100

    monkeypatch.setattr(agent.time, "sleep", oversleep)
    with pytest.raises(httpx.HTTPStatusError):
        completion([])
    assert len(calls) == 1


def test_initial_evidence_constrains_schema_and_is_available_without_model_search():
    seen = []

    def completion(messages, *, tools=None, schema=None):
        seen.append((messages, schema))
        if tools is not None:
            return {"role": "assistant", "content": "Ready"}
        return {
            "role": "assistant",
            "content": Decision(
                intent="hours",
                reply_kind="knowledge",
                answer="Open from 10 AM.",
                evidence_ids=["doc-1"],
            ).model_dump_json(),
        }

    result = Agent(completion, lambda *_: None, max_rounds=1).run(
        "Hours?",
        "text",
        [],
        system="Sales",
        initial_evidence=[{"id": "doc-1", "text": "Open from 10 AM.", "page": 2}],
        initial_trace=[{"tool": "knowledge_search", "status": "ok"}],
    )
    assert result["decision"].reply_kind == "knowledge"
    assert seen[-1][1]["properties"]["evidence_ids"]["items"]["enum"] == ["doc-1"]
    assert (
        "UNTRUSTED" in seen[0][0][1]["content"] and "Open from 10 AM." in seen[0][0][1]["content"]
    )
    assert "doc-1" in seen[-1][0][-1]["content"]
    assert result["trace"] == [{"tool": "knowledge_search", "status": "ok"}]


def test_no_observed_evidence_schema_forbids_fabricated_ids():
    seen = []

    def completion(messages, *, tools=None, schema=None):
        if tools is not None:
            return {"role": "assistant", "content": "Ready"}
        seen.append(schema)
        return {
            "role": "assistant",
            "content": Decision(intent="identity", reply_kind="greeting").model_dump_json(),
        }

    Agent(completion, lambda *_: None, max_rounds=1).run(
        "Are you real?", "text", [], system="Sales"
    )
    assert seen[0]["properties"]["evidence_ids"]["maxItems"] == 0


def test_qwen_request_disables_reasoning_and_limits_output_only_for_matching_model(monkeypatch):
    import json

    import httpx

    completion, calls, _ = groq_transport(
        monkeypatch,
        [
            httpx.Response(
                200, json={"choices": [{"message": {"role": "assistant", "content": "done"}}]}
            )
        ],
    )
    completion([])
    body = json.loads(calls[0].content)
    assert body["reasoning_effort"] == "none" and body["max_completion_tokens"] == 1000
    completion.config.groq_model = "other-model"
    completion([])
    other = json.loads(calls[1].content)
    assert "reasoning_effort" not in other and other["max_completion_tokens"] == 2000


def test_optional_read_stop_skips_repeated_lookup_rounds():
    calls = []

    def completion(messages, *, tools=None, schema=None):
        calls.append(schema is not None)
        if tools is not None:
            return {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "read",
                        "type": "function",
                        "function": {
                            "name": "service_lookup",
                            "arguments": '{"service_id":"exam"}',
                        },
                    }
                ],
            }
        return {
            "role": "assistant",
            "content": Decision(
                intent="price",
                reply_kind="service",
                service_ids=["exam"],
                evidence_ids=["catalog:exam"],
            ).model_dump_json(),
        }

    result = Agent(completion, lambda *_: {"id": "exam"}, stop_after_read=True).run(
        "price", "text", []
    )
    assert calls == [False, True]
    assert len(result["trace"]) == 1


def test_initial_read_trace_does_not_stop_following_mutation_chain():
    calls = []

    def completion(messages, *, tools=None, schema=None):
        calls.append(schema is not None)
        if tools is not None:
            name = "update_customer" if len(calls) == 1 else "service_lookup"
            args = '{"name":"Customer"}' if len(calls) == 1 else '{"service_id":"exam"}'
            return {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": str(len(calls)),
                        "type": "function",
                        "function": {"name": name, "arguments": args},
                    }
                ],
            }
        return {
            "role": "assistant",
            "content": Decision(
                intent="price",
                reply_kind="service",
                service_ids=["exam"],
                evidence_ids=["catalog:exam"],
            ).model_dump_json(),
        }

    def execute(name, args):
        return {"id": "exam"} if name == "service_lookup" else {"name": args["name"]}

    initial = [{"tool": "knowledge_search", "status": "ok", "result": []}]
    result = Agent(completion, execute, stop_after_read=True).run(
        "price", "text", [], initial_trace=initial
    )
    assert calls == [False, False, True]
    assert [item["tool"] for item in result["trace"]] == [
        "knowledge_search",
        "update_customer",
        "service_lookup",
    ]


def test_qwen_parallel_tool_requests_enabled_only_for_matching_model(monkeypatch):
    import json

    import httpx

    completion, calls, _ = groq_transport(
        monkeypatch,
        [
            httpx.Response(
                200, json={"choices": [{"message": {"role": "assistant", "content": "done"}}]}
            )
        ],
    )
    completion([], tools=[{"type": "function"}])
    assert json.loads(calls[0].content)["parallel_tool_calls"] is True
    completion.config.groq_model = "other-model"
    completion([], tools=[{"type": "function"}])
    assert json.loads(calls[1].content)["parallel_tool_calls"] is False
