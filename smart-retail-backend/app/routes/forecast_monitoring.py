"""
Forecast Model Monitoring & Continuous Learning API Routes
==========================================================
Exposes the complete model lifecycle management surface.

Endpoints
---------
  POST /api/forecast-monitor/evaluate              — run evaluation + save record
  GET  /api/forecast-monitor/evaluate/val-split    — val-split metrics (fast, no inference)
  GET  /api/forecast-monitor/evaluate/test-split   — test-set evaluation
  GET  /api/forecast-monitor/evaluations           — paginated evaluation history
  GET  /api/forecast-monitor/accuracy              — current accuracy report
  GET  /api/forecast-monitor/accuracy/per-product  — per-product breakdown
  GET  /api/forecast-monitor/trend                 — accuracy trend over time
  GET  /api/forecast-monitor/drift                 — drift detection report
  GET  /api/forecast-monitor/recommendation        — retraining recommendation
  POST /api/forecast-monitor/retrain               — trigger full retraining workflow
  GET  /api/forecast-monitor/retrain/status        — check retraining job status
  GET  /api/forecast-monitor/models                — model version history
  GET  /api/forecast-monitor/models/active         — current active model info

Authentication: Bearer JWT required.
Roles:
  read endpoints  — admin, manager, analyst
  evaluate        — admin, analyst
  retrain         — admin only

Prefix: /api/forecast-monitor
Tag:    Forecast Model Monitoring
"""

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, Depends, Query, status
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_active_user, require_roles
from app.database.connection import get_db
from app.models.user import User
from app.services.forecast_monitor_service import (
    evaluate_hindcast,
    evaluate_from_test_split,
    evaluate_from_val_split,
    save_evaluation_record,
    get_evaluation_history,
    get_accuracy_trend,
    get_drift_report,
    get_retraining_recommendation,
    get_per_product_accuracy,
    get_model_version_history,
    get_active_model_info,
    HINDCAST_WINDOW_DAYS,
)
from app.services.retraining_service import (
    trigger_retraining,
    get_retraining_status,
)

logger = logging.getLogger("smart_retail.forecast_monitor_routes")

router = APIRouter(
    prefix="/api/forecast-monitor",
    tags=["Forecast Model Monitoring"],
)

_READ    = require_roles("admin", "manager", "analyst")
_ANALYST = require_roles("admin", "analyst")
_ADMIN   = require_roles("admin")


# ── POST /api/forecast-monitor/evaluate ───────────────────────────────────────

