"""
Metrics API Routes
====================
Exposes real-time operational metrics collected by the in-process
MetricsCollector for monitoring dashboards and alerting.

Endpoints:
  GET  /api/metrics/summary     — full metrics snapshot (all groups)
  GET  /api/metrics/http        — HTTP request/error counters
  GET  /api/metrics/latency     — per-endpoint latency percentiles
  GET  /api/metrics/business    — domain-level business event counters
  GET  /api/metrics/system      — runtime environment (CPU, memory, disk)
  POST /api/metrics/reset       — zero all counters (admin only)

All read endpoints require role: admin or analyst.
The reset endpoint requires role: admin.
"""

import logging
from typing import Any, Dict

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_active_user, require_roles
from app.core.metrics import metrics
from app.database.connection import get_db
from app.models.user import User

logger = logging.getLogger("smart_retail.metrics_routes")

router = APIRouter(prefix="/api/metrics", tags=["Metrics & Performance"])


# ── GET /api/metrics/summary ──────────────────────────────────────────────────

@router.get("/summary", status_code=status.HTTP_200_OK)
def metrics_summary(
    _: User = Depends(require_roles("admin", "analyst")),
) -> Dict[str, Any]:
    """
    Full real-time metrics snapshot.

    Returns all metric groups:
      - uptime_seconds, started_at
      - http: total_requests, total_errors, error_rate_pct, active_requests,
              by_status_code
      - latency: per-endpoint p50/p95/p99/max/min, slowest_top10
      - business: ml_train, forecast, alert_run, order_create, login, …
      - system: python_version, platform, cpu_count, memory_mb, disk_free_gb
    """
    return metrics.snapshot()


# ── GET /api/metrics/http ─────────────────────────────────────────────────────

@router.get("/http", status_code=status.HTTP_200_OK)
def http_metrics(
    _: User = Depends(require_roles("admin", "analyst")),
) -> Dict[str, Any]:
    """
    HTTP-level counters: total requests, errors, active connections.

    Suitable for lightweight polling from monitoring dashboards (lower payload
    than the full snapshot).
    """
    snap = metrics.snapshot()
    return {
        "uptime_seconds": snap["uptime_seconds"],
        "started_at":     snap["started_at"],
        **snap["http"],
    }


# ── GET /api/metrics/latency ──────────────────────────────────────────────────

@router.get("/latency", status_code=status.HTTP_200_OK)
def latency_metrics(
    _: User = Depends(require_roles("admin", "analyst")),
) -> Dict[str, Any]:
    """
    Per-endpoint latency percentiles (p50 / p95 / p99 / max / min).

    Also returns `slowest_top10`: the 10 endpoints with the highest p95
    latency — useful for identifying performance bottlenecks.

    Values are derived from a rolling window of up to 2 000 samples per
    endpoint.  Zero-sample endpoints are omitted.
    """
    snap = metrics.snapshot()
    return snap["latency"]


# ── GET /api/metrics/business ─────────────────────────────────────────────────

@router.get("/business", status_code=status.HTTP_200_OK)
def business_metrics(
    _: User = Depends(require_roles("admin", "analyst")),
) -> Dict[str, Any]:
    """
    Business-level event counters since server start.

    Counters include:
      ml_train      — number of model training runs
      forecast      — number of forecast requests
      alert_run     — number of notification engine runs
      order_create  — number of orders created
      login         — number of successful logins
      auth_failure  — number of 401/403 responses
      rate_limited  — number of 429 responses
      export        — number of CSV/PDF export downloads
    """
    snap = metrics.snapshot()
    return {
        "uptime_seconds": snap["uptime_seconds"],
        "business_events": snap["business"],
    }


# ── GET /api/metrics/system ───────────────────────────────────────────────────

@router.get("/system", status_code=status.HTTP_200_OK)
def system_metrics(
    _: User = Depends(require_roles("admin", "analyst")),
) -> Dict[str, Any]:
    """
    Runtime environment information.

    Returns: python_version, platform, cpu_count, memory_mb (RSS),
             disk_free_gb (working directory).

    Note: memory_mb uses rusage on Linux/macOS — not available on Windows.
    """
    snap = metrics.snapshot()
    return {
        "uptime_seconds": snap["uptime_seconds"],
        "started_at":     snap["started_at"],
        **snap["system"],
    }


# ── POST /api/metrics/reset ───────────────────────────────────────────────────

@router.post("/reset", status_code=status.HTTP_200_OK)
def reset_metrics(
    _: User = Depends(require_roles("admin")),
) -> Dict[str, Any]:
    """
    Zero all in-process metric counters.

    Use this before a load test to get clean baseline measurements.
    Uptime is NOT reset.  Requires role: admin.
    """
    metrics.reset()
    logger.info("Metrics counters reset by admin")
    return {
        "status":  "reset",
        "message": "All metric counters have been zeroed. Uptime preserved.",
    }
