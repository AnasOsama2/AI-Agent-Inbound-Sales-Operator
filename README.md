# Salma: configurable inbound sales

Invite-only business workspaces with an owner Setup Assistant and a separate customer sales agent. FastAPI, LangGraph, Groq, PostgreSQL/pgvector, dedicated Redis, and React. Existing Chatwoot and Evolution remain separate services.

## Start locally

```powershell
python scripts/init_env.py
docker compose up --build -d
.venv/Scripts/python.exe scripts/setup_platform_pilots.py
```

Open **http://localhost:5173/**. Sign in with the operator or business owner credentials saved privately in `data/local-access.json`. The pilot setup creates Bright Nile Dental Clinic and Harbor Auto Care through real APIs; it is an explicit demo setup, never automatic global seeding. Do not rerun it over owner edits you intend to keep.

Follow [the platform quickstart](docs/platform-quickstart.md) for invitations, documents, prices, publication, and channel pairing. Salma's API is on port8000, PostgreSQL on5433, and its dedicated Redis on6380. Provider credentials are encrypted server-side using `CREDENTIAL_KEY`; keep the key and database backups together securely. Original uploads use a persistent private Docker volume.

## Configure and publish

Use Overview, Setup Assistant, Knowledge, Products & Services, Agent Settings, Channels, Test Chat, and Conversations. Setup chat proposes changes for owner review. Both chat and forms use the same persistent draft. Publish explicitly to create an atomic snapshot of instructions, rules, catalog and approved document versions. Rollback creates another published version. Draft edits never change live replies.

New business knowledge uses the multilingual embedding model, supports English and Arabic, and retains document/page evidence. Upload text PDFs or UTF-8 TXT, up to20MB. OCR is deferred. Prices and offers are rendered from structured approved catalog records. Staff confirm appointments, orders, payments and custom final quotes. Explicit human requests and staff replies stop AI; only staff resume it. Opt-out is independent of ownership.

The legacy agency records are preserved in an isolated paused workspace. Legacy global APIs are disabled by default. New owners use opaque Redis-backed HttpOnly cookie sessions and CSRF-protected mutations. Every business resource checks membership; inbound channels resolve business identity from verified account/inbox bindings.

## Channels

WhatsApp routes through **WhatsApp → Evolution → Salma → Evolution**, with its Chatwoot bridge disabled. In Channels, set up WhatsApp, scan the QR, publish your configuration and explicitly activate replies. Human handoffs appear in Salma Conversations; replying from the connected phone stops AI, and staff explicitly resume it. Messenger and Instagram use separate, business-scoped Chatwoot inboxes. Meta app configuration and live social verification are still required. Historical message imports are excluded from automation.

The local Evolution instance uses port18080. `evolution-salma.override.yaml` adjusts that host binding and enables integration support globally; Salma disables the Chatwoot bridge for each direct WhatsApp instance. Configure provider URLs and keys in your private `.env`. Do not stop unrelated services to free a port.

Durable inbound/outbox processing deduplicates messages and bot echoes. Uncertain sends remain ambiguous for staff reconciliation and are never blindly retried. Redis loss cannot erase configuration or resume human-owned conversations. A real phone scan and live-message check are required before claiming WhatsApp acceptance.

## Development and verification

```powershell
uv sync --native-tls --extra dev --extra knowledge
$env:PYTHONPATH = 'backend'
$env:TEST_DATABASE_URL = 'local'
.venv/Scripts/python.exe -m pytest -q
.venv/Scripts/ruff.exe check backend tests migrations
cd frontend
npm run build
```

Tests use the separate `salma_test` database, never the running worker's development queue. Apply Alembic migrations to that database before a fresh test setup. Docker applies development migrations on startup. Tests without a configured integration database skip those checks.

Set `AGENT_MODE=groq`, `GROQ_API_KEY` and `INSTALL_KNOWLEDGE=true` for real configured agents. The model is `qwen/qwen3.8-27b`; provider failures do not silently fall back to fabricated answers. Platform safeguards cannot be disabled through business instructions or uploaded documents. Secrets and private local access records are excluded from Git and Docker build contexts.

See [implementation status](docs/platform-implementation.md) for verified checks and remaining live acceptance steps. Billing, public signup, OCR, calendar execution, scheduled follow-ups and CRM integrations are deferred.
