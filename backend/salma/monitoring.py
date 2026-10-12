"""Authorized runtime health and durable message-flow statistics."""

import json
import os
import time
from datetime import timedelta
from functools import lru_cache

import psutil
from fastapi import APIRouter, Request
from sqlalchemy import func, select, text

from salma.auth import require_business, require_operator
from salma.db import Event, Message, Outbox, now, session
from salma.settings import settings

router = APIRouter(prefix="/api")
_process = psutil.Process()
_last_heartbeat = 0.0
_completed_at = None
HEARTBEAT_KEY = "salma:monitoring:worker"


@lru_cache(maxsize=1)
def telemetry_redis():
    from redis import Redis

    return Redis.from_url(
        settings().redis_url, decode_responses=True, socket_connect_timeout=0.5, socket_timeout=0.5
    )


def resources():
    return {
        "pid": _process.pid,
        "uptime_seconds": round(time.time() - _process.create_time()),
        "memory_mb": round(_process.memory_info().rss / 1024 / 1024, 1),
        "cpu_percent": _process.cpu_percent(),
        "cpu_seconds": round(sum(_process.cpu_times()[:2]), 2),
        "cpu_count": os.cpu_count(),
    }


def heartbeat(*, completed=False):
    global _last_heartbeat, _completed_at
    timestamp = time.time()
    if completed:
        _completed_at = timestamp
    if timestamp - _last_heartbeat < 5:
        return
    _last_heartbeat = timestamp
    try:
        telemetry_redis().setex(
            HEARTBEAT_KEY,
            300,
            json.dumps(
                {
                    "seen_at": timestamp,
                    "completed_at": _completed_at,
                    "resources": resources(),
                }
            ),
        )
    except Exception:
        from salma.observability import emit

        emit("heartbeat_unavailable")


def worker_health(heartbeat, *, now=None):
    timestamp = time.time() if now is None else now
    if not heartbeat or not isinstance(heartbeat, dict):
        return {"status": "unavailable"}
    age = max(0, timestamp - float(heartbeat.get("seen_at", 0)))
    return {**heartbeat, "status": "ok" if age < 90 else "stale", "age_seconds": round(age)}


def pipeline_stats(db, business_id=None):
    since = now() - timedelta(hours=24)

    def counts(model, scope):
        query = select(model.status, func.count()).group_by(model.status)
        if business_id:
            query = query.where(model.business_id == business_id)
        return dict(db.execute(query.where(*scope)).all())

    incoming = counts(Message, [Message.direction == "incoming", Message.timestamp >= since])
    delivery = counts(Outbox, [Outbox.created_at >= since])
    # Backlog is unbounded by time: old pending work still needs attention.
    pending = counts(Message, [Message.direction == "incoming", Message.status == "pending"])
    outbound = counts(Outbox, [Outbox.status.in_(["pending", "sending"])])
    query = select(
        Event.data["duration_ms"],
        Event.data["guardrail"],
        Event.data["decision"]["handoff_reason"],
        Event.data["usage"],
        Event.data["error"],
    ).where(Event.kind == "decision_validated", Event.created_at >= since)
    if business_id:
        query = query.where(Event.business_id == business_id)
    # Bound aggregate memory for the local console; expose the sample size explicitly.
    records = db.execute(query.order_by(Event.id.desc()).limit(10000)).all()
    latencies, reasons, tokens, failures = [], {}, 0, 0
    for duration, guardrail, handoff_reason, usage, error in records:
        if isinstance(duration, (int, float)):
            latencies.append(duration)
        reason = guardrail or handoff_reason
        if reason:
            reasons[reason] = reasons.get(reason, 0) + 1
        failures += bool(error)
        tokens += sum(int(u.get("total_tokens") or 0) for u in (usage or []))
    latencies.sort()
    return {
        "window_hours": 24,
        "incoming": incoming,
        "delivery": delivery,
        "pending_messages": sum(pending.values()),
        "pending_deliveries": sum(outbound.values()),
        "sample_size": len(records),
        "sample_limit": 10000,
        "average_latency_ms": round(sum(latencies) / len(latencies)) if latencies else None,
        "p95_latency_ms": latencies[min(len(latencies) - 1, int(len(latencies) * 0.95))]
        if latencies
        else None,
        "handoffs": reasons,
        "handoff_rate": round(sum(reasons.values()) / len(records), 3) if records else 0,
        "agent_failures": failures,
        "total_tokens": tokens,
    }


@router.get("/businesses/{business_id}/monitoring")
def business_status(business_id: str, request: Request):
    require_business(request, business_id)
    with session() as db:
        return {"pipeline": pipeline_stats(db, business_id)}


@router.get("/monitoring/system")
def system_status(request: Request):
    require_operator(request)
    components = {}
    try:
        with session() as db:
            db.execute(text("SELECT 1"))
            pipeline = pipeline_stats(db)
        components["database"] = "ok"
    except Exception:
        components["database"], pipeline = "unavailable", None
    worker = None
    try:
        client = telemetry_redis()
        client.ping()
        raw = client.get(HEARTBEAT_KEY)
        worker = json.loads(raw) if raw else None
        components["redis"] = "ok"
    except Exception:
        components["redis"] = "unavailable"
    enabled = os.environ.get("LANGSMITH_TRACING", "").lower() == "true"
    return {
        "components": components,
        "api": resources(),
        "worker": worker_health(worker),
        "pipeline": pipeline,
        "langsmith": {
            "configured": enabled and bool(os.environ.get("LANGSMITH_API_KEY")),
            "project": os.environ.get("LANGSMITH_PROJECT", "salma"),
            "project_url": os.environ.get("LANGSMITH_PROJECT_URL") or None,
        },
        "updated_at": now().isoformat(),
    }
