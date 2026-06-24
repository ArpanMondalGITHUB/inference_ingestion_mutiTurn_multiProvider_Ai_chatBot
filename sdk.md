# Build a Lightweight LLM Metadata SDK

This tutorial walks from a basic wrapper around an LLM call to a more advanced SDK that captures inference metadata and sends logs to an ingestion endpoint in near real time.

The examples use TypeScript and Node.js, but the architecture works in Python, Go, Java, or any backend language.

## 1. What You Are Building

You want a small SDK that application developers can use like this:

```ts
const sdk = new LLMTracker({
  provider: "openai",
  model: "gpt-4.1-mini",
  ingestionUrl: "https://logs.example.com/llm-events",
  apiKey: process.env.LOG_INGESTION_KEY
});

const result = await sdk.track({
  sessionId: "session_123",
  conversationId: "conversation_456",
  input: "Summarize this support ticket.",
  call: async () => {
    return llmClient.responses.create({
      model: "gpt-4.1-mini",
      input: "Summarize this support ticket."
    });
  }
});

console.log(result.outputText);
```

Behind the scenes, the SDK captures metadata such as:

- `model`
- `provider`
- `latency`
- `token usage`
- `timestamps`
- `request status`
- `errors`
- `session ID`
- `conversation ID`
- `input preview`
- `output preview`

Then it sends the log to an ingestion endpoint quickly without blocking the user request longer than necessary.

## 2. Basic Architecture

A lightweight SDK usually has four parts:

```txt
Application code
    |
    v
LLM SDK wrapper
    |
    |-- calls real LLM provider
    |-- captures metadata
    |-- formats event
    |-- sends event to ingestion API
    v
Ingestion endpoint
    |
    v
Database, queue, analytics, dashboard, alerts
```

The SDK should not replace your LLM provider client. It should wrap it.

That keeps the SDK simple and flexible.

## 3. Metadata Schema

Start by defining the log event shape.

```ts
export type LLMRequestStatus = "success" | "error";

export type TokenUsage = {
  inputTokens?: number;
  outputTokens?: number;
  totalTokens?: number;
};

export type LLMInferenceEvent = {
  eventId: string;
  provider: string;
  model: string;

  status: LLMRequestStatus;
  errorType?: string;
  errorMessage?: string;

  startedAt: string;
  endedAt: string;
  latencyMs: number;

  sessionId?: string;
  conversationId?: string;
  requestId?: string;

  inputPreview?: string;
  outputPreview?: string;

  tokenUsage?: TokenUsage;

  metadata?: Record<string, unknown>;
};
```

Keep the schema small at first. You can add more fields later.

## 4. Basic SDK Wrapper

Create a minimal tracker that:

1. Starts a timer.
2. Runs the LLM call.
3. Captures success or failure.
4. Sends the event to your ingestion endpoint.
5. Returns the original LLM result.

```ts
type LLMTrackerConfig = {
  provider: string;
  model: string;
  ingestionUrl: string;
  apiKey?: string;
};

type TrackInput<T> = {
  sessionId?: string;
  conversationId?: string;
  requestId?: string;
  input?: string;
  metadata?: Record<string, unknown>;
  call: () => Promise<T>;
  extractOutput?: (result: T) => string | undefined;
  extractTokenUsage?: (result: T) => TokenUsage | undefined;
};

export class LLMTracker {
  private config: LLMTrackerConfig;

  constructor(config: LLMTrackerConfig) {
    this.config = config;
  }

  async track<T>(input: TrackInput<T>): Promise<T> {
    const startedAtDate = new Date();
    const startedAt = startedAtDate.toISOString();
    const startTime = performance.now();

    try {
      const result = await input.call();

      const endedAtDate = new Date();
      const latencyMs = Math.round(performance.now() - startTime);

      const event: LLMInferenceEvent = {
        eventId: crypto.randomUUID(),
        provider: this.config.provider,
        model: this.config.model,
        status: "success",
        startedAt,
        endedAt: endedAtDate.toISOString(),
        latencyMs,
        sessionId: input.sessionId,
        conversationId: input.conversationId,
        requestId: input.requestId,
        inputPreview: preview(input.input),
        outputPreview: preview(input.extractOutput?.(result)),
        tokenUsage: input.extractTokenUsage?.(result),
        metadata: input.metadata
      };

      await this.sendEvent(event);
      return result;
    } catch (error) {
      const endedAtDate = new Date();
      const latencyMs = Math.round(performance.now() - startTime);

      const event: LLMInferenceEvent = {
        eventId: crypto.randomUUID(),
        provider: this.config.provider,
        model: this.config.model,
        status: "error",
        startedAt,
        endedAt: endedAtDate.toISOString(),
        latencyMs,
        sessionId: input.sessionId,
        conversationId: input.conversationId,
        requestId: input.requestId,
        inputPreview: preview(input.input),
        errorType: error instanceof Error ? error.name : "UnknownError",
        errorMessage: error instanceof Error ? error.message : String(error),
        metadata: input.metadata
      };

      await this.sendEvent(event);
      throw error;
    }
  }

  private async sendEvent(event: LLMInferenceEvent): Promise<void> {
    await fetch(this.config.ingestionUrl, {
      method: "POST",
      headers: {
        "content-type": "application/json",
        ...(this.config.apiKey ? { authorization: `Bearer ${this.config.apiKey}` } : {})
      },
      body: JSON.stringify(event)
    });
  }
}

function preview(value: string | undefined, maxLength = 300): string | undefined {
  if (!value) return undefined;
  return value.length <= maxLength ? value : `${value.slice(0, maxLength)}...`;
}
```

