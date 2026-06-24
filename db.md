# Database tutorial for this project

This guide explains how to connect your FastAPI backend to a SQL database, save LLM events, read them back, and move the same idea to production.

Your current project already has most of the pieces:

- `server/llm-events.sql` has the SQL table for LLM events.
- `server/src/db/db.py` opens a SQLite connection.
- `server/src/routes/llm_event_routes.py` receives events at `POST /llm-events`.
- `server/src/sdk/llm_event_tracker.py` sends events to the ingestion route.
- `server/src/services/ai.py` already uses `LLMTracker` around the Gemini call.

The missing part is this: the route receives and processes events, but it currently only prints them. To "get the events" later, you must insert them into the database and create a read endpoint.

## 1. The mental model

A database connection has four parts:

1. Schema
   This is the SQL that creates your table. In this project it is `server/llm-events.sql`.

2. Connection
   This is Python code that opens the database. In this project it belongs in `server/src/db/db.py`.

3. Insert
   When an event arrives at `POST /llm-events`, save it into the table.

4. Select
   When you want to display or debug events, query the table with `SELECT`.

Flow:

```text
Gemini request
  -> LLMTracker creates event
  -> POST /llm-events
  -> FastAPI validates event
  -> process_event()
  -> insert into SQLite/Postgres
  -> GET /llm-events reads saved rows
```

## 2. Use SQLite locally

SQLite is the easiest local SQL database because it is just one file. You already have:

```text
server/src/db/mydb.db
```

Recommended local environment:

```env
DATABASE_PATH=src/db/mydb.db
LOG_INGESTION_KEY=dev-secret
LLM_INGESTION_URL=http://127.0.0.1:8000/llm-events
LLM_LOGGING_ENABLED=true
GEMINI_API_KEY=your-gemini-key
GEMINI_MODEL=your-gemini-model
FRONTEND_URL=http://localhost:5173
CORS_ORIGINS=http://localhost:5173
```

Put those values in `server/.env`.

Important: `DATABASE_PATH=src/db/mydb.db` assumes you run commands from the `server` folder. If you run from another folder, use an absolute path or adjust the relative path.

Example absolute Windows path:

```env
DATABASE_PATH=C:\Users\Arpan Mondal\assesment\server\src\db\mydb.db
```

## 3. Create the database table

Your SQL table is in:

```text
server/llm-events.sql
```

That file creates the `llm_inference_events` table and useful indexes.

From the project root:

```powershell
cd server
```

Run this small one-time script:

```powershell
@'
from pathlib import Path
import sqlite3

db_path = Path("src/db/mydb.db")
schema_path = Path("llm-events.sql")

db_path.parent.mkdir(parents=True, exist_ok=True)

conn = sqlite3.connect(db_path)
try:
    conn.executescript(schema_path.read_text(encoding="utf-8"))
    conn.commit()
    print(f"Database ready: {db_path}")
finally:
    conn.close()
'@ | python -
```

This creates the table inside `server/src/db/mydb.db`.

If you get an error saying the schema file is missing, make sure you are inside the `server` folder before running it.

## 4. Make `db.py` responsible for database work

Your current `server/src/db/db.py` only opens a connection. Expand it so the rest of the app can reuse the same database functions.

Use this shape:

```python
import json
import sqlite3
from pathlib import Path
from typing import Any

from core.config import DATABASE_PATH


def get_connection() -> sqlite3.Connection:
    if not DATABASE_PATH:
        raise RuntimeError("DATABASE_PATH is not configured.")

    db_path = Path(DATABASE_PATH)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    schema_path = Path(__file__).resolve().parents[2] / "llm-events.sql"

    with get_connection() as conn:
        conn.executescript(schema_path.read_text(encoding="utf-8"))
        conn.commit()


def insert_llm_event(event: dict[str, Any]) -> None:
    row = {
        "event_id": event.get("eventId"),
        "provider": event.get("provider"),
        "model": event.get("model"),
        "status": event.get("status"),
        "error_type": event.get("errorType"),
        "error_message": event.get("errorMessage"),
        "started_at": event.get("startedAt"),
        "ended_at": event.get("endedAt"),
        "latency_ms": event.get("latencyMs"),
        "session_id": event.get("sessionId"),
        "conversation_id": event.get("conversationId"),
        "request_id": event.get("requestId"),
        "input_preview": event.get("inputPreview"),
        "output_preview": event.get("outputPreview"),
        "input_preview_length": event.get("inputPreviewLength", 0),
        "output_preview_length": event.get("outputPreviewLength", 0),
        "input_tokens": event.get("inputTokens"),
        "output_tokens": event.get("outputTokens"),
        "total_tokens": event.get("totalTokens"),
        "has_error": 1 if event.get("hasError") else 0,
        "metadata_json": json.dumps(event.get("metadata") or {}),
        "metadata_keys_json": json.dumps(event.get("metadataKeys") or []),
        "raw_event_json": json.dumps(event.get("rawEvent") or {}),
        "client_ip": event.get("clientIp"),
        "user_agent": event.get("userAgent"),
        "received_at": event.get("receivedAt"),
    }

    columns = ", ".join(row.keys())
    placeholders = ", ".join(f":{key}" for key in row.keys())

    sql = f"""
        INSERT OR IGNORE INTO llm_inference_events ({columns})
        VALUES ({placeholders})
    """

    with get_connection() as conn:
        conn.execute(sql, row)
        conn.commit()


def list_llm_events(limit: int = 50) -> list[dict[str, Any]]:
    safe_limit = min(max(limit, 1), 200)

    sql = """
        SELECT
            event_id,
            provider,
            model,
            status,
            error_type,
            error_message,
            started_at,
            ended_at,
            latency_ms,
            session_id,
            conversation_id,
            request_id,
            input_preview,
            output_preview,
            input_tokens,
            output_tokens,
            total_tokens,
            has_error,
            metadata_json,
            received_at
        FROM llm_inference_events
        ORDER BY received_at DESC
        LIMIT ?
    """

    with get_connection() as conn:
        rows = conn.execute(sql, (safe_limit,)).fetchall()

    return [dict(row) for row in rows]


def get_llm_event(event_id: str) -> dict[str, Any] | None:
    sql = """
        SELECT *
        FROM llm_inference_events
        WHERE event_id = ?
    """

    with get_connection() as conn:
        row = conn.execute(sql, (event_id,)).fetchone()

    return dict(row) if row else None
```

Why this works:

- `get_connection()` opens the DB file from `DATABASE_PATH`.
- `init_db()` runs your `llm-events.sql` schema.
- `insert_llm_event()` saves one processed event.
- `list_llm_events()` returns recent events.
- `get_llm_event()` returns one event by ID.

## 5. Initialize the table when the app starts

In `server/src/server.py`, import `init_db`:

```python
from db.db import init_db
```

Then add a startup handler:

```python
@app.on_event("startup")
async def startup() -> None:
    init_db()
```

This makes sure the table exists every time the backend starts.

FastAPI also supports the newer lifespan API, but `@app.on_event("startup")` is simple and fine while learning.

## 6. Save events inside the existing route

In `server/src/routes/llm_event_routes.py`, import the insert and read helpers:

```python
from db.db import get_llm_event, insert_llm_event, list_llm_events
```

Find this part:

```python
for event in processed_events:
    print("LLM event received", json.dumps(event, ensure_ascii=False))
```

Change it to:

```python
for event in processed_events:
    insert_llm_event(event)
    print("LLM event saved", json.dumps(event, ensure_ascii=False))
```

Now every accepted event is stored in SQLite.

## 7. Add endpoints to get events

In the same file, `server/src/routes/llm_event_routes.py`, add these routes near the bottom:

```python
@router.get("/llm-events")
async def get_llm_events(limit: int = 50) -> dict[str, Any]:
    return {"events": list_llm_events(limit)}


@router.get("/llm-events/{event_id}")
async def get_llm_event_by_id(event_id: str) -> dict[str, Any]:
    event = get_llm_event(event_id)
    if event is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Event not found")

    return {"event": event}
```

Now you have:

```text
POST /llm-events        saves events
GET  /llm-events        lists recent events
GET  /llm-events/{id}   gets one event
```

For local development this is fine. Before production, protect these `GET` routes with admin authentication because saved events can contain prompt previews, output previews, error messages, IP addresses, and user agents.

## 8. Run the backend locally

From the `server` folder:

```powershell
cd server
$env:PYTHONPATH = "src"
poetry run uvicorn server:app --reload
```

If you are not using Poetry:

```powershell
cd server
$env:PYTHONPATH = "src"
python -m uvicorn server:app --reload
```

The backend should be available at:

```text
http://127.0.0.1:8000
```

Check health:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
```

## 9. Test event insert manually

Use this PowerShell request:

```powershell
$body = @{
  eventId = "evt_test_1"
  provider = "gemini"
  model = "gemini-test"
  status = "success"
  startedAt = "2026-06-19T10:00:00Z"
  endedAt = "2026-06-19T10:00:01Z"
  latencyMs = 1000
  inputPreview = "hello"
  outputPreview = "hi there"
  tokenUsage = @{
    inputTokens = 5
    outputTokens = 4
    totalTokens = 9
  }
  metadata = @{
    route = "/manual-test"
  }
} | ConvertTo-Json -Depth 5

