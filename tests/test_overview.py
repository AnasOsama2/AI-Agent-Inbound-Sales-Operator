"""Dashboard totals must stay scoped, exclude tests, and respect local midnight."""

import json
from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from salma import auth
from salma.db import Base, Conversation, Customer, Event, Message, Outbox
from salma.platform_models import Business, Channel, ConfigVersion, Document, Membership, User
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

AT = datetime(2026, 10, 11, 10, tzinfo=timezone.utc)


@pytest.fixture
def overview_db():
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    with sessionmaker(engine, expire_on_commit=False)() as db:
        business = Business(id="own", name="Shop", draft={"timezone": "Africa/Cairo"})
        db.add_all([business, Business(id="other", name="Other", draft={})])
        db.commit()
        yield db, business, engine
    engine.dispose()


def conversation(
    db,
    key,
    *,
    business="own",
    provider="chatwoot",
    ownership="AI_ACTIVE",
    created="2026-10-10T22:00:00+00:00",
    lead=None,
):
    db.add(Customer(key=key, business_id=business, profile={"name": key}))
    row = Conversation(
        key=key,
        customer_key=key,
        business_id=business,
        provider=provider,
        channel="whatsapp",
        account_id="1",
        inbox_id="2",
        external_id=key,
        ownership=ownership,
        created_at=datetime.fromisoformat(created),
        lead=lead or {},
    )
    db.add(row)
    db.flush()
    return row


def test_totals_exclude_simulators_other_businesses_and_use_business_midnight(overview_db):
    from salma.overview import build_overview

    db, business, _ = overview_db
    conversation(
        db,
        "local-today",
        ownership="HANDOFF_PENDING",
        lead={"requirements": "Need an exam", "temperature": "hot"},
    )
    conversation(db, "yesterday", created="2026-10-10T20:59:00+00:00")
    conversation(db, "test", provider="simulator", lead={"requirements": "test"})
    conversation(db, "foreign", business="other", ownership="HANDOFF_PENDING")
    conversation(db, "empty-score", lead={"score": 0, "temperature": "cold", "signals": []})
    db.commit()
    result = build_overview(db, business, 7, AT)
    assert result["today"] == {"new_conversations": 2, "new_lead_conversations": 1}
    assert result["current"] == {"ai_active": 2, "human_active": 0, "pending_handoffs": 1}
    assert [r["key"] for r in result["priority_conversations"]] == ["local-today"]
    assert [r["key"] for r in result["leads"]] == ["local-today"]
    assert len(result["trend"]) == 7
    assert result["trend"][-1]["total"] == 2
    assert sum(r["total"] for r in result["trend"]) == 3


def test_counts_are_not_limited_to_inbox_page_and_archived_leads_are_excluded(overview_db):
    from salma.overview import build_overview

    db, business, _ = overview_db
    for i in range(105):
        conversation(db, f"customer-{i}")
    conversation(db, "archived", lead={"requirements": "Old", "archived": True})
    conversation(db, "closed", ownership="CLOSED", lead={"requirements": "Done"})
    conversation(db, "opted-out", lead={"requirements": "Stop"}).opted_out = True
    db.commit()
    result = build_overview(db, business, 30, AT)
    assert result["today"]["new_conversations"] == 108
    assert result["leads"] == []
    assert len(result["trend"]) == 30


def test_attention_delivery_wait_time_and_published_changes(overview_db):
    from salma.overview import build_overview

    db, business, _ = overview_db
    business.published_version = 1
    db.add(
        ConfigVersion(
            business_id="own",
            version=1,
            config=business.draft,
            product_revision_id="published-products",
        )
    )
    business.draft_product_revision_id = "new-products"
    row = conversation(db, "waiting", ownership="HANDOFF_PENDING")
    db.add(
        Event(
            conversation_key=row.key,
            business_id="own",
            kind="handoff_created",
            data={"reason": "human_requested"},
            correlation_id="1",
            created_at=AT,
        )
    )
    msg = Message(
        conversation_key=row.key,
        business_id="own",
        external_id="msg",
        direction="incoming",
        text="Help",
        correlation_id="1",
    )
    db.add(msg)
    db.flush()
    db.add(
        Outbox(
            message_id=msg.id,
            conversation_key=row.key,
            business_id="own",
            text="Reply",
            status="ambiguous",
        )
    )
    db.add(
        Document(
            business_id="own",
            filename="policy.txt",
            storage_path="/unused",
            status="ready",
            approved=False,
        )
    )
    db.add(
        Channel(
            business_id="own",
            kind="whatsapp",
            name="Phone",
            account_id="phone",
            webhook_secret_hash="secret",
            credentials="PRIVATE",
            status="awaiting_pairing",
            live_enabled=True,
            details={"connection_state": "close"},
        )
    )
    db.commit()
    result = build_overview(db, business, 7, AT)
    assert result["attention"]["delivery_issues"] == 1
    assert result["attention"]["documents_to_approve"] == 1
    assert result["attention"]["channel_issues"] == 1
    assert result["agent"]["unpublished_changes"] is True
    assert result["priority_conversations"][0]["pending_since"] == AT.isoformat()
    assert "PRIVATE" not in json.dumps(result)
    assert result["channels"][0]["live_enabled"] is True
    assert result["channels"][0]["connection_state"] == "close"


def test_endpoint_requires_membership_and_validates_range(overview_db, monkeypatch):
    from salma import overview

    db, _, engine = overview_db
    db.add(User(id="owner", email="owner@test", password_hash="unused", role="owner"))
    db.add(Membership(user_id="owner", business_id="own"))
    db.commit()
    factory = sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(auth, "session", factory)
    monkeypatch.setattr(overview, "session", factory)
    monkeypatch.setattr(
        auth, "redis_call", lambda *args: json.dumps({"user_id": "owner", "csrf": "x"})
    )
    app = FastAPI()
    app.include_router(overview.router)
    with TestClient(app) as client:
        assert client.get("/api/businesses/own/overview").status_code == 401
        client.cookies.set(auth.COOKIE, "test-session")
        assert client.get("/api/businesses/other/overview").status_code == 404
        assert client.get("/api/businesses/own/overview?days=99").status_code == 422
        assert client.get("/api/businesses/own/overview?days=7").status_code == 200
