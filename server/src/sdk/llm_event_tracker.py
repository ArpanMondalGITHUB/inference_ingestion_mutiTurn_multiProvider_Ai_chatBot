import asyncio
import json
from core.redaction import redact
import time
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Awaitable, Callable
from uuid import uuid4
from events.broker import broker
import httpx
from models.llm_enference_models import LLMInferenceEvent, TokenUsage

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
                errorMessage=redact(str(error)),
                metadata=metadata,
            )

            self._send_soon(event)
            raise

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
                sessionId=session_id,
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
                sessionId=session_id,
                requestId=request_id,
                inputPreview=_preview(input_text),
                outputPreview=_preview("".join(parts)),
                errorType=type(error).__name__,
                errorMessage=redact(str(error)),
                metadata={
                    **(metadata or {}),
                    "stream": True,
                    "chunkCount": chunk_count,
                },
            )
            self._send_soon(event)
            raise

    def _send_soon(self, event: LLMInferenceEvent) -> None:
        if not self.enabled:
            return

        asyncio.create_task(self._publish(event=event))

    async def _publish(self, event: LLMInferenceEvent) -> None:
        try:
            await broker.publish(event.model_dump(mode="json",exclude_none=True))
        except Exception:
            pass

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _preview(value: str | None, max_length: int = 300) -> str | None:
    if not value:
        return None

    clean = " ".join(redact(value).split())
    if len(clean) <= max_length:
        return clean

    return clean[: max_length - 3] + "..."
