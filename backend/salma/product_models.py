"""Immutable, business-scoped product catalog snapshots."""

from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from salma.db import Base, Vector, now
from salma.platform_models import uid


class ProductRevision(Base):
    __tablename__ = "product_revisions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    business_id: Mapped[str] = mapped_column(ForeignKey("businesses.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ProductVariant(Base):
    __tablename__ = "product_variants"
    revision_id: Mapped[str] = mapped_column(ForeignKey("product_revisions.id"), primary_key=True)
    sku: Mapped[str] = mapped_column(String(200), primary_key=True)
    business_id: Mapped[str] = mapped_column(ForeignKey("businesses.id"), index=True)
    product_key: Mapped[str] = mapped_column(String(64), index=True)
    data: Mapped[dict] = mapped_column(JSON)
    embedding: Mapped[list | None] = mapped_column(Vector(), nullable=True)
