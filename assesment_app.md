# Assessment App: LLM Chatbot with Inference Logging and Ingestion

Last updated: 2026-05-25

This document combines the ideas from:

- `multiturn_context_chatui.md`
- `sdk.md`
- `ingestion.md`

It describes a lightweight but complete LLM application that satisfies the assessment requirements:

- A browser chatbot with multi-turn conversation support.
- A short context window for the model request.
- A lightweight SDK/wrapper around the LLM call.
- Near real-time inference logging.
- An ingestion API that validates, parses, and stores logs.
- SQLite database tables for chat messages, inference logs, and extracted metadata.

The implementation uses Node.js, Express, the Google Gemini API, and SQLite through `better-sqlite3`.

If `gemini-3.5-flash` is not available in your account or region, replace it with another available Gemini text model such as `gemini-2.5-flash`.

---

## 1. System Overview

```text
Browser UI
   |
   | POST /api/chat
   v
Express app
   |
   | loads recent conversation messages from SQLite
   | wraps the Gemini API call with LLMTracker
   v
Gemini API
   |
   | model response
   v
Express app
   |
   | stores chat messages
   | queues inference metadata event
   v
LLMTracker queue
   |
   | POST /llm-events
   v
Ingestion endpoint
   |
   | validates event
   | extracts metadata columns
   | stores normalized row + raw JSON
   v
SQLite database
```

This is intentionally one deployable service for the assessment. In production, the ingestion API can be split into its own service and backed by Postgres, ClickHouse, BigQuery, or a queue plus worker.

---

## 2. Requirements Mapping

| Requirement | Implementation |
| --- | --- |
| Simple chatbot | Express backend plus static HTML/CSS/JS frontend |
| Foundation model API | Gemini API via `@google/genai` |
| Multi-turn conversations | Conversation IDs and recent messages loaded from SQLite |
| Short conversational context | `MAX_CONTEXT_MESSAGES` limits how many previous messages are sent |
| Lightweight SDK/wrapper | `src/llmTracker.js` wraps each model call |
| Metadata captured | provider, model, latency, status, errors, token usage, timestamps, IDs, previews |
| Near real-time logs | In-memory queue flushes to `/llm-events` every second or by batch size |
| Ingestion API | `POST /llm-events` accepts single events or batches |
| Validation/parsing | Required fields, timestamp checks, status checks, preview limits, token checks |
| Database storage | `conversations`, `chat_messages`, and `llm_inference_events` tables |

---

## 3. Project Structure

```text
llm-observability-chatbot/
  .env
  .env.example
  .gitignore
  package.json
  server.js
  src/
    llmTracker.js
  public/
    index.html
    styles.css
    app.js
```

---

## 4. Setup

Create the project:

```powershell
mkdir llm-observability-chatbot
cd llm-observability-chatbot
npm init -y
npm pkg set type=module
npm pkg set scripts.start="node server.js"
npm pkg set scripts.dev="node --watch server.js"
npm install express dotenv cors better-sqlite3 @google/genai
mkdir src
mkdir public
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

Create `.env.example`:

```env
PORT=3000
GEMINI_API_KEY=replace_with_your_gemini_key
GEMINI_MODEL=gemini-3.5-flash
DATABASE_PATH=./assessment.sqlite
MAX_CONTEXT_MESSAGES=8
MAX_INPUT_CHARS=4000
LOG_INGESTION_URL=http://localhost:3000/llm-events
LOG_INGESTION_KEY=replace_with_a_long_random_secret
MAX_EVENTS_PER_REQUEST=100
```

Create `.env` from `.env.example` and put real secrets there. Do not commit `.env`.

---

## 5. Database Schema

The app uses SQLite for local development. The schema separates application data from inference observability data.

```sql
CREATE TABLE IF NOT EXISTS conversations (
  id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS conversations_session_id_idx
  ON conversations (session_id);

CREATE TABLE IF NOT EXISTS chat_messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  conversation_id TEXT NOT NULL,
  role TEXT NOT NULL CHECK (role IN ('user', 'model', 'system')),
  content TEXT NOT NULL,
  inference_event_id TEXT,
  created_at TEXT NOT NULL,
  FOREIGN KEY (conversation_id) REFERENCES conversations(id),
  FOREIGN KEY (inference_event_id) REFERENCES llm_inference_events(event_id)
);

CREATE INDEX IF NOT EXISTS chat_messages_conversation_id_idx
  ON chat_messages (conversation_id, id);

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

Design notes:

- `event_id` is the primary key, so SDK retries do not duplicate inference rows.
- Chat messages are stored separately from inference logs because one conversation can contain many messages and many model calls.
- `llm_inference_events` stores normalized query columns plus `raw_event_json` for future schema evolution.
- Full chat messages are stored because the assessment asks for chat storage. In a real product, retention, deletion, and privacy controls would be important.

---

## 6. Lightweight SDK: `src/llmTracker.js`

This wrapper measures an LLM call, extracts useful metadata, and sends it to the ingestion endpoint through a small background queue.

```js
import { randomUUID } from "node:crypto";

const DEFAULT_PREVIEW_LENGTH = 300;

export class LLMTracker {
  constructor(config) {
    this.config = {
      provider: config.provider,
      model: config.model,
      ingestionUrl: config.ingestionUrl,
      apiKey: config.apiKey || "",
      privacy: {
        captureInputPreview: true,
        captureOutputPreview: true,
        previewLength: DEFAULT_PREVIEW_LENGTH,
        ...(config.privacy || {}),
      },
    };

    this.queue = new EventQueue({
      ingestionUrl: this.config.ingestionUrl,
      apiKey: this.config.apiKey,
      ...(config.queue || {}),
    });
  }

  async track(input) {
    const eventId = input.eventId || randomUUID();
    const startedAt = new Date().toISOString();
    const startTime = performance.now();

    try {
      const result = await input.call();
      const endedAt = new Date().toISOString();
      const latencyMs = Math.round(performance.now() - startTime);
      const outputText = input.extractOutput?.(result);
      const tokenUsage = input.extractTokenUsage?.(result);

      this.queue.enqueue({
        eventId,
        provider: this.config.provider,
        model: this.config.model,
        status: "success",
        startedAt,
        endedAt,
        latencyMs,
        sessionId: input.sessionId,
        conversationId: input.conversationId,
        requestId: input.requestId,
        inputPreview: this.makeInputPreview(input.input),
        outputPreview: this.makeOutputPreview(outputText),
        tokenUsage,
        metadata: input.metadata,
      });

      return result;
    } catch (error) {
      const endedAt = new Date().toISOString();
      const latencyMs = Math.round(performance.now() - startTime);

      this.queue.enqueue({
        eventId,
        provider: this.config.provider,
        model: this.config.model,
        status: "error",
        startedAt,
        endedAt,
        latencyMs,
        sessionId: input.sessionId,
        conversationId: input.conversationId,
        requestId: input.requestId,
        inputPreview: this.makeInputPreview(input.input),
        errorType: error instanceof Error ? error.name : "UnknownError",
        errorMessage: error instanceof Error ? error.message : String(error),
        metadata: input.metadata,
      });

      throw error;
    }
  }

  makeInputPreview(value) {
    if (!this.config.privacy.captureInputPreview) {
      return undefined;
    }

    return safePreview(value, this.config.privacy.previewLength);
  }

  makeOutputPreview(value) {
    if (!this.config.privacy.captureOutputPreview) {
      return undefined;
    }

    return safePreview(value, this.config.privacy.previewLength);
  }

  async flush() {
    await this.queue.flush();
  }

  async close() {
    await this.queue.close();
  }
}

class EventQueue {
  constructor(config) {
    this.queue = [];
    this.isFlushing = false;
    this.config = {
      ingestionUrl: config.ingestionUrl,
      apiKey: config.apiKey || "",
      flushIntervalMs: config.flushIntervalMs || 1000,
      maxBatchSize: config.maxBatchSize || 20,
      maxQueueSize: config.maxQueueSize || 1000,
      timeoutMs: config.timeoutMs || 2000,
    };

    this.timer = setInterval(() => {
      void this.flush();
    }, this.config.flushIntervalMs);

    this.timer.unref?.();
  }

  enqueue(event) {
    if (this.queue.length >= this.config.maxQueueSize) {
      this.queue.shift();
    }

    this.queue.push(event);

    if (this.queue.length >= this.config.maxBatchSize) {
      void this.flush();
    }
  }

  async flush() {
    if (this.isFlushing || this.queue.length === 0) {
      return;
    }

    this.isFlushing = true;
    const batch = this.queue.splice(0, this.config.maxBatchSize);

    try {
      await postJsonWithTimeout(
        this.config.ingestionUrl,
        { events: batch },
        {
          apiKey: this.config.apiKey,
          timeoutMs: this.config.timeoutMs,
        },
      );
    } catch {
      this.queue.unshift(...batch);

      if (this.queue.length > this.config.maxQueueSize) {
        this.queue.splice(0, this.queue.length - this.config.maxQueueSize);
      }
    } finally {
      this.isFlushing = false;
    }
  }

  async close() {
    clearInterval(this.timer);
    await this.flush();
  }
}

async function postJsonWithTimeout(url, body, options) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), options.timeoutMs);

  try {
    const response = await fetch(url, {
      method: "POST",
      headers: {
        "content-type": "application/json",
        ...(options.apiKey ? { authorization: `Bearer ${options.apiKey}` } : {}),
      },
      body: JSON.stringify(body),
      signal: controller.signal,
    });

    if (!response.ok) {
      throw new Error(`Ingestion failed with status ${response.status}`);
    }
  } finally {
    clearTimeout(timeout);
  }
}

function safePreview(value, maxLength) {
  if (!value) {
    return undefined;
  }

  const normalized = redact(String(value)).replace(/\s+/g, " ").trim();
  return normalized.length <= maxLength
    ? normalized
    : `${normalized.slice(0, maxLength)}...`;
}

function redact(value) {
  return value
    .replace(/[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}/gi, "[email]")
    .replace(/\b\d{3}[-.]?\d{2}[-.]?\d{4}\b/g, "[ssn]")
    .replace(/\b(?:\d[ -]*?){13,16}\b/g, "[card]");
}
```

SDK behavior:

- The original LLM result is returned unchanged.
- LLM errors are logged and then rethrown.
- Ingestion failures do not fail the chat request.
- Logs are batched for near real-time delivery.
- Input and output previews are redacted and truncated.

---

## 7. Backend and Ingestion API: `server.js`

This one Express server serves the UI, handles chat requests, calls Gemini through the SDK wrapper, receives SDK events, validates them, and writes SQLite rows.

```js
import "dotenv/config";
import crypto from "node:crypto";
import cors from "cors";
import express from "express";
import Database from "better-sqlite3";
import { GoogleGenAI } from "@google/genai";
import { LLMTracker } from "./src/llmTracker.js";

const app = express();

const PORT = Number(process.env.PORT || 3000);
const MODEL = process.env.GEMINI_MODEL || "gemini-3.5-flash";
const DATABASE_PATH = process.env.DATABASE_PATH || "./assessment.sqlite";
const MAX_CONTEXT_MESSAGES = Number(process.env.MAX_CONTEXT_MESSAGES || 8);
const MAX_INPUT_CHARS = Number(process.env.MAX_INPUT_CHARS || 4000);
const MAX_EVENTS_PER_REQUEST = Number(process.env.MAX_EVENTS_PER_REQUEST || 100);
const MAX_PREVIEW_CHARS = 1000;
const MAX_ERROR_MESSAGE_CHARS = 2000;
const LOG_INGESTION_KEY = process.env.LOG_INGESTION_KEY || "";

if (!process.env.GEMINI_API_KEY) {
  console.warn("Missing GEMINI_API_KEY. Add it to your .env file.");
}

if (!LOG_INGESTION_KEY) {
  console.warn("Missing LOG_INGESTION_KEY. Add it to your .env file.");
}

const ai = new GoogleGenAI({
  apiKey: process.env.GEMINI_API_KEY,
});

const db = new Database(DATABASE_PATH);
db.pragma("journal_mode = WAL");
initializeDatabase();

const tracker = new LLMTracker({
  provider: "gemini",
  model: MODEL,
  ingestionUrl: process.env.LOG_INGESTION_URL || `http://localhost:${PORT}/llm-events`,
  apiKey: LOG_INGESTION_KEY,
  privacy: {
    captureInputPreview: true,
    captureOutputPreview: true,
    previewLength: 300,
  },
  queue: {
    flushIntervalMs: 1000,
    maxBatchSize: 20,
    maxQueueSize: 1000,
    timeoutMs: 2000,
  },
});

const statements = {
  findConversation: db.prepare(`
    SELECT id, session_id AS sessionId
    FROM conversations
    WHERE id = ?
  `),
  insertConversation: db.prepare(`
    INSERT INTO conversations (id, session_id, created_at, updated_at)
    VALUES (@id, @sessionId, @createdAt, @updatedAt)
  `),
  touchConversation: db.prepare(`
    UPDATE conversations
    SET updated_at = @updatedAt
    WHERE id = @id
  `),
  insertMessage: db.prepare(`
    INSERT INTO chat_messages (
      conversation_id,
      role,
      content,
      inference_event_id,
      created_at
    ) VALUES (
      @conversationId,
      @role,
      @content,
      @inferenceEventId,
      @createdAt
    )
  `),
  recentMessages: db.prepare(`
    SELECT role, content
    FROM chat_messages
    WHERE conversation_id = ?
    ORDER BY id DESC
    LIMIT ?
  `),
  insertInferenceEvent: db.prepare(`
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
  `),
};

const insertInferenceEvents = db.transaction((events) => {
  let inserted = 0;
  let duplicates = 0;

  for (const event of events) {
    const result = statements.insertInferenceEvent.run(event);

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
app.use(express.static("public"));

app.get("/api/health", (req, res) => {
  const eventRow = db
    .prepare("SELECT COUNT(*) AS count FROM llm_inference_events")
    .get();
  const messageRow = db
    .prepare("SELECT COUNT(*) AS count FROM chat_messages")
    .get();

  res.json({
    ok: true,
    model: MODEL,
    maxContextMessages: MAX_CONTEXT_MESSAGES,
    inferenceEvents: eventRow.count,
    chatMessages: messageRow.count,
  });
});

app.post("/api/chat", async (req, res) => {
  const { sessionId: rawSessionId, conversationId: rawConversationId, message } =
    req.body || {};
  const userText = typeof message === "string" ? message.trim() : "";

  if (!userText) {
    return res.status(400).json({ error: "Message is required." });
  }

  if (userText.length > MAX_INPUT_CHARS) {
    return res.status(400).json({
      error: `Message is too long. Keep it under ${MAX_INPUT_CHARS} characters.`,
    });
  }

  const sessionId = cleanId(rawSessionId) || crypto.randomUUID();
  const conversationId = getOrCreateConversation(cleanId(rawConversationId), sessionId);
  const recentMessages = loadRecentMessages(conversationId);
  const currentUserMessage = { role: "user", content: userText };
  const contents = [
    ...recentMessages.map(toGeminiContent),
    toGeminiContent(currentUserMessage),
  ];
  const now = new Date().toISOString();
  const eventId = crypto.randomUUID();
  const requestId = crypto.randomUUID();

  statements.insertMessage.run({
    conversationId,
    role: "user",
    content: userText,
    inferenceEventId: null,
    createdAt: now,
  });

  try {
    const response = await tracker.track({
      eventId,
      sessionId,
      conversationId,
      requestId,
      input: userText,
      metadata: {
        route: "/api/chat",
        maxContextMessages: MAX_CONTEXT_MESSAGES,
        sentContextMessages: recentMessages.length,
      },
      call: () =>
        ai.models.generateContent({
          model: MODEL,
          contents,
          config: {
            systemInstruction:
              "You are a helpful, concise chatbot. Use recent conversation context when relevant. If you do not know something, say so plainly.",
            maxOutputTokens: 700,
          },
        }),
      extractOutput: (geminiResponse) => geminiResponse.text,
      extractTokenUsage: extractGeminiTokenUsage,
    });

    const assistantText =
      response.text?.trim() ||
      "I could not generate a response. Please try again.";

    statements.insertMessage.run({
      conversationId,
      role: "model",
      content: assistantText,
      inferenceEventId: eventId,
      createdAt: new Date().toISOString(),
    });

    statements.touchConversation.run({
      id: conversationId,
      updatedAt: new Date().toISOString(),
    });

    return res.json({
      sessionId,
      conversationId,
      reply: assistantText,
    });
  } catch (error) {
    console.error(error);

    statements.touchConversation.run({
      id: conversationId,
      updatedAt: new Date().toISOString(),
    });

    return res.status(500).json({
      error: "The chatbot failed to respond. Check the server logs.",
    });
  }
});

app.post("/api/reset", (req, res) => {
  const sessionId = cleanId(req.body?.sessionId) || crypto.randomUUID();
  const conversationId = crypto.randomUUID();
  const now = new Date().toISOString();

  statements.insertConversation.run({
    id: conversationId,
    sessionId,
    createdAt: now,
    updatedAt: now,
  });

  res.json({ ok: true, sessionId, conversationId });
});

app.get("/api/logs/recent", (req, res) => {
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

app.get("/api/metrics/summary", (req, res) => {
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

app.post("/llm-events", authenticateIngestion, (req, res) => {
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

  const result = insertInferenceEvents(processedEvents);

  return res.status(202).json({
    accepted: processedEvents.length,
    inserted: result.inserted,
    duplicates: result.duplicates,
  });
});

app.use((err, req, res, next) => {
  if (err instanceof SyntaxError && "body" in err) {
    return res.status(400).json({ error: "Request body must be valid JSON." });
  }

  console.error(err);
  return res.status(500).json({ error: "Internal server error." });
});

app.listen(PORT, () => {
  console.log(`Assessment app running at http://localhost:${PORT}`);
});

process.on("SIGINT", () => {
  void shutdown();
});

process.on("SIGTERM", () => {
  void shutdown();
});

async function shutdown() {
  await tracker.close();
  db.close();
  process.exit(0);
}

function initializeDatabase() {
  db.exec(`
    CREATE TABLE IF NOT EXISTS conversations (
      id TEXT PRIMARY KEY,
      session_id TEXT NOT NULL,
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL
    );

    CREATE INDEX IF NOT EXISTS conversations_session_id_idx
      ON conversations (session_id);

    CREATE TABLE IF NOT EXISTS chat_messages (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      conversation_id TEXT NOT NULL,
      role TEXT NOT NULL CHECK (role IN ('user', 'model', 'system')),
      content TEXT NOT NULL,
      inference_event_id TEXT,
      created_at TEXT NOT NULL,
      FOREIGN KEY (conversation_id) REFERENCES conversations(id),
      FOREIGN KEY (inference_event_id) REFERENCES llm_inference_events(event_id)
    );

    CREATE INDEX IF NOT EXISTS chat_messages_conversation_id_idx
      ON chat_messages (conversation_id, id);

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
}

function getOrCreateConversation(candidateConversationId, sessionId) {
  if (candidateConversationId) {
    const existing = statements.findConversation.get(candidateConversationId);

    if (existing) {
      return existing.id;
    }
  }

  const id = crypto.randomUUID();
  const now = new Date().toISOString();

  statements.insertConversation.run({
    id,
    sessionId,
    createdAt: now,
    updatedAt: now,
  });

  return id;
}

function loadRecentMessages(conversationId) {
  return statements.recentMessages
    .all(conversationId, MAX_CONTEXT_MESSAGES)
    .reverse();
}

function toGeminiContent(message) {
  return {
    role: message.role,
    parts: [{ text: message.content }],
  };
}

function extractGeminiTokenUsage(response) {
  const usage = response.usageMetadata || {};

  return {
    inputTokens: usage.promptTokenCount,
    outputTokens: usage.candidatesTokenCount,
    totalTokens: usage.totalTokenCount,
  };
}

function authenticateIngestion(req, res, next) {
  if (!LOG_INGESTION_KEY) {
    return res.status(500).json({
      error: "LOG_INGESTION_KEY is not configured on the server.",
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

function cleanId(value) {
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

function clampInteger(value, min, max) {
  if (!Number.isInteger(value)) {
    return min;
  }

  return Math.min(max, Math.max(min, value));
}
```

---

## 8. Frontend: `public/index.html`

```html
<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>LLM Observability Chat</title>
    <link rel="stylesheet" href="/styles.css" />
  </head>
  <body>
    <main class="appShell">
      <section class="chatPane">
        <header class="topbar">
          <div>
            <h1>LLM Observability Chat</h1>
            <p id="status">Ready</p>
          </div>
          <button id="resetButton" type="button">Reset</button>
        </header>

        <section id="messages" class="messages" aria-live="polite"></section>

        <form id="chatForm" class="composer">
          <textarea
            id="messageInput"
            rows="1"
            placeholder="Type a message"
            autocomplete="off"
          ></textarea>
          <button id="sendButton" type="submit">Send</button>
        </form>
      </section>

      <aside class="logPane">
        <header>
          <h2>Recent Inference Logs</h2>
          <button id="refreshLogsButton" type="button">Refresh</button>
        </header>
        <div id="logs" class="logs"></div>
      </aside>
    </main>

    <script src="/app.js"></script>
  </body>
</html>
```

---

## 9. Frontend: `public/styles.css`

```css
:root {
  color-scheme: light;
  --bg: #f5f7fa;
  --panel: #ffffff;
  --text: #18202a;
  --muted: #667085;
  --line: #d9dee7;
  --primary: #2563eb;
  --primary-hover: #1d4ed8;
  --user: #0f766e;
  --assistant: #ffffff;
  --danger: #b42318;
  --success: #067647;
}

* {
  box-sizing: border-box;
}

html,
body {
  height: 100%;
}

body {
  margin: 0;
  background: var(--bg);
  color: var(--text);
  font-family:
    Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI",
    sans-serif;
}

button,
textarea {
  font: inherit;
}

.appShell {
  min-height: 100dvh;
  display: grid;
  grid-template-columns: minmax(0, 1fr) 360px;
}

.chatPane {
  min-width: 0;
  display: grid;
  grid-template-rows: auto 1fr auto;
  background: var(--panel);
  border-right: 1px solid var(--line);
}

.topbar,
.logPane header {
  min-height: 72px;
  padding: 16px 20px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  border-bottom: 1px solid var(--line);
}

.topbar h1,
.logPane h2 {
  margin: 0;
  font-size: 18px;
  line-height: 1.25;
  letter-spacing: 0;
}

.topbar p {
  margin: 4px 0 0;
  color: var(--muted);
  font-size: 14px;
}

button {
  border: 0;
  border-radius: 8px;
  cursor: pointer;
  font-weight: 650;
}

.topbar button,
.logPane button {
  min-height: 38px;
  padding: 0 12px;
  color: var(--text);
  background: #eef1f5;
}

.topbar button:hover,
.logPane button:hover {
  background: #e4e8ef;
}

.messages {
  overflow-y: auto;
  padding: 24px 20px;
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.message {
  width: fit-content;
  max-width: min(700px, 88%);
  padding: 12px 14px;
  border-radius: 8px;
  line-height: 1.5;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.message.user {
  align-self: flex-end;
  background: var(--user);
  color: white;
}

.message.model,
.message.system {
  align-self: flex-start;
  background: var(--assistant);
  border: 1px solid var(--line);
}

.message.system {
  color: var(--muted);
}

.message.error {
  border-color: #f3b3ad;
  color: var(--danger);
}

.composer {
  padding: 14px;
  display: grid;
  grid-template-columns: 1fr auto;
  gap: 10px;
  border-top: 1px solid var(--line);
  background: #fbfcfe;
}

.composer textarea {
  width: 100%;
  max-height: 180px;
  resize: none;
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 12px 13px;
  outline: none;
  line-height: 1.45;
  color: var(--text);
  background: #ffffff;
}

.composer textarea:focus {
  border-color: var(--primary);
  box-shadow: 0 0 0 3px rgb(37 99 235 / 14%);
}

.composer button {
  min-width: 86px;
  min-height: 46px;
  padding: 0 18px;
  color: white;
  background: var(--primary);
}

.composer button:hover {
  background: var(--primary-hover);
}

button:disabled,
textarea:disabled {
  cursor: not-allowed;
  opacity: 0.65;
}

.logPane {
  min-width: 0;
  background: #f8fafc;
  display: grid;
  grid-template-rows: auto 1fr;
}

.logs {
  overflow-y: auto;
  padding: 14px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.logItem {
  border: 1px solid var(--line);
  border-radius: 8px;
  background: #ffffff;
  padding: 12px;
}

.logItem strong {
  display: block;
  margin-bottom: 4px;
  font-size: 14px;
}

.logMeta {
  display: grid;
  gap: 3px;
  color: var(--muted);
  font-size: 13px;
}

.statusSuccess {
  color: var(--success);
}

.statusError {
  color: var(--danger);
}

@media (max-width: 880px) {
  .appShell {
    grid-template-columns: 1fr;
  }

  .chatPane {
    min-height: 72dvh;
    border-right: 0;
    border-bottom: 1px solid var(--line);
  }

  .logPane {
    min-height: 28dvh;
  }
}

@media (max-width: 640px) {
  .topbar,
  .logPane header {
    padding: 14px;
  }

  .messages {
    padding: 18px 14px;
  }

  .message {
    max-width: 94%;
  }

  .composer {
    grid-template-columns: 1fr;
  }

  .composer button {
    width: 100%;
  }
}
```

---

## 10. Frontend: `public/app.js`

```js
const form = document.querySelector("#chatForm");
const input = document.querySelector("#messageInput");
const messages = document.querySelector("#messages");
const sendButton = document.querySelector("#sendButton");
const resetButton = document.querySelector("#resetButton");
const statusText = document.querySelector("#status");
const logs = document.querySelector("#logs");
const refreshLogsButton = document.querySelector("#refreshLogsButton");

const SESSION_KEY = "assessment_chat_session_id";
const CONVERSATION_KEY = "assessment_chat_conversation_id";

let sessionId =
  localStorage.getItem(SESSION_KEY) ||
  (crypto.randomUUID ? crypto.randomUUID() : String(Date.now()));
let conversationId = localStorage.getItem(CONVERSATION_KEY) || "";

localStorage.setItem(SESSION_KEY, sessionId);

function setStatus(text) {
  statusText.textContent = text;
}

function addMessage(role, text, options = {}) {
  const bubble = document.createElement("div");
  bubble.className = `message ${role}${options.error ? " error" : ""}`;
  bubble.textContent = text;
  messages.appendChild(bubble);
  messages.scrollTop = messages.scrollHeight;
  return bubble;
}

function setBusy(isBusy) {
  sendButton.disabled = isBusy;
  resetButton.disabled = isBusy;
  input.disabled = isBusy;
}

function autoResizeInput() {
  input.style.height = "auto";
  input.style.height = `${input.scrollHeight}px`;
}

async function sendMessage(message) {
  setBusy(true);
  setStatus("Thinking");

  addMessage("user", message);
  input.value = "";
  autoResizeInput();

  try {
    const response = await fetch("/api/chat", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        sessionId,
        conversationId,
        message,
      }),
    });

    const data = await response.json();

    if (!response.ok) {
      throw new Error(data.error || "Request failed.");
    }

    sessionId = data.sessionId;
    conversationId = data.conversationId;
    localStorage.setItem(SESSION_KEY, sessionId);
    localStorage.setItem(CONVERSATION_KEY, conversationId);

    addMessage("model", data.reply);
    setStatus("Ready");
    await loadLogs();
  } catch (error) {
    addMessage("system", error.message, { error: true });
    setStatus("Error");
  } finally {
    setBusy(false);
    input.focus();
  }
}

async function resetConversation() {
  setBusy(true);

  try {
    const response = await fetch("/api/reset", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ sessionId }),
    });

    const data = await response.json();

    if (!response.ok) {
      throw new Error(data.error || "Reset failed.");
    }

    sessionId = data.sessionId;
    conversationId = data.conversationId;
    localStorage.setItem(SESSION_KEY, sessionId);
    localStorage.setItem(CONVERSATION_KEY, conversationId);

    messages.textContent = "";
    addMessage("system", "Conversation reset.");
    setStatus("Ready");
  } catch (error) {
    addMessage("system", error.message, { error: true });
    setStatus("Error");
  } finally {
    setBusy(false);
    input.focus();
  }
}

async function loadLogs() {
  try {
    const response = await fetch("/api/logs/recent?limit=10");
    const data = await response.json();

    if (!response.ok) {
      throw new Error(data.error || "Could not load logs.");
    }

    renderLogs(data.rows || []);
  } catch (error) {
    logs.textContent = error.message;
  }
}

function renderLogs(rows) {
  logs.textContent = "";

  if (rows.length === 0) {
    const empty = document.createElement("div");
    empty.className = "logItem";
    empty.textContent = "No inference logs yet.";
    logs.appendChild(empty);
    return;
  }

  for (const row of rows) {
    const item = document.createElement("article");
    item.className = "logItem";

    const title = document.createElement("strong");
    title.className = row.status === "success" ? "statusSuccess" : "statusError";
    title.textContent = `${row.status.toUpperCase()} ${row.model}`;

    const meta = document.createElement("div");
    meta.className = "logMeta";
    meta.innerHTML = `
      <span>Latency: ${row.latencyMs} ms</span>
      <span>Tokens: ${row.totalTokens ?? "unknown"}</span>
      <span>Conversation: ${shortId(row.conversationId)}</span>
    `;

    item.appendChild(title);
    item.appendChild(meta);
    logs.appendChild(item);
  }
}

function shortId(value) {
  if (!value) {
    return "none";
  }

  return value.length > 8 ? `${value.slice(0, 8)}...` : value;
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const message = input.value.trim();

  if (!message) {
    return;
  }

  await sendMessage(message);
});

input.addEventListener("input", autoResizeInput);

input.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    form.requestSubmit();
  }
});

resetButton.addEventListener("click", resetConversation);
refreshLogsButton.addEventListener("click", loadLogs);

addMessage("system", "Ask me something.");
void loadLogs();
input.focus();
```

---

## 11. Run the App

Start the app:

```powershell
npm run dev
```

Open:

```text
http://localhost:3000
```

Try a multi-turn context test:

```text
My name is Arpan and I am building an LLM logging system.
```

Then ask:

```text
What am I building?
```

The model should answer using the recent conversation context. The right-side log panel should show a new inference event after the call is ingested.

---

## 12. Test the Ingestion Endpoint Directly

Use this PowerShell test request:

```powershell
$body = @{
  events = @(
    @{
      eventId = "evt_test_1"
      provider = "gemini"
      model = "gemini-3.5-flash"
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
        feature = "chat"
        userPlan = "demo"
      }
    }
  )
} | ConvertTo-Json -Depth 10

Invoke-RestMethod `
  -Method Post `
  -Uri "http://localhost:3000/llm-events" `
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

If you send the same `eventId` again, `duplicates` should become `1`.

---

## 13. Useful API Endpoints

```text
GET  /api/health
POST /api/chat
POST /api/reset
GET  /api/logs/recent
GET  /api/metrics/summary
POST /llm-events
```

`POST /api/chat` body:

```json
{
  "sessionId": "browser-session-id",
  "conversationId": "conversation-id",
  "message": "Hello"
}
```

`POST /llm-events` accepts either one event:

```json
{
  "eventId": "evt_123",
  "provider": "gemini",
  "model": "gemini-3.5-flash",
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
      "provider": "gemini",
      "model": "gemini-3.5-flash",
      "status": "success",
      "startedAt": "2026-05-25T04:00:00.000Z",
      "endedAt": "2026-05-25T04:00:01.250Z",
      "latencyMs": 1250
    }
  ]
}
```

---

## 14. Validation Rules

The ingestion endpoint rejects invalid payloads with `400`.

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
- Token counts must be non-negative integers when present.
- Preview fields are capped to avoid huge payloads.
- `metadata` must be an object when provided.
- A batch can contain at most `MAX_EVENTS_PER_REQUEST` events.

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

## 15. Useful Queries

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

Conversation transcript:

```sql
SELECT
  role,
  content,
  created_at
FROM chat_messages
WHERE conversation_id = ?
ORDER BY id ASC;
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

## 16. Practical Tradeoffs

SQLite is a good assessment and local-development choice because it is simple, durable, and requires no separate database server. For production, Postgres is a natural next step for normal app dashboards, while ClickHouse or BigQuery is better for high-volume analytics.

The SDK queue is intentionally in memory. That keeps the implementation lightweight and fast, but logs can be lost if the process exits before flushing. Production versions should add retry backoff, disk buffering, or a durable queue.

The app stores full chat messages because the assessment asks for chat message storage. The inference event table stores only previews by default, with redaction for common sensitive patterns. A real product should add retention policies, deletion workflows, authentication, and stronger PII controls.

The chatbot sends only the last `MAX_CONTEXT_MESSAGES` messages to Gemini. The database stores the full conversation, but the model receives only a short recent window. This lowers latency and cost while preserving enough context for ordinary multi-turn chat.

The ingestion endpoint preserves `raw_event_json` so the SDK schema can evolve without losing fields. Important fields are also extracted into columns for practical querying.

---

## 17. Production Upgrade Path

The assessment app is:

```text
Browser -> Express app -> Gemini
                    |
                    v
              /llm-events -> SQLite
```

A production version can become:

```text
Browser -> Chat API -> LLM provider
                  |
                  v
               SDK event
                  |
                  v
        Ingestion API -> Durable queue -> Worker -> Analytics database
```

Recommended upgrades:

- Use Postgres migrations instead of creating tables in `server.js`.
- Add authentication for chat users.
- Add per-key or per-user rate limiting.
- Add retry counts and exponential backoff in the SDK queue.
- Add tenant or project IDs for multi-team logging.
- Add dashboards for latency, error rate, token usage, and estimated cost.
- Add cost estimation with configurable model pricing.
- Add retention and deletion policies.
- Use HTTPS in production.
- Store full prompts only when explicitly needed and legally permitted.

---

## 18. Assessment Summary

This app is a browser-based multi-turn chatbot backed by Gemini. The browser sends messages to the Express server, which stores conversations and chat messages in SQLite. For every user turn, the server loads only the latest messages from the conversation, sends that short context plus the new user message to Gemini, and stores the assistant response.

The LLM call is wrapped by a lightweight `LLMTracker` SDK. The wrapper captures provider, model, timestamps, latency, request status, errors, session ID, conversation ID, request ID, token usage, and redacted input/output previews. Events are queued and sent in near real time to an ingestion endpoint.

The ingestion API authenticates SDK requests, accepts single or batched events, validates payloads, normalizes timestamps, extracts useful metadata into queryable columns, preserves the raw event JSON, and stores everything in SQLite. This gives the application practical observability over LLM behavior while keeping the implementation small and understandable.
