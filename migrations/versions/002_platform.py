"""Invite-only workspaces and immutable published business configuration."""

import sqlalchemy as sa
from alembic import op
from salma.db import Base
from salma.platform_models import Business
from sqlalchemy import inspect

revision = "002"
down_revision = "001"
branch_labels = None
depends_on = None


NEW_TABLES = (
    "businesses",
    "users",
    "memberships",
    "invites",
    "config_versions",
    "admin_messages",
    "documents",
    "channels",
)
OLD_TABLES = ("customers", "conversations", "messages", "outbox", "events", "services", "knowledge")


def upgrade():
    bind = op.get_bind()
    Base.metadata.create_all(bind, tables=[Base.metadata.tables[name] for name in NEW_TABLES])
    found = bind.execute(sa.select(Business.id).where(Business.id == "legacy")).first()
    if not found:
        bind.execute(
            sa.insert(Business).values(
                id="legacy",
                name="Legacy demo (paused)",
                draft={
                    "name": "Legacy demo",
                    "catalog": [],
                    "document_ids": [],
                    "handoff_rules": [],
                },
                published_version=None,
            )
        )
    for table in OLD_TABLES:
        columns = {column["name"] for column in inspect(bind).get_columns(table)}
        if "business_id" not in columns:
            op.add_column(
                table,
                sa.Column("business_id", sa.String(36), nullable=False, server_default="legacy"),
            )
            op.create_index("ix_" + table + "_business_id", table, ["business_id"])
        if table in ("conversations", "messages", "outbox") and "config_version" not in columns:
            op.add_column(table, sa.Column("config_version", sa.Integer(), nullable=True))
        if table == "conversations" and "opted_out" not in columns:
            op.add_column(
                table,
                sa.Column("opted_out", sa.Boolean(), nullable=False, server_default=sa.false()),
            )
        if table == "knowledge" and "document_id" not in columns:
            op.add_column(table, sa.Column("document_id", sa.String(36), nullable=True))
            op.create_index("ix_knowledge_document_id", table, ["document_id"])
    for table, column in (
        ("customers", "key"),
        ("conversations", "key"),
        ("conversations", "customer_key"),
        ("messages", "conversation_key"),
        ("outbox", "conversation_key"),
        ("events", "conversation_key"),
    ):
        op.alter_column(table, column, type_=sa.String(400))
    # Existing records are never silently activated on a newly configured business.
    bind.execute(
        sa.text(
            "UPDATE outbox SET status='suppressed', error='Legacy migration: paused' "
            "WHERE business_id='legacy' AND status='pending'"
        )
    )


def downgrade():
    raise RuntimeError("Workspace migration is irreversible; restore a database backup")
