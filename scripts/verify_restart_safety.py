"""Local acceptance probe: temporary context loss and worker/Redis restarts."""

import json
import subprocess
import time
from pathlib import Path
from uuid import uuid4

import httpx
from redis import Redis

root = Path(__file__).resolve().parents[1]
access = json.loads((root / "data/local-access.json").read_text(encoding="utf-8"))
owner = access["owners"]["clinic"]
bid = owner["business_id"]
with httpx.Client(base_url="http://localhost:8000", timeout=30) as client:
    response = client.post("/api/auth/login", json={k: owner[k] for k in ("email", "password")})
    response.raise_for_status()
    client.headers["X-CSRF-Token"] = response.json()["csrf_token"]
    base = f"/api/businesses/{bid}"
    key = "restart-acceptance-" + uuid4().hex
    response = client.post(
        base + "/simulator/messages",
        json={
            "conversation_id": key,
            "customer_id": key,
            "text": "I want a human",
        },
    )
    response.raise_for_status()
    conversation = response.json()["conversation_key"]
    path = base + "/conversations/" + conversation
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        before = client.get(path).json()
        if before["conversation"]["ownership"] == "HANDOFF_PENDING":
            break
        time.sleep(0.5)
    assert before["conversation"]["ownership"] == "HANDOFF_PENDING"
    draft_before = client.get(base + "/draft").json()
    redis = Redis(host="127.0.0.1", port=6380)
    for cache_key in redis.scan_iter(match=f"salma:context:{bid}:*"):
        redis.delete(cache_key)
    subprocess.run(["docker", "compose", "restart", "redis", "worker"], cwd=root, check=True)
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        response = client.get(path)
        if response.is_success:
            break
        time.sleep(1)
    response.raise_for_status()
    after = response.json()
    assert after["conversation"]["ownership"] == "HANDOFF_PENDING"
    assert after["messages"] == before["messages"]
    assert client.get(base + "/draft").json() == draft_before
    print(
        "PASS: Redis context loss and Redis/worker restarts preserve "
        "published configuration, history and human ownership."
    )