This is the simplest useful version.

It works, but it has one problem: `await this.sendEvent(event)` adds ingestion latency to every LLM request.

Next, you will improve that.

## 5. Near Real Time Logging

"Near real time" usually means logs are sent within milliseconds or a few seconds, but the user request should not fail just because logging failed.

For that, use a background queue.

```ts
type EventQueueConfig = {
  ingestionUrl: string;
  apiKey?: string;
  flushIntervalMs?: number;
  maxBatchSize?: number;
};

export class EventQueue {
  private queue: LLMInferenceEvent[] = [];
  private timer: NodeJS.Timeout;
  private config: Required<EventQueueConfig>;

  constructor(config: EventQueueConfig) {
    this.config = {
      ingestionUrl: config.ingestionUrl,
      apiKey: config.apiKey ?? "",
      flushIntervalMs: config.flushIntervalMs ?? 1000,
      maxBatchSize: config.maxBatchSize ?? 20
    };

    this.timer = setInterval(() => {
      void this.flush();
    }, this.config.flushIntervalMs);

    this.timer.unref?.();
  }

  enqueue(event: LLMInferenceEvent): void {
    this.queue.push(event);

    if (this.queue.length >= this.config.maxBatchSize) {
      void this.flush();
    }
  }

  async flush(): Promise<void> {
    if (this.queue.length === 0) return;

    const batch = this.queue.splice(0, this.config.maxBatchSize);

    try {
      await fetch(this.config.ingestionUrl, {
        method: "POST",
        headers: {
          "content-type": "application/json",
          ...(this.config.apiKey ? { authorization: `Bearer ${this.config.apiKey}` } : {})
        },
        body: JSON.stringify({ events: batch })
      });
    } catch {
      this.queue.unshift(...batch);
    }
  }

  async close(): Promise<void> {
    clearInterval(this.timer);
    await this.flush();
  }
}
```

Now update the tracker to use the queue:

```ts
export class LLMTracker {
  private config: LLMTrackerConfig;
  private queue: EventQueue;

  constructor(config: LLMTrackerConfig) {
    this.config = config;
    this.queue = new EventQueue({
      ingestionUrl: config.ingestionUrl,
      apiKey: config.apiKey
    });
  }

  async track<T>(input: TrackInput<T>): Promise<T> {
    const startedAt = new Date().toISOString();
    const startTime = performance.now();

    try {
      const result = await input.call();

      this.queue.enqueue({
        eventId: crypto.randomUUID(),
        provider: this.config.provider,
        model: this.config.model,
        status: "success",
        startedAt,
        endedAt: new Date().toISOString(),
        latencyMs: Math.round(performance.now() - startTime),
        sessionId: input.sessionId,
        conversationId: input.conversationId,
        requestId: input.requestId,
        inputPreview: preview(input.input),
        outputPreview: preview(input.extractOutput?.(result)),
        tokenUsage: input.extractTokenUsage?.(result),
        metadata: input.metadata
      });

      return result;
    } catch (error) {
      this.queue.enqueue({
        eventId: crypto.randomUUID(),
        provider: this.config.provider,
        model: this.config.model,
        status: "error",
        startedAt,
        endedAt: new Date().toISOString(),
        latencyMs: Math.round(performance.now() - startTime),
        sessionId: input.sessionId,
        conversationId: input.conversationId,
        requestId: input.requestId,
        inputPreview: preview(input.input),
        errorType: error instanceof Error ? error.name : "UnknownError",
        errorMessage: error instanceof Error ? error.message : String(error),
        metadata: input.metadata
      });

      throw error;
    }
  }

  async flush(): Promise<void> {
    await this.queue.flush();
  }

  async close(): Promise<void> {
    await this.queue.close();
  }
}
```

