import json
from typing import Any
from datetime import datetime, timedelta, timezone
from core.config import (DATABASE_PATH)
import sqlite3
from pathlib import Path
def get_connection():
    if not DATABASE_PATH:
        raise RuntimeError("DATABASE_PATH is not configured.")
    
    db_path = Path(DATABASE_PATH)
    db_path.parent.mkdir(parents=True,exist_ok=True)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn

def init_db() -> None:
    schema_path = Path(__file__).resolve().parents[2] / "llm-events.sql"

    with get_connection() as conn:        
        _run_migrations(conn)
        conn.executescript(schema_path.read_text(encoding="utf-8"))
        conn.commit()
        

def _run_migrations(conn: sqlite3.Connection) -> None:
    # Fresh DB — table doesn't exist yet, executescript will create it correctly
    table_exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='conversations'"
    ).fetchone()

    if not table_exists:
        return

    existing_cols = {
        row[1]
        for row in conn.execute("PRAGMA table_info(conversations)").fetchall()
    }

    if "session_id" not in existing_cols:
        conn.execute(
            "ALTER TABLE conversations ADD COLUMN session_id VARCHAR(200) NOT NULL DEFAULT ''"
        )
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

def upsert_conversation(
    conversation_id: str,
    title: str,
    provider: str,
    model: str,
    created_at: str,
    updated_at: str,
) -> None:
    sql = """
        INSERT INTO conversations (conversation_id, title, provider, model, created_at, updated_at)
        VALUES (:conversation_id, :title, :provider, :model, :created_at, :updated_at)
        ON CONFLICT(conversation_id) DO UPDATE SET
            title      = excluded.title,
            provider   = excluded.provider,
            model      = excluded.model,
            updated_at = excluded.updated_at
    """
    with get_connection() as conn:
        conn.execute(sql,{
            "conversation_id": conversation_id,
            "title":title,
            "provider":provider,
            "model":model,
            "created_at": created_at,
            "updated_at": updated_at,
        })
        conn.commit()

def insert_message(
        conversation_id:str,
        role:str,
        content:str,
        created_at:str
) -> None:
    sql = """
        INSERT INTO conversation_messages(conversation_id, role, content, created_at)
        VALUES(:conversation_id, :role, :content, :created_at)
    """
    with get_connection() as conn:
        conn.execute(sql,{
            "conversation_id":conversation_id,
            "role":role,
            "content":content,
            "created_at":created_at
        })
        conn.commit()

def get_message_for_conversations(conversation_id:str) -> list[dict[str,Any]]:
    sql = """
        SELECT role, content, created_at
        FROM conversation_messages
        WHERE conversation_id = ?
        ORDER BY id ASC
    """
    with get_connection() as conn:
        rows = conn.execute(sql,(conversation_id,)).fetchall()
    return [dict(row) for row in rows]

def list_conversations_db() -> list[dict[str, Any]]:
    sql = """
        SELECT
            c.conversation_id,
            c.title,
            c.provider,
            c.model,
            c.created_at,
            c.updated_at,
            COUNT(m.id) AS message_count
        FROM conversations c
        LEFT JOIN conversation_messages m ON c.conversation_id = m.conversation_id
        GROUP BY c.conversation_id
        ORDER BY c.updated_at DESC
    """
    with get_connection() as conn:
        rows = conn.execute(sql).fetchall()
    return [dict(row) for row in rows]


def get_conversation_db(conversation_id: str) -> dict[str, Any] | None:
    sql = """
        SELECT conversation_id, title, provider, model, created_at, updated_at
        FROM conversations
        WHERE conversation_id = ?
    """
    with get_connection() as conn:
        row = conn.execute(sql, (conversation_id,)).fetchone()
    return dict(row) if row else None


def delete_conversation_db(conversation_id: str) -> bool:
    with get_connection() as conn:
        exists = conn.execute(
            "SELECT 1 FROM conversations WHERE conversation_id = ?",
            (conversation_id,),
        ).fetchone()

        if not exists:
            return False

        # Delete messages first (no cascade enforcement in SQLite by default)
        conn.execute(
            "DELETE FROM conversation_messages WHERE conversation_id = ?",
            (conversation_id,),
        )
        conn.execute(
            "DELETE FROM conversations WHERE conversation_id = ?",
            (conversation_id,),
        )
        conn.commit()

    return True

_BUCKET_LEN = {"day": 10, "hour": 13, "minute": 16}


def drop_iso(since_hours: int | None) -> str | None:
    """Return an ISO-8601 'Z' cutoff for 'last N hours', or None for all-time."""
    if not since_hours or since_hours <= 0:
        return None
    cutoff = datetime.now(timezone.utc) - timedelta(hours=since_hours)
    return cutoff.isoformat().replace("+00:00", "Z")


def _time_clause(since_iso: str | None) -> tuple[str, list[Any]]:
    """Build a WHERE fragment + params for an optional time window."""
    if since_iso:
        return "WHERE started_at >= ?", [since_iso]
    return "", []

def metrics_overview(since_iso: str | None) -> dict[str , Any]:
    """Single-row totals used by the summary endpoint."""
    where , params = _time_clause(since_iso=since_iso)
    sql = f"""
        SELECT
            COUNT(*)                                            AS total,
            SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) AS success_count,
            SUM(has_error)                                      AS error_count,
            AVG(latency_ms)                                     AS avg_latency_ms,
            MIN(latency_ms)                                     AS min_latency_ms,
            MAX(latency_ms)                                     AS max_latency_ms,
            SUM(COALESCE(total_tokens,0))                       AS total_tokens
        FROM llm_inference_events
        {where}
    """
    with get_connection() as conn:
        row = conn.execute(sql,params).fetchone()
    return dict(row)

def fetch_latencies(since_iso:str | None) -> list[int]:
    """All latency values in the window (for Python percentile computation)."""
    where , params = _time_clause(since_iso=since_iso)
    sql = f""" SELECT latency_ms FROM llm_inference_events {where}"""
    with get_connection() as conn:
        rows = conn.execute(sql,params).fetchall()
    return [r[0] for r in rows]

def latency_rows(since_iso:str | None) -> list[dict[str , Any]]:
    """(provider, model, latency_ms) rows — grouped/percentiled in Python."""
    where , params = _time_clause(since_iso=since_iso)
    sql = F"""
        SELECT
        provider , model , latency_ms
        FROM llm_inference_events
        {where}
    """
    with get_connection() as conn:
        rows = conn.execute(sql,params).fetchall()

    return [dict(r) for r in rows]

def throughput_rows(since_iso: str | None, bucket: str) -> list[dict[str, Any]]:
    """Request counts per time bucket, with an error count per bucket."""
    length = _BUCKET_LEN.get(bucket,13)
    where, params = _time_clause(since_iso)
    sql = f"""
        SELECT
            substr(started_at, 1, {length}) AS bucket,
            COUNT(*)                        AS total,
            SUM(has_error)                  AS errors
        FROM llm_inference_events
        {where}
        GROUP BY bucket
        ORDER BY bucket ASC
    """
    with get_connection() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]

def error_breakdown(since_iso: str | None) -> list[dict[str, Any]]:
    """Count of errors grouped by provider/model/error_type."""
    clause = "WHERE has_error = 1"
    params: list[Any] = []
    if since_iso:
        clause += " AND started_at >= ?"
        params.append(since_iso)
    sql = f"""
        SELECT provider, model, error_type, COUNT(*) AS count
        FROM llm_inference_events
        {clause}
        GROUP BY provider, model, error_type
        ORDER BY count DESC
    """
    with get_connection() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]