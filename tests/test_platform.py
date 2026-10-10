# These tests use real PostgreSQL and an isolated in-memory Redis transport.
import os
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from salma import auth
from salma.auth import hash_password, verify_password
from salma.db import session
from salma.platform_api import effective_prompt, router, validate_config
from salma.platform_models import Business, ConfigVersion, Membership, User
from sqlalchemy import select


def test_password_hash_is_salted_and_verified():
    first = hash_password("correct horse battery staple")
    assert first != hash_password("correct horse battery staple")
    assert verify_password("correct horse battery staple", first)
    assert not verify_password("wrong", first)


def test_config_validates_catalog_and_preserves_business_fields():
    value = validate_config(
        {
            "name": "Clinic",
            "catalog": [
                {
                    "id": "exam",
                    "name": "Exam",
                    "currency": "EGP",
                    "price_type": "fixed",
                    "amount": 500,
                }
            ],
        }
    )
    assert value["catalog"][0]["amount"] == 500
    with pytest.raises(ValueError):
        validate_config(
            {"catalog": [{"id": "bad", "name": "Bad", "price_type": "fixed", "amount": -1}]}
        )
    with pytest.raises(ValueError):
        validate_config({"handoff_rules": [{"name": "Missing conditions"}]})


def test_effective_prompt_keeps_platform_constraints_before_owner_instructions():
    result = effective_prompt({"name": "Clinic", "owner_instructions": "Be friendly"})
    assert "Never" in result and "Be friendly" in result and "Clinic" in result


class MemoryRedis:
    def __init__(self):
        self.values = {}

    def setex(self, key, ttl, value):
        self.values[key] = value

    def get(self, key):
        return self.values.get(key)

    def incr(self, key):
        self.values[key] = int(self.values.get(key, 0)) + 1
        return self.values[key]

    def expire(self, key, ttl):
        return True

    def delete(self, key):
        self.values.pop(key, None)


@pytest.fixture
def platform_client(monkeypatch):
    if not os.environ.get("TEST_DATABASE_URL"):
        pytest.skip("PostgreSQL integration")
    memory = MemoryRedis()
    monkeypatch.setattr(auth, "redis_client", lambda: memory)
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        yield client


def create_identity(role="operator"):
    with session() as db:
        user = User(
            email=f"{uuid4()}@example.test",
            password_hash=hash_password("test password 123"),
            role=role,
        )
        db.add(user)
        db.commit()
        return user


def sign_in(client, user):
    response = client.post(
        "/api/auth/login", json={"email": user.email, "password": "test password 123"}
    )
    assert response.status_code == 200, response.text
    return {"X-CSRF-Token": response.json()["csrf_token"]}


def test_provider_shortcuts_and_diagnostics_enforce_workspace_access(platform_client, monkeypatch):
    from salma import workspace_services as services
    from salma.platform_models import Channel

    client = platform_client
    client.app.include_router(services.router)
    owner = create_identity("owner")
    with session() as db, db.begin():
        own = Business(name="Own tools", draft={})
        other = Business(name="Other tools", draft={})
        db.add_all([own, other])
        db.flush()
        db.add(Membership(user_id=owner.id, business_id=own.id))
        account = str(int(uuid4().hex[:8], 16))
        channels = [
            Channel(
                business_id=business.id,
                kind="whatsapp",
                name="Tools",
                account_id=account + str(index),
                inbox_id="1",
                credentials="encrypted",
                webhook_secret_hash="hash",
                live_enabled=False,
                status="configured",
                details={},
            )
            for index, business in enumerate([own, other])
        ]
        db.add_all(channels)
        db.flush()
        own_id, other_id = own.id, other.id
        first, second = channels[0].id, channels[1].id
    base = f"/api/businesses/{own_id}"
    assert client.get(base + "/provider-tools").status_code == 401
    sign_in(client, owner)
    tools = client.get(base + "/provider-tools").json()
    assert tools["evolution_url"] is None
    assert len(tools["chatwoot"]) == 1
    assert tools["chatwoot"][0]["account_id"] == account + "0"
    assert client.get(f"/api/businesses/{other_id}/provider-tools").status_code == 404
    called = []
    monkeypatch.setattr(
        services,
        "inspect_channel_access",
        lambda row, **kwargs: called.append(row.id) or {"verified": True},
    )
    assert client.get(base + f"/channels/{second}/access").status_code == 404
    assert not called
    assert client.get(base + f"/channels/{first}/access").json() == {"verified": True}
    assert called == [first]
    operator = create_identity()
    sign_in(client, operator)
    assert client.get(base + "/provider-tools").json()["evolution_url"].endswith("/manager")


