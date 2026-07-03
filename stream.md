# Streaming Chat Responses: WebSocket vs HTTP, and What Must Change

This document answers the current question:

> We send a message from the frontend with `ChatApi.chat()`, the backend route calls `run_assistant()`, and `run_assistant()` waits for the full provider response before saving and returning it. Why are we not using WebSocket? Do we need a WebSocket class? Will WebSocket help stream responses? What needs to change in models, routes, provider, services `ai.py`, database, SDK, and frontend?

Short answer:

- You are not using WebSocket right now because the current API is a normal request/response HTTP flow.
- WebSocket can stream responses, but it is not the only way.
- For "assistant text comes token by token from server to browser", Server-Sent Events or `fetch()` streaming is usually simpler than WebSocket.
- Use WebSocket if you want two-way realtime behavior: cancel generation, user typing events, live tool progress, multiple tabs, multi-user chat, or bidirectional agent events.
- You do not strictly need a "WebSocket class", but creating a small frontend WebSocket client wrapper or React hook helps keep the component clean.
- The biggest backend change is not the WebSocket object. The biggest change is converting `run_assistant()` from "return one final `ChatResponse`" into "yield many stream events, then save the final assistant message".

---

## 1. What Your Current Flow Does

Current frontend:

```ts
const response = await ChatApi.chat({
  conversationId,
  message,
  provider: selectedProvider,
  model: selectedModel,
});

setConversationId(response.conversationId);
setMessages((currentMessages) => [
  ...currentMessages,
  response.message,
]);
```

Current API client:

```ts
chat: async (data: ChatRequestType): Promise<ChatResponseType> => {
  const response = await axiosInstance.post("/v1/api/chat", data);
  return ChatResponseSchema.parse(response.data);
}
```

Current backend route:

```py
@router.post("/v1/api/chat", response_model=ChatResponse)
async def chat(payload: ChatRequest) -> ChatResponse:
    return await run_assistant(payload)
```

Current service:

```py
result = await tracker.track(
    call=lambda: provider.chat(...),
    extract_output=lambda chat_result: chat_result.text,
)

insert_message(... role=User ...)
insert_message(... role=Assistant, content=result.text ...)

return ChatResponse(...)
```

This design waits for the full LLM result. The browser cannot show partial text because the backend does not send partial text.

The important point:

> Streaming is not mainly a frontend issue. The provider, service, route, SDK tracker, and frontend must all agree that output arrives as chunks/events, not as one final object.

---

## 2. Why You Are Not Using WebSocket Currently

You are not using WebSocket because your backend route is an HTTP `POST` endpoint:

```py
@router.post("/v1/api/chat")
```

That route receives one request and returns one response.

Your frontend uses Axios:

```ts
axiosInstance.post("/v1/api/chat", data)
```

Axios is also built around normal HTTP request/response. It will not give you each token as it arrives from the model in the browser. It waits for the response to complete.

So the current system is good for:

- simple chat requests
- easy validation with `response_model=ChatResponse`
- easy error handling with HTTP status codes
- saving messages after the LLM call finishes

But it is not good for:

- showing tokens as they arrive
- showing "time to first token"
- canceling a running generation over the same connection
- sending progress events from tools

---

## 3. Does WebSocket Help With Streaming?

Yes, WebSocket can help stream responses.

With WebSocket:

1. Browser opens a persistent connection.
2. Browser sends the user message as JSON.
3. Backend sends events like:
   - `start`
   - `chunk`
   - `done`
   - `error`
4. Frontend appends every `chunk` to the assistant message in the UI.
5. Backend saves the final assistant text after the stream finishes.

Example event flow:

```txt
frontend -> backend:
{
  "type": "message",
  "conversationId": null,
  "message": "Explain websockets",
  "provider": "gemini",
  "model": "gemini-3.5-flash"
}

backend -> frontend:
{ "type": "start", "conversationId": "abc", "provider": "gemini", "model": "gemini-3.5-flash" }

backend -> frontend:
{ "type": "chunk", "content": "WebSockets" }

backend -> frontend:
{ "type": "chunk", "content": " keep a persistent connection" }

backend -> frontend:
{ "type": "done", "message": { "role": "Assistant", "content": "WebSockets keep a persistent connection..." } }
```

But WebSocket is not required for basic one-way streaming.

---

## 4. WebSocket vs SSE vs Fetch Streaming

For this project, you have three realistic options.

### Option A: Keep Current POST

Endpoint:

```txt
POST /v1/api/chat
```

Best for:

- simple implementation
- no streaming
- normal JSON response

Tradeoff:

- user waits until the full response is ready

### Option B: Add HTTP Streaming or SSE

Endpoint:

```txt
POST /v1/api/chat/stream
```

or:

```txt
GET /v1/api/chat/stream?... 
```

Using FastAPI `StreamingResponse`.

Best for:

- assistant text streaming from backend to frontend
- simpler than WebSocket
- works well when communication is mostly one-way

Tradeoff:

- cancel/control messages are less clean than WebSocket
- if you use classic SSE, browser `EventSource` only supports GET, so POST payloads are awkward
- `fetch()` streaming with newline-delimited JSON is often more convenient than `EventSource`

### Option C: Add WebSocket

Endpoint:

```txt
WS /v1/ws/chat
```

Best for:

- token streaming
- cancellation
- bidirectional events
- future tool progress
- long-running agent workflows
- realtime collaboration style features

Tradeoff:

- more moving pieces
- you manually handle validation, close codes, connection lifecycle, reconnects, and frontend state
- FastAPI `response_model` does not apply to WebSocket messages

My recommendation:

> If your only goal is "show the assistant response as it is generated", start with `StreamingResponse` using newline-delimited JSON events. If you want a more realtime chat transport with cancellation and future agent events, use WebSocket.

Since you specifically asked about WebSocket, the rest of this doc explains the WebSocket path.

---

## 5. Do You Need a WebSocket Class?

Backend:

- No, FastAPI does not require a WebSocket class.
- You can add a route with `@router.websocket("/v1/ws/chat")`.
- For one-user chat, a route function is enough.

Frontend:

- No, the browser already has the `WebSocket` class.
- But a small wrapper or hook is a good idea because the React component should not contain all socket lifecycle logic.

Good frontend shape:

```txt
chatui/src/api/chat.socket.ts
```

or:

```txt
chatui/src/hooks/useChatSocket.ts
```

This wrapper can handle:

- opening the socket
- sending a chat request
- parsing stream events
- reconnect or cleanup
- closing on component unmount
- mapping backend events into UI updates

You do not need a complex connection manager unless you support many rooms/users/conversations at the same time.

---

## 6. Important Current Code Issue

Your provider interface and service are currently inconsistent.

In `server/src/provider/base.py`, the protocol says:

```py
async def chat(...) -> AsyncIterator[str]:
    ...
```

That means `provider.chat()` should stream chunks.

But `server/src/services/ai.py` expects:

