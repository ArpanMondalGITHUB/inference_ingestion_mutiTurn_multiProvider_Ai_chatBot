# Multi-Provider Support Implementation Guide

This document explains how to change this codebase so the chatbot can use multiple AI providers instead of being hardcoded to one SDK call.

The feature you want is:

```python
get_provider("anthropic").chat(...)
get_provider("openai").chat(...)
get_provider("gemini").chat(...)
```

instead of:

```python
client.models.generate_content(...)
```

or:

```python
anthropic_client.messages.create(...)
```

The important idea is not just "add OpenAI and Anthropic imports". The important idea is to create one internal chat interface that your application owns, then make Gemini, OpenAI, and Anthropic adapters translate that interface into each provider's SDK shape.

Your frontend should not know API keys. Your frontend should only send something like:

```json
{
  "conversationId": "optional-existing-id",
  "message": "Explain recursion simply",
  "provider": "openai",
  "model": "gpt-4.1"
}
```

The backend receives that request, validates the provider and model, selects the right provider adapter, sends the conversation to the chosen provider, stores recent context, logs the LLM event with the correct provider/model, and returns a normal chat response.

## Current Codebase State

Before adding multi-provider support, understand what currently exists.

### Backend

The backend is a FastAPI app in:

```text
server/src
```

The chat endpoint is here:

```text
server/src/routes/run_ai_routes.py
```

It currently imports one function:

```python
from services.ai import run_assistant
```

and exposes:

```python
@router.post("/v1/api/chat", response_model=ChatResponse)
async def chat(payload: ChatRequest) -> ChatResponse:
    try:
        return await run_assistant(payload)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Can't send your request: {e}",
        ) from e
```

The actual AI call is here:

```text
server/src/services/ai.py
```

Right now it is Gemini-specific:

```python
from google import genai
from core.config import GEMINI_API_KEY, GEMINI_MODEL

client = genai.Client(api_key=GEMINI_API_KEY)
```

and later:

```python
response = await tracker.track(
    call=lambda: asyncio.to_thread(
        client.models.generate_content,
        model=GEMINI_MODEL,
        contents=prompt,
    ),
    ...
)
```

The tracker is also hardcoded to Gemini:

```python
tracker = LLMTracker(
    provider="gemini",
    model=GEMINI_MODEL or "gemini",
    ...
)
```

That means the backend has four Gemini-specific assumptions:

1. The only SDK client is `google.genai.Client`.
2. The prompt is built as one Gemini-friendly text string.
3. The tracker always logs `provider="gemini"`.
4. The request payload has no `provider` or `model` field.

The current chat request and response models are here:

```text
server/src/models/chat_models.py
```

Current request:

```python
class ChatRequest(BaseModel):
    conversationId: Optional[str] = None
    message: str = Field(min_length=1)
```

Current response:

```python
class ChatResponse(BaseModel):
    conversationId: str
    message: ChatMessage
```

So the API currently cannot receive provider choice from the UI and cannot return which provider/model produced the answer.

Good news: your logging schema already supports provider and model. In:

```text
server/llm-events.sql
```

you already have:

```sql
provider VARCHAR NOT NULL,
model VARCHAR NOT NULL,
```

and an index:

```sql
CREATE INDEX IF NOT EXISTS llm_events_provider_model_idx
  ON llm_inference_events (provider, model);
```

So you do not need a new database concept for "multi-provider logging". You mainly need to make sure the tracker receives the selected provider and model for each request.

### Frontend

The chat UI is here:

```text
chatui/src/pages/Chat.tsx
```

It currently only sends:

```ts
const response = await ChatApi.chat({
  conversationId,
  message,
});
```

The frontend schema is here:

```text
chatui/src/schemas/run_ai.schemas.ts
```

Current request schema:

```ts
export const ChatRequestSchema = z.object({
    conversationId:z.string().optional(),
    message:z.string().min(1,"Prompt Is Required")
});
```

The UI also says:

```tsx
<p className="eyebrow">Gemini Chat</p>
```

So the frontend is visually and technically Gemini-only right now.

## What Multi-Provider Support Should Mean In This Codebase

Do not spread provider-specific conditionals everywhere.

Avoid this style:

```python
if payload.provider == "gemini":
    ...
elif payload.provider == "openai":
    ...
elif payload.provider == "anthropic":
    ...
```

inside `run_assistant`.

That works for a quick demo, but it becomes painful when you add:

- streaming
- tool calls
- token usage
- retries
- provider-specific errors
- provider-specific model lists
- default models
- disabled providers
- tracing
- fallback models
- structured output
- image input

Instead, use this shape:

```text
UI dropdown
  -> POST /v1/api/chat { message, conversationId, provider, model }
    -> route validates request
      -> service chooses provider through registry
        -> provider adapter converts common messages to provider SDK call
          -> LLMTracker logs selected provider/model
            -> ChatResponse comes back to UI
```

The backend should own the provider registry:

```python
provider = get_provider(payload.provider)
```

Each provider should expose the same method:

```python
result = await provider.chat(
    messages=messages,
    model=selected_model,
    system_prompt=ASSISTANT_PROMPT,
)
```

Each provider should return the same internal result object:

```python
ProviderChatResult(
    text="assistant reply",
    token_usage=TokenUsage(...),
)
```

That way `services/ai.py` does not care whether the answer came from Gemini, OpenAI, or Anthropic.

## Recommended Final Backend Shape

Add a new provider package:

```text
server/src/providers/
  __init__.py
  base.py
  registry.py
  gemini_provider.py
  openai_provider.py
  anthropic_provider.py
```

Then refactor:

```text
server/src/services/ai.py
```

so it becomes the orchestration layer:

- choose conversation id
- read recent history
- build canonical message list
- select provider
- select model
- wrap the provider call with `LLMTracker`
- append messages to history
- return `ChatResponse`

The provider adapters should be the only files that import provider SDKs:

```python
from google import genai
from openai import AsyncOpenAI
from anthropic import AsyncAnthropic
```

Do not import all provider SDKs in `services/ai.py`.

## Recommended Final Frontend Shape

Add provider and model state in:

```text
chatui/src/pages/Chat.tsx
```

For a first version, you can hardcode provider choices in the frontend:

```ts
const PROVIDERS = [
  {
    id: "gemini",
    label: "Google Gemini",
    models: ["gemini-3.5-flash"],
  },
  {
    id: "openai",
    label: "OpenAI",
    models: ["gpt-4.1"],
  },
  {
    id: "anthropic",
    label: "Anthropic Claude",
    models: ["claude-opus-4-6"],
  },
] as const;
```

These model IDs are examples. Keep the allowed model list in your backend config and pin it to the model IDs your provider accounts are allowed to use.

But the better version is to expose a backend endpoint:

```text
GET /v1/api/providers
```

that returns only configured providers:

```json
{
  "defaultProvider": "gemini",
  "providers": [
    {
      "id": "gemini",
      "label": "Google Gemini",
      "defaultModel": "gemini-3.5-flash",
      "models": ["gemini-3.5-flash"]
    },
    {
      "id": "openai",
      "label": "OpenAI",
      "defaultModel": "gpt-4.1",
      "models": ["gpt-4.1"]
    }
  ]
}
```

That is better because:

1. The frontend does not show providers with missing API keys.
2. Model lists live close to backend validation.
3. You can change model names without redeploying frontend code.
4. The backend remains the source of truth.

For an assessment/demo project, either approach is acceptable. For a real app, use the backend provider-list endpoint.

## Request And Response Contract

### Backend Request Model

Change:

```text
server/src/models/chat_models.py
```

from:

```python
class ChatRequest(BaseModel):
    conversationId: Optional[str] = None
    message: str = Field(min_length=1)
```

to:

```python
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class ProviderType(str, Enum):
    GEMINI = "gemini"
    OPENAI = "openai"
    ANTHROPIC = "anthropic"


class RoleType(str, Enum):
    ASSISTANT = "Assistant"
    USER = "User"


class ChatMessage(BaseModel):
    role: RoleType = Field(description="for understanding role types")
    content: str


class ChatRequest(BaseModel):
    conversationId: Optional[str] = None
    message: str = Field(min_length=1)
    provider: ProviderType | None = None
    model: str | None = Field(default=None, min_length=1, max_length=200)


class ChatResponse(BaseModel):
    conversationId: str
    message: ChatMessage
    provider: ProviderType
    model: str
```

Why add `provider` and `model` to the response too?

Because the UI can display exactly what answered. This matters when:

- the request omitted provider and the backend used a default
- the requested model was omitted and the provider default was used
- a provider fallback was used later
- debugging logs need to match UI behavior

### Frontend Schema

Change:

```text
chatui/src/schemas/run_ai.schemas.ts
```

from:

```ts
export const ChatRequestSchema = z.object({
    conversationId:z.string().optional(),
    message:z.string().min(1,"Prompt Is Required")
});
```

to:

```ts
import { z } from "zod";

export const ProviderTypeSchema = z.enum([
  "gemini",
  "openai",
  "anthropic",
]);
export type ProviderType = z.infer<typeof ProviderTypeSchema>;

export const RoleTypeSchema = z.enum([
  "User",
  "Assistant",
]);
export type RoleType = z.infer<typeof RoleTypeSchema>;

export const ChatMessageSchema = z.object({
  role: RoleTypeSchema,
  content: z.string().min(1),
});
export type ChatMessageType = z.infer<typeof ChatMessageSchema>;

export const ChatRequestSchema = z.object({
  conversationId: z.string().optional(),
  message: z.string().min(1, "Prompt Is Required"),
  provider: ProviderTypeSchema.optional(),
  model: z.string().min(1).optional(),
});
export type ChatRequestType = z.infer<typeof ChatRequestSchema>;

export const ChatResponseSchema = z.object({
  conversationId: z.string(),
  message: ChatMessageSchema,
  provider: ProviderTypeSchema,
  model: z.string(),
});
export type ChatResponseType = z.infer<typeof ChatResponseSchema>;

export const ProviderInfoSchema = z.object({
  id: ProviderTypeSchema,
  label: z.string(),
  defaultModel: z.string(),
  models: z.array(z.string()).min(1),
});
export type ProviderInfoType = z.infer<typeof ProviderInfoSchema>;

export const ProviderListResponseSchema = z.object({
  defaultProvider: ProviderTypeSchema,
  providers: z.array(ProviderInfoSchema),
});
export type ProviderListResponseType = z.infer<
  typeof ProviderListResponseSchema
>;
```

## Config Changes

Current config only has Gemini settings:

```python
GEMINI_API_KEY = _clean_env("GEMINI_API_KEY")
GEMINI_MODEL = _clean_env("GEMINI_MODEL")
```

Change:

```text
server/src/core/config.py
```

to include all providers:

```python
AI_DEFAULT_PROVIDER = _clean_env("AI_DEFAULT_PROVIDER") or "gemini"

GEMINI_API_KEY = _clean_env("GEMINI_API_KEY")
GEMINI_MODEL = _clean_env("GEMINI_MODEL") or "gemini-3.5-flash"

OPENAI_API_KEY = _clean_env("OPENAI_API_KEY")
OPENAI_MODEL = _clean_env("OPENAI_MODEL") or "gpt-4.1"

ANTHROPIC_API_KEY = _clean_env("ANTHROPIC_API_KEY")
ANTHROPIC_MODEL = _clean_env("ANTHROPIC_MODEL") or "claude-opus-4-6"

MAX_CONTEXT_MESSAGES = _get_int_env("MAX_CONTEXT_MESSAGES", 8)
```

You can also add explicit allowed model lists:

```python
GEMINI_MODELS = _get_csv_env("GEMINI_MODELS") or [GEMINI_MODEL]
OPENAI_MODELS = _get_csv_env("OPENAI_MODELS") or [OPENAI_MODEL]
ANTHROPIC_MODELS = _get_csv_env("ANTHROPIC_MODELS") or [ANTHROPIC_MODEL]
```

Then `.env` can look like:

```env
DATABASE_PATH=server/src/db/mydb.db
FRONTEND_URL=http://localhost:5173
CORS_ORIGINS=http://localhost:5173

AI_DEFAULT_PROVIDER=gemini

GEMINI_API_KEY=your_google_key
GEMINI_MODEL=gemini-3.5-flash
GEMINI_MODELS=gemini-3.5-flash

OPENAI_API_KEY=your_openai_key
OPENAI_MODEL=gpt-4.1
OPENAI_MODELS=gpt-4.1

ANTHROPIC_API_KEY=your_anthropic_key
ANTHROPIC_MODEL=claude-opus-4-6
ANTHROPIC_MODELS=claude-opus-4-6

LLM_LOGGING_ENABLED=true
LLM_INGESTION_URL=http://localhost:8000/llm-events
LOG_INGESTION_KEY=dev-secret
```

Important: never send API keys to the frontend. The frontend should only know provider ids and model names.

## Dependency Changes

Current backend dependencies in:

```text
server/pyproject.toml
```

include:

```toml
"google-genai (>=2.6.0,<3.0.0)",
```

Add:

```toml
"openai (>=2.0.0,<3.0.0)",
"anthropic (>=0.70.0,<1.0.0)",
```

Then run from `server`:

```powershell
poetry lock
poetry install
```

If this project uses `poetry add`, you can do:

```powershell
poetry add openai anthropic
```

That will update both `pyproject.toml` and `poetry.lock`.

## Provider Base Contract

Create:

```text
server/src/providers/base.py
```

This file defines your internal provider interface.

```python
from dataclasses import dataclass
from typing import Protocol

from models.chat_models import ChatMessage
from models.llm_enference_models import TokenUsage


@dataclass(frozen=True)
class ProviderInfo:
    id: str
    label: str
    default_model: str
    models: list[str]
    configured: bool


@dataclass(frozen=True)
class ProviderChatResult:
    text: str
    token_usage: TokenUsage | None = None


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
```

Why use this?

Because the app only wants to know:

- What providers exist?
- Is the provider configured?
- Which model should be used?
- Given canonical chat messages, return assistant text and token usage.

The app should not know:

- Gemini calls the user input `contents`
- OpenAI Responses calls the input `input`
- Anthropic uses `system` separately from `messages`
- token usage fields are named differently

Those differences belong inside each provider adapter.

## Provider Errors

Create simple provider errors either in `base.py` or a separate file:

```python
class ProviderError(Exception):
    pass


class UnknownProviderError(ProviderError):
    pass


class ProviderNotConfiguredError(ProviderError):
    pass


class ModelNotAllowedError(ProviderError):
    pass
```