def test_owner_links_only_social_inboxes_from_bound_account(platform_client, monkeypatch):
    from salma import workspace_services as services
    from salma.platform_models import Channel

    client = platform_client
    client.app.include_router(services.router)
    owner = create_identity("owner")
    with session() as db, db.begin():
        business = Business(name="Social owner", draft={})
        db.add(business)
        db.flush()
        bid = business.id
        account = str(int(uuid4().hex[:8], 16))
        db.add(Membership(user_id=owner.id, business_id=bid))
        db.add(
            Channel(
                business_id=bid,
                kind="whatsapp",
                name="Existing WhatsApp",
                account_id=account,
                inbox_id="1",
                credentials="encrypted",
                webhook_secret_hash="hash",
                live_enabled=True,
                status="ready",
                details={},
            )
        )
    headers = sign_in(client, owner)
    base = f"/api/businesses/{bid}"
    calls = []
    monkeypatch.setattr(services, "get_channel_credentials", lambda row: {"api_token": "private"})
    monkeypatch.setattr(services, "encrypt_credentials", lambda value: "encrypted-new")

    def provider(provider, method, path, **kwargs):
        calls.append(path)
        return {
            "payload": [
                {"id": 1, "name": "WhatsApp", "channel_type": "Channel::Api"},
                {"id": 2, "name": "Facebook page", "channel_type": "Channel::FacebookPage"},
                {"id": 3, "name": "Instagram shop", "channel_type": "Channel::Instagram"},
            ]
        }

    monkeypatch.setattr(services, "provider_request", provider)
    result = client.get(base + "/social-inboxes")
    assert result.status_code == 200
    assert [i["kind"] for i in result.json()["inboxes"]] == ["messenger", "instagram"]
    assert "private" not in result.text
    assert all(f"/accounts/{account}/" in path for path in calls)
    calls.clear()
    body = {"account_id": account, "inbox_id": "2"}
    assert client.post(base + "/social-channels", json=body).status_code == 403
    assert (
        client.post(
            base + "/social-channels", headers=headers, json={**body, "account_id": account + "9"}
        ).status_code
        == 404
    )
    assert not calls
    assert (
        client.post(
            base + "/social-channels", headers=headers, json={**body, "inbox_id": "1"}
        ).status_code
        == 422
    )
    monkeypatch.setattr(services, "register_webhook", lambda *args: None)
    response = client.post(base + "/social-channels", headers=headers, json=body)
    assert response.status_code == 201, response.text
    assert response.json()["kind"] == "messenger"
    assert response.json()["live_enabled"] is False
    assert response.json()["status"] == "ready"
    assert client.post(base + "/social-channels", headers=headers, json=body).status_code == 409
    with session() as db:
        whatsapp = db.scalar(
            select(Channel).where(Channel.business_id == bid, Channel.kind == "whatsapp")
        )
        assert whatsapp.live_enabled is True


