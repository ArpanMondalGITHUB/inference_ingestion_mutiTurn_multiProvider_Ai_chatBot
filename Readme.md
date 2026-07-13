# Multi-Provider LLM Chat + Inference Logging

A full-stack chat application that talks to **Anthropic, OpenAI, and Gemini** behind a
single API, streams responses token-by-token, persists conversations, and records a
structured telemetry event for every LLM call.

- **Frontend** — React 19 + Vite + TailwindCSS, served by nginx.
- **Backend** — FastAPI (Python 3.13), pluggable provider layer, SQLite storage.
- **Observability** — every inference emits an `llm_inference_event` that is ingested,
  validated, and stored for later analytics.
- **Durable telemetry** — events are published to a **Redis Stream** and drained by a
  background consumer with per-message acknowledgement, crash-safe replay, and a
  dead-letter queue, so telemetry survives restarts instead of being lost in memory.

---

## 1. Setup

### Option A — Docker Compose (recommended)

Requirements: Docker + Docker Compose.

```bash
# 1. Add your provider keys
cp server/src/.env.example server/src/.env   # then edit, or create the file directly
# server/src/.env must contain at least one provider key (see "Environment" below)

# 2. Build and run
docker compose up --build
```

- Frontend → <http://localhost:8080>
- The backend is **not** published to the host; nginx reverse-proxies `/api/*` to it on
  the internal Docker network. To reach it directly during development, uncomment the
  `ports:` block under the `backend` service in `docker-compose.yml`.
- The SQLite database is persisted in the named volume `backend_data`, so it survives
  restarts and rebuilds.

### Option B — Run each service locally

**Backend**

```bash
cd server
poetry install
# create server/src/.env (see Environment below)
poetry run uvicorn server:app --app-dir src --host 0.0.0.0 --port 8000 --reload
```

**Frontend**

```bash
cd chatui
pnpm install
# chatui/.env -> VITE_API_URL=http://127.0.0.1:8000
pnpm run dev        # http://localhost:5173
```

### Environment

`server/src/.env` (read by `src/core/config.py`):

