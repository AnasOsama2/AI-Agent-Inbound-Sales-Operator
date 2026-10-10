"""Direct, tenant-bound WhatsApp transport. Chatwoot is for social channels."""

import hashlib
import re
import secrets
from datetime import datetime, timezone
from uuid import uuid4

import httpx
from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from salma.adapters import Inbound
from salma.auth import rate_limit, require_business
from salma.db import Conversation, Message, Outbox, event, now, session
from salma.platform_models import Business, Channel
from salma.settings import settings
from salma.store import accept, ownership

router = APIRouter()
EVENTS = ["MESSAGES_UPSERT", "SEND_MESSAGE", "MESSAGES_UPDATE"]


def channel_condition(conversation):
    return (
        Channel.business_id == conversation.business_id,
        Channel.account_id == conversation.account_id,
        (Channel.id if conversation.provider == "evolution" else Channel.inbox_id)
        == conversation.inbox_id,
    )


def route_enabled(channel, conversation):
    if not channel or not channel.live_enabled:
        return False
    transport = (channel.details or {}).get("transport", "chatwoot")
    return (
        transport == "evolution"
        if conversation.provider == "evolution"
        else transport == "chatwoot"
    )


def normalize_evolution(payload, channel, version):
    if payload.get("instance") != channel.evolution_instance:
        raise ValueError("Wrong Evolution instance")
    if payload.get("event", "").lower().replace("_", ".") != "messages.upsert":
        return None
    data = payload.get("data") or {}
    if not isinstance(data, dict) or data.get("type") == "append":
        return None
    key = data.get("key") or {}
    jid = key.get("remoteJid", "")
    if key.get("fromMe") or not re.fullmatch(r"[0-9]+@(s\.whatsapp\.net|lid)", jid):
        return None
    if not key.get("id"):
        raise ValueError("Missing message identity")
    timestamp = data.get("messageTimestamp")
    if isinstance(timestamp, dict):
        timestamp = timestamp.get("low")
    timestamp = float(timestamp or 0)
    if not timestamp:
        raise ValueError("Missing message timestamp")
    if timestamp < (channel.details or {}).get("direct_since", 0):
        return None
    message = data.get("message") or {}
    for _ in range(3):
        wrapper = message.get("ephemeralMessage") or message.get("viewOnceMessage")
        if not wrapper:
            break
        message = wrapper.get("message") or {}
    text = message.get("conversation") or (message.get("extendedTextMessage") or {}).get("text")
    if not text and not any(
        k in message for k in ("imageMessage", "audioMessage", "videoMessage", "documentMessage")
    ):
        return None
    return Inbound(
        business_id=channel.business_id,
        config_version=version,
        provider="evolution",
        channel="whatsapp",
        account_id=channel.account_id,
        inbox_id=channel.id,
        external_message_id=str(key["id"]),
        external_conversation_id=jid,
        external_customer_id=jid,
        text=text or "",
        content_type="text" if text else "media",
        timestamp=datetime.fromtimestamp(timestamp, timezone.utc),
    )


def direct_webhook_url(channel, credentials):
    return (
        settings().public_api_url.rstrip("/")
        + f"/webhooks/evolution/{channel.id}/{credentials['webhook_secret']}"
    )


