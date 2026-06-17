#!/usr/bin/env python3
"""
Smart Retail Platform — Production Startup Script
Runs pre-flight checks, applies DB migrations, then launches uvicorn.
Usage: python deploy/start.py [--port 8000] [--workers 2]
"""

import argparse
import logging
import os
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path

# ── logging setup ─────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("startup")

# ── root is the smart-retail-backend directory ────────────────
ROOT = Path(__file__).resolve().parent.parent


def check_env() -> None:
    """Abort if critical env vars are missing or insecure."""
    required = ["JWT_SECRET_KEY", "DATABASE_URL"]
    missing = [k for k in required if not os.environ.get(k)]
    if missing:
        log.error(f"Missing required environment variables: {missing}")
        sys.exit(1)

    jwt_key = os.environ.get("JWT_SECRET_KEY", "")
    if len(jwt_key) < 32:
        log.error("JWT_SECRET_KEY must be at least 32 characters.")
        sys.exit(1)

    if "CHANGE_ME" in jwt_key or "REPLACE_WITH" in jwt_key:
        env = os.environ.get("APP_ENV", "development")
        if env == "production":
            log.error("Default/placeholder JWT_SECRET_KEY detected in production! Aborting.")
            sys.exit(1)
        else:
            log.warning("Placeholder JWT_SECRET_KEY — acceptable for dev only.")

    log.info("Environment variables validated.")


def run_migrations() -> None:
    """Apply pending Alembic migrations."""
    import subprocess  # noqa: S404 — controlled local invocation
    log.info("Applying database migrations…")
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        log.error(f"Alembic migration failed:\n{result.stderr}")
        sys.exit(1)
    log.info("Migrations applied successfully.")


def wait_for_api(host: str, port: int, retries: int = 10, delay: float = 3.0) -> bool:
    """Poll /health until the API responds or retries exhausted."""
    url = f"http://{host}:{port}/health"
    for attempt in range(1, retries + 1):
        try:
            with urllib.request.urlopen(url, timeout=5) as resp:
                if resp.status == 200:
                    log.info(f"API healthy at {url}")
                    return True
        except (urllib.error.URLError, OSError):
            log.info(f"Health check attempt {attempt}/{retries} failed — retrying in {delay}s…")
            time.sleep(delay)
    log.error("API did not become healthy in time.")
    return False


def start_server(host: str, port: int, workers: int) -> None:
    """Launch uvicorn."""
    import subprocess  # noqa: S404
    log.info(f"Starting uvicorn on {host}:{port} with {workers} worker(s)…")
    cmd = [
        sys.executable, "-m", "uvicorn", "main:app",
        "--host", host,
        "--port", str(port),
        "--workers", str(workers),
        "--loop", "uvloop",
        "--log-level", os.environ.get("LOG_LEVEL", "info").lower(),
        "--access-log",
    ]
    result = subprocess.run(cmd, cwd=ROOT)  # noqa: S603
    sys.exit(result.returncode)


def main() -> None:
    parser = argparse.ArgumentParser(description="Smart Retail startup")
    parser.add_argument("--host",    default="0.0.0.0")
    parser.add_argument("--port",    default=8000, type=int)
    parser.add_argument("--workers", default=2, type=int)
    parser.add_argument("--skip-migrations", action="store_true")
    args = parser.parse_args()

    log.info("=" * 60)
    log.info("Smart Retail Platform — Production Startup")
    log.info("=" * 60)

    check_env()

    if not args.skip_migrations:
        run_migrations()

    start_server(args.host, args.port, args.workers)


if __name__ == "__main__":
    main()
