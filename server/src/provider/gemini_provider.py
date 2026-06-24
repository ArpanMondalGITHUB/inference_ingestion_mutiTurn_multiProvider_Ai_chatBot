import asyncio
from google import genai
from google.genai import types
from models.chat_models import ChatMessage, RoleType
from models.llm_enference_models import TokenUsage
from provider.base import (
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