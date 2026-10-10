import asyncio
import hmac
import json
from uuid import uuid4

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError

from salma.adapters import Inbound, normalize_chatwoot
from salma.conversation_api import router as conversation_router
from salma.db import (
    Conversation,
    Event,
    Knowledge,
    Message,
    Outbox,
    event,
    record_outgoing,
    session,
)
from salma.evolution_channel import router as evolution_router
from salma.platform_api import router as platform_router
from salma.product_catalog import router as product_router
from salma.product_imports import router as product_import_router
from salma.settings import settings
from salma.store import accept, ownership
from salma.workspace_services import router as workspace_router

app = FastAPI(title="Salma", version="0.1.0")


@app.middleware("http")
async def legacy_gate(request, call_next):
    path = request.url.path
    if not settings().legacy_api_enabled and (
        path in {"/api/status", "/api/catalog", "/api/conversations", "/api/simulator/messages"}
        or path.startswith(("/api/conversations/", "/api/delivery/", "/webhooks/chatwoot/"))
    ):
        return JSONResponse({"detail": "Use business-scoped APIs"}, status_code=404)
    return await call_next(request)


def staff(authorization: str = Header(default="")):
    expected = settings().staff_token.get_secret_value()
    supplied = authorization.removeprefix("Bearer ")
    if (
        not expected
        or not authorization.startswith("Bearer ")
        or not hmac.compare_digest(expected, supplied)
    ):
        raise HTTPException(401, "Staff authentication required")


def serialize(row, fields):
    result = {}
    for name in fields:
        value = getattr(row, name)
        result[name] = value.isoformat() if hasattr(value, "isoformat") else value
    return result


CONVERSATION_FIELDS = [
    "key",
    "customer_key",
    "provider",
    "channel",
    "external_id",
    "ownership",
    "lead",
    "handoff",
    "created_at",
]
MESSAGE_FIELDS = [
    "id",
    "direction",
    "content_type",
    "text",
    "status",
    "timestamp",
    "decision",
    "correlation_id",
]
OUTBOX_FIELDS = ["id", "text", "kind", "status", "provider_id", "error", "created_at"]
EVENT_FIELDS = ["id", "kind", "data", "correlation_id", "created_at"]


@app.get("/health")
def health():
    try:
        with session() as db:
            db.execute(text("SELECT 1"))
    except SQLAlchemyError:
        raise HTTPException(503, "Database unavailable") from None
    return {
        "status": "ok",
        "agent_mode": settings().agent_mode,
        "live_enabled": settings().live_enabled,
    }


@app.get("/api/status", dependencies=[Depends(staff)])
def status():
    config = settings()
    with session() as db:
        vectors = db.scalar(
            select(func.count()).select_from(Knowledge).where(Knowledge.model.is_not(None))
        )
    return {
        "agent_mode": config.agent_mode,
        "model": config.groq_model,
        "groq_configured": bool(config.groq_api_key.get_secret_value()),
        "live_enabled": config.live_enabled,
        "chatwoot_configured": bool(config.chatwoot_api_token.get_secret_value())
        and bool(config.chatwoot_inboxes),
        "scoring_configured": bool(config.lead_weights)
        and {"warm", "hot"} <= config.lead_thresholds.keys(),
        "vector_chunks": vectors,
    }


@app.get("/api/conversations", dependencies=[Depends(staff)])
def conversations():
    with session() as db:
        return [
            serialize(row, CONVERSATION_FIELDS)
            for row in db.scalars(
                select(Conversation).order_by(Conversation.created_at.desc()).limit(100)
            )
        ]


@app.get("/api/catalog", dependencies=[Depends(staff)])
def get_catalog():
    from salma.worker import catalog

    with session() as db:
        return list(catalog(db).values())


