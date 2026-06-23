#!/usr/bin/env python3
"""
Smart Retail Platform — Performance Benchmark
==============================================
Measures API response times across all major endpoint groups and
produces a color-coded latency report with p50, p95, p99 statistics.

Usage:
    python benchmark.py [--url http://localhost:8000] [--n 30] [--token <jwt>]

Options:
    --url      Base URL of the API (default: http://localhost:8000)
    --n        Number of requests per endpoint (default: 20)
    --token    Pre-existing Bearer token (skips auto-login)
    --user     Username for auto-login  (default: admin)
    --password Password for auto-login  (default: Admin@123)
    --json     Emit raw JSON results to stdout instead of table
"""

import argparse
import json
import math
import statistics
import sys
import time
from typing import Optional

try:
    import requests
except ImportError:
    sys.exit("requests is not installed. Run: pip install requests")

# ── ANSI colour helpers ───────────────────────────────────────────────────────

_RESET  = "\033[0m"
_BOLD   = "\033[1m"
_GREEN  = "\033[92m"
_YELLOW = "\033[93m"
_RED    = "\033[91m"
_CYAN   = "\033[96m"
_GREY   = "\033[90m"


def _colour(text: str, code: str) -> str:
    return f"{code}{text}{_RESET}"


def _latency_colour(ms: float) -> str:
    if ms < 100:
        return _colour(f"{ms:>8.1f} ms", _GREEN)
    if ms < 500:
        return _colour(f"{ms:>8.1f} ms", _YELLOW)
    return _colour(f"{ms:>8.1f} ms", _RED)


# ── Benchmark core ────────────────────────────────────────────────────────────

def _percentile(data: list[float], p: float) -> float:
    if not data:
        return 0.0
    sorted_data = sorted(data)
    idx = math.ceil(p / 100 * len(sorted_data)) - 1
    return sorted_data[max(0, idx)]


def _run_requests(
    session: requests.Session,
    method: str,
    url: str,
    n: int,
    **kwargs,
) -> dict:
    latencies = []
    status_codes: dict[int, int] = {}
    errors = 0

    for _ in range(n):
        start = time.perf_counter()
        try:
            resp = session.request(method, url, timeout=30, **kwargs)
            elapsed_ms = (time.perf_counter() - start) * 1000
            latencies.append(elapsed_ms)
            status_codes[resp.status_code] = status_codes.get(resp.status_code, 0) + 1
        except Exception:
            errors += 1

    if not latencies:
        return {"error": "all requests failed", "errors": errors}

    return {
        "n": n,
        "errors": errors,
        "min_ms": round(min(latencies), 1),
        "median_ms": round(statistics.median(latencies), 1),
        "p95_ms": round(_percentile(latencies, 95), 1),
        "p99_ms": round(_percentile(latencies, 99), 1),
        "max_ms": round(max(latencies), 1),
        "rps": round(n / (sum(latencies) / 1000), 1),
        "status_codes": status_codes,
    }


# ── Endpoint groups ───────────────────────────────────────────────────────────

def _build_endpoints(base: str) -> list[tuple[str, str, str]]:
    """Return list of (group, label, url) tuples."""
    return [
        # System
        ("System",   "GET /",                           f"{base}/"),
        ("System",   "GET /health",                     f"{base}/health"),
        ("System",   "GET /health/detailed",            f"{base}/health/detailed"),
        # Products
        ("Products", "GET /api/products/",              f"{base}/api/products/"),
        ("Products", "GET /api/products/1",             f"{base}/api/products/1"),
        # Orders
        ("Orders",   "GET /api/orders/",                f"{base}/api/orders/"),
        ("Orders",   "GET /api/orders/1",               f"{base}/api/orders/1"),
        # Suppliers
        ("Suppliers","GET /api/suppliers/",             f"{base}/api/suppliers/"),
        # Analytics
        ("Analytics","GET /api/analytics/summary",      f"{base}/api/analytics/summary"),
        ("Analytics","GET /api/analytics/bottlenecks",  f"{base}/api/analytics/bottlenecks"),
        # Reports
        ("Reports",  "GET /api/reports/sales/summary",  f"{base}/api/reports/sales/summary"),
        ("Reports",  "GET /api/reports/operations/kpis",f"{base}/api/reports/operations/kpis"),
        # Dashboard
        ("Dashboard","GET /api/dashboard/summary",      f"{base}/api/dashboard/summary"),
        # BI
        ("BI",       "GET /api/bi/executive-summary",   f"{base}/api/bi/executive-summary"),
        ("BI",       "GET /api/bi/kpi-trends",          f"{base}/api/bi/kpi-trends"),
        ("BI",       "GET /api/bi/strategic-insights",  f"{base}/api/bi/strategic-insights"),
        # Recommendations
        ("Reco",     "GET /api/recommendations/health", f"{base}/api/recommendations/health"),
        ("Reco",     "GET /api/recommendations/",       f"{base}/api/recommendations/"),
    ]