def test_owner_isolation_csrf_and_operator_permissions(platform_client):
    client = platform_client
    user = create_identity("owner")
    with session() as db:
        own = Business(name="Own", draft={"name": "Own"})
        other = Business(name="Other", draft={"name": "Other"})
        db.add_all([own, other])
        db.flush()
        db.add(Membership(user_id=user.id, business_id=own.id))
        db.commit()
    headers = sign_in(client, user)
    assert client.get(f"/api/businesses/{own.id}/draft").status_code == 200
    assert client.get(f"/api/businesses/{other.id}/draft").status_code == 404
    assert client.get(f"/api/businesses/{other.id}/admin/messages").status_code == 404
    assert client.put(f"/api/businesses/{own.id}/draft", json={"name": "New"}).status_code == 403
    assert (
        client.post(
            f"/api/businesses/{own.id}/invites", headers=headers, json={"email": "new@example.test"}
        ).status_code
        == 403
    )
    assert (
        client.put(
            f"/api/businesses/{other.id}/draft", headers=headers, json={"name": "No"}
        ).status_code
        == 404
    )
    assert (
        client.put(
            f"/api/businesses/{own.id}/draft", headers=headers, json={"name": "New"}
        ).status_code
        == 200
    )
    assert [row["id"] for row in client.get("/api/businesses").json()["businesses"]] == [own.id]


def test_owner_creates_empty_workspace_and_gets_only_its_membership(platform_client):
    client = platform_client
    owner = create_identity("owner")
    other = create_identity("owner")
    headers = sign_in(client, owner)
    assert client.post("/api/businesses", json={"name": "New company"}).status_code == 403
    assert client.post("/api/businesses", headers=headers, json={"name": "   "}).status_code == 422
    created = client.post("/api/businesses", headers=headers, json={"name": "Comfort Furniture"})
    assert created.status_code == 200
    bid = created.json()["id"]
    draft = client.get(f"/api/businesses/{bid}/draft").json()
    assert draft["published_version"] is None
    assert draft["config"]["name"] == "Comfort Furniture"
    for field in ("catalog", "document_ids", "handoff_rules"):
        assert draft["config"][field] == []
    assert client.get(f"/api/businesses/{bid}/admin/messages").json()["messages"] == []
    assert bid in {b["id"] for b in client.get("/api/auth/me").json()["businesses"]}
    with session() as db:
        members = db.scalars(select(Membership).where(Membership.business_id == bid)).all()
        assert [m.user_id for m in members] == [owner.id]
    sign_in(client, other)
    assert client.get(f"/api/businesses/{bid}/draft").status_code == 404
    assert bid not in {b["id"] for b in client.get("/api/auth/me").json()["businesses"]}


def test_setup_greeting_is_persisted_without_model_and_quota_failure_is_clear(
    platform_client, monkeypatch
):
    from salma import agent, platform_api

    client = platform_client
    owner = create_identity("owner")
    headers = sign_in(client, owner)
    bid = client.post("/api/businesses", headers=headers, json={"name": "New company"}).json()["id"]
    path = f"/api/businesses/{bid}/admin/messages"

    def unavailable(*args, **kwargs):
        raise AssertionError("Greeting must work even when the provider is unavailable")

    monkeypatch.setattr(agent, "GroqCompletion", unavailable)
    result = client.post(path, headers=headers, json={"text": "اهلا"})
    assert result.status_code == 200
    assert result.json()["proposal"] is None
    assert [m["role"] for m in client.get(path).json()["messages"]] == ["user", "assistant"]

    def quota_failure(*args, **kwargs):
        request = httpx.Request("POST", "https://example.test/provider")
        raise httpx.HTTPStatusError(
            "private provider details",
            request=request,
            response=httpx.Response(429, request=request),
        )

    monkeypatch.setattr(platform_api, "admin_proposal", quota_failure)
    failed = client.post(path, headers=headers, json={"text": "Review our sales objectives"})
    assert failed.status_code == 503
    assert "rate-limited" in failed.json()["detail"]
    assert "private provider details" not in failed.text
    assert client.get(path).json()["messages"][-1]["text"] == "Review our sales objectives"


