"""Read-only, workspace-scoped operational dashboard aggregates."""

from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import and_, case, func, or_, select

from salma.auth import require_business
from salma.db import Conversation, Customer, Event, Message, Outbox, now, session
from salma.platform_models import Channel, ConfigVersion, Document

router = APIRouter()


def iso(value):
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


def build_overview(db, business, days, at):
    try:
        zone = ZoneInfo(business.draft.get("timezone") or "Africa/Cairo")
    except ZoneInfoNotFoundError, ValueError:
        zone = ZoneInfo("Africa/Cairo")
    today = at.astimezone(zone).date()
    dates = [today - timedelta(days=i) for i in reversed(range(days))]

    def midnight(day):
        return datetime.combine(day, time.min, zone).astimezone(timezone.utc)

    start = midnight(today)
    scope = [Conversation.business_id == business.id, Conversation.provider != "simulator"]
    available = [Conversation.ownership != "CLOSED", Conversation.opted_out.is_(False)]
    lead = and_(
        func.coalesce(Conversation.lead["archived"].as_boolean(), False).is_(False),
        or_(
            func.coalesce(Conversation.lead["requirements"].as_string(), "") != "",
            func.coalesce(Conversation.lead["service_id"].as_string(), "") != "",
            func.coalesce(Conversation.lead["service_interest"].as_string(), "[]") != "[]",
            func.coalesce(Conversation.lead["signals"].as_string(), "[]") != "[]",
        ),
    )

    def count(*conditions):
        return db.scalar(select(func.count()).select_from(Conversation).where(*scope, *conditions))

    today_counts = {
        "new_conversations": count(Conversation.created_at >= start, Conversation.created_at <= at),
        "new_lead_conversations": count(
            Conversation.created_at >= start, Conversation.created_at <= at, lead, *available
        ),
    }
    current = {
        name: count(Conversation.ownership == state, Conversation.opted_out.is_(False))
        for name, state in (
            ("ai_active", "AI_ACTIVE"),
            ("human_active", "HUMAN_ACTIVE"),
            ("pending_handoffs", "HANDOFF_PENDING"),
        )
    }
    delivery = (
        select(Outbox.conversation_key.label("key"), func.count().label("issues"))
        .where(
            Outbox.business_id == business.id, Outbox.status.in_(["failed", "ambiguous", "blocked"])
        )
        .group_by(Outbox.conversation_key)
        .subquery()
    )
    delivery_issues = db.scalar(
        select(func.coalesce(func.sum(delivery.c.issues), 0))
        .select_from(Conversation)
        .join(delivery, delivery.c.key == Conversation.key)
        .where(*scope)
    )
    pending = (
        select(Event.conversation_key.label("key"), func.max(Event.created_at).label("since"))
        .where(
            Event.business_id == business.id,
            or_(
                Event.kind == "handoff_created",
                and_(
                    Event.kind == "ownership_changed",
                    Event.data["after"].as_string() == "HANDOFF_PENDING",
                ),
            ),
        )
        .group_by(Event.conversation_key)
        .subquery()
    )
    activity = (
        select(Message.conversation_key.label("key"), func.max(Message.timestamp).label("last"))
        .where(Message.business_id == business.id, Message.direction.in_(["incoming", "human"]))
        .group_by(Message.conversation_key)
        .subquery()
    )
    rows = (
        select(Conversation, Customer.profile, pending.c.since, activity.c.last, delivery.c.issues)
        .outerjoin(
            Customer,
            and_(Customer.key == Conversation.customer_key, Customer.business_id == business.id),
        )
        .outerjoin(pending, pending.c.key == Conversation.key)
        .outerjoin(activity, activity.c.key == Conversation.key)
        .outerjoin(delivery, delivery.c.key == Conversation.key)
        .where(*scope)
    )
    published = (
        db.scalar(
            select(ConfigVersion).where(
                ConfigVersion.business_id == business.id,
                ConfigVersion.version == business.published_version,
            )
        )
        if business.published_version
        else None
    )
    catalog = {
        item["id"]: item.get("name", item["id"])
        for item in (published.catalog if published else [])
    }

    def summary(row):
        conv, profile, since, last, issues = row
        details = conv.lead or {}
        interest = details.get("service_interest") or [details.get("service_id")]
        return {
            "key": conv.key,
            "customer": (profile or {}).get("name")
            or (profile or {}).get("full_name")
            or conv.external_id,
            "channel": conv.channel,
            "ownership": conv.ownership,
            "reason": (conv.handoff or {}).get("reason"),
            "pending_since": iso(since) if conv.ownership == "HANDOFF_PENDING" else None,
            "last_activity": iso(last or conv.created_at),
            "delivery_issues": issues or 0,
            "requirements": details.get("requirements", ""),
            "interest": [catalog.get(key, key) for key in interest if key],
            "temperature": details.get("temperature"),
            "score": details.get("score"),
        }

    priorities = db.execute(
        rows.where(
            or_(
                and_(
                    Conversation.ownership == "HANDOFF_PENDING", Conversation.opted_out.is_(False)
                ),
                delivery.c.issues > 0,
            )
        )
        .order_by(
            func.coalesce(pending.c.since, activity.c.last, Conversation.created_at),
            Conversation.key,
        )
        .limit(6)
    ).all()
    leads = db.execute(
        rows.where(lead, *available)
        .order_by(func.coalesce(activity.c.last, Conversation.created_at).desc(), Conversation.key)
        .limit(6)
    ).all()
    bucket = case(
        *[
            (
                and_(
                    Conversation.created_at >= midnight(day),
                    Conversation.created_at < midnight(day + timedelta(days=1)),
                ),
                day.isoformat(),
            )
            for day in dates
        ]
    )
    trend_rows = db.execute(
        select(bucket.label("day"), Conversation.channel, func.count())
        .where(*scope, Conversation.created_at >= midnight(dates[0]), Conversation.created_at <= at)
        .group_by(bucket, Conversation.channel)
    ).all()
    trend = {
        day.isoformat(): {"date": day.isoformat(), "total": 0, "channels": {}} for day in dates
    }
    for day, channel, total in trend_rows:
        trend[day]["total"] += total
        trend[day]["channels"][channel] = total

    documents = db.execute(
        select(Document.status, Document.approved).where(Document.business_id == business.id)
    ).all()
    channels = list(
        db.scalars(select(Channel).where(Channel.business_id == business.id).order_by(Channel.name))
    )
    channel_rows = [
        {
            "id": row.id,
            "name": row.name,
            "kind": row.kind,
            "status": row.status,
            "live_enabled": row.live_enabled,
            "connection_state": (row.details or {}).get("connection_state"),
        }
        for row in channels
    ]
    from salma.platform_api import validate_config

    changed = bool(
        published
        and (
            validate_config(business.draft) != published.config
            or business.draft_product_revision_id != published.product_revision_id
        )
    )
    return {
        "generated_at": iso(at),
        "timezone": str(zone),
        "today": today_counts,
        "current": current,
        "attention": {
            "pending_handoffs": current["pending_handoffs"],
            "delivery_issues": delivery_issues,
            "documents_to_approve": sum(
                not d.approved and d.status in ("ready", "indexed") for d in documents
            ),
            "document_failures": sum(d.status in ("failed", "error") for d in documents),
            "channel_issues": sum(
                row.status in ("setup_error", "awaiting_pairing", "error", "disconnected")
                or (
                    row.live_enabled
                    and (row.details or {}).get("connection_state")
                    in ("close", "closed", "connecting")
                )
                for row in channels
            ),
        },
        "priority_conversations": [summary(row) for row in priorities],
        "leads": [summary(row) for row in leads],
        "trend": list(trend.values()),
        "channels": channel_rows,
        "agent": {
            "published_version": business.published_version,
            "published_at": iso(published.created_at) if published else None,
            "unpublished_changes": changed,
        },
        "resources": {
            "approved_knowledge": sum(
                d.approved and d.status in ("ready", "indexed") for d in documents
            ),
            "draft_services": len(business.draft.get("catalog", [])),
        },
    }


@router.get("/api/businesses/{business_id}/overview")
def overview(request: Request, business_id: str, days: int = 7):
    business = require_business(request, business_id)
    if days not in (7, 30):
        raise HTTPException(422, "Choose a 7 or 30 day period")
    with session() as db:
        return build_overview(db, business, days, now())