```py
result.text
result.token_usage
```

That means `provider.chat()` should return a full `ProviderChatResult`.

Your concrete providers are mixed:

- `OpenAIProvider.chat()` returns `ProviderChatResult`
- `AnthropicProvider.chat()` yields text chunks
- `GeminiProvider.chat()` yields text chunks, but currently collects all chunks first before yielding

This mismatch must be fixed before streaming is clean.

Recommended provider design:

```py
class ChatProvider(Protocol):
    async def chat(...) -> ProviderChatResult:
        ...

    async def stream_chat(...) -> AsyncIterator[str]:
        ...
```

Then:

- existing `POST /v1/api/chat` can call `provider.chat()`
- new WebSocket route can call `provider.stream_chat()`
- providers can implement `chat()` by collecting `stream_chat()` if needed

This keeps backward compatibility and avoids breaking the current frontend immediately.

---

## 7. Event Contract for Streaming

Do not send plain text chunks only. Send typed JSON events.

That gives the frontend enough information to update state correctly.

Recommended backend-to-frontend event types:

```ts
type ChatStreamEvent =
  | {
      type: "start";
      conversationId: string;
      provider: "anthropic" | "openai" | "gemini";
      model: string;
      requestId: string;
    }
  | {
      type: "chunk";
      content: string;
    }
  | {
      type: "done";
      conversationId: string;
      message: {
        role: "Assistant";
        content: string;
      };
      provider: "anthropic" | "openai" | "gemini";
      model: string;
    }
  | {
      type: "error";
      message: string;
    };
```

Optional future events:

```ts
type OptionalChatStreamEvent =
  | { type: "typing"; value: boolean }
  | { type: "tool_start"; name: string }
  | { type: "tool_output"; name: string; content: string }
  | { type: "tool_done"; name: string }
  | { type: "cancelled" };
```

For this repo, start with:

- `start`
- `chunk`
- `done`
- `error`

---

## 8. Backend Models: What to Change

File:

```txt
server/src/models/chat_models.py
```

Keep existing models:

```py
ChatRequest
ChatResponse
ChatMessage
```

Add stream event models:

```py
from typing import Literal, Union

class ChatStreamStart(BaseModel):
    type: Literal["start"] = "start"
    conversationId: str
    provider: ProviderType
    model: str
    requestId: str

class ChatStreamChunk(BaseModel):
    type: Literal["chunk"] = "chunk"
    content: str

class ChatStreamDone(BaseModel):
    type: Literal["done"] = "done"
    conversationId: str
    message: ChatMessage
    provider: ProviderType
    model: str

class ChatStreamError(BaseModel):
    type: Literal["error"] = "error"
    message: str

ChatStreamEvent = Union[
    ChatStreamStart,
    ChatStreamChunk,
    ChatStreamDone,
    ChatStreamError,
]
```

Notes:

- WebSocket routes do not use `response_model`, but these models are still useful.
- Use `.model_dump()` before sending JSON through the WebSocket.
- Keep `ChatRequest` as the inbound shape so the same payload works for POST and WebSocket.

Potential issue:

`ChatResponse.provider` is currently typed as `ProviderType`, but `provider.id` is a plain string from provider objects. It works if values match the enum, but a cleaner service should cast:

```py
ProviderType(provider.id)
```

or make response provider a string. I would keep the enum.

---

## 9. Frontend Schemas: What to Change

File:

```txt
chatui/src/schemas/run_ai.schemas.ts
```

Add stream event schemas:

```ts
export const ChatStreamStartSchema = z.object({
  type: z.literal("start"),
  conversationId: z.string(),
  provider: ProviderTypeSchema,
  model: z.string(),
  requestId: z.string(),
});

export const ChatStreamChunkSchema = z.object({
  type: z.literal("chunk"),
  content: z.string(),
});

export const ChatStreamDoneSchema = z.object({
  type: z.literal("done"),
  conversationId: z.string(),
  message: ChatMessageSchema,
  provider: ProviderTypeSchema,
  model: z.string(),
});

export const ChatStreamErrorSchema = z.object({
  type: z.literal("error"),
  message: z.string(),
});

export const ChatStreamEventSchema = z.discriminatedUnion("type", [
  ChatStreamStartSchema,
  ChatStreamChunkSchema,
  ChatStreamDoneSchema,
  ChatStreamErrorSchema,
]);

export type ChatStreamEventType = z.infer<typeof ChatStreamEventSchema>;
```

This lets the frontend parse every incoming WebSocket message safely.

---

## 10. Provider Layer: What to Change

Files:

```txt
server/src/provider/base.py
server/src/provider/open_ai_provider.py
server/src/provider/gemini_provider.py
server/src/provider/anthropic_provider.py
```

Recommended contract:

```py
from typing import AsyncIterator, Protocol

class ChatProvider(Protocol):
    id: str
    label: str
    default_model: str
    models: list[str]

    @property
    def configured(self) -> bool:
        ...

    def resolve_model(self, requested_model: str | None) -> str:
        ...

    async def chat(
        self,
        *,
        messages: list[ChatMessage],
        model: str,
        system_prompt: str,
    ) -> ProviderChatResult:
        ...

    async def stream_chat(
        self,
        *,
        messages: list[ChatMessage],
        model: str,
        system_prompt: str,
    ) -> AsyncIterator[str]:
        ...
```

Then each provider can implement both.

### Anthropic

Anthropic already uses streaming:

```py
async with self._client.messages.stream(...) as stream:
    async for text_chunk in stream.text_stream:
        yield text_chunk
```

Move this into `stream_chat()`.

Then implement `chat()` as:

```py
async def chat(...) -> ProviderChatResult:
    parts: list[str] = []
    async for chunk in self.stream_chat(...):
        parts.append(chunk)

    text = "".join(parts).strip()
    if not text:
        raise RuntimeError("Anthropic returned an empty response.")

    return ProviderChatResult(text=text, token_usage=None)
```

Token usage may be missing in streaming unless you explicitly capture the final message metadata from the provider stream. That can be improved later.

### Gemini

Gemini currently does this:

```py
chunks = await asyncio.to_thread(_stream_sync)

for chunk in chunks:
    yield chunk
```

This is not true browser-visible streaming because it collects all chunks first. The frontend will only receive chunks after Gemini has already finished.

Better approach:

- use an async Gemini streaming API if available in your installed SDK
- or run the blocking stream in a thread and push chunks into an async queue as they arrive

Conceptual shape:

```py
async def stream_chat(...) -> AsyncIterator[str]:
    queue: asyncio.Queue[str | None] = asyncio.Queue()

    def produce() -> None:
        try:
            for chunk in self._client.models.generate_content_stream(...):
                if chunk.text:
                    asyncio.run_coroutine_threadsafe(queue.put(chunk.text), loop)
        finally:
            asyncio.run_coroutine_threadsafe(queue.put(None), loop)

    loop = asyncio.get_running_loop()
    thread = threading.Thread(target=produce, daemon=True)
    thread.start()

    while True:
        item = await queue.get()
        if item is None:
            break
        yield item
```