| Variable | Purpose | Default |
|---|---|---|
| `GEMINI_API_KEY` / `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` | Provider credentials (configure at least one) | — |
| `AI_DEFAULT_PROVIDER` | Provider used when the request omits one | `gemini` |
| `GEMINI_MODEL` / `OPENAI_MODEL` / `ANTHROPIC_MODEL` | Default model per provider | see `config.py` |
| `*_MODELS` (CSV) | Extra selectable models per provider | default only |
| `DATABASE_PATH` | SQLite file location | — (required) |
| `CORS_ORIGINS` (CSV) | Allowed browser origins | from `FRONTEND_URL` |
| `LLM_LOGGING_ENABLED` | Toggle telemetry emission | `true` |
| `LLM_INGESTION_URL` | Where the tracker POSTs events (usually this service's own `/llm-events`) | — |
| `LOG_INGESTION_KEY` | Bearer token shared between emitter and `/llm-events` | — |
| `MAX_EVENTS_PER_REQUEST` | Batch cap for ingestion | `100` |
| `MAX_CONTEXT_MESSAGES` | History window sent to the model | `8` |
| `REDIS_URL` | Redis connection for the event broker | `redis://localhost:6379/0` |
| `EVENT_BROKER_ENABLED` | Start the stream consumer on boot | `true` |
| `EVENT_STREAM_KEY` | Stream key events are published to | `llm-events` |
| `EVENT_STREAM_GROUP` | Consumer group name | `ingest` |
| `EVENT_CONSUMER_NAME` | This consumer's name within the group | `consumer-1` |
| `EVENT_STREAM_MAXLEN` | Approx. max stream length (retention cap) | `100000` |
| `EVENT_DLQ_KEY` | Dead-letter stream for poison messages | `llm-events:dlq` |
| `EVENT_MAX_DELIVERIES` | Delivery attempts before dead-lettering | `5` |
| `EVENT_CLAIM_MIN_IDLE_MS` | Idle time before an un-acked entry is reclaimed | `30000` |

`chatui/.env`: `VITE_API_URL` — API base baked into the bundle at build time
(`/api` in Docker for same-origin proxying; a direct URL for local dev).

---

## 2. Architecture Overview

```
  Browser ──▶ nginx (:8080) ──/api/*──▶ FastAPI backend (:8000)

  ── chat path ──────────────────────────────────────────────────────────
  routes/run_ai_routes ─▶ services/ai ─▶ provider/* (anthropic / openai / gemini)
                                │
                                ▼
                         sdk/LLMTracker.track / track_stream
                                │  broker.publish(event)   (non-blocking, best-effort)
                                ▼
                         events/broker.py ──XADD──▶ ┌──────────────────────────┐
                                                    │  Redis Streams (:6379)   │
                                                    │  "llm-events"  (+ :dlq)  │
                                                    │  AOF-persisted, durable  │
                                                    └───────────┬──────────────┘
  ── ingest path (background consumer) ───────────────────────┼──────────────────
                         events/consumer.py ◀──XREADGROUP / XAUTOCLAIM
                                │  validate → process_event → insert_llm_event
                                │  XACK on success · dead-letter on poison
                                ▼
                         db/db.py ──▶ SQLite (conversations, messages, llm_inference_events)

  ── external path (still available) ────────────────────────────────────
  POST /llm-events  (Bearer auth) ──▶ validate + store   ← for external collectors
```

**Provider layer** — `provider/base.py` defines a `ChatProvider` protocol
(`chat`, `chat_stream`, `resolve_model`, `configured`). Each concrete provider adapts a
vendor SDK to a common `ProviderChatResult` (text + token usage). `registry.py` holds one
instance per provider and exposes lookup / "which providers are configured" helpers, so
adding a provider is: implement the protocol + register it.

**Chat flow** — `services/ai.py` loads recent history, trims it to a fixed window, calls
the selected provider (buffered `chat` or `chat_stream` SSE), persists the user +
assistant turns, and wraps the whole call in `LLMTracker` for telemetry.

**Key endpoints**

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/v1/api/chat` | One-shot completion |
| `POST` | `/v1/api/chat/stream` | Token stream (SSE: `start` / `chunk` / `done` / `error`) |
| `GET` | `/v1/api/providers` | Configured providers + selectable models |
| `GET`/`DELETE` | `/v1/api/conversations[/{id}]` | List / fetch / delete conversations |
| `POST` | `/llm-events` | Ingest telemetry (Bearer auth, batchable) |
| `GET` | `/llm-events[/{id}]` | Query stored events |
| `GET` | `/health` | Health + logging config |

---

## 3. Schema Design Decisions

Storage is **SQLite** with three tables (`server/llm-events.sql`), applied idempotently on
startup by `init_db()` (`CREATE TABLE IF NOT EXISTS` + a small column-add migration).

**`conversations` / `conversation_messages`** — the chat domain, normalized 1-to-many with
a `conversation_id` foreign key (`ON DELETE CASCADE`). Roles are constrained with a
`CHECK (role IN ('User','Assistant'))`. Message ordering relies on the autoincrement `id`
rather than the string timestamp, which keeps ordering stable even when two messages share
the same `created_at`.

**`llm_inference_events`** — one row per LLM call, deliberately **denormalized and
analytics-oriented**:

- **Wide, query-first columns.** `provider`, `model`, `status`, `latency_ms`,
  `*_tokens`, `session_id`, `conversation_id` are first-class columns (not buried in JSON)
  so dashboards can `GROUP BY provider, model` or filter on `status` cheaply. Indexes back
  the common access paths: `started_at`, `session_id`, `conversation_id`,
  `(provider, model)`, and `status`.
- **JSON kept as text.** SQLite has no native JSON type, so `metadata_json`,
  `metadata_keys_json`, and `raw_event_json` are stored as strings. `metadata_keys_json`
  is a denormalized, sorted key list so you can see *what* metadata exists without parsing
  the blob; `raw_event_json` is the full original payload, kept for forward-compatibility
  (new fields survive even before columns exist for them).
- **Precomputed derivations.** `has_error`, `input_preview_length`, and
  `output_preview_length` are stored rather than computed at read time.
- **Idempotent writes.** Inserts use `INSERT OR IGNORE` keyed on `event_id`, so a retried
  or duplicated delivery of the same event is a no-op — safe with at-least-once emission.
- **Timestamps as ISO-8601 strings** (UTC, `Z`-normalized) rather than a native type,
  matching JSON-over-the-wire and keeping the emitter and store on identical formats.

---

## 4. Tradeoffs Made

- **SQLite over Postgres.** Zero-config, file-backed, trivial to ship in one container —
  ideal for an assessment / single-node deploy. Cost: one writer at a time, no network
  concurrency, JSON stored as opaque text.
- **Non-blocking telemetry over a durable broker.** `LLMTracker` still emits off the
  request path via `asyncio.create_task`, but now `publish()` writes to a **Redis Stream**
  instead of an in-memory HTTP POST, so **logging can never slow down or break a chat
  response** *and* an emitted event is durable the moment it reaches Redis. Cost: one more
  container to run, and a small lag between "chat done" and "row in SQLite" while the
  consumer drains (see Failure Handling in `ARCHITECTURE.md`).
- **In-process consumer.** The stream is drained by a background task inside the same
  FastAPI process (lightest option for a single-node deploy). Simple to run; not fully
  isolated — a spike in inference and a spike in ingestion still share one process. The
  seam is ready to split into a separate worker container (`EVENT_BROKER_ENABLED=false` on
  the web service, `true` on the worker).
- **HTTP `/llm-events` retained.** The endpoint still exists for external collectors, so
  the emitter can be pointed elsewhere with no code change.
- **Fixed context window (`MAX_CONTEXT_MESSAGES = 8`).** Predictable token cost and latency
  with no summarization step. Cost: older context is silently dropped, no long-term memory.
- **Bounded previews, not full payloads.** Inputs/outputs are truncated (~300 chars by the
  tracker, hard-capped at 1000 by the model) to limit row size and PII exposure. Cost: the
  stored event is not a faithful transcript.
- **Provider protocol over inheritance.** A structural `Protocol` keeps providers
  decoupled and easy to add, at the cost of no shared base implementation.

---

## 5. What I'd Improve With More Time

- **Move to Postgres** for real concurrency, native `jsonb` columns + GIN indexes, and
  retention/partitioning on the events table.
- **Durable event delivery — done.** In-memory fire-and-forget was replaced with a
  **Redis Streams** broker + background consumer (`events/broker.py`, `events/consumer.py`):
  per-message `XACK`, `XAUTOCLAIM` replay of un-acked entries on crash, and a dead-letter
  queue for poison messages. Next steps here: split the consumer into its own worker
  container and honor `EVENT_MAX_DELIVERIES` for DLQ decisions.
- **Fix `event_id` type affinity.** It's declared `INTEGER PRIMARY KEY` but holds UUID
  strings; SQLite tolerates this via flexible typing, but the column should be `TEXT`.
- **Capture streaming token usage.** The streaming path records `chunkCount` /
  `timeToFirstChunkMs` but not token counts — wire up the providers' usage events.
- **Harden the API.** Auth + rate limiting on chat routes, request size limits, and
  per-session quotas. Enable SQLite `PRAGMA foreign_keys=ON` and `journal_mode=WAL`
  (FKs currently aren't enforced, so deletes cascade manually in code).
- **Tests + CI.** Unit tests for the provider adapters and ingestion validation, plus an
  end-to-end streaming test; wire into CI with lint/type-check.
- **Observability UI.** A small dashboard over `llm_inference_events` (latency percentiles,
  error rate, cost per provider/model).

See **[`ARCHITECTURE.md`](./ARCHITECTURE.md)** for the ingestion flow, logging strategy,
scaling, and failure-handling assumptions.
