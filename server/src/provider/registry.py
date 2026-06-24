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
from provider.anthropic_provider import AnthropicProvider
from provider.base import (
    ChatProvider,
    ProviderInfo,
    ProviderNotConfiguredError,
    UnknownProviderError,
)
from provider.gemini_provider import GeminiProvider
from provider.open_ai_provider import OpenAIProvider

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