Now application latency stays focused on the LLM call, while logs are delivered in the background.

## 6. Example Ingestion Endpoint

Your ingestion endpoint receives events from the SDK.

Here is a minimal Express server.

```ts
import express from "express";

const app = express();

app.use(express.json({ limit: "2mb" }));

app.post("/llm-events", async (req, res) => {
  const authHeader = req.header("authorization");

  if (authHeader !== `Bearer ${process.env.LOG_INGESTION_KEY}`) {
    return res.status(401).json({ error: "Unauthorized" });
  }

  const events = Array.isArray(req.body.events) ? req.body.events : [req.body];

  for (const event of events) {
    console.log("LLM event received", event);

    // In production, write to a database, queue, or analytics pipeline.
    // Examples: Postgres, ClickHouse, Kafka, S3, BigQuery, Datadog, OpenTelemetry.
  }

  return res.status(202).json({ accepted: events.length });
});

app.listen(3001, () => {
  console.log("Ingestion API listening on http://localhost:3001");
});
```

The SDK would point to:

```txt
http://localhost:3001/llm-events
```

## 7. Token Usage Extraction

Different providers return token usage differently.

Instead of hardcoding one provider response shape, let users pass an extractor.

```ts
const result = await sdk.track({
  sessionId: "session_123",
  conversationId: "conversation_456",
  input: userPrompt,
  call: () => llmClient.responses.create({
    model: "gpt-4.1-mini",
    input: userPrompt
  }),
  extractOutput: (response) => response.output_text,
  extractTokenUsage: (response) => ({
    inputTokens: response.usage?.input_tokens,
    outputTokens: response.usage?.output_tokens,
    totalTokens: response.usage?.total_tokens
  })
});
```

For another provider, you only change the extraction functions.

```ts
extractTokenUsage: (response) => ({
  inputTokens: response.usage?.prompt_tokens,
  outputTokens: response.usage?.completion_tokens,
  totalTokens: response.usage?.total_tokens
})
```

## 8. Input and Output Previews

Do not log full prompts by default.

Previews are safer because they are short and easier to inspect.

```ts
function preview(value: string | undefined, maxLength = 300): string | undefined {
  if (!value) return undefined;

  const normalized = value.replace(/\s+/g, " ").trim();
  return normalized.length <= maxLength
    ? normalized
    : `${normalized.slice(0, maxLength)}...`;
}
```

You can also redact sensitive values.

```ts
function redact(value: string): string {
  return value
    .replace(/[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}/gi, "[email]")
    .replace(/\b\d{3}[-.]?\d{2}[-.]?\d{4}\b/g, "[ssn]")
    .replace(/\b(?:\d[ -]*?){13,16}\b/g, "[card]");
}

function safePreview(value: string | undefined, maxLength = 300): string | undefined {
  if (!value) return undefined;
  return preview(redact(value), maxLength);
}
```

Then use `safePreview` instead of `preview`.

## 9. Session and Conversation IDs

Use IDs to connect requests across a user flow.

Suggested definitions:

- `sessionId`: one browser session, login session, or agent run.
- `conversationId`: one chat thread or support conversation.
- `requestId`: one specific LLM call.
- `eventId`: one SDK log event.

Example:

```ts
await sdk.track({
  sessionId: req.session.id,
  conversationId: chat.id,
  requestId: crypto.randomUUID(),
  input: message,
  call: () => callModel(message)
});
```

These IDs make it possible to answer questions like:

- Which sessions are slow?
- Which conversations have the most failed calls?
- Which model is most expensive for this workflow?
- Which prompts produce unusually long outputs?

## 10. Retry Failed Log Delivery

The queue above retries by putting failed events back into memory.

For a more production-ready SDK, add:

- maximum retry count
- exponential backoff
- maximum queue size
- log dropping strategy
- local disk buffer if losing logs is unacceptable

Example retry shape:

```ts
type QueuedEvent = {
  event: LLMInferenceEvent;
  attempts: number;
  nextAttemptAt: number;
};
```

Backoff helper:

```ts
function backoffMs(attempts: number): number {
  const base = 500;
  const max = 30_000;
  const jitter = Math.floor(Math.random() * 250);
  return Math.min(max, base * 2 ** attempts) + jitter;
}
```

When delivery fails:

```ts
queuedEvent.attempts += 1;
queuedEvent.nextAttemptAt = Date.now() + backoffMs(queuedEvent.attempts);
```

Then only flush events whose `nextAttemptAt` is in the past.

## 11. Advanced SDK Design

As the SDK grows, split it into modules.

```txt
src/
  index.ts
  tracker.ts
  queue.ts
  schema.ts
  preview.ts
  providers/
    generic.ts
    openai.ts
    anthropic.ts
```

Recommended responsibilities:

- `tracker.ts`: wraps calls and creates events.
- `queue.ts`: buffers and sends events.
- `schema.ts`: owns TypeScript types.
- `preview.ts`: truncation and redaction.
- `providers/*`: optional helpers for provider-specific output and token extraction.

## 12. Provider Adapter Pattern

The earlier examples require users to pass `extractOutput` and `extractTokenUsage` every time.

For a cleaner SDK, create provider adapters.

```ts
export type ProviderAdapter<TResponse> = {
  provider: string;
  model: string;
  extractOutput: (response: TResponse) => string | undefined;
  extractTokenUsage: (response: TResponse) => TokenUsage | undefined;
};
```

Example OpenAI-style adapter:

```ts
export function createOpenAIAdapter(model: string): ProviderAdapter<any> {
  return {
    provider: "openai",
    model,
    extractOutput: (response) => response.output_text,
    extractTokenUsage: (response) => ({
      inputTokens: response.usage?.input_tokens,
      outputTokens: response.usage?.output_tokens,
      totalTokens: response.usage?.total_tokens
    })
  };
}
```

Then the SDK can use adapter defaults:

```ts
const tracker = new LLMTracker({
  adapter: createOpenAIAdapter("gpt-4.1-mini"),
  ingestionUrl: "http://localhost:3001/llm-events"
});
```

Updated config:

```ts
type LLMTrackerConfig<TResponse = unknown> = {
  adapter: ProviderAdapter<TResponse>;
  ingestionUrl: string;
  apiKey?: string;
};
```

Now users can do:

```ts
const response = await tracker.track({
  input: prompt,
  sessionId,
  conversationId,
  call: () => llmClient.responses.create({
    model: "gpt-4.1-mini",
    input: prompt
  })
});
```

The SDK extracts output and token usage automatically.

## 13. Streaming Responses

Streaming is trickier because the output arrives in chunks.

For streaming, capture:

- time to first token
- total stream duration
- total output preview
- final status
- token usage if the provider returns it at the end

Example shape:

```ts
type StreamingMetadata = {
  timeToFirstTokenMs?: number;
  chunkCount: number;
};
```

Simple streaming wrapper idea:

```ts
async function trackStream<TChunk>(input: {
  input: string;
  stream: AsyncIterable<TChunk>;
  extractText: (chunk: TChunk) => string | undefined;
}) {
  const startedAt = new Date().toISOString();
  const startTime = performance.now();
  let firstTokenAt: number | undefined;
  let chunkCount = 0;
  let output = "";

  async function* wrappedStream() {
    try {
      for await (const chunk of input.stream) {
        chunkCount += 1;

        const text = input.extractText(chunk);
        if (text) {
          if (firstTokenAt === undefined) {
            firstTokenAt = performance.now();
          }
          output += text;
        }

        yield chunk;
      }
    } finally {
      const latencyMs = Math.round(performance.now() - startTime);

      queue.enqueue({
        eventId: crypto.randomUUID(),
        provider: "openai",
        model: "gpt-4.1-mini",
        status: "success",
        startedAt,
        endedAt: new Date().toISOString(),
        latencyMs,
        inputPreview: safePreview(input.input),
        outputPreview: safePreview(output),
        metadata: {
          chunkCount,
          timeToFirstTokenMs:
            firstTokenAt === undefined ? undefined : Math.round(firstTokenAt - startTime)
        }
      });
    }
  }

  return wrappedStream();
}
```

The important idea: yield chunks to the application while collecting metadata in the background.

## 14. Ingestion Storage Options

For local development, `console.log` is enough.

For production, choose storage based on query needs.

