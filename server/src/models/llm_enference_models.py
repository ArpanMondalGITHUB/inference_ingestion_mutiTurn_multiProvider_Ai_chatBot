from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


MAX_PREVIEW_CHARS = 1000
MAX_ERROR_MESSAGE_CHARS = 2000


def _parse_iso_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)

    return parsed


class TokenUsage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    inputTokens: int | None = Field(default=None, ge=0, strict=True)
    outputTokens: int | None = Field(default=None, ge=0, strict=True)
    totalTokens: int | None = Field(default=None, ge=0, strict=True)


class LLMInferenceEvent(BaseModel):
    model_config = ConfigDict(extra="allow")

    eventId: str = Field(min_length=1, max_length=200, strict=True)
    provider: str = Field(min_length=1, max_length=100, strict=True)
    model: str = Field(min_length=1, max_length=200, strict=True)
    status: Literal["success", "error"]
    startedAt: str = Field(strict=True)
    endedAt: str = Field(strict=True)
    latencyMs: int = Field(ge=0, strict=True)
    errorType: str | None = Field(default=None, max_length=200, strict=True)
    errorMessage: str | None = Field(
        default=None,
        max_length=MAX_ERROR_MESSAGE_CHARS,
        strict=True,
    )
    sessionId: str | None = Field(default=None, max_length=200, strict=True)
    conversationId: str | None = Field(default=None, max_length=200, strict=True)
    requestId: str | None = Field(default=None, max_length=200, strict=True)
    inputPreview: str | None = Field(
        default=None,
        max_length=MAX_PREVIEW_CHARS,
        strict=True,
    )
    outputPreview: str | None = Field(
        default=None,
        max_length=MAX_PREVIEW_CHARS,
        strict=True,
    )
    tokenUsage: TokenUsage | None = None
    metadata: dict[str, Any] | None = None

    @field_validator("eventId", "provider", "model")
    @classmethod
    def required_strings_must_not_be_blank(cls, value: str) -> str:
        if value.strip() == "":
            raise ValueError("must be a non-empty string")

        return value

    @field_validator("startedAt", "endedAt")
    @classmethod
    def timestamps_must_be_iso_strings(cls, value: str) -> str:
        try:
            _parse_iso_timestamp(value)
        except ValueError as exc:
            raise ValueError("must be a valid ISO timestamp string") from exc

        return value

    @model_validator(mode="after")
    def ended_at_must_not_be_before_started_at(self) -> "LLMInferenceEvent":
        started = _parse_iso_timestamp(self.startedAt)
        ended = _parse_iso_timestamp(self.endedAt)

        if ended < started:
            raise ValueError("endedAt must be after startedAt")

        return self


class LLMEventBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    events: list[LLMInferenceEvent] = Field(min_length=1)
