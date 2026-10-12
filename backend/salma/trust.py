import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Decision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    intent: str
    language: Literal["ar", "en", "mixed"] = "en"
    reply_kind: Literal[
        "service", "product", "knowledge", "clarify", "handoff", "greeting", "media"
    ]
    service_ids: list[str] = Field(default_factory=list, max_length=5)
    product_skus: list[str] = Field(default_factory=list, max_length=5)
    evidence_ids: list[str] = Field(default_factory=list, max_length=8)
    lead_signals: list[Literal["service_interest", "budget", "timeline", "requirements"]] = Field(
        default_factory=list
    )
    handoff_reason: str | None = None
    # Diagnostic only; never used as an authorization or evidence check.
    confidence: float | None = Field(default=None, ge=0, le=1)
    answer: str = Field(default="", max_length=4000)
    acknowledgment: Literal[
        "none", "happy_to_help", "understood", "thanks", "preference_noted", "no_pressure"
    ] = "none"
    include_service_details: bool = False
    rule_id: str | None = None
    question_kind: Literal[
        "none",
        "general",
        "service",
        "contact",
        "preferred_time",
        "preferred_day",
        "preferred_clock_time",
        "requirements",
        "budget",
        "timeline",
        "identity",
        "variant",
        "product",
        "price_or_booking",
        "booking_interest",
        "service_goal",
        "comparison",
    ] = "none"


@dataclass
class Reply:
    text: str
    handoff: bool = False
    reason: str | None = None


def handoff(language: str, reason: str) -> Reply:
    return Reply(
        "هحوّلك لفريقنا عشان يساعدك. تم إرسال طلبك، وهيتابعوا معاك."
        if language != "en"
        else "I’ve passed your request to our team. A staff member will follow up.",
        True,
        reason,
    )


def render(decision: Decision, catalog: dict, evidence: dict[str, str]) -> Reply:
    ar = decision.language != "en"
    if decision.reply_kind == "handoff":
        return handoff(decision.language, decision.handoff_reason or "human_requested")
    if decision.reply_kind == "service":
        if not decision.service_ids:
            return handoff(decision.language, "missing_service")
        lines = []
        quote_only = False
        for service_id in decision.service_ids:
            service = catalog.get(service_id)
            if not service or f"catalog:{service_id}" not in decision.evidence_ids:
                return handoff(decision.language, "missing_evidence")
            name = service["name_ar" if ar else "name_en"]
            if service["price"] is None:
                lines.append(
                    f"{name}: السعر حسب المتطلبات؛ فريقنا هيجهز عرض سعر."
                    if ar
                    else f"{name}: quote required after reviewing your requirements."
                )
                quote_only = True
            else:
                amount = format(Decimal(str(service["price"])).normalize(), "f")
                lines.append(f"{name}: {amount} {service['currency']}.")
        if quote_only:
            lines.append(handoff(decision.language, "quote_required").text)
        return Reply("\n".join(lines), quote_only, "quote_required" if quote_only else None)
    if decision.reply_kind == "knowledge":
        if not decision.evidence_ids or any(ref not in evidence for ref in decision.evidence_ids):
            return handoff(decision.language, "missing_evidence")
        excerpts = [evidence[ref] for ref in decision.evidence_ids]
        if any(re.search(r"\d|EGP|جنيه|price|سعر|discount|خصم", item, re.I) for item in excerpts):
            return handoff(decision.language, "monetary_knowledge")
        return Reply("\n\n".join(excerpts))
    if decision.reply_kind == "greeting":
        return Reply(
            "أهلاً! أقدر أساعدك في خدماتنا أو أجمع متطلبات مشروعك. محتاج إيه؟"
            if ar
            else "Hello! I can help with our services or collect your project requirements. "
            "What do you need?"
        )
    if decision.reply_kind == "media":
        return Reply(
            "ممكن تبعت طلبك كتابة؟ حالياً بقدر أتعامل مع النصوص بس."
            if ar
            else "Please send your request as text. I can currently handle text messages."
        )
    return Reply(
        "ممكن توضح طلبك أو الخدمة اللي مهتم بيها؟"
        if ar
        else "Could you clarify your request or the service you’re interested in?"
    )


def calculate(service_id: str, quantity: int, catalog: dict) -> str:
    if isinstance(quantity, bool) or not isinstance(quantity, int) or not 1 <= quantity <= 100:
        raise ValueError("Quantity must be an integer between 1 and 100")
    service = catalog.get(service_id)
    if not service or service["price"] is None:
        raise ValueError("An approved fixed-price service is required")
    return str((Decimal(str(service["price"])) * quantity).quantize(Decimal("0.01")))


def score(signals: set[str], weights: dict[str, int], thresholds: dict[str, int]):
    if not weights or not {"warm", "hot"} <= thresholds.keys():
        return None, "unconfigured"
    value = sum(weights.get(signal, 0) for signal in signals)
    return value, "hot" if value >= thresholds["hot"] else "warm" if value >= thresholds[
        "warm"
    ] else "cold"


def mandatory_handoff(text: str) -> str | None:
    if re.search(
        r"\b(human|person|staff|agent|representative|complaint|refund|discount)\b|"
        r"موظف|بشري|بني آدم|بنى ادم|حد من الفريق|شكوى|شكوي|استرجاع|خصم",
        text,
        re.I,
    ):
        return "human_request_or_restricted_action"
    if re.search(r"\b(book|booking|reserve|reservation|quote)\b|حجز|احجز|عرض سعر", text, re.I):
        return "staff_confirmation_required"
    return None
