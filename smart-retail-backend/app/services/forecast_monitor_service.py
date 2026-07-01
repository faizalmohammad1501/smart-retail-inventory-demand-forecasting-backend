"""
Forecast Model Monitoring Service
====================================
Evaluates the demand forecasting model's accuracy against real sales data,
tracks performance over time, and detects model degradation (drift).

Evaluation strategy
-------------------
Three evaluation modes are supported, applied in priority order:

1. **Hindcast** (primary, live)
   Loads recent orders from the DB, re-builds the feature matrix for those
   historical dates, runs the current model, and compares predictions to
   actual quantities.  This is the most operationally relevant evaluation
   because it uses real production data that arrived after the model was
   trained.

2. **Test-split** (offline, data-file-based)
   Loads `ml/datasets/processed/test.csv` (the held-out 15 % split from the
   preprocessing pipeline) and evaluates the model on it.  Available only
   after the ML pipeline has been run.

3. **Val-split** (fast, from saved metadata)
   Reads the `val_metrics` block from `ml/saved_models/training_metadata.json`.
   No model inference is run — it simply surfaces the numbers saved at training
   time.  Useful as a quick sanity check.

Metrics computed
----------------
  MAE   — Mean Absolute Error (same unit as demand quantity)
  RMSE  — Root Mean Squared Error (penalises large errors)
  MAPE  — Mean Absolute Percentage Error (%, scale-independent)
  SMAPE — Symmetric MAPE (%, bounded, handles zero actuals better than MAPE)
  R²    — Coefficient of determination (1.0 = perfect, 0 = mean-baseline)

Drift detection
---------------
Compares the current evaluation MAPE against the model's baseline MAPE
(val_mape stored in ModelVersion or training_metadata.json).

  drift_score = (current_mape - baseline_mape) / baseline_mape × 100

Thresholds:
  drift_score ≥ 30 %  → drift_detected = True
  drift_score ≥ 20 %  → "warning" (does not set drift flag, but flagged in report)

Retraining recommendation
--------------------------
Rule-based engine using 4 signals:
  1. Drift detected (MAPE degraded > 30 %)
  2. Absolute MAPE > 25 % (below acceptable accuracy)
  3. Days since last training > 30 days
  4. New data volume: > 25 % more order records since last training
Any one signal triggers a "recommend" level; all four → "urgent".
"""

import json
import logging
import math
import statistics
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.forecast_evaluation import ForecastEvaluation
from app.models.model_version import ModelVersion
from app.models.product import Product
from app.models.sales import Order

logger = logging.getLogger("smart_retail.forecast_monitor")

# ── Constants ─────────────────────────────────────────────────────────────────
DRIFT_THRESHOLD_PCT      = 30.0    # % MAPE increase → drift
DRIFT_WARNING_PCT        = 20.0    # % MAPE increase → warning
MAPE_ACCEPTABLE_MAX      = 25.0    # % — MAPE above this triggers recommendation
RETRAIN_STALENESS_DAYS   = 30      # days since training → staleness signal
NEW_DATA_THRESHOLD_PCT   = 25.0    # % more records than training set → data signal
HINDCAST_WINDOW_DAYS     = 30      # default hindcast evaluation window


# ── Helpers ───────────────────────────────────────────────────────────────────

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _r(v, n: int = 4) -> float:
    return round(float(v), n) if v is not None else 0.0


def _window(days: int):
    end = _now()
    return end - timedelta(days=days), end


def _compute_metrics(
    y_true: List[float],
    y_pred: List[float],
) -> Dict[str, float]:
    """
    Compute MAE, RMSE, MAPE, SMAPE, R² from paired lists.
    Filters pairs where both values are finite.
    """
    pairs = [
        (a, p) for a, p in zip(y_true, y_pred)
        if math.isfinite(a) and math.isfinite(p)
    ]
    if len(pairs) < 2:
        return {"mae": 0, "rmse": 0, "mape": 0, "smape": 0, "r2": 0, "n_samples": len(pairs)}

    actuals = [p[0] for p in pairs]
    preds   = [p[1] for p in pairs]

    n    = len(actuals)
    mae  = _r(sum(abs(a - p) for a, p in zip(actuals, preds)) / n)
    rmse = _r(math.sqrt(sum((a - p) ** 2 for a, p in zip(actuals, preds)) / n))

    # MAPE — skip where actual == 0 to avoid division by zero
    mape_terms = [abs(a - p) / a * 100 for a, p in zip(actuals, preds) if a != 0]
    mape = _r(sum(mape_terms) / len(mape_terms)) if mape_terms else 0.0

    # SMAPE — symmetric, bounded [0, 200]
    smape_terms = [
        2 * abs(a - p) / (abs(a) + abs(p)) * 100
        for a, p in zip(actuals, preds)
        if (abs(a) + abs(p)) != 0
    ]
    smape = _r(sum(smape_terms) / len(smape_terms)) if smape_terms else 0.0

    # R²
    mean_actual = statistics.mean(actuals)
    ss_res = sum((a - p) ** 2 for a, p in zip(actuals, preds))
    ss_tot = sum((a - mean_actual) ** 2 for a in actuals)
    r2 = _r(1 - ss_res / ss_tot) if ss_tot > 0 else 0.0

    return {
        "mae":      mae,
        "rmse":     rmse,
        "mape":     mape,
        "smape":    smape,
        "r2":       r2,
        "n_samples": n,
    }


