import json
from types import SimpleNamespace

from salma import agent, platform_api
from salma.settings import Settings


def test_setup_greetings_and_language_requests_do_not_depend_on_provider(monkeypatch):
    def unavailable(*args, **kwargs):
        raise AssertionError("Greeting must not call the external provider")

    monkeypatch.setattr(agent, "GroqCompletion", unavailable)
    for text in ("سلام عليكم", "اهلا", "مرحبا", "باللغة العربية", "hello"):
        result = platform_api.admin_proposal(
            {"name": "New business", "catalog": [], "document_ids": []},
            [SimpleNamespace(role="user", text=text)],
            [],
        )
        assert result["text"] and result["proposal"] is None
        assert "Bright Nile" not in result["text"]


def test_admin_proposal_uses_bounded_shared_client_and_cannot_apply_changes(monkeypatch):
    captured = {}

    class Completion:
        def __init__(self, _):
            pass

        def __call__(self, messages, *, tools):
            captured.update(messages=messages, tools=tools)
            return {
                "tool_calls": [
                    {
                        "function": {
                            "name": "propose_draft",
                            "arguments": json.dumps(
                                {"text": "Review this tone", "changes": {"tone": "warm"}}
                            ),
                        }
                    }
                ],
            }

    monkeypatch.setattr(agent, "GroqCompletion", Completion)
    monkeypatch.setattr(platform_api, "settings", lambda: Settings(groq_api_key="local-test"))
    config = {
        "name": "Clinic",
        "description": "Dental clinic",
        "tone": "formal",
        "catalog": [],
        "document_ids": [],
    }
    messages = [SimpleNamespace(role="user", text="a" * 12000) for _ in range(20)]
    result = platform_api.admin_proposal(config, messages, [{"text": "b" * 100000}])
    assert config["tone"] == "formal" and result["proposal"]["tone"] == "warm"
    assert len(captured["messages"]) == 7
    assert len(captured["messages"][0]["content"]) < 14000
    assert len(captured["messages"][-1]["content"]) == 2000
    assert {tool["function"]["name"] for tool in captured["tools"]} == {
        "propose_draft",
        "report_business_mismatch",
    }
    assert "cannot publish" in captured["messages"][0]["content"]


def test_different_business_cannot_inherit_existing_catalog_in_admin_proposal(monkeypatch):
    class Completion:
        def __init__(self, _):
            pass

        def __call__(self, messages, *, tools):
            return {
                "tool_calls": [
                    {
                        "function": {
                            "name": "propose_draft",
                            "arguments": json.dumps(
                                {
                                    "text": "I have prepared your furniture business.",
                                    "changes": {
                                        "name": "Comfort Furniture",
                                        "description": "Furniture sales",
                                    },
                                }
                            ),
                        }
                    }
                ]
            }

    monkeypatch.setattr(agent, "GroqCompletion", Completion)
    monkeypatch.setattr(platform_api, "settings", lambda: Settings(groq_api_key="local-test"))
    config = {
        "name": "Bright Nile",
        "catalog": [{"id": "exam", "name": "Dental exam"}],
        "document_ids": ["clinic-doc"],
    }
    result = platform_api.admin_proposal(
        config, [SimpleNamespace(role="user", text="I own Comfort Furniture")], []
    )
    assert result["proposal"] is None
    assert "Bright Nile" in result["text"] and "Comfort Furniture" in result["text"]
    assert config["name"] == "Bright Nile" and config["document_ids"] == ["clinic-doc"]


def test_admin_reports_workspace_mismatch_in_arabic_without_proposing_changes(monkeypatch):
    class Completion:
        def __init__(self, _):
            pass

        def __call__(self, messages, *, tools):
            return {
                "tool_calls": [
                    {
                        "function": {
                            "name": "report_business_mismatch",
                            "arguments": json.dumps(
                                {"business_name": "راحة لبيع كراسي الليزي بوي"}
                            ),
                        }
                    }
                ]
            }

    monkeypatch.setattr(agent, "GroqCompletion", Completion)
    monkeypatch.setattr(platform_api, "settings", lambda: Settings(groq_api_key="local-test"))
    result = platform_api.admin_proposal(
        {"name": "Bright Nile", "description": "Dental clinic", "catalog": [], "document_ids": []},
        [SimpleNamespace(role="user", text="سلام عليكم انا انس صاحب شركة راحة")],
        [],
    )
    assert result["proposal"] is None
    assert "Bright Nile" in result["text"] and "راحة" in result["text"]
    assert "مساحة" in result["text"]
    assert "أنا مساعدة Bright Nile" not in result["text"]


def test_fresh_workspace_can_propose_owners_business_name(monkeypatch):
    class Completion:
        def __init__(self, _):
            pass

        def __call__(self, messages, *, tools):
            assert [tool["function"]["name"] for tool in tools] == ["propose_draft"]
            return {
                "tool_calls": [
                    {
                        "function": {
                            "name": "propose_draft",
                            "arguments": json.dumps(
                                {
                                    "text": "Review your business details.",
                                    "changes": {
                                        "name": "Comfort Furniture",
                                        "description": "Furniture sales",
                                    },
                                }
                            ),
                        }
                    }
                ]
            }

    monkeypatch.setattr(agent, "GroqCompletion", Completion)
    monkeypatch.setattr(platform_api, "settings", lambda: Settings(groq_api_key="local-test"))
    original = {"name": "New business", "catalog": [], "document_ids": []}
    result = platform_api.admin_proposal(
        original, [SimpleNamespace(role="user", text="I own Comfort Furniture")], []
    )
    assert result["proposal"]["name"] == "Comfort Furniture"
    assert result["proposal"]["catalog"] == [] and original["name"] == "New business"
