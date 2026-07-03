from typing import Any, AsyncIterator
from uuid import uuid4
from datetime import datetime, timezone

from core.config import (
    LLM_INGESTION_URL,
    LLM_LOGGING_ENABLED,
    LOG_INGESTION_KEY,
    MAX_CONTEXT_MESSAGES,
)
from db.db import insert_message, list_conversations_db,get_conversation_db,get_message_for_conversations, delete_conversation_db, upsert_conversation
from models.chat_models import ChatMessage, ChatRequest, ChatResponse, ChatStreamChunk, ChatStreamDone, ChatStreamEvent, ChatStreamStart, ProviderType, RoleType , ConversationDetail , ConversationSummary
from provider.registry import get_provider
from sdk.llm_event_tracker import LLMTracker


ASSISTANT_PROMPT = (
    "You are a helpful assistant. Answer naturally and use the recent "
    "conversation context when it is relevant."
)

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

def _make_title(first_message: str, max_chars: int = 60) -> str:
    stripped = first_message.strip()
    return stripped[:max_chars] + ("…" if len(stripped) > max_chars else "")

def list_conversations() -> list[ConversationSummary]:
    return [
        ConversationSummary(
            conversationId=row["conversation_id"],
            title=row["title"] or "Untitled conversation",
            messageCount=row["message_count"],
            provider=row["provider"],
            model=row["model"],
            createdAt=row["created_at"],
            updatedAt=row["updated_at"],
        )
        for row in list_conversations_db()
    ]


def get_conversation(conversation_id: str) -> ConversationDetail | None:
    row = get_conversation_db(conversation_id)
    if row is None:
        return None

    db_messages = get_message_for_conversations(conversation_id)
    return ConversationDetail(
        conversationId=row["conversation_id"],
        title=row["title"] or "Untitled conversation",
        messageCount=len(db_messages),
        provider=row["provider"],
        model=row["model"],
        createdAt=row["created_at"],
        updatedAt=row["updated_at"],
        messages=[
            ChatMessage(role=RoleType(m["role"]), content=m["content"])
            for m in db_messages
        ],
    )

def delete_conversation(conversation_id: str) -> bool:
    return delete_conversation_db(conversation_id)


def _build_context(db_messages: list[dict[str, Any]], user_message: str) -> list[ChatMessage]:
    # Trim to window before adding the new user message
    recent = db_messages[-MAX_CONTEXT_MESSAGES:]
    history = [
        ChatMessage(role=RoleType(row["role"]), content=row["content"])
        for row in recent
    ]
    return [*history, ChatMessage(role=RoleType.USER, content=user_message)]


def _preview(messages: list[ChatMessage]) -> str:
    return "\n".join(f"{m.role.value}: {m.content}" for m in messages)


async def run_assistant(payload: ChatRequest) -> ChatResponse:
    conversation_id = payload.conversationId or str(uuid4())
    session_id = payload.session_id or str(uuid4())
    now = _now_iso()

    db_messages = get_message_for_conversations(conversation_id)
    messages = _build_context(db_messages, payload.message)

    existing = get_conversation_db(conversation_id)
    title = existing["title"] if existing else _make_title(payload.message)
    created_at = existing["created_at"] if existing else now

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
        input_text=_preview(messages),
        extract_output=lambda chat_result: chat_result.text,
        extract_token_usage=lambda chat_result: chat_result.token_usage,
        conversation_id=conversation_id,
        session_id=session_id,
        request_id=request_id,
        metadata={
            "route": "/v1/api/chat",
            "maxContextMessages": MAX_CONTEXT_MESSAGES,
            "provider": provider.id,
            "model": model,
        },
    )
    print(result.text)
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
        created_at=now
    )
    insert_message(
        conversation_id, 
        role=RoleType.ASSISTANT.value, 
        content=result.text, 
        created_at=now
    )

    return ChatResponse(
        conversationId=conversation_id,
        message=ChatMessage(role=RoleType.ASSISTANT, content=result.text),
        provider=provider.id,
        model=model,
    )



async def stream_assistant(payload:ChatRequest) -> AsyncIterator[ChatStreamEvent]:
    conversation_id = payload.conversationId or str(uuid4())
    session_id = payload.session_id or str(uuid4())
    now = _now_iso()
    

    db_message = get_message_for_conversations(conversation_id)
    message = _build_context(db_message,payload.message)

    existing = get_conversation_db(conversation_id)
    title = existing["title"] if existing else _make_title(payload.message)
    created_at = existing["created_at"] if existing else now

    provider = get_provider(payload.provider.value if payload.provider else None)
    model = provider.resolve_model(payload.model)
    provider_type = ProviderType(provider.id)
    request_id = str(uuid4())

    tracker = LLMTracker(
        provider=provider.id,
        model=model,
        ingestion_url=LLM_INGESTION_URL,
        api_key=LOG_INGESTION_KEY,
        enabled=LLM_LOGGING_ENABLED,
    )

    yield ChatStreamStart(
        conversationId=conversation_id,
        provider=provider_type,
        model=model,
        requestId=request_id,
    )

    upsert_conversation(
        conversation_id=conversation_id,
        title=title,
        provider=provider_type,
        model=model,
        created_at=created_at,
        updated_at=now
    )

    insert_message(
        conversation_id=conversation_id,
        role=RoleType.USER.value,
        content=payload.message,
        created_at=now
    )

    parts: list[str] = []

    stream = tracker.track_stream(
        stream=provider.chat_stream(
            messages=message,
            model=model,
            system_prompt=ASSISTANT_PROMPT
        ),
        input_text=_preview(message),
        session_id=session_id,
        conversation_id=conversation_id,
        request_id=request_id,
        metadata={
            "operation": "stream_assistant",
            "maxContextMessages": MAX_CONTEXT_MESSAGES,
            "provider": provider.id,
            "model": model,
        }
    )

    async for chunk in stream:
        if not chunk:
            continue

        parts.append(chunk)
        yield ChatStreamChunk(content=chunk)
    
    assistant_text = "".join(parts).strip()
    if not assistant_text:
        raise RuntimeError(f"{provider.label} returned an empty response.")
    
    finished_at = _now_iso()

    insert_message(
        conversation_id=conversation_id,
        role=RoleType.ASSISTANT.value,
        content=assistant_text,
        created_at=finished_at,
    )

    upsert_conversation(
        conversation_id=conversation_id,
        title=title,
        provider=provider.id,
        model=model,
        created_at=created_at,
        updated_at=finished_at,
    )

    yield ChatStreamDone(
        conversationId=conversation_id,
        message=ChatMessage(role=RoleType.ASSISTANT,content=assistant_text),
        provider=provider_type,
        model=model,
    )