def _get_training_metadata() -> Dict[str, Any]:
    """Load training_metadata.json from saved_models dir. Returns {} on failure."""
    try:
        from ml.config import SAVED_MODELS_DIR
        path = Path(SAVED_MODELS_DIR) / "training_metadata.json"
        if path.exists():
            with open(path) as f:
                return json.load(f)
    except Exception as exc:
        logger.warning("Could not read training_metadata.json: %s", exc)
    return {}


def _get_baseline_mape() -> Optional[float]:
    """Return the val_mape from the saved training metadata, or None."""
    meta = _get_training_metadata()
    val = meta.get("val_metrics") or {}
    mape = val.get("mape")
    return float(mape) if mape is not None else None


# ── 1. HINDCAST EVALUATION ────────────────────────────────────────────────────

def evaluate_hindcast(
    db: Session,
    window_days: int = HINDCAST_WINDOW_DAYS,
    product_ids: Optional[List[int]] = None,
) -> Dict[str, Any]:
    """
    Hindcast evaluation: compare model predictions vs actual order quantities
    over the most recent `window_days` days.

    Method:
    1. Aggregate actual daily demand per product from the orders table.
    2. Build a feature DataFrame for those historical dates using the same
       feature-engineering functions the training pipeline uses.
    3. Run the loaded model predictor on the feature matrix.
    4. Compute error metrics at product and portfolio level.

    Returns a full evaluation dict.  Call `save_evaluation_record()` to
    persist it.
    """
    try:
        from ml.prediction.predictor import ModelPredictor
        from ml.preprocessing.feature_engineer import (
            build_feature_dataframe_for_product,
            get_feature_columns,
        )
        predictor = ModelPredictor.load()
    except Exception as exc:
        logger.warning("ModelPredictor unavailable: %s — falling back to val-split", exc)
        return evaluate_from_val_split(db)

    if predictor is None:
        logger.info("No trained model found — using val-split evaluation")
        return evaluate_from_val_split(db)

    start, end = _window(window_days)

    # ── Actual daily demand from orders table ─────────────────────────────────
    demand_rows = (
        db.query(
            Order.product_id,
            func.date(Order.order_placed_at).label("day"),
            func.sum(Order.quantity).label("qty"),
        )
        .filter(Order.order_placed_at.between(start, end))
        .group_by(Order.product_id, func.date(Order.order_placed_at))
        .all()
    )

    if not demand_rows:
        return {
            "evaluation_type": "hindcast",
            "status":          "no_data",
            "message":         f"No orders found in the last {window_days} days.",
            "window_days":     window_days,
        }

    # Organise by product
    by_pid: Dict[int, List[Tuple]] = defaultdict(list)
    for pid, day, qty in demand_rows:
        if product_ids and pid not in product_ids:
            continue
        by_pid[pid].append((str(day), float(qty or 0)))

    product_map = {p.id: p for p in db.query(Product).all()}

    all_actuals: List[float] = []
    all_preds:   List[float] = []
    per_product: List[Dict]  = []

    for pid, series in by_pid.items():
        product = product_map.get(pid)
        if not product:
            continue

        dates   = [s[0] for s in series]
        actuals = [s[1] for s in series]

        # Build features for these historical dates
        try:
            feat_df = _build_product_features(pid, dates, db)
            if feat_df is None or feat_df.empty:
                continue
            raw_preds = predictor.predict(feat_df)
            preds = [max(0.0, float(p)) for p in raw_preds]
        except Exception as exc:
            logger.debug("Feature build failed for product %d: %s", pid, exc)
            continue

        if len(preds) != len(actuals):
            # Trim to the shorter
            n = min(len(preds), len(actuals))
            preds   = preds[:n]
            actuals = actuals[:n]

        if not preds:
            continue

        metrics = _compute_metrics(actuals, preds)
        per_product.append({
            "product_id":   pid,
            "product_name": product.product_name,
            "sku":          product.sku,
            "category":     product.category,
            **metrics,
            "sample_pairs": list(zip(
                [round(a, 2) for a in actuals[:5]],
                [round(p, 2) for p in preds[:5]],
            )),  # first 5 pairs for inspection
        })
        all_actuals.extend(actuals)
        all_preds.extend(preds)

    if not all_actuals:
        return {
            "evaluation_type": "hindcast",
            "status":          "insufficient_data",
            "message":         "Could not build features for any product.",
            "window_days":     window_days,
        }

    portfolio_metrics = _compute_metrics(all_actuals, all_preds)
    per_product.sort(key=lambda r: r["mape"], reverse=True)

    # Drift check
    baseline_mape = _get_baseline_mape()
    drift_score, drift_detected = _check_drift(portfolio_metrics["mape"], baseline_mape)

    return {
        "evaluation_type":   "hindcast",
        "status":            "success",
        "evaluated_at":      _now().isoformat(),
        "window_days":       window_days,
        "model_version":     _get_model_version_tag(),
        "portfolio_metrics": portfolio_metrics,
        "n_products":        len(per_product),
        "baseline_mape":     baseline_mape,
        "drift_detected":    drift_detected,
        "drift_score":       _r(drift_score) if drift_score is not None else None,
        "per_product":       per_product,
    }


