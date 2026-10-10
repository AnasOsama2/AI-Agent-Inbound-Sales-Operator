# Configurable Salma MVP implementation ledger

Approved specification: user's 2026-10-09 “Salma: Configurable Inbound Sales MVP” plan in this conversation.

- Architecture: PostgreSQL durable tenant-scoped state, Redis ephemeral/auth state, admin draft agent and published sales agent, owner forms and chat, managed Chatwoot accounts, WhatsApp first.
- Work split under subagent-driven-development: platform auth/config/model migration; owner React console; documents/channels; coordinator owns sales runtime, scoped conversation API, integration and verification.
- Existing untracked project files are preserved. Repository has no tracked implementation baseline; do not discard existing work or create a worktree from an empty HEAD.
- Runtime policy tests first run: expected collection failure because workspace_runtime did not yet exist.
- Ruling: retain legacy APIs only for legacy test compatibility behind a disabled-by-default flag; public product uses cookie-authenticated, business-scoped endpoints exclusively.
- Ruling: existing agency data becomes a paused legacy workspace. Bright Nile is imported only as a pilot fixture, never globally seeded.
- Ruling: full WhatsApp verification needs valid provider credentials and user phone pairing; prepare and verify everything else without simulating provider success.

## Status

- [x] Auth, tenancy and migration
- [x] Admin agent, draft/publish/rollback
- [x] Private uploads, ingestion and scoped RAG
- [x] Published sales runtime and ownership
- [x] Owner console
- [x] WhatsApp guided connection, provider bridge and inbox/webhook verification
- [x] Regression suite, browser QA and pilot configuration
- [ ] Pilot phone pairing and real WhatsApp reply, human takeover and explicit resume

## Verification on 2026-10-09

- 163 backend regression tests pass in the isolated `salma_test` database. Frontend production build passes.
- Real owner sessions reject cross-business access. Bright Nile and Harbor Auto Care have separate approved documents, catalogs and published configurations.
- Real multilingual embeddings and pgvector ingestion completed. Actual Groq replies quote clinic prices and approved offers, answer English and Egyptian Arabic hours, disclose AI identity, and quote the garage's own oil-change package.
- Consolidated real-agent acceptance: 11 of 12 cases pass, including negotiated discounts and explicit human requests. The missing-fact case exposed overly generic clarification; the shared prompt was corrected and deployed. Its recheck exhausted provider rate-limit retries and safely handed off as `agent_failure`, so it is not counted as passing. Groq's diagnostic confirmed the 200,000-token daily quota was nearly exhausted, with insufficient remaining tokens for the full clinic request.
- The real setup agent proposes reviewed changes; draft persistence, explicit publishing and rollback were verified through the authenticated API. Rollback creates a new immutable version.
- Deleting temporary conversation context and restarting Redis and the worker preserve published settings, history and human ownership.
- Browser checks at the current narrow workspace width confirmed a visible Test Chat composer and usable navigation. WhatsApp QR rendering was verified.
- Evolution 2.3.7 and Chatwoot 4.18.0 are connected through the managed pilot bridge. Historical import is disabled. The pilot connection remains paused and awaiting the owner's phone scan; no real WhatsApp roundtrip has been claimed.

Local owner credentials are in the ignored `data/local-access.json`; provider access is in the separate ignored `data/provider-access.json`. See `platform-quickstart.md` for setup and live verification.