def test_invitation_single_use_and_publish_rollback_immutable(platform_client):
    client = platform_client
    operator = create_identity()
    headers = sign_in(client, operator)
    business = client.post("/api/businesses", headers=headers, json={"name": "Alpha"}).json()
    bid = business["id"]
    invite = client.post(
        f"/api/businesses/{bid}/invites", headers=headers, json={"email": f"{uuid4()}@example.test"}
    ).json()
    client.cookies.clear()
    body = {"token": invite["token"], "password": "owner password 123"}
    response = client.post("/api/auth/accept-invite", json=body)
    assert response.status_code == 200, response.text
    headers = {"X-CSRF-Token": response.json()["csrf_token"]}
    assert response.json()["businesses"][0]["id"] == bid
    assert client.post("/api/auth/accept-invite", json=body).status_code == 400
    assert client.post(f"/api/businesses/{bid}/publish", headers=headers).json()["version"] == 1
    client.put(f"/api/businesses/{bid}/draft", headers=headers, json={"name": "Beta"})
    with session() as db:
        snapshot = db.scalar(select(ConfigVersion).where(ConfigVersion.business_id == bid))
        assert snapshot.config["name"] == "Alpha"
    assert client.post(f"/api/businesses/{bid}/publish", headers=headers).json()["version"] == 2
    assert (
        client.post(f"/api/businesses/{bid}/rollback", headers=headers, json={"version": 1}).json()[
            "version"
        ]
        == 3
    )
    versions = client.get(f"/api/businesses/{bid}/versions").json()["versions"]
    assert versions[0]["config"]["name"] == "Alpha"
    assert client.get(f"/api/businesses/{bid}/draft").json()["config"]["name"] == "Beta"


def test_redis_session_loss_requires_login_but_preserves_business(platform_client, monkeypatch):
    client = platform_client
    user = create_identity()
    headers = sign_in(client, user)
    business = client.post("/api/businesses", headers=headers, json={"name": "Survives"}).json()
    memory = MemoryRedis()
    monkeypatch.setattr(auth, "redis_client", lambda: memory)
    assert client.get("/api/auth/me").status_code == 401
    headers = sign_in(client, user)
    assert client.get(f"/api/businesses/{business['id']}/draft").status_code == 200


def test_publish_rejects_another_business_document(platform_client):
    from salma.platform_models import Document

    client = platform_client
    user = create_identity("owner")
    with session() as db:
        own = Business(name="Own", draft={"name": "Own"})
        other = Business(name="Other", draft={"name": "Other"})
        db.add_all([own, other])
        db.flush()
        db.add(Membership(user_id=user.id, business_id=own.id))
        document = Document(
            business_id=other.id,
            filename="private.txt",
            storage_path="/private",
            status="ready",
            approved=True,
        )
        db.add(document)
        db.commit()
    headers = sign_in(client, user)
    client.put(
        f"/api/businesses/{own.id}/draft",
        headers=headers,
        json={"name": "Own", "document_ids": [document.id]},
    )
    response = client.post(f"/api/businesses/{own.id}/publish", headers=headers)
    assert response.status_code == 422
    with session() as db:
        assert db.get(Business, own.id).published_version is None


def test_admin_proposal_requires_owner_review_and_never_publishes(platform_client, monkeypatch):
    from salma import platform_api

    client = platform_client
    user = create_identity()
    headers = sign_in(client, user)
    bid = client.post("/api/businesses", headers=headers, json={"name": "Before"}).json()["id"]
    monkeypatch.setattr(
        platform_api,
        "admin_proposal",
        lambda *args, **kwargs: {
            "text": "Please review this change",
            "proposal": {"name": "After", "catalog": [], "document_ids": [], "handoff_rules": []},
        },
    )
    response = client.post(
        f"/api/businesses/{bid}/admin/messages",
        headers=headers,
        json={"text": "Our business name is After"},
    )
    assert response.status_code == 200
    assert response.json()["proposal"]["name"] == "After"
    draft = client.get(f"/api/businesses/{bid}/draft").json()
    assert draft["config"]["name"] == "Before"
    assert draft["published_version"] is None
    history = client.get(f"/api/businesses/{bid}/admin/messages").json()["messages"]
    assert [row["role"] for row in history] == ["user", "assistant"]