# ── 2. TEST-SPLIT EVALUATION ──────────────────────────────────────────────────

def evaluate_from_test_split(db: Session) -> Dict[str, Any]:
    """
    Evaluate model on the held-out test.csv from the preprocessing pipeline.
    Returns the same structure as hindcast evaluation.
    """
    try:
        from ml.config import PROCESSED_DIR, TARGET_COLUMN
        from ml.prediction.predictor import ModelPredictor
        from ml.preprocessing.feature_engineer import get_feature_columns

        test_path = Path(PROCESSED_DIR) / "test.csv"
        if not test_path.exists():
            return {
                "evaluation_type": "test_split",
                "status":          "no_data",
                "message":         "test.csv not found. Run /api/ml/pipeline/run first.",
            }

        predictor = ModelPredictor.load()
        if predictor is None:
            return {
                "evaluation_type": "test_split",
                "status":          "no_model",
                "message":         "No trained model. Run /api/predictions/train first.",
            }

        df = pd.read_csv(test_path)
        feature_cols = get_feature_columns(df)
        if not feature_cols or TARGET_COLUMN not in df.columns:
            return {
                "evaluation_type": "test_split",
                "status":          "invalid_data",
                "message":         "test.csv has missing feature or target columns.",
            }

        X  = df[feature_cols].fillna(0).astype(float)
        y  = df[TARGET_COLUMN].astype(float).tolist()
        y_hat = [max(0.0, float(p)) for p in predictor.predict(X)]

        metrics = _compute_metrics(y, y_hat)

        # Per-product breakdown if product_id column exists
        per_product = []
        if "product_id" in df.columns:
            product_map = {p.id: p for p in db.query(Product).all()}
            for pid, grp in df.groupby("product_id"):
                X_p   = grp[feature_cols].fillna(0).astype(float)
                y_p   = grp[TARGET_COLUMN].astype(float).tolist()
                yh_p  = [max(0.0, float(v)) for v in predictor.predict(X_p)]
                pm    = _compute_metrics(y_p, yh_p)
                prod  = product_map.get(int(pid))
                per_product.append({
                    "product_id":   int(pid),
                    "product_name": prod.product_name if prod else f"Product {pid}",
                    "sku":          prod.sku          if prod else "",
                    "category":     prod.category     if prod else "",
                    **pm,
                })
            per_product.sort(key=lambda r: r["mape"], reverse=True)

        baseline_mape = _get_baseline_mape()
        drift_score, drift_detected = _check_drift(metrics["mape"], baseline_mape)

        return {
            "evaluation_type":   "test_split",
            "status":            "success",
            "evaluated_at":      _now().isoformat(),
            "window_days":       None,
            "model_version":     _get_model_version_tag(),
            "portfolio_metrics": metrics,
            "n_products":        len(per_product),
            "baseline_mape":     baseline_mape,
            "drift_detected":    drift_detected,
            "drift_score":       _r(drift_score) if drift_score is not None else None,
            "per_product":       per_product,
        }
    except Exception as exc:
        logger.exception("Test-split evaluation failed: %s", exc)
        return {
            "evaluation_type": "test_split",
            "status":          "error",
            "message":         str(exc),
        }


