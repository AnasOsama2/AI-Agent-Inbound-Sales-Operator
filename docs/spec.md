# Salma MVP V1 — Refined Build Plan

## Summary

Build a reliable inbound assistant for one fictional Egyptian AI/software agency using **FastAPI, React, Python 3.14, LangGraph, PostgreSQL/pgvector, and Groq `qwen/qwen3.8-27b`**.

V1 answers grounded business questions, quotes verified prices, collects lead requirements, and hands conversations to staff. Start locally with Docker; connect live channels after the simulator and safety evaluations pass.

## Architecture and interfaces

- Route WhatsApp through **Evolution → Chatwoot → Salma**. Instagram and Messenger also reach Salma through Chatwoot. Send all live replies through Chatwoot, with no parallel direct Evolution sending path.
- Implement `ChatwootAdapter` and `SimulatorAdapter`. Keep channel-specific payloads outside the agent.
- Normalize messages into a shared schema containing channel, provider/inbox identity, external message and conversation IDs, customer identity, content type, text, timestamp, and metadata.
- Authenticate incoming integrations, ignore private notes and bot echoes, and distinguish customer messages from human replies.
- Persist inbound messages before acknowledging webhooks. Deduplicate repeated deliveries and process each conversation sequentially through a durable worker.
- Persist outbound work and delivery status. Ambiguous delivery failures require reconciliation rather than blindly resending.

Use one LangGraph agent with a **bounded tool loop**, followed by a structured decision call. Groq currently documents that structured output cannot be combined with tool use in the same call. [Groq documentation](https://console.groq.com/docs/structured-outputs)

## Agent, knowledge, and trust

- Tools: knowledge search, service lookup, restricted calculator, current-customer lookup/update, lead-request creation/update, and confirmed draft-request archival.
- Supply customer identity from backend context. Expose typed operations rather than arbitrary SQL; prohibit changes to catalog prices, policies, and other customers’ records.
- Use the calculator for arithmetic over approved catalog values. Discounts, orders, payments, and confirmed bookings remain unsupported.
- Return validated decisions containing intent, language, service interest, lead signals, evidence references, reply content, and handoff information.
- Render monetary claims in Python from current database values. Quote-only services never receive invented amounts.
- Require supporting evidence for business claims. Missing facts, serious complaints, human requests, unauthorized discounts, or failed validation produce clarification or handoff as appropriate. Self-reported confidence is only a diagnostic signal.
- Calculate lead scores in Python using the draft’s weights and temperature thresholds. Maintain conversation-level signals without awarding points repeatedly for repeated messages.

Knowledge ingestion:

- Load staff-controlled, text-based PDFs, preserve document/page references, recursively chunk within each embedding model’s tokenizer limit, then store vectors in pgvector.
- Use `all-MiniLM-L6-v2` for English and `paraphrase-multilingual-MiniLM-L12-v2` for Arabic in separate partitions.
- Query each partition using its corresponding model. For mixed-language requests, retrieve from both and combine evidence without comparing incompatible vectors.
- Detect extraction failures; scanned PDFs and OCR remain outside V1.
- Seed matching Arabic/English business knowledge and an SQL catalog. Include the **500 EGP consultation** and quote-only custom projects; remove inconsistent course examples.

## Ownership, console, and monitoring

Use one ownership state machine:

`AI_ACTIVE → HANDOFF_PENDING → HUMAN_ACTIVE → CLOSED`

- Handoff persists the reason, factual summary, requirements, service interest, lead temperature, and suggested reply.
- Send one handoff acknowledgment, then suppress AI replies.
- Recheck ownership immediately before sending, preventing an in-flight AI response after takeover.
- Only an authenticated staff action resumes AI. Closing or receiving another customer message does not automatically resume it.
- Collect booking preferences and quote requirements, then hand off. Never claim a reservation or quote is confirmed.
- Support text, emojis, Egyptian Arabic, English, and mixed language. Unsupported media receives a localized request for text.

React provides a simulator and inspection console: conversation history, sources, tools, decisions, guardrails, lead state, ownership, and live events. Staff answer customers in Chatwoot.

Record graph transitions, observable state changes, tool activity, validation, ownership, delivery, errors, timings, and token usage with correlation IDs. Redact credentials and restrict customer-data access; logs do not require private model reasoning.

## Milestones and acceptance

1. **Foundation:** verify Python 3.14 dependency compatibility, Docker startup, migrations, mock catalog, bilingual PDF ingestion, and Groq tool/decision calls.
2. **Brain and trust:** implement the agent, customer-scoped tools, backend-rendered prices, scoring, handoff, and failure handling.
3. **Simulator:** add React chat, inspection, ownership controls, and event streaming.
4. **Live channels:** verify Evolution–Chatwoot bridging, then WhatsApp, Instagram, and Messenger, including human takeover.
5. **Alpha:** test with 5–10 people and record reviewed outcomes.

Retain the six original success targets, measured against labeled evaluations. The initial 40 conversations are a regression suite, not proof of general reliability.

Test duplicate webhooks, rapid messages, restarts, bot echoes, cross-customer access, draft archival confirmation, missing evidence, Arabic retrieval, mixed language, pricing attacks, calculator misuse, provider failures, and takeover during generation.

Require zero fabricated prices, zero unauthorized discounts, and every explicit human-request fixture to create a handoff. Report quality rates with their sample counts; distinguish attempted sending from confirmed delivery.

Defaults: one business, no automatic cross-channel customer merging, local CPU embeddings, and no V2 features. Keep secrets in environment variables; rotate the exposed Groq key. Qwen 3.8 is currently a preview model, so make its ID configurable and verify availability before alpha without silently substituting another model. [Groq model catalog](https://console.groq.com/docs/models)