def test_handoff_rule_has_stable_identifier():
    config = {
        "handoff_rules": [{"name": "Custom quote", "conditions": "Needs personalized pricing"}]
    }
    first = validate_config(config)["handoff_rules"][0]
    second = validate_config(config)["handoff_rules"][0]
    assert first["id"] == second["id"]
    with pytest.raises(ValueError):
        validate_config(
            {
                "handoff_rules": [
                    {"name": "Dup", "conditions": "one"},
                    {"name": "Dup", "conditions": "two"},
                ]
            }
        )


def test_rollback_restores_immutable_retired_document_snapshot(platform_client):
    from salma.platform_models import Document

    client = platform_client
    headers = sign_in(client, create_identity())
    bid = client.post("/api/businesses", headers=headers, json={"name": "History"}).json()["id"]
    with session() as db:
        document = Document(
            business_id=bid,
            filename="old.txt",
            storage_path="/private",
            status="ready",
            approved=True,
        )
        db.add(document)
        db.commit()
    client.put(
        f"/api/businesses/{bid}/draft",
        headers=headers,
        json={"name": "History", "document_ids": [document.id]},
    )
    assert client.post(f"/api/businesses/{bid}/publish", headers=headers).status_code == 200
    with session() as db:
        old = db.get(Document, document.id)
        old.status = "retired"
        old.approved = False
        db.commit()
    client.put(f"/api/businesses/{bid}/draft", headers=headers, json={"name": "History"})
    client.post(f"/api/businesses/{bid}/publish", headers=headers)
    response = client.post(f"/api/businesses/{bid}/rollback", headers=headers, json={"version": 1})
    assert response.status_code == 200, response.text
    assert response.json()["version"] == 3
    assert client.get(f"/api/businesses/{bid}/versions").json()["versions"][0]["document_ids"] == [
        document.id
    ]
    # A new publish cannot include retired or unapproved content.
    client.put(
        f"/api/businesses/{bid}/draft",
        headers=headers,
        json={"name": "History", "document_ids": [document.id]},
    )
    assert client.post(f"/api/businesses/{bid}/publish", headers=headers).status_code == 422


@pytest.mark.parametrize(
    "field,value",
    [
        ("timezone", "Mars/Olympus"),
        ("catalog", [{"id": "x::offer::0", "name": "X", "price_type": "quote_only"}]),
        ("catalog", [{"id": "x", "name": ["wrong"], "price_type": "quote_only"}]),
        (
            "catalog",
            [{"id": "x", "name": "X", "currency": "BAD MONEY", "price_type": "quote_only"}],
        ),
        ("catalog", [{"id": "x", "name": "X", "price_type": "fixed", "amount": float("inf")}]),
        ("catalog", [{"id": "x", "name": "X", "offers": [{"name": "Sale", "amount": -1}]}]),
        (
            "catalog",
            [
                {
                    "id": "x",
                    "name": "X",
                    "offers": [{"name": "Sale", "amount": 1, "valid_until": "tomorrow"}],
                }
            ],
        ),
        (
            "catalog",
            [{"id": "x", "name": "X", "offers": [{"name": "Sale", "amount": 1, "conditions": []}]}],
        ),
        ("handoff_rules", [{"name": "X", "conditions": "Y", "examples": "wrong"}]),
    ],
)
def test_config_rejects_invalid_runtime_inputs(field, value):
    with pytest.raises(ValueError):
        validate_config({field: value})


