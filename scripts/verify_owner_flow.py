"""Verify real admin proposals, draft persistence, publish and rollback on the pilot."""

import json
from pathlib import Path

import httpx

root = Path(__file__).resolve().parents[1]
path = root / "data/local-access.json"
access = json.loads(path.read_text(encoding="utf-8"))
owner = access["owners"]["clinic"]
with httpx.Client(base_url="http://localhost:8000", timeout=180) as client:
    response = client.post("/api/auth/login", json={k: owner[k] for k in ("email", "password")})
    response.raise_for_status()
    client.headers["X-CSRF-Token"] = response.json()["csrf_token"]
    base = f"/api/businesses/{owner['business_id']}"
    original = client.get(base + "/draft").json()
    response = client.post(
        base + "/admin/messages",
        json={
            "text": "Propose changing our tone to warm and concise. Keep all other fields, "
            "services, prices, documents, and rules unchanged. Do not publish.",
        },
    )
    response.raise_for_status()
    proposal = response.json()["proposal"]
    assert proposal and "warm" in proposal["tone"].lower()
    assert client.get(base + "/draft").json() == original
    try:
        response = client.put(base + "/draft", json=proposal)
        response.raise_for_status()
        saved = client.get(base + "/draft").json()
        assert saved["config"] == proposal
        assert saved["published_version"] == original["published_version"]
        response = client.post(base + "/publish")
        response.raise_for_status()
        published = response.json()["version"]
        assert published > original["published_version"]
        response = client.post(base + "/rollback", json={"version": original["published_version"]})
        response.raise_for_status()
        restored = response.json()["version"]
        assert restored > published
        versions = client.get(base + "/versions").json()["versions"]
        prior = next(v for v in versions if v["version"] == original["published_version"])
        current = next(v for v in versions if v["version"] == restored)
        assert current["config"] == prior["config"]
        access["businesses"]["clinic"]["version"] = restored
        path.write_text(json.dumps(access, ensure_ascii=False, indent=2), encoding="utf-8")
    finally:
        # The editable draft is restored independently of the published rollback snapshot.
        response = client.put(base + "/draft", json=original["config"])
        response.raise_for_status()
    print(
        "PASS: real setup-agent proposal requires review; draft persists; "
        "publication and rollback verified."
    )
