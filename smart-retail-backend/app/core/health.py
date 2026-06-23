"""
Health Check Service
======================
Deep health checks for all system components.
Used by GET /health (liveness) and GET /health/detailed (readiness).
"""

import os
import time
from datetime import datetime, timezone
from typing import Any, Dict

from sqlalchemy.orm import Session
from sqlalchemy import text


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def check_database(db: Session) -> Dict[str, Any]:
    """Verify DB connectivity and return basic stats."""
    try:
        start = time.perf_counter()
        db.execute(text("SELECT 1"))
        latency_ms = round((time.perf_counter() - start) * 1000, 2)

        # Row counts for key tables
        counts = {}
        for table in ("orders", "products", "suppliers", "inventory"):
            try:
                result = db.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar()
                counts[table] = result
            except Exception:
                counts[table] = "unavailable"

        return {
            "status": "healthy",
            "latency_ms": latency_ms,
            "table_counts": counts,
        }
    except Exception as exc:
        return {"status": "unhealthy", "error": str(exc)}


def check_ml_model() -> Dict[str, Any]:
    """Check whether the trained ML model artefact exists on disk."""
    try:
        from ml.config import SAVED_MODELS_DIR
        model_path = os.path.join(SAVED_MODELS_DIR, "model.pkl")
        meta_path = os.path.join(SAVED_MODELS_DIR, "training_metadata.json")

        model_exists = os.path.isfile(model_path)
        meta_exists = os.path.isfile(meta_path)

        trained_at = None
        val_metrics = None
        if meta_exists:
            import json
            with open(meta_path) as f:
                meta = json.load(f)
            trained_at = meta.get("trained_at")
            val_metrics = meta.get("val_metrics")

        return {
            "status": "ready" if model_exists else "not_trained",
            "model_file_exists": model_exists,
            "metadata_file_exists": meta_exists,
            "trained_at": trained_at,
            "val_metrics": val_metrics,
        }
    except Exception as exc:
        return {"status": "unknown", "error": str(exc)}


def check_ml_datasets() -> Dict[str, Any]:
    """Check whether preprocessed dataset files are available."""
    try:
        from ml.config import PROCESSED_DIR
        splits = {}
        for split in ("train", "val", "test"):
            path = os.path.join(PROCESSED_DIR, f"{split}.csv")
            splits[split] = os.path.isfile(path)
        all_ready = all(splits.values())
        return {"status": "ready" if all_ready else "missing", "splits": splits}
    except Exception as exc:
        return {"status": "unknown", "error": str(exc)}


def check_disk_space() -> Dict[str, Any]:
    """Return free disk space on the working directory."""
    try:
        import shutil
        total, used, free = shutil.disk_usage(".")
        return {
            "status": "healthy" if free > 100 * 1024 * 1024 else "low",  # warn below 100 MB
            "total_gb": round(total / 1e9, 2),
            "used_gb": round(used / 1e9, 2),
            "free_gb": round(free / 1e9, 2),
            "used_pct": round(used / total * 100, 1),
        }
    except Exception as exc:
        return {"status": "unknown", "error": str(exc)}


def get_system_info() -> Dict[str, Any]:
    """Return runtime environment info (non-sensitive)."""
    import platform
    import sys
    return {
        "python_version": sys.version.split()[0],
        "platform": platform.system(),
        "pid": os.getpid(),
    }


def full_health_check(db: Session) -> Dict[str, Any]:
    """
    Aggregate health across all subsystems.
    Returns overall 'healthy' only when ALL critical components pass.
    """
    start = time.perf_counter()

    checks = {
        "database": check_database(db),
        "ml_model": check_ml_model(),
        "ml_datasets": check_ml_datasets(),
        "disk": check_disk_space(),
        "cache": _check_cache(),
    }

    # Critical: database must be healthy
    critical_ok = checks["database"]["status"] == "healthy"
    overall = "healthy" if critical_ok else "degraded"

    return {
        "status": overall,
        "timestamp": _now(),
        "uptime_check_ms": round((time.perf_counter() - start) * 1000, 2),
        "system": get_system_info(),
        "checks": checks,
    }


def _check_cache() -> Dict[str, Any]:
    """Return in-process cache statistics."""
    try:
        from app.core.cache import cache
        stats = cache.stats()
        return {"status": "healthy", **stats}
    except Exception as exc:
        return {"status": "unknown", "error": str(exc)}