These errors let the route return clean HTTP responses:

- unknown provider -> `400 Bad Request`
- provider exists but missing API key -> `400 Bad Request` or `503 Service Unavailable`
- model not in allowlist -> `400 Bad Request`
- provider SDK failure -> `500` or `502`

For a user-facing chat app, I recommend:

- `400` when the request is invalid
- `503` when the provider is known but not configured
- `502` when the upstream provider failed

## Model Validation

Do not allow arbitrary model strings from the frontend unless you are intentionally building an admin tool.

Bad:

```python
model = payload.model
```

with no validation.

Better:

```python
def resolve_model(self, requested_model: str | None) -> str:
    model = requested_model or self.default_model
    if model not in self.models:
        raise ModelNotAllowedError(
            f"Model '{model}' is not allowed for provider '{self.id}'."
        )
    return model
```

Why?

1. It prevents users from calling models you did not budget for.
2. It prevents typo-driven production errors.
3. It keeps the UI dropdown and backend validation consistent.
4. It makes future provider-specific capability checks easier.

## Gemini Provider Adapter

Create:

```text
server/src/providers/gemini_provider.py
```

You already have the Gemini SDK installed and working.

The current code uses:

```python
client.models.generate_content(
    model=GEMINI_MODEL,
    contents=prompt,
)
```

Google's Gemini docs show the Python SDK shape as:

```python
from google import genai

client = genai.Client()

response = client.models.generate_content(
    model="gemini-3.5-flash",
    contents="How does AI work?"
)
print(response.text)
```

Source: https://ai.google.dev/gemini-api/docs/text-generation

For your adapter:

```python
import asyncio

from google import genai
from google.genai import types

from models.chat_models import ChatMessage, RoleType
from models.llm_enference_models import TokenUsage
from providers.base import (
    ModelNotAllowedError,
    ProviderChatResult,
    ProviderNotConfiguredError,
)


class GeminiProvider:
    id = "gemini"
    label = "Google Gemini"

    def __init__(
        self,
        *,
        api_key: str | None,
        default_model: str,
        models: list[str],
    ) -> None:
        self.api_key = api_key
        self.default_model = default_model
        self.models = models
        self._client = genai.Client(api_key=api_key) if api_key else None

    @property
    def configured(self) -> bool:
        return self._client is not None

    def resolve_model(self, requested_model: str | None) -> str:
        model = requested_model or self.default_model
        if model not in self.models:
            raise ModelNotAllowedError(
                f"Model '{model}' is not allowed for provider '{self.id}'."
            )
        return model

    async def chat(
        self,
        *,
        messages: list[ChatMessage],
        model: str,
        system_prompt: str,
    ) -> ProviderChatResult:
        if self._client is None:
            raise ProviderNotConfiguredError("Gemini API key is not configured.")

        contents = _messages_to_gemini_prompt(messages)

        response = await asyncio.to_thread(
            self._client.models.generate_content,
            model=model,
            contents=contents,
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
            ),
        )

        text = (response.text or "").strip()
        if not text:
            raise RuntimeError("Gemini returned an empty response.")

        return ProviderChatResult(
            text=text,
            token_usage=_extract_gemini_token_usage(response),
        )


def _messages_to_gemini_prompt(messages: list[ChatMessage]) -> str:
    return "\n".join(
        f"{message.role.value}: {message.content}" for message in messages
    )


def _extract_gemini_token_usage(response: object) -> TokenUsage | None:
    usage = getattr(response, "usage_metadata", None)
    if usage is None:
        return None

    return TokenUsage(
        inputTokens=getattr(usage, "prompt_token_count", None),
        outputTokens=getattr(usage, "candidates_token_count", None),
        totalTokens=getattr(usage, "total_token_count", None),
    )
```

Notes:

1. This keeps your current Gemini style but moves it behind the provider interface.
2. `asyncio.to_thread` is used because the Gemini SDK call is sync in your current code.
3. `system_instruction` keeps the assistant prompt separate from chat history.
4. You can later improve `_messages_to_gemini_prompt` to use native Gemini content parts instead of one string.

## OpenAI Provider Adapter

Create:

```text
server/src/providers/openai_provider.py
```

OpenAI has two relevant API surfaces for text chat:

1. Responses API
2. Chat Completions API

OpenAI's current API reference describes `POST /responses` as creating a model response with text/image inputs and optional tools. It accepts an `input` field and can also accept `instructions`. Source: https://platform.openai.com/docs/api-reference/responses/create

OpenAI also still documents Chat Completions, where a list of messages produces a model response. Source: https://platform.openai.com/docs/api-reference/chat/create

For new code, use the Responses API unless you have a specific reason to keep Chat Completions. It is the more future-facing surface and maps well to later tool use.

Example adapter:

```python
from openai import AsyncOpenAI

from models.chat_models import ChatMessage, RoleType
from models.llm_enference_models import TokenUsage
from providers.base import (
    ModelNotAllowedError,
    ProviderChatResult,
    ProviderNotConfiguredError,
)


class OpenAIProvider:
    id = "openai"
    label = "OpenAI"

    def __init__(
        self,
        *,
        api_key: str | None,
        default_model: str,
        models: list[str],
    ) -> None:
        self.api_key = api_key
        self.default_model = default_model
        self.models = models
        self._client = AsyncOpenAI(api_key=api_key) if api_key else None

    @property
    def configured(self) -> bool:
        return self._client is not None

    def resolve_model(self, requested_model: str | None) -> str:
        model = requested_model or self.default_model
        if model not in self.models:
            raise ModelNotAllowedError(
                f"Model '{model}' is not allowed for provider '{self.id}'."
            )
        return model

    async def chat(
        self,
        *,
        messages: list[ChatMessage],
        model: str,
        system_prompt: str,
    ) -> ProviderChatResult:
        if self._client is None:
            raise ProviderNotConfiguredError("OpenAI API key is not configured.")

        response = await self._client.responses.create(
            model=model,
            instructions=system_prompt,
            input=_messages_to_openai_input(messages),
        )

        text = (getattr(response, "output_text", "") or "").strip()
        if not text:
            raise RuntimeError("OpenAI returned an empty response.")

        return ProviderChatResult(
            text=text,
            token_usage=_extract_openai_token_usage(response),
        )


def _messages_to_openai_input(messages: list[ChatMessage]) -> list[dict[str, str]]:
    return [
        {
            "role": _to_openai_role(message.role),
            "content": message.content,
        }
        for message in messages
    ]


def _to_openai_role(role: RoleType) -> str:
    if role == RoleType.ASSISTANT:
        return "assistant"
    return "user"


def _extract_openai_token_usage(response: object) -> TokenUsage | None:
    usage = getattr(response, "usage", None)
    if usage is None:
        return None

    return TokenUsage(
        inputTokens=getattr(usage, "input_tokens", None),
        outputTokens=getattr(usage, "output_tokens", None),
        totalTokens=getattr(usage, "total_tokens", None),
    )
```

If you choose Chat Completions instead, the adapter would look like:

```python
response = await self._client.chat.completions.create(
    model=model,
    messages=[
        {"role": "system", "content": system_prompt},
        *_messages_to_openai_input(messages),
    ],
)

text = response.choices[0].message.content
```

