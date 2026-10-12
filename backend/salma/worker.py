import json
import logging
import re
import ssl
import time
from contextlib import contextmanager

import httpx
from sqlalchemy import or_, select, text
from sqlalchemy.exc import SQLAlchemyError

from salma.agent import Agent, GroqCompletion, simulated_decision
from salma.db import (
    Conversation,
    Customer,
    Event,
    Knowledge,
    Message,
    Outbox,
    Service,
    engine,
    event,
    record_outgoing,
    session,
)
from salma.observability import diagnostics, emit, flow, phase
from salma.settings import settings
from salma.store import archive_draft, update_customer, update_draft
from salma.trust import Decision, calculate, render, score

logger = logging.getLogger("salma.worker")


def catalog(db):
    return {
        item.id: {
            "id": item.id,
            "name_en": item.name_en,
            "name_ar": item.name_ar,
            "price": str(item.price) if item.price is not None else None,
            "currency": item.currency,
        }
        for item in db.scalars(select(Service).where(Service.business_id == "legacy"))
    }


@contextmanager
def conversation_lock(key):
    with engine().connect() as connection:
        acquired = connection.scalar(
            text("SELECT pg_try_advisory_lock(hashtextextended(:key, 1))"), {"key": key}
        )
        connection.commit()
        try:
            yield acquired
        finally:
            if acquired:
                connection.execute(
                    text("SELECT pg_advisory_unlock(hashtextextended(:key, 1))"), {"key": key}
                )
                connection.commit()