Invoke-RestMethod `
  -Method Post `
  -Uri http://127.0.0.1:8000/llm-events `
  -Headers @{ Authorization = "Bearer dev-secret" } `
  -ContentType "application/json" `
  -Body $body
```

Expected response:

```json
{
  "ok": true,
  "accepted": 1
}
```

Then read it back:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/llm-events?limit=10
```

Get one event:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/llm-events/evt_test_1
```

## 10. Make automatic event logging work

Your `server/src/services/ai.py` already creates:

```python
tracker = LLMTracker(
    provider="gemini",
    model=GEMINI_MODEL or "gemini",
    ingestion_url=LLM_INGESTION_URL,
    api_key=LOG_INGESTION_KEY,
    enabled=LLM_LOGGING_ENABLED,
)
```

For automatic logging, these env vars must match:

```env
LLM_INGESTION_URL=http://127.0.0.1:8000/llm-events
LOG_INGESTION_KEY=dev-secret
LLM_LOGGING_ENABLED=true
```

When `/v1/api/chat` calls Gemini, the tracker sends an event to `/llm-events`. After you add the insert code, that event will be saved in the database.

## 11. Get events from the frontend

Your frontend uses `VITE_API_URL` as the backend URL.

In `chatui/.env`:

```env
VITE_API_URL=http://127.0.0.1:8000
```

Simple fetch example:

```ts
const response = await fetch(`${import.meta.env.VITE_API_URL}/llm-events?limit=50`);
const data = await response.json();
console.log(data.events);
```

In a real UI, put this in an API file like `chatui/src/api/events.api.ts`.

Example:

```ts
export type LLMEvent = {
  event_id: string;
  provider: string;
  model: string;
  status: "success" | "error";
  latency_ms: number;
  input_preview: string | null;
  output_preview: string | null;
  received_at: string;
};

export async function getLLMEvents(limit = 50): Promise<LLMEvent[]> {
  const response = await fetch(
    `${import.meta.env.VITE_API_URL}/llm-events?limit=${limit}`,
  );

  if (!response.ok) {
    throw new Error("Failed to load events");
  }

  const data = await response.json();
  return data.events;
}
```

## 12. Local SQLite vs production database

SQLite is good for:

- local development
- learning SQL
- small single-server apps
- simple internal tools

SQLite is not ideal for:

- multiple backend containers
- serverless deployments with temporary files
- many concurrent writes
- teams needing managed backups and failover

For production, usually use hosted Postgres.

Good hosted Postgres options:

- Supabase
- Neon
- Railway Postgres
- Render Postgres
- AWS RDS
- Google Cloud SQL
- Azure Database for PostgreSQL

## 13. Production option A: SQLite on a VPS

You can use SQLite in production only if:

- your app runs on one server
- the database file is on persistent disk
- you make backups
- you do not run many write-heavy workers at the same time

Example production env:

```env
DATABASE_PATH=/var/app/data/mydb.db
LOG_INGESTION_KEY=use-a-long-random-secret
LLM_INGESTION_URL=https://your-api-domain.com/llm-events
LLM_LOGGING_ENABLED=true
CORS_ORIGINS=https://your-frontend-domain.com
```

If using Docker, mount a volume:

```text
/var/app/data on host -> /app/data in container
```

Then:

```env
DATABASE_PATH=/app/data/mydb.db
```

Do not store the SQLite file inside a temporary container filesystem.

## 14. Production option B: Postgres

Postgres is the better production default.

Instead of:

```env
DATABASE_PATH=src/db/mydb.db
```

Use:

```env
DATABASE_URL=postgresql+psycopg://USER:PASSWORD@HOST:5432/DB_NAME
```

You would also install:

```powershell
poetry add sqlalchemy psycopg alembic
```

Recommended production stack:

- SQLAlchemy for database connections and queries
- Alembic for schema migrations
- Postgres as the hosted database
- `DATABASE_URL` stored as an environment variable

The production flow becomes:

```text
FastAPI route
  -> SQLAlchemy session
  -> insert event
  -> hosted Postgres
