from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from core.config import CORS_ORIGINS, LOG_INGESTION_KEY, MAX_EVENTS_PER_REQUEST
from routes import llm_event_routes, run_ai_routes , dashboard_routes
from db.db import init_db
import asyncio
from events.broker import broker
from events.consumer import run_consumer, close
from core.config import EVENT_BROKER_ENABLED


app = FastAPI()

app.include_router(run_ai_routes.router)
app.include_router(llm_event_routes.router)
app.include_router(dashboard_routes.router)

_consumer_task = None

@app.on_event("startup")
async def startup() -> None:
    init_db()
    if EVENT_BROKER_ENABLED:
        await broker.ensure_group()
        global _consumer_task
        _consumer_task = asyncio.create_task(run_consumer())

@app.on_event("shutdown")
async def shutdown() -> None:
    close()
    if _consumer_task:
        _consumer_task.cancel()
    await broker.close()

@app.on_event("startup")
async def startup() -> None:
    init_db()

@app.get("/")
async def health():
    return {"Status": "200"}


@app.get("/health")
async def app_health():
    return {
        "ok": True,
        "llmEvents": {
            "route": "/llm-events",
            "authConfigured": bool(LOG_INGESTION_KEY),
            "maxEventsPerRequest": MAX_EVENTS_PER_REQUEST,
        },
    }

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)