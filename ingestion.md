# 3. Ingestion Pipeline

This section builds an ingestion service/API for the metadata SDK from `sdk.md`.

The service does four core jobs:

1. Receives logs from the SDK.
2. Validates and parses single-event or batched payloads.
3. Extracts useful metadata into queryable columns.
4. Stores the processed event data in a database.

The implementation below uses **Node.js**, **Express**, and **SQLite** for local development. The same API shape can later be backed by Postgres, ClickHouse, BigQuery, or a queue plus worker pipeline.

---

## 1. Ingestion Flow

```text
Application
   |
   v
LLM metadata SDK
   |
   | POST /llm-events
   | Authorization: Bearer <LOG_INGESTION_KEY>
   v
Ingestion API
   |
   | validate payload
   | normalize timestamps
   | extract provider/model/status/token/latency metadata
   | preserve raw event JSON
   v
SQLite database
```

The endpoint accepts both shapes used by the SDK:

```json
{
  "eventId": "evt_123",
  "provider": "openai",
  "model": "gpt-4.1-mini",
  "status": "success",
  "startedAt": "2026-05-25T04:00:00.000Z",
  "endedAt": "2026-05-25T04:00:01.250Z",
  "latencyMs": 1250
}
```

or a batch:

```json
{
  "events": [
    {
      "eventId": "evt_123",
      "provider": "openai",
      "model": "gpt-4.1-mini",
      "status": "success",
      "startedAt": "2026-05-25T04:00:00.000Z",
      "endedAt": "2026-05-25T04:00:01.250Z",
      "latencyMs": 1250
    }
  ]
}
```

---

## 2. Project Setup

From:

```powershell
C:\Users\Arpan Mondal\assesment
```

create a new service folder:

```powershell
mkdir llm-ingestion-service
cd llm-ingestion-service
npm init -y
npm pkg set type=module
npm pkg set scripts.start="node server.js"
npm pkg set scripts.dev="node --watch server.js"
npm install express dotenv cors better-sqlite3
```

Create this file structure:

```text
llm-ingestion-service/
  .env
  .gitignore
  package.json
  server.js
```

Create `.gitignore`:

```gitignore
node_modules
.env
*.sqlite
*.sqlite-shm
*.sqlite-wal
npm-debug.log
```

Create `.env`:

```env
PORT=3001
LOG_INGESTION_KEY=replace_with_a_long_random_secret
DATABASE_PATH=./llm-events.sqlite
MAX_EVENTS_PER_REQUEST=100
```

The SDK should use the same `LOG_INGESTION_KEY` as its `apiKey`.

---

## 3. Database Schema

The service stores a processed row per event.

Important design choices:

- `event_id` is the primary key, so retries do not create duplicate rows.
- frequently queried fields are stored as columns.
- the original payload is preserved as `raw_event_json`.
- flexible metadata is stored as JSON text for local SQLite.

```sql
CREATE TABLE IF NOT EXISTS llm_inference_events (
  event_id TEXT PRIMARY KEY,
  provider TEXT NOT NULL,
  model TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('success', 'error')),
  error_type TEXT,
  error_message TEXT,
  started_at TEXT NOT NULL,
  ended_at TEXT NOT NULL,
  latency_ms INTEGER NOT NULL,
  session_id TEXT,
  conversation_id TEXT,
  request_id TEXT,
  input_preview TEXT,
  output_preview TEXT,
  input_preview_length INTEGER NOT NULL DEFAULT 0,
  output_preview_length INTEGER NOT NULL DEFAULT 0,
  input_tokens INTEGER,
  output_tokens INTEGER,
  total_tokens INTEGER,
  has_error INTEGER NOT NULL,
  metadata_json TEXT NOT NULL,
  metadata_keys_json TEXT NOT NULL,
  raw_event_json TEXT NOT NULL,
  client_ip TEXT,
  user_agent TEXT,
  received_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS llm_events_started_at_idx
  ON llm_inference_events (started_at);

CREATE INDEX IF NOT EXISTS llm_events_session_id_idx
  ON llm_inference_events (session_id);

CREATE INDEX IF NOT EXISTS llm_events_conversation_id_idx
  ON llm_inference_events (conversation_id);

CREATE INDEX IF NOT EXISTS llm_events_provider_model_idx
  ON llm_inference_events (provider, model);

CREATE INDEX IF NOT EXISTS llm_events_status_idx
  ON llm_inference_events (status);
```

