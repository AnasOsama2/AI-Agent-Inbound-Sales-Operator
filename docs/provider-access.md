# Provider access from Salma

## Current routing: direct WhatsApp

WhatsApp now uses **WhatsApp → Evolution → Salma → Evolution**. Chatwoot is used only for Messenger and Instagram. Existing WhatsApp history is retained; the old WhatsApp Chatwoot webhook is ignored after migration to prevent duplicate replies.

In Channels, owners can select **Set up WhatsApp via Evolution**, pair their number, publish their business configuration and activate replies. Existing bridged channels offer **Use Evolution directly**. Migration pauses dispatch while disabling the bridge and registering a secret, instance-bound direct webhook. Only new messages after the migration are eligible; historical imports are excluded. Provider credentials remain server-side.

Human handoffs for direct WhatsApp are visible in Salma **Conversations**. A reply from the connected phone takes ownership; staff explicitly resume Salma in Conversations. Chatwoot team assignment applies to social channels.

Verified on 2026-10-10: the Bright Nile pilot is paired (`open`), its direct webhook is verified, its Chatwoot bridge is disabled, and live replies are enabled. A real customer asked `تنضيف الأسنان بكام`; Salma quoted the published cleaning price of 600 EGP. Evolution accepted the reply, and the owner confirmed receiving it on WhatsApp. Full backend verification: 232 tests passed; Ruff passed. Browser evidence: `tmp/direct-whatsapp.png`. Human takeover has regression coverage; a live phone takeover/resume test was not performed in this verification.

Open **Channels** in the business workspace.

- **Open Chatwoot** opens the account mapped to that workspace. Chatwoot requires its own sign-in and enforces its account memberships.
- Platform operators also see **Open Evolution Manager**. Owners pair their own number with the existing **Pair WhatsApp** controls; the shared Evolution manager is not exposed to owners.
- Each channel checks its providers automatically. **Check provider connections** refreshes the read-only check.
- Chatwoot verification checks social inbox mappings and Salma webhook subscriptions. Direct Evolution verification checks pairing, the Salma webhook and the disabled Chatwoot bridge.
- Pairing and activation are separate. Connectivity checks never pair a number, activate sending or send a message.

Browser-facing URLs are configured with `CHATWOOT_PUBLIC_URL` (default `http://localhost:3001`) and `EVOLUTION_PUBLIC_URL` (default `http://localhost:18080`). Internal provider URLs remain server-side. Set the public URLs to browser-reachable addresses when deploying beyond this computer.

## Messenger and Instagram

The Channels page now includes direct **Connect Messenger** and **Connect Instagram** links to the mapped Chatwoot account's inbox setup. Finish Meta login and inbox creation in Chatwoot, return to Salma and select **Refresh Chatwoot inboxes**, then **Link to Salma**. New links remain paused until explicitly activated.

Owners never enter account IDs, inbox IDs or API tokens in this flow. Inbox discovery is limited to accounts already bound to their workspace. Linking rechecks the provider inbox type and account ownership, creates encrypted channel credentials and registers a separate authenticated Salma webhook. It preserves existing WhatsApp settings and rejects duplicate or foreign mappings.

Self-hosted Chatwoot requires a configured Meta app and public HTTPS callback URLs before social login works. On this local installation the Facebook and Instagram app fields were absent during verification. No social inbox exists yet; external Meta login and live social messaging remain unverified.

Verification of the new flow: 226 backend tests and 5 frontend tests passed; Ruff and the production frontend build passed. The live discovery endpoint returned only the pilot account and no social inboxes. Browser verified both setup links and the refresh/empty states. Screenshot: `tmp/social-channels.png`.

## Earlier bridged-route verification, superseded by direct routing

Bright Nile pilot: Chatwoot account 1/inbox 1 and webhook verified. Evolution API reachable, bridge verified, WhatsApp state `close` (awaiting QR pairing). Live sending remains paused. Both provider browser interfaces returned HTTP 200.

Validation: 225 backend tests, 5 frontend tests, Ruff and production frontend build passed. Browser verified the scoped Chatwoot shortcut and provider statuses. Evidence: `tmp/provider-connections.json`, `tmp/provider-connections.png` and `tmp/provider-status.png`.