That way chunks are yielded while the provider is still producing them.

### OpenAI

OpenAI currently uses:

```py
response = await self._client.responses.create(...)
text = response.output_text
```

Add a streaming version.

Depending on the OpenAI SDK version, the exact API may differ. The shape is usually:

```py
async with self._client.responses.stream(
    model=model,
    instructions=system_prompt,
    input=_messages_to_openai_input(messages),
) as stream:
    async for event in stream:
        if event.type == "response.output_text.delta":
            yield event.delta
```

Then `chat()` can either keep the existing non-streaming call or collect `stream_chat()`.

Recommended for consistency:

- `stream_chat()` uses the provider-native stream
- `chat()` either uses non-stream call for better token usage or collects stream chunks for shared behavior

---

## 11. Service Layer: What to Change in `ai.py`

File:

```txt
server/src/services/ai.py
```

Keep existing:

```py
async def run_assistant(payload: ChatRequest) -> ChatResponse:
    ...
```

Add:

```py
async def stream_assistant(payload: ChatRequest) -> AsyncIterator[ChatStreamEvent]:
    ...
```

The streaming service should:

1. Create or reuse `conversation_id`.
2. Load previous messages.
3. Build context.
4. Resolve provider and model.
5. Create `request_id`.
6. Yield a `start` event immediately.
7. Insert/upsert the conversation and user message.
8. Stream chunks from `provider.stream_chat()`.
9. Accumulate chunks into `assistant_text`.
10. Yield every chunk as a `chunk` event.
11. Save final assistant message after stream completes.
12. Track/log the event.
13. Yield a `done` event.

Pseudo-code:

```py
async def stream_assistant(payload: ChatRequest) -> AsyncIterator[ChatStreamEvent]:
    conversation_id = payload.conversationId or str(uuid4())
    now = _now_iso()

    db_messages = get_message_for_conversations(conversation_id)
    messages = _build_context(db_messages, payload.message)

    existing = get_conversation_db(conversation_id)
    title = existing["title"] if existing else _make_title(payload.message)
    created_at = existing["created_at"] if existing else now

    provider = get_provider(payload.provider.value if payload.provider else None)
    model = provider.resolve_model(payload.model)
    request_id = str(uuid4())

    yield ChatStreamStart(
        conversationId=conversation_id,
        provider=ProviderType(provider.id),
        model=model,
        requestId=request_id,
    )

    upsert_conversation(
        conversation_id=conversation_id,
        title=title,
        provider=provider.id,
        model=model,
        created_at=created_at,
        updated_at=now,
    )

    insert_message(
        conversation_id,
        role=RoleType.USER.value,
        content=payload.message,
        created_at=now,
    )

    parts: list[str] = []

    async for chunk in provider.stream_chat(
        messages=messages,
        model=model,
        system_prompt=ASSISTANT_PROMPT,
    ):
        parts.append(chunk)
        yield ChatStreamChunk(content=chunk)

    assistant_text = "".join(parts).strip()
    if not assistant_text:
        raise RuntimeError(f"{provider.id} returned an empty response.")

    done_at = _now_iso()

    insert_message(
        conversation_id,
        role=RoleType.ASSISTANT.value,
        content=assistant_text,
        created_at=done_at,
    )

    upsert_conversation(
        conversation_id=conversation_id,
        title=title,
        provider=provider.id,
        model=model,
        created_at=created_at,
        updated_at=done_at,
    )

    yield ChatStreamDone(
        conversationId=conversation_id,
        message=ChatMessage(role=RoleType.ASSISTANT, content=assistant_text),
        provider=ProviderType(provider.id),
        model=model,
    )
```

Important design choice:

Save the user message before streaming starts. Save the assistant message only after the stream completes.

Why?

- If the stream fails, you still know the user asked something.
- You do not save incomplete assistant text as a normal assistant message.
- Later you can add a `status` column if you want to store partial/failed assistant messages.

Potential improvement:

- If client disconnects halfway, decide whether to save partial assistant output or discard it.
- I would discard it at first unless you add message status fields.

---

## 12. Route Layer: What to Change

File:

```txt
server/src/routes/run_ai_routes.py
```

Keep existing HTTP route:

```py
@router.post("/v1/api/chat", response_model=ChatResponse)
async def chat(payload: ChatRequest) -> ChatResponse:
    ...
```

Add WebSocket route:

```py
from fastapi import WebSocket, WebSocketDisconnect
from pydantic import ValidationError
```

Example:

```py
@router.websocket("/v1/ws/chat")
async def chat_ws(websocket: WebSocket) -> None:
    await websocket.accept()

    try:
        while True:
            raw_payload = await websocket.receive_json()
            payload = ChatRequest.model_validate(raw_payload)

            try:
                async for event in stream_assistant(payload):
                    await websocket.send_json(event.model_dump())
            except UnknownProviderError as error:
                await websocket.send_json({
                    "type": "error",
                    "message": str(error),
                })
            except ModelNotAllowedError as error:
                await websocket.send_json({
                    "type": "error",
                    "message": str(error),
                })
            except ProviderNotConfiguredError as error:
                await websocket.send_json({
                    "type": "error",
                    "message": str(error),
                })
            except Exception as error:
                await websocket.send_json({
                    "type": "error",
                    "message": str(error),
                })

    except ValidationError as error:
        await websocket.send_json({
            "type": "error",
            "message": "Invalid chat request.",
        })
        await websocket.close(code=1003)
    except WebSocketDisconnect:
        pass
```

Notes:

- WebSocket routes do not have `response_model`.
- You manually validate incoming JSON with `ChatRequest.model_validate(...)`.
- You manually send errors as JSON events.
- You can keep the connection open for multiple messages or close after one message.

For a simple first implementation, I recommend one WebSocket message in, one assistant stream out, then keep open for the next user message.

### Alternative: HTTP Streaming Route

If you choose not to use WebSocket, use:

```py
from fastapi.responses import StreamingResponse
import json

@router.post("/v1/api/chat/stream")
async def chat_stream(payload: ChatRequest) -> StreamingResponse:
    async def events():
        async for event in stream_assistant(payload):
            yield json.dumps(event.model_dump()) + "\n"

    return StreamingResponse(events(), media_type="application/x-ndjson")
```

Frontend would then use `fetch()` and read `response.body`.

This is simpler than WebSocket and is often enough.

---

## 13. Frontend API: What to Change

File:

```txt
chatui/src/api/chat.api.ts
```

You can keep `ChatApi.chat()` for non-streaming.

Add a socket helper in a new file:

```txt
chatui/src/api/chat.socket.ts
```

Example:

```ts
import { ChatStreamEventSchema } from "../schemas/run_ai.schemas";
import type {
  ChatRequestType,
  ChatStreamEventType,
} from "../schemas/run_ai.schemas";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

function toWsUrl(httpUrl: string): string {
  return httpUrl.replace(/^http/, "ws");
}

export function streamChat(
  data: ChatRequestType,
  handlers: {
    onEvent: (event: ChatStreamEventType) => void;
    onError: (message: string) => void;
    onClose?: () => void;
  },
): WebSocket {
  const socket = new WebSocket(`${toWsUrl(API_BASE_URL)}/v1/ws/chat`);

  socket.onopen = () => {
    socket.send(JSON.stringify(data));
  };

  socket.onmessage = (message) => {
    const parsed = ChatStreamEventSchema.safeParse(JSON.parse(message.data));
    if (!parsed.success) {
      handlers.onError("Received an invalid streaming event.");
      return;
    }

    handlers.onEvent(parsed.data);
  };

  socket.onerror = () => {
    handlers.onError("Streaming connection failed.");
  };

  socket.onclose = () => {
    handlers.onClose?.();
  };

  return socket;
}
```

Important:

- Your existing `axiosInstance` base URL helps HTTP calls.
- Native WebSocket does not use Axios.
- You need to derive `ws://` or `wss://` from the backend URL.
- In production, if HTTP is `https://`, WebSocket should be `wss://`.

---

## 14. Frontend Chat Page: What to Change

File:

```txt
chatui/src/pages/Chat.tsx
```

Current logic:

```ts
const response = await ChatApi.chat(...)
setMessages((currentMessages) => [
  ...currentMessages,
  response.message,
]);
```

Streaming logic:

1. Add the user message immediately.
2. Add an empty assistant message immediately.
3. Open WebSocket and send request.
4. On `start`, set `conversationId`.
5. On `chunk`, append chunk to the last assistant message.
6. On `done`, replace final assistant message with the backend's final message.
7. On `error`, show error and optionally remove the empty assistant message.

Pseudo-code:

```ts
const assistantMessage: ChatMessageType = {
  role: "Assistant",
  content: "",
};

setMessages((currentMessages) => [
  ...currentMessages,
  userMessage,
  assistantMessage,
]);

const socket = streamChat(
  {
    conversationId,
    message,
    provider: selectedProvider,
    model: selectedModel,
  },
  {
    onEvent: (event) => {
      if (event.type === "start") {
        setConversationId(event.conversationId);
        return;
      }

      if (event.type === "chunk") {
        setMessages((currentMessages) => {
          const next = [...currentMessages];
          const last = next[next.length - 1];

          if (last?.role === "Assistant") {
            next[next.length - 1] = {
              ...last,
              content: last.content + event.content,
            };
          }

          return next;
        });
        return;
      }

      if (event.type === "done") {
        setMessages((currentMessages) => {
          const next = [...currentMessages];
          const last = next[next.length - 1];

          if (last?.role === "Assistant") {
            next[next.length - 1] = event.message;
          }

          return next;
        });
        setIsSending(false);
        return;
      }

      if (event.type === "error") {
        setError(event.message);
        setIsSending(false);
      }
    },
    onError: (message) => {
      setError(message);
      setIsSending(false);
    },
    onClose: () => {
      setIsSending(false);
    },
  },
);
```

You should store the socket in a ref so you can close it:

```ts
const socketRef = useRef<WebSocket | null>(null);
```

On new submit:

```ts
socketRef.current?.close();
socketRef.current = socket;
```

On unmount:

```ts
useEffect(() => {
  return () => socketRef.current?.close();
}, []);
```

For a cancel button later:

```ts
socketRef.current?.send(JSON.stringify({ type: "cancel" }));
```

But cancellation requires backend support too.

---

## 15. Database: What to Change

Files:

```txt
server/src/db/db.py
server/llm-events.sql
```

For the first streaming version:

> No schema change is required.

Your current tables are enough:

```sql
conversations
conversation_messages
llm_inference_events
```

Recommended behavior:

- Insert user message when streaming starts.
- Insert assistant message only when streaming completes.
- Update conversation `updated_at` when streaming completes.

What not to do initially:

- Do not insert one database row per chunk.
- Do not update the assistant message every token.

That would cause unnecessary DB writes.

Potential future DB changes:

Add message status if you want to preserve failed or partial assistant messages:

```sql
ALTER TABLE conversation_messages
ADD COLUMN status VARCHAR(40) NOT NULL DEFAULT 'complete';
```

Possible statuses:

- `streaming`
- `complete`
- `failed`
- `cancelled`

Then you could:

- insert an assistant message with empty content and `status='streaming'`
- update it while streaming or at the end
- mark it `failed` if provider fails

But for now, do not add this unless you actually want partial message history.

---

## 16. SDK Tracker: What to Change

File:

```txt
server/src/sdk/llm_event_tracker.py
```

Current tracker:

```py
async def track(
    self,
    call: Callable[[], Awaitable[Any]],
    extract_output: Callable[[Any], str | None] | None = None,
    ...
) -> Any:
    result = await call()
    outputPreview = extract_output(result)
```

This works for non-streaming because there is one final result.

For streaming, the tracker needs either:

### Option A: Track manually in `stream_assistant()`

Simpler first version.

In `stream_assistant()`:

- record `started_at`
- record start time
- count chunks
- track first chunk time
- accumulate output
- on success, call a new tracker helper to send a completed event
- on error, call a tracker helper to send an error event

This requires exposing something like:

```py
tracker.send_success(...)
tracker.send_error(...)
```

or adding a small method:

```py
tracker.track_completed_stream(...)
```

### Option B: Add `track_stream()` to `LLMTracker`

Cleaner long-term design.

Example shape:

```py
async def track_stream(
    self,
    *,
    stream: AsyncIterator[str],
    input_text: str | None = None,
    session_id: str | None = None,
    conversation_id: str | None = None,
    request_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> AsyncIterator[str]:
    started_at = _now_iso()
    start = time.perf_counter()
    first_chunk_at: float | None = None
    chunk_count = 0
    parts: list[str] = []

    try:
        async for chunk in stream:
            if chunk and first_chunk_at is None:
                first_chunk_at = time.perf_counter()

            chunk_count += 1
            parts.append(chunk)
            yield chunk

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
            conversationId=conversation_id,
            requestId=request_id,
            inputPreview=_preview(input_text),
            outputPreview=_preview("".join(parts)),
            metadata={
                **(metadata or {}),
                "stream": True,
                "chunkCount": chunk_count,
                "timeToFirstChunkMs": (
                    round((first_chunk_at - start) * 1000)
                    if first_chunk_at is not None
                    else None
                ),
            },
        )
        self._send_soon(event)

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
            conversationId=conversation_id,
            requestId=request_id,
            inputPreview=_preview(input_text),
            outputPreview=_preview("".join(parts)),
            errorType=type(error).__name__,
            errorMessage=str(error),
            metadata={
                **(metadata or {}),
                "stream": True,
                "chunkCount": chunk_count,
            },
        )
        self._send_soon(event)
        raise
```

