# Architecture Notes

Companion to the [README](./Readme.md). Focuses on the telemetry subsystem: how inference
events flow, how they're logged, how the design scales, and what it assumes about failure.

---

## 1. Ingestion Flow

Every LLM call is wrapped by `LLMTracker` (`server/src/sdk/llm_event_tracker.py`), which
both executes the call and produces a telemetry event. The event then travels over HTTP to
the ingestion endpoint and lands in SQLite.

```
run_assistant / stream_assistant  (services/ai.py)
        │  builds request_id, session_id, conversation_id, metadata
        ▼
LLMTracker.track(...) / track_stream(...)
        │  times the call, captures status/tokens/preview,
        │  builds an LLMInferenceEvent
        ▼
_send_soon()  ──▶  asyncio.create_task(_send_event())     ← non-blocking, decoupled
        │            POST {LLM_INGESTION_URL}  (Bearer LOG_INGESTION_KEY, 2s timeout)
        ▼
POST /llm-events   (routes/llm_event_routes.py)
        │  1. authenticate  — Bearer must equal LOG_INGESTION_KEY
        │  2. size guard    — Content-Length + body <= MAX_INGESTION_BODY_BYTES (2 MB)
        │  3. parse         — accepts a single event OR { "events": [...] } (<= 100)
        │  4. validate      — Pydantic LLMInferenceEvent (types, ISO timestamps,
        │                     endedAt >= startedAt); invalid batch -> 400 with details
        │  5. enrich        — clientIp, userAgent, receivedAt; derive preview lengths,
        │                     hasError, sorted metadataKeys
        ▼
insert_llm_event()  ──▶  INSERT OR IGNORE INTO llm_inference_events   (idempotent on event_id)
        │
        ▼
HTTP 202 Accepted  { "ok": true, "accepted": N }
```

Design points:

- **Emitter and store are the same service.** `LLM_INGESTION_URL` normally points back at
  this app's own `/llm-events`, so telemetry rides the same HTTP boundary an external
  collector would — the emitter can later be pointed at a separate service with no code
  change.
- **`202 Accepted`, not `200`.** Ingestion acknowledges receipt, not durability of any
  downstream processing — honest semantics for a telemetry sink.
- **Batch or single.** A bare event object and a `{ "events": [...] }` batch are both
  accepted; batches are capped at `MAX_EVENTS_PER_REQUEST` (100) and validated per-item so
  one bad event reports a precise `events[i].field` error.
- **Idempotent by `event_id`.** `INSERT OR IGNORE` makes re-delivery safe, which is what
  lets the emitter be fire-and-forget / at-least-once without creating duplicates.

---

## 2. Logging Strategy

**Non-blocking by construction.** `_send_soon()` schedules delivery with
`asyncio.create_task` and returns immediately; the chat response never waits on logging.
`_send_event()` uses a short 2s HTTP timeout and wraps everything in a bare `except: pass`,
so a slow or down collector cannot degrade or fail a user's chat.

**What each event captures:**

- **Identity & routing** — `provider`, `model`, `eventId`, and the correlation keys
  `requestId`, `sessionId`, `conversationId` that stitch a telemetry row back to a
  conversation and a single request.
- **Outcome** — `status` (`success` / `error`), and on failure `errorType` +
  `errorMessage`.
- **Performance** — `latencyMs` for all calls; streaming additionally records
  `chunkCount` and `timeToFirstChunkMs` (TTFB) in metadata.
- **Cost** — `inputTokens` / `outputTokens` / `totalTokens` when the provider returns
  usage (non-streaming path today).
- **Content, bounded** — `inputPreview` / `outputPreview` are whitespace-collapsed and
  truncated (~300 chars) to keep rows small and limit PII; full transcripts are **not**
  logged. The complete original event is retained as `raw_event_json` for debugging.

**Two-layer validation.** The emitter constructs a typed `LLMInferenceEvent`, and the
ingestion endpoint **re-validates** the same model on receipt — the collector never trusts
its input, even when the input is itself. Storage then normalizes: JSON fields serialized
to text, timestamps normalized to UTC `Z` form, booleans to `0/1`.