| Storage | Good For |
| --- | --- |
| Postgres | Simple app dashboards, moderate volume |
| ClickHouse | High-volume event analytics |
| Kafka | Large event pipelines |
| S3 or object storage | Cheap long-term raw logs |
| BigQuery or Snowflake | Analytics and reporting |
| OpenTelemetry collector | Standardized observability pipelines |

A common production path:

```txt
SDK -> Ingestion API -> Queue -> Worker -> Database
```

This prevents spikes in LLM traffic from overwhelming your database.

## 15. Minimal Database Table

Example Postgres table:

```sql
create table llm_inference_events (
  event_id text primary key,
  provider text not null,
  model text not null,
  status text not null,
  error_type text,
  error_message text,
  started_at timestamptz not null,
  ended_at timestamptz not null,
  latency_ms integer not null,
  session_id text,
  conversation_id text,
  request_id text,
  input_preview text,
  output_preview text,
  input_tokens integer,
  output_tokens integer,
  total_tokens integer,
  metadata jsonb,
  created_at timestamptz not null default now()
);

create index llm_events_started_at_idx on llm_inference_events (started_at);
create index llm_events_session_id_idx on llm_inference_events (session_id);
create index llm_events_conversation_id_idx on llm_inference_events (conversation_id);
create index llm_events_model_idx on llm_inference_events (model);
```

## 16. Useful Metrics

Once events are stored, you can calculate:

- average latency by model
- p95 latency by model
- error rate by provider
- token usage by user or session
- cost by model
- slowest conversations
- most expensive workflows
- output length trends

Example SQL:

```sql
select
  provider,
  model,
  count(*) as requests,
  avg(latency_ms) as avg_latency_ms,
  percentile_cont(0.95) within group (order by latency_ms) as p95_latency_ms,
  sum(total_tokens) as total_tokens
from llm_inference_events
where started_at >= now() - interval '24 hours'
group by provider, model
order by requests desc;
```

## 17. Security and Privacy

LLM logs can contain sensitive data.

Follow these rules:

- Do not log full prompts or outputs by default.
- Redact emails, tokens, API keys, financial data, and user identifiers when possible.
- Allow applications to disable input and output previews.
- Use HTTPS for the ingestion endpoint.
- Authenticate SDK requests to the ingestion endpoint.
- Add tenant or project IDs if this is used by multiple teams.
- Set retention policies.
- Make deletion possible for compliance workflows.

Suggested SDK config:

```ts
type PrivacyConfig = {
  captureInputPreview?: boolean;
  captureOutputPreview?: boolean;
  previewLength?: number;
  redact?: (value: string) => string;
};
```

Example:

```ts
const sdk = new LLMTracker({
  provider: "openai",
  model: "gpt-4.1-mini",
  ingestionUrl: "https://logs.example.com/llm-events",
  privacy: {
    captureInputPreview: true,
    captureOutputPreview: false,
    previewLength: 200,
    redact
  }
});
```

## 18. Failure Rules

A good observability SDK should not break the application.

Recommended behavior:

- If the LLM call fails, rethrow the original LLM error.
- If log delivery fails, do not fail the LLM request.
- If the queue is full, drop oldest logs or sample logs.
- If the process exits, expose `flush()` and `close()`.

Example shutdown handling:

```ts
process.on("SIGTERM", async () => {
  await sdk.close();
  process.exit(0);
});
```

## 19. Sampling

At high scale, you may not want to log every successful call.

Example:

```ts
function shouldSample(rate: number): boolean {
  return Math.random() < rate;
}
```

Use it like this:

```ts
if (event.status === "error" || shouldSample(0.1)) {
  queue.enqueue(event);
}
```

This logs all errors and 10 percent of successful calls.

## 20. Cost Tracking

If you know model prices, add estimated cost.

```ts
type ModelPricing = {
  inputCostPerMillionTokens: number;
  outputCostPerMillionTokens: number;
};

function estimateCost(usage: TokenUsage, pricing: ModelPricing): number | undefined {
  if (usage.inputTokens === undefined || usage.outputTokens === undefined) {
    return undefined;
  }

  const inputCost = (usage.inputTokens / 1_000_000) * pricing.inputCostPerMillionTokens;
  const outputCost = (usage.outputTokens / 1_000_000) * pricing.outputCostPerMillionTokens;

  return inputCost + outputCost;
}
```

Add it to metadata:

```ts
metadata: {
  estimatedCostUsd: estimateCost(tokenUsage, pricing)
}
```

Keep pricing configurable because model prices change.

## 21. Testing the SDK

Test the SDK without calling a real LLM.

```ts
import { describe, expect, it, vi } from "vitest";

it("captures successful LLM metadata", async () => {
  const fetchMock = vi.fn().mockResolvedValue({ ok: true });
  global.fetch = fetchMock as any;

  const sdk = new LLMTracker({
    provider: "test-provider",
    model: "test-model",
    ingestionUrl: "http://localhost:3001/llm-events"
  });

  const result = await sdk.track({
    input: "Hello",
    call: async () => ({ text: "Hi", usage: { total_tokens: 2 } }),
    extractOutput: (response) => response.text,
    extractTokenUsage: (response) => ({
      totalTokens: response.usage.total_tokens
    })
  });

  await sdk.flush();

  expect(result.text).toBe("Hi");
  expect(fetchMock).toHaveBeenCalled();
});
```

Also test:

- failed LLM calls
- ingestion failures
- preview truncation
- redaction
- batching
- queue size limits
- streaming metadata

## 22. Packaging the SDK

For a real TypeScript package:

```txt
llm-metadata-sdk/
  package.json
  tsconfig.json
  src/
    index.ts
    tracker.ts
    queue.ts
    schema.ts
    preview.ts
```

Minimal `package.json`:

```json
{
  "name": "llm-metadata-sdk",
  "version": "0.1.0",
  "type": "module",
  "main": "dist/index.js",
  "types": "dist/index.d.ts",
  "scripts": {
    "build": "tsc",
    "test": "vitest"
  },
  "dependencies": {},
  "devDependencies": {
    "typescript": "^5.0.0",
    "vitest": "^2.0.0"
  }
}
```

Export only the public API:

```ts
export { LLMTracker } from "./tracker";
export type {
  LLMInferenceEvent,
  LLMRequestStatus,
  TokenUsage
} from "./schema";
```

## 23. Production Checklist

Before using the SDK in production, check:

- Events are authenticated.
- Logs are sent over HTTPS.
- Prompt and output previews are redacted.
- Full prompts are disabled unless explicitly needed.
- Queue has max size protection.
- Ingestion failures do not break user requests.
- LLM errors are still logged and rethrown.
- `flush()` runs during shutdown.
- Batching is enabled.
- Timeouts are set for ingestion requests.
- Tests cover success, failure, batching, and redaction.
- Model pricing is configurable.
- The ingestion endpoint validates payload size.

## 24. Final Recommended SDK API

A clean final API might look like this:

```ts
const sdk = new LLMTracker({
  provider: "openai",
  model: "gpt-4.1-mini",
  ingestionUrl: process.env.LLM_LOG_INGESTION_URL!,
  apiKey: process.env.LLM_LOG_INGESTION_KEY,
  privacy: {
    captureInputPreview: true,
    captureOutputPreview: true,
    previewLength: 300,
    redact
  },
  queue: {
    flushIntervalMs: 1000,
    maxBatchSize: 20,
    maxQueueSize: 1000
  }
});

const response = await sdk.track({
  sessionId,
  conversationId,
  requestId: crypto.randomUUID(),
  input: prompt,
  metadata: {
    feature: "support-summary",
    userPlan: "pro"
  },
  call: () => llmClient.responses.create({
    model: "gpt-4.1-mini",
    input: prompt
  }),
  extractOutput: (response) => response.output_text,
  extractTokenUsage: (response) => ({
    inputTokens: response.usage?.input_tokens,
    outputTokens: response.usage?.output_tokens,
    totalTokens: response.usage?.total_tokens
  })
});
```

## 25. Build Order

If you are building this from scratch, implement it in this order:

1. Define the event schema.
2. Create the basic `track()` wrapper.
3. Send one event to an ingestion endpoint.
4. Add input and output previews.
5. Capture token usage.
6. Add error logging.
7. Replace direct sending with a queue.
8. Add batching.
9. Add retries and timeouts.
10. Add redaction and privacy settings.
11. Add provider adapters.
12. Add streaming support.
13. Add tests.
14. Package the SDK.
15. Build dashboards from the stored events.

## Summary

The core idea is simple:

```txt
wrap the LLM call -> measure it -> extract metadata -> send event -> return original result
```

Start with a small wrapper. Add reliability, batching, privacy, adapters, and streaming only after the basic metadata flow works.
