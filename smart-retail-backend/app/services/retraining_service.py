"""
Retraining Workflow Service
=============================
Orchestrates the end-to-end continuous-learning pipeline:

  Step 1 — Validate prerequisites (data volume, model artefacts)
  Step 2 — Run the ML preprocessing pipeline (feature engineering + splits)
  Step 3 — Train the GradientBoostingRegressor on updated data
  Step 4 — Evaluate the new model on the test split
  Step 5 — Compare new model vs current model (champion/challenger)
  Step 6 — Register the new version in ModelVersion table
  Step 7 — Save an evaluation record in ForecastEvaluation table
  Step 8 — Return a comprehensive retraining report

Champion / challenger logic
----------------------------
The new model is only "promoted" (registered as active) if its val_mape
is not worse than the current model's val_mape by more than REGRESSION_TOLERANCE
(default 5 %).  If the challenger is worse, it is registered as inactive
and the report explains why.  This prevents accidental degradation from a
noisy retraining run.

Thread safety
-------------
A module-level lock prevents concurrent retraining runs.  A second call
while training is running returns 409 Conflict.
"""

import logging
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from app.models.model_version import ModelVersion
from app.services.forecast_monitor_service import (
    register_model_version,
    save_evaluation_record,
    evaluate_from_test_split,
    evaluate_from_val_split,
    get_retraining_recommendation,
    _get_training_metadata,
    _r,
)

logger = logging.getLogger("smart_retail.retraining")

# ── Constants ─────────────────────────────────────────────────────────────────
REGRESSION_TOLERANCE    = 5.0    # % — challenger may be this much worse and still be promoted
MIN_TRAIN_SAMPLES       = 100    # refuse to retrain on tiny datasets
RETRAIN_LOCK            = threading.Lock()
_retraining_status: Dict[str, Any] = {
    "running":    False,
    "started_at": None,
    "step":       None,
    "result":     None,
}


# ── Public API ────────────────────────────────────────────────────────────────

def get_retraining_status() -> Dict[str, Any]:
    """Return the current retraining job status."""
    return dict(_retraining_status)


def trigger_retraining(
    db: Session,
    reason: str = "manual",
    hyperparams: Optional[Dict] = None,
    force: bool = False,
) -> Dict[str, Any]:
    """
    Synchronously run the full retraining pipeline.

    Parameters
    ----------
    db
        Database session (used for evaluation and version registration).
    reason
        Human-readable reason for retraining (stored in ModelVersion).
    hyperparams
        Optional override for GBR hyperparameters.
    force
        If True, skip the "recommendation" check and always retrain.

    Returns
    -------
    Comprehensive retraining report dict.
    """
    global _retraining_status

    # ── Concurrency guard ─────────────────────────────────────────────────────
    if not RETRAIN_LOCK.acquire(blocking=False):
        return {
            "status":  "conflict",
            "message": "A retraining run is already in progress.",
            "started_at": _retraining_status.get("started_at"),
        }

    _retraining_status.update({
        "running":    True,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "step":       "initialising",
        "result":     None,
    })

    started = time.monotonic()
    report: Dict[str, Any] = {
        "status":       "in_progress",
        "reason":       reason,
        "started_at":   _retraining_status["started_at"],
        "steps":        [],
    }

    try:
        result = _run_pipeline(db, reason, hyperparams, force, report)
        _retraining_status["result"] = result
        return result
    except Exception as exc:
        logger.exception("Retraining pipeline failed: %s", exc)
        report.update({
            "status":    "error",
            "error":     str(exc),
            "duration_seconds": round(time.monotonic() - started, 2),
        })
        _retraining_status["result"] = report
        return report
    finally:
        _retraining_status["running"] = False
        _retraining_status["step"]    = "idle"
        RETRAIN_LOCK.release()


# ── Pipeline ──────────────────────────────────────────────────────────────────

def _step(report: Dict, name: str, status: str, detail: Any = None) -> None:
    _retraining_status["step"] = name
    entry = {"step": name, "status": status}
    if detail:
        entry["detail"] = detail
    report["steps"].append(entry)
    logger.info("Retraining step [%s] — %s", name, status)


