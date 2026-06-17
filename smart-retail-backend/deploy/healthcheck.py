#!/usr/bin/env python3
"""
Smart Retail Platform — Health Check Script
Polls /health and /health/detailed and prints a formatted report.
Usage:  python deploy/healthcheck.py [--url http://localhost:8000]
Exit code: 0 = healthy, 1 = degraded/unhealthy
"""

import argparse
import json
import sys
import urllib.error
import urllib.request
from datetime import datetime


def fetch(url: str, timeout: int = 10) -> tuple[int, dict]:
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode())
            return resp.status, body
    except urllib.error.HTTPError as e:
        body = {}
        try:
            body = json.loads(e.read().decode())
        except Exception:
            pass
        return e.code, body
    except Exception as exc:
        return 0, {"error": str(exc)}


def color(text: str, code: int) -> str:
    return f"\033[{code}m{text}\033[0m"


def ok(msg: str) -> str:
    return color(f"  [OK]  {msg}", 32)


def warn(msg: str) -> str:
    return color(f" [WARN] {msg}", 33)


def fail(msg: str) -> str:
    return color(f" [FAIL] {msg}", 31)


def print_section(title: str) -> None:
    print(f"\n{'─' * 55}")
    print(f"  {title}")
    print(f"{'─' * 55}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Smart Retail health check")
    parser.add_argument("--url", default="http://localhost:8000",
                        help="Base URL of the API (default: http://localhost:8000)")
    args = parser.parse_args()
    base = args.url.rstrip("/")

    print(f"\nSmart Retail Platform — Health Report")
    print(f"Timestamp : {datetime.utcnow().isoformat()}Z")
    print(f"Target    : {base}")

    # ── Liveness ────────────────────────────────────────────
    print_section("Liveness Probe  GET /health")
    status, body = fetch(f"{base}/health")
    if status == 200:
        print(ok(f"HTTP {status} — {body.get('status', 'ok')}"))
    else:
        print(fail(f"HTTP {status} — {body}"))

    # ── Readiness ───────────────────────────────────────────
    print_section("Readiness Probe  GET /health/detailed")
    status, body = fetch(f"{base}/health/detailed")
    overall = body.get("status", "unknown")

    if overall == "healthy":
        print(ok(f"Overall: {overall}"))
    elif overall == "degraded":
        print(warn(f"Overall: {overall}"))
    else:
        print(fail(f"Overall: {overall}  (HTTP {status})"))

    checks = body.get("checks", {})
    for check_name, check_data in checks.items():
        if isinstance(check_data, dict):
            s = check_data.get("status", "unknown")
            detail = check_data.get("detail", "")
            msg = f"{check_name}: {s}  {detail}"
            if s == "healthy":
                print(ok(msg))
            elif s == "degraded":
                print(warn(msg))
            else:
                print(fail(msg))

    # ── System info ─────────────────────────────────────────
    sys_info = body.get("system", {})
    if sys_info:
        print_section("System Info")
        for k, v in sys_info.items():
            print(f"    {k:<25} {v}")

    print()
    if overall in ("healthy",):
        return 0
    elif overall == "degraded":
        return 1
    return 1


if __name__ == "__main__":
    sys.exit(main())