Then service can use:

```py
stream = tracker.track_stream(
    stream=provider.stream_chat(...),
    input_text=_preview(messages),
    conversation_id=conversation_id,
    request_id=request_id,
    metadata={...},
)

async for chunk in stream:
    parts.append(chunk)
    yield ChatStreamChunk(content=chunk)
```

This is the better architecture.

Important:

- `track_stream()` should yield chunks immediately.
- It should not wait for the full stream before yielding.
- It should send the tracking event after stream ends or errors.

---

## 17. LLM Event Model: What to Change

File:

```txt
server/src/models/llm_enference_models.py
```

No required schema change for first version.

You already have:

```py
metadata: dict[str, Any] | None = None
```

So stream-specific data can go into metadata:

```json
{
  "stream": true,
  "chunkCount": 42,
  "timeToFirstChunkMs": 820
}
```

Future improvement:

Add first-class fields if you want to query them directly in SQL:

- `streamed`
- `chunk_count`
- `time_to_first_token_ms`

But I would not migrate the DB yet. Put them in metadata first.

---

## 18. Error Handling Differences

HTTP route errors use status codes:

```py
raise HTTPException(status_code=400, detail=str(error))
```

WebSocket errors should usually be sent as events:

```json
{
  "type": "error",
  "message": "Provider 'openai' is not configured."
}
```

Why not only close the socket?

Because the frontend needs a readable error message.

Recommended pattern:

- validation error: send `error`, close with `1003`
- provider/model config error: send `error`, keep socket open or close normally
- unexpected server error: send generic `error`, close with `1011`
- client disconnect: stop streaming, optionally discard partial assistant message

Do not expose internal stack traces to users in production.

Your current route returns:

```py
detail=str(error)
```

That is okay for local development. For production, make unexpected errors generic.

---

## 19. Connection Lifecycle

Simple WebSocket lifecycle:

```txt
frontend opens socket
frontend sends ChatRequest JSON
backend sends start
backend sends chunk events
backend sends done
socket remains open for another message
```

Alternative:

```txt
frontend opens socket
frontend sends one ChatRequest JSON
backend streams response
backend sends done
backend closes socket
```

For your app, I recommend keeping the socket open only if you plan to reuse it. Otherwise one socket per submitted message is easier.

One socket per submitted message:

- easier state management
- easier cleanup
- less reconnect logic
- no need to multiplex conversations

Persistent socket:

- better if you want live chat behavior
- useful for cancellation and future agent events
- more lifecycle management

Start simple.

---

## 20. Recommended Build Order

Do this in small steps.

### Step 1: Fix provider contract

In `server/src/provider/base.py`:

- make `chat()` return `ProviderChatResult`
- add `stream_chat()` returning `AsyncIterator[str]`

Update concrete providers:

- `OpenAIProvider.chat()` stays mostly as-is
- `OpenAIProvider.stream_chat()` added
- `AnthropicProvider.stream_chat()` gets current stream logic
- `AnthropicProvider.chat()` collects chunks
- `GeminiProvider.stream_chat()` should yield chunks as they arrive
- `GeminiProvider.chat()` collects chunks

### Step 2: Add stream event models

In `server/src/models/chat_models.py`:

- add `ChatStreamStart`
- add `ChatStreamChunk`
- add `ChatStreamDone`
- add `ChatStreamError`

In `chatui/src/schemas/run_ai.schemas.ts`:

- add matching Zod schemas

### Step 3: Add `LLMTracker.track_stream()`

In `server/src/sdk/llm_event_tracker.py`:

- add streaming tracker support
- capture output preview by accumulating chunks
- include `chunkCount` and `timeToFirstChunkMs` in metadata

### Step 4: Add `stream_assistant()`

In `server/src/services/ai.py`:

- keep `run_assistant()`
- add `stream_assistant()`
- save user message before streaming
- save assistant message after streaming completes

### Step 5: Add WebSocket route

In `server/src/routes/run_ai_routes.py`:

- add `@router.websocket("/v1/ws/chat")`
- validate inbound JSON using `ChatRequest`
- call `stream_assistant()`
- send events with `websocket.send_json(...)`

### Step 6: Add frontend socket helper

In `chatui/src/api/chat.socket.ts`:

- open `ws://.../v1/ws/chat`
- send request
- parse stream events
- call handlers

### Step 7: Update `Chat.tsx`

In `chatui/src/pages/Chat.tsx`:

- add user message immediately
- add empty assistant message immediately
- append chunks into that assistant message
- replace with final message on `done`
- close socket on unmount

### Step 8: Keep old POST endpoint

Do not delete `/v1/api/chat`.

Keep it for:

- fallback
- tests
- debugging
- providers that do not stream well yet

---

## 21. Minimal Backend WebSocket Sketch

This is not copy-paste complete because it depends on adding the stream models and service function first, but this shows the final shape.

```py
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import ValidationError

from models.chat_models import ChatRequest, ChatStreamError
from services.ai import stream_assistant

router = APIRouter()

@router.websocket("/v1/ws/chat")
async def chat_ws(websocket: WebSocket) -> None:
    await websocket.accept()

    try:
        while True:
            try:
                raw_payload = await websocket.receive_json()
                payload = ChatRequest.model_validate(raw_payload)

                async for event in stream_assistant(payload):
                    await websocket.send_json(event.model_dump())

            except ValidationError:
                await websocket.send_json(
                    ChatStreamError(message="Invalid chat request.").model_dump()
                )
            except Exception as error:
                await websocket.send_json(
                    ChatStreamError(message=str(error)).model_dump()
                )

    except WebSocketDisconnect:
        return
```

For production, split the exception handling like your HTTP route already does:

- `UnknownProviderError`
- `ModelNotAllowedError`
- `ProviderNotConfiguredError`
- generic `Exception`

---

## 22. Minimal Frontend Socket Sketch

```ts
import { ChatStreamEventSchema } from "../schemas/run_ai.schemas";
import type {
  ChatRequestType,
  ChatStreamEventType,
} from "../schemas/run_ai.schemas";

type StreamHandlers = {
  onEvent: (event: ChatStreamEventType) => void;
  onError: (message: string) => void;
  onClose?: () => void;
};

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

function toWebSocketUrl(url: string): string {
  return url.replace(/^http:/, "ws:").replace(/^https:/, "wss:");
}

export function openChatStream(
  data: ChatRequestType,
  handlers: StreamHandlers,
): WebSocket {
  const socket = new WebSocket(`${toWebSocketUrl(API_BASE_URL)}/v1/ws/chat`);

  socket.onopen = () => {
    socket.send(JSON.stringify(data));
  };

  socket.onmessage = (event) => {
    try {
      const parsed = ChatStreamEventSchema.safeParse(JSON.parse(event.data));
      if (!parsed.success) {
        handlers.onError("Invalid stream event received.");
        return;
      }

      handlers.onEvent(parsed.data);
    } catch {
      handlers.onError("Could not parse stream event.");
    }
  };

  socket.onerror = () => {
    handlers.onError("Streaming connection failed.");
  };

  socket.onclose = () => {
    handlers.onClose?.();
  };

  return socket;
}
```