def _run_pipeline(
    db: Session,
    reason: str,
    hyperparams: Optional[Dict],
    force: bool,
    report: Dict,
) -> Dict[str, Any]:
    import time
    started = time.monotonic()

    # ── Step 1: Prerequisites ─────────────────────────────────────────────────
    _step(report, "prerequisites", "running")
    prereq = _check_prerequisites(db, force)
    _step(report, "prerequisites", prereq["status"], prereq.get("message"))
    if prereq["status"] == "failed":
        report.update({"status": "aborted", "abort_reason": prereq["message"]})
        return report

    # ── Step 2: ML preprocessing pipeline ────────────────────────────────────
    _step(report, "preprocessing", "running")
    try:
        from app.services.preprocessing_service import PreprocessingService
        pp_svc = PreprocessingService(db)
        pp_result = pp_svc.run_pipeline()
        _step(report, "preprocessing", "success", pp_result)
    except Exception as exc:
        _step(report, "preprocessing", "failed", str(exc))
        report.update({"status": "error", "error": f"Preprocessing failed: {exc}"})
        return report

    # ── Step 3: Train new model ───────────────────────────────────────────────
    _step(report, "training", "running")
    try:
        from ml.training.model_trainer import train
        train_result = train(hyperparams=hyperparams)
        if not train_result.success:
            _step(report, "training", "failed", train_result.message)
            report.update({"status": "error", "error": train_result.message})
            return report
        _step(report, "training", "success", {
            "n_train_samples": train_result.n_train_samples,
            "n_features":      train_result.n_features,
            "val_mae":         (train_result.val_metrics or {}).get("mae"),
            "val_mape":        (train_result.val_metrics or {}).get("mape"),
        })
    except Exception as exc:
        _step(report, "training", "failed", str(exc))
        report.update({"status": "error", "error": f"Training failed: {exc}"})
        return report

    # ── Step 4: Evaluate new model on test split ──────────────────────────────
    _step(report, "evaluation", "running")
    eval_result = evaluate_from_test_split(db)
    if eval_result.get("status") not in ("success",):
        # Fall back to val-split metrics from training result
        eval_result = evaluate_from_val_split(db)
    _step(report, "evaluation", "success", {
        "mape":  eval_result.get("portfolio_metrics", {}).get("mape"),
        "rmse":  eval_result.get("portfolio_metrics", {}).get("rmse"),
        "r2":    eval_result.get("portfolio_metrics", {}).get("r2"),
        "type":  eval_result.get("evaluation_type"),
    })

    # ── Step 5: Champion / challenger comparison ──────────────────────────────
    _step(report, "champion_challenger", "running")
    challenger_mape = (eval_result.get("portfolio_metrics") or {}).get("mape")
    promotion = _champion_challenger(db, challenger_mape)
    _step(report, "champion_challenger", promotion["status"], promotion)

    # ── Step 6: Register model version ───────────────────────────────────────
    _step(report, "version_registration", "running")
    try:
        version = register_model_version(
            db=db,
            training_result={
                "trained_at":      train_result.trained_at,
                "n_train_samples": train_result.n_train_samples,
                "n_val_samples":   train_result.n_val_samples,
                "n_features":      train_result.n_features,
                "train_metrics":   train_result.train_metrics,
                "val_metrics":     train_result.val_metrics,
                "model_path":      train_result.model_path,
                "hyperparams":     hyperparams or {},
            },
            retrain_reason=reason,
        )
        # If challenger was rejected, mark it inactive
        if promotion["status"] == "rejected":
            version.is_active = False
            db.commit()
        _step(report, "version_registration", "success", {"version_tag": version.version_tag})
    except Exception as exc:
        _step(report, "version_registration", "warning", str(exc))

    # ── Step 7: Save evaluation record ───────────────────────────────────────
    _step(report, "save_evaluation", "running")
    try:
        saved_eval = save_evaluation_record(
            db=db,
            eval_result=eval_result,
            triggered_by=reason,
            notes=f"Retraining run — {reason}",
        )
        _step(report, "save_evaluation", "success", {"evaluation_id": saved_eval.id})
    except Exception as exc:
        _step(report, "save_evaluation", "warning", str(exc))

    # ── Finalise ──────────────────────────────────────────────────────────────
    duration = round(time.monotonic() - started, 2)
    report.update({
        "status":           "success",
        "promoted":         promotion.get("promoted", True),
        "promotion_reason": promotion.get("reason"),
        "duration_seconds": duration,
        "new_model": {
            "version_tag":      version.version_tag if "version" in dir() else "unknown",
            "n_train_samples":  train_result.n_train_samples,
            "n_features":       train_result.n_features,
            "val_mae":          (train_result.val_metrics or {}).get("mae"),
            "val_mape":         (train_result.val_metrics or {}).get("mape"),
            "val_r2":           (train_result.val_metrics or {}).get("r2"),
        },
        "evaluation": {
            "type": eval_result.get("evaluation_type"),
            **{k: eval_result.get("portfolio_metrics", {}).get(k)
               for k in ("mae", "rmse", "mape", "smape", "r2")},
        },
        "steps_completed": len([s for s in report["steps"] if s["status"] == "success"]),
    })
    return report


