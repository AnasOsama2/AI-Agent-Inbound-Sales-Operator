"""Authenticated business conversation APIs and verified channel webhooks."""

import asyncio
import json
from types import SimpleNamespace

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select

from salma.adapters import Inbound, normalize_chatwoot
from salma.auth import rate_limit, require_business
from salma.db import Conversation, Customer, Event, Knowledge, Message, Outbox, session
from salma.platform_models import Business, Channel
from salma.settings import settings
from salma.store import accept, ownership

router = APIRouter()


def scoped_conversation(db, business_id, key):
    row = db.scalar(
        select(Conversation).where(Conversation.key == key, Conversation.business_id == business_id)
    )
    if not row:
        raise HTTPException(404, "Conversation not found")
    return row


@router.get("/api/businesses/{business_id}/status")
def workspace_status(request: Request, business_id: str):
    business = require_business(request, business_id)
    cfg = settings()
    with session() as db:
        return {
            "agent_mode": cfg.agent_mode,
            "model": cfg.groq_model,
            "groq_configured": bool(cfg.groq_api_key.get_secret_value()),
            "published_version": business.published_version,
            "vector_chunks": db.scalar(
                select(func.count())
                .select_from(Knowledge)
                .where(Knowledge.business_id == business_id)
            ),
            "live_enabled": bool(
                db.scalar(
                    select(Channel.id).where(
                        Channel.business_id == business_id, Channel.live_enabled.is_(True)
                    )
                )
            ),
        }


@router.get("/api/businesses/{business_id}/conversations")
def list_conversations(request: Request, business_id: str):
    from salma.api import CONVERSATION_FIELDS, serialize

    require_business(request, business_id)
    with session() as db:
        return [
            serialize(row, CONVERSATION_FIELDS + ["business_id", "config_version", "opted_out"])
            for row in db.scalars(
                select(Conversation)
                .where(Conversation.business_id == business_id)
                .order_by(Conversation.created_at.desc())
                .limit(100)
            )
        ]


@router.get("/api/businesses/{business_id}/conversations/{key}")
def detail(request: Request, business_id: str, key: str):
    from salma.api import (
        CONVERSATION_FIELDS,
        EVENT_FIELDS,
        MESSAGE_FIELDS,
        OUTBOX_FIELDS,
        serialize,
    )

    require_business(request, business_id)
    with session() as db:
        row = scoped_conversation(db, business_id, key)
        customer = db.scalar(
            select(Customer).where(
                Customer.key == row.customer_key, Customer.business_id == business_id
            )
        )
        messages = db.scalars(
            select(Message)
            .where(Message.conversation_key == key, Message.business_id == business_id)
            .order_by(Message.id.desc())
            .limit(200)
        ).all()
        return {
            "customer": {"key": customer.key, "profile": customer.profile} if customer else None,
            "conversation": serialize(
                row, CONVERSATION_FIELDS + ["business_id", "config_version", "opted_out"]
            ),
            "messages": [
                serialize(m, MESSAGE_FIELDS + ["config_version"]) for m in reversed(messages)
            ],
            "outbox": [
                serialize(o, OUTBOX_FIELDS + ["config_version"])
                for o in db.scalars(
                    select(Outbox)
                    .where(Outbox.conversation_key == key, Outbox.business_id == business_id)
                    .order_by(Outbox.id)
                )
            ],
            "events": [
                serialize(e, EVENT_FIELDS)
                for e in db.scalars(
                    select(Event)
                    .where(Event.conversation_key == key, Event.business_id == business_id)
                    .order_by(Event.id.desc())
                    .limit(100)
                )
            ],
        }


@router.post("/api/businesses/{business_id}/simulator/messages", status_code=202)
def simulation(request: Request, business_id: str, body: dict):
    from salma.api import SimulatorMessage

    require_business(request, business_id)
    rate_limit("simulation:" + business_id, 100)
    try:
        data = SimulatorMessage.model_validate(body)
    except ValueError:
        raise HTTPException(422, "Invalid simulation message") from None
    with session() as db:
        business = db.get(Business, business_id)
        if not business.published_version:
            raise HTTPException(409, "Publish your business configuration before testing Salma")
        incoming = Inbound(
            business_id=business_id,
            config_version=business.published_version,
            provider="simulator",
            channel="simulator",
            account_id="local",
            inbox_id="sim",
            external_message_id=data.message_id,
            external_conversation_id=data.conversation_id,
            external_customer_id=data.customer_id,
            text=data.text,
            content_type=data.content_type,
        )
        try:
            identifier, created = accept(db, incoming)
        except ValueError:
            raise HTTPException(409, "Conversation customer cannot change") from None
        return {
            "message_id": identifier,
            "created": created,
            "conversation_key": incoming.conversation_key,
        }


