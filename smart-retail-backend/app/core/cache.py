"""
In-Process TTL Cache
=====================
Thread-safe key/value store with per-entry time-to-live.

Design constraints:
  - No external dependencies (no Redis required)
  - Safe for use with FastAPI's threaded Uvicorn workers
  - Automatic expiry: expired entries are evicted on read and periodically swept

Usage:
    from app.core.cache import cache

    # Store a value for 60 seconds
    cache.set("products:all", product_list, ttl=60)

    # Retrieve (returns None if missing or expired)
    cached = cache.get("products:all")

    # Invalidate all keys that start with a prefix
    cache.invalidate_prefix("products:")

    # Stats for /health/detailed
    cache.stats()
"""

import time
import threading
import logging
from typing import Any, Optional

logger = logging.getLogger("smart_retail.cache")


class TTLCache:
    """
    Minimal, thread-safe in-process TTL cache.

    Eviction strategy:
      - Lazy eviction on read (O(1) per access)
      - Periodic background sweep every `sweep_interval` seconds removes
        stale entries so memory does not grow indefinitely.
    """

    def __init__(self, default_ttl: int = 300, sweep_interval: int = 60) -> None:
        self._store: dict[str, tuple[Any, float]] = {}
        self._lock = threading.Lock()
        self.default_ttl = default_ttl
        self._hits = 0
        self._misses = 0

        # Background sweeper
        self._sweep_interval = sweep_interval
        self._sweeper = threading.Thread(
            target=self._sweep_loop, daemon=True, name="cache-sweeper"
        )
        self._sweeper.start()

    # ── Public API ─────────────────────────────────────────────────────────────

    def get(self, key: str) -> Optional[Any]:
        """Return the cached value, or None if missing / expired."""
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                self._misses += 1
                return None
            value, expiry = entry
            if time.monotonic() > expiry:
                del self._store[key]
                self._misses += 1
                return None
            self._hits += 1
            return value

    def set(self, key: str, value: Any, ttl: Optional[int] = None) -> None:
        """Store *value* under *key* with a TTL of *ttl* seconds."""
        expiry = time.monotonic() + (ttl if ttl is not None else self.default_ttl)
        with self._lock:
            self._store[key] = (value, expiry)

    def delete(self, key: str) -> bool:
        """Delete a single key. Returns True if it existed."""
        with self._lock:
            return self._store.pop(key, None) is not None

    def invalidate_prefix(self, prefix: str) -> int:
        """
        Delete all keys that begin with *prefix*.
        Returns the number of entries removed.
        """
        with self._lock:
            keys = [k for k in self._store if k.startswith(prefix)]
            for k in keys:
                del self._store[k]
            if keys:
                logger.debug("Cache invalidated %d keys with prefix '%s'", len(keys), prefix)
            return len(keys)

    def clear(self) -> None:
        """Remove all entries."""
        with self._lock:
            self._store.clear()
        logger.debug("Cache cleared")

    def stats(self) -> dict:
        """Return diagnostic counters (for /health/detailed)."""
        with self._lock:
            now = time.monotonic()
            total = len(self._store)
            alive = sum(1 for _, (_, exp) in self._store.items() if exp > now)
            total_requests = self._hits + self._misses
            hit_rate = round(self._hits / total_requests * 100, 1) if total_requests else 0.0
        return {
            "total_keys": total,
            "alive_keys": alive,
            "expired_keys": total - alive,
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate_pct": hit_rate,
        }

    # ── Background eviction ────────────────────────────────────────────────────

    def _sweep_loop(self) -> None:
        """Daemon thread: evicts expired entries every sweep_interval seconds."""
        while True:
            time.sleep(self._sweep_interval)
            self._evict_expired()

    def _evict_expired(self) -> None:
        now = time.monotonic()
        with self._lock:
            expired = [k for k, (_, exp) in self._store.items() if exp <= now]
            for k in expired:
                del self._store[k]
        if expired:
            logger.debug("Cache sweep evicted %d expired entries", len(expired))


# ── Module-level singleton ─────────────────────────────────────────────────────
# Import this instance everywhere — all workers share the same in-process cache.
cache = TTLCache(default_ttl=300, sweep_interval=60)

# ── Cache key helpers (prevents typos / key collisions) ───────────────────────

class CacheKeys:
    """Centralised cache key constants."""

    # Product listing — invalidate on any product mutation
    PRODUCTS_ALL = "products:all"

    @staticmethod
    def product(product_id: int) -> str:
        return f"products:{product_id}"

    @staticmethod
    def products_category(category: str) -> str:
        return f"products:category:{category.lower()}"

    # Supplier listing
    SUPPLIERS_ALL = "suppliers:all"

    @staticmethod
    def supplier(supplier_id: int) -> str:
        return f"suppliers:{supplier_id}"

    # Dashboard / BI (expensive aggregations)
    @staticmethod
    def dashboard_summary(days: int) -> str:
        return f"dashboard:summary:{days}"

    @staticmethod
    def bi_executive_summary(days: int) -> str:
        return f"bi:executive_summary:{days}"

    @staticmethod
    def bi_kpi_trends(days: int, granularity: str) -> str:
        return f"bi:kpi_trends:{days}:{granularity}"

    # Forecasts
    @staticmethod
    def forecast(product_id: int, days: int) -> str:
        return f"forecast:{product_id}:{days}"

    FORECAST_ALL = "forecast:all"

    # Recommendations
    INVENTORY_HEALTH = "recommendations:health"
    REPLENISHMENT = "recommendations:replenishment"

    # Notification summary
    NOTIFICATION_SUMMARY = "notifications:summary"


# TTL constants (seconds)
class CacheTTL:
    PRODUCT = 300         # 5 min — products change rarely
    SUPPLIER = 300
    DASHBOARD = 120       # 2 min — dashboard refreshes often
    BI = 180              # 3 min — BI reports
    FORECAST = 600        # 10 min — forecasts are expensive to compute
    HEALTH = 30           # 30 s — health metrics should be fresh
    RECOMMENDATIONS = 120
    NOTIFICATIONS = 60