---

## 23. Minimal `Chat.tsx` Streaming State Sketch

```ts
const socketRef = useRef<WebSocket | null>(null);

useEffect(() => {
  return () => {
    socketRef.current?.close();
  };
}, []);

const appendAssistantChunk = (chunk: string) => {
  setMessages((currentMessages) => {
    const next = [...currentMessages];
    const last = next[next.length - 1];

    if (last?.role !== "Assistant") {
      return next;
    }

    next[next.length - 1] = {
      ...last,
      content: last.content + chunk,
    };

    return next;
  });
};
```

Submit flow:

```ts
const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
  event.preventDefault();

  const message = input.trim();
  if (!canSend) return;

  const userMessage: ChatMessageType = {
    role: "User",
    content: message,
  };

  const assistantMessage: ChatMessageType = {
    role: "Assistant",
    content: "",
  };

  setMessages((currentMessages) => [
    ...currentMessages,
    userMessage,
    assistantMessage,
  ]);
  setInput("");
  setError("");
  setIsSending(true);

  socketRef.current?.close();
  socketRef.current = openChatStream(
    {
      conversationId,
      message,
      provider: selectedProvider,
      model: selectedModel,
    },
    {
      onEvent: (event) => {
        if (event.type === "start") {
          setConversationId(event.conversationId);
          return;
        }

        if (event.type === "chunk") {
          appendAssistantChunk(event.content);
          return;
        }

        if (event.type === "done") {
          setConversationId(event.conversationId);
          setMessages((currentMessages) => {
            const next = [...currentMessages];
            const last = next[next.length - 1];

            if (last?.role === "Assistant") {
              next[next.length - 1] = event.message;
            }

            return next;
          });
          setIsSending(false);
          socketRef.current?.close();
          socketRef.current = null;
          return;
        }

        if (event.type === "error") {
          setError(event.message);
          setIsSending(false);
        }
      },
      onError: (message) => {
        setError(message);
        setIsSending(false);
      },
      onClose: () => {
        setIsSending(false);
      },
    },
  );
};
```

---

## 24. What About Axios?

Axios is fine for your current HTTP APIs:

- providers
- list conversations
- get conversation
- delete conversation
- non-streaming chat

Do not use Axios for WebSocket.

Use the browser's native:

```ts
new WebSocket(...)
```

If you choose HTTP streaming instead of WebSocket, prefer native `fetch()` because browser Axios does not expose streaming response chunks in the same clean way.

---

## 25. What About CORS?

Your app currently has:

```py
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

For WebSockets, browser origin still matters, but CORS middleware is not exactly the same as normal HTTP CORS.

For development, it may work directly.

For production, validate `websocket.headers["origin"]` if you need strict origin checks.

Example:

```py
origin = websocket.headers.get("origin")
if origin not in CORS_ORIGINS:
    await websocket.close(code=1008)
    return
```

Do this before `accept()`.

---

## 26. What About Authentication?

This repo currently does not show user auth.

If you add auth later:

- normal HTTP can use Authorization headers
- browser WebSocket cannot easily set custom headers directly with `new WebSocket()`

Common WebSocket auth options:

- cookie-based auth
- short-lived token in query string
- first message contains an auth token
- use a subprotocol token if appropriate

For this local app, skip auth until the rest is working.

---

## 27. Cancellation

WebSocket makes cancellation cleaner.

Frontend can send:

```json
{ "type": "cancel", "requestId": "..." }
```

Backend then stops the provider stream.

But cancellation is not automatic. You need to design the route loop to listen for cancel messages while streaming.

Simple first version:

- closing the WebSocket cancels the task indirectly
- backend catches disconnect
- do not save partial assistant response

Advanced version:

- create an `asyncio.Task` for streaming
- listen for incoming cancel message at the same time
- cancel the task
- send `cancelled` event
- decide whether to save partial output

Do not build advanced cancellation until basic streaming works.

---

## 28. Common Mistakes to Avoid

### Mistake 1: Provider "streams" but service waits anyway

Bad:

```py
chunks = []
async for chunk in provider.stream_chat(...):
    chunks.append(chunk)

return "".join(chunks)
```

This gives no UI streaming.

Good:

```py
async for chunk in provider.stream_chat(...):
    yield ChatStreamChunk(content=chunk)
```

### Mistake 2: Gemini collecting all chunks first

Current Gemini code collects chunks in a thread and then yields them after completion. That does not give real frontend streaming.

Fix it with an async queue or an async SDK streaming method.

### Mistake 3: Saving every chunk to DB

Do not do one DB insert/update per token.

Save the final assistant message once.

### Mistake 4: Plain text stream without event types

Plain text seems easy, but the frontend then cannot distinguish:

- metadata
- assistant chunks
- errors
- done state

Use typed JSON events.

### Mistake 5: Removing the old endpoint too early

Keep the old POST endpoint until streaming is stable.

---

## 29. Final Recommendation for This Repo

I would implement this in the following architecture:

```txt
Frontend Chat.tsx
  |
  | uses native WebSocket helper
  v
chatui/src/api/chat.socket.ts
  |
  | sends ChatRequest JSON
  v
WS /v1/ws/chat
  |
  | validates with ChatRequest
  v
services.ai.stream_assistant()
  |
  | builds context, resolves provider/model, saves user message
  v
provider.stream_chat()
  |
  | yields text chunks from OpenAI/Gemini/Anthropic
  v
services.ai.stream_assistant()
  |
  | yields ChatStreamChunk per provider chunk
  | saves final assistant message
  | logs stream event with LLMTracker.track_stream()
  v
WebSocket route
  |
  | sends JSON stream events to frontend
  v
Chat.tsx appends chunks into the visible Assistant message
```

Keep this too:

```txt
POST /v1/api/chat -> run_assistant() -> ChatResponse
```

The POST route remains useful as a fallback and for debugging.

---

## 30. One-Sentence Answer

You do not currently use WebSocket because the app is built as a normal HTTP request/response flow; WebSocket can make streaming possible, but the real work is changing the provider contract, service function, route, tracker, and frontend state so they handle chunk events instead of one final `ChatResponse`.

---

## 31. How To Use SSE Instead Of WebSocket

If you do not need two-way realtime communication, SSE is a very good fit for this app.

Your chat request is naturally:

```txt
frontend sends one message
backend streams assistant chunks back
frontend appends chunks to the assistant message
backend saves the final message
```

That is mostly one-way after the request starts, so SSE is simpler than WebSocket.

Important detail:

> Native browser `EventSource` only supports `GET`. Your chat request has a JSON body, so the best practical option is `POST /v1/api/chat/stream` with `fetch()` and a `text/event-stream` response.

This still uses the SSE wire format:

```txt
event: start
data: {"type":"start","conversationId":"abc","provider":"gemini","model":"gemini-3.5-flash","requestId":"req"}