---

## 4. Complete Ingestion API

Create `server.js`:

```js
import "dotenv/config";
import cors from "cors";
import express from "express";
import Database from "better-sqlite3";

const app = express();

const PORT = Number(process.env.PORT || 3001);
const LOG_INGESTION_KEY = process.env.LOG_INGESTION_KEY || "";
const DATABASE_PATH = process.env.DATABASE_PATH || "./llm-events.sqlite";
const MAX_EVENTS_PER_REQUEST = Number(process.env.MAX_EVENTS_PER_REQUEST || 100);
const MAX_PREVIEW_CHARS = 1000;
const MAX_ERROR_MESSAGE_CHARS = 2000;

const db = new Database(DATABASE_PATH);
db.pragma("journal_mode = WAL");

db.exec(`
  CREATE TABLE IF NOT EXISTS llm_inference_events (
    event_id TEXT PRIMARY KEY,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('success', 'error')),
    error_type TEXT,
    error_message TEXT,
    started_at TEXT NOT NULL,
    ended_at TEXT NOT NULL,
    latency_ms INTEGER NOT NULL,
    session_id TEXT,
    conversation_id TEXT,
    request_id TEXT,
    input_preview TEXT,
    output_preview TEXT,
    input_preview_length INTEGER NOT NULL DEFAULT 0,
    output_preview_length INTEGER NOT NULL DEFAULT 0,
    input_tokens INTEGER,
    output_tokens INTEGER,
    total_tokens INTEGER,
    has_error INTEGER NOT NULL,
    metadata_json TEXT NOT NULL,
    metadata_keys_json TEXT NOT NULL,
    raw_event_json TEXT NOT NULL,
    client_ip TEXT,
    user_agent TEXT,
    received_at TEXT NOT NULL
  );

  CREATE INDEX IF NOT EXISTS llm_events_started_at_idx
    ON llm_inference_events (started_at);

  CREATE INDEX IF NOT EXISTS llm_events_session_id_idx
    ON llm_inference_events (session_id);

  CREATE INDEX IF NOT EXISTS llm_events_conversation_id_idx
    ON llm_inference_events (conversation_id);

  CREATE INDEX IF NOT EXISTS llm_events_provider_model_idx
    ON llm_inference_events (provider, model);

  CREATE INDEX IF NOT EXISTS llm_events_status_idx
    ON llm_inference_events (status);
`);

const insertEvent = db.prepare(`
  INSERT INTO llm_inference_events (
    event_id,
    provider,
    model,
    status,
    error_type,
    error_message,
    started_at,
    ended_at,
    latency_ms,
    session_id,
    conversation_id,
    request_id,
    input_preview,
    output_preview,
    input_preview_length,
    output_preview_length,
    input_tokens,
    output_tokens,
    total_tokens,
    has_error,
    metadata_json,
    metadata_keys_json,
    raw_event_json,
    client_ip,
    user_agent,
    received_at
  ) VALUES (
    @eventId,
    @provider,
    @model,
    @status,
    @errorType,
    @errorMessage,
    @startedAt,
    @endedAt,
    @latencyMs,
    @sessionId,
    @conversationId,
    @requestId,
    @inputPreview,
    @outputPreview,
    @inputPreviewLength,
    @outputPreviewLength,
    @inputTokens,
    @outputTokens,
    @totalTokens,
    @hasError,
    @metadataJson,
    @metadataKeysJson,
    @rawEventJson,
    @clientIp,
    @userAgent,
    @receivedAt
  )
  ON CONFLICT(event_id) DO NOTHING
