import os
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from salma.workspace_services import decrypt_credentials, encrypt_credentials, extract_upload


def test_txt_upload_normalizes_text_and_rejects_invalid_encoding(tmp_path):
    source = tmp_path / "facts.txt"
    source.write_text("Clinic hours: 10:00–20:00. Phone 01000000000.", encoding="utf-8")
    assert extract_upload(source)[0][1].startswith("Clinic hours")
    source.write_bytes(b"\xff\xfe\x00")
    with pytest.raises(ValueError, match="UTF-8"):
        extract_upload(source)


def test_upload_extensions_are_not_interpreted_as_paths(tmp_path):
    with pytest.raises(ValueError, match="PDF or UTF-8 TXT"):
        extract_upload(Path(tmp_path / "script.exe"))


def test_credentials_encrypt_and_wrong_key_cannot_decrypt(monkeypatch):
    from cryptography.fernet import Fernet
    from salma.settings import settings

    monkeypatch.setenv("CREDENTIAL_KEY", Fernet.generate_key().decode())
    settings.cache_clear()
    encrypted = encrypt_credentials({"api_token": "private", "webhook_secret": "secret"})
    assert "private" not in encrypted
    assert decrypt_credentials(encrypted)["api_token"] == "private"
    monkeypatch.setenv("CREDENTIAL_KEY", Fernet.generate_key().decode())
    settings.cache_clear()
    with pytest.raises(HTTPException) as failure:
        decrypt_credentials(encrypted)
    assert failure.value.status_code == 503
    settings.cache_clear()


def test_channel_response_never_exposes_credentials():
    from salma.workspace_services import channel_view

    channel = SimpleNamespace(
        id="one",
        kind="whatsapp",
        name="Sales",
        account_id="1",
        inbox_id="2",
        evolution_instance="salma-one",
        live_enabled=False,
        status="configured",
        details={},
        credentials="encrypted",
        webhook_secret_hash="private",
    )
    result = channel_view(channel)
    assert "credentials" not in result
    assert "webhook_secret_hash" not in result


def test_channel_access_checks_real_mapping_without_exposing_secrets(monkeypatch):
    import salma.workspace_services as services

    channel = SimpleNamespace(
        id="one",
        kind="whatsapp",
        account_id="1",
        inbox_id="2",
        evolution_instance="salma-one",
        live_enabled=False,
    )
    calls = []

    def provider(provider, method, path, **kwargs):
        calls.append((provider, method, path))
        if path.endswith("/inboxes"):
            return {"payload": [{"id": 2}]}
        if path.endswith("/webhooks"):
            return {
                "payload": [
                    {
                        "url": services.settings().public_api_url.rstrip("/")
                        + "/webhooks/channels/one/secret",
                        "subscriptions": ["message_created", "message_updated"],
                    }
                ]
            }
        if path.startswith("/chatwoot/find/"):
            return {
                "enabled": True,
                "accountId": "1",
                "token": "private",
                "url": services.settings().chatwoot_url,
                "importMessages": False,
            }
        return {"instance": {"state": "close"}}

    monkeypatch.setattr(services, "provider_request", provider)
    monkeypatch.setattr(
        services,
        "get_channel_credentials",
        lambda _: {"api_token": "private", "webhook_secret": "secret"},
    )
    result = services.inspect_channel_access(channel, operator=False)
    assert result["chatwoot"]["status"] == "connected"
    assert result["chatwoot"]["webhook_verified"] is True
    assert result["evolution"]["status"] == "awaiting_pairing"
    assert result["evolution"]["bridge_verified"] is True
    assert result["chatwoot"]["url"].endswith("/app/accounts/1/dashboard")
    assert result["evolution"]["url"] is None
    assert result["live_enabled"] is False
    assert "private" not in str(result) and "secret" not in str(result)
    assert all(method == "GET" for _, method, _ in calls)
    assert services.inspect_channel_access(channel, operator=True)["evolution"]["url"]


def test_channel_access_reports_provider_failure_and_no_false_live_state(monkeypatch):
    import salma.workspace_services as services

    monkeypatch.setattr(services, "get_channel_credentials", lambda _: {"api_token": "private"})

    def unavailable(*args, **kwargs):
        raise HTTPException(502, "Untrusted provider body with private token")

    monkeypatch.setattr(services, "provider_request", unavailable)
    channel = SimpleNamespace(
        id="one",
        kind="whatsapp",
        account_id="1",
        inbox_id="2",
        evolution_instance="salma-one",
        live_enabled=False,
    )
    result = services.inspect_channel_access(channel, operator=False)
    assert result["chatwoot"]["status"] == "unavailable"
    assert result["evolution"]["status"] == "unavailable"
    assert "private" not in str(result)