event: chunk
data: {"type":"chunk","content":"Hello"}

event: done
data: {"type":"done","conversationId":"abc","message":{"role":"Assistant","content":"Hello"},"provider":"gemini","model":"gemini-3.5-flash"}
```

But the frontend reads it with `fetch()` instead of `new EventSource(...)`.

### SSE Architecture For This Repo

Use this shape:

```txt
Chat.tsx
  |
  | calls ChatApi.streamChat(...)
  v
POST /v1/api/chat/stream
  |
  | FastAPI StreamingResponse text/event-stream
  v
services.ai.stream_assistant()
  |
  | yields start/chunk/done/error events
  v
provider.stream_chat()
  |
  | yields provider text chunks
  v
browser fetch reader
  |
  | parses SSE events and appends chunks
  v
Chat.tsx visible assistant message updates live
```

Keep the old endpoint:

```txt
POST /v1/api/chat
```

Use the new endpoint for streaming:

```txt
POST /v1/api/chat/stream
```

### Backend Route

File:

```txt
server/src/routes/run_ai_routes.py
```

Add imports:

```py
import json
from collections.abc import AsyncIterator
from fastapi.responses import StreamingResponse
```

Add a helper that formats one SSE message:

```py
def _sse(event_name: str, data: dict) -> str:
    return (
        f"event: {event_name}\n"
        f"data: {json.dumps(data, ensure_ascii=False)}\n\n"
    )
```

Add the streaming route:

```py
@router.post("/v1/api/chat/stream")
async def chat_stream(payload: ChatRequest) -> StreamingResponse:
    async def events() -> AsyncIterator[str]:
        try:
            async for event in stream_assistant(payload):
                event_data = event.model_dump()
                yield _sse(event_data["type"], event_data)

        except UnknownProviderError as error:
            yield _sse("error", {
                "type": "error",
                "message": str(error),
            })
        except ModelNotAllowedError as error:
            yield _sse("error", {
                "type": "error",
                "message": str(error),
            })
        except ProviderNotConfiguredError as error:
            yield _sse("error", {
                "type": "error",
                "message": str(error),
            })
        except Exception as error:
            yield _sse("error", {
                "type": "error",
                "message": str(error),
            })

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
```

What this does:

- The frontend sends normal JSON with `POST`.
- FastAPI validates `payload` using your existing `ChatRequest`.
- `stream_assistant(payload)` yields typed events.
- Each event is serialized as SSE text.
- The browser receives chunks while the model is still generating.

You do not need a WebSocket route for this.

### Service Layer Is The Same As WebSocket Streaming

File:

```txt
server/src/services/ai.py
```

You still need:

```py
async def stream_assistant(payload: ChatRequest) -> AsyncIterator[ChatStreamEvent]:
    ...
```

This function should not know whether the transport is WebSocket or SSE.

That is the clean architecture:

```txt
transport route: WebSocket or SSE
business logic: stream_assistant()
provider logic: provider.stream_chat()
```

So this function still:

1. Creates or reuses `conversation_id`.
2. Loads existing messages.
3. Builds the context.
4. Resolves provider and model.
5. Yields a `start` event.
6. Saves the user message.
7. Streams chunks from `provider.stream_chat()`.
8. Yields one `chunk` event per text chunk.
9. Saves the final assistant message.
10. Yields a `done` event.

The service code does not change just because you choose SSE.

Only the route changes.

### Models Are The Same

File:

```txt
server/src/models/chat_models.py
```

Use the same stream event models:

```py
class ChatStreamStart(BaseModel):
    type: Literal["start"] = "start"
    conversationId: str
    provider: ProviderType
    model: str
    requestId: str

class ChatStreamChunk(BaseModel):
    type: Literal["chunk"] = "chunk"
    content: str

class ChatStreamDone(BaseModel):
    type: Literal["done"] = "done"
    conversationId: str
    message: ChatMessage
    provider: ProviderType
    model: str

class ChatStreamError(BaseModel):
    type: Literal["error"] = "error"
    message: str
```

The same models work for:

- SSE
- WebSocket
- tests
- future CLI streaming

### Frontend Schemas Are The Same

File:

```txt
chatui/src/schemas/run_ai.schemas.ts
```

Use the same discriminated union:

```ts
export const ChatStreamEventSchema = z.discriminatedUnion("type", [
  ChatStreamStartSchema,
  ChatStreamChunkSchema,
  ChatStreamDoneSchema,
  ChatStreamErrorSchema,
]);
```

The frontend receives JSON inside the SSE `data:` line and validates it with Zod.

### Frontend API With Fetch Streaming

File:

```txt
chatui/src/api/chat.api.ts
```

Do not use Axios for this streaming call.

Use `fetch()` because you need access to `response.body.getReader()`.

Add this helper:

```ts
import { API_BASE_URL } from "../config/runtime";
import {
  ChatStreamEventSchema,
  type ChatRequestType,
  type ChatStreamEventType,
} from "../schemas/run_ai.schemas";

type StreamHandlers = {
  signal?: AbortSignal;
  onEvent: (event: ChatStreamEventType) => void;
  onError: (message: string) => void;
  onDone?: () => void;
};

