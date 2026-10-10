import re
from uuid import uuid4

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert

from salma.adapters import Inbound
from salma.db import Conversation, Customer, Message, Outbox, event


def parse_optout_control(value):
    if re.fullmatch(
        r"\s*(stop|unsubscribe|الغاء|إلغاء|توقف|وقف|مش عايز رسايل|لا ترسل رسائل)\s*[.!؟]*",
        value,
        re.I,
    ):
        return "optout"
    if re.fullmatch(r"\s*(start|subscribe|ابدأ|ابدا|اشترك)\s*[.!؟]*", value, re.I):
        return "optin"
    return None


def accept(db, incoming: Inbound) -> tuple[int, bool]:
    # Transaction-level lock protects concurrent conversation creation and identity checks.
    db.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": incoming.conversation_key},
    )
    conversation = db.scalar(
        select(Conversation)
        .where(Conversation.key == incoming.conversation_key)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if conversation and conversation.customer_key != incoming.customer_key:
        db.rollback()
        raise ValueError("Conversation customer identity cannot change")
    if not conversation:
        db.execute(
            insert(Customer)
            .values(key=incoming.customer_key, profile={}, business_id=incoming.business_id)
            .on_conflict_do_nothing()
        )
        conversation = Conversation(
            business_id=incoming.business_id,
            config_version=incoming.config_version,
            key=incoming.conversation_key,
            customer_key=incoming.customer_key,
            provider=incoming.provider,
            channel=incoming.channel,
            account_id=incoming.account_id,
            inbox_id=incoming.inbox_id,
            external_id=incoming.external_conversation_id,
        )
        db.add(conversation)
        db.flush()
    correlation = str(uuid4())
    message_id = db.scalar(
        insert(Message)
        .values(
            business_id=incoming.business_id,
            config_version=incoming.config_version,
            conversation_key=conversation.key,
            external_id=incoming.external_message_id,
            direction="incoming",
            content_type=incoming.content_type,
            text=incoming.text,
            timestamp=incoming.timestamp,
            status="pending",
            correlation_id=correlation,
        )
        .on_conflict_do_nothing()
        .returning(Message.id)
    )
    created = message_id is not None
    if created:
        control = (
            parse_optout_control(incoming.text)
            if incoming.business_id != "legacy" and incoming.content_type == "text"
            else None
        )
        if control:
            before = conversation.opted_out
            conversation.opted_out = control == "optout"
            stored = db.get(Message, message_id)
            stored.decision = {
                "control": control,
                "acknowledge": control == "optout" and not before,
            }
            if control == "optout":
                for item in db.scalars(
                    select(Outbox).where(
                        Outbox.conversation_key == conversation.key,
                        Outbox.status == "pending",
                        Outbox.kind != "optout",
                    )
                ):
                    item.status = "suppressed"
                    item.error = "Customer opted out before dispatch"
            event(
                db,
                conversation.key,
                "consent_changed",
                {"control": control, "before": before, "opted_out": conversation.opted_out},
                correlation,
            )
        event(db, conversation.key, "inbound_persisted", {"message_id": message_id}, correlation)
    else:
        message_id = db.scalar(
            select(Message.id).where(
                Message.conversation_key == conversation.key,
                Message.external_id == incoming.external_message_id,
                Message.direction == "incoming",
            )
        )
    db.commit()
    return message_id, created


def ownership(db, key: str, action: str):
    conversation = db.scalar(select(Conversation).where(Conversation.key == key).with_for_update())
    if not conversation:
        raise LookupError("Conversation not found")
    states = {"takeover": "HUMAN_ACTIVE", "close": "CLOSED", "resume": "AI_ACTIVE"}
    if action not in states:
        raise ValueError("Unknown ownership action")
    before = conversation.ownership
    conversation.ownership = states[action]
    if action == "resume":
        conversation.handoff = None
        # Resume is forward-only: backlog accumulated during human ownership is not replayed.
        for message in db.scalars(
            select(Message).where(Message.conversation_key == key, Message.status == "pending")
        ):
            message.status = "suppressed"
    for item in db.scalars(
        select(Outbox).where(Outbox.conversation_key == key, Outbox.status == "pending")
    ):
        item.status = "suppressed"
    event(
        db,
        key,
        "ownership_changed",
        {"before": before, "after": conversation.ownership},
        str(uuid4()),
    )
    db.commit()
    return conversation


def update_customer(db, customer_key: str, changes: dict):
    allowed = {"name", "company", "email", "phone", "requirements", "budget", "timeline"}
    if (
        not changes
        or not changes.keys() <= allowed
        or any(not isinstance(value, str) or len(value) > 2000 for value in changes.values())
    ):
        raise ValueError("Only permitted customer profile fields can be updated")
    customer = db.scalar(select(Customer).where(Customer.key == customer_key).with_for_update())
    if not customer:
        raise LookupError("Customer not found")
    customer.profile = {**customer.profile, **changes}
    db.flush()
    return customer.profile


def update_draft(db, conversation_key: str, requirements: str, service_id: str | None):
    conversation = db.scalar(
        select(Conversation).where(Conversation.key == conversation_key).with_for_update()
    )
    lead = dict(conversation.lead)
    lead.update(requirements=requirements[:4000], service_id=service_id, archived=False)
    conversation.lead = lead
    db.flush()
    return lead


def archive_draft(db, conversation_key: str, latest_customer_text: str):
    if not re.fullmatch(
        r"\s*(confirm archive|yes archive|أكد الأرشفة|اكد الارشفة)\s*[.!؟]?\s*",
        latest_customer_text,
        re.I,
    ):
        raise ValueError("Ask the customer to reply 'confirm archive' or 'أكد الأرشفة'")
    conversation = db.scalar(
        select(Conversation).where(Conversation.key == conversation_key).with_for_update()
    )
    conversation.lead = {**conversation.lead, "archived": True}
    db.flush()
    return {"archived": True}