@pytest.mark.parametrize(
    "payload",
    [
        [{"id": 1}],
        {"payload": [{"id": 1}]},
        {"payload": {"webhooks": [{"id": 1}]}},
        {"webhooks": [{"id": 1}]},
    ],
)
def test_installed_chatwoot_collection_response_shapes(payload):
    from salma.workspace_services import unwrap_list

    assert unwrap_list(payload) == [{"id": 1}]


def test_nested_webhook_collection_does_not_duplicate_registered_hook(monkeypatch):
    import salma.workspace_services as services

    calls = []
    channel = SimpleNamespace(id="channel-one", account_id="1")
    credentials = {"api_token": "private", "webhook_secret": "hook-secret"}
    url = (
        services.settings().public_api_url.rstrip("/")
        + "/webhooks/channels/channel-one/hook-secret"
    )

    def provider(provider, method, path, **kwargs):
        calls.append(method)
        return {"payload": {"webhooks": [{"id": 1, "url": url}]}}

    monkeypatch.setattr(services, "provider_request", provider)
    services.register_webhook(channel, credentials)
    assert calls == ["GET"]


def test_inbox_verification_matches_only_the_expected_provider(monkeypatch):
    import salma.workspace_services as services

    calls = []

    def provider(provider, method, path, **kwargs):
        calls.append((provider, method, path, kwargs))
        return {"payload": [{"id": 42, "name": "salma-one", "api_token": "private"}]}

    monkeypatch.setattr(services, "provider_request", provider)
    channel = SimpleNamespace(
        account_id="1", inbox_id=None, evolution_instance="salma-one", kind="whatsapp"
    )
    assert services.verify_inbox(channel, {"api_token": "secret"}) == "42"
    channel.kind = "instagram"
    with pytest.raises(HTTPException) as failure:
        services.verify_inbox(channel, {"api_token": "secret"})
    assert failure.value.status_code == 409


def test_handoff_sends_private_note_and_only_configured_assignment(monkeypatch):
    import salma.workspace_services as services

    calls = []

    def provider(provider, method, path, **kwargs):
        calls.append((provider, method, path, kwargs["body"]))
        return {}

    monkeypatch.setattr(services, "provider_request", provider)
    monkeypatch.setattr(
        services, "get_channel_credentials", lambda channel: {"api_token": "secret"}
    )
    channel = SimpleNamespace(account_id="1", details={"destinations": {"sales": {"team_id": 4}}})
    conversation = SimpleNamespace(external_id="42")
    result = services.notify_handoff(
        channel, conversation, "Customer requests a custom quote", "sales"
    )
    assert result == {"note": "accepted", "assignment": "accepted"}
    assert calls[0][3]["private"] is True
    assert calls[1][3] == {"team_id": 4}
    calls.clear()
    result = services.notify_handoff(channel, conversation, "Need staff", "unconfigured-team")
    assert result["assignment"] == "unconfigured"
    assert len(calls) == 1


def test_handoff_failure_is_reported_without_public_send_or_retry(monkeypatch):
    import salma.workspace_services as services

    calls = []

    def provider(*args, **kwargs):
        calls.append(args)
        raise HTTPException(502, "Provider unavailable")

    monkeypatch.setattr(services, "provider_request", provider)
    monkeypatch.setattr(
        services, "get_channel_credentials", lambda channel: {"api_token": "secret"}
    )
    channel = SimpleNamespace(account_id="1", details={})
    result = services.notify_handoff(
        channel, SimpleNamespace(external_id="42"), "Human needed", "staff"
    )
    assert result == {"note": "failed", "assignment": "unconfigured"}
    assert len(calls) == 1


@pytest.fixture
def workspace_client(monkeypatch, tmp_path):
    if not os.environ.get("TEST_DATABASE_URL"):
        pytest.skip("PostgreSQL integration")
    import salma.workspace_services as services
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from salma.db import session
    from salma.platform_models import Business
    from salma.settings import settings

    first, second = str(uuid4()), str(uuid4())
    with session() as db, db.begin():
        db.add_all(
            [
                Business(id=first, name="Clinic", draft={}),
                Business(id=second, name="Cafe", draft={}),
            ]
        )
    monkeypatch.setenv("UPLOADS_DIR", str(tmp_path))
    settings.cache_clear()
    monkeypatch.setattr(services, "require_business", lambda request, business_id: None)
    monkeypatch.setattr(services, "require_operator", lambda request: None)
    app = FastAPI()
    app.include_router(services.router)
    with TestClient(app) as client:
        yield client, first, second
    settings.cache_clear()


