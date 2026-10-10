# Local WhatsApp delivery blocker

## Current resolution: separate transports

On 2026-10-10 the owner requested direct Evolution WhatsApp routing, with Chatwoot reserved for Messenger and Instagram. Implemented an authenticated, instance-bound Evolution webhook, direct text delivery through the durable outbox, tenant checks, deduplication, history exclusion, bot-echo filtering, human takeover and existing configuration/ownership dispatch safeguards.

The installed Evolution 2.3.7 schema requires `signMsg`, `reopenConversation` and `conversationPending` even when disabling its Chatwoot bridge. Added these fields and regression coverage. The pilot migration and activation returned HTTP 200; provider inspection and the Channels screen verify the bridge is disabled, the direct webhook is correct and WhatsApp is open. No old customer messages were replayed.

End-to-end delivery verified: a new WhatsApp customer message, `تنضيف الأسنان بكام`, reached the Evolution webhook and was processed once. Salma answered with the published cleaning price of 600 EGP; the outbox recorded provider acceptance and a message identity. The owner explicitly confirmed receiving the reply on WhatsApp. Conversation ownership remained AI_ACTIVE. All 232 backend tests and Ruff passed. Live human takeover/resume was not exercised; those controls are covered by direct transport regression tests.

The investigation below describes the earlier bridged architecture and its repairs.

Verified 2026-10-10 after the owner activated Bright Nile's channel:

- Salma channel `live_enabled` is true.
- Chatwoot has the configured Salma webhook and incoming inbox messages.
- Salma has no Chatwoot messages for the pilot, so agent execution and reply delivery have not begun.
- Chatwoot Sidekiq reports `Hostname 'host.docker.internal' has no public ip addresses` in `Webhooks::Trigger` / `SafeFetch`.
- The same validation also blocks API-inbox callbacks to Evolution. Basic network reachability alone passes.

The installed Chatwoot `SafeFetch` supports `SAFE_FETCH_ALLOW_PRIVATE_NETWORK`, default false. It has no destination allowlist setting. Enabling it allows all SafeFetch requests to private networks, including file retrieval; this is a security-relevant change requiring owner approval.

Prepared opt-in configuration: `ops/chatwoot-local-network.override.yaml`. Nothing has been applied. Deployment uses `D:\chatwoot\docker-compose.production.yaml`, services `rails` and `sidekiq`.

After approval, combine the existing Chatwoot compose file with this override and recreate only rails/sidekiq. Preserve existing environment, volumes and other services. Then verify a new WhatsApp message reaches Salma and receives a Chatwoot/Evolution reply. Do not replay old conversations or automatically resume human-owned conversations.

The Channels status check verifies provider configuration and pairing; it does not establish that Chatwoot's background webhook requests pass its private-network validation. A connected badge alone is not an end-to-end delivery test.

## Approved repair applied

Following explicit owner approval, enabled `SAFE_FETCH_ALLOW_PRIVATE_NETWORK=true` in Chatwoot rails and sidekiq using the prepared override and recreated those two services. Verified the setting in both running containers. Chatwoot's actual `SafeFetch` client successfully fetched Salma's health endpoint and Evolution's root endpoint. The private-network transport blocker is resolved.

Incoming webhooks began reaching Salma. Inspection showed they were Evolution's startup notices from its `EvolutionAPI` contact, with no WhatsApp source ID. One greeting reply to this provider conversation was accepted by Chatwoot and marked sent; this is **not** evidence of a real customer WhatsApp reply.

Added a narrowly matched normalization filter for that exact English startup notice, sender name and absent source ID on WhatsApp channels. Real messages from the same sender or source-backed messages remain eligible. Preserved and suppressed 85 already queued notices in the verified provider conversation; no customer history was deleted. Regression result: 227 backend tests and Ruff passed.

Evolution reports repeated stream conflicts with type `replaced`. Instance inspection confirmed that `salma-el-nour-furniture` and the pilot instance share one WhatsApp identity and alternate connected/disconnected states. Neither instance was logged out or deleted. Resolving the older instance's session requires owner authorization because it may serve another integration. A new real customer message and its WhatsApp reply still need end-to-end verification after the session conflict is resolved.

## Session repair follow-up

The owner approved disconnecting the older instance. Its logout request returned a provider error; subsequent checks showed it remained closed throughout a bounded observation window, so the shared identity alone did not establish the cause of the pilot's continued conflicts. No instance was deleted.

Restarted `evolution_api` to clear stale sockets after confirming the pilot was its only connected instance. Following restart at `2026-10-10T19:53:59Z`, the pilot reconnected to `open`, the older instance remained `close`, and no `replaced` stream conflicts appeared during follow-up checks. This supports stale concurrent connections as the immediate fault; the exact origin of those sockets is unconfirmed. A fresh real customer message is still required for end-to-end verification.