# ── Helpers ───────────────────────────────────────────────────────────────────

def _check_prerequisites(db: Session, force: bool) -> Dict[str, Any]:
    """Validate data volume and (optionally) recommendation signal."""
    from sqlalchemy import func as sqlfunc
    from app.models.sales import Order

    n_orders = db.query(sqlfunc.count(Order.id)).scalar() or 0
    if n_orders < MIN_TRAIN_SAMPLES:
        return {
            "status":  "failed",
            "message": f"Insufficient data: {n_orders} orders (minimum {MIN_TRAIN_SAMPLES} required).",
        }

    if not force:
        rec = get_retraining_recommendation(db)
        if not rec["should_retrain"]:
            return {
                "status":  "failed",
                "message": (
                    "Retraining not recommended at this time. "
                    "Use force=true to override. "
                    f"Reason: {rec['message']}"
                ),
            }

    return {"status": "success", "n_orders": n_orders}


def _champion_challenger(
    db: Session,
    challenger_mape: Optional[float],
) -> Dict[str, Any]:
    """
    Compare challenger MAPE against current champion.
    Promotes automatically if challenger is better or within tolerance.
    """
    current = (
        db.query(ModelVersion)
        .filter(ModelVersion.is_active == True)  # noqa: E712
        .first()
    )
    if not current or current.val_mape is None:
        return {
            "status":   "promoted",
            "promoted": True,
            "reason":   "No existing champion — challenger promoted by default.",
        }

    if challenger_mape is None:
        return {
            "status":   "promoted",
            "promoted": True,
            "reason":   "Could not compute challenger MAPE — promoting anyway.",
        }

    champion_mape = current.val_mape
    # Positive = challenger is worse; negative = challenger is better
    relative_change = (challenger_mape - champion_mape) / champion_mape * 100

    if relative_change <= REGRESSION_TOLERANCE:
        return {
            "status":         "promoted",
            "promoted":       True,
            "champion_mape":  _r(champion_mape),
            "challenger_mape": _r(challenger_mape),
            "relative_change_pct": _r(relative_change),
            "reason": (
                f"Challenger MAPE ({challenger_mape:.2f}%) is better than or within "
                f"{REGRESSION_TOLERANCE}% of champion ({champion_mape:.2f}%)."
            ),
        }
    else:
        return {
            "status":         "rejected",
            "promoted":       False,
            "champion_mape":  _r(champion_mape),
            "challenger_mape": _r(challenger_mape),
            "relative_change_pct": _r(relative_change),
            "reason": (
                f"Challenger MAPE ({challenger_mape:.2f}%) is {relative_change:.1f}% "
                f"worse than champion ({champion_mape:.2f}%). "
                f"Champion retained (tolerance: {REGRESSION_TOLERANCE}%)."
            ),
        }