def configure_direct(business_id, channel_id):
    from salma.workspace_services import (
        channel_view,
        get_channel_credentials,
        provider_request,
        tenant_row,
        unwrap_list,
    )

    with session() as db, db.begin():
        channel = tenant_row(db, Channel, channel_id, business_id)
        if channel.kind != "whatsapp":
            raise HTTPException(422, "This connection is for WhatsApp only")
        was_live = channel.live_enabled
        channel.live_enabled = False
        channel.evolution_instance = channel.evolution_instance or "salma-" + channel.id.replace(
            "-", ""
        )
        if db.scalar(
            select(Channel.id).where(
                Channel.evolution_instance == channel.evolution_instance, Channel.id != channel.id
            )
        ):
            raise HTTPException(409, "Evolution instance already belongs to another channel")
        channel.details = {**(channel.details or {}), "transport": "migrating"}
        instance = channel.evolution_instance
        credentials = get_channel_credentials(channel)
    try:
        existing = unwrap_list(provider_request("evolution", "GET", "/instance/fetchInstances"))
        if not any(
            r.get("name") == instance or (r.get("instance") or {}).get("instanceName") == instance
            for r in existing
        ):
            provider_request(
                "evolution",
                "POST",
                "/instance/create",
                body={"instanceName": instance, "integration": "WHATSAPP-BAILEYS", "qrcode": False},
            )
        bridge = provider_request("evolution", "GET", f"/chatwoot/find/{instance}")
        if bridge and bridge.get("enabled"):
            provider_request(
                "evolution",
                "POST",
                f"/chatwoot/set/{instance}",
                body={
                    "enabled": False,
                    "accountId": str(bridge["accountId"]),
                    "token": bridge["token"],
                    "url": bridge["url"],
                    "signMsg": False,
                    "reopenConversation": False,
                    "conversationPending": False,
                    "importMessages": False,
                    "importContacts": False,
                    "daysLimitImportMessages": 0,
                    "autoCreate": False,
                },
            )
        provider_request(
            "evolution",
            "POST",
            f"/webhook/set/{instance}",
            body={
                "webhook": {
                    "enabled": True,
                    "url": direct_webhook_url(channel, credentials),
                    "byEvents": False,
                    "base64": False,
                    "events": EVENTS,
                }
            },
        )
        hook = provider_request("evolution", "GET", f"/webhook/find/{instance}")
        if not (
            hook.get("enabled") is True
            and hook.get("url") == direct_webhook_url(channel, credentials)
            and set(EVENTS).issubset(hook.get("events") or [])
        ):
            raise HTTPException(502, "Evolution webhook verification failed")
        state = (
            provider_request("evolution", "GET", f"/instance/connectionState/{instance}")
            .get("instance", {})
            .get("state")
        )
        with session() as db, db.begin():
            row = tenant_row(db, Channel, channel_id, business_id)
            previous = row.details or {}
            row.details = {
                "transport": "evolution",
                "direct_since": previous.get("direct_since", now().timestamp()),
                "webhook_registered": True,
                "connection_state": state,
                "guidance": "WhatsApp connects directly through Evolution. "
                "Staff take over and resume in Conversations.",
                "destinations": previous.get("destinations", {}),
            }
            row.status = "ready" if state == "open" else "awaiting_pairing"
            row.live_enabled = was_live
            return channel_view(row)
    except Exception:
        with session() as db, db.begin():
            row = tenant_row(db, Channel, channel_id, business_id)
            row.live_enabled = False
            row.status = "setup_error"
        raise


@router.post("/api/businesses/{business_id}/channels/{channel_id}/direct-whatsapp")
def connect_direct(request: Request, business_id: str, channel_id: str):
    require_business(request, business_id)
    return configure_direct(business_id, channel_id)


@router.post("/api/businesses/{business_id}/whatsapp", status_code=201)
def create_direct(request: Request, business_id: str):
    from salma.workspace_services import encrypt_credentials

    require_business(request, business_id)
    rate_limit("create-whatsapp:" + business_id, 5)
    with session() as db, db.begin():
        if db.scalar(
            select(Channel.id).where(Channel.business_id == business_id, Channel.kind == "whatsapp")
        ):
            raise HTTPException(409, "Use the WhatsApp connection already in this workspace")
        identifier, secret = str(uuid4()), secrets.token_urlsafe(32)
        db.add(
            Channel(
                id=identifier,
                business_id=business_id,
                kind="whatsapp",
                name="Business WhatsApp",
                account_id="evolution",
                inbox_id=identifier,
                credentials=encrypt_credentials({"webhook_secret": secret}),
                webhook_secret_hash=hashlib.sha256(secret.encode()).hexdigest(),
                live_enabled=False,
                status="configured",
                details={},
            )
        )
    return configure_direct(business_id, identifier)


def send_evolution(conversation, item, config):
    with session() as db:
        channel = db.scalar(select(Channel).where(*channel_condition(conversation)))
        if not route_enabled(channel, conversation):
            raise ValueError("Direct WhatsApp channel is paused")
        instance = channel.evolution_instance
    with httpx.Client(timeout=20) as client:
        response = client.post(
            config.evolution_url.rstrip("/") + f"/message/sendText/{instance}",
            headers={"apikey": config.evolution_api_key.get_secret_value()},
            json={"number": conversation.external_id, "text": item.text},
        )
        response.raise_for_status()
        payload = response.json()
    return {"id": (payload.get("key") or {}).get("id"), "status": "accepted"}


