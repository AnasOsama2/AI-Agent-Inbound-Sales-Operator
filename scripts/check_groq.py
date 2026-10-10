"""Read model availability and run one simulator-only tool/decision probe."""

import ssl
from uuid import uuid4

import httpx
from salma.adapters import Inbound
from salma.db import Conversation, Message, session
from salma.settings import Settings
from salma.store import accept
from salma.worker import Worker

config = Settings(agent_mode="groq", live_enabled=False)
key = config.groq_api_key.get_secret_value()
if not key:
    raise SystemExit("Groq key not configured")
with httpx.Client(verify=ssl.create_default_context(), timeout=20) as client:
    response = client.get(f"{config.groq_url}/models", headers={"Authorization": f"Bearer {key}"})
    if response.status_code != 200:
        raise SystemExit(f"Groq availability check failed: HTTP {response.status_code}")
    if config.groq_model not in {row["id"] for row in response.json()["data"]}:
        raise SystemExit(
            f"Configured model unavailable: {config.groq_model}; no substitution attempted"
        )
print(f"Configured model available: {config.groq_model}")
identifier = str(uuid4())
incoming = Inbound(
    provider="simulator",
    channel="simulator",
    account_id="local",
    inbox_id="sim",
    external_message_id=str(uuid4()),
    external_conversation_id=identifier,
    external_customer_id=identifier,
    text="What does a consultation cost?",
)
with session() as db:
    message_id, _ = accept(db, incoming)
    message = db.get(Message, message_id)
    conversation = db.get(Conversation, incoming.conversation_key)
    db.expunge_all()
try:
    result = Worker(config).generate_decision(message, conversation, [])
except Exception as exc:
    raise SystemExit(f"Groq probe failed: {type(exc).__name__}; inspect configuration") from None
print("Structured decision:", result["decision"].model_dump_json())
print("Tool operations:", [row["tool"] for row in result["trace"]])
print("Tokens:", sum(row.get("total_tokens", 0) for row in result["usage"]))