# ── 3. VAL-SPLIT FROM METADATA ────────────────────────────────────────────────

def evaluate_from_val_split(db: Session) -> Dict[str, Any]:
    """
    Return the val_metrics block from training_metadata.json as an
    EvaluationResult dict.  Fast — no model inference.
    """
    meta = _get_training_metadata()
    if not meta:
        return {
            "evaluation_type": "val_split",
            "status":          "no_metadata",
            "message":         "training_metadata.json not found. Train a model first.",
        }

    val  = meta.get("val_metrics") or {}
    train= meta.get("train_metrics") or {}
    metrics = {
        "mae":      _r(val.get("mae",  0)),
        "rmse":     _r(val.get("rmse", 0)),
        "mape":     _r(val.get("mape", 0)),
        "smape":    _r(val.get("smape", 0)),
        "r2":       _r(val.get("r2",   0)),
        "n_samples": meta.get("n_val_samples", 0),
    }

    return {
        "evaluation_type":   "val_split",
        "status":            "success",
        "evaluated_at":      meta.get("trained_at"),
        "window_days":       None,
        "model_version":     meta.get("trained_at", "unknown"),
        "portfolio_metrics": metrics,
        "train_metrics":     {k: _r(train.get(k, 0)) for k in ("mae","rmse","r2")},
        "hyperparams":       meta.get("hyperparams", {}),
        "n_features":        meta.get("n_features", 0),
        "n_train_samples":   meta.get("n_train_samples", 0),
        "n_products":        0,
        "baseline_mape":     metrics["mape"],
        "drift_detected":    False,
        "drift_score":       0.0,
        "per_product":       [],
    }


# ── 4. SAVE EVALUATION RECORD ─────────────────────────────────────────────────

