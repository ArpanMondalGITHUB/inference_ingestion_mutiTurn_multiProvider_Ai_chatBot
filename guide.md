# FastAPI Guide: Add a Lightweight LLM Metadata SDK

This project already has a TypeScript SDK tutorial in `sdk.md`, but the backend here is FastAPI/Python. This guide explains where each part should go and how to implement it later without changing the app behavior.

Current important files:

```text
server/
  src/
    server.py                  # FastAPI app setup
    core/
      config.py                # environment variables
    models/
      chat_models.py           # request/response models
    routes/
      run_ai_routes.py         # /v1/api/chat route
    services/
      ai.py                    # Gemini client and LLM call
```

The LLM call currently happens in:

```text
server/src/services/ai.py
```

That is the best place to connect the SDK wrapper, because the route should stay focused on HTTP and the service should own the AI call.

## 1. What You Are Building

You need a small Python SDK/wrapper that captures metadata for every Gemini request:

- provider, for example `gemini`
- model, for example your `GEMINI_MODEL`
- latency in milliseconds
- timestamps
- success/error status
- error type and message
- conversation ID
- request ID
- input preview
- output preview
- token usage when the provider response exposes it
- custom metadata

Then the SDK should send that event to an ingestion endpoint in near real time.

The flow should be:

```text
Chat UI
  -> FastAPI route: /v1/api/chat
  -> services/ai.py
  -> LLMTracker.track(...)
  -> Gemini API call
  -> event queued/sent to ingestion endpoint
  -> response returned to user
```

The SDK should wrap the Gemini call. It should not replace the Gemini client.

## 2. Add Environment Variables

Add these values to your backend `.env` file later:

```env
GEMINI_API_KEY=your_gemini_api_key
GEMINI_MODEL=gemini-2.5-flash

LLM_INGESTION_URL=http://localhost:8000/llm-events
LOG_INGESTION_KEY=replace_with_a_long_random_secret
LLM_LOGGING_ENABLED=true
```

Use the same `LOG_INGESTION_KEY` on the SDK sender and the ingestion receiver.

If you keep ingestion inside the same FastAPI server, `LLM_INGESTION_URL` can point to:

```text
http://localhost:8000/llm-events
```

If you create a separate ingestion service, point it there instead.

## 3. Update Config Later

Add these values in:

```text
server/src/core/config.py
```

Suggested variables:

```python
LLM_INGESTION_URL = _clean_env("LLM_INGESTION_URL")
LOG_INGESTION_KEY = _clean_env("LOG_INGESTION_KEY")
LLM_LOGGING_ENABLED = (_clean_env("LLM_LOGGING_ENABLED") or "true").lower() == "true"
```

This lets the SDK know where to send events and whether logging is enabled.

## 4. Create the SDK Module

Create a new folder:

```text
server/src/sdk/
```

Inside it, create:

```text
server/src/sdk/__init__.py
server/src/sdk/llm_tracker.py
```

`llm_tracker.py` should contain:

- a preview helper
- a `LLMTracker` class
- an async `track()` method
- a non-blocking or near-real-time event sender

Important: in this FastAPI project, do not define `TokenUsage` and `LLMInferenceEvent` again inside `llm_tracker.py`.

Define those models once in:

```text
server/src/models/llm_event_models.py
```

Then import them into:

```text
server/src/sdk/llm_tracker.py
server/src/routes/llm_event_routes.py
```

So `llm_tracker.py` is not mainly a model file. It is your SDK wrapper file.

Its job is:

1. receive the real Gemini call as a function
2. start a timer
3. run the Gemini call
4. capture success or error metadata
5. create an `LLMInferenceEvent`
6. send that event to `/llm-events`
7. return the original Gemini response back to `services/ai.py`

Recommended SDK shape:

