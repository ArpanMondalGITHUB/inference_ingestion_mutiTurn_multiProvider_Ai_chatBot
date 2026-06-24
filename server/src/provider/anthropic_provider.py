from anthropic import AsyncAnthropic
from models.chat_models import ChatMessage, RoleType
from models.llm_enference_models import TokenUsage
from provider.base import (
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