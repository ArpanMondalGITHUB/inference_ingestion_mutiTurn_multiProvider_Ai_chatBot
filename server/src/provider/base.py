# will contain----

# providerinfo
# providerchatresult
# chatprovider
# provider exceptions

from typing import Protocol , AsyncIterator
from dataclasses import dataclass
from models.llm_enference_models import TokenUsage
from models.chat_models import ChatMessage

@dataclass(frozen=True)
class ProviderInfo:
    id:str
    label:str
    default_model:str
    models:list[str]
    configured:bool

@dataclass(frozen=True)
class ProviderChatResult:
    text:str
    token_usage:TokenUsage | None = None

class ChatProvider(Protocol):
    id:str
    label:str
    default_model:str
    models:list[str]

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
        
    async def chat_stream(
        self,
        *,
        messages: list[ChatMessage],
        model: str,
        system_prompt: str,
    ) -> AsyncIterator[str]:
        ...

# provider_exceptions

class ProviderError(Exception):
    pass


class UnknownProviderError(ProviderError):
    pass


class ProviderNotConfiguredError(ProviderError):
    pass


class ModelNotAllowedError(ProviderError):
    pass