def test_upload_download_and_approval_are_scoped(workspace_client, monkeypatch):
    client, first, second = workspace_client
    first_url, second_url = f"/api/businesses/{first}", f"/api/businesses/{second}"
    response = client.post(first_url + "/documents", files={"file": ("facts.txt", b"Hours: 10-20")})
    assert response.status_code == 202
    identifier = response.json()["id"]
    assert response.json()["status"] == "pending"
    assert client.get(second_url + f"/documents/{identifier}/download").status_code == 404
    assert (
        client.patch(second_url + f"/documents/{identifier}", json={"approved": True}).status_code
        == 404
    )
    assert client.get(first_url + f"/documents/{identifier}/download").content == b"Hours: 10-20"
    assert (
        client.patch(first_url + f"/documents/{identifier}", json={"approved": True}).status_code
        == 409
    )
    monkeypatch.setattr("salma.workspace_services.MAX_UPLOAD_BYTES", 4)
    assert (
        client.post(first_url + "/documents", files={"file": ("big.txt", b"12345")}).status_code
        == 413
    )
    assert len(client.get(first_url + "/documents").json()["documents"]) == 1
    assert client.get(second_url + "/documents").json()["documents"] == []


def test_channel_account_cannot_bind_to_two_businesses(workspace_client, monkeypatch):
    from cryptography.fernet import Fernet
    from salma.settings import settings

    monkeypatch.setenv("CREDENTIAL_KEY", Fernet.generate_key().decode())
    settings.cache_clear()
    client, first, second = workspace_client
    account_id = str(int(uuid4().hex[:8], 16))
    payload = {"kind": "whatsapp", "name": "Sales", "account_id": account_id, "api_token": "secret"}
    response = client.post(f"/api/businesses/{first}/channels", json=payload)
    assert response.status_code == 201
    assert "secret" not in response.text
    assert response.json()["live_enabled"] is False
    assert client.post(f"/api/businesses/{second}/channels", json=payload).status_code == 409
    channel_id = response.json()["id"]
    assert (
        client.post(
            f"/api/businesses/{first}/channels/{channel_id}/activate", json={"enabled": True}
        ).status_code
        == 409
    )
    assert client.get(f"/api/businesses/{second}/channels/{channel_id}/status").status_code == 404


def test_handoff_destinations_are_validated_in_the_bound_chatwoot_account(
    workspace_client, monkeypatch
):
    import salma.workspace_services as services
    from cryptography.fernet import Fernet
    from salma.settings import settings

    monkeypatch.setenv("CREDENTIAL_KEY", Fernet.generate_key().decode())
    settings.cache_clear()
    client, first, second = workspace_client
    base = f"/api/businesses/{first}/channels"
    payload = {
        "kind": "whatsapp",
        "name": "Sales",
        "account_id": str(int(uuid4().hex[:8], 16)),
        "api_token": "private",
    }
    channel_id = client.post(base, json=payload).json()["id"]
    monkeypatch.setattr(
        services,
        "provider_request",
        lambda *args, **kwargs: {
            "payload": [{"id": 4, "name": "Sales", "access_token": "must-not-return"}]
        },
    )
    response = client.get(base + f"/{channel_id}/destinations")
    assert response.status_code == 200
    assert "must-not-return" not in response.text
    response = client.patch(
        base + f"/{channel_id}/destinations", json={"destinations": {"sales": {"team_id": 4}}}
    )
    assert response.status_code == 200
    response = client.patch(
        base + f"/{channel_id}/destinations", json={"destinations": {"sales": {"team_id": 999}}}
    )
    assert response.status_code == 422
    assert (
        client.get(f"/api/businesses/{second}/channels/{channel_id}/destinations").status_code
        == 404
    )