The Responses API version is recommended for this guide because it keeps `instructions` separate from the conversation and leaves a better path for future tool use.

## Anthropic Provider Adapter

Create:

```text
server/src/providers/anthropic_provider.py
```

Anthropic's Python SDK uses `client.messages.create(...)` with:

- `model`
- `max_tokens`
- `system`
- `messages`

The SDK README shows this basic shape:

```python
from anthropic import Anthropic

client = Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

message = client.messages.create(
    max_tokens=1024,
    messages=[
        {
            "role": "user",
            "content": "Hello, Claude",
        }
    ],
    model="claude-opus-4-6",
)
```

Source: https://github.com/anthropics/anthropic-sdk-python

Use the async client in FastAPI:

```python
from anthropic import AsyncAnthropic

from models.chat_models import ChatMessage, RoleType
from models.llm_enference_models import TokenUsage
from providers.base import (
    ModelNotAllowedError,
    ProviderChatResult,
    ProviderNotConfiguredError,
)


class AnthropicProvider:
    id = "anthropic"
    label = "Anthropic Claude"

    def __init__(
        self,
        *,
        api_key: str | None,
        default_model: str,
        models: list[str],
        max_tokens: int = 1024,
    ) -> None:
        self.api_key = api_key
        self.default_model = default_model
        self.models = models
        self.max_tokens = max_tokens
        self._client = AsyncAnthropic(api_key=api_key) if api_key else None

    @property
    def configured(self) -> bool:
        return self._client is not None

    def resolve_model(self, requested_model: str | None) -> str:
        model = requested_model or self.default_model
        if model not in self.models:
            raise ModelNotAllowedError(
                f"Model '{model}' is not allowed for provider '{self.id}'."
            )
        return model

    async def chat(
        self,
        *,
        messages: list[ChatMessage],
        model: str,
        system_prompt: str,
    ) -> ProviderChatResult:
        if self._client is None:
            raise ProviderNotConfiguredError("Anthropic API key is not configured.")

        response = await self._client.messages.create(
            model=model,
            max_tokens=self.max_tokens,
            system=system_prompt,
            messages=_messages_to_anthropic(messages),
        )

        text = _extract_anthropic_text(response).strip()
        if not text:
            raise RuntimeError("Anthropic returned an empty response.")

        return ProviderChatResult(
            text=text,
            token_usage=_extract_anthropic_token_usage(response),
        )


def _messages_to_anthropic(messages: list[ChatMessage]) -> list[dict[str, str]]:
    return [
        {
            "role": _to_anthropic_role(message.role),
            "content": message.content,
        }
        for message in messages
    ]


def _to_anthropic_role(role: RoleType) -> str:
    if role == RoleType.ASSISTANT:
        return "assistant"
    return "user"


def _extract_anthropic_text(response: object) -> str:
    parts: list[str] = []
    for block in getattr(response, "content", []) or []:
        if getattr(block, "type", None) == "text":
            parts.append(getattr(block, "text", ""))
    return "\n".join(part for part in parts if part)


def _extract_anthropic_token_usage(response: object) -> TokenUsage | None:
    usage = getattr(response, "usage", None)
    if usage is None:
        return None

    input_tokens = getattr(usage, "input_tokens", None)
    output_tokens = getattr(usage, "output_tokens", None)

    return TokenUsage(
        inputTokens=input_tokens,
        outputTokens=output_tokens,
        totalTokens=(
            input_tokens + output_tokens
            if input_tokens is not None and output_tokens is not None
            else None
        ),
    )
```

Note: Anthropic does not accept `system` as a normal chat message in the same way OpenAI Chat Completions does. Keep the system prompt separate and pass it as `system=system_prompt`.

## Provider Registry

Create:

```text
server/src/providers/registry.py
```

This file builds the providers and exposes helper functions.

```python
from core.config import (
    AI_DEFAULT_PROVIDER,
    ANTHROPIC_API_KEY,
    ANTHROPIC_MODEL,
    ANTHROPIC_MODELS,
    GEMINI_API_KEY,
    GEMINI_MODEL,
    GEMINI_MODELS,
    OPENAI_API_KEY,
    OPENAI_MODEL,
    OPENAI_MODELS,
)
from providers.anthropic_provider import AnthropicProvider
from providers.base import (
    ChatProvider,
    ProviderInfo,
    ProviderNotConfiguredError,
    UnknownProviderError,
)
from providers.gemini_provider import GeminiProvider
from providers.openai_provider import OpenAIProvider


_providers: dict[str, ChatProvider] = {
    "gemini": GeminiProvider(
        api_key=GEMINI_API_KEY,
        default_model=GEMINI_MODEL,
        models=GEMINI_MODELS,
    ),
    "openai": OpenAIProvider(
        api_key=OPENAI_API_KEY,
        default_model=OPENAI_MODEL,
        models=OPENAI_MODELS,
    ),
    "anthropic": AnthropicProvider(
        api_key=ANTHROPIC_API_KEY,
        default_model=ANTHROPIC_MODEL,
        models=ANTHROPIC_MODELS,
    ),
}


def get_provider(provider_id: str | None = None) -> ChatProvider:
    selected_id = provider_id or AI_DEFAULT_PROVIDER
    provider = _providers.get(selected_id)

    if provider is None:
        raise UnknownProviderError(f"Unknown provider '{selected_id}'.")

    if not provider.configured:
        raise ProviderNotConfiguredError(
            f"Provider '{selected_id}' is not configured."
        )

    return provider


def list_configured_providers() -> list[ProviderInfo]:
    return [
        ProviderInfo(
            id=provider.id,
            label=provider.label,
            default_model=provider.default_model,
            models=provider.models,
            configured=provider.configured,
        )
        for provider in _providers.values()
        if provider.configured
    ]


def get_default_provider_id() -> str:
    if AI_DEFAULT_PROVIDER in _providers and _providers[AI_DEFAULT_PROVIDER].configured:
        return AI_DEFAULT_PROVIDER

    for provider in _providers.values():
        if provider.configured:
            return provider.id

    raise ProviderNotConfiguredError("No AI provider is configured.")
```

Important: importing this registry constructs clients once at app startup/import time. That is fine for this small app. In a larger app, you might move construction into FastAPI startup state or use lazy initialization.

## Refactor `services/ai.py`

Current `services/ai.py` is doing everything:

- imports Gemini SDK
- creates Gemini client
- creates Gemini tracker
- builds Gemini prompt
- extracts Gemini token usage
- stores history
- calls model

After the refactor, it should not import provider SDKs at all.

It should look like this:

```python
from uuid import uuid4

from core.config import (
    LLM_INGESTION_URL,
    LLM_LOGGING_ENABLED,
    LOG_INGESTION_KEY,
    MAX_CONTEXT_MESSAGES,
)
from models.chat_models import ChatMessage, ChatRequest, ChatResponse, RoleType
from providers.registry import get_provider
from sdk.llm_event_tracker import LLMTracker


ASSISTANT_PROMPT = (
    "You are a helpful assistant. Answer naturally and use the recent "
    "conversation context when it is relevant."
)

conversations: dict[str, list[ChatMessage]] = {}


def _build_messages(history: list[ChatMessage], user_message: str) -> list[ChatMessage]:
    recent_messages = history[-MAX_CONTEXT_MESSAGES:]
    return [
        *recent_messages,
        ChatMessage(role=RoleType.USER, content=user_message),
    ]


def _preview_messages(messages: list[ChatMessage]) -> str:
    return "\n".join(
        f"{message.role.value}: {message.content}" for message in messages
    )


async def run_assistant(payload: ChatRequest) -> ChatResponse:
    conversation_id = payload.conversationId or str(uuid4())
    history = conversations.setdefault(conversation_id, [])
    messages = _build_messages(history, payload.message)

    provider = get_provider(payload.provider.value if payload.provider else None)
    model = provider.resolve_model(payload.model)
    request_id = str(uuid4())

    tracker = LLMTracker(
        provider=provider.id,
        model=model,
        ingestion_url=LLM_INGESTION_URL,
        api_key=LOG_INGESTION_KEY,
        enabled=LLM_LOGGING_ENABLED,
    )

    result = await tracker.track(
        call=lambda: provider.chat(
            messages=messages,
            model=model,
            system_prompt=ASSISTANT_PROMPT,
        ),
        input_text=_preview_messages(messages),
        extract_output=lambda chat_result: chat_result.text,
        extract_token_usage=lambda chat_result: chat_result.token_usage,
        conversation_id=conversation_id,
        request_id=request_id,
        metadata={
            "route": "/v1/api/chat",
            "maxContextMessages": MAX_CONTEXT_MESSAGES,
            "provider": provider.id,
            "model": model,
        },
    )

    assistant_message = ChatMessage(
        role=RoleType.ASSISTANT,
        content=result.text,
    )

    user_message = ChatMessage(role=RoleType.USER, content=payload.message)
    history.extend([user_message, assistant_message])
    conversations[conversation_id] = history[-MAX_CONTEXT_MESSAGES:]

    return ChatResponse(
        conversationId=conversation_id,
        message=assistant_message,
        provider=provider.id,
        model=model,
    )
```

Notice what changed:

1. No `from google import genai`.
2. No global Gemini client.
3. No global Gemini tracker.
4. The tracker is created per request with the selected provider and model.
5. `provider.chat(...)` is the only place where AI generation happens.
6. `services/ai.py` remains responsible for conversation memory.

## Why The Tracker Should Be Created Per Request

Currently:

```python
tracker = LLMTracker(
    provider="gemini",
    model=GEMINI_MODEL or "gemini",
    ...
)
```

That cannot work when every request can choose a different provider/model.

You have two clean options:

### Option A: Create `LLMTracker` per request

```python
tracker = LLMTracker(
    provider=provider.id,
    model=model,
    ...
)
```

This is simple and works well in your current app.

### Option B: Change `LLMTracker.track(...)` to accept provider/model

For example:

```python
await tracker.track(
    provider=provider.id,
    model=model,
    call=...
)
```

This requires changing the tracker class. It is fine, but not necessary.

For this codebase, Option A is easier because your tracker is lightweight. It only stores a few strings and config values. It does not hold a network connection.

## Route Error Handling

Current route catches every exception and returns 500:

```python
except Exception as e:
    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail=f"Can't send your request: {e}",
    ) from e
```

After multi-provider support, provider selection errors should be clearer.

Change:

```text
server/src/routes/run_ai_routes.py
```

to:

```python
from fastapi import APIRouter, HTTPException, status

from models.chat_models import ChatRequest, ChatResponse
from providers.base import (
    ModelNotAllowedError,
    ProviderNotConfiguredError,
    UnknownProviderError,
)
from services.ai import run_assistant


router = APIRouter()


@router.post("/v1/api/chat", response_model=ChatResponse)
async def chat(payload: ChatRequest) -> ChatResponse:
    try:
        return await run_assistant(payload)
    except UnknownProviderError as error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(error),
        ) from error
    except ModelNotAllowedError as error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(error),
        ) from error
    except ProviderNotConfiguredError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(error),
        ) from error
    except Exception as error:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"AI provider request failed: {error}",
        ) from error
```

Why `502 Bad Gateway` for provider failures?

Because your backend is acting as a gateway to an upstream AI provider. If OpenAI, Gemini, or Anthropic fails, the problem is not necessarily your request syntax; it may be upstream failure, invalid provider response, rate limit, network issue, or provider error.

For a student assessment, `500` is acceptable. For production semantics, `502` is cleaner.

## Provider List Endpoint

Add a route that lets the frontend populate the dropdown from backend config.

You can put it in `run_ai_routes.py` for simplicity:

```python
from providers.registry import get_default_provider_id, list_configured_providers
```

Then:

```python
@router.get("/v1/api/providers")
async def providers() -> dict[str, object]:
    provider_infos = list_configured_providers()

    return {
        "defaultProvider": get_default_provider_id(),
        "providers": [
            {
                "id": provider.id,
                "label": provider.label,
                "defaultModel": provider.default_model,
                "models": provider.models,
            }
            for provider in provider_infos
        ],
    }
```

If you want stronger typing, add Pydantic models:

```python
class ProviderInfoResponse(BaseModel):
    id: ProviderType
    label: str
    defaultModel: str
    models: list[str]


class ProviderListResponse(BaseModel):
    defaultProvider: ProviderType
    providers: list[ProviderInfoResponse]
```

Then use:

```python
@router.get("/v1/api/providers", response_model=ProviderListResponse)
```

The simple dict response is enough for a first implementation.

## Frontend API Client

Current:

```text
chatui/src/api/chat.api.ts
```

only has:

```ts
chat: async (data: ChatRequestType): Promise<ChatResponseType> => {
  const response = await axiosInstance.post("/v1/api/chat", data);
  return ChatResponseSchema.parse(response.data);
},
```

Add:

```ts
import {
  ChatResponseSchema,
  ProviderListResponseSchema,
} from "../schemas/run_ai.schemas";
import type {
  ChatRequestType,
  ChatResponseType,
  ProviderListResponseType,
} from "../schemas/run_ai.schemas";
import axiosInstance from "./axios.config";

const ChatApi = {
  chat: async (data: ChatRequestType): Promise<ChatResponseType> => {
    const response = await axiosInstance.post("/v1/api/chat", data);
    return ChatResponseSchema.parse(response.data);
  },

  providers: async (): Promise<ProviderListResponseType> => {
    const response = await axiosInstance.get("/v1/api/providers");
    return ProviderListResponseSchema.parse(response.data);
  },
};

export default ChatApi;
```

## Frontend UI Changes

In:

```text
chatui/src/pages/Chat.tsx
```

you need state for providers:

```tsx
const [providers, setProviders] = useState<ProviderInfoType[]>([]);
const [selectedProvider, setSelectedProvider] = useState<ProviderType>("gemini");
const [selectedModel, setSelectedModel] = useState("");
```

Load providers when the component mounts:

```tsx
import { useEffect, useMemo, useState, type FormEvent } from "react";
```

Then:

```tsx
useEffect(() => {
  let ignore = false;

  const loadProviders = async () => {
    try {
      const response = await ChatApi.providers();
      if (ignore) {
        return;
      }

      setProviders(response.providers);

      const defaultProvider =
        response.providers.find(
          (provider) => provider.id === response.defaultProvider,
        ) ?? response.providers[0];

      if (defaultProvider) {
        setSelectedProvider(defaultProvider.id);
        setSelectedModel(defaultProvider.defaultModel);
      }
    } catch {
      setError("Could not load AI providers.");
    }
  };

  loadProviders();

  return () => {
    ignore = true;
  };
}, []);
```