```python
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable
from uuid import uuid4

import httpx

from models.llm_event_models import LLMInferenceEvent, TokenUsage


class LLMTracker:
    def __init__(
        self,
        provider: str,
        model: str,
        ingestion_url: str | None,
        api_key: str | None = None,
        enabled: bool = True,
    ) -> None:
        self.provider = provider
        self.model = model
        self.ingestion_url = ingestion_url
        self.api_key = api_key
        self.enabled = enabled

    async def track(
        self,
        call: Callable[[], Awaitable[Any]],
        input_text: str | None = None,
        extract_output: Callable[[Any], str | None] | None = None,
        extract_token_usage: Callable[[Any], TokenUsage | None] | None = None,
        session_id: str | None = None,
        conversation_id: str | None = None,
        request_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Any:
        started_at = _now_iso()
        start = time.perf_counter()

        try:
            result = await call()
            ended_at = _now_iso()
            latency_ms = round((time.perf_counter() - start) * 1000)

            event = LLMInferenceEvent(
                eventId=str(uuid4()),
                provider=self.provider,
                model=self.model,
                status="success",
                startedAt=started_at,
                endedAt=ended_at,
                latencyMs=latency_ms,
                sessionId=session_id,
                conversationId=conversation_id,
                requestId=request_id,
                inputPreview=_preview(input_text),
                outputPreview=_preview(extract_output(result) if extract_output else None),
                tokenUsage=extract_token_usage(result) if extract_token_usage else None,
                metadata=metadata,
            )

            self._send_soon(event)
            return result

        except Exception as error:
            ended_at = _now_iso()
            latency_ms = round((time.perf_counter() - start) * 1000)

            event = LLMInferenceEvent(
                eventId=str(uuid4()),
                provider=self.provider,
                model=self.model,
                status="error",
                startedAt=started_at,
                endedAt=ended_at,
                latencyMs=latency_ms,
                sessionId=session_id,
                conversationId=conversation_id,
                requestId=request_id,
                inputPreview=_preview(input_text),
                errorType=type(error).__name__,
                errorMessage=str(error),
                metadata=metadata,
            )

            self._send_soon(event)
            raise

    def _send_soon(self, event: LLMInferenceEvent) -> None:
        if not self.enabled or not self.ingestion_url:
            return

        asyncio.create_task(self._send_event(event))

    async def _send_event(self, event: LLMInferenceEvent) -> None:
        headers = {"content-type": "application/json"}
        if self.api_key:
            headers["authorization"] = f"Bearer {self.api_key}"

        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                await client.post(
                    self.ingestion_url,
                    headers=headers,
                    json=event.model_dump(exclude_none=True),
                )
        except Exception:
            # Do not break chat responses if logging fails.
            pass


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _preview(value: str | None, max_length: int = 300) -> str | None:
    if not value:
        return None

    clean = " ".join(value.split())
    if len(clean) <= max_length:
        return clean

    return clean[: max_length - 3] + "..."
```

Important: this example uses `httpx`, so add it to dependencies later if it is not already installed.

With Poetry:

```powershell
cd server
poetry add httpx
```

## 5. How `track()` Wraps the Gemini Call

This is the main idea.

Right now your code in `services/ai.py` calls Gemini directly:

```python
response = await asyncio.to_thread(
    client.models.generate_content,
    model=GEMINI_MODEL,
    contents=prompt,
)
```

That works, but there is no metadata logging around it.

After adding the SDK, you do not remove the Gemini call. You pass that same Gemini call into `tracker.track()`:

```python
response = await tracker.track(
    call=lambda: asyncio.to_thread(
        client.models.generate_content,
        model=GEMINI_MODEL,
        contents=prompt,
    ),
    input_text=prompt,
    extract_output=lambda result: (result.text or "").strip(),
    extract_token_usage=extract_gemini_token_usage,
    conversation_id=conversation_id,
    request_id=str(uuid4()),
    metadata={
        "route": "/v1/api/chat",
        "maxContextMessages": MAX_CONTEXT_MESSAGES,
    },
)
```

The important part is this:

```python
call=lambda: asyncio.to_thread(
    client.models.generate_content,
    model=GEMINI_MODEL,
    contents=prompt,
)
```

That `call` is the real LLM request. The SDK runs it internally:

```python
result = await call()
```

Because the SDK runs the call internally, it can measure everything around it:

```text
startedAt before the call
latency while the call runs
endedAt after the call
status success/error
inputPreview from prompt
outputPreview from response.text
tokenUsage from response.usage_metadata
conversationId from your chat flow
```

Then `track()` returns the original Gemini response:

```python
assistant_text = (response.text or "").strip()
```

So your chat service still receives a normal Gemini response. The only difference is that metadata is captured and sent in the background.

For one successful chat request, the SDK will create an event like this:

```json
{
  "eventId": "3de6ef8f-7c2f-4d8e-95dd-7bb0b4c8e9fd",
  "provider": "gemini",
  "model": "gemini-2.5-flash",
  "status": "success",
  "startedAt": "2026-06-07T05:30:00+00:00",
  "endedAt": "2026-06-07T05:30:01+00:00",
  "latencyMs": 1000,
  "conversationId": "conversation-id-from-your-chat",
  "requestId": "single-request-id",
  "inputPreview": "You are a helpful assistant...",
  "outputPreview": "Sure, here is the answer...",
  "tokenUsage": {
    "inputTokens": 120,
    "outputTokens": 40,
    "totalTokens": 160
  },
  "metadata": {
    "route": "/v1/api/chat",
    "maxContextMessages": 8
  }
}
```

The flow inside `track()` is basically:

```python
started_at = now()
start_timer()

try:
    result = await call()
    event = build_success_event(result)
    send_event_in_background(event)
    return result
except Exception as error:
    event = build_error_event(error)
    send_event_in_background(event)
    raise
```

## 6. Extract Gemini Token Usage

Gemini responses may expose token usage metadata depending on the SDK response shape.

Add this helper near the Gemini service in:

```text
server/src/services/ai.py
```

This keeps `llm_tracker.py` provider-agnostic. The SDK should not need to know Gemini-specific response field names.

```python
def extract_gemini_token_usage(response) -> TokenUsage | None:
    usage = getattr(response, "usage_metadata", None)
    if usage is None:
        return None

    input_tokens = getattr(usage, "prompt_token_count", None)
    output_tokens = getattr(usage, "candidates_token_count", None)
    total_tokens = getattr(usage, "total_token_count", None)

    return TokenUsage(
        inputTokens=input_tokens,
        outputTokens=output_tokens,
        totalTokens=total_tokens,
    )
```

If Gemini does not return usage metadata for a call, keep `tokenUsage` empty. The log is still useful because latency, status, model, timestamps, and previews are captured.

## 7. Wire the SDK into `services/ai.py`

Later, update:

```text
server/src/services/ai.py
```

Import the tracker:

```python
from core.config import (
    GEMINI_API_KEY,
    GEMINI_MODEL,
    LLM_INGESTION_URL,
    LOG_INGESTION_KEY,
    LLM_LOGGING_ENABLED,
)
from models.llm_event_models import TokenUsage
from sdk.llm_tracker import LLMTracker
```

Create one tracker near your Gemini client:

```python
tracker = LLMTracker(
    provider="gemini",
    model=GEMINI_MODEL,
    ingestion_url=LLM_INGESTION_URL,
    api_key=LOG_INGESTION_KEY,
    enabled=LLM_LOGGING_ENABLED,
)
```

Then replace the direct Gemini call:

```python
response = await asyncio.to_thread(
    client.models.generate_content,
    model=GEMINI_MODEL,
    contents=prompt,
)
```

with a tracked call:

```python
request_id = str(uuid4())

response = await tracker.track(
    call=lambda: asyncio.to_thread(
        client.models.generate_content,
        model=GEMINI_MODEL,
        contents=prompt,
    ),
    input_text=prompt,
    extract_output=lambda result: (result.text or "").strip(),
    extract_token_usage=extract_gemini_token_usage,
    conversation_id=conversation_id,
    request_id=request_id,
    metadata={
        "route": "/v1/api/chat",
        "maxContextMessages": MAX_CONTEXT_MESSAGES,
    },
)
```

This keeps your existing response handling almost exactly the same:

```python
assistant_text = (response.text or "").strip()
```

## 8. Add an Ingestion Route

You have two options.

Option A: keep ingestion inside the same FastAPI backend.

Create:

```text
server/src/models/llm_event_models.py
server/src/routes/llm_event_routes.py
```

`llm_event_models.py` should define the request schema:

```python
from typing import Any, Literal

from pydantic import BaseModel, Field


class TokenUsage(BaseModel):
    inputTokens: int | None = Field(default=None, ge=0)
    outputTokens: int | None = Field(default=None, ge=0)
    totalTokens: int | None = Field(default=None, ge=0)


class LLMInferenceEvent(BaseModel):
    eventId: str
    provider: str
    model: str
    status: Literal["success", "error"]
    startedAt: str
    endedAt: str
    latencyMs: int = Field(ge=0)
    errorType: str | None = None
    errorMessage: str | None = None
    sessionId: str | None = None
    conversationId: str | None = None
    requestId: str | None = None
    inputPreview: str | None = None
    outputPreview: str | None = None
    tokenUsage: TokenUsage | None = None
    metadata: dict[str, Any] | None = None


class LLMEventBatch(BaseModel):
    events: list[LLMInferenceEvent]
```

`llm_event_routes.py` should accept either a single event or a batch. At first, you can print or store events later:

```python
from fastapi import APIRouter, Header, HTTPException, status

from core.config import LOG_INGESTION_KEY
from models.llm_event_models import LLMEventBatch, LLMInferenceEvent

router = APIRouter()


@router.post("/llm-events", status_code=status.HTTP_202_ACCEPTED)
async def ingest_llm_event(
    payload: LLMInferenceEvent | LLMEventBatch,
    authorization: str | None = Header(default=None),
) -> dict[str, int | bool]:
    expected = f"Bearer {LOG_INGESTION_KEY}"
    if LOG_INGESTION_KEY and authorization != expected:
        raise HTTPException(status_code=401, detail="Unauthorized")

    events = payload.events if isinstance(payload, LLMEventBatch) else [payload]

    # First step: print/log the events.
    # Later: insert them into SQLite, Postgres, MongoDB, ClickHouse, etc.
    for event in events:
        print(event.model_dump())

    return {"ok": True, "accepted": len(events)}
```