`);

const insertEvents = db.transaction((events) => {
  let inserted = 0;
  let duplicates = 0;

  for (const event of events) {
    const result = insertEvent.run(event);

    if (result.changes === 1) {
      inserted += 1;
    } else {
      duplicates += 1;
    }
  }

  return { inserted, duplicates };
});

app.set("trust proxy", true);
app.use(cors());
app.use(express.json({ limit: "2mb" }));

app.get("/health", (req, res) => {
  const row = db
    .prepare("SELECT COUNT(*) AS eventCount FROM llm_inference_events")
    .get();

  res.json({
    ok: true,
    databasePath: DATABASE_PATH,
    eventCount: row.eventCount,
  });
});

app.post("/llm-events", authenticate, (req, res) => {
  const parsed = parseEventsBody(req.body);

  if (!parsed.ok) {
    return res.status(400).json({
      error: "Invalid ingestion payload.",
      details: parsed.errors,
    });
  }

  const receivedAt = new Date().toISOString();
  const clientIp = req.ip || null;
  const userAgent = req.header("user-agent") || null;

  const processedEvents = parsed.events.map((event) =>
    processEvent(event, {
      clientIp,
      userAgent,
      receivedAt,
    }),
  );

  const result = insertEvents(processedEvents);

  return res.status(202).json({
    accepted: processedEvents.length,
    inserted: result.inserted,
    duplicates: result.duplicates,
  });
});

app.get("/metrics/summary", (req, res) => {
  const rows = db
    .prepare(`
      SELECT
        provider,
        model,
        COUNT(*) AS requests,
        SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END) AS errors,
        ROUND(AVG(latency_ms), 2) AS avgLatencyMs,
        SUM(COALESCE(total_tokens, 0)) AS totalTokens
      FROM llm_inference_events
      GROUP BY provider, model
      ORDER BY requests DESC
    `)
    .all();

  res.json({ rows });
});

app.get("/events/recent", (req, res) => {
  const limit = clampInteger(Number(req.query.limit || 20), 1, 100);

  const rows = db
    .prepare(`
      SELECT
        event_id AS eventId,
        provider,
        model,
        status,
        latency_ms AS latencyMs,
        session_id AS sessionId,
        conversation_id AS conversationId,
        total_tokens AS totalTokens,
        received_at AS receivedAt
      FROM llm_inference_events
      ORDER BY received_at DESC
      LIMIT ?
    `)
    .all(limit);

  res.json({ rows });
});

app.use((err, req, res, next) => {
  if (err instanceof SyntaxError && "body" in err) {
    return res.status(400).json({ error: "Request body must be valid JSON." });
  }

  console.error(err);
  return res.status(500).json({ error: "Internal ingestion error." });
});

app.listen(PORT, () => {
  console.log(`LLM ingestion API listening on http://localhost:${PORT}`);
});

function authenticate(req, res, next) {
  if (!LOG_INGESTION_KEY) {
    return res.status(500).json({
      error: "LOG_INGESTION_KEY is not configured on the ingestion service.",
    });
  }

  const expected = `Bearer ${LOG_INGESTION_KEY}`;
  const actual = req.header("authorization") || "";

  if (actual !== expected) {
    return res.status(401).json({ error: "Unauthorized." });
  }

  return next();
}

function parseEventsBody(body) {
  const events = Array.isArray(body?.events) ? body.events : [body];

  if (events.length === 0) {
    return { ok: false, errors: ["events must contain at least one item."] };
  }

  if (events.length > MAX_EVENTS_PER_REQUEST) {
    return {
      ok: false,
      errors: [`events must contain at most ${MAX_EVENTS_PER_REQUEST} items.`],
    };
  }

  const errors = [];

  events.forEach((event, index) => {
    validateEvent(event, index, errors);
  });

  if (errors.length > 0) {
    return { ok: false, errors };
  }

  return { ok: true, events };
}

