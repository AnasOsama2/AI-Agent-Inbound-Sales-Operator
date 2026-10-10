"""Use an isolated PostgreSQL database, never the running development worker's queue."""

import os

target = os.environ.get("TEST_DATABASE_URL")
if target:
    os.environ["AGENT_MODE"] = "simulator"
    os.environ["LIVE_ENABLED"] = "false"
    os.environ["DATABASE_URL"] = (
        "postgresql+psycopg://salma:salma_local@127.0.0.1:5433/salma_test?connect_timeout=5"
        if target == "local"
        else target
    )