async function streamChat(
  data: ChatRequestType,
  handlers: StreamHandlers,
): Promise<void> {
  const response = await fetch(`${API_BASE_URL}/v1/api/chat/stream`, {
    method: "POST",
    headers: {
      "content-type": "application/json",
      "accept": "text/event-stream",
    },
    body: JSON.stringify(data),
    signal: handlers.signal,
  });

  if (!response.ok || !response.body) {
    handlers.onError("Could not start chat stream.");
    return;
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  try {
    while (true) {
      const { value, done } = await reader.read();

      if (done) {
        break;
      }

      buffer += decoder.decode(value, { stream: true });

      const messages = buffer.split("\n\n");
      buffer = messages.pop() ?? "";

      for (const rawMessage of messages) {
        const event = parseSseMessage(rawMessage);
        if (!event.data) {
          continue;
        }

        const parsed = ChatStreamEventSchema.safeParse(JSON.parse(event.data));
        if (!parsed.success) {
          handlers.onError("Invalid stream event received.");
          continue;
        }

        handlers.onEvent(parsed.data);
      }
    }
  } catch (error) {
    if (handlers.signal?.aborted) {
      return;
    }

    handlers.onError("Chat stream failed.");
  } finally {
    handlers.onDone?.();
  }
}

function parseSseMessage(rawMessage: string): { event?: string; data?: string } {
  const result: { event?: string; data?: string } = {};
  const dataLines: string[] = [];

  for (const line of rawMessage.split("\n")) {
    if (line.startsWith("event:")) {
      result.event = line.slice("event:".length).trim();
    }

    if (line.startsWith("data:")) {
      dataLines.push(line.slice("data:".length).trimStart());
    }
  }

  if (dataLines.length > 0) {
    result.data = dataLines.join("\n");
  }

  return result;
}
```

Then export it from `ChatApi`:

```ts
const ChatApi = {
  chat: async (data: ChatRequestType): Promise<ChatResponseType> => {
    const response = await axiosInstance.post("/v1/api/chat", data);
    return ChatResponseSchema.parse(response.data);
  },

  streamChat,

  providers: async (): Promise<ProviderListResponseType> => {
    const response = await axiosInstance.get("/v1/api/providers");
    return ProviderListResponseSchema.parse(response.data);
  },
};
```

If your `runtime.ts` does not export `API_BASE_URL`, use the same base URL that `axios.config.ts` uses.

### Frontend Chat Page With SSE

File:

```txt
chatui/src/pages/Chat.tsx
```

Use an `AbortController` instead of a WebSocket ref.

Add imports:

```ts
import { useRef } from "react";
```

Add the ref:

```ts
const streamAbortRef = useRef<AbortController | null>(null);
```

Cleanup on unmount:

```ts
useEffect(() => {
  return () => {
    streamAbortRef.current?.abort();
  };
}, []);
```

Update `handleSubmit`:

```ts
const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
  event.preventDefault();

  const message = input.trim();
  if (!canSend) {
    return;
  }

  const userMessage: ChatMessageType = {
    role: "User",
    content: message,
  };

  const assistantMessage: ChatMessageType = {
    role: "Assistant",
    content: "",
  };

  setMessages((currentMessages) => [
    ...currentMessages,
    userMessage,
    assistantMessage,
  ]);
  setInput("");
  setError("");
  setIsSending(true);

  streamAbortRef.current?.abort();
  const controller = new AbortController();
  streamAbortRef.current = controller;

  await ChatApi.streamChat(
    {
      conversationId,
      message,
      provider: selectedProvider,
      model: selectedModel,
    },
    {
      signal: controller.signal,
      onEvent: (event) => {
        if (event.type === "start") {
          setConversationId(event.conversationId);
          return;
        }

        if (event.type === "chunk") {
          setMessages((currentMessages) => {
            const next = [...currentMessages];
            const last = next[next.length - 1];

            if (last?.role === "Assistant") {
              next[next.length - 1] = {
                ...last,
                content: last.content + event.content,
              };
            }

            return next;
          });
          return;
        }

        if (event.type === "done") {
          setConversationId(event.conversationId);
          setMessages((currentMessages) => {
            const next = [...currentMessages];
            const last = next[next.length - 1];

            if (last?.role === "Assistant") {
              next[next.length - 1] = event.message;
            }

            return next;
          });
          return;
        }

        if (event.type === "error") {
          setError(event.message);
        }
      },
      onError: (message) => {
        setError(message);
      },
      onDone: () => {
        setIsSending(false);
        streamAbortRef.current = null;
      },
    },
  );
};
```

For a cancel button:

```ts
const handleCancelStream = () => {
  streamAbortRef.current?.abort();
  streamAbortRef.current = null;
  setIsSending(false);
};
```

This cancels the HTTP stream from the browser side. On the backend, the generator should stop when the client disconnects.

### Provider Layer Does Not Care About SSE

Files:

```txt
server/src/provider/base.py
server/src/provider/open_ai_provider.py
server/src/provider/gemini_provider.py
server/src/provider/anthropic_provider.py
```

Same requirement:

```py
async def stream_chat(...) -> AsyncIterator[str]:
    ...
```

The provider only yields chunks.

It should not know whether chunks are going to:

- SSE
- WebSocket
- terminal output
- tests

That separation is important.

### Database Does Not Need To Change

SSE does not require a DB schema change.

Use the same behavior:

- Save the user message when streaming starts.
- Accumulate assistant chunks in memory.
- Save one final assistant message when streaming completes.
- Do not save every chunk.

### SDK Tracker Is The Same

SSE does not change `LLMTracker.track_stream()`.

The tracker wraps the provider stream:

```py
stream = tracker.track_stream(
    stream=provider.stream_chat(...),
    input_text=_preview(messages),
    conversation_id=conversation_id,
    request_id=request_id,
    metadata={
        "route": "/v1/api/chat/stream",
        "transport": "sse",
        "provider": provider.id,
        "model": model,
    },
)
```

Then:

```py
async for chunk in stream:
    yield ChatStreamChunk(content=chunk)
```

Put this in metadata:

```json
{
  "transport": "sse",
  "stream": true,
  "chunkCount": 42,
  "timeToFirstChunkMs": 700
}
```

### Native EventSource Option

If you want to use the browser's native `EventSource`, remember:

```txt
EventSource only sends GET requests.
```

That means this is awkward:

```txt
GET /v1/api/chat/stream?message=very-long-message&provider=gemini&model=...
```

Problems:

- long messages do not belong in query params
- query params may be logged by proxies
- request body is not supported
- complex payloads become painful

A better EventSource architecture would be two-step:

```txt
POST /v1/api/chat/stream-jobs
  -> returns { streamId }

GET /v1/api/chat/stream-jobs/{streamId}/events
  -> EventSource reads server events
```

But that requires server-side job state.

For this repo, use:

```txt
POST /v1/api/chat/stream + fetch() reader
```

It is simpler.

### SSE vs WebSocket In This App

Use SSE when:

- you only need server-to-client streaming after the user submits a message
- cancellation can be handled by aborting the request
- you want normal HTTP request validation
- you want simpler backend and frontend code

Use WebSocket when:

- the client must send messages while the assistant is still streaming
- you need rich cancellation messages
- you need tool progress plus user interrupts
- you want a persistent realtime connection

For your current chat UI, SSE is probably the cleaner first implementation.

### SSE Build Order

Use this order:

1. Fix provider contract with `chat()` and `stream_chat()`.
2. Add stream event models in Python.
3. Add stream event schemas in TypeScript.
4. Add `LLMTracker.track_stream()`.
5. Add `stream_assistant()` in `services/ai.py`.
6. Add `POST /v1/api/chat/stream` using `StreamingResponse`.
7. Add `ChatApi.streamChat()` using native `fetch()`.
8. Update `Chat.tsx` to append chunks into the visible assistant message.
9. Keep old `POST /v1/api/chat` as fallback.

### Final SSE Recommendation

For this repo, I would use:

```txt
REST normal JSON:
GET    /v1/api/providers
GET    /v1/api/conversations
GET    /v1/api/conversations/{id}
DELETE /v1/api/conversations/{id}
POST   /v1/api/chat

REST streaming SSE:
POST   /v1/api/chat/stream
```

That gives you streaming without the extra lifecycle complexity of WebSocket.
