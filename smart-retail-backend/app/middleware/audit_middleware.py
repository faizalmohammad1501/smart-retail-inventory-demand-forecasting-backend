"""
Observability Middleware
=========================
A single Starlette middleware that performs three jobs per request:

  1. Metrics recording — updates the MetricsCollector with request count,
     latency, and status code; manages the active-requests gauge.

  2. Audit logging — enqueues an audit record for every MUTATING request
     (POST, PUT, PATCH, DELETE) and for security events (401, 403, 429).
     Read requests (GET, HEAD, OPTIONS) are silently skipped to keep the
     audit table focused on state changes and access-control events.

  3. Actor resolution — decodes the Bearer token (if present) to attach
     user_id and username to the audit record without touching the DB.

Security classification rules:
  status 401 / 403  → event_type=AUTH    severity=WARNING  (access denied)
  status 429        → event_type=SECURITY severity=WARNING  (rate-limited)
  status 5xx        → event_type=SYSTEM  severity=ERROR
  POST auth/login   → event_type=AUTH    action=LOGIN
  POST auth/logout  → event_type=AUTH    action=LOGOUT
  POST auth/refresh → event_type=AUTH    action=TOKEN_REFRESH
  POST auth/register→ event_type=AUTH    action=REGISTER
  POST ml/pipeline  → event_type=ML      action=PIPELINE_RUN
  POST predictions  → event_type=ML      action=TRAIN / FORECAST
  POST notifications→ event_type=ALERT   action=ALERT_RUN
  POST/PUT/PATCH/DEL → event_type=CRUD   action=CREATE/UPDATE/DELETE

Paths excluded from audit (static / read-only):
  /health, /health/detailed, /docs, /redoc, /openapi.json, /
"""

import logging
import time
from typing import Optional, Tuple

from fastapi import Request
from fastapi.responses import Response
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.metrics import metrics

logger = logging.getLogger("smart_retail.observability")

# ── Skip-list for audit (never mutating / high-frequency read paths) ─────────
_AUDIT_SKIP_PATHS = frozenset({
    "/", "/health", "/health/detailed",
    "/docs", "/redoc", "/openapi.json",
})
_AUDIT_SKIP_PREFIXES = ("/docs/", "/redoc/", "/openapi.json")

# ── Path-to-event classification ──────────────────────────────────────────────
_ML_PREFIXES = (
    "/api/ml/",
    "/api/predictions/train",
    "/api/predictions/forecast",
)
_AUTH_PATHS = {
    "/api/auth/login":    "LOGIN",
    "/api/auth/logout":   "LOGOUT",
    "/api/auth/refresh":  "TOKEN_REFRESH",
    "/api/auth/register": "REGISTER",
}
_ALERT_PREFIXES = ("/api/notifications/run",)
_EXPORT_PREFIXES = ("/api/reports/export/", "/api/dashboard/export/")


def _classify(method: str, path: str, status: int) -> Tuple[str, str, str]:
    """
    Returns (event_type, action, severity).
    Called once per request after the response is received.
    """
    # Security events from status code first
    if status == 429:
        return "SECURITY", "RATE_LIMITED",  "WARNING"
    if status == 401:
        return "AUTH",     "ACCESS_DENIED", "WARNING"
    if status == 403:
        return "AUTH",     "RBAC_DENIED",   "WARNING"
    if status >= 500:
        return "SYSTEM",   "SERVER_ERROR",  "ERROR"

    # Auth paths (POST only)
    if path in _AUTH_PATHS:
        action = _AUTH_PATHS[path]
        sev = "INFO" if status < 400 else "WARNING"
        return "AUTH", action, sev

    # ML paths
    if any(path.startswith(p) for p in _ML_PREFIXES):
        action = "TRAIN" if "train" in path else "FORECAST" if "forecast" in path else "ML_PIPELINE"
        return "ML", action, "INFO"

    # Alert engine
    if any(path.startswith(p) for p in _ALERT_PREFIXES):
        return "ALERT", "ALERT_RUN", "INFO"

    # Exports
    if any(path.startswith(p) for p in _EXPORT_PREFIXES):
        return "EXPORT", "EXPORT", "INFO"

    # Generic CRUD
    method_to_action = {
        "POST":   "CREATE",
        "PUT":    "UPDATE",
        "PATCH":  "UPDATE",
        "DELETE": "DELETE",
    }
    action = method_to_action.get(method, method)
    return "CRUD", action, "INFO"


