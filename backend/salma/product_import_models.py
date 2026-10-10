"""Durable owner-reviewed product import jobs; uploaded files remain private."""

from datetime import datetime
from uuid import uuid4

from sqlalchemy import JSON, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from salma.db import Base, now


class ProductImport(Base):
    __tablename__ = "product_imports"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    business_id: Mapped[str] = mapped_column(ForeignKey("businesses.id"), index=True)
    filename: Mapped[str] = mapped_column(String(255))
    storage_path: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(30), default="pending", index=True)
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    lease_token: Mapped[str | None] = mapped_column(String(36), nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    pending_stage: Mapped[str] = mapped_column(String(20), default="parse")
    preview_revision_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    applied_revision_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
