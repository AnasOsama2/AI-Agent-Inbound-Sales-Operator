"""Create local secrets without printing or replacing existing credentials."""

import secrets
from pathlib import Path

path = Path(".env")
content = path.read_text(encoding="utf-8-sig") if path.exists() else ""
existing = {line.split("=", 1)[0].strip().lower() for line in content.splitlines() if "=" in line}
additions = []
for line in Path(".env.example").read_text(encoding="utf-8").splitlines():
    if not line or line.startswith("#"):
        continue
    key, value = line.split("=", 1)
    if key.lower() in existing or (key == "GROQ_API_KEY" and "groq_key" in existing):
        continue
    if key in {"STAFF_TOKEN", "WEBHOOK_TOKEN"}:
        value = secrets.token_urlsafe(32)
    if key == "CREDENTIAL_KEY":
        from cryptography.fernet import Fernet
        value = Fernet.generate_key().decode()
    additions.append(f"{key}={value}")
if additions:
    path.write_text(content.rstrip() + "\n" + "\n".join(additions) + "\n", encoding="utf-8")
print(f"Added {len(additions)} missing settings; existing credentials preserved")
