from openai import AsyncOpenAI
from models.chat_models import ChatMessage, RoleType
from models.llm_enference_models import TokenUsage
from provider.base import (
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