@app.get("/api/conversations/{key}", dependencies=[Depends(staff)])
def conversation_detail(key: str):
    with session() as db:
        row = db.get(Conversation, key)
        if not row:
            raise HTTPException(404, "Conversation not found")
        messages = db.scalars(
            select(Message)
            .where(Message.conversation_key == key)
            .order_by(Message.id.desc())
            .limit(200)
        ).all()
        events = db.scalars(
            select(Event).where(Event.conversation_key == key).order_by(Event.id.desc()).limit(100)
        ).all()
        return {
            "conversation": serialize(row, CONVERSATION_FIELDS),
            "messages": [serialize(item, MESSAGE_FIELDS) for item in reversed(messages)],
            "outbox": [
                serialize(item, OUTBOX_FIELDS)
                for item in db.scalars(
                    select(Outbox).where(Outbox.conversation_key == key).order_by(Outbox.id)
                )
            ],
            "events": [serialize(item, EVENT_FIELDS) for item in reversed(events)],
        }


class SimulatorMessage(BaseModel):
    conversation_id: str = Field(min_length=1, max_length=100)
    customer_id: str = Field(min_length=1, max_length=100)
    message_id: str = Field(default_factory=lambda: str(uuid4()), max_length=100)
    text: str = Field(max_length=12000)
    content_type: str = "text"


@app.post("/api/simulator/messages", status_code=202, dependencies=[Depends(staff)])
def simulator(body: SimulatorMessage):
    message = Inbound(
        provider="simulator",
        channel="simulator",
        account_id="local",
        inbox_id="sim",
        external_message_id=body.message_id,
        external_conversation_id=body.conversation_id,
        external_customer_id=body.customer_id,
        text=body.text,
        content_type=body.content_type,
    )
    with session() as db:
        try:
            message_id, created = accept(db, message)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None
    return {
        "message_id": message_id,
        "created": created,
        "conversation_key": message.conversation_key,
    }


class OwnershipAction(BaseModel):
    action: str = Field(pattern="^(takeover|close|resume)$")


@app.post("/api/conversations/{key}/ownership", dependencies=[Depends(staff)])
def change_ownership(key: str, body: OwnershipAction):
    with session() as db:
        try:
            row = ownership(db, key, body.action)
        except LookupError:
            raise HTTPException(404, "Conversation not found") from None
        return serialize(row, CONVERSATION_FIELDS)


@app.get("/api/conversations/{key}/events", dependencies=[Depends(staff)])
async def stream_events(key: str, request: Request, after: int = 0):
    with session() as db:
        if not db.get(Conversation, key):
            raise HTTPException(404, "Conversation not found")

    def read_events(cursor):
        with session() as db:
            return [
                serialize(row, EVENT_FIELDS)
                for row in db.scalars(
                    select(Event)
                    .where(Event.conversation_key == key, Event.id > cursor)
                    .order_by(Event.id)
                    .limit(100)
                )
            ]

    async def stream():
        cursor = after
        while not await request.is_disconnected():
            rows = await asyncio.to_thread(read_events, cursor)
            for row in rows:
                cursor = row["id"]
                yield f"id: {cursor}\ndata: {json.dumps(row, ensure_ascii=False)}\n\n"
            if not rows:
                yield ": heartbeat\n\n"
            await asyncio.sleep(1)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def outgoing_event(db, data, config, *, business_id="legacy"):
    if data.get("private"):
        return {"ignored": True}
    account = data.get("account", {}).get("id")
    inbox = data.get("inbox", {}).get("id")
    if account != config.chatwoot_account_id or inbox not in config.chatwoot_inboxes:
        raise ValueError("Unauthorized account or inbox")
    prefix = "" if business_id == "legacy" else business_id + ":"
    key = f"{prefix}chatwoot:{account}:{inbox}:{data['conversation']['id']}"
    conversation = db.scalar(select(Conversation).where(Conversation.key == key).with_for_update())
    if not conversation:
        return {"ignored": True}
    marker = data.get("content_attributes", {}).get("salma_outbox_id")
    item = (
        db.scalar(select(Outbox).where(Outbox.id == marker).with_for_update())
        if isinstance(marker, int)
        else None
    )
    if not item:
        item = db.scalar(
            select(Outbox)
            .where(Outbox.conversation_key == key, Outbox.provider_id == str(data.get("id")))
            .with_for_update()
        )
    if item and item.conversation_key == key:
        if data.get("status") in ("delivered", "read") and item.status != "delivered":
            item.status = "delivered"
            item.provider_id = str(data["id"])
            record_outgoing(db, item)
            event(db, key, "delivery_confirmed", {"outbox_id": item.id}, str(uuid4()))
            db.commit()
        return {"ignored": True, "bot_echo": True}
    if data.get("event") == "message_created" and data.get("sender", {}).get(
        "type", ""
    ).lower() in ("user", ""):
        correlation = str(uuid4())
        message_id = db.scalar(
            insert(Message)
            .values(
                business_id=business_id,
                conversation_key=key,
                external_id=str(data["id"]),
                direction="human",
                text=data.get("content") or "",
                status="delivered",
                correlation_id=correlation,
            )
            .on_conflict_do_nothing()
            .returning(Message.id)
        )
        if message_id is not None:
            event(db, key, "human_reply", {"message_id": message_id}, correlation)
            ownership(db, key, "takeover")
        else:
            db.commit()
        return {"ownership": conversation.ownership, "created": message_id is not None}
    return {"ignored": True}


