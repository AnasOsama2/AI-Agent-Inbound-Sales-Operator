from salma.trust import Decision, render


def test_integer_catalog_price_preserves_trailing_zeroes():
    reply = render(
        Decision(
            intent="price",
            reply_kind="service",
            service_ids=["consultation"],
            evidence_ids=["catalog:consultation"],
        ),
        {
            "consultation": {
                "price": 500,
                "name_en": "Consultation",
                "name_ar": "استشارة",
                "currency": "EGP",
            }
        },
        {},
    )
    assert "500 EGP" in reply.text