def save_evaluation_record(
    db: Session,
    eval_result: Dict[str, Any],
    triggered_by: str = "manual",
    notes: Optional[str] = None,
) -> ForecastEvaluation:
    """
    Persist a portfolio-level evaluation result to the forecast_evaluations table.
    Returns the saved ORM object.
    """
    m = eval_result.get("portfolio_metrics", {})
    record = ForecastEvaluation(
        evaluated_at           = _now(),
        evaluation_window_days = eval_result.get("window_days") or 0,
        n_samples              = m.get("n_samples", 0),
        n_products             = eval_result.get("n_products", 0),
        mae                    = m.get("mae"),
        rmse                   = m.get("rmse"),
        mape                   = m.get("mape"),
        smape                  = m.get("smape"),
        r2                     = m.get("r2"),
        drift_detected         = eval_result.get("drift_detected", False),
        drift_score            = eval_result.get("drift_score"),
        baseline_mape          = eval_result.get("baseline_mape"),
        model_version          = eval_result.get("model_version"),
        evaluation_type        = eval_result.get("evaluation_type", "hindcast"),
        triggered_by           = triggered_by,
        notes                  = notes,
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    logger.info(
        "Evaluation saved: id=%d type=%s mape=%.2f drift=%s",
        record.id, record.evaluation_type, record.mape or 0, record.drift_detected,
    )
    return record


# ── 5. GET EVALUATION HISTORY ─────────────────────────────────────────────────

def get_evaluation_history(
    db: Session,
    skip: int = 0,
    limit: int = 50,
    evaluation_type: Optional[str] = None,
    product_id: Optional[int] = None,
    drift_only: bool = False,
) -> Dict[str, Any]:
    """Paginated history of past evaluations."""
    q = db.query(ForecastEvaluation)
    if evaluation_type:
        q = q.filter(ForecastEvaluation.evaluation_type == evaluation_type)
    if product_id is not None:
        q = q.filter(ForecastEvaluation.product_id == product_id)
    if drift_only:
        q = q.filter(ForecastEvaluation.drift_detected == True)  # noqa: E712

    total = q.count()
    rows  = q.order_by(ForecastEvaluation.evaluated_at.desc()).offset(skip).limit(limit).all()

    return {
        "total": total,
        "skip":  skip,
        "limit": limit,
        "evaluations": [_serialize_eval(r) for r in rows],
    }


# ── 6. ACCURACY TREND ─────────────────────────────────────────────────────────

def get_accuracy_trend(
    db: Session,
    lookback_days: int = 90,
    evaluation_type: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Time-series of model accuracy metrics over the lookback window.

    Returns chronological list of (evaluated_at, mae, rmse, mape, r2) plus
    trend direction (improving / degrading / stable) based on linear regression
    slope of MAPE values.
    """
    start, _ = _window(lookback_days)
    q = (
        db.query(ForecastEvaluation)
        .filter(
            ForecastEvaluation.evaluated_at >= start,
            ForecastEvaluation.product_id.is_(None),  # portfolio-level only
        )
    )
    if evaluation_type:
        q = q.filter(ForecastEvaluation.evaluation_type == evaluation_type)

    rows = q.order_by(ForecastEvaluation.evaluated_at.asc()).all()

    series = [_serialize_eval(r) for r in rows]
    mapes  = [r["mape"] for r in series if r["mape"] is not None]

    # Trend via simple linear slope
    trend = "stable"
    if len(mapes) >= 3:
        n     = len(mapes)
        x_mean = (n - 1) / 2
        y_mean = statistics.mean(mapes)
        num   = sum((i - x_mean) * (m - y_mean) for i, m in enumerate(mapes))
        den   = sum((i - x_mean) ** 2 for i in range(n))
        slope = num / den if den else 0
        if slope > 0.1:
            trend = "degrading"
        elif slope < -0.1:
            trend = "improving"

    return {
        "lookback_days":    lookback_days,
        "total_evaluations": len(series),
        "trend":            trend,
        "series":           series,
        "latest":           series[-1] if series else None,
        "best_mape":        min(mapes) if mapes else None,
        "worst_mape":       max(mapes) if mapes else None,
    }


# ── 7. DRIFT REPORT ───────────────────────────────────────────────────────────

def get_drift_report(db: Session, window_days: int = 30) -> Dict[str, Any]:
    """
    Current drift status based on the most recent hindcast evaluation.
    If no stored evaluation exists, runs a fresh hindcast.
    """
    latest = (
        db.query(ForecastEvaluation)
        .filter(
            ForecastEvaluation.product_id.is_(None),
            ForecastEvaluation.evaluation_type == "hindcast",
        )
        .order_by(ForecastEvaluation.evaluated_at.desc())
        .first()
    )

    if latest:
        current_mape   = latest.mape or 0.0
        baseline_mape  = latest.baseline_mape or _get_baseline_mape() or 0.0
        drift_score    = latest.drift_score or 0.0
        drift_detected = latest.drift_detected
        evaluated_at   = latest.evaluated_at.isoformat()
    else:
        baseline_mape = _get_baseline_mape() or 0.0
        current_mape  = baseline_mape
        drift_score   = 0.0
        drift_detected= False
        evaluated_at  = None

    # Drift history
    drift_events = (
        db.query(ForecastEvaluation)
        .filter(ForecastEvaluation.drift_detected == True)  # noqa: E712
        .order_by(ForecastEvaluation.evaluated_at.desc())
        .limit(10)
        .all()
    )

    return {
        "current_mape":     _r(current_mape),
        "baseline_mape":    _r(baseline_mape),
        "drift_score_pct":  _r(drift_score),
        "drift_detected":   drift_detected,
        "drift_threshold_pct": DRIFT_THRESHOLD_PCT,
        "drift_warning_pct":   DRIFT_WARNING_PCT,
        "status": (
            "drift" if drift_detected else
            "warning" if drift_score and drift_score >= DRIFT_WARNING_PCT else
            "healthy"
        ),
        "last_evaluated_at": evaluated_at,
        "recent_drift_events": [_serialize_eval(e) for e in drift_events],
        "recommendation": (
            "Model drift detected — retraining is strongly recommended."
            if drift_detected else
            "Accuracy is degrading — monitor closely and consider retraining."
            if drift_score and drift_score >= DRIFT_WARNING_PCT else
            "Model accuracy is within acceptable bounds."
        ),
    }


# ── 8. RETRAINING RECOMMENDATION ─────────────────────────────────────────────

def get_retraining_recommendation(db: Session) -> Dict[str, Any]:
    """
    Rule-based engine that outputs a retraining recommendation.

    Signals checked:
      1. drift_detected     — MAPE > 30 % above baseline
      2. poor_accuracy      — current MAPE > 25 %
      3. stale_model        — last training > 30 days ago
      4. new_data_available — > 25 % more orders since last training run
    """
    meta = _get_training_metadata()
    signals: List[Dict] = []
    recommend_level = "none"

    # ── Signal 1: drift ───────────────────────────────────────────────────────
    drift_rec = get_drift_report(db)
    if drift_rec["drift_detected"]:
        signals.append({
            "signal":      "drift_detected",
            "description": f"MAPE has degraded {drift_rec['drift_score_pct']:.1f}% above baseline.",
            "severity":    "critical",
        })
        recommend_level = "urgent"
    elif drift_rec["status"] == "warning":
        signals.append({
            "signal":      "drift_warning",
            "description": f"MAPE is {drift_rec['drift_score_pct']:.1f}% above baseline (warning threshold).",
            "severity":    "warning",
        })
        if recommend_level == "none":
            recommend_level = "recommend"

    # ── Signal 2: poor absolute accuracy ─────────────────────────────────────
    latest_eval = (
        db.query(ForecastEvaluation)
        .filter(ForecastEvaluation.product_id.is_(None))
        .order_by(ForecastEvaluation.evaluated_at.desc())
        .first()
    )
    if latest_eval and latest_eval.mape and latest_eval.mape > MAPE_ACCEPTABLE_MAX:
        signals.append({
            "signal":      "poor_accuracy",
            "description": f"Current MAPE {latest_eval.mape:.1f}% exceeds acceptable threshold of {MAPE_ACCEPTABLE_MAX}%.",
            "severity":    "critical",
        })
        recommend_level = "urgent"

    # ── Signal 3: model staleness ─────────────────────────────────────────────
    trained_at_str = meta.get("trained_at")
    days_since_training = None
    if trained_at_str:
        try:
            trained_at = datetime.fromisoformat(trained_at_str.replace("Z", "+00:00"))
            if trained_at.tzinfo is None:
                trained_at = trained_at.replace(tzinfo=timezone.utc)
            days_since_training = (_now() - trained_at).days
            if days_since_training > RETRAIN_STALENESS_DAYS:
                signals.append({
                    "signal":      "stale_model",
                    "description": f"Model was trained {days_since_training} days ago (threshold: {RETRAIN_STALENESS_DAYS} days).",
                    "severity":    "warning",
                })
                if recommend_level == "none":
                    recommend_level = "recommend"
        except Exception:
            pass

    # ── Signal 4: new data volume ─────────────────────────────────────────────
    n_train = meta.get("n_train_samples", 0)
    if n_train > 0:
        current_orders = db.query(func.count(Order.id)).scalar() or 0
        new_data_pct = (current_orders - n_train) / n_train * 100
        if new_data_pct > NEW_DATA_THRESHOLD_PCT:
            signals.append({
                "signal":      "new_data_available",
                "description": f"{new_data_pct:.0f}% more records available since last training ({current_orders:,} vs {n_train:,}).",
                "severity":    "info",
            })
            if recommend_level == "none":
                recommend_level = "suggest"

    return {
        "recommendation_level": recommend_level,
        "should_retrain":       recommend_level in ("recommend", "urgent"),
        "urgency":              recommend_level,
        "signals":              signals,
        "signal_count":         len(signals),
        "days_since_training":  days_since_training,
        "last_trained_at":      trained_at_str,
        "current_mape":         drift_rec["current_mape"],
        "baseline_mape":        drift_rec["baseline_mape"],
        "message": (
            "URGENT: Retrain immediately — model accuracy has significantly degraded."
            if recommend_level == "urgent" else
            "RECOMMENDED: Retraining advised based on performance signals."
            if recommend_level == "recommend" else
            "SUGGESTED: Consider retraining when convenient."
            if recommend_level == "suggest" else
            "Model performance is acceptable. No retraining required at this time."
        ),
    }


# ── 9. MODEL VERSION REGISTRY ─────────────────────────────────────────────────

def register_model_version(
    db: Session,
    training_result: Dict[str, Any],
    retrain_reason: Optional[str] = None,
) -> ModelVersion:
    """
    Register a new model version from a TrainingResult dict.
    Retires the previously active version.
    """
    import json as _json
    # Retire current active version
    db.query(ModelVersion).filter(ModelVersion.is_active == True).update(  # noqa: E712
        {"is_active": False, "retired_at": _now()},
        synchronize_session=False,
    )
    db.flush()

    trained_at = training_result.get("trained_at", _now().isoformat())
    # Build a unique version tag
    tag = trained_at[:19].replace(":", "").replace("-", "").replace("T", "_")

    train_m = training_result.get("train_metrics") or {}
    val_m   = training_result.get("val_metrics")   or {}

    version = ModelVersion(
        version_tag     = tag,
        trained_at      = _now(),
        n_train_samples = training_result.get("n_train_samples"),
        n_val_samples   = training_result.get("n_val_samples"),
        n_features      = training_result.get("n_features"),
        hyperparams     = _json.dumps(training_result.get("hyperparams", {})),
        train_mae       = train_m.get("mae"),
        train_rmse      = train_m.get("rmse"),
        train_r2        = train_m.get("r2"),
        val_mae         = val_m.get("mae"),
        val_rmse        = val_m.get("rmse"),
        val_mape        = val_m.get("mape"),
        val_smape       = val_m.get("smape"),
        val_r2          = val_m.get("r2"),
        model_path      = training_result.get("model_path"),
        retrain_reason  = retrain_reason,
        is_active       = True,
    )
    db.add(version)
    db.commit()
    db.refresh(version)
    logger.info("Model version registered: %s", version.version_tag)
    return version


def get_model_version_history(
    db: Session,
    skip: int = 0,
    limit: int = 20,
) -> Dict[str, Any]:
    """Paginated model version registry."""
    total   = db.query(func.count(ModelVersion.id)).scalar() or 0
    rows    = (
        db.query(ModelVersion)
        .order_by(ModelVersion.trained_at.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )
    return {
        "total": total,
        "skip":  skip,
        "limit": limit,
        "versions": [_serialize_version(v) for v in rows],
    }


def get_active_model_info(db: Session) -> Dict[str, Any]:
    """Return metadata for the currently active model version."""
    version = (
        db.query(ModelVersion)
        .filter(ModelVersion.is_active == True)  # noqa: E712
        .first()
    )
    meta = _get_training_metadata()
    if version:
        return {
            "source":           "db_registry",
            "version_tag":      version.version_tag,
            "trained_at":       version.trained_at.isoformat(),
            "n_train_samples":  version.n_train_samples,
            "n_features":       version.n_features,
            "val_mae":          version.val_mae,
            "val_rmse":         version.val_rmse,
            "val_mape":         version.val_mape,
            "val_r2":           version.val_r2,
            "model_path":       version.model_path,
        }
    elif meta:
        return {
            "source":           "metadata_file",
            "version_tag":      meta.get("trained_at", "unknown"),
            "trained_at":       meta.get("trained_at"),
            "n_train_samples":  meta.get("n_train_samples"),
            "n_features":       meta.get("n_features"),
            **{f"val_{k}": v for k, v in (meta.get("val_metrics") or {}).items()},
        }
    else:
        return {"source": "none", "message": "No trained model found."}


# ── Per-product accuracy report ───────────────────────────────────────────────

def get_per_product_accuracy(
    db: Session,
    window_days: int = HINDCAST_WINDOW_DAYS,
) -> Dict[str, Any]:
    """
    Per-product accuracy metrics from recent evaluations stored in the DB.
    Falls back to a fresh hindcast if no stored per-product records exist.
    """
    # Check if we have recent per-product records
    recent = (
        db.query(ForecastEvaluation)
        .filter(
            ForecastEvaluation.product_id.isnot(None),
            ForecastEvaluation.evaluated_at >= _now() - timedelta(days=7),
        )
        .order_by(ForecastEvaluation.evaluated_at.desc())
        .limit(200)
        .all()
    )
    if recent:
        # Aggregate latest per product
        latest_by_pid: Dict[int, ForecastEvaluation] = {}
        for r in recent:
            pid = r.product_id
            if pid not in latest_by_pid:
                latest_by_pid[pid] = r

        product_map = {p.id: p for p in db.query(Product).all()}
        items = []
        for pid, r in latest_by_pid.items():
            p = product_map.get(pid)
            items.append({
                "product_id":   pid,
                "product_name": p.product_name if p else f"Product {pid}",
                "sku":          p.sku          if p else "",
                "category":     p.category     if p else "",
                "mae":          r.mae,
                "rmse":         r.rmse,
                "mape":         r.mape,
                "smape":        r.smape,
                "r2":           r.r2,
                "evaluated_at": r.evaluated_at.isoformat(),
            })
        items.sort(key=lambda x: (x["mape"] or 0), reverse=True)
        return {
            "source":      "stored_records",
            "window_days": window_days,
            "n_products":  len(items),
            "products":    items,
        }

    # Fall back to live hindcast per-product data
    result = evaluate_hindcast(db, window_days=window_days)
    per_product = result.get("per_product", [])
    return {
        "source":      "live_hindcast",
        "window_days": window_days,
        "n_products":  len(per_product),
        "products":    per_product,
    }


# ── Internal helpers ──────────────────────────────────────────────────────────

def _build_product_features(
    product_id: int,
    dates: List[str],
    db: Session,
) -> Optional[pd.DataFrame]:
    """
    Build feature rows for a product on specific historical dates.
    Uses the same feature engineering functions as the training pipeline.
    """
    try:
        from ml.preprocessing.feature_engineer import build_feature_dataframe_for_product
        return build_feature_dataframe_for_product(
            product_id=product_id,
            dates=dates,
            db=db,
        )
    except (ImportError, AttributeError):
        # Fallback: build minimal time features
        return _build_minimal_time_features(product_id, dates, db)


def _build_minimal_time_features(
    product_id: int,
    dates: List[str],
    db: Session,
) -> Optional[pd.DataFrame]:
    """Minimal feature set when the full feature engineer is not available."""
    from ml.config import CATEGORY_ENCODING
    product = db.query(Product).filter(Product.id == product_id).first()
    if not product:
        return None

    rows = []
    for date_str in dates:
        try:
            d = datetime.strptime(date_str, "%Y-%m-%d")
        except ValueError:
            continue
        rows.append({
            "day_of_week":     d.weekday(),
            "day_of_month":    d.day,
            "week_of_year":    d.isocalendar()[1],
            "month":           d.month,
            "quarter":         (d.month - 1) // 3 + 1,
            "year":            d.year,
            "is_weekend":      int(d.weekday() >= 5),
            "is_month_start":  int(d.day == 1),
            "is_month_end":    int(d.day >= 28),
            "days_since_start": (d - datetime(2020, 1, 1)).days,
            "category_encoded": CATEGORY_ENCODING.get(product.category or "", 0),
            "unit_price":       product.unit_price or 0.0,
            "reorder_level":    product.reorder_level or 10,
        })
    return pd.DataFrame(rows) if rows else None


def _check_drift(
    current_mape: Optional[float],
    baseline_mape: Optional[float],
) -> Tuple[Optional[float], bool]:
    """Returns (drift_score_pct, drift_detected)."""
    if current_mape is None or baseline_mape is None or baseline_mape == 0:
        return None, False
    drift = (current_mape - baseline_mape) / baseline_mape * 100
    return drift, drift >= DRIFT_THRESHOLD_PCT


def _get_model_version_tag() -> str:
    meta = _get_training_metadata()
    return meta.get("trained_at", "unknown")


def _serialize_eval(r: ForecastEvaluation) -> Dict[str, Any]:
    return {
        "id":                    r.id,
        "evaluated_at":          r.evaluated_at.isoformat() if r.evaluated_at else None,
        "evaluation_type":       r.evaluation_type,
        "evaluation_window_days": r.evaluation_window_days,
        "n_samples":             r.n_samples,
        "n_products":            r.n_products,
        "mae":                   r.mae,
        "rmse":                  r.rmse,
        "mape":                  r.mape,
        "smape":                 r.smape,
        "r2":                    r.r2,
        "drift_detected":        r.drift_detected,
        "drift_score":           r.drift_score,
        "baseline_mape":         r.baseline_mape,
        "model_version":         r.model_version,
        "triggered_by":          r.triggered_by,
        "product_id":            r.product_id,
    }


def _serialize_version(v: ModelVersion) -> Dict[str, Any]:
    return {
        "id":               v.id,
        "version_tag":      v.version_tag,
        "trained_at":       v.trained_at.isoformat() if v.trained_at else None,
        "n_train_samples":  v.n_train_samples,
        "n_features":       v.n_features,
        "train_mae":        v.train_mae,
        "train_rmse":       v.train_rmse,
        "val_mae":          v.val_mae,
        "val_rmse":         v.val_rmse,
        "val_mape":         v.val_mape,
        "val_smape":        v.val_smape,
        "val_r2":           v.val_r2,
        "is_active":        v.is_active,
        "retired_at":       v.retired_at.isoformat() if v.retired_at else None,
        "retrain_reason":   v.retrain_reason,
        "model_path":       v.model_path,
    }
