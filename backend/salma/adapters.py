from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field


class Inbound(BaseModel):
    business_id: str = "legacy"
    config_version: int | None = None
    provider: Literal["chatwoot", "evolution", "simulator"]
    channel: str
    account_id: str
    inbox_id: str
    external_message_id: str
    external_conversation_id: str
    external_customer_id: str
    content_type: str = "text"
    text: str = Field(default="", max_length=12000)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict = Field(default_factory=dict)

    @property
    def customer_key(self):
        prefix = "" if self.business_id == "legacy" else f"{self.business_id}:"
        return (
            f"{prefix}{self.provider}:{self.account_id}:{self.inbox_id}:{self.external_customer_id}"
        )

    @property
    def conversation_key(self):
        prefix = "" if self.business_id == "legacy" else f"{self.business_id}:"
        scope = f"{prefix}{self.provider}:{self.account_id}:{self.inbox_id}:"
        return scope + self.external_conversation_id


def normalize_chatwoot(data: dict, account_id: int, inboxes: dict[int, str]) -> Inbound | None:
    if data.get("event") != "message_created" or data.get("private", False):
        return None
    if data.get("message_type") not in ("incoming", 0):
        return None
    sender = data.get("sender") or {}
    if str(sender.get("type", "")).lower() not in ("contact", ""):
        return None
    if data.get("account", {}).get("id") != account_id:
        raise ValueError("Account is not authorized")
    inbox_id = data.get("inbox", {}).get("id")
    if inbox_id not in inboxes:
        raise ValueError("Inbox is not authorized")
    # Evolution posts this startup notice as an incoming Chatwoot contact message.
    # It has no WhatsApp source ID. Do not treat it as a customer greeting.
    if (
        inboxes[inbox_id] == "whatsapp"
        and sender.get("name") == "EvolutionAPI"
        and not data.get("source_id")
        and data.get("content") == "🚀 Connection successfully established!"
    ):
        return None
    conversation_id = data.get("conversation", {}).get("id")
    if not all((conversation_id, sender.get("id"), data.get("id"))):
        raise ValueError("Missing provider identity")
    content_type = data.get("content_type", "text")
    if data.get("attachments"):
        content_type = "media"
    return Inbound(
        provider="chatwoot",
        channel=inboxes[inbox_id],
        account_id=str(account_id),
        inbox_id=str(inbox_id),
        external_message_id=str(data["id"]),
        external_conversation_id=str(conversation_id),
        external_customer_id=str(sender["id"]),
        content_type=content_type,
        text=data.get("content") or "",
        timestamp=data.get("created_at"),
        metadata={"source_id": data.get("source_id")},
    )
