from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from core.config import CORS_ORIGINS, LOG_INGESTION_KEY, MAX_EVENTS_PER_REQUEST
from routes import llm_event_routes, run_ai_routes
from db.db import init_db

app = FastAPI()

app.include_router(run_ai_routes.router)
app.include_router(llm_event_routes.router)

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