```

With SQLAlchemy, your code does not need to care much whether the database is local or hosted. You change the connection URL.

## 15. Example Postgres table

Your current SQLite schema mostly works as normal SQL, but Postgres has better native types.

Postgres-style schema:

```sql
CREATE TABLE IF NOT EXISTS llm_inference_events (
  event_id TEXT PRIMARY KEY,
  provider TEXT NOT NULL,
  model TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('success', 'error')),
  error_type TEXT,
  error_message TEXT,
  started_at TIMESTAMPTZ NOT NULL,
  ended_at TIMESTAMPTZ NOT NULL,
  latency_ms INTEGER NOT NULL,
  session_id TEXT,
  conversation_id TEXT,
  request_id TEXT,
  input_preview TEXT,
  output_preview TEXT,
  input_preview_length INTEGER NOT NULL DEFAULT 0,
  output_preview_length INTEGER NOT NULL DEFAULT 0,
  input_tokens INTEGER,
  output_tokens INTEGER,
  total_tokens INTEGER,
  has_error BOOLEAN NOT NULL,
  metadata_json JSONB NOT NULL,
  metadata_keys_json JSONB NOT NULL,
  raw_event_json JSONB NOT NULL,
  client_ip TEXT,
  user_agent TEXT,
  received_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS llm_events_started_at_idx
  ON llm_inference_events (started_at);

CREATE INDEX IF NOT EXISTS llm_events_session_id_idx
  ON llm_inference_events (session_id);

CREATE INDEX IF NOT EXISTS llm_events_conversation_id_idx
  ON llm_inference_events (conversation_id);

CREATE INDEX IF NOT EXISTS llm_events_provider_model_idx
  ON llm_inference_events (provider, model);

CREATE INDEX IF NOT EXISTS llm_events_status_idx
  ON llm_inference_events (status);
```

Main differences:

- `TIMESTAMPTZ` instead of timestamp strings.
- `BOOLEAN` instead of `0` or `1`.
- `JSONB` instead of JSON stored as text.

## 16. Environment variables are the key to "connect anywhere"

Do not hardcode database paths or secrets in code.

Use environment variables:

Local SQLite:

```env
DATABASE_PATH=src/db/mydb.db
```

Local Postgres:

```env
DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:5432/my_app
```

Production Postgres:

```env
DATABASE_URL=postgresql+psycopg://USER:PASSWORD@HOST:5432/DB_NAME
```

Production SQLite:

```env
DATABASE_PATH=/app/data/mydb.db
```

The app should read the env var and open the correct database.

## 17. What not to commit

Do not commit these to Git:

```text
.env
*.db
*.sqlite
```

Commit these:

```text
llm-events.sql
db.py
route code
migration files
documentation
```

The schema belongs in Git. The real database data usually does not.

## 18. Common errors

### Existing script says `schema.sql` is missing

Your repo has `server/llm-events.sql`, not `server/schema.sql`.

If you use `server/scripts/create_sqlite_db.py`, update this:

```python
SCHEMA_PATH = ROOT / "schema.sql"
```

to this:

```python
SCHEMA_PATH = ROOT / "llm-events.sql"
```

The tutorial script in step 3 already uses the correct file.

### `DATABASE_PATH is not configured`

Your `.env` is missing:

```env
DATABASE_PATH=src/db/mydb.db
```

### `no such table: llm_inference_events`

The DB file exists, but the table was never created.

Fix by running the schema from step 3, or by adding `init_db()` at app startup.

### Events are accepted but not saved

Your route still only prints events.

Make sure this line exists:

```python
insert_llm_event(event)
```

inside the `for event in processed_events` loop.

### `Unauthorized`

The request header does not match `LOG_INGESTION_KEY`.

If `.env` has:

```env
LOG_INGESTION_KEY=dev-secret
```

Then the request must include:

```text
Authorization: Bearer dev-secret
```

### Wrong database file is being used

This usually happens because `DATABASE_PATH` is relative to a different current working directory.

Use an absolute path while debugging:

```env
DATABASE_PATH=C:\Users\Arpan Mondal\assesment\server\src\db\mydb.db
```

### SQLite database is locked

This can happen with too many concurrent writes or long-running transactions.

Fixes:

- keep transactions short
- open a connection only when needed
- commit quickly
- use Postgres if the app becomes write-heavy

## 19. Recommended path for you

For this project, do this first:

1. Use SQLite locally.
2. Set `DATABASE_PATH=src/db/mydb.db` in `server/.env`.
3. Run the schema from step 3.
4. Add `insert_llm_event()` and `list_llm_events()` to `server/src/db/db.py`.
5. Call `insert_llm_event(event)` inside `POST /llm-events`.
6. Add `GET /llm-events`.
7. Test with PowerShell.
8. Later, move to hosted Postgres with `DATABASE_URL`.

That gives you the full loop:

```text
send chat message -> Gemini runs -> tracker sends event -> backend saves event -> frontend/admin can read events
```
