"""End-to-end local Docker smoke test. Credentials are read, never printed."""

import time
from uuid import uuid4

import httpx
from salma.settings import settings

token = settings().staff_token.get_secret_value()
headers = {"Authorization": f"Bearer {token}"}
key = str(uuid4())
with httpx.Client(base_url="http://127.0.0.1:8000", headers=headers, timeout=10) as client:
    client.get("/health").raise_for_status()
    state = client.get("/api/status")
    state.raise_for_status()
    response = client.post(
        "/api/simulator/messages",
        json={
            "conversation_id": key,
            "customer_id": key,
            "message_id": str(uuid4()),
            "text": "What does a consultation cost?",
        },
    )
    response.raise_for_status()
    route = f"/api/conversations/{response.json()['conversation_key']}"
    for _ in range(90):
        response = client.get(route)
        response.raise_for_status()
        outgoing = [row for row in response.json()["messages"] if row["direction"] == "outgoing"]
        if outgoing:
            assert "500 EGP" in outgoing[-1]["text"]
            assert outgoing[-1]["status"] == "simulated"
            break
        time.sleep(1)
    else:
        raise SystemExit("Worker reply timed out")
    client.post(route + "/ownership", json={"action": "takeover"}).raise_for_status()
    client.post(
        "/api/simulator/messages",
        json={
            "conversation_id": key,
            "customer_id": key,
            "message_id": str(uuid4()),
            "text": "hello",
        },
    ).raise_for_status()
    for _ in range(20):
        detail = client.get(route).json()
        incoming = [row for row in detail["messages"] if row["direction"] == "incoming"]
        if incoming[-1]["status"] == "suppressed":
            break
        time.sleep(0.5)
    assert detail["conversation"]["ownership"] == "HUMAN_ACTIVE"
    assert len([row for row in detail["messages"] if row["direction"] == "outgoing"]) == 1
print("Docker API -> durable worker -> verified price -> takeover suppression: passed")
print("Agent mode:", state.json()["agent_mode"], "| Live channels:", state.json()["live_enabled"])