class Worker:
    def __init__(self, config=None, generate=None, send=None):
        self.config = config or settings()
        self.generate = generate or self.generate_decision
        self.send = send or self.send_chatwoot

    def execute_tool(self, key, latest_text, name, args, *, version=None):
        with session() as db, db.begin():
            conversation = db.get(Conversation, key)
            if name in {"product_search", "product_lookup"}:
                from salma.product_tools import lookup, search

                if (
                    not conversation
                    or version is None
                    or version.business_id != conversation.business_id
                ):
                    raise ValueError("Published workspace context is required")
                if name == "product_lookup":
                    return lookup(db, conversation.business_id, version, args["sku"])
                return search(
                    db,
                    conversation.business_id,
                    version,
                    args["query"],
                    vector=self.config.agent_mode == "groq",
                )
            if version is not None:
                from salma.workspace_runtime import snapshot_catalog

                active_catalog = snapshot_catalog(version)
            else:
                active_catalog = catalog(db)
            if name == "service_lookup":
                from salma.workspace_runtime import lookup_service

                return lookup_service(active_catalog, **args)
            if name == "calculator":
                return {
                    "total": calculate(args["service_id"], args["quantity"], active_catalog),
                    "currency": active_catalog[args["service_id"]]["currency"],
                }
            if name == "knowledge_search":
                if version is not None:
                    from salma.workspace_runtime import knowledge_search

                    return knowledge_search(
                        db,
                        conversation.business_id,
                        version,
                        args["query"],
                        vector=self.config.agent_mode == "groq",
                    )
                from salma.knowledge import search

                return search(
                    db, args["query"], args["language"], vector=self.config.agent_mode == "groq"
                )
            if name == "current_customer":
                return db.get(Customer, conversation.customer_key).profile
            conversation = db.scalar(
                select(Conversation)
                .where(Conversation.key == key)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if conversation.ownership != "AI_ACTIVE":
                raise ValueError("Human ownership prevents mutations")
            if version is not None:
                from salma.platform_models import Business

                business = db.get(Business, conversation.business_id)
                if business.published_version != version.version or conversation.opted_out:
                    raise ValueError("Configuration changed or customer opted out")
            if name == "update_customer":
                return update_customer(db, conversation.customer_key, args)
            if name == "update_lead_request":
                if args.get("service_id") and args["service_id"] not in active_catalog:
                    raise ValueError("Unknown service")
                return update_draft(db, key, args["requirements"], args.get("service_id"))
            if name == "archive_lead_request":
                return archive_draft(db, key, latest_text)
            raise ValueError("Unknown tool")

    def generate_decision(self, message, conversation, history):
        if conversation.business_id != "legacy":
            from salma.workspace_runtime import generate

            return generate(self, message, conversation, history)
        if self.config.agent_mode == "simulator":
            return {
                "decision": simulated_decision(message.text, message.content_type),
                "trace": [],
                "mode": "simulator",
                "usage": [],
            }
        completion = GroqCompletion(self.config)
        result = Agent(
            completion,
            lambda name, args: self.execute_tool(conversation.key, message.text, name, args),
            self.config.max_tool_rounds,
        ).run(message.text, message.content_type, history)
        return {
            "decision": result["decision"],
            "trace": result["trace"],
            "mode": "groq",
            "usage": completion.usage,
        }

    def process_conversation(self, key):
        return self._observed_conversation(key, delivery=False)

    def _observed_conversation(self, key, *, delivery):
        with session() as db:
            query = select(Message).where(
                Message.conversation_key == key, Message.direction == "incoming"
            )
            parent = None
            if delivery:
                item = db.scalar(
                    select(Outbox)
                    .where(
                        Outbox.conversation_key == key, Outbox.status.in_(["pending", "sending"])
                    )
                    .order_by(Outbox.id)
                    .limit(1)
                )
                if not item:
                    return False
                query = query.where(Message.id == item.message_id)
            else:
                query = query.where(Message.status == "pending")
            message = db.scalar(query.order_by(Message.id).limit(1))
            if not message:
                return False
            metadata = {
                "message_id": message.id,
                "business_id": message.business_id,
                "correlation_id": message.correlation_id,
                "config_version": message.config_version,
            }
            if delivery:
                previous = db.scalar(
                    select(Event.data)
                    .where(
                        Event.conversation_key == key,
                        Event.correlation_id == message.correlation_id,
                        Event.kind.in_(["flow_completed", "decision_validated"]),
                    )
                    .order_by(Event.id.desc())
                    .limit(1)
                )
                previous = previous or {}
                parent = previous.get("trace_parent") or previous.get("diagnostics", {}).get(
                    "trace_parent"
                )
        with flow(metadata, name="salma.delivery" if delivery else "salma.message", parent=parent):
            with phase("delivery" if delivery else "message_processing") as step:
                handled = (self._deliver_conversation if delivery else self._process_conversation)(
                    key, expected_message_id=message.id
                )
                step["handled"] = handled
            if handled:
                try:
                    with session() as db:
                        event(
                            db,
                            key,
                            "delivery_flow_completed" if delivery else "flow_completed",
                            diagnostics(),
                            message.correlation_id,
                        )
                        db.commit()
                except Exception as exc:
                    emit("diagnostics_persistence_failed", error=type(exc).__name__)
            return handled

    def _process_conversation(self, key, *, expected_message_id=None):
        with conversation_lock(key) as acquired:
            if not acquired:
                return False
            with session() as db:
                conversation = db.get(Conversation, key)
                message = db.scalar(
                    select(Message)
                    .where(
                        Message.conversation_key == key,
                        Message.direction == "incoming",
                        Message.status == "pending",
                    )
                    .order_by(Message.id)
                    .limit(1)
                )
                if not message:
                    return False
                if expected_message_id is not None and message.id != expected_message_id:
                    return False
                if conversation.business_id != "legacy":
                    from salma.platform_models import Business

                    business = db.get(Business, conversation.business_id)
                    if (message.decision or {}).get("control") == "optout":
                        acknowledge = message.decision.get("acknowledge", False)
                        message.status = "processed" if acknowledge else "suppressed"
                        if acknowledge:
                            db.add(
                                Outbox(
                                    business_id=conversation.business_id,
                                    config_version=business.published_version if business else None,
                                    message_id=message.id,
                                    conversation_key=key,
                                    kind="optout",
                                    text="تم إيقاف الرسائل التلقائية. ابعت ابدأ لو حبيت ترجع."
                                    if re.search(r"[\u0600-\u06ff]", message.text)
                                    else "Automated messages stopped. Send START to opt in again.",
                                )
                            )
                        db.commit()
                        return True
                    if (
                        not business
                        or not business.published_version
                        or message.config_version != business.published_version
                    ):
                        message.status = "suppressed"
                        event(
                            db,
                            key,
                            "reply_suppressed",
                            {"reason": "configuration_changed"},
                            message.correlation_id,
                        )
                        db.commit()
                        return True
                    if conversation.opted_out:
                        message.status = "suppressed"
                        db.commit()
                        return True
                if conversation.ownership != "AI_ACTIVE":
                    message.status = "suppressed"
                    event(
                        db,
                        key,
                        "reply_suppressed",
                        {"reason": "human_ownership"},
                        message.correlation_id,
                    )
                    db.commit()
                    return True
                # Do not generate another reply until prior outbound work has been dispatched.
                if db.scalar(
                    select(Outbox.id).where(
                        Outbox.conversation_key == key, Outbox.status.in_(["pending", "sending"])
                    )
                ):
                    return False
                previous = db.scalars(
                    select(Message)
                    .where(
                        Message.conversation_key == key,
                        Message.business_id == conversation.business_id,
                        Message.id != message.id,
                        or_(Message.direction == "outgoing", Message.id < message.id),
                        Message.status.in_(["processed", "accepted", "delivered", "simulated"]),
                    )
                    .order_by(Message.id.desc())
                    .limit(20)
                ).all()
                history = [
                    {
                        "role": "user" if row.direction == "incoming" else "assistant",
                        "content": row.text,
                    }
                    for row in reversed(previous)
                ]
                if conversation.business_id != "legacy":
                    try:
                        from salma.auth import redis_client

                        redis_client().setex(
                            f"salma:context:{conversation.business_id}:{key}:{message.config_version}",
                            86400,
                            json.dumps(history),
                        )
                    except Exception:
                        pass  # Durable history and ownership remain in PostgreSQL.
                event(db, key, "graph_started", {"message_id": message.id}, message.correlation_id)
                db.commit()
            started = time.monotonic()
            try:
                with phase("generation") as step:
                    result = self.generate(message, conversation, history)
                    step["decision"] = result["decision"].model_dump()
                decision = result["decision"]
            except Exception as exc:
                emit("generation_failed", error=type(exc).__name__)
                # Provider details can contain secrets or customer content; persist only class.
                result = {"trace": [], "error": type(exc).__name__, "usage": []}
                decision = Decision(
                    intent="failure",
                    language=simulated_decision(message.text, message.content_type).language,
                    reply_kind="handoff",
                    handoff_reason="agent_failure",
                )
            with session() as db:
                current = db.scalar(
                    select(Conversation)
                    .where(Conversation.key == key)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
                stored = db.get(Message, message.id)
                stale = False
                if current.business_id != "legacy":
                    from salma.platform_models import Business

                    stale = (
                        db.get(Business, current.business_id).published_version
                        != stored.config_version
                    )
                if (
                    current.ownership != "AI_ACTIVE"
                    or stored.status != "pending"
                    or stale
                    or current.opted_out
                ):
                    stored.status = "suppressed"
                    event(
                        db,
                        key,
                        "reply_suppressed",
                        {
                            "reason": "configuration_changed"
                            if stale
                            else "takeover_during_generation"
                        },
                        message.correlation_id,
                    )
                    db.commit()
                    return True
                with phase("reply_rendering") as step:
                    evidence = {
                        row.id: row.text
                        for row in db.scalars(
                            select(Knowledge).where(
                                Knowledge.id.in_(decision.evidence_ids),
                                Knowledge.business_id == current.business_id,
                            )
                        )
                    }
                    if current.business_id != "legacy":
                        from salma.workspace_runtime import (
                            conversational_reply,
                            published_profile_evidence,
                            render_workspace,
                            snapshot,
                            snapshot_catalog,
                        )

                        version = snapshot(db, current.business_id, stored.config_version)
                        allowed = set(version.document_ids)
                        evidence = {
                            row.id: row.text
                            for row in db.scalars(
                                select(Knowledge).where(
                                    Knowledge.id.in_(decision.evidence_ids),
                                    Knowledge.business_id == current.business_id,
                                    Knowledge.document_id.in_(allowed),
                                )
                            )
                        }
                        evidence.update(
                            {
                                row["id"]: row["text"]
                                for row in published_profile_evidence(current.business_id, version)
                                if row["id"] in decision.evidence_ids
                            }
                        )
                        reply = render_workspace(
                            decision,
                            snapshot_catalog(version),
                            evidence,
                            version.config,
                            service_details=result.get("service_details", {}),
                        )
                        if decision.reply_kind == "product":
                            from salma.product_tools import (
                                lookup,
                                needs_variant_choice,
                                render_products,
                            )

                            products = {}
                            for sku in decision.product_skus:
                                try:
                                    row = lookup(db, current.business_id, version, sku)
                                    products[sku.casefold()] = row
                                except LookupError:
                                    pass
                            if needs_variant_choice(
                                db,
                                current.business_id,
                                version,
                                message.text,
                                list(products.values()),
                            ):
                                decision = Decision(
                                    intent="variant_choice",
                                    language=decision.language,
                                    reply_kind="clarify",
                                    question_kind="variant",
                                )
                                reply = render_workspace(decision, {}, {}, version.config)
                            else:
                                reply = conversational_reply(
                                    decision, render_products(decision, products)
                                )
                    else:
                        reply = render(decision, catalog(db), evidence)
                    step.update(text=reply.text, handoff=reply.handoff, reason=reply.reason)
                signals = set(current.lead.get("signals", [])) | set(decision.lead_signals)
                points, temperature = score(
                    signals, self.config.lead_weights, self.config.lead_thresholds
                )
                current.lead = {
                    **current.lead,
                    "signals": sorted(signals),
                    "score": points,
                    "temperature": temperature,
                    "service_interest": decision.service_ids
                    or current.lead.get("service_interest", []),
                }
                stored.decision = decision.model_dump()
                stored.status = "processed"
                if reply.handoff:
                    current.ownership = "HANDOFF_PENDING"
                    customer = db.scalar(
                        select(Customer).where(
                            Customer.key == current.customer_key,
                            Customer.business_id == current.business_id,
                        )
                    )
                    profile = (customer.profile or {}) if customer else {}
                    customer_details = {
                        str(field)[:100]: str(value)[:500]
                        for field, value in list(profile.items())[:20]
                    }
                    lead_details = {}
                    for field, value in list(current.lead.items())[:20]:
                        if isinstance(value, str):
                            value = value[: 1000 if field == "requirements" else 500]
                        elif isinstance(value, list):
                            value = [str(item)[:500] for item in value[:20]]
                        elif isinstance(value, dict):
                            value = str(value)[:500]
                        lead_details[str(field)[:100]] = value
                    current.handoff = {
                        "reason": reply.reason,
                        "summary": message.text[:1000],
                        "requirements": str(
                            current.lead.get("requirements") or profile.get("requirements") or ""
                        )[:1000],
                        "customer_details": customer_details,
                        "lead_details": lead_details,
                        "context": [
                            {"role": row["role"], "content": row["content"][:500]}
                            for row in history[-6:]
                        ]
                        + [{"role": "user", "content": message.text[:1000]}],
                        "service_interest": current.lead.get("service_interest", []),
                        "temperature": temperature,
                        "suggested_reply": "Please review the customer’s requirements "
                        + (
                            "and follow up from the connected WhatsApp phone."
                            if current.provider == "evolution"
                            else "and follow up in Chatwoot."
                        ),
                        "rule_id": decision.rule_id,
                        "destination": next(
                            (
                                rule.get("destination", "staff")
                                for rule in (
                                    version.config.get("handoff_rules", [])
                                    if current.business_id != "legacy"
                                    else []
                                )
                                if decision.rule_id in {rule.get("id"), rule.get("name")}
                            ),
                            "staff",
                        ),
                    }
                    event(db, key, "handoff_created", current.handoff, message.correlation_id)
                db.add(
                    Outbox(
                        business_id=current.business_id,
                        config_version=stored.config_version,
                        message_id=message.id,
                        conversation_key=key,
                        text=reply.text,
                        kind="handoff" if reply.handoff else "reply",
                    )
                )
                event(
                    db,
                    key,
                    "decision_validated",
                    {
                        "decision": stored.decision,
                        "tools": result.get("trace", []),
                        "guardrail": reply.reason,
                        "duration_ms": round((time.monotonic() - started) * 1000),
                        "mode": result.get("mode", self.config.agent_mode),
                        "usage": result.get("usage", []),
                        "error": result.get("error"),
                        "diagnostics": diagnostics(),
                    },
                    message.correlation_id,
                )
                db.commit()
                return True

    def send_chatwoot(self, conversation, item):
        if conversation.provider == "evolution":
            from salma.evolution_channel import send_evolution

            return send_evolution(conversation, item, self.config)
        channel = None
        if conversation.business_id != "legacy":
            from salma.platform_models import Channel
            from salma.workspace_services import get_channel_credentials

            with session() as db:
                channel = db.scalar(
                    select(Channel).where(
                        Channel.business_id == conversation.business_id,
                        Channel.account_id == conversation.account_id,
                        Channel.inbox_id == conversation.inbox_id,
                    )
                )
                if not channel or not channel.live_enabled:
                    raise ValueError("Channel is paused")
                db.expunge(channel)
            token = get_channel_credentials(channel)["api_token"]
        else:
            token = self.config.chatwoot_api_token.get_secret_value()
        if not token:
            raise ValueError("Chatwoot token is not configured")
        with httpx.Client(timeout=20, verify=ssl.create_default_context()) as client:
            response = client.post(
                f"{self.config.chatwoot_url}/api/v1/accounts/{conversation.account_id}"
                f"/conversations/{conversation.external_id}/messages",
                headers={"api_access_token": token},
                json={
                    "content": item.text,
                    "message_type": "outgoing",
                    "private": False,
                    "content_attributes": {"salma_outbox_id": item.id},
                },
            )
            response.raise_for_status()
            result = response.json()
        if channel and item.kind == "handoff" and result.get("id"):
            from salma.workspace_services import notify_handoff

            summary = json.dumps(conversation.handoff or {}, ensure_ascii=False)
            result["handoff_notification"] = notify_handoff(
                channel,
                conversation,
                summary,
                (conversation.handoff or {}).get("destination", "staff"),
            )
        return result

    def deliver_conversation(self, key):
        return self._observed_conversation(key, delivery=True)

    def _deliver_conversation(self, key, *, expected_message_id=None):
        with conversation_lock(key) as acquired:
            if not acquired:
                return False
            with session() as db:
                # Publication and dispatch lock the business before touching outbound rows.
                # A published change cannot race between this check and the provider call.
                owner = db.get(Conversation, key)
                active_version = None
                if owner and owner.business_id != "legacy":
                    from salma.platform_models import Business

                    active_version = db.scalar(
                        select(Business)
                        .where(Business.id == owner.business_id)
                        .with_for_update()
                        .execution_options(populate_existing=True)
                    ).published_version
                item = db.scalar(
                    select(Outbox)
                    .where(
                        Outbox.conversation_key == key, Outbox.status.in_(["pending", "sending"])
                    )
                    .order_by(Outbox.id)
                    .limit(1)
                )
                if not item:
                    return False
                if expected_message_id is not None and item.message_id != expected_message_id:
                    return False
                correlation = db.scalar(
                    select(Message.correlation_id).where(Message.id == item.message_id)
                )
                if item.status == "sending":
                    item.status = "ambiguous"
                    item.error = "Worker restarted after dispatch began; reconciliation required"
                    conversation = db.scalar(
                        select(Conversation)
                        .where(Conversation.key == key)
                        .with_for_update()
                        .execution_options(populate_existing=True)
                    )
                    conversation.ownership = "HANDOFF_PENDING"
                    conversation.handoff = {
                        **(conversation.handoff or {}),
                        "reason": "delivery_reconciliation",
                        "outbox_id": item.id,
                    }
                    event(db, key, "delivery_ambiguous", {"outbox_id": item.id}, correlation)
                    db.commit()
                    return True
                if owner.business_id != "legacy" and item.config_version != active_version:
                    item.status = "suppressed"
                    item.error = "Configuration changed before dispatch"
                    db.commit()
                    return True
                conversation = db.scalar(
                    select(Conversation)
                    .where(Conversation.key == key)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
                allowed = item.kind == "optout" or (
                    not conversation.opted_out
                    and (
                        conversation.ownership == "AI_ACTIVE"
                        or (conversation.ownership == "HANDOFF_PENDING" and item.kind == "handoff")
                    )
                )
                if not allowed:
                    item.status = "suppressed"
                    db.commit()
                    return True
                live_allowed = self.config.live_enabled
                if conversation.business_id != "legacy":
                    from salma.evolution_channel import channel_condition, route_enabled
                    from salma.platform_models import Channel

                    live_allowed = route_enabled(
                        db.scalar(select(Channel).where(*channel_condition(conversation))),
                        conversation,
                    )
                if conversation.provider in {"chatwoot", "evolution"} and not live_allowed:
                    item.status = "blocked"
                    item.error = "Live sending is disabled"
                    db.commit()
                    return True
                item.status = "sending"
                db.commit()  # Durable dispatch intent precedes the network operation.
                if owner.business_id != "legacy":
                    active_version = db.scalar(
                        select(Business)
                        .where(Business.id == owner.business_id)
                        .with_for_update()
                        .execution_options(populate_existing=True)
                    ).published_version
                conversation = db.scalar(
                    select(Conversation)
                    .where(Conversation.key == key)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
                item = db.scalar(
                    select(Outbox)
                    .where(Outbox.id == item.id)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
                if owner.business_id != "legacy" and conversation.provider in {
                    "chatwoot",
                    "evolution",
                }:
                    from salma.evolution_channel import channel_condition, route_enabled
                    from salma.platform_models import Channel

                    channel = db.scalar(
                        select(Channel)
                        .where(
                            *channel_condition(conversation),
                        )
                        .with_for_update()
                        .execution_options(populate_existing=True)
                    )
                    if not route_enabled(channel, conversation):
                        item.status = "blocked"
                        item.error = "Channel paused before dispatch"
                        db.commit()
                        return True
                if item.status != "sending":
                    db.commit()
                    return True
                if owner.business_id != "legacy" and item.config_version != active_version:
                    item.status = "suppressed"
                    item.error = "Configuration changed before dispatch"
                    db.commit()
                    return True
                allowed = item.kind == "optout" or (
                    not conversation.opted_out
                    and (
                        conversation.ownership == "AI_ACTIVE"
                        or (conversation.ownership == "HANDOFF_PENDING" and item.kind == "handoff")
                    )
                )
                if not allowed:
                    item.status = "suppressed"
                    db.commit()
                    return True
                try:
                    if conversation.provider == "simulator":
                        item.status = "simulated"
                    else:
                        result = self.send(conversation, item)
                        if not result.get("id"):
                            raise ValueError("Provider did not return a message identity")
                        item.provider_id = str(result["id"])
                        item.status = (
                            "delivered"
                            if result.get("status") in ("delivered", "read")
                            else "accepted"
                        )
                        if result.get("handoff_notification"):
                            event(
                                db,
                                key,
                                "handoff_notification",
                                result["handoff_notification"],
                                correlation,
                            )
                    record_outgoing(db, item)
                except httpx.HTTPStatusError as exc:
                    item.status = "failed" if 400 <= exc.response.status_code < 500 else "ambiguous"
                    item.error = f"HTTP {exc.response.status_code}; staff reconciliation required"
                except Exception as exc:
                    item.status = "ambiguous"
                    item.error = f"{type(exc).__name__}; staff reconciliation required"
                event(
                    db,
                    key,
                    "delivery_state",
                    {
                        "outbox_id": item.id,
                        "status": item.status,
                        "provider_id": item.provider_id,
                        "error": item.error,
                    },
                    correlation,
                )
                if item.status in ("failed", "ambiguous"):
                    conversation.ownership = "HANDOFF_PENDING"
                    conversation.handoff = {
                        **(conversation.handoff or {}),
                        "reason": "delivery_reconciliation",
                        "outbox_id": item.id,
                    }
                db.commit()
                return True

    def tick(self):
        with session() as db:
            keys = set(
                db.scalars(select(Message.conversation_key).where(Message.status == "pending"))
            )
            keys.update(
                db.scalars(
                    select(Outbox.conversation_key).where(Outbox.status.in_(["pending", "sending"]))
                )
            )
        count = 0
        for key in sorted(keys):
            count += self.deliver_conversation(key)
            count += self.process_conversation(key)
            count += self.deliver_conversation(key)
        from salma.workspace_services import process_document_job

        count += process_document_job()
        from salma.product_imports import process_product_import_job

        count += process_product_import_job()
        if self.config.agent_mode == "groq":
            from salma.product_tools import process_product_index_job

            try:
                count += process_product_index_job()
            except Exception:
                logger.warning(
                    "Product semantic indexing unavailable; keyword search remains available"
                )
        return count


def main():
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    from threading import Event as StopEvent
    from threading import Thread

    from salma.monitoring import heartbeat

    stop_heartbeat = StopEvent()

    def pulse():
        while not stop_heartbeat.is_set():
            heartbeat()
            stop_heartbeat.wait(5)

    Thread(target=pulse, name="salma-heartbeat", daemon=True).start()
    worker = Worker()
    while True:
        try:
            if not worker.tick():
                time.sleep(0.5)
            heartbeat(completed=True)
        except SQLAlchemyError:
            logger.error("Database unavailable; retrying without acknowledging pending work")
            time.sleep(3)


if __name__ == "__main__":
    main()