Find current provider:

```tsx
const currentProvider = useMemo(
  () => providers.find((provider) => provider.id === selectedProvider),
  [providers, selectedProvider],
);
```

When sending:

```tsx
const response = await ChatApi.chat({
  conversationId,
  message,
  provider: selectedProvider,
  model: selectedModel,
});
```

Update the header:

```tsx
<div>
  <p className="eyebrow">
    {currentProvider ? currentProvider.label : "AI Chat"}
  </p>
  <h1>Multi-provider assistant</h1>
</div>
```

Add provider/model dropdowns:

```tsx
<div className="provider-controls">
  <label>
    <span>Provider</span>
    <select
      value={selectedProvider}
      onChange={(event) => {
        const nextProvider = providers.find(
          (provider) => provider.id === event.target.value,
        );

        if (!nextProvider) {
          return;
        }

        setSelectedProvider(nextProvider.id);
        setSelectedModel(nextProvider.defaultModel);
      }}
      disabled={isSending || providers.length === 0}
    >
      {providers.map((provider) => (
        <option key={provider.id} value={provider.id}>
          {provider.label}
        </option>
      ))}
    </select>
  </label>

  <label>
    <span>Model</span>
    <select
      value={selectedModel}
      onChange={(event) => setSelectedModel(event.target.value)}
      disabled={isSending || !currentProvider}
    >
      {(currentProvider?.models ?? []).map((model) => (
        <option key={model} value={model}>
          {model}
        </option>
      ))}
    </select>
  </label>
</div>
```

Update `canSend`:

```tsx
const canSend =
  input.trim().length > 0 &&
  !isSending &&
  providers.length > 0 &&
  selectedProvider &&
  selectedModel;
```

This prevents sending before provider config loads.

### Should Changing Provider Start A New Chat?

You have two product choices.

### Option A: Let users switch provider inside the same conversation

This means:

- user asks with Gemini
- then switches to OpenAI
- OpenAI receives the same recent history
- the conversation continues

This is useful for comparing models.

### Option B: Start a new conversation when provider changes

This avoids confusion because each conversation belongs to one provider/model.

If you choose this, in the provider `onChange`, call:

```tsx
setMessages([]);
setConversationId(undefined);
setInput("");
setError("");
```

For your described UI, I recommend Option A first because the whole point is "user can switch between them". Just make sure the assistant message displays provider/model or the header clearly shows the current provider.

## Frontend CSS

In:

```text
chatui/src/App.css
```

add:

```css
.provider-controls {
  align-items: end;
  display: flex;
  gap: 10px;
}

.provider-controls label {
  color: #4d5c50;
  display: grid;
  font-size: 0.78rem;
  font-weight: 700;
  gap: 4px;
}

.provider-controls select {
  background: #ffffff;
  border: 1px solid #c9d3ca;
  border-radius: 6px;
  color: #17201a;
  min-height: 38px;
  min-width: 150px;
  padding: 0 10px;
}

.provider-controls select:focus {
  border-color: #307a69;
  box-shadow: 0 0 0 3px rgba(48, 122, 105, 0.14);
  outline: none;
}
```

And adjust mobile:

```css
@media (max-width: 640px) {
  .provider-controls {
    align-items: stretch;
    flex-direction: column;
    width: 100%;
  }

  .provider-controls select {
    width: 100%;
  }
}
```

You may also need to make `.chat-header` wrap gracefully if the controls become wide.

## Conversation Context Design

Current memory:

```python
conversations: dict[str, list[ChatMessage]] = {}
```

This is in-memory only.

That means:

1. It is lost when the server restarts.
2. It is shared by all users if there is no session/user id.
3. It is not safe for multi-worker production deployments.
4. It is fine for a small assessment app.

For multi-provider support, this memory can stay as-is for the first version.

The important change is to store canonical app messages:

```python
ChatMessage(role=RoleType.USER, content="...")
ChatMessage(role=RoleType.ASSISTANT, content="...")
```

Do not store raw provider responses as the main history format.

Bad:

```python
history.append(openai_response)
history.append(anthropic_message)
history.append(gemini_response)
```

Good:

```python
history.append(ChatMessage(role=RoleType.USER, content=payload.message))
history.append(ChatMessage(role=RoleType.ASSISTANT, content=result.text))
```

Why?

Because every provider adapter can translate `ChatMessage` into its own format.

## Role Mapping

Your app currently uses:

```python
class RoleType(str, Enum):
    ASSISTANT = "Assistant"
    USER = "User"
```

Provider APIs usually use lowercase roles:

```text
user
assistant
system
developer
```

Keep your public UI roles as `"User"` and `"Assistant"` if you want. Just map them in adapters.

Example:

```python
def _to_openai_role(role: RoleType) -> str:
    if role == RoleType.ASSISTANT:
        return "assistant"
    return "user"
```

Do the same for Anthropic.

For Gemini, you can start with transcript text:

```text
User: hi
Assistant: hello
User: explain X
```

Later, you can improve to native Gemini content objects.

## Prompt Design

Current prompt is:

```python
ASSISTANT_PROMPT = (
    "You are a helpful assistant. Answer naturally and use the recent "
    "conversation context when it is relevant."
)
```

Keep that as the common system prompt.

But do not bake it into the same string as history for every provider.

Better:

```python
provider.chat(
    messages=messages,
    model=model,
    system_prompt=ASSISTANT_PROMPT,
)
```

Then each provider handles it correctly:

- Gemini: `GenerateContentConfig(system_instruction=...)`
- OpenAI Responses: `instructions=...`
- OpenAI Chat Completions: first `system` message
- Anthropic: `system=...`

This is one of the main reasons to create provider adapters.

## Token Usage Mapping

Your logging model uses:

```python
class TokenUsage(BaseModel):
    inputTokens: int | None
    outputTokens: int | None
    totalTokens: int | None
```

Each provider names token usage differently.

### Gemini

Current code extracts:

```python
inputTokens=getattr(usage, "prompt_token_count", None)
outputTokens=getattr(usage, "candidates_token_count", None)
totalTokens=getattr(usage, "total_token_count", None)
```

Keep that in `gemini_provider.py`.

### OpenAI

OpenAI Responses usage commonly maps to:

```python
inputTokens=usage.input_tokens
outputTokens=usage.output_tokens
totalTokens=usage.total_tokens
```

Keep that in `openai_provider.py`.

### Anthropic

Anthropic usage commonly maps to:

```python
inputTokens=usage.input_tokens
outputTokens=usage.output_tokens
totalTokens=usage.input_tokens + usage.output_tokens
```

Keep that in `anthropic_provider.py`.

The service and tracker should never need provider-specific token field names.

## LLM Event Logging

Your current tracker is already generic enough:

```python
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
```

The only problem is where it is created.

Currently it is created once globally with Gemini.

Move tracker creation into `run_assistant` after provider/model selection:

```python
tracker = LLMTracker(
    provider=provider.id,
    model=model,
    ingestion_url=LLM_INGESTION_URL,
    api_key=LOG_INGESTION_KEY,
    enabled=LLM_LOGGING_ENABLED,
)
```

Then logged events automatically get correct values:

```json
{
  "provider": "openai",
  "model": "gpt-4.1",
  "status": "success"
}
```

or:

```json
{
  "provider": "anthropic",
  "model": "claude-opus-4-6",
  "status": "error"
}
```

No database migration is needed because `provider` and `model` already exist.

## Full Backend File Change Checklist

### 1. Update `server/pyproject.toml`

Add provider SDKs:

```toml
"openai (>=2.0.0,<3.0.0)",
"anthropic (>=0.70.0,<1.0.0)",
```

Then update lock/install:

```powershell
cd server
poetry lock
poetry install
```

### 2. Update `server/src/core/config.py`

Add:

```python
AI_DEFAULT_PROVIDER = _clean_env("AI_DEFAULT_PROVIDER") or "gemini"

GEMINI_MODEL = _clean_env("GEMINI_MODEL") or "gemini-3.5-flash"
GEMINI_MODELS = _get_csv_env("GEMINI_MODELS") or [GEMINI_MODEL]

OPENAI_API_KEY = _clean_env("OPENAI_API_KEY")
OPENAI_MODEL = _clean_env("OPENAI_MODEL") or "gpt-4.1"
OPENAI_MODELS = _get_csv_env("OPENAI_MODELS") or [OPENAI_MODEL]

ANTHROPIC_API_KEY = _clean_env("ANTHROPIC_API_KEY")
ANTHROPIC_MODEL = _clean_env("ANTHROPIC_MODEL") or "claude-opus-4-6"
ANTHROPIC_MODELS = _get_csv_env("ANTHROPIC_MODELS") or [ANTHROPIC_MODEL]

MAX_CONTEXT_MESSAGES = _get_int_env("MAX_CONTEXT_MESSAGES", 8)
```

### 3. Update `server/src/models/chat_models.py`

Add:

```python
class ProviderType(str, Enum):
    GEMINI = "gemini"
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
```

Add fields:

```python
class ChatRequest(BaseModel):
    conversationId: Optional[str] = None
    message: str = Field(min_length=1)
    provider: ProviderType | None = None
    model: str | None = Field(default=None, min_length=1, max_length=200)
```

Return selected provider/model:

```python
class ChatResponse(BaseModel):
    conversationId: str
    message: ChatMessage
    provider: ProviderType
    model: str
```

Optionally add provider list response models.

### 4. Add `server/src/providers/__init__.py`

Empty file:

```python
```

### 5. Add `server/src/providers/base.py`

Contains:

- `ProviderInfo`
- `ProviderChatResult`
- `ChatProvider`
- provider exceptions

### 6. Add `server/src/providers/gemini_provider.py`

Move Gemini-specific logic from `services/ai.py` here.

### 7. Add `server/src/providers/openai_provider.py`

Implement OpenAI Responses API call.

### 8. Add `server/src/providers/anthropic_provider.py`

Implement Anthropic Messages API call.

### 9. Add `server/src/providers/registry.py`

Build provider instances and expose:

```python
get_provider(...)
list_configured_providers()
get_default_provider_id()
```

### 10. Refactor `server/src/services/ai.py`

Remove:

```python
import asyncio
from google import genai
GEMINI_API_KEY
GEMINI_MODEL
client = genai.Client(...)
extract_gemini_token_usage(...)
```

Add:

```python
from providers.registry import get_provider
```

Create tracker per request.

Call:

```python
provider.chat(...)
```

### 11. Update `server/src/routes/run_ai_routes.py`

Add provider-specific error handling.

Add:

```python
@router.get("/v1/api/providers")
```

### 12. No database schema change required

`provider` and `model` already exist in LLM events.

## Full Frontend File Change Checklist

### 1. Update `chatui/src/schemas/run_ai.schemas.ts`

Add:

- `ProviderTypeSchema`
- request `provider`
- request `model`
- response `provider`
- response `model`
- provider list response schema

### 2. Update `chatui/src/api/chat.api.ts`

Add:

```ts
providers: async () => {
  const response = await axiosInstance.get("/v1/api/providers");
  return ProviderListResponseSchema.parse(response.data);
}
```

### 3. Update `chatui/src/pages/Chat.tsx`

Add:

- provider list state
- selected provider state
- selected model state
- provider loading effect
- provider dropdown
- model dropdown
- send selected provider/model in chat request
- display selected provider/model in header or message metadata

### 4. Update `chatui/src/App.css`

Add styles for dropdown controls.

## Example Final User Flow

1. User opens UI.
2. UI calls `GET /v1/api/providers`.
3. Backend returns configured providers.
4. UI renders dropdown:

```text
[Google Gemini v] [gemini-3.5-flash v]
```

5. User switches provider:

```text
[OpenAI v] [gpt-4.1 v]
```

6. User sends message.
7. UI posts:

```json
{
  "conversationId": "abc",
  "message": "Explain LangGraph",
  "provider": "openai",
  "model": "gpt-4.1"
}
```

8. Backend:

```python
provider = get_provider("openai")
model = provider.resolve_model("gpt-4.1")
result = await provider.chat(...)
```

9. Tracker logs:

```json
{
  "provider": "openai",
  "model": "gpt-4.1",
  "status": "success"
}
```

10. Backend returns:

```json
{
  "conversationId": "abc",
  "message": {
    "role": "Assistant",
    "content": "LangGraph is..."
  },
  "provider": "openai",
  "model": "gpt-4.1"
}
```

11. UI appends assistant response.

## Minimal Implementation Versus Clean Implementation

You can build this in two ways.

### Fast Minimal Version

In `services/ai.py`:

```python
if payload.provider == ProviderType.OPENAI:
    call OpenAI
elif payload.provider == ProviderType.ANTHROPIC:
    call Anthropic
else:
    call Gemini
```

This is faster to write but worse long-term.

Problems:

- `services/ai.py` becomes huge
- provider SDK imports all live together
- token usage extraction is mixed together
- adding provider 4 means editing the same big function again
- route/service tests become harder

### Clean Version

Use provider adapters:

```text
providers/base.py
providers/gemini_provider.py
providers/openai_provider.py
providers/anthropic_provider.py
providers/registry.py
```

This is the right version for your feature.

The clean version is not overengineering here because "multi-provider support" is exactly the kind of feature that benefits from an adapter interface.

## Testing Strategy

### Backend Unit Tests

Add tests for provider registry:

1. Unknown provider raises `UnknownProviderError`.
2. Missing API key makes provider unconfigured.
3. Requested model outside allowlist raises `ModelNotAllowedError`.
4. `get_default_provider_id()` returns configured default.
5. If default provider is not configured, it picks the first configured provider.

Add tests for `run_assistant` using a fake provider:

1. It passes recent history plus new user message.
2. It returns provider and model in response.
3. It appends user and assistant messages to history.
4. It keeps only `MAX_CONTEXT_MESSAGES`.
5. It creates tracker metadata with provider/model.

You do not need real OpenAI/Anthropic/Gemini calls in unit tests. Mock the provider.

### Backend Integration Tests

Test:

```text
POST /v1/api/chat
```

with:

```json
{
  "message": "hello",
  "provider": "not-real"
}
```

should return `422` if Pydantic enum catches it, or `400` if you accept string and validate manually.

Test:

```json
{
  "message": "hello",
  "provider": "openai",
  "model": "not-allowed"
}
```

