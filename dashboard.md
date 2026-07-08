# Dashboards — Latency, Throughput & Errors (Full Build Guide)

This document answers, end to end:

1. **What routes** you need to add.
2. **Where the data comes from** (spoiler: it's already in your database — no schema change).
3. **Whether you need DB functions + SQL** (yes — this file gives you every one, ready to paste).
4. **Exactly what code changes** to make, file by file.

The whole feature is **3 new pieces of code**:

- new query functions in `server/src/db/db.py`
- a new route file `server/src/routes/metrics_routes.py`
- one line in `server/src/server.py` to register it

Plus an optional frontend page. **Zero database/schema changes.**

---

## Part 1 — Where does the data come from?

You already log everything. Every LLM call writes one row into `llm_inference_events`
(via `POST /llm-events` → `insert_llm_event`). Look at the columns you already have:

| Column | Type | Powers which dashboard |
|---|---|---|
| `latency_ms` | INT | **Latency** (avg, min, max, p50/p95/p99) |
| `started_at` | ISO string (UTC `Z`) | **Throughput** & **Errors** (time buckets) |
| `status` (`success`/`error`) | VARCHAR | **Errors** (success vs error) |
| `has_error` | INT (0/1) | **Errors** (fast error counting) |
| `error_type` | VARCHAR | **Errors** (breakdown by error kind) |
| `provider`, `model` | VARCHAR | grouping for all three |
| `total_tokens` / `input_tokens` / `output_tokens` | INT | cost/usage stats |

So the dashboards are **pure read/aggregation over data you already collect**. Nothing new
needs to be captured. You just need SQL that *summarizes* these rows.

> **Key insight:** dashboards = `SELECT ... GROUP BY ...` over `llm_inference_events`. That's it.

Two important facts about your data that shape the queries:

1. **Timestamps are ISO-8601 UTC strings**, e.g. `2026-07-05T11:23:45.123Z`, all in the same
   normalized format (see `normalize_timestamp` in `llm_event_routes.py`). Because they're
   fixed-width and zero-padded, **string comparison equals time comparison** — so
   `WHERE started_at >= '2026-07-04T11:00:00Z'` correctly means "last 24h", and
   `substr(started_at, 1, 13)` neatly slices out `2026-07-05T11` = an **hourly bucket** with no
   date parsing needed.
2. **SQLite has no `PERCENTILE_CONT` function.** So p95/p99 can't be done in pure SQL. We fetch
   the `latency_ms` values and compute percentiles in Python (a tiny helper). At assessment
   scale this is fast and correct; I note the scaling caveat at the end.

---

## Part 2 — What routes do you need?

Four endpoints under the public API prefix `/v1/api/metrics/*` (they're called by the browser,
so they belong with the public API — see the earlier routing discussion):

| Method & Path | Purpose | Feeds UI panel |
|---|---|---|
| `GET /v1/api/metrics/summary` | Top-line KPI numbers (totals, error rate, avg/p95 latency, tokens) | The stat tiles at the top |
| `GET /v1/api/metrics/latency` | Per provider+model: count, avg, min, max, p50/p95/p99 | **Latency** table/chart |
| `GET /v1/api/metrics/throughput` | Requests per time bucket (with success/error split) | **Throughput** line chart |
| `GET /v1/api/metrics/errors` | Error rate over time + breakdown by `error_type` | **Errors** chart + table |

All four accept `?since_hours=24` (time window; `0` = all time). `throughput` and `errors` also
accept `?bucket=hour` (`minute` / `hour` / `day`).

### Example responses (so you know what the frontend receives)

`GET /v1/api/metrics/summary?since_hours=24`
```json
{
  "sinceHours": 24,
  "totalRequests": 128,
  "successCount": 121,
  "errorCount": 7,
  "errorRate": 0.0547,
  "avgLatencyMs": 842,
  "p50LatencyMs": 610,
  "p95LatencyMs": 2100,
  "p99LatencyMs": 3400,
  "minLatencyMs": 180,
  "maxLatencyMs": 4200,
  "totalTokens": 54213
}
```

`GET /v1/api/metrics/latency?since_hours=24`
```json
{
  "sinceHours": 24,
  "groups": [
    {"provider": "gemini", "model": "gemini-2.0-flash", "count": 90,
     "avgLatencyMs": 700, "minLatencyMs": 180, "maxLatencyMs": 3000,
     "p50LatencyMs": 610, "p95LatencyMs": 1900, "p99LatencyMs": 2800},
    {"provider": "openai", "model": "gpt-4o-mini", "count": 38, "...": "..."}
  ]
}
```

`GET /v1/api/metrics/throughput?since_hours=24&bucket=hour`
```json
{
  "sinceHours": 24, "bucket": "hour",
  "series": [
    {"bucket": "2026-07-05T09", "total": 12, "success": 12, "errors": 0},
    {"bucket": "2026-07-05T10", "total": 20, "success": 18, "errors": 2}
  ]
}
```

`GET /v1/api/metrics/errors?since_hours=24&bucket=hour`
```json
{
  "sinceHours": 24, "bucket": "hour",
  "series": [
    {"bucket": "2026-07-05T10", "total": 20, "errors": 2, "errorRate": 0.10}
  ],
  "breakdown": [
    {"provider": "openai", "model": "gpt-4o-mini", "errorType": "TimeoutError", "count": 4},
    {"provider": "gemini", "model": "gemini-2.0-flash", "errorType": "RateLimitError", "count": 3}
  ]
}
```

---

## Part 3 — The DB functions (SQL you paste into `db.py`)

**Yes, you write functions that run SQL queries** — exactly following the pattern already in
your `db.py` (`get_connection()`, `conn.execute(sql, params).fetchall()`, `dict(row)`).

Add these at the bottom of `server/src/db/db.py`. Also add the datetime import at the top.

**Top of `db.py` — add this import** (you already import `json`, `sqlite3`, `Path`):

```python
from datetime import datetime, timedelta, timezone
```

**Bottom of `db.py` — add the metrics functions:**

```python
# ----------------------------------------------------------------------------
# Metrics / dashboard queries (read-only aggregation over llm_inference_events)
# ----------------------------------------------------------------------------

# Time buckets are produced by slicing the fixed-width ISO timestamp:
#   substr(started_at, 1, 10) -> "2026-07-05"        (day)
#   substr(started_at, 1, 13) -> "2026-07-05T11"     (hour)
#   substr(started_at, 1, 16) -> "2026-07-05T11:23"  (minute)
_BUCKET_LEN = {"day": 10, "hour": 13, "minute": 16}


def cutoff_iso(since_hours: int | None) -> str | None:
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


def metrics_overview(since_iso: str | None) -> dict[str, Any]:
    """Single-row totals used by the summary endpoint."""
    where, params = _time_clause(since_iso)
    sql = f"""
        SELECT
            COUNT(*)                                            AS total,
            SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) AS success_count,
            SUM(has_error)                                      AS error_count,
            AVG(latency_ms)                                     AS avg_latency_ms,
            MIN(latency_ms)                                     AS min_latency_ms,
            MAX(latency_ms)                                     AS max_latency_ms,
            SUM(COALESCE(total_tokens, 0))                      AS total_tokens
        FROM llm_inference_events
        {where}
    """
    with get_connection() as conn:
        row = conn.execute(sql, params).fetchone()
    return dict(row)


def fetch_latencies(since_iso: str | None) -> list[int]:
    """All latency values in the window (for Python percentile computation)."""
    where, params = _time_clause(since_iso)
    sql = f"SELECT latency_ms FROM llm_inference_events {where}"
    with get_connection() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [r[0] for r in rows]


def latency_rows(since_iso: str | None) -> list[dict[str, Any]]:
    """(provider, model, latency_ms) rows — grouped/percentiled in Python."""
    where, params = _time_clause(since_iso)
    sql = f"""
        SELECT provider, model, latency_ms
        FROM llm_inference_events
        {where}
    """
    with get_connection() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def throughput_rows(since_iso: str | None, bucket: str) -> list[dict[str, Any]]:
    """Request counts per time bucket, with an error count per bucket."""
    length = _BUCKET_LEN.get(bucket, 13)          # int we control -> safe to inline
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
```

**Why these are safe & correct:**

- The only value ever inlined into an f-string is `{length}`, an integer chosen from the fixed
  `_BUCKET_LEN` map — never user input. Every real value goes through `?` parameters, so there's
  **no SQL injection risk**.
- All the time-windowed queries hit the existing `llm_events_started_at_idx` and
  `llm_events_status_idx` indexes, so they stay fast as rows grow.
- `COALESCE(total_tokens, 0)` handles the streaming rows where token usage is NULL.
- `Any` is already imported at the top of your `db.py` (`from typing import Any`).

---

## Part 4 — The route file (`metrics_routes.py`)

Create `server/src/routes/metrics_routes.py`. This layer calls the DB functions, computes
percentiles in Python (SQLite can't), and shapes the JSON the frontend consumes.

```python
# server/src/routes/metrics_routes.py
import math
from typing import Any

from fastapi import APIRouter, Query

from db.db import (
    cutoff_iso,
    metrics_overview,
    fetch_latencies,
    latency_rows,
    throughput_rows,
    error_breakdown,
)

router = APIRouter(prefix="/v1/api/metrics")


def _percentile(values: list[int], pct: float) -> float | None:
    """Linear-interpolation percentile. pct in 0..100. None if no data."""
    if not values:
        return None
    s = sorted(values)
    if len(s) == 1:
        return float(s[0])
    k = (len(s) - 1) * pct / 100.0
    low, high = math.floor(k), math.ceil(k)
    if low == high:
        return float(s[int(k)])
    return round(s[low] + (s[high] - s[low]) * (k - low), 1)


@router.get("/summary")
async def summary(since_hours: int = Query(24, ge=0)) -> dict[str, Any]:
    since = cutoff_iso(since_hours)
    ov = metrics_overview(since)
    latencies = fetch_latencies(since)

    total = ov["total"] or 0
    errors = ov["error_count"] or 0
    avg = ov["avg_latency_ms"]

    return {
        "sinceHours": since_hours,
        "totalRequests": total,
        "successCount": ov["success_count"] or 0,
        "errorCount": errors,
        "errorRate": round(errors / total, 4) if total else 0,
        "avgLatencyMs": round(avg) if avg is not None else None,
        "minLatencyMs": ov["min_latency_ms"],
        "maxLatencyMs": ov["max_latency_ms"],
        "p50LatencyMs": _percentile(latencies, 50),
        "p95LatencyMs": _percentile(latencies, 95),
        "p99LatencyMs": _percentile(latencies, 99),
        "totalTokens": ov["total_tokens"] or 0,
    }


@router.get("/latency")
async def latency(since_hours: int = Query(24, ge=0)) -> dict[str, Any]:
    since = cutoff_iso(since_hours)
    groups: dict[tuple[str, str], list[int]] = {}
    for r in latency_rows(since):
        groups.setdefault((r["provider"], r["model"]), []).append(r["latency_ms"])

    result = [
        {
            "provider": provider,
            "model": model,
            "count": len(vals),
            "avgLatencyMs": round(sum(vals) / len(vals)),
            "minLatencyMs": min(vals),
            "maxLatencyMs": max(vals),
            "p50LatencyMs": _percentile(vals, 50),
            "p95LatencyMs": _percentile(vals, 95),
            "p99LatencyMs": _percentile(vals, 99),
        }
        for (provider, model), vals in groups.items()
    ]
    result.sort(key=lambda g: g["count"], reverse=True)
    return {"sinceHours": since_hours, "groups": result}


@router.get("/throughput")
async def throughput(
    since_hours: int = Query(24, ge=0),
    bucket: str = Query("hour", pattern="^(minute|hour|day)$"),
) -> dict[str, Any]:
    since = cutoff_iso(since_hours)
    series = [
        {
            "bucket": r["bucket"],
            "total": r["total"],
            "errors": r["errors"] or 0,
            "success": r["total"] - (r["errors"] or 0),
        }
        for r in throughput_rows(since, bucket)
    ]
    return {"sinceHours": since_hours, "bucket": bucket, "series": series}


@router.get("/errors")
async def errors(
    since_hours: int = Query(24, ge=0),
    bucket: str = Query("hour", pattern="^(minute|hour|day)$"),
) -> dict[str, Any]:
    since = cutoff_iso(since_hours)
    series = [
        {
            "bucket": r["bucket"],
            "total": r["total"],
            "errors": r["errors"] or 0,
            "errorRate": round((r["errors"] or 0) / r["total"], 4) if r["total"] else 0,
        }
        for r in throughput_rows(since, bucket)
    ]
    return {
        "sinceHours": since_hours,
        "bucket": bucket,
        "series": series,
        "breakdown": [
            {
                "provider": b["provider"],
                "model": b["model"],
                "errorType": b["error_type"],
                "count": b["count"],
            }
            for b in error_breakdown(since)
        ],
    }
```

Notes:
- `router = APIRouter(prefix="/v1/api/metrics")` means each path (`/summary`, `/latency`, …)
  becomes `/v1/api/metrics/summary`, etc. — no repetition.
- `bucket` uses a regex `pattern` so FastAPI rejects anything but `minute`/`hour`/`day` with a
  422 automatically. Defense in depth on top of the `_BUCKET_LEN.get(...)` default.
- `since_hours` uses `ge=0` so negatives are rejected; `0` means "all time".

---

## Part 5 — Register the router (`server.py`)

One import + one line. In `server/src/server.py`:

```python
from routes import llm_event_routes, run_ai_routes, metrics_routes   # add metrics_routes
```
```python
app.include_router(run_ai_routes.router)
app.include_router(llm_event_routes.router)
app.include_router(metrics_routes.router)     # <-- add this line
```

That's the entire backend. **No schema change, no migration, no new table.**

---

## Part 6 — Try it before touching the frontend

Run the backend (`docker compose up --build`, or locally), send a few chat messages so events
get logged, then hit the endpoints. Through Docker (nginx proxies `/api/` and strips it):

```bash
curl "http://localhost:8080/api/v1/api/metrics/summary?since_hours=24"
curl "http://localhost:8080/api/v1/api/metrics/latency?since_hours=24"
curl "http://localhost:8080/api/v1/api/metrics/throughput?since_hours=24&bucket=hour"
curl "http://localhost:8080/api/v1/api/metrics/errors?since_hours=24&bucket=hour"
```

Running the backend directly (port 8000, no nginx):

```bash
curl "http://localhost:8000/v1/api/metrics/summary?since_hours=0"
```

You should get JSON matching the shapes in Part 2. If `summary` shows `totalRequests: 0`, either
you haven't sent any chats yet, or logging is off (check `LLM_LOGGING_ENABLED` / `LLM_INGESTION_URL`).

---

## Part 7 — The frontend dashboard (optional but expected)

The data is now available; you need a page to show it. Minimal plan for your React + Vite app:

1. **Install a chart library:**
   ```bash
   cd chatui
   pnpm add recharts
   ```
2. **Add an API module** `chatui/src/api/metrics.api.ts` that calls the four endpoints through
   your existing `axiosInstance` (its `baseURL` is `/api` in Docker, so the browser call
   `/v1/api/metrics/summary` becomes `/api/v1/api/metrics/summary` → nginx → backend):
   ```ts
   import axiosInstance from "./axios.config";

   export const getSummary = (h = 24) =>
     axiosInstance.get(`/v1/api/metrics/summary?since_hours=${h}`).then(r => r.data);
   export const getLatency = (h = 24) =>
     axiosInstance.get(`/v1/api/metrics/latency?since_hours=${h}`).then(r => r.data);
   export const getThroughput = (h = 24, b = "hour") =>
     axiosInstance.get(`/v1/api/metrics/throughput?since_hours=${h}&bucket=${b}`).then(r => r.data);
   export const getErrors = (h = 24, b = "hour") =>
     axiosInstance.get(`/v1/api/metrics/errors?since_hours=${h}&bucket=${b}`).then(r => r.data);
   ```
3. **Add a `Dashboard.tsx` page** with:
   - A **KPI row** (stat tiles) from `getSummary` — total requests, error rate %, avg + p95 latency, total tokens.
   - A **Throughput** `LineChart` (`series` → x=`bucket`, lines for `success` and `errors`).
   - An **Errors** `LineChart`/`BarChart` (x=`bucket`, y=`errorRate`) + a small table from `breakdown`.
   - A **Latency** table or `BarChart` from `getLatency().groups` (bars for p50/p95/p99 per model).
4. **Add routing** — a nav link/route to `/dashboard`. Since nginx already does SPA fallback
   (`try_files ... /index.html`), client-side routing to a new page needs no server change.

> Tip: if you use charts, skim the `dataviz` skill before picking colors/layout so the three
> panels read as one consistent system.

---

## Part 8 — Recap & the one caveat

**What you build:**
1. `db.py` — 6 read functions (`cutoff_iso`, `metrics_overview`, `fetch_latencies`,
   `latency_rows`, `throughput_rows`, `error_breakdown`) + one import.
2. `routes/metrics_routes.py` — 4 endpoints + a `_percentile` helper.
3. `server.py` — one `include_router` line.
4. Frontend — `recharts`, an api module, a `Dashboard` page, a route.

**What you do NOT touch:** the database schema, the events table, the ingestion path, the
tracker. All data already exists.

**The one caveat (worth writing in your assessment notes):** percentiles are computed in Python
by fetching `latency_ms` values, because SQLite lacks `PERCENTILE_CONT`. That's fine at this
scale, but for very large event volumes you'd either (a) move to Postgres and use
`percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms)` directly in SQL, or (b) pre-aggregate
metrics into rollup tables on a schedule. The route layer wouldn't change — only the query
implementation behind it. This ties straight into the "move to Postgres" future-work note
already in your `Readme.md`.
