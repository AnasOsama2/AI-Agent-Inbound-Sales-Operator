# Salma monitoring

Open **Monitoring** in the console for workspace processing, delivery, latency, handoffs,
and token totals over the last 24 hours. Backlog counts include older pending work.
Decision statistics sample at most the newest 10,000 decisions in that window. Test Chat
is included so failures can be inspected during development. System health, worker/API
process CPU and memory, and the LangSmith project link require the platform operator role.
CPU percentages are sampled between reads; the initial sample can be zero. These are
process resources, not total host or Docker resource consumption.

Open a conversation's **Activity → Flow completed** for ordered stages, timings, inputs
and outputs. Rejected knowledge answers preserve the draft, selected evidence, validator
verdict and explanation before the decision becomes a handoff. The validator explains
its factual check; provider chain-of-thought is neither requested nor stored.

Worker stdout and API stdout emit JSON flow records with message ID, business ID,
configuration version, and correlation ID. Events emitted during database transactions
are labelled `persistence=staged`; the database Activity record is the durable source.
Delivery spans use the original inbound correlation ID and continue the generation trace.
An independent worker heartbeat updates Redis every five seconds; over 90 seconds old
is stale. Last completed tick is also recorded to distinguish liveness from progress.

```powershell
docker compose logs --since 10m api worker
```

Docker rotates each API/worker log at 10 MB and retains three files. Saved conversation
diagnostics remain in the existing Event table and follow its existing retention policy;
no new automatic deletion policy is introduced.

## LangSmith

Configure these in the running checkout's ignored `.env`, then recreate API and worker:

```dotenv
LANGSMITH_TRACING=true
LANGSMITH_API_KEY=<application tracing key>
LANGSMITH_PROJECT=salma
LANGSMITH_ENDPOINT=https://api.smith.langchain.com
LANGSMITH_PROJECT_URL=https://smith.langchain.com/o/<organization>/projects/p/<project>
```

The explicit LangSmith spans cover processing, retrieval, tools, model calls, structured
decisions, validation, rendering, and delivery. Implicit LangGraph state export is disabled
to keep all exports on the redacted client. Nested JSON tool arguments are sanitized.
Credentials, structured names/contact fields, recognizable emails and telephone numbers
are redacted before structured stdout and trace export. This is pattern-based redaction,
not a general free-text personal-data classifier. Strings, lists, nesting and stage counts
are bounded; very large inputs may be truncated. Do not place secrets in business knowledge.

LangSmith outages do not decide customer behavior. Local Activity diagnostics remain
available and `telemetry_unavailable` records identify SDK failures. `configured` means
settings are present, not proof of successful export; verify a newly generated trace in
the project. Disabling `LANGSMITH_TRACING` preserves local diagnostics.

Existing historical handoffs cannot recover their missing draft or validation reason.
The detailed diagnostics apply to messages processed after this deployment.

SDK references: [manual traces](https://reference.langchain.com/python/langsmith/run_helpers/trace)
and [tracing context](https://reference.langchain.com/python/langsmith/run_helpers/tracing_context).
