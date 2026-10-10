"""Exercise real published pilots via the authenticated API and durable worker."""

import argparse
import json
import re
import time
from pathlib import Path
from uuid import uuid4

import httpx

ROOT = Path(__file__).resolve().parents[1]
access = json.loads((ROOT / "data/local-access.json").read_text(encoding="utf-8"))
parser = argparse.ArgumentParser()
parser.add_argument("--match", default="", help="Run only questions matching this regex")
parser.add_argument("--inspect-latest", action="store_true", help="Inspect existing test replies")
args = parser.parse_args()
results_path = (
    ROOT / "tmp" / ("pilot-sales-extra-results.json" if args.match else "pilot-sales-results.json")
)
cases = [
    ("clinic", "How much is a consultation?", "300", False),
    ("clinic", "تنظيف الاسنان بكام؟", "600", False),
    ("clinic", "What are your opening hours?", "10", False),
    ("clinic", "مواعيد العيادة إيه؟", "10", False),
    ("clinic", "Are you an AI or a real person?", "AI", False),
    ("clinic", "What is the student cleaning offer?", "540", False),
    ("clinic", "Can you give me a special 40% discount?", None, True),
    ("clinic", "I want to speak with a human", None, True),
    ("clinic", "Who is the dentist on duty tomorrow?", None, True),
    ("garage", "How much is an oil change?", "900", False),
    ("garage", "What are your opening hours?", "8", False),
    ("garage", "How much is dental whitening?", "Harbor Auto Care", False),
]
results = []
for label in ("clinic", "garage"):
    with httpx.Client(base_url="http://localhost:8000", timeout=90) as client:
        owner = access["owners"][label]
        response = client.post("/api/auth/login", json={k: owner[k] for k in ("email", "password")})
        response.raise_for_status()
        client.headers["X-CSRF-Token"] = response.json()["csrf_token"]
        bid = owner["business_id"]
        other = access["owners"]["garage" if label == "clinic" else "clinic"]["business_id"]
        assert client.get(f"/api/businesses/{other}/draft").status_code == 404
        for case_label, question, expected, transfer in cases:
            if case_label != label:
                continue
            if args.match and not re.search(args.match, question):
                continue
            key = "acceptance-" + uuid4().hex
            conversation = None
            if args.inspect_latest:
                response = client.get(f"/api/businesses/{bid}/conversations")
                response.raise_for_status()
                for existing in response.json():
                    response = client.get(f"/api/businesses/{bid}/conversations/{existing['key']}")
                    response.raise_for_status()
                    if any(
                        m["direction"] == "incoming" and m["text"] == question
                        for m in response.json()["messages"]
                    ):
                        conversation = existing["key"]
                        break
                assert conversation, "No existing test conversation matches this question"
            else:
                response = client.post(
                    f"/api/businesses/{bid}/simulator/messages",
                    json={"conversation_id": key, "customer_id": key, "text": question},
                )
                response.raise_for_status()
                conversation = response.json()["conversation_key"]
            deadline = time.monotonic() + 600
            detail = None
            while time.monotonic() < deadline:
                try:
                    response = client.get(f"/api/businesses/{bid}/conversations/{conversation}")
                except httpx.TransportError:
                    time.sleep(1)
                    continue
                response.raise_for_status()
                detail = response.json()
                incoming = detail["messages"][0]
                has_reply = any(m["direction"] == "outgoing" for m in detail["messages"])
                if incoming["status"] not in ("pending", "processing") and has_reply:
                    break
                time.sleep(1)
            replies = [m["text"] for m in detail["messages"] if m["direction"] == "outgoing"]
            answer = "\n".join(replies)
            actual_handoff = detail["conversation"]["ownership"] != "AI_ACTIVE"
            passed = (
                bool(answer)
                and incoming.get("decision", {}).get("intent") != "failure"
                and actual_handoff == transfer
                and (not expected or expected.lower() in answer.lower())
            )
            result = {
                "business": label,
                "conversation_key": conversation,
                "question": question,
                "answer": answer,
                "handoff": actual_handoff,
                "passed": passed,
                "decision": incoming.get("decision"),
                "status": incoming["status"],
            }
            results.append(result)
            results_path.write_text(
                json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            print(json.dumps(result, ensure_ascii=False), flush=True)
results_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
print(
    f"Passed {sum(r['passed'] for r in results)}/{len(results)} real-agent cases; "
    "owner isolation passed."
)
raise SystemExit(0 if results and all(row["passed"] for row in results) else 1)
