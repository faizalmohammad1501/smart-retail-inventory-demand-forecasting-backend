"""
In-Process Metrics Collector
==============================
Collects real-time request and performance metrics without any external
dependency (no Prometheus client library required).

Design:
  - Thread-safe: a single RLock guards all mutable state
  - Low-overhead: O(1) record_request(); percentile computation is O(n log n)
    only on read, not on write
  - Rolling window: latency samples are capped at 2 000 per endpoint via
    deque(maxlen) to prevent unbounded memory growth
  - Business counters: separate counters for domain events (orders, forecasts, …)
    so the monitoring dashboard shows business-level throughput alongside
    infrastructure metrics

Metric groups:
  1. HTTP — request counts, error counts, active requests
  2. Latency — per-endpoint rolling p50 / p95 / p99 / max
  3. Errors — breakdown by HTTP status code
  4. Business — ML trains, forecast requests, alert runs, exports
  5. System — process uptime, startup timestamp

Usage:
    from app.core.metrics import metrics

    # In middleware (called every request):
    metrics.record_request(method, path, status_code, duration_ms)

    # In business logic (optional manual instrumentation):
    metrics.increment_business("forecast")
    metrics.increment_business("ml_train")

    # In monitoring endpoint:
    data = metrics.snapshot()
"""

import math
import platform
import sys
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timezone
from typing import Any, Dict


_LATENCY_WINDOW = 2_000   # max samples per endpoint kept in memory


class MetricsCollector:
    """Thread-safe rolling-window metrics store."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._start_monotonic: float = time.monotonic()
        self._start_wall: str = datetime.now(timezone.utc).isoformat()

        # HTTP counters
        self._total_requests: int = 0
        self._total_errors: int = 0
        self._active_requests: int = 0

        # Per-endpoint request counts  key = "METHOD:path"
        self._endpoint_counts: Dict[str, int] = defaultdict(int)

        # Per-endpoint latency deques  key = "METHOD:path"
        self._endpoint_latencies: Dict[str, deque] = defaultdict(
            lambda: deque(maxlen=_LATENCY_WINDOW)
        )

        # Error breakdown by status code
        self._status_counts: Dict[int, int] = defaultdict(int)

        # Business-level event counters (domain events)
        self._business_counters: Dict[str, int] = defaultdict(int)

    # ── Write path (called in middleware — must be fast) ──────────────────────

    def record_request(
        self,
        method: str,
        path: str,
        status_code: int,
        duration_ms: float,
    ) -> None:
        """Record one completed HTTP request. O(1)."""
        key = f"{method}:{path}"
        with self._lock:
            self._total_requests += 1
            self._endpoint_counts[key] += 1
            self._endpoint_latencies[key].append(duration_ms)
            self._status_counts[status_code] += 1
            if status_code >= 400:
                self._total_errors += 1

    def increment_active(self) -> None:
        with self._lock:
            self._active_requests += 1

    def decrement_active(self) -> None:
        with self._lock:
            self._active_requests = max(0, self._active_requests - 1)

    def increment_business(self, event: str) -> None:
        """
        Increment a named business counter.

        Suggested event names:
          ml_train  forecast  alert_run  export  notification_read
          product_create  order_create  supplier_create
        """
        with self._lock:
            self._business_counters[event] += 1

    # ── Read path (called by the metrics API endpoint) ────────────────────────

    def snapshot(self) -> Dict[str, Any]:
        """
        Return a consistent point-in-time snapshot of all collected metrics.
        Percentile computation happens here (not on write).
        """
        with self._lock:
            uptime_s = round(time.monotonic() - self._start_monotonic, 1)
            error_rate = (
                round(self._total_errors / self._total_requests * 100, 2)
                if self._total_requests > 0 else 0.0
            )

            # Per-endpoint stats
            endpoints: Dict[str, Any] = {}
            for key, samples in self._endpoint_latencies.items():
                if samples:
                    endpoints[key] = {
                        "requests":  self._endpoint_counts[key],
                        **_percentiles(samples),
                    }

            # Identify the 10 slowest endpoints by p95
            slowest = sorted(
                endpoints.items(),
                key=lambda kv: kv[1].get("p95_ms", 0),
                reverse=True,
            )[:10]

            return {
                "uptime_seconds":   uptime_s,
                "started_at":       self._start_wall,
                "http": {
                    "total_requests":  self._total_requests,
                    "total_errors":    self._total_errors,
                    "error_rate_pct":  error_rate,
                    "active_requests": self._active_requests,
                    "by_status_code":  dict(self._status_counts),
                },
                "latency": {
                    "endpoints":    endpoints,
                    "slowest_top10": [
                        {"endpoint": k, **v} for k, v in slowest
                    ],
                },
                "business": dict(self._business_counters),
                "system": _system_info(),
            }

    def endpoint_stats(self) -> Dict[str, Any]:
        """Return per-endpoint stats only (lighter payload than full snapshot)."""
        with self._lock:
            return {
                key: {
                    "requests": self._endpoint_counts[key],
                    **_percentiles(samples),
                }
                for key, samples in self._endpoint_latencies.items()
                if samples
            }

    def reset(self) -> None:
        """
        Reset all counters.  Admin-only — useful for baseline testing.
        Does NOT reset the start time so uptime is preserved.
        """
        with self._lock:
            self._total_requests = 0
            self._total_errors = 0
            self._active_requests = 0
            self._endpoint_counts.clear()
            self._endpoint_latencies.clear()
            self._status_counts.clear()
            self._business_counters.clear()


# ── Module-level singleton ────────────────────────────────────────────────────

metrics: MetricsCollector = MetricsCollector()


# ── Helper functions ──────────────────────────────────────────────────────────

def _percentile(sorted_data: list, p: float) -> float:
    if not sorted_data:
        return 0.0
    idx = max(0, math.ceil(p / 100 * len(sorted_data)) - 1)
    return round(sorted_data[idx], 2)


def _percentiles(samples) -> Dict[str, float]:
    """Compute p50 / p95 / p99 / max from a deque of latency values."""
    s = sorted(samples)
    return {
        "p50_ms": _percentile(s, 50),
        "p95_ms": _percentile(s, 95),
        "p99_ms": _percentile(s, 99),
        "max_ms": round(s[-1], 2) if s else 0.0,
        "min_ms": round(s[0],  2) if s else 0.0,
        "samples": len(s),
    }


def _system_info() -> Dict[str, Any]:
    """Collect lightweight runtime system info (no psutil required)."""
    info: Dict[str, Any] = {
        "python_version": sys.version.split()[0],
        "platform":       platform.system(),
        "cpu_count":      None,
        "memory_mb":      None,
        "disk_free_gb":   None,
    }

    # Optional: CPU count (always available without psutil)
    try:
        import os
        info["cpu_count"] = os.cpu_count()
    except Exception:
        pass

    # Optional: memory usage (psutil or /proc/self/status on Linux)
    try:
        import resource
        mem_bytes = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        # macOS reports bytes; Linux reports kilobytes
        divisor = 1_048_576 if platform.system() == "Darwin" else 1_024
        info["memory_mb"] = round(mem_bytes / divisor, 1)
    except Exception:
        pass

    # Disk free (always available via shutil)
    try:
        import shutil
        _, _, free = shutil.disk_usage(".")
        info["disk_free_gb"] = round(free / 1e9, 2)
    except Exception:
        pass

    return info
