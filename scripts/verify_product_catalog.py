"""Inspect the fictional catalog browser rehearsal without sending more customer messages."""

import json
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
access = json.loads((ROOT / "data/local-access.json").read_text(encoding="utf8"))
owner = access["owners"]["clinic"]
with httpx.Client(base_url="http://localhost:8000", timeout=30) as client:
    response = client.post("/api/auth/login", json={k: owner[k] for k in ("email", "password")})
    response.raise_for_status()
    business = next(
        row
        for row in client.get("/api/businesses").json()["businesses"]
        if row["name"] == "Catalog QA — Cotton Store"
    )
    base = f"/api/businesses/{business['id']}"
    catalog = client.get(base + "/products").json()
    assert catalog["total"] == 4
    imports = client.get(base + "/product-imports").json()
    assert any(row["status"] == "applied" for row in imports)
    versions = client.get(base + "/versions").json()["versions"]
    assert versions[0]["product_revision_id"] == catalog["revision_id"]
    summaries = []
    for conversation in client.get(base + "/conversations").json():
        detail = client.get(base + f"/conversations/{conversation['key']}").json()
        summaries.append(
            {
                "conversation": conversation["key"],
                "ownership": detail["conversation"]["ownership"],
                "messages": [
                    {
                        "direction": row["direction"],
                        "text": row["text"],
                        "config_version": row["config_version"],
                    }
                    for row in detail["messages"]
                ],
            }
        )
    answers = [
        row["text"]
        for summary in summaries
        for row in summary["messages"]
        if row["direction"] == "outgoing"
    ]
    assert any("249.5 EGP" in answer and "availability unknown" in answer for answer in answers)
    assert any("279 EGP" in answer and "الفريق" in answer for answer in answers)
    assert any("color or size" in answer for answer in answers)
    assert any("product name or SKU" in answer for answer in answers)
    assert any(
        summary["ownership"] == "HANDOFF_PENDING"
        and any("special 20% discount" in row["text"] for row in summary["messages"])
        and any("passed your request to our team" in row["text"] for row in summary["messages"])
        for summary in summaries
    )
    result = {
        "business_id": business["id"],
        "product_count": catalog["total"],
        "published_version": versions[0]["version"],
        "conversations": summaries,
    }
    (ROOT / "tmp/product-catalog-verification.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf8"
    )
    print(
        "Verified published English/Arabic prices, unknown stock snapshot, "
        "variant/missing-product clarification, and discount negotiation handoff."
    )
