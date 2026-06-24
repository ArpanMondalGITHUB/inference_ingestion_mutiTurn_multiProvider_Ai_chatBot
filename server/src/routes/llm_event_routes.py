import json
from datetime import datetime, timezone
from typing import Any
from fastapi import APIRouter, Header, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from db.db import get_llm_event,insert_llm_event,list_llm_events
from core.config import (
    LOG_INGESTION_KEY,
    MAX_EVENTS_PER_REQUEST,
    MAX_INGESTION_BODY_BYTES,
)
from models.llm_enference_models import LLMInferenceEvent

router = APIRouter()


@router.post("/llm-events", status_code=status.HTTP_202_ACCEPTED)
async def ingest_llm_event(
    request: Request,
    authorization: str | None = Header(default=None),
) -> JSONResponse:
    authenticate(authorization)

    payload, payload_error = await read_json_payload(request)
    if payload_error:
        return payload_error

    events, errors = parse_events_body(payload)
    if errors:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={
                "error": "Invalid ingestion payload.",
                "details": errors,
            },
        )

    received_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    request_metadata = {
        "clientIp": request.client.host if request.client else None,
        "userAgent": request.headers.get("user-agent"),
        "receivedAt": received_at,
    }

    processed_events = [
        process_event(event, request_metadata) for event in events
    ]


    for event in processed_events:
        insert_llm_event(event)
        print("LLM event received", json.dumps(event, ensure_ascii=False))

    return JSONResponse(
        status_code=status.HTTP_202_ACCEPTED,
        content={"ok": True, "accepted": len(processed_events),},
    )


def authenticate(authorization: str | None) -> None:
    if not LOG_INGESTION_KEY:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="LOG_INGESTION_KEY is not configured on the ingestion service.",
        )

    expected = f"Bearer {LOG_INGESTION_KEY}"
    if authorization != expected:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")


async def read_json_payload(request: Request) -> tuple[Any | None, JSONResponse | None]:
    size_error = validate_content_length(request)
    if size_error:
        return None, size_error

    raw_body = await request.body()
    if len(raw_body) > MAX_INGESTION_BODY_BYTES:
        return None, payload_too_large_response()

    try:
        return json.loads(raw_body), None
    except (json.JSONDecodeError, UnicodeDecodeError):
        return (
            None,
            JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={"error": "Request body must be valid JSON."},
            ),
        )


def validate_content_length(request: Request) -> JSONResponse | None:
    content_length = request.headers.get("content-length")
    if content_length is None:
        return None

    try:
        request_size = int(content_length)
    except ValueError:
        return None

    if request_size <= MAX_INGESTION_BODY_BYTES:
        return None

    return payload_too_large_response()


def payload_too_large_response() -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
        content={
            "error": "Ingestion payload is too large.",
            "maxBytes": MAX_INGESTION_BODY_BYTES,
        },
    )


def parse_events_body(payload: Any) -> tuple[list[LLMInferenceEvent], list[str]]:
    event_payloads = payload.get("events") if isinstance(payload, dict) else None
    if event_payloads is None:
        event_payloads = [payload]
    elif not isinstance(event_payloads, list):
        return [], ["events must be an array when provided."]

    if len(event_payloads) == 0:
        return [], ["events must contain at least one item."]

    if len(event_payloads) > MAX_EVENTS_PER_REQUEST:
        return [], [f"events must contain at most {MAX_EVENTS_PER_REQUEST} items."]

    events: list[LLMInferenceEvent] = []
    errors: list[str] = []

    for index, event_payload in enumerate(event_payloads):
        if not isinstance(event_payload, dict):
            errors.append(f"events[{index}] must be an object.")
            continue

        try:
            events.append(LLMInferenceEvent.model_validate(event_payload))
        except ValidationError as exc:
            errors.extend(format_validation_errors(index, exc))

    return events, errors


def format_validation_errors(index: int, exc: ValidationError) -> list[str]:
    messages: list[str] = []
    for error in exc.errors():
        location = ".".join(str(part) for part in error["loc"])
        if location:
            messages.append(f"events[{index}].{location}: {error['msg']}")
        else:
            messages.append(f"events[{index}]: {error['msg']}")

    return messages


def process_event(
    event: LLMInferenceEvent,
    request_metadata: dict[str, str | None],
) -> dict[str, Any]:
    token_usage = event.tokenUsage.model_dump(exclude_none=True) if event.tokenUsage else {}
    metadata = event.metadata or {}
    raw_event = event.model_dump(mode="json", exclude_none=True)

    return {
        "eventId": event.eventId,
        "provider": event.provider,
        "model": event.model,
        "status": event.status,
        "errorType": event.errorType,
        "errorMessage": event.errorMessage,
        "startedAt": normalize_timestamp(event.startedAt),
        "endedAt": normalize_timestamp(event.endedAt),
        "latencyMs": event.latencyMs,
        "sessionId": event.sessionId,
        "conversationId": event.conversationId,
        "requestId": event.requestId,
        "inputPreview": event.inputPreview,
        "outputPreview": event.outputPreview,
        "inputPreviewLength": len(event.inputPreview or ""),
        "outputPreviewLength": len(event.outputPreview or ""),
        "inputTokens": token_usage.get("inputTokens"),
        "outputTokens": token_usage.get("outputTokens"),
        "totalTokens": token_usage.get("totalTokens"),
        "hasError": event.status == "error",
        "metadata": metadata,
        "metadataKeys": sorted(metadata.keys()),
        "rawEvent": raw_event,
        "clientIp": request_metadata["clientIp"],
        "userAgent": request_metadata["userAgent"],
        "receivedAt": request_metadata["receivedAt"],
    }


def normalize_timestamp(value: str) -> str:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)

    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


@router.get("/llm-events")
async def get_llm_events(limit: int = 50) -> dict[str, Any]:
    return {"events": list_llm_events(limit)}


@router.get("/llm-events/{event_id}")
async def get_llm_event_by_id(event_id: str) -> dict[str, Any]:
    event = get_llm_event(event_id)
    if event is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Event not found")

    return {"event": event}