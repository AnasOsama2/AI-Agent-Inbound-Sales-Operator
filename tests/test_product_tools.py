import json

from salma.agent import Agent
from salma.trust import Decision


def test_discount_negotiation_requires_staff_but_offer_questions_do_not():
    from salma.workspace_runtime import platform_handoff

    for question in (
        "Can you give me a special 20% discount on BLUE-M?",
        "Can you give me a discount?",
        "Please lower the price of BLUE-M",
        "ممكن تعملي خصم على القميص؟",
        "عايز خصم 20%",
    ):
        assert platform_handoff(question) == "discount_negotiation"
    for question in (
        "What is the student discount?",
        "Can you tell me about the discount?",
        "ممكن تفاصيل الخصم؟",
        "خصم الطلبة كام؟",
        "ممكن خصم الطلبة؟",
        "Don't give me a discount, just the catalog price",
        "مش عايز خصم",
    ):
        assert platform_handoff(question) is None


def product(sku="BLUE-M", **changes):
    return {
        "id": f"product:revision:{sku.lower()}",
        "sku": sku,
        "name": "قميص Shirt",
        "options": {"color": "Blue", "size": "M"},
        "price": "249.50",
        "currency": "EGP",
        "stock_quantity": None,
        "stock_status": "unknown",
        "snapshot_at": "2026-10-10T12:00:00Z",
        "source_type": "file",
        "source_ref": "products.csv:2",
        "conditions": "",
        **changes,
    }


def test_product_prices_and_unknown_snapshot_are_backend_rendered():
    from salma.product_tools import render_products

    row = product()
    decision = Decision(
        intent="price",
        reply_kind="product",
        product_skus=[row["sku"]],
        evidence_ids=[row["id"]],
        answer="Free today!",
    )
    reply = render_products(decision, {row["sku"].casefold(): row})
    assert not reply.handoff and "249.5 EGP" in reply.text
    assert "unknown" in reply.text and "2026-10-10" in reply.text
    assert "confirm" in reply.text and "Free" not in reply.text


def test_product_render_rejects_unobserved_price_and_renders_arabic_stock():
    from salma.product_tools import render_products

    row = product(stock_quantity=0, stock_status="out_of_stock")
    decision = Decision(
        intent="price",
        language="ar",
        reply_kind="product",
        product_skus=[row["sku"]],
        evidence_ids=[row["id"]],
    )
    reply = render_products(decision, {row["sku"].casefold(): row})
    assert "249.5 EGP" in reply.text and "غير متوفر" in reply.text and "الفريق" in reply.text
    decision.evidence_ids = []
    assert render_products(decision, {row["sku"].casefold(): row}).handoff


def test_ambiguous_variant_needs_customer_choice():
    from salma.product_tools import ambiguous_variant

    rows = [
        product(product_key="shirt"),
        product("RED-M", product_key="shirt", options={"color": "Red", "size": "M"}),
    ]
    assert ambiguous_variant("How much is the shirt?", [rows[0]], rows)
    assert not ambiguous_variant("BLUE-M price?", [rows[0]], rows)
    assert not ambiguous_variant("Blue M shirt price", [rows[0]], rows)
    assert ambiguous_variant("M shirt price", [rows[0]], rows)
    assert ambiguous_variant("How much is the blue shirt?", [rows[0]], rows)
    assert not ambiguous_variant("سعر القميص الأزرق مقاس M", [rows[0]], rows)
    short = [product("A", product_key="short"), product("B", product_key="short")]
    assert ambiguous_variant("What price is the shirt?", [short[0]], short)
    colors = [
        product("BLUE", product_key="color"),
        product("BLUE-XXL", product_key="color", options={"color": "Blue", "size": "XXL"}),
    ]
    assert ambiguous_variant("بكام القميص الأزرق؟", [colors[0]], colors)


def test_product_tool_evidence_limits_decision_schema():
    row = product()

    def completion(messages, *, tools=None, schema=None):
        if tools:
            assert any(t["function"]["name"] == "product_search" for t in tools)
            return {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": "p",
                        "type": "function",
                        "function": {
                            "name": "product_search",
                            "arguments": json.dumps({"query": "shirt", "language": "en"}),
                        },
                    }
                ],
            }
        assert schema["properties"]["product_skus"]["items"]["enum"] == [row["sku"]]
        return {
            "role": "assistant",
            "content": Decision(
                intent="price",
                reply_kind="product",
                product_skus=[row["sku"]],
                evidence_ids=[row["id"]],
            ).model_dump_json(),
        }

    result = Agent(completion, lambda *_: [row], stop_after_read=True).run("shirt", "text", [])
    assert result["decision"].product_skus == [row["sku"]]