# ── Report formatters ─────────────────────────────────────────────────────────

def _print_table(results: list[dict]) -> None:
    header = (
        f"{'Group':<12} {'Endpoint':<40} {'n':>4}  "
        f"{'min':>10}  {'p50':>10}  {'p95':>10}  {'p99':>10}  {'max':>10}  {'rps':>7}"
    )
    print()
    print(_colour("=" * 115, _BOLD))
    print(_colour("  Smart Retail Platform — Benchmark Results", _CYAN + _BOLD))
    print(_colour("=" * 115, _BOLD))
    print(_colour(header, _GREY))
    print(_colour("-" * 115, _GREY))

    prev_group = None
    for r in results:
        if r.get("error"):
            status_str = _colour(f"  {'ERROR: ' + r['error']:<60}", _RED)
            print(f"  {r['group']:<12} {r['label']:<40} {status_str}")
            continue

        if r["group"] != prev_group:
            prev_group = r["group"]

        codes = ", ".join(f"{k}×{v}" for k, v in sorted(r["status_codes"].items()))
        err_str = _colour(f" +{r['errors']}err", _RED) if r["errors"] else ""

        print(
            f"  {r['group']:<12} {r['label']:<40} {r['n']:>4}  "
            f"{_latency_colour(r['min_ms'])}  "
            f"{_latency_colour(r['median_ms'])}  "
            f"{_latency_colour(r['p95_ms'])}  "
            f"{_latency_colour(r['p99_ms'])}  "
            f"{_latency_colour(r['max_ms'])}  "
            f"{r['rps']:>7.1f}/s  "
            f"{_colour(codes, _GREY)}{err_str}"
        )

    print(_colour("-" * 115, _GREY))
    all_p95 = [r["p95_ms"] for r in results if "p95_ms" in r]
    if all_p95:
        overall_p95 = round(statistics.median(all_p95), 1)
        grade_colour = _GREEN if overall_p95 < 200 else (_YELLOW if overall_p95 < 500 else _RED)
        print(f"\n  Overall median p95: {_colour(f'{overall_p95} ms', grade_colour)}")
    print()


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Smart Retail API Benchmark")
    parser.add_argument("--url",      default="http://localhost:8000")
    parser.add_argument("--n",        type=int, default=20)
    parser.add_argument("--token",    default=None)
    parser.add_argument("--user",     default="admin")
    parser.add_argument("--password", default="Admin@123")
    parser.add_argument("--json",     action="store_true")
    args = parser.parse_args()

    session = requests.Session()
    session.headers["Content-Type"] = "application/json"

    # ── Authenticate ─────────────────────────────────────────────────────────
    token: Optional[str] = args.token
    if not token:
        print(f"  Logging in as '{args.user}'…", end=" ", flush=True)
        try:
            resp = session.post(
                f"{args.url}/api/auth/login",
                json={"username": args.user, "password": args.password},
                timeout=10,
            )
            resp.raise_for_status()
            token = resp.json()["access_token"]
            print(_colour("OK", _GREEN))
        except Exception as exc:
            print(_colour(f"FAILED ({exc})", _RED))
            print("  Proceeding without auth token (auth-protected endpoints will return 401)")

    if token:
        session.headers["Authorization"] = f"Bearer {token}"

    # ── Run benchmark ─────────────────────────────────────────────────────────
    endpoints = _build_endpoints(args.url)
    results: list[dict] = []

    print(f"\n  Benchmarking {len(endpoints)} endpoints × {args.n} requests each…\n")

    for group, label, url in endpoints:
        print(f"    {_colour('→', _CYAN)} {label:<45}", end="", flush=True)
        result = _run_requests(session, "GET", url, args.n)
        result["group"] = group
        result["label"] = label
        result["url"] = url
        results.append(result)

        if "error" in result:
            print(_colour(f"  ERROR: {result['error']}", _RED))
        else:
            print(
                f"  p50={result['median_ms']:>6.1f}ms  "
                f"p95={result['p95_ms']:>6.1f}ms  "
                f"rps={result['rps']:>6.1f}/s"
            )

    # ── Output ────────────────────────────────────────────────────────────────
    if args.json:
        print(json.dumps(results, indent=2))
    else:
        _print_table(results)


if __name__ == "__main__":
    main()
