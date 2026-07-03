from typing import Literal, Optional, Union
from pydantic import BaseModel, Field
from enum import Enum

class RoleType(str,Enum):
    ASSISTANT='Assistant'
    USER='User'

class ProviderType(str,Enum):
    ANTHROPIC='anthropic'
    GEMINI='gemini'
    OPENAI='openai'

class ChatMessage(BaseModel):
    role:RoleType = Field(description="for understanding role types")
    content:str


class ChatRequest(BaseModel):
    conversationId:Optional[str] = None
    session_id:Optional[str] = None
    message:str = Field(min_length=1)
    provider:ProviderType | None = None
    model:str | None = Field(default=None,min_length=1,max_length=200)

class ChatResponse(BaseModel):
    conversationId:str
    message:ChatMessage
    provider:ProviderType
    model:str


class ConversationSummary(BaseModel):
    conversationId: str
    title: str
    messageCount: int = Field(ge=0)
    provider: str
    model: str
    createdAt: str
    updatedAt: str


class ConversationDetail(ConversationSummary):
    messages: list[ChatMessage]


class ConversationListResponse(BaseModel):
    conversations: list[ConversationSummary]


class ConversationDetailResponse(BaseModel):
    conversation: ConversationDetail


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