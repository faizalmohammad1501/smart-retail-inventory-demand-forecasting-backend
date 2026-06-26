"""
API Key Rate Limiting Middleware
=================================
Enforces **per-key** sliding-window rate limits for requests authenticated
with the ``X-API-Key`` header.

How it works
------------
1. Inspect the ``X-API-Key`` header.  If absent, skip (JWT or unauthenticated
   request — handled elsewhere).
2. Hash the raw key to look up the stored ``rate_limit_per_minute``.
3. Maintain an in-memory ``deque`` of timestamps keyed by *key_hash*.
4. Prune entries older than 60 seconds; if ``len(window) >= limit``, return
   429 with ``Retry-After`` and ``X-RateLimit-*`` response headers.
5. On pass: append current timestamp, set ``request.state.api_key_hash`` so
   downstream handlers can reference the validated key without re-hashing.

Thread safety
-------------
A ``threading.Lock`` guards the shared ``_windows`` dict.  The middleware runs
in an async context, so the lock is acquired for the minimal critical section
(prune + check + append) and released immediately.

Memory bound
------------
At most ``MAX_WINDOWS`` distinct keys are tracked in memory.  If this cap is
exceeded, the oldest entry is evicted (LRU-ish eviction via ``popitem`` on the
OrderedDict).

Note
----
This middleware only enforces the *per-minute* rate limit.  The daily *quota*
is enforced inside ``api_key_service.verify_api_key()`` which writes to the DB.
"""

import hashlib
import logging
import time
import threading
from collections import deque, OrderedDict
from typing import Deque, Optional

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

logger = logging.getLogger("smart_retail.api_key_ratelimit")

# ── Constants ─────────────────────────────────────────────────────────────────
WINDOW_SECONDS = 60
MAX_WINDOWS    = 10_000   # max distinct API keys tracked in-memory
DEFAULT_LIMIT  = 60       # fallback if we cannot look up the key record


class APIKeyRateLimitMiddleware(BaseHTTPMiddleware):
    """
    Per-API-key sliding-window rate limiter.

    Registered in ``main.py`` *after* the main ``ObservabilityMiddleware`` so
    that rate-limited API key requests are still recorded in metrics/audit.
    """

    def __init__(self, app, default_limit: int = DEFAULT_LIMIT):
        super().__init__(app)
        self._default_limit = default_limit
        self._windows: OrderedDict[str, Deque[float]] = OrderedDict()
        self._lock = threading.Lock()

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _get_limit(self, key_hash: str) -> int:
        """
        Look up the per-key rate limit without a DB session.

        We do a quick DB read here because this middleware runs outside the
        normal request lifecycle.  The cost is one indexed lookup per request.
        """
        try:
            from app.database.connection import SessionLocal
            from app.models.api_key import APIKey

            with SessionLocal() as db:
                key = db.query(APIKey).filter(
                    APIKey.key_hash == key_hash,
                    APIKey.is_active == True,  # noqa: E712
                ).first()
                if key and key.rate_limit_per_minute > 0:
                    return key.rate_limit_per_minute
        except Exception:
            pass
        return self._default_limit

    def _check_rate_limit(self, key_hash: str, limit: int) -> tuple[bool, int, int]:
        """
        Sliding-window check.

        Returns (allowed: bool, used: int, limit: int).
        """
        now = time.monotonic()
        cutoff = now - WINDOW_SECONDS

        with self._lock:
            # Evict oldest entry if at cap
            if key_hash not in self._windows and len(self._windows) >= MAX_WINDOWS:
                self._windows.popitem(last=False)

            window: Deque[float] = self._windows.setdefault(key_hash, deque())

            # Prune timestamps outside the 60-second window
            while window and window[0] < cutoff:
                window.popleft()

            used = len(window)
            if used >= limit:
                return False, used, limit

            window.append(now)
            # Move to end to keep OrderedDict in access order (LRU eviction)
            self._windows.move_to_end(key_hash)
            return True, used + 1, limit

    # ── Middleware dispatch ────────────────────────────────────────────────────

    async def dispatch(self, request: Request, call_next):
        raw_key: Optional[str] = request.headers.get("X-API-Key")

        if not raw_key:
            # Not an API-key request — skip this middleware
            return await call_next(request)

        key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
        limit    = self._get_limit(key_hash)
        allowed, used, cap = self._check_rate_limit(key_hash, limit)

        # Attach hash to request state for downstream use
        request.state.api_key_hash = key_hash

        if not allowed:
            logger.warning(
                "API key rate limit exceeded: prefix=%s used=%d limit=%d path=%s",
                raw_key[:16], used, cap, request.url.path,
            )
            return JSONResponse(
                status_code=429,
                content={
                    "detail": (
                        f"API key rate limit exceeded: {cap} requests/minute. "
                        f"Retry after {WINDOW_SECONDS} seconds."
                    )
                },
                headers={
                    "Retry-After":          str(WINDOW_SECONDS),
                    "X-RateLimit-Limit":    str(cap),
                    "X-RateLimit-Remaining": "0",
                    "X-RateLimit-Window":   f"{WINDOW_SECONDS}s",
                },
            )

        response = await call_next(request)
        response.headers["X-RateLimit-Limit"]     = str(cap)
        response.headers["X-RateLimit-Remaining"] = str(max(0, cap - used))
        response.headers["X-RateLimit-Window"]    = f"{WINDOW_SECONDS}s"
        return response
