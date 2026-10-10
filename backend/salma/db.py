from datetime import datetime, timezone
from decimal import Decimal
from functools import lru_cache

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    select,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker
from sqlalchemy.types import UserDefinedType

from salma.settings import settings


def now():
    return datetime.now(timezone.utc)


class Vector(UserDefinedType):
    cache_ok = True

    def get_col_spec(self, **kw):
        return "vector(384)"

    def bind_processor(self, dialect):
        return lambda value: None if value is None else str([float(v) for v in value])

    def result_processor(self, dialect, coltype):
        def process(value):
            if value is None:
                return None
            if isinstance(value, str):
                return [float(part) for part in value.strip("[]").split(",")]
            return [float(part) for part in value]

        return process


class Base(DeclarativeBase):
    pass


class Customer(Base):
    __tablename__ = "customers"
    key: Mapped[str] = mapped_column(String(400), primary_key=True)
    profile: Mapped[dict] = mapped_column(JSON, default=dict)
    business_id: Mapped[str] = mapped_column(String(36), default="legacy", index=True)


class Conversation(Base):
    __tablename__ = "conversations"
    key: Mapped[str] = mapped_column(String(400), primary_key=True)
    customer_key: Mapped[str] = mapped_column(ForeignKey("customers.key"))
    provider: Mapped[str] = mapped_column(String(20))
    channel: Mapped[str] = mapped_column(String(30))
    account_id: Mapped[str] = mapped_column(String(50))
    inbox_id: Mapped[str] = mapped_column(String(50))
    external_id: Mapped[str] = mapped_column(String(100))
    ownership: Mapped[str] = mapped_column(String(30), default="AI_ACTIVE")
    lead: Mapped[dict] = mapped_column(JSON, default=dict)
    handoff: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    business_id: Mapped[str] = mapped_column(String(36), default="legacy", index=True)
    config_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    opted_out: Mapped[bool] = mapped_column(default=False)


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (UniqueConstraint("conversation_key", "external_id", "direction"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    conversation_key: Mapped[str] = mapped_column(ForeignKey("conversations.key"), index=True)
    external_id: Mapped[str] = mapped_column(String(100))
    direction: Mapped[str] = mapped_column(String(20))
    content_type: Mapped[str] = mapped_column(String(30), default="text")
    text: Mapped[str] = mapped_column(Text)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    status: Mapped[str] = mapped_column(String(30), default="pending", index=True)
    correlation_id: Mapped[str] = mapped_column(String(36))
    decision: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    business_id: Mapped[str] = mapped_column(String(36), default="legacy", index=True)
    config_version: Mapped[int | None] = mapped_column(Integer, nullable=True)


class Outbox(Base):
    __tablename__ = "outbox"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    message_id: Mapped[int] = mapped_column(ForeignKey("messages.id"), unique=True)
    conversation_key: Mapped[str] = mapped_column(ForeignKey("conversations.key"), index=True)
    text: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(20), default="reply")
    status: Mapped[str] = mapped_column(String(30), default="pending")
    provider_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    business_id: Mapped[str] = mapped_column(String(36), default="legacy", index=True)
    config_version: Mapped[int | None] = mapped_column(Integer, nullable=True)


class Event(Base):
    __tablename__ = "events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    conversation_key: Mapped[str] = mapped_column(ForeignKey("conversations.key"), index=True)
    kind: Mapped[str] = mapped_column(String(50))
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    correlation_id: Mapped[str] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    business_id: Mapped[str] = mapped_column(String(36), default="legacy", index=True)


class Service(Base):
    __tablename__ = "services"
    id: Mapped[str] = mapped_column(String(50), primary_key=True)
    name_en: Mapped[str] = mapped_column(String(200))
    name_ar: Mapped[str] = mapped_column(String(200))
    price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    currency: Mapped[str] = mapped_column(String(10), default="EGP")
    business_id: Mapped[str] = mapped_column(String(36), default="legacy", index=True)


class Knowledge(Base):
    __tablename__ = "knowledge"
    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    document: Mapped[str] = mapped_column(String(200))
    page: Mapped[int] = mapped_column(Integer)
    language: Mapped[str] = mapped_column(String(10), index=True)
    model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    text: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list | None] = mapped_column(Vector(), nullable=True)
    business_id: Mapped[str] = mapped_column(String(36), default="legacy", index=True)
    document_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)


@lru_cache
def engine():
    return create_engine(settings().database_url, pool_pre_ping=True)


def session():
    return sessionmaker(engine(), expire_on_commit=False)()


def event(db, key, kind, data, correlation):
    conversation = db.get(Conversation, key)
    business_id = conversation.business_id if conversation else "legacy"
    source = None
    for field, model in (("outbox_id", Outbox), ("message_id", Message)):
        identifier = data.get(field)
        if isinstance(identifier, int) and not isinstance(identifier, bool):
            source = db.scalar(
                select(model).where(
                    model.id == identifier,
                    model.conversation_key == key,
                    model.business_id == business_id,
                )
            )
            if source is not None:
                break
    if source is None:
        source = db.scalar(
            select(Message)
            .where(
                Message.conversation_key == key,
                Message.business_id == business_id,
                Message.correlation_id == correlation,
            )
            .order_by(Message.id)
            .limit(1)
        )
    version = (
        source.config_version
        if source is not None
        else (conversation.config_version if conversation else None)
    )
    db.add(
        Event(
            conversation_key=key,
            business_id=business_id,
            kind=kind,
            data={**data, "business_id": business_id, "config_version": version},
            correlation_id=correlation,
        )
    )


def record_outgoing(db, item):
    row = db.scalar(
        select(Message).where(
            Message.conversation_key == item.conversation_key,
            Message.external_id == f"outbox:{item.id}",
            Message.direction == "outgoing",
        )
    )
    if row:
        if row.status != "delivered":
            row.status = item.status
    else:
        source = db.get(Message, item.message_id)
        db.add(
            Message(
                business_id=item.business_id,
                config_version=item.config_version,
                conversation_key=item.conversation_key,
                external_id=f"outbox:{item.id}",
                direction="outgoing",
                text=item.text,
                status=item.status,
                correlation_id=source.correlation_id,
            )
        )