@router.post("/evaluate", status_code=status.HTTP_200_OK)
def run_evaluation(
    window_days: int = Query(
        default=HINDCAST_WINDOW_DAYS, ge=7, le=180,
        description="Days of recent orders to use for hindcast evaluation"),
    save: bool = Query(default=True, description="Persist the evaluation record to the database"),
    notes: Optional[str] = Query(default=None, description="Optional notes for this evaluation run"),
    _: User = Depends(_ANALYST),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """
    **Run a hindcast evaluation** and optionally save the result.

    Hindcast method:
    1. Aggregate actual daily demand per product from the orders table
       over the last `window_days` days.
    2. Build the feature matrix for those historical dates using the same
       feature-engineering pipeline the model was trained with.
    3. Run the loaded GradientBoostingRegressor on the features.
    4. Compute MAE, RMSE, MAPE, SMAPE, R² at portfolio and per-product level.
    5. Check for drift vs the training-time baseline MAPE.

    Set `save=true` to persist a portfolio-level record to `forecast_evaluations`
    (used for trend analysis and drift history).

    Requires role: **admin** or **analyst**.
    """
    result = evaluate_hindcast(db, window_days=window_days)

    if save and result.get("status") == "success":
        saved = save_evaluation_record(db, result, triggered_by="manual", notes=notes)
        result["evaluation_id"] = saved.id
        result["saved"] = True
    else:
        result["saved"] = False

    return result


# ── GET /api/forecast-monitor/evaluate/val-split ──────────────────────────────

@router.get("/evaluate/val-split", status_code=status.HTTP_200_OK)
def val_split_metrics(
    _: User = Depends(_READ),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """
    **Validation-split metrics** — reads saved `val_metrics` from
    `training_metadata.json`.  No model inference is performed.

    Returns the accuracy numbers from the last training run (MAE, RMSE, MAPE,
    SMAPE, R², n_val_samples, hyperparams, trained_at).

    Use as a quick sanity check that the model was trained successfully and
    with acceptable validation accuracy.
    """
    return evaluate_from_val_split(db)


# ── GET /api/forecast-monitor/evaluate/test-split ─────────────────────────────

@router.get("/evaluate/test-split", status_code=status.HTTP_200_OK)
def test_split_evaluation(
    _: User = Depends(_ANALYST),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """
    **Test-split evaluation** — loads `ml/datasets/processed/test.csv`
    (the 15 % held-out set) and runs inference to compute error metrics.

    Prerequisite: run `POST /api/ml/pipeline/run` then
    `POST /api/predictions/train` before calling this endpoint.

    Returns portfolio-level and per-product metrics (MAE, RMSE, MAPE, SMAPE, R²).
    Also checks drift vs the training-time baseline.
    """
    return evaluate_from_test_split(db)


# ── GET /api/forecast-monitor/evaluations ─────────────────────────────────────

@router.get("/evaluations", status_code=status.HTTP_200_OK)
def evaluation_history(
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    evaluation_type: Optional[str] = Query(
        default=None,
        description="Filter by type: hindcast | test_split | val_split"),
    product_id: Optional[int] = Query(default=None, description="Filter by product ID"),
    drift_only: bool = Query(default=False, description="Return only drift-detected records"),
    _: User = Depends(_READ),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """
    **Paginated evaluation history** from the `forecast_evaluations` table.

    Each record contains: evaluation type, timestamp, window_days, MAE/RMSE/MAPE/SMAPE/R²,
    drift_detected, drift_score, baseline_mape, model_version, n_samples.

    Use `drift_only=true` to see only evaluations where drift was flagged.
    """
    return get_evaluation_history(
        db,
        skip=skip,
        limit=limit,
        evaluation_type=evaluation_type,
        product_id=product_id,
        drift_only=drift_only,
    )


# ── GET /api/forecast-monitor/accuracy ────────────────────────────────────────

@router.get("/accuracy", status_code=status.HTTP_200_OK)
def current_accuracy(
    window_days: int = Query(default=HINDCAST_WINDOW_DAYS, ge=7, le=180),
    _: User = Depends(_READ),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """
    **Current model accuracy report** — live hindcast evaluation.

    Runs a fresh hindcast (predictions vs actual orders for the last
    `window_days` days) and returns:
    - Portfolio MAE, RMSE, MAPE, SMAPE, R²
    - Per-product breakdown sorted by MAPE (worst first)
    - Drift status vs training-time baseline
    - Model version info

    Unlike `POST /evaluate`, this endpoint does **not** save a record.
    Use it for on-demand accuracy spot-checks.
    """
    return evaluate_hindcast(db, window_days=window_days)


# ── GET /api/forecast-monitor/accuracy/per-product ────────────────────────────

@router.get("/accuracy/per-product", status_code=status.HTTP_200_OK)
def per_product_accuracy(
    window_days: int = Query(default=HINDCAST_WINDOW_DAYS, ge=7, le=180),
    _: User = Depends(_READ),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """
    **Per-product accuracy breakdown.**

    Returns MAE, RMSE, MAPE, SMAPE, R² for each product, sorted worst-first
    by MAPE.  Identifies which products the model struggles with most.

    If recent stored per-product records exist (from a saved evaluation within
    the last 7 days), those are returned directly.  Otherwise, a live hindcast
    is run.
    """
    return get_per_product_accuracy(db, window_days=window_days)


# ── GET /api/forecast-monitor/trend ──────────────────────────────────────────

@router.get("/trend", status_code=status.HTTP_200_OK)
def accuracy_trend(
    lookback_days: int = Query(default=90, ge=14, le=365),
    evaluation_type: Optional[str] = Query(default=None),
    _: User = Depends(_READ),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """
    **Accuracy trend over time** — time-series of evaluation metrics.

    Returns a chronological series of portfolio-level MAE/RMSE/MAPE/R² from
    stored evaluation records.  Also computes an overall trend direction:
    - `improving` — MAPE slope is declining (model getting better)
    - `degrading` — MAPE slope is rising (model getting worse)
    - `stable` — flat or insufficient data

    Requires at least 3 stored evaluation records for trend computation.
    Use `POST /evaluate` with `save=true` periodically to build up history.
    """
    return get_accuracy_trend(db, lookback_days=lookback_days, evaluation_type=evaluation_type)


# ── GET /api/forecast-monitor/drift ──────────────────────────────────────────

@router.get("/drift", status_code=status.HTTP_200_OK)
def drift_report(
    _: User = Depends(_READ),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """
    **Model drift detection report.**

    Compares the most recent evaluation MAPE against the baseline MAPE
    (from training time).

    Drift classification:
    - `healthy` — MAPE degradation < 20%
    - `warning` — MAPE degradation 20–30%
    - `drift`   — MAPE degradation ≥ 30% (action required)

    Also returns the last 10 drift-detected evaluation records and a
    plain-language recommendation.

    Thresholds: warning=20%, drift=30% (relative to baseline MAPE).
    """
    return get_drift_report(db)


# ── GET /api/forecast-monitor/recommendation ──────────────────────────────────

@router.get("/recommendation", status_code=status.HTTP_200_OK)
def retraining_recommendation(
    _: User = Depends(_READ),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """
    **Rule-based retraining recommendation.**

    Evaluates 4 signals and returns a recommendation level:
    - `none`      — all clear, no action needed
    - `suggest`   — consider retraining (new data available)
    - `recommend` — retraining advised (staleness or warning drift)
    - `urgent`    — retrain immediately (drift detected or poor accuracy)

    Signals:
    1. **Drift detected** — MAPE > 30% above training baseline
    2. **Poor accuracy** — current MAPE > 25%
    3. **Model staleness** — last training > 30 days ago
    4. **New data available** — > 25% more orders than at training time

    `should_retrain=true` when level is `recommend` or `urgent`.
    """
    return get_retraining_recommendation(db)


# ── POST /api/forecast-monitor/retrain ────────────────────────────────────────

@router.post("/retrain", status_code=status.HTTP_200_OK)
def retrain_model(
    reason: str = Query(
        default="manual",
        description="Reason for retraining (stored in version registry)"),
    force: bool = Query(
        default=False,
        description="Skip recommendation check and force retraining"),
    n_estimators: Optional[int]   = Query(default=None, ge=50, le=1000),
    learning_rate: Optional[float]= Query(default=None, ge=0.001, le=1.0),
    max_depth: Optional[int]      = Query(default=None, ge=1, le=10),
    _: User = Depends(_ADMIN),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """
    **Trigger the full retraining pipeline.** Requires role: **admin**.

    Pipeline steps:
    1. Validate prerequisites (data volume, recommendation signal)
    2. Run ML preprocessing (feature engineering + train/val/test splits)
    3. Train GradientBoostingRegressor with optional custom hyperparameters
    4. Evaluate on test split (MAE/RMSE/MAPE/SMAPE/R²)
    5. Champion/challenger comparison — new model must not be worse than
       current by more than 5% MAPE to be promoted
    6. Register model version in the DB registry
    7. Save evaluation record

    Returns a step-by-step execution report.

    Use `force=true` to bypass the recommendation check and retrain even if
    the model appears healthy (useful for scheduled periodic retraining).

    **Concurrent retraining**: a second call while retraining is running
    returns HTTP 409 Conflict.
    """
    hyperparams = {}
    if n_estimators  is not None: hyperparams["n_estimators"]  = n_estimators
    if learning_rate is not None: hyperparams["learning_rate"] = learning_rate
    if max_depth     is not None: hyperparams["max_depth"]     = max_depth

    result = trigger_retraining(
        db=db,
        reason=reason,
        hyperparams=hyperparams or None,
        force=force,
    )

    if result.get("status") == "conflict":
        from fastapi import HTTPException
        raise HTTPException(status_code=409, detail=result["message"])

    return result


# ── GET /api/forecast-monitor/retrain/status ─────────────────────────────────

@router.get("/retrain/status", status_code=status.HTTP_200_OK)
def retraining_status(
    _: User = Depends(_READ),
) -> Dict[str, Any]:
    """
    **Check the current retraining job status.**

    Returns `running`, `step` (current pipeline step), `started_at`,
    and the final `result` (populated when the job completes).

    Poll this endpoint after triggering a retraining run to follow progress.
    """
    return get_retraining_status()


# ── GET /api/forecast-monitor/models ─────────────────────────────────────────

@router.get("/models", status_code=status.HTTP_200_OK)
def model_version_history(
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
    _: User = Depends(_READ),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """
    **Model version registry** — history of all training runs.

    Each version record includes:
    - `version_tag` — ISO-timestamp-based unique ID
    - `trained_at`, `n_train_samples`, `n_features`
    - Train metrics: `train_mae`, `train_rmse`, `train_r2`
    - Validation metrics: `val_mae`, `val_rmse`, `val_mape`, `val_smape`, `val_r2`
    - `is_active` — True for the currently deployed model
    - `retired_at`, `retrain_reason`
    - `model_path` — filesystem path to the artefact

    Sorted by `trained_at` descending (newest first).
    """
    return get_model_version_history(db, skip=skip, limit=limit)


# ── GET /api/forecast-monitor/models/active ──────────────────────────────────

@router.get("/models/active", status_code=status.HTTP_200_OK)
def active_model_info(
    _: User = Depends(_READ),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """
    **Currently active model metadata.**

    Returns version tag, trained_at, n_features, and validation metrics
    (MAE/RMSE/MAPE/R²) for the model currently serving predictions.

    Sources in priority order:
    1. DB `model_versions` table (if a version has been registered via this API)
    2. `ml/saved_models/training_metadata.json` (if the model was trained
       directly via `/api/predictions/train` without using the monitor API)
    3. `{ "source": "none", "message": "No trained model found." }`
    """
    return get_active_model_info(db)