def inspect_direct(channel, *, operator):
    from salma.workspace_services import get_channel_credentials, provider_request

    evolution = {
        "status": "unavailable",
        "detail": "Cannot verify Evolution connection.",
        "webhook_verified": False,
        "url": settings().evolution_public_url.rstrip("/") + "/manager" if operator else None,
    }
    try:
        credentials = get_channel_credentials(channel)
        state = (
            provider_request(
                "evolution",
                "GET",
                f"/instance/connectionState/{channel.evolution_instance}",
                timeout=8,
            )
            .get("instance", {})
            .get("state")
        )
        hook = provider_request(
            "evolution", "GET", f"/webhook/find/{channel.evolution_instance}", timeout=8
        )
        bridge = provider_request(
            "evolution", "GET", f"/chatwoot/find/{channel.evolution_instance}", timeout=8
        )
        verified = (
            hook.get("enabled") is True
            and hook.get("url") == direct_webhook_url(channel, credentials)
            and set(EVENTS).issubset(hook.get("events") or [])
            and not bridge.get("enabled")
        )
        evolution.update(
            connection_state=state,
            webhook_verified=verified,
            status=("connected" if state == "open" else "awaiting_pairing")
            if verified
            else "needs_setup",
            detail=(
                "Direct Salma webhook verified; Chatwoot bridge disabled. "
                + ("WhatsApp paired." if state == "open" else "Pair WhatsApp using the QR code.")
            )
            if verified
            else "Direct webhook or disabled bridge needs repair.",
        )
    except HTTPException:
        pass
    return {
        "chatwoot": {
            "status": "not_used",
            "url": None,
            "detail": "WhatsApp uses Evolution directly. Chatwoot handles Messenger and Instagram.",
        },
        "evolution": evolution,
        "live_enabled": channel.live_enabled,
        "checked_at": now().isoformat(),
    }


@router.post("/webhooks/evolution/{channel_id}/{secret}", status_code=202)
def evolution_webhook(channel_id: str, secret: str, payload: dict):
    from salma.workspace_services import resolve_channel_webhook

    channel = resolve_channel_webhook(channel_id, secret, allow_paused=True)
    if channel.kind != "whatsapp" or (channel.details or {}).get("transport") != "evolution":
        raise HTTPException(409, "This channel does not use direct Evolution routing")
    if payload.get("instance") != channel.evolution_instance:
        raise HTTPException(422, "Instance does not match this channel")
    name = payload.get("event", "").lower().replace("_", ".")
    data = payload.get("data") or {}
    if not isinstance(data, dict):
        return {"ignored": True}
    key = data.get("key") or {}
    if name == "send.message":
        return {"ignored": True, "bot_echo": True}
    if name == "messages.upsert" and key.get("fromMe"):
        jid, identity = key.get("remoteJid"), key.get("id")
        if not jid or not identity:
            raise HTTPException(422, "Missing message identity")
        with session() as db:
            conv = db.scalar(
                select(Conversation)
                .where(
                    Conversation.business_id == channel.business_id,
                    Conversation.provider == "evolution",
                    Conversation.inbox_id == channel.id,
                    Conversation.external_id == jid,
                )
                .with_for_update()
            )
            if not conv:
                return {"ignored": True}
            if db.scalar(
                select(Outbox.id).where(
                    Outbox.conversation_key == conv.key, Outbox.provider_id == str(identity)
                )
            ):
                return {"ignored": True, "bot_echo": True}
            correlation = str(uuid4())
            created = db.scalar(
                insert(Message)
                .values(
                    business_id=channel.business_id,
                    conversation_key=conv.key,
                    external_id=str(identity),
                    direction="human",
                    text=(data.get("message") or {}).get("conversation")
                    or (data.get("message") or {}).get("extendedTextMessage", {}).get("text", ""),
                    status="delivered",
                    correlation_id=correlation,
                )
                .on_conflict_do_nothing()
                .returning(Message.id)
            )
            if created is not None:
                event(db, conv.key, "human_reply", {"message_id": created}, correlation)
                ownership(db, conv.key, "takeover")
            else:
                db.commit()
            return {"human_takeover": created is not None}
    try:
        with session() as db:
            business = db.get(Business, channel.business_id)
            if not business or not business.published_version:
                raise HTTPException(503, "Publish the business configuration first")
            incoming = normalize_evolution(payload, channel, business.published_version)
            if incoming is None:
                return {"ignored": True}
            identifier, created = accept(db, incoming)
            if created and not channel.live_enabled:
                db.get(Message, identifier).status = "suppressed"
                db.commit()
            return {"message_id": identifier, "created": created}
    except ValueError, TypeError, KeyError:
        raise HTTPException(422, "Invalid Evolution message") from None