def test_catalog_money_is_normalized_to_two_decimal_places():
    result = validate_config(
        {
            "timezone": "Africa/Cairo",
            "catalog": [
                {
                    "id": "x",
                    "name": "X",
                    "price_type": "fixed",
                    "amount": 1.005,
                    "currency": "egp",
                    "offers": [{"name": "Sale", "amount": 0.555, "valid_until": "2027-02-28"}],
                }
            ],
        }
    )
    assert result["catalog"][0]["amount"] == 1.01
    assert result["catalog"][0]["currency"] == "EGP"
    assert result["catalog"][0]["offers"][0]["amount"] == 0.56


@pytest.fixture
def full_platform_client(monkeypatch):
    if not os.environ.get("TEST_DATABASE_URL"):
        pytest.skip("PostgreSQL integration")
    from salma.api import app

    memory = MemoryRedis()
    monkeypatch.setattr(auth, "redis_client", lambda: memory)
    with TestClient(app) as client:
        yield client, memory


def make_conversation_workspace(user):
    from salma.adapters import Inbound
    from salma.db import Outbox
    from salma.store import accept

    with session() as db:
        own = Business(name="Own conversation", draft={"name": "Own"}, published_version=1)
        other = Business(name="Other conversation", draft={"name": "Other"}, published_version=1)
        db.add_all([own, other])
        db.flush()
        db.add(Membership(user_id=user.id, business_id=own.id))
        db.commit()
        incoming = Inbound(
            business_id=other.id,
            config_version=1,
            provider="simulator",
            channel="simulator",
            account_id="local",
            inbox_id="sim",
            external_message_id=str(uuid4()),
            external_conversation_id=str(uuid4()),
            external_customer_id=str(uuid4()),
            text="hello",
        )
        mid, _ = accept(db, incoming)
        item = Outbox(
            business_id=other.id,
            config_version=1,
            message_id=mid,
            conversation_key=incoming.conversation_key,
            text="Reply",
            status="ambiguous",
        )
        db.add(item)
        db.commit()
        return own, other, incoming.conversation_key, item.id


def test_all_conversation_routes_reject_cross_business_access(full_platform_client):
    client, _ = full_platform_client
    user = create_identity("owner")
    own, other, key, item = make_conversation_workspace(user)
    headers = sign_in(client, user)
    for path in ("conversations", f"conversations/{key}", f"conversations/{key}/events"):
        assert client.get(f"/api/businesses/{other.id}/{path}").status_code == 404
    assert client.get(f"/api/businesses/{own.id}/conversations/{key}").status_code == 404
    assert client.get(f"/api/businesses/{own.id}/conversations/{key}/events").status_code == 404
    assert (
        client.post(
            f"/api/businesses/{own.id}/conversations/{key}/ownership",
            headers=headers,
            json={"action": "resume"},
        ).status_code
        == 404
    )
    body = {"outcome": "not_sent", "note": "Provider history checked"}
    assert (
        client.post(
            f"/api/businesses/{own.id}/delivery/{item}/reconcile", headers=headers, json=body
        ).status_code
        == 404
    )
    assert (
        client.post(
            f"/api/businesses/{other.id}/simulator/messages", headers=headers, json={}
        ).status_code
        == 404
    )


def test_scoped_reconciliation_requires_csrf_and_records_delivery(full_platform_client):
    client, _ = full_platform_client
    user = create_identity("owner")
    own, other, key, item = make_conversation_workspace(user)
    with session() as db:
        db.add(Membership(user_id=user.id, business_id=other.id))
        db.commit()
    headers = sign_in(client, user)
    path = f"/api/businesses/{other.id}/delivery/{item}/reconcile"
    body = {
        "outcome": "accepted",
        "provider_id": "verified-provider-id",
        "note": "Provider history checked",
    }
    assert client.post(path, json=body).status_code == 403
    response = client.post(path, headers=headers, json=body)
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "accepted"
    detail = client.get(f"/api/businesses/{other.id}/conversations/{key}").json()
    assert detail["messages"][-1]["direction"] == "outgoing"
    assert detail["messages"][-1]["status"] == "accepted"
    assert client.post(path, headers=headers, json=body).status_code == 409