@router.post("/api/businesses/{business_id}/conversations/{key}/ownership")
def change_ownership(request: Request, business_id: str, key: str, body: dict):
    require_business(request, business_id)
    if body.get("action") not in {"resume", "takeover", "close"}:
        raise HTTPException(422, "Choose resume, takeover or close")
    with session() as db:
        scoped_conversation(db, business_id, key)
        row = ownership(db, key, body["action"])
        return {"key": row.key, "ownership": row.ownership, "opted_out": row.opted_out}


@router.get("/api/businesses/{business_id}/conversations/{key}/events")
async def events(request: Request, business_id: str, key: str, after: int = 0):
    require_business(request, business_id)
    with session() as db:
        scoped_conversation(db, business_id, key)

    async def stream():
        cursor = after
        while not await request.is_disconnected():
            try:
                require_business(request, business_id)
            except HTTPException:
                # Logout, expiry and membership changes revoke established streams too.
                return
            with session() as db:
                rows = db.scalars(
                    select(Event)
                    .where(
                        Event.business_id == business_id,
                        Event.conversation_key == key,
                        Event.id > cursor,
                    )
                    .order_by(Event.id)
                    .limit(100)
                ).all()
            for row in rows:
                cursor = row.id
                payload = json.dumps({"id": row.id, "kind": row.kind, "data": row.data})
                yield f"id: {cursor}\ndata: {payload}\n\n"
            if not rows:
                yield ": heartbeat\n\n"
            await asyncio.sleep(1)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/api/businesses/{business_id}/delivery/{item_id}/reconcile")
def reconcile_delivery(request: Request, business_id: str, item_id: int, body: dict):
    from salma.api import Reconciliation, reconcile

    require_business(request, business_id)
    with session() as db:
        item = db.scalar(
            select(Outbox).where(Outbox.id == item_id, Outbox.business_id == business_id)
        )
        if not item:
            raise HTTPException(404, "Outbound work not found")
        scoped_conversation(db, business_id, item.conversation_key)
    try:
        validated = Reconciliation.model_validate(body)
    except ValueError:
        raise HTTPException(
            422, "Invalid reconciliation; include outcome and review note"
        ) from None
    # The shared handler locks conversation then outbox and never automatically retries.
    return reconcile(item_id, validated)


@router.post("/webhooks/channels/{channel_id}/{secret}", status_code=202)
def channel_webhook(channel_id: str, secret: str, data: dict):
    from salma.api import outgoing_event
    from salma.workspace_services import resolve_channel_webhook

    channel = resolve_channel_webhook(channel_id, secret, allow_paused=True)
    if channel.kind == "whatsapp" and (channel.details or {}).get("transport") in {
        "evolution",
        "migrating",
    }:
        return {"ignored": True, "reason": "whatsapp_uses_evolution"}
    cfg = SimpleNamespace(
        chatwoot_account_id=int(channel.account_id),
        chatwoot_inboxes={int(channel.inbox_id): channel.kind},
    )
    try:
        with session() as db:
            if data.get("message_type") in ("outgoing", 1) and data.get("event") in {
                "message_created",
                "message_updated",
            }:
                return outgoing_event(db, data, cfg, business_id=channel.business_id)
            incoming = normalize_chatwoot(
                data, int(channel.account_id), {int(channel.inbox_id): channel.kind}
            )
            if not incoming:
                return {"ignored": True}
            business = db.get(Business, channel.business_id)
            if not business.published_version:
                raise HTTPException(503, "No published configuration")
            incoming = incoming.model_copy(
                update={
                    "business_id": channel.business_id,
                    "config_version": business.published_version,
                }
            )
            identifier, created = accept(db, incoming)
            if not channel.live_enabled and created:
                stored = db.get(Message, identifier)
                if (stored.decision or {}).get("control") != "optout":
                    stored.status = "suppressed"
                    db.commit()
            return {"message_id": identifier, "created": created}
    except ValueError, KeyError:
        raise HTTPException(422, "Invalid or unauthorized webhook") from None