Then include the route in:

```text
server/src/server.py
```

```python
from routes import llm_event_routes, run_ai_routes

app.include_router(run_ai_routes.router)
app.include_router(llm_event_routes.router)
```

Option B: create a separate ingestion service.

Use `ingestion.md` if you want the ingestion service to be separate from this FastAPI app. In that setup, the FastAPI SDK sends events to the separate service, and the separate service stores them.

For this project, Option A is simpler because you already have FastAPI running.

## 9. Store Logs Later

For the first pass, printing events is enough to prove the SDK works.

After that, store logs in a database.

Good local choices:

- SQLite for simple local testing
- MongoDB because `pymongo` is already in your dependencies
- Postgres for production-style relational queries

The ingestion schema from `ingestion.md` is still useful. Store these fields as queryable columns:

```text
event_id
provider
model
status
error_type
error_message
started_at
ended_at
latency_ms
session_id
conversation_id
request_id
input_preview
output_preview
input_tokens
output_tokens
total_tokens
metadata_json
raw_event_json
received_at
```

## 10. How to Start the Backend

From the project root:

```powershell
cd server
poetry install
```

If you added `httpx`:

```powershell
poetry add httpx
```

Run the FastAPI server:

```powershell
poetry run uvicorn src.server:app --reload --host 0.0.0.0 --port 8000
```

Your chat endpoint should be:

```text
http://localhost:8000/v1/api/chat
```

Your ingestion endpoint should be:

```text
http://localhost:8000/llm-events
```

## 11. How to Test the Ingestion Endpoint

After adding the ingestion route, test it directly:

```powershell
$headers = @{
  Authorization = "Bearer replace_with_a_long_random_secret"
  "Content-Type" = "application/json"
}

$body = @{
  eventId = "test-event-1"
  provider = "gemini"
  model = "gemini-2.5-flash"
  status = "success"
  startedAt = "2026-06-07T00:00:00+00:00"
  endedAt = "2026-06-07T00:00:01+00:00"
  latencyMs = 1000
  conversationId = "test-conversation"
  inputPreview = "hello"
  outputPreview = "hi"
} | ConvertTo-Json

Invoke-RestMethod `
  -Method Post `
  -Uri "http://localhost:8000/llm-events" `
  -Headers $headers `
  -Body $body
```

Expected response:

```json
{
  "ok": true,
  "accepted": 1
}
```

## 12. How to Test the Full Flow

Once the SDK is wired into `services/ai.py`:

1. Start the FastAPI backend.
2. Start the React chat UI.
3. Send a chat message.
4. Confirm the chat response still works.
5. Check the FastAPI terminal.
6. You should see one metadata event printed for the Gemini call.

If the ingestion endpoint is down or the key is wrong, the chat response should still work. Logging failure should not break the user experience.

## 13. Recommended Implementation Order

Do it in this order:

1. Add config variables in `server/src/core/config.py`.
2. Create `server/src/models/llm_event_models.py`.
3. Create `server/src/sdk/llm_tracker.py`.
4. Add `httpx` dependency.
5. Create `server/src/routes/llm_event_routes.py`.
6. Include the ingestion router in `server/src/server.py`.
7. Test `/llm-events` manually.
8. Add `extract_gemini_token_usage()` in `server/src/services/ai.py`.
9. Wrap the Gemini call in `server/src/services/ai.py`.
10. Send a real chat message.
11. Confirm metadata logs appear.
12. Add database storage after the event shape is stable.

## 14. What Not to Do

Do not put SDK logic inside `run_ai_routes.py`.

The route should only receive the request, call the service, and return the response.

Do not store full prompts and full responses by default.

Use previews first. This is safer and keeps the log payload small.

Do not block the chat response on logging.

The SDK should send logs in the background. If ingestion fails, the LLM response should still return to the user.

## 15. Final Target Structure

After implementation, the backend should look like this:

```text
server/
  src/
    server.py
    core/
      config.py
    models/
      chat_models.py
      llm_event_models.py
    routes/
      run_ai_routes.py
      llm_event_routes.py
    sdk/
      __init__.py
      llm_tracker.py
    services/
      ai.py
```

The main idea:

```text
run_ai_routes.py
  calls run_assistant()

services/ai.py
  builds prompt
  calls tracker.track(...)
  returns ChatResponse

sdk/llm_tracker.py
  measures metadata
  sends event to /llm-events

routes/llm_event_routes.py
  receives metadata
  validates auth
  stores or prints event
```