function validateEvent(event, index, errors) {
  const label = `events[${index}]`;

  if (!isPlainObject(event)) {
    errors.push(`${label} must be an object.`);
    return;
  }

  requireString(event, "eventId", label, errors);
  requireString(event, "provider", label, errors);
  requireString(event, "model", label, errors);

  if (event.status !== "success" && event.status !== "error") {
    errors.push(`${label}.status must be "success" or "error".`);
  }

  requireIsoDate(event, "startedAt", label, errors);
  requireIsoDate(event, "endedAt", label, errors);

  if (!Number.isInteger(event.latencyMs) || event.latencyMs < 0) {
    errors.push(`${label}.latencyMs must be a non-negative integer.`);
  }

  optionalString(event, "errorType", label, errors, 200);
  optionalString(event, "errorMessage", label, errors, MAX_ERROR_MESSAGE_CHARS);
  optionalString(event, "sessionId", label, errors, 200);
  optionalString(event, "conversationId", label, errors, 200);
  optionalString(event, "requestId", label, errors, 200);
  optionalString(event, "inputPreview", label, errors, MAX_PREVIEW_CHARS);
  optionalString(event, "outputPreview", label, errors, MAX_PREVIEW_CHARS);

  if (event.tokenUsage !== undefined) {
    validateTokenUsage(event.tokenUsage, label, errors);
  }

  if (event.metadata !== undefined && !isPlainObject(event.metadata)) {
    errors.push(`${label}.metadata must be an object when provided.`);
  }

  if (isIsoDate(event.startedAt) && isIsoDate(event.endedAt)) {
    const started = Date.parse(event.startedAt);
    const ended = Date.parse(event.endedAt);

    if (ended < started) {
      errors.push(`${label}.endedAt must be after startedAt.`);
    }
  }
}

function validateTokenUsage(tokenUsage, label, errors) {
  if (!isPlainObject(tokenUsage)) {
    errors.push(`${label}.tokenUsage must be an object when provided.`);
    return;
  }

  optionalNonNegativeInteger(tokenUsage, "inputTokens", `${label}.tokenUsage`, errors);
  optionalNonNegativeInteger(tokenUsage, "outputTokens", `${label}.tokenUsage`, errors);
  optionalNonNegativeInteger(tokenUsage, "totalTokens", `${label}.tokenUsage`, errors);
}

function processEvent(event, requestMetadata) {
  const tokenUsage = isPlainObject(event.tokenUsage) ? event.tokenUsage : {};
  const metadata = isPlainObject(event.metadata) ? event.metadata : {};
  const inputPreview = event.inputPreview || null;
  const outputPreview = event.outputPreview || null;

  return {
    eventId: event.eventId,
    provider: event.provider,
    model: event.model,
    status: event.status,
    errorType: event.errorType || null,
    errorMessage: event.errorMessage || null,
    startedAt: new Date(event.startedAt).toISOString(),
    endedAt: new Date(event.endedAt).toISOString(),
    latencyMs: event.latencyMs,
    sessionId: event.sessionId || null,
    conversationId: event.conversationId || null,
    requestId: event.requestId || null,
    inputPreview,
    outputPreview,
    inputPreviewLength: inputPreview ? inputPreview.length : 0,
    outputPreviewLength: outputPreview ? outputPreview.length : 0,
    inputTokens: tokenUsage.inputTokens ?? null,
    outputTokens: tokenUsage.outputTokens ?? null,
    totalTokens: tokenUsage.totalTokens ?? null,
    hasError: event.status === "error" ? 1 : 0,
    metadataJson: JSON.stringify(metadata),
    metadataKeysJson: JSON.stringify(Object.keys(metadata).sort()),
    rawEventJson: JSON.stringify(event),
    clientIp: requestMetadata.clientIp,
    userAgent: requestMetadata.userAgent,
    receivedAt: requestMetadata.receivedAt,
  };
}

function requireString(object, field, label, errors) {
  if (typeof object[field] !== "string" || object[field].trim() === "") {
    errors.push(`${label}.${field} is required and must be a non-empty string.`);
  }
}

