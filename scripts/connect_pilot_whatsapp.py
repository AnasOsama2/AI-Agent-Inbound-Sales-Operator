"""Configure the local pilot's managed channel; never activate or print credentials."""

import json
from pathlib import Path

import httpx

root = Path(__file__).resolve().parents[1]
access = json.loads((root / "data/local-access.json").read_text(encoding="utf-8"))
providers = json.loads((root / "data/provider-access.json").read_text(encoding="utf-8"))
bid = access["businesses"]["clinic"]["id"]
with httpx.Client(base_url="http://localhost:8000", timeout=100) as client:
    response = client.post("/api/auth/login", json=access["operator"])
    response.raise_for_status()
    client.headers["X-CSRF-Token"] = response.json()["csrf_token"]
    path = f"/api/businesses/{bid}/channels"
    response = client.get(path)
    response.raise_for_status()
    channels = response.json()["channels"]
    if not channels:
        response = client.post(
            path,
            json={
                "kind": "whatsapp",
                "name": "Bright Nile pilot WhatsApp",
                "account_id": providers["clinic"]["account_id"],
                "api_token": providers["clinic"]["api_token"],
            },
        )
        response.raise_for_status()
        channel = response.json()
    else:
        channel = channels[0]
    response = client.post(path + f"/{channel['id']}/connect")
    if not response.is_success:
        print("Connection result:", response.status_code, response.text)
        raise SystemExit(1)
    print("Managed channel configured and paused:", response.json())
