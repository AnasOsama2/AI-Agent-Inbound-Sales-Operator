"""Separate immutable product revisions from service configuration."""

import sqlalchemy as sa
from alembic import op

revision = "003"
down_revision = "002"
branch_labels = None
depends_on = None


def upgrade():
    from salma import product_import_models, product_models  # noqa: F401
    from salma.db import Base

    bind = op.get_bind()
    for table, column in (
        ("businesses", "draft_product_revision_id"),
        ("config_versions", "product_revision_id"),
    ):
        if column not in {item["name"] for item in sa.inspect(bind).get_columns(table)}:
            op.add_column(table, sa.Column(column, sa.String(36), nullable=True))
    tables = [
        table
        for name, table in Base.metadata.tables.items()
        if name in ("product_revisions", "product_variants", "product_imports")
    ]
    Base.metadata.create_all(bind, tables=tables)


def downgrade():
    for table in ("product_imports", "product_variants", "product_revisions"):
        op.drop_table(table)
    op.drop_column("config_versions", "product_revision_id")
    op.drop_column("businesses", "draft_product_revision_id")