function optionalString(object, field, label, errors, maxLength) {
  if (object[field] === undefined) {
    return;
  }

  if (typeof object[field] !== "string") {
    errors.push(`${label}.${field} must be a string when provided.`);
    return;
  }

  if (object[field].length > maxLength) {
    errors.push(`${label}.${field} must be at most ${maxLength} characters.`);
  }
}

function requireIsoDate(object, field, label, errors) {
  if (!isIsoDate(object[field])) {
    errors.push(`${label}.${field} must be a valid ISO timestamp string.`);
  }
}

function isIsoDate(value) {
  return typeof value === "string" && !Number.isNaN(Date.parse(value));
}

function optionalNonNegativeInteger(object, field, label, errors) {
  if (object[field] === undefined) {
    return;
  }

  if (!Number.isInteger(object[field]) || object[field] < 0) {
    errors.push(`${label}.${field} must be a non-negative integer.`);
  }
}

function isPlainObject(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function clampInteger(value, min, max) {
  if (!Number.isInteger(value)) {
    return min;
  }

  return Math.min(max, Math.max(min, value));
}
```

---

## 5. What Metadata Is Extracted?

The SDK sends a flexible event object. The ingestion service stores two versions of it:

1. A normalized, query-friendly row.
2. The raw original event JSON.

The extracted fields are:

| Field | Why It Matters |
| --- | --- |
| `provider` | Compare OpenAI, Gemini, Anthropic, local models, etc. |
| `model` | Measure latency, errors, and token usage by model. |
| `status` | Separate successful calls from failed calls. |
| `latency_ms` | Build latency dashboards and alerts. |
| `started_at`, `ended_at`, `received_at` | Time-series analysis and ingestion delay checks. |
| `session_id`, `conversation_id`, `request_id` | Connect related events across a user journey. |
| `input_preview`, `output_preview` | Debug behavior without storing full prompts by default. |
| `input_tokens`, `output_tokens`, `total_tokens` | Estimate cost and usage. |
| `has_error`, `error_type`, `error_message` | Investigate failures. |
| `metadata_json`, `metadata_keys_json` | Preserve custom app metadata. |
| `client_ip`, `user_agent` | Basic operational debugging. |

Keeping `raw_event_json` is useful because the SDK schema may evolve. New fields are not lost even before the ingestion database has first-class columns for them.

---

## 6. Run the Service

Start the API:

```powershell
npm run dev
```

Expected output:

```text
LLM ingestion API listening on http://localhost:3001
```

Check health:

```powershell
Invoke-RestMethod -Uri "http://localhost:3001/health"
```

Send a test event:

```powershell
$body = @{
  events = @(
    @{
      eventId = "evt_test_1"
      provider = "openai"
      model = "gpt-4.1-mini"
      status = "success"
      startedAt = "2026-05-25T04:00:00.000Z"
      endedAt = "2026-05-25T04:00:01.250Z"
      latencyMs = 1250
      sessionId = "session_123"
      conversationId = "conversation_456"
      requestId = "request_789"
      inputPreview = "Summarize this support ticket."
      outputPreview = "The customer needs help resetting their password."
      tokenUsage = @{
        inputTokens = 32
        outputTokens = 18
        totalTokens = 50
      }
      metadata = @{
        feature = "support-summary"
        userPlan = "pro"
      }
    }
  )
} | ConvertTo-Json -Depth 10

Invoke-RestMethod `
  -Method Post `
  -Uri "http://localhost:3001/llm-events" `
  -Headers @{ Authorization = "Bearer replace_with_a_long_random_secret" } `
  -ContentType "application/json" `
  -Body $body
```

Expected response:

```json
{
  "accepted": 1,
  "inserted": 1,
  "duplicates": 0
}
```

If you run the same request again, `duplicates` should become `1` because `eventId` is idempotent.

Check recent events:

```powershell
Invoke-RestMethod -Uri "http://localhost:3001/events/recent"
```

Check summary metrics:

```powershell
Invoke-RestMethod -Uri "http://localhost:3001/metrics/summary"
```

---

## 7. Connect the SDK

The SDK from `sdk.md` should point to:

```text
http://localhost:3001/llm-events
```

Example SDK configuration:

```ts
const sdk = new LLMTracker({
  provider: "openai",
  model: "gpt-4.1-mini",
  ingestionUrl: "http://localhost:3001/llm-events",
  apiKey: process.env.LOG_INGESTION_KEY
});
```

The SDK queue sends batched events like this:

```json
{
  "events": []
}
```

The ingestion service also accepts a single event object, which is useful for early testing.

---

## 8. Validation Rules

The API rejects invalid payloads with `400`.

Required fields:

- `eventId`
- `provider`
- `model`
- `status`
- `startedAt`
- `endedAt`
- `latencyMs`

Accepted values:

- `status` must be `success` or `error`.
- `startedAt` and `endedAt` must be valid timestamp strings.
- `endedAt` must not be before `startedAt`.
- `latencyMs` must be a non-negative integer.
- token counts must be non-negative integers when present.
- previews are capped to prevent huge payloads.
- `metadata` must be an object when provided.
- a batch can contain at most `MAX_EVENTS_PER_REQUEST` events.

Example validation error:

```json
{
  "error": "Invalid ingestion payload.",
  "details": [
    "events[0].status must be \"success\" or \"error\".",
    "events[0].latencyMs must be a non-negative integer."
  ]
}
```

---

## 9. Useful Queries

Average latency and token usage by model:

```sql
SELECT
  provider,
  model,
  COUNT(*) AS requests,
  ROUND(AVG(latency_ms), 2) AS avg_latency_ms,
  SUM(COALESCE(total_tokens, 0)) AS total_tokens
FROM llm_inference_events
GROUP BY provider, model
ORDER BY requests DESC;
```

Error rate by model:

```sql
SELECT
  provider,
  model,
  COUNT(*) AS requests,
  SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END) AS errors,
  ROUND(
    100.0 * SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END) / COUNT(*),
    2
  ) AS error_rate_percent
FROM llm_inference_events
GROUP BY provider, model
ORDER BY error_rate_percent DESC;
```

Slowest recent calls:

```sql
SELECT
  event_id,
  provider,
  model,
  latency_ms,
  session_id,
  conversation_id,
  started_at
FROM llm_inference_events
ORDER BY latency_ms DESC
LIMIT 20;
```

Token usage by conversation:

```sql
SELECT
  conversation_id,
  COUNT(*) AS requests,
  SUM(COALESCE(total_tokens, 0)) AS total_tokens
FROM llm_inference_events
WHERE conversation_id IS NOT NULL
GROUP BY conversation_id
ORDER BY total_tokens DESC
LIMIT 20;
```

---

## 10. Production Upgrade Path

SQLite is good for local development and small demos. For production, use this architecture:

```text
SDK
  -> Ingestion API
  -> Durable queue
  -> Worker
  -> Analytics database
```

Recommended improvements:

- Use Postgres for normal application dashboards.
- Use ClickHouse, BigQuery, or Snowflake for high-volume analytics.
- Put Kafka, RabbitMQ, SQS, or another queue between the API and database.
- Add rate limiting per API key or project.
- Add tenant/project IDs if multiple teams send logs.
- Use HTTPS in production.
- Rotate ingestion keys.
- Store only previews by default, not full prompts or full outputs.
- Add retention and deletion policies.
- Add migrations instead of creating tables inside `server.js`.
- Add request tracing with a `requestId`.
- Add dashboards for latency, error rate, token usage, and estimated cost.

---

## 11. Assessment Summary

This ingestion pipeline exposes a secure `/llm-events` API for the SDK. It authenticates SDK requests with a bearer token, accepts single or batched log events, validates required fields and data types, normalizes timestamps, extracts useful metadata such as provider, model, status, latency, token usage, previews, session IDs, and error information, then stores the processed event in SQLite. The raw event JSON is also retained so the schema can evolve without losing data.

The result is a practical observability backend for LLM calls:

```text
SDK log event -> validate -> normalize -> extract metadata -> store in database -> query metrics
```