**Toggle & auth.** `LLM_LOGGING_ENABLED=false` (or a missing `LLM_INGESTION_URL`) turns
emission off cleanly at `_send_soon`. Ingestion is gated by a Bearer token
(`LOG_INGESTION_KEY`); if the key isn't configured the endpoint fails closed with `500`
rather than accepting unauthenticated data.

---

## 3. Scaling Considerations

**Where it holds up today**

- Telemetry is off the request's critical path, so inference throughput is bounded by the
  providers, not by logging.
- The events table is indexed for the queries that matter at scale (`started_at`,
  `session_id`, `conversation_id`, `(provider, model)`, `status`), so read/analytics
  patterns stay index-backed as row count grows.
- Stateless request handling (identity comes from the payload / `session_id`) means the
  FastAPI layer itself can run behind more workers without shared in-process state.

**Where it becomes the bottleneck**

- **SQLite is the ceiling.** A single writer with file-level locking caps concurrent
  ingestion + chat writes. First scaling move: **Postgres** (connection pool, concurrent
  writers, `jsonb`, partitioning/retention on the events table).
- **Per-event HTTP POST.** One request per event is fine at low volume; at high volume it
  should become **client-side batching** (the endpoint already accepts batches of 100) and
  ideally a **queue/buffer** (Kafka, SQS, Redis Stream) between emitter and store, with the
  writer draining in bulk.
- **Unbounded `asyncio.create_task`.** Under a burst, tasks accumulate with no backpressure
  or concurrency cap. A bounded worker pool / queue would prevent event-loop pressure.
- **Split the collector out.** Co-locating emit + ingest shares one process's resources;
  extracting `/llm-events` into its own service lets telemetry and chat scale independently.

---

## 4. Failure Handling Assumptions

**Telemetry is best-effort; chat is authoritative.** The core assumption is that losing a
telemetry event is acceptable, but breaking a chat response is not. Concretely:

- **Logging failures are swallowed.** Network errors, timeouts (2s), and collector 4xx/5xx
  are caught and ignored in `_send_event`; the user's chat is unaffected. **Assumption:**
  occasional gaps in telemetry are tolerable — this is not an audit log.
- **Events can be lost.** Because delivery is in-memory fire-and-forget, a process crash or
  restart drops any un-sent tasks. There is no local buffer, retry, or replay. **Assumption:**
  no durability guarantee on telemetry (the fix is a durable queue — see README §5).
- **At-least-once, deduped by design.** If retries were added, `INSERT OR IGNORE` on
  `event_id` already makes redelivery idempotent, so the store is safe under duplicate
  delivery.
- **Chat-path errors are surfaced, not hidden.** In contrast to logging, a provider failure
  during chat is recorded as an `error` event *and* propagated: `run_assistant` maps
  provider exceptions to HTTP status codes (`400` unknown provider/model, `503` not
  configured, `502` upstream failure); the streaming path emits a terminal SSE `error`
  event so the client is always told.
- **Empty model output is a hard failure.** A stream that yields no text raises rather than
  persisting a blank assistant turn — the assistant message is only written after a
  non-empty result, so the conversation store never contains empty replies.
- **Ingestion fails closed on misconfiguration.** A missing `LOG_INGESTION_KEY` returns
  `500` (refuse unauthenticated writes); oversized or malformed bodies return `413` / `400`
  with detail rather than being partially stored.
- **Startup is idempotent.** `init_db()` uses `CREATE TABLE IF NOT EXISTS` plus a guarded
  column-add migration, so repeated boots (and container restarts against the persisted
  volume) are safe and non-destructive.

**Known gap:** SQLite foreign keys aren't enforced by default, so `conversation_messages`
cleanup is done explicitly in `delete_conversation_db()` rather than relying on
`ON DELETE CASCADE`. Enabling `PRAGMA foreign_keys=ON` would let the schema enforce it.
