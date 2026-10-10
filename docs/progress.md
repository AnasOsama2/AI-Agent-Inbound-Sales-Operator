# Salma implementation progress

Plan: docs/superpowers/plans/2026-10-07-salma.md

Ruling: user explicitly requested implementation; proceed inline without additional
design approval loops. Preserve the pasted plan as the specification.
Ruling: use a separate database/container rather than touching Chatwoot's database.
Ruling: optional CPU embeddings are isolated from core dependency installation;
availability must be verified before vector-ingestion claims.
Pre-flight: agent consumes normalized messages and catalog; worker consumes decisions;
API and React consume persisted events. Shared contracts defined in backend/salma.

Tasks 1–5: implemented. Python 3.14 dependencies installed; frontend and Docker built.
Verification: 70 regression tests passed, including 40 labeled simulator fixtures.
Real Groq qwen/qwen3.8-27b availability and bounded tool/separate-decision probe passed
(4,485 tokens). No live customer messages sent.
Both CPU embedding models loaded (384 dimensions); English/Arabic PDF ingestion
and English/Arabic/mixed pgvector retrieval passed. Seed PDFs visually inspected.
Independent review found restart handoff, human-webhook deduplication and delivery
confirmation races; fixed with transaction/locking changes and regression coverage.
Ruling: use a separate salma_test database; background worker interference produced
one flaky integration test in the original development database.
Remaining external validation: existing Evolution–Chatwoot bridge, actual channel
payloads/delivery callbacks, authenticated human takeover in real Chatwoot, alpha
with 5–10 people, original scoring weights and six success targets.

Final Docker smoke: passed using actual Groq-backed worker, authenticated simulator
API, catalog-rendered 500 EGP reply and human takeover suppression. API and database
health checks passed; worker/console running. Existing Chatwoot/Evolution containers
remain up. Local .env enables Groq + CPU embeddings and keeps LIVE_ENABLED=false.
Final source checks: Ruff clean, Python compilation passed, React build passed,
70 backend tests passed in isolated salma_test database.

## Configurable platform — 2026-10-09

Implemented invite-only workspaces, cookie/Redis login, tenant authorization, reviewed
setup-agent proposals, persistent drafts, atomic publication and rollback, private
durable document ingestion, scoped multilingual retrieval and structured catalogs.
The owner console now exposes the complete setup flow, channels, Test Chat and
conversation inspection. Legacy data is isolated and paused; no global business
identity is seeded automatically.

Current verification: 163 backend tests pass; frontend production build passes.
Real API tests verify owner isolation, reviewed admin proposals, draft persistence,
publication/rollback and Redis/worker restart safety. Real Groq pilot conversations
verify prices, offers, English/Egyptian Arabic FAQs, identity disclosure, negotiated
discount handoff and explicit human handoff. A clear missing-fact question exposed
generic clarification behavior; its prompt correction is deployed, but the real
recheck is blocked by the nearly exhausted Groq daily token quota. It safely hands
off as agent_failure; this is not a successful missing-fact policy verification.
The consolidated real-agent suite is 11/12, with this one verification outstanding.
These simulator checks do not send WhatsApp messages.

Existing Evolution and Chatwoot installations now have a verified pilot bridge,
inbox and authenticated Salma webhook. Evolution uses host port 18080 because an
existing Oracle service occupies 8080. QR pairing is available in Channels. The
pilot channel remains paused until the owner pairs the phone and explicitly
activates it. Real WhatsApp reply and Chatwoot human takeover/resume remain pending.

### Setup Assistant identity correction

The owner reported that introducing a furniture business inside the clinic pilot
made the setup assistant identify with Bright Nile and offer unsupported creation
of another business. The admin prompt is now explicitly neutral, with an
authenticated workspace label outside the truncated draft excerpt. Configured
workspace mismatches use a bounded, read-only explanation; proposals cannot rename
an existing business while carrying over its knowledge/catalog. Fresh workspaces
start onboarding without treating their placeholder name as a mismatch. Deliberate
renaming remains available through the owner-reviewed profile form.

Real Arabic model probes verify both the clinic mismatch and fresh furniture
onboarding without persisting test messages in owner history. Three additional
regression cases cover mixed-business proposals, Arabic mismatch responses and
fresh-workspace naming. The console shows which workspace is being edited, offers
operators a direct new-workspace action, and does not revive old proposals after
a later chat response. Narrow layouts bound the chat height so history scrolls
inside it and the composer remains visible.

Focused review also caught a delayed admin-chat response overwriting the displayed
chat/proposal after a workspace switch. The POST/history chain now checks its
captured workspace after both awaits, and switching clears chat, proposal and
composer state. Two deterministic race tests failed before the guard and pass
after it; a normal-response test passes as well. Final checks: 166 backend tests,
3 frontend race/response tests, clean Ruff, and successful production build.

### Owner workspace creation — 2026-10-10

The user could not start another business from an invited owner account. Owner
workspace creation is now explicitly authorized: the existing authenticated,
CSRF-protected creation endpoint creates a fresh draft and creator membership
in one transaction. Invitations and channel provisioning remain operator-only.
Whitespace names are rejected. All signed-in owners see the new-workspace action;
creation opens Setup Assistant, with no copied catalog, documents or history.
167 backend tests and 3 frontend tests pass, including independent-owner denial
and creator membership after session reload.

Setup greetings and Arabic-language requests now return a neutral, immediate
response without external model calls; owner messages and replies still persist
in the same workspace history. Full setup conversations retain the real admin
model. The chat shows a pending-reply indicator and local error, and provider
rate limits are reported explicitly without exposing provider payloads. Regression
coverage checks persisted greetings even with an unavailable model and safe 503
quota failures with the owner message preserved.

Evolution recheck: actual instance state is `close`; authenticated Salma status
and QR endpoints return 200. The QR image is nonempty and its PNG bytes validate.
The channel is unpaired and paused. No provider bug reproduced; the required
remaining action is a fresh phone scan, followed by connection status refresh.