def _resource_from_path(path: str) -> Tuple[str, Optional[int]]:
    """
    Extract resource_type and resource_id from a URL path.
    E.g. /api/products/42  → ("product", 42)
         /api/orders/      → ("order", None)
    """
    segments = [s for s in path.strip("/").split("/") if s]
    resource_map = {
        "products":      "product",
        "orders":        "order",
        "suppliers":     "supplier",
        "inventory":     "inventory",
        "users":         "user",
        "notifications": "notification",
        "predictions":   "ml_model",
        "pipeline":      "ml_pipeline",
        "reports":       "report",
        "dashboard":     "dashboard",
        "bi":            "bi",
        "auth":          "user",
        "recommendations":"recommendation",
    }
    # Walk path segments to find a known resource type
    resource_type = ""
    resource_id: Optional[int] = None
    for i, seg in enumerate(segments):
        if seg in resource_map:
            resource_type = resource_map[seg]
            # Next segment might be an integer ID
            if i + 1 < len(segments):
                try:
                    resource_id = int(segments[i + 1])
                except ValueError:
                    pass
            break

    return resource_type, resource_id


def _decode_actor(request: Request) -> Tuple[Optional[int], str]:
    """
    Lightweight actor extraction: decode the JWT without DB lookup.
    Returns (user_id, username).  Falls back to (None, "anonymous").
    """
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None, "anonymous"
    token = auth[7:]
    try:
        from app.core.security import decode_token
        payload = decode_token(token)
        if payload and payload.get("type") == "access":
            return payload.get("user_id"), payload.get("sub", "anonymous")
    except Exception:
        pass
    return None, "anonymous"


def _client_ip(request: Request) -> str:
    fwd = request.headers.get("X-Forwarded-For")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


# ── Middleware class ──────────────────────────────────────────────────────────

class ObservabilityMiddleware(BaseHTTPMiddleware):
    """
    Starlette middleware that records metrics and enqueues audit records.
    Never raises — any internal failure is logged and silently ignored.
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        path   = request.url.path
        method = request.method

        # Track active requests
        metrics.increment_active()
        t0 = time.perf_counter()

        try:
            response: Response = await call_next(request)
        except Exception as exc:
            # Record the error even if the response object doesn't exist
            duration_ms = round((time.perf_counter() - t0) * 1000, 2)
            metrics.decrement_active()
            metrics.record_request(method, path, 500, duration_ms)
            raise

        duration_ms = round((time.perf_counter() - t0) * 1000, 2)
        status_code = response.status_code

        # ── 1. Metrics ────────────────────────────────────────────────────────
        metrics.decrement_active()
        metrics.record_request(method, path, status_code, duration_ms)

        # ── 2. Audit log ──────────────────────────────────────────────────────
        try:
            self._maybe_audit(request, method, path, status_code, duration_ms)
        except Exception as exc:
            logger.warning("Audit enqueue failed: %s", exc)

        return response

    # ─────────────────────────────────────────────────────────────────────────

    @staticmethod
    def _maybe_audit(
        request: Request,
        method: str,
        path: str,
        status_code: int,
        duration_ms: float,
    ) -> None:
        """Decide whether to emit an audit record and, if so, enqueue it."""
        # Skip static / read-only paths
        if path in _AUDIT_SKIP_PATHS:
            return
        if any(path.startswith(p) for p in _AUDIT_SKIP_PREFIXES):
            return

        # Audit all mutating methods AND security events on any method
        is_mutating = method in ("POST", "PUT", "PATCH", "DELETE")
        is_security_event = status_code in (401, 403, 429) or status_code >= 500

        if not is_mutating and not is_security_event:
            return

        event_type, action, severity = _classify(method, path, status_code)
        resource_type, resource_id   = _resource_from_path(path)
        user_id, username            = _decode_actor(request)
        ip                           = _client_ip(request)
        request_id                   = getattr(request.state, "request_id", "")
        user_agent                   = request.headers.get("user-agent", "")

        from app.services.audit_service import enqueue_audit
        enqueue_audit(
            event_type    = event_type,
            action        = action,
            severity      = severity,
            resource_type = resource_type,
            resource_id   = resource_id,
            user_id       = user_id,
            username      = username,
            ip_address    = ip,
            user_agent    = user_agent,
            request_id    = request_id,
            method        = method,
            path          = path,
            status_code   = status_code,
            duration_ms   = duration_ms,
        )

        # Business counter instrumentation
        if event_type == "ML" and action == "TRAIN":
            metrics.increment_business("ml_train")
        elif event_type == "ML" and action == "FORECAST":
            metrics.increment_business("forecast")
        elif event_type == "ALERT":
            metrics.increment_business("alert_run")
        elif event_type == "EXPORT":
            metrics.increment_business("export")
        elif event_type == "CRUD" and action == "CREATE" and "orders" in path:
            metrics.increment_business("order_create")
        elif event_type == "AUTH" and action == "LOGIN" and status_code == 200:
            metrics.increment_business("login")
        elif event_type == "AUTH" and action == "ACCESS_DENIED":
            metrics.increment_business("auth_failure")
        elif event_type == "SECURITY" and action == "RATE_LIMITED":
            metrics.increment_business("rate_limited")