@app.post("/webhooks/chatwoot/{token}", status_code=202)
def webhook(token: str, data: dict):
    config = settings()
    expected = config.webhook_token.get_secret_value()
    if not expected or not hmac.compare_digest(expected, token):
        raise HTTPException(401, "Webhook authentication required")
    if not config.live_enabled:
        raise HTTPException(503, "Live integration is disabled until simulator evaluation passes")
    try:
        with session() as db:
            if data.get("message_type") in ("outgoing", 1) and data.get("event") in (
                "message_created",
                "message_updated",
            ):
                return outgoing_event(db, data, config)
            incoming = normalize_chatwoot(data, config.chatwoot_account_id, config.chatwoot_inboxes)
            if not incoming:
                return {"ignored": True}
            message_id, created = accept(db, incoming)
            return {"message_id": message_id, "created": created}
    except ValueError, KeyError:
        raise HTTPException(422, "Invalid or unauthorized webhook payload") from None
    except SQLAlchemyError:
        raise HTTPException(503, "Persistence unavailable; retry delivery") from None


class Reconciliation(BaseModel):
    outcome: str = Field(pattern="^(accepted|not_sent)$")
    provider_id: str | None = None
    note: str = Field(min_length=10, max_length=2000)


@app.post("/api/delivery/{item_id}/reconcile", dependencies=[Depends(staff)])
def reconcile(item_id: int, body: Reconciliation):
    # Staff must inspect provider history before recording an outcome. Never auto-retry.
    with session() as db:
        item = db.get(Outbox, item_id)
        if not item:
            raise HTTPException(404, "Outbound work not found")
        db.scalar(
            select(Conversation).where(Conversation.key == item.conversation_key).with_for_update()
        )
        item = db.scalar(
            select(Outbox)
            .where(Outbox.id == item_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if item.status not in ("ambiguous", "failed", "blocked"):
            raise HTTPException(409, "This delivery does not require reconciliation")
        if body.outcome == "accepted" and not body.provider_id:
            raise HTTPException(422, "Provider message ID is required")
        item.status = "accepted" if body.outcome == "accepted" else "not_sent"
        item.provider_id = body.provider_id
        item.error = None
        if body.outcome == "accepted":
            record_outgoing(db, item)
        event(db, item.conversation_key, "delivery_reconciled", body.model_dump(), str(uuid4()))
        db.commit()
        return serialize(item, OUTBOX_FIELDS)


app.include_router(platform_router)
app.include_router(product_router)
app.include_router(workspace_router)
app.include_router(conversation_router)
app.include_router(product_import_router)
app.include_router(evolution_router)