@pytest.mark.parametrize("revocation", ["session", "membership"])
def test_event_stream_closes_when_access_is_revoked(full_platform_client, revocation):
    import asyncio

    from salma.conversation_api import events
    from starlette.requests import Request

    client, memory = full_platform_client
    user = create_identity("owner")
    own, other, key, item = make_conversation_workspace(user)
    with session() as db:
        db.add(Membership(user_id=user.id, business_id=other.id))
        db.commit()
    sign_in(client, user)
    cookie = client.cookies.get("salma_session")
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": [(b"cookie", f"salma_session={cookie}".encode())],
        "query_string": b"",
        "server": ("testserver", 80),
    }
    request = Request(scope)

    async def connected():
        return False

    request.is_disconnected = connected

    async def check():
        response = await events(request, other.id, key, after=2_000_000_000)
        iterator = response.body_iterator
        assert await anext(iterator) == ": heartbeat\n\n"
        if revocation == "session":
            memory.values.clear()
        else:
            with session() as db:
                db.delete(db.get(Membership, (user.id, other.id)))
                db.commit()
        with pytest.raises(StopAsyncIteration):
            await anext(iterator)

    asyncio.run(check())


def test_publish_preserves_constant_consent_acknowledgments(platform_client):
    from salma.adapters import Inbound
    from salma.db import Outbox
    from salma.store import accept

    client = platform_client
    headers = sign_in(client, create_identity())
    bid = client.post("/api/businesses", headers=headers, json={"name": "Consent"}).json()["id"]
    client.post(f"/api/businesses/{bid}/publish", headers=headers)
    identifiers = {}
    with session() as db:
        for label, kind, status in [
            ("waiting", "optout", "pending"),
            ("dispatching", "optout", "sending"),
            ("ordinary", "reply", "pending"),
            ("completed", "optout", "accepted"),
        ]:
            incoming = Inbound(
                business_id=bid,
                config_version=1,
                provider="simulator",
                channel="simulator",
                account_id="local",
                inbox_id="sim",
                external_message_id=str(uuid4()),
                external_conversation_id=str(uuid4()),
                external_customer_id=str(uuid4()),
                text="STOP" if kind == "optout" else "hello",
            )
            message_id, _ = accept(db, incoming)
            item = Outbox(
                business_id=bid,
                config_version=1,
                message_id=message_id,
                conversation_key=incoming.conversation_key,
                kind=kind,
                status=status,
                text="Automated replies stopped." if kind == "optout" else "Old business answer",
            )
            db.add(item)
            db.commit()
            identifiers[label] = item.id
    assert client.post(f"/api/businesses/{bid}/publish", headers=headers).json()["version"] == 2
    with session() as db:
        pending = db.get(Outbox, identifiers["waiting"])
        dispatching = db.get(Outbox, identifiers["dispatching"])
        reply = db.get(Outbox, identifiers["ordinary"])
        completed = db.get(Outbox, identifiers["completed"])
        assert pending.status == "pending" and pending.config_version == 2
        assert dispatching.status == "sending" and dispatching.config_version == 2
        assert pending.text == "Automated replies stopped."
        assert reply.status == "suppressed" and reply.config_version == 1
        assert completed.status == "accepted" and completed.config_version == 1
    from salma.db import Message
    from salma.settings import Settings
    from salma.worker import Worker

    worker = Worker(Settings(agent_mode="simulator"))
    assert worker.deliver_conversation(pending.conversation_key)
    assert not worker.deliver_conversation(pending.conversation_key)
    with session() as db:
        assert db.get(Outbox, pending.id).status == "simulated"
        sent = list(
            db.scalars(
                select(Message).where(
                    Message.conversation_key == pending.conversation_key,
                    Message.direction == "outgoing",
                )
            )
        )
        assert len(sent) == 1
        assert sent[0].text == "Automated replies stopped."
