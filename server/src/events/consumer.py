import asyncio
import json
from datetime import datetime, timezone

from pydantic import ValidationError
from events.broker import broker

from models.llm_enference_models import LLMInferenceEvent
from routes.llm_event_routes import process_event
from db.db import insert_llm_event
from core.config import EVENT_CLAIM_MIN_IDLE_MS

_running = False


async def _handle(entry_id: str, fields: dict[str, str]) -> None:
    raw = fields.get("data","")

    try:
        event = LLMInferenceEvent.model_validate(json.loads(raw))
    except (json.JSONDecodeError, ValidationError) as exc:
        await broker.dead_letter(entry_id,raw, f"invalid: {exc}")
        return
    
    request_metadata = {
        "clientIp": None, "userAgent": "stream-consumer",
        "receivedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    processed = process_event(event, request_metadata)
    insert_llm_event(processed)
    await broker.ack(entry_id)

async def _drain_new() ->None :
    entries = await broker.read_group(count=50,block_ms=5000)
    for entry_id , fields in entries:
        await _handle(entry_id,fields)

async def _reclaim_stale() -> None:
    entries = await broker.claim_stale(min_idle_ms=EVENT_CLAIM_MIN_IDLE_MS,count=50)
    for entry_id , fields in entries:
        await _handle(entry_id,fields)

async def run_consumer() -> None:
    global _running
    _running = True
    await broker.ensure_group()
    while _running:
        try:
            await _drain_new()
            await _reclaim_stale()
        except Exception:
            await asyncio.sleep(1)

def close() -> None:
    global _running
    _running = False