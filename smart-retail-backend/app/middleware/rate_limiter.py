"""
Sliding-Window Rate Limiting Middleware
========================================
Enforces per-IP request limits using an in-process sliding window counter.

Design:
  - No external dependencies (no Redis)
  - Thread-safe: a single lock guards the shared window dict
  - Per-path overrides: auth endpoints get a tighter limit than the default
  - Returns RFC 6585-compliant 429 with Retry-After header
  - Adds X-RateLimit-{Limit,Remaining,Reset} headers to all responses

Configuration (app/core/config.py):
  RATE_LIMIT_PER_MINUTE         = 120   (default for all routes)
  LOGIN_RATE_LIMIT_PER_MINUTE   = 5     (POST /api/auth/login)
  REGISTER_RATE_LIMIT_PER_MINUTE = 10  (POST /api/auth/register)
"""

import time
import threading
from collections import defaultdict, deque
from fastapi import Request, status
from fastapi.responses import JSONResponse

from app.core.config import settings

# Window duration (seconds) — fixed at 60 s for simplicity
_WINDOW = 60

# Path-specific (method, path) → (limit, window) overrides
def _build_endpoint_limits() -> dict[tuple[str, str], tuple[int, int]]:
    return {
        ("POST", "/api/auth/login"):    (settings.LOGIN_RATE_LIMIT_PER_MINUTE, _WINDOW),
        ("POST", "/api/auth/register"): (settings.REGISTER_RATE_LIMIT_PER_MINUTE, _WINDOW),
        ("POST", "/api/auth/refresh"):  (settings.LOGIN_RATE_LIMIT_PER_MINUTE * 2, _WINDOW),
    }


# ── Shared state ──────────────────────────────────────────────────────────────

class _RateLimiterState:
    def __init__(self) -> None:
        # key → deque of request timestamps (monotonic)
        self._windows: dict[str, deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def is_limited(
        self, key: str, limit: int, window_secs: int
    ) -> tuple[bool, int]:
        """
        Check and record a request for *key*.

        Returns (limited: bool, remaining: int).
        """
        now = time.monotonic()
        cutoff = now - window_secs

        with self._lock:
            bucket = self._windows[key]
            # Evict timestamps outside the current window
            while bucket and bucket[0] < cutoff:
                bucket.popleft()

            if len(bucket) >= limit:
                return True, 0

            bucket.append(now)
            return False, max(0, limit - len(bucket))


_state = _RateLimiterState()


# ── Middleware ────────────────────────────────────────────────────────────────

async def rate_limiting_middleware(request: Request, call_next):
    """
    Sliding-window rate-limiting middleware.

    Skips rate limiting for:
      - Swagger / ReDoc documentation paths (developer tooling)
      - Static /openapi.json schema

    All other paths use the default RATE_LIMIT_PER_MINUTE.
    Auth paths use tighter per-path limits defined above.
    """
    path = request.url.path
    method = request.method

    # Bypass for documentation (no auth needed there anyway)
    if path.startswith(("/docs", "/redoc", "/openapi.json")):
        return await call_next(request)

    # Determine the client identifier
    forwarded_for = request.headers.get("X-Forwarded-For")
    client_ip = (
        forwarded_for.split(",")[0].strip()
        if forwarded_for
        else (request.client.host if request.client else "unknown")
    )

    # Resolve limit for this endpoint
    endpoint_limits = _build_endpoint_limits()
    limit, window = endpoint_limits.get(
        (method, path), (settings.RATE_LIMIT_PER_MINUTE, _WINDOW)
    )

    cache_key = f"{client_ip}:{method}:{path}"
    limited, remaining = _state.is_limited(cache_key, limit, window)

    if limited:
        return JSONResponse(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            content={
                "status": "error",
                "detail": "Too many requests. Please slow down.",
                "code": "RATE_LIMIT_EXCEEDED",
                "retry_after_seconds": window,
            },
            headers={
                "Retry-After": str(window),
                "X-RateLimit-Limit": str(limit),
                "X-RateLimit-Remaining": "0",
                "X-RateLimit-Window": str(window),
            },
        )

    response = await call_next(request)
    response.headers["X-RateLimit-Limit"] = str(limit)
    response.headers["X-RateLimit-Remaining"] = str(remaining)
    response.headers["X-RateLimit-Window"] = str(window)
    return response