should return `400`.

Test:

```text
GET /v1/api/providers
```

returns only configured providers.

### Frontend Tests Or Manual Checks

At minimum:

1. Run `pnpm build`.
2. Open the UI.
3. Confirm provider dropdown appears.
4. Confirm model dropdown changes when provider changes.
5. Confirm sending includes provider/model.
6. Confirm New chat keeps selected provider or intentionally resets it.
7. Confirm API errors show a friendly message.

## Manual Verification Commands

From backend:

```powershell
cd server
poetry install
poetry run uvicorn src.server:app --reload
```

From frontend:

```powershell
cd chatui
pnpm install
pnpm build
pnpm dev
```

Provider list check:

```powershell
Invoke-RestMethod http://localhost:8000/v1/api/providers
```

Chat check:

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri http://localhost:8000/v1/api/chat `
  -ContentType "application/json" `
  -Body '{
    "message": "Say hello in one sentence.",
    "provider": "gemini",
    "model": "gemini-3.5-flash"
  }'
```

Then try:

```json
{
  "message": "Say hello in one sentence.",
  "provider": "openai",
  "model": "gpt-4.1"
}
```

and:

```json
{
  "message": "Say hello in one sentence.",
  "provider": "anthropic",
  "model": "claude-opus-4-6"
}
```

Only providers with valid API keys will work.

## Edge Cases To Handle

### 1. No provider is configured

`GET /v1/api/providers` should return a useful error or empty list.

Better:

```json
{
  "detail": "No AI provider is configured."
}
```

with `503`.

### 2. Provider configured but model missing

Use default model:

```python
model = requested_model or self.default_model
```

### 3. Provider configured but default model not in model list

Make config robust:

```python
OPENAI_MODELS = _get_csv_env("OPENAI_MODELS") or [OPENAI_MODEL]
if OPENAI_MODEL not in OPENAI_MODELS:
    OPENAI_MODELS = [OPENAI_MODEL, *OPENAI_MODELS]
```

You can create a helper:

```python
def _models_with_default(models: list[str], default_model: str) -> list[str]:
    if default_model in models:
        return models
    return [default_model, *models]
```

### 4. Provider returns empty text

Each adapter should check:

```python
if not text:
    raise RuntimeError("Provider returned an empty response.")
```

### 5. Token usage missing

Return:

```python
token_usage=None
```

The tracker already accepts `None`.

### 6. User switches provider mid-conversation

This is okay because the stored history is provider-neutral.

### 7. Anthropic requires alternating messages

Anthropic APIs can be stricter about message roles than some other providers. Your current chat history naturally alternates user/assistant if every request succeeds. If a request fails after adding a user message, avoid appending that failed user message. The refactor above appends only after the provider returns successfully, so history remains clean.

### 8. Frontend sends provider before list loads

Disable send until provider list is loaded:

```ts
providers.length > 0
```

### 9. Provider SDK import fails

If you install `openai` and `anthropic`, imports should work.

If you want optional dependencies, you can import provider SDKs inside the provider constructors. But for this app, normal dependencies are simpler.

## Security Notes

1. API keys stay in backend `.env`.
2. Do not return configured/unconfigured reason details that expose secrets.
3. Do not let frontend send arbitrary model names unless this is an admin-only app.
4. Keep provider errors friendly in UI but detailed in server logs.
5. If you later add user accounts, conversation memory should be keyed by user/session.
6. If you deploy with multiple workers, in-memory conversation state will not be consistent.

## Production Improvements Later

After the basic feature works, you can improve:

### 1. Persistent Conversations

Move:

```python
conversations: dict[str, list[ChatMessage]] = {}
```

to a database table.

Suggested tables:

```sql
conversations (
  id,
  user_id,
  created_at,
  updated_at
)

conversation_messages (
  id,
  conversation_id,
  role,
  content,
  provider,
  model,
  created_at
)
```

Store provider/model per assistant message so users can see which model answered each turn.

### 2. Streaming

Provider adapters can expose:

```python
async def stream_chat(...) -> AsyncIterator[str]:
    ...
```

Then the route can use FastAPI streaming responses or WebSockets.

### 3. Fallback Provider

Example:

```python
try:
    result = await selected_provider.chat(...)
except RateLimitError:
    result = await fallback_provider.chat(...)
```

If you do this, return the actual provider/model used in `ChatResponse`.

### 4. Provider Capabilities

Your provider info can include:

```json
{
  "id": "openai",
  "supportsStreaming": true,
  "supportsTools": true,
  "supportsImages": true
}
```

Then the UI can enable/disable features based on provider capability.

### 5. Cost Controls

Add:

- max output tokens per provider
- allowed model tiers
- user-level daily limits
- request timeout
- retry policy

### 6. Observability

Your LLM event system already captures:

- provider
- model
- status
- latency
- token usage
- metadata

Once multi-provider support exists, you can compare:

- latency by provider
- error rate by provider
- token usage by model
- cost by provider/model if you add pricing metadata

## Recommended First Commit Plan

Implement in this order:

1. Update backend config for provider env vars.
2. Update backend chat models with `provider` and `model`.
3. Add provider base contract and errors.
4. Move Gemini into `GeminiProvider`.
5. Add provider registry.
6. Refactor `services/ai.py` to use `get_provider(...).chat(...)`.
7. Add `/v1/api/providers`.
8. Verify Gemini still works.
9. Add OpenAI dependency and adapter.
10. Verify OpenAI works with a real key.
11. Add Anthropic dependency and adapter.
12. Verify Anthropic works with a real key.
13. Update frontend schemas and API client.
14. Add dropdowns in `Chat.tsx`.
15. Update CSS.
16. Run backend and frontend builds/tests.

This order matters because Gemini already works. By moving Gemini first, you prove the architecture without introducing new provider SDK uncertainty at the same time.

## What Not To Do

Do not make the frontend call OpenAI/Anthropic/Gemini directly.

Reasons:

- exposes API keys
- CORS/provider browser restrictions
- no central logging
- no model allowlist
- no rate limiting
- no consistent conversation storage

Do not use provider names as display labels everywhere.

Use stable ids:

```text
openai
gemini
anthropic
```

and labels:

```text
OpenAI
Google Gemini
Anthropic Claude
```

Do not store provider-specific raw response objects in chat history.

Store your own canonical `ChatMessage`.

Do not make one giant `if provider == ...` function.

Use adapters.

## Final Target Mental Model

The backend should feel like this:

```python
provider = get_provider(payload.provider)
model = provider.resolve_model(payload.model)

result = await tracker.track(
    call=lambda: provider.chat(
        messages=messages,
        model=model,
        system_prompt=ASSISTANT_PROMPT,
    ),
    extract_output=lambda result: result.text,
    extract_token_usage=lambda result: result.token_usage,
)
```

The frontend should feel like this:

```tsx
<select value={selectedProvider}>
  <option value="anthropic">Anthropic Claude</option>
  <option value="openai">OpenAI</option>
  <option value="gemini">Google Gemini</option>
</select>
```

and send:

```ts
await ChatApi.chat({
  conversationId,
  message,
  provider: selectedProvider,
  model: selectedModel,
});
```

That is multi-provider support: one app contract, multiple provider adapters, one UI dropdown, consistent logging, and no hardcoded provider call in the main chat service.
