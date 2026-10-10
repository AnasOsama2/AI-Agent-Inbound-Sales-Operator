# Salma platform quickstart

Open the business admin workspace at **http://localhost:5173/**. Salma has two agents: the Setup Assistant proposes business configuration for owner review; the sales agent answers customers using the published configuration. Setup chat never publishes or activates a channel automatically.

## Local pilot accounts

With the latest Salma API, worker, PostgreSQL and dedicated Redis services running, execute from the project root:

```powershell
.venv/Scripts/python.exe scripts/setup_platform_pilots.py
```

The script uses the real API, Redis cookie sessions, document ingestion worker and publication workflow. It creates two fictional businesses, Bright Nile Dental Clinic and Harbor Auto Care, and an owner account for each. It creates an operator only if that email has no existing account; it never resets an existing password. If an operator exists without a saved local access record, supply its existing `SALMA_OPERATOR_EMAIL` and `SALMA_OPERATOR_PASSWORD` before rerunning.

Account credentials and workspace IDs are saved to **data/local-access.json**. This local credential record must remain private and excluded from Git. Open it locally to obtain the operator and owner login details; do not paste it into chat or upload it as knowledge. The script prints workspace IDs and the record location, never passwords. Rerunning reuses existing businesses, documents and owner credentials, and republishes only when configuration differs. It deliberately restores these demo configurations, so do not rerun over business edits you want to keep.

The clinic source is the provided PDF in Downloads. Only curated business facts from sections 1–7 are indexed; evaluation examples, ROI assumptions and embedded agent instructions are excluded. Its 300 EGP consultation, service prices, offers and limits are structured catalog data. The garage has its own 900 EGP oil-change package and knowledge. All clinic names, addresses, phone numbers and emergency numbers are fictional; no number in this demo is a real medical or emergency service.

## Configure your business

1. Sign in as operator to create a workspace and issue an owner invitation. Share the single-use invitation link manually. The owner follows it and sets a password. A consumed invitation cannot be reused.
2. Sign in as the owner. Use **New business workspace** beneath the workspace selector to create another business. Enter its name and select **Create workspace**; Salma opens its fresh Setup Assistant. The creator automatically gets membership, with no copied demo data or published configuration. Complete business details through the editable settings and Setup Assistant. Review its proposed changes before applying them.
3. Upload a text-based PDF or UTF-8 TXT in Knowledge, up to 20 MB. Wait for indexing to finish, then approve the document. Scanned PDFs require a text-based replacement; OCR is not included.
4. Review Services & Prices, including currency, fixed/base/starting-from prices, conditions, and offer expiry dates. Review named handoff rules and lead fields in Agent Settings.
5. Publish explicitly. Draft changes do not change customer replies. Published versions can be restored using rollback; the restoration creates a new version.
6. Use Test Chat to ask English and Egyptian Arabic questions. Start a new simulation when testing fresh behavior. After handoff, AI remains paused until staff explicitly resume it. An opt-out remains separate from ownership.

Suggested clinic checks: consultation 300 EGP; cleaning 600 EGP; student cleaning 540 EGP with valid ID; whitening 3500 EGP; opening hours; Friday closure; identity disclosure; a negotiated discount; a medical question; and appointment preference collection. For the garage, verify oil change 900 EGP and 8 AM–6 PM hours, then ask about clinic services to confirm that clinic facts are unavailable.

Appointments, orders, custom final quotes and payments require staff confirmation. No live calendar, scheduled reminders or CRM sequences are connected. Test Chat uses the real configured agent and document retrieval; it does not send WhatsApp messages.

## Connect a live channel

Use Channels to configure the business's separate Chatwoot account and inbox. Provider credentials are stored server-side. WhatsApp routes through **Evolution → Chatwoot → Salma**, and Salma sends live replies only through Chatwoot. Keep historical message import disabled during pairing.

The existing Oracle service occupies port 8080 on this computer. When using the local Evolution port override, its host endpoint is **http://localhost:18080**. Supply the matching Evolution endpoint and credentials for the actual deployment; container-to-container addresses may differ from host URLs.

Create or link the WhatsApp instance, refresh the QR code, and scan it using the pilot phone's WhatsApp linked-device flow. A phone scan is required; software setup alone cannot pair the account. Check connection status and inbox mapping, publish the business configuration, then explicitly activate the channel.

Verify a fresh WhatsApp message receives the correct business answer. Reply as staff in Chatwoot and confirm AI stops, then explicitly resume it for a new message. An authenticated delivery callback or staff reply is still processed when automation is paused. Messenger and Instagram can link an already configured Chatwoot inbox; embedded Meta signup is deferred.

If delivery becomes ambiguous, inspect Chatwoot provider history before reconciling it in Conversations. Record whether it was accepted or not sent; do not retry blindly. Staff assignment failures remain visible and never resume AI automatically.

The setup script does not activate channels or claim a live WhatsApp test passed. Successful live verification requires provider access, the phone scan and an actual message exchange.

## Local verification

`scripts/verify_owner_flow.py` exercises a real setup-agent proposal, reviewed draft edit, publication and rollback on the clinic pilot, then restores its original draft. `scripts/verify_pilot_sales.py` runs real sales conversations; `--inspect-latest` checks existing replies without new model calls. `scripts/verify_restart_safety.py` checks that temporary Redis context loss and Redis/worker restarts preserve durable conversation ownership and history.

The currently provisioned Groq quota is 8,000 tokens per minute, 200,000 tokens per day and 1,000 requests per day. Calls use bounded rate-limit retries, so test replies may take around a minute under quota pressure. Daily token exhaustion can cause a safe provider-failure handoff until quota replenishes. These checks establish behavior; production response latency still requires measurement with the intended provider capacity.

As of the final 2026-10-09 check, 11/12 real-agent pilot cases pass. The corrected missing-fact case still needs a successful recheck after token quota replenishes: run `scripts/verify_pilot_sales.py --match "on duty"`, then `scripts/verify_pilot_sales.py --inspect-latest`. The verification script exits unsuccessfully if any selected case fails; a provider-failure handoff is not accepted as a policy pass.
