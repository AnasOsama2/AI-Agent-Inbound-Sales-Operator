from alembic import context
from salma.db import Base
from salma.settings import settings
from sqlalchemy import create_engine

engine = create_engine(settings().database_url)
with engine.connect() as connection:
    context.configure(connection=connection, target_metadata=Base.metadata)
    with context.begin_transaction():
        context.run_migrations()
