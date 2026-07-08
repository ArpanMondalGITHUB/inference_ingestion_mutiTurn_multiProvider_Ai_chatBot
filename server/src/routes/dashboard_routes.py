import math
from typing import Any

from fastapi import APIRouter, Query

from db.db import drop_iso,metrics_overview,fetch_latencies,latency_rows,throughput_rows,error_breakdown


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
def summary(since_hours: int = Query(24,ge=0)) -> dict[str, Any]:
    since = drop_iso(since_hours=since_hours)
    ov = metrics_overview(since_iso=since)
    latencies = fetch_latencies(since_iso=since)

    total = ov["total"] or 0
    errors = ov["error_count"] or 0
    avg = ov["avg_latency_ms"]

    return {
       "sinceHours":since_hours,
       "totalRequests" : total,
       "successCount" : ov["success_count"] or 0,
       "errorCount" : errors,
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
    since = drop_iso(since_hours)
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
    since = drop_iso(since_hours)
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
    since = drop_iso(since_hours)
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