def test_document_job_recovers_expired_lease_and_keeps_evidence_scoped(
    workspace_client, monkeypatch
):
    import salma.workspace_services as services
    from salma.db import Knowledge, session
    from salma.platform_models import Document
    from sqlalchemy import select

    class Tokenizer:
        model_max_length = 128

        def encode(self, content, **kwargs):
            return list(range(len(content.split())))

    class Vector(list):
        def tolist(self):
            return list(self)

    class Model:
        tokenizer = Tokenizer()
        max_seq_length = 128

        def encode(self, text, **kwargs):
            return Vector([0.1] * 384)

    monkeypatch.setattr(services, "embedding_model", lambda language: Model())
    client, first, second = workspace_client
    base = f"/api/businesses/{first}/documents"
    response = client.post(base, files={"file": ("facts.txt", b"Hours: 10-20. Phone 01000000000.")})
    identifier = response.json()["id"]
    with session() as db, db.begin():
        document = db.get(Document, identifier)
        document.status = "indexing"
        document.details = {"claim": "old-worker", "lease_until": "2000-01-01T00:00:00+00:00"}
    assert services.process_document_job(identifier) is True
    assert services.process_document_job(identifier) is False
    with session() as db:
        document = db.get(Document, identifier)
        assert document.status == "ready"
        evidence = db.scalars(select(Knowledge).where(Knowledge.document_id == identifier)).all()
        assert len(evidence) == 1
        assert evidence[0].business_id == first
        assert evidence[0].page == 1
        assert "01000000000" in evidence[0].text
    assert client.patch(base + "/" + identifier, json={"approved": True}).status_code == 200
    assert client.delete(base + "/" + identifier).status_code == 200
    with session() as db:
        assert db.scalar(select(Knowledge).where(Knowledge.document_id == identifier))


def test_whatsapp_connection_disables_history_and_activates_only_after_pairing(
    workspace_client, monkeypatch
):
    import salma.workspace_services as services
    from cryptography.fernet import Fernet
    from salma.db import session
    from salma.platform_models import Business, ConfigVersion
    from salma.settings import settings

    monkeypatch.setenv("CREDENTIAL_KEY", Fernet.generate_key().decode())
    settings.cache_clear()
    client, first, second = workspace_client
    base = f"/api/businesses/{first}/channels"
    created = client.post(
        base,
        json={
            "kind": "whatsapp",
            "name": "Sales",
            "account_id": str(int(uuid4().hex[:8], 16)),
            "api_token": "private",
        },
    )
    identifier = created.json()["id"]
    instance = "salma-" + identifier.replace("-", "")
    calls = []
    state = {"connected": False}

    def provider(provider, method, path, **kwargs):
        calls.append((provider, method, path, kwargs))
        if path.endswith("fetchInstances"):
            return []
        if path.endswith("/inboxes"):
            return {"payload": [{"id": 77, "name": instance}]}
        if "/connectionState/" in path:
            return {"instance": {"state": "open" if state["connected"] else "close"}}
        if "/instance/connect/" in path:
            return {"base64": "data:image/png;base64,YQ==", "apikey": "private"}
        if "/webhook/find/" in path:
            return next(call[3]["body"]["webhook"] for call in calls if "/webhook/set/" in call[2])
        if "/chatwoot/find/" in path:
            return {"enabled": True, "accountId": 1, "token": "private", "url": "http://chatwoot"}
        if "/chatwoot/set/" in path:
            body = kwargs["body"]
            assert {
                "enabled",
                "accountId",
                "token",
                "url",
                "signMsg",
                "reopenConversation",
                "conversationPending",
            } <= body.keys()
            assert body["enabled"] is False
            assert body["importMessages"] is False
        return {}

    monkeypatch.setattr(services, "provider_request", provider)
    response = client.post(base + f"/{identifier}/connect")
    assert response.status_code == 200
    assert "private" not in response.text
    assert response.json()["live_enabled"] is False
    assert all(call[0] == "evolution" for call in calls)
    hook = next(call for call in calls if "/webhook/set/" in call[2])[3]["body"]["webhook"]
    assert "MESSAGES_SET" not in hook["events"]
    assert response.json()["details"]["transport"] == "evolution"
    qr = client.get(base + f"/{identifier}/qr")
    assert qr.json()["qr_code"] == "data:image/png;base64,YQ=="
    assert "private" not in qr.text
    with session() as db, db.begin():
        db.get(Business, first).published_version = 1
        db.add(ConfigVersion(business_id=first, version=1, config={}, catalog=[], document_ids=[]))
    assert client.post(base + f"/{identifier}/activate", json={"enabled": True}).status_code == 409
    state["connected"] = True
    response = client.post(base + f"/{identifier}/activate", json={"enabled": True})
    assert response.status_code == 200
    assert response.json()["live_enabled"] is True
