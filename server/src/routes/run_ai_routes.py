import json
import traceback
from collections.abc import AsyncIterator
from fastapi import APIRouter, HTTPException, status
from fastapi.responses import StreamingResponse

from models.chat_models import(
    ChatRequest, 
    ChatResponse ,
    ConversationListResponse,
    ConversationDetailResponse
)

from provider.base import (
    ModelNotAllowedError,
    ProviderNotConfiguredError,
    UnknownProviderError,
)
from services.ai import (
    delete_conversation,
    get_conversation, 
    list_conversations, 
    run_assistant,
    stream_assistant
)
from provider.registry import get_default_provider_id, list_configured_providers

router = APIRouter()


def _sse(event_name: str, data: dict) -> str:
    return (
        f"event: {event_name}\n"
        f"data: {json.dumps(data, ensure_ascii=False)}\n\n"
    )

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
            detail=str(error),
        ) from error
    

@router.post("/v1/api/chat/stream")
async def chat_stream(payload: ChatRequest) -> StreamingResponse:
    async def events() -> AsyncIterator[str]:
        try:
            async for event in stream_assistant(payload):
                event_data = event.model_dump()
                yield _sse(event_data["type"], event_data)

        except UnknownProviderError as error:
            yield _sse("error", {
                "type": "error",
                "message": str(error),
            })
        except ModelNotAllowedError as error:
            yield _sse("error", {
                "type": "error",
                "message": str(error),
            })
        except ProviderNotConfiguredError as error:
            yield _sse("error", {
                "type": "error",
                "message": str(error),
            })
        except Exception as error:
            yield _sse("error", {
                "type": "error",
                "message": str(error),
            })

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
    
@router.get("/v1/api/providers")
async def providers() -> dict[str, object]:
    try:
        provider_infos = list_configured_providers()
        default_provider = get_default_provider_id()
    except ProviderNotConfiguredError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(error),
        ) from error

    return {
        "defaultProvider": default_provider,
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


@router.get("/v1/api/conversations", response_model=ConversationListResponse)
async def list_conversations_route() -> ConversationListResponse:
    return ConversationListResponse(conversations=list_conversations())


@router.get("/v1/api/conversations/{conversation_id}", response_model=ConversationDetailResponse)
async def get_conversation_route(conversation_id: str) -> ConversationDetailResponse:
    conversation = get_conversation(conversation_id)
    if conversation is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conversation not found.",
        )
    return ConversationDetailResponse(conversation=conversation)


@router.delete("/v1/api/conversations/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_conversation_route(conversation_id: str) -> None:
    deleted = delete_conversation(conversation_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conversation not found.",
        )