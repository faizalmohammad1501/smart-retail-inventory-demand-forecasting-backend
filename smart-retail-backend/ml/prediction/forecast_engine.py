"""
ForecastEngine: generates multi-step ahead demand forecasts for a single
product by iteratively applying feature engineering and model inference,
feeding each prediction back in as the lag value for the next step.

Works with a trained ModelPredictor *or* falls back to the statistical
baseline if no model has been trained yet.
"""
import logging
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from ml.config import (
    CATEGORY_ENCODING,
    FORECAST_HORIZON_DAYS,
    LAG_DAYS,
    ROLLING_WINDOWS,
    TARGET_COLUMN,
)
from ml.prediction.predictor import ModelPredictor, baseline_predict

logger = logging.getLogger(__name__)

# Maximum lag we need to look back in history
MAX_LAG = max(LAG_DAYS)
MAX_ROLLING = max(ROLLING_WINDOWS)
LOOKBACK = max(MAX_LAG, MAX_ROLLING) + 5   # safety buffer


# ─────────────────────────────────────────────────────────────────────────────
# Result container
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class DayForecast:
    date: str
    predicted_demand: float
    lower_bound: float
    upper_bound: float
    confidence_score: float    # 0-1, higher = more confident


@dataclass
class ProductForecast:
    product_id: int
    product_name: str
    category: str
    sku: str
    horizon_days: int
    model_used: str             # "GradientBoosting" or "Baseline"
    forecasts: List[DayForecast]
    recommended_stock: float
    safety_stock: float
    avg_predicted_demand: float
    total_predicted_demand: float
    forecast_generated_at: str


# ─────────────────────────────────────────────────────────────────────────────
# Main engine
# ─────────────────────────────────────────────────────────────────────────────

class ForecastEngine:
    """
    Produces rolling multi-step forecasts for individual products.

    Args:
        predictor: Optional trained ModelPredictor.  If None, the
                   statistical baseline is used automatically.
    """

    def __init__(self, predictor: Optional[ModelPredictor] = None) -> None:
        self._predictor = predictor
        self._use_model = predictor is not None and predictor.is_ready()
        self._model_label = (
            "GradientBoosting" if self._use_model else "WeightedMovingAverage"
        )

    # ── Public ────────────────────────────────────────────────────────────────

    def forecast(
        self,
        history_df: pd.DataFrame,
        product_meta: Dict,
        horizon_days: int = FORECAST_HORIZON_DAYS,
    ) -> ProductForecast:
        """
        Generate a `horizon_days`-step ahead forecast.

        Args:
            history_df: DataFrame of past daily demand rows for this product,
                        sorted ascending by date. Must contain at least the
                        columns: date, demand, unit_price, reorder_level,
                        category, quantity_available, and the lifecycle columns.
            product_meta: Dict with product_id, product_name, category, sku,
                          unit_price, reorder_level, quantity_available.
            horizon_days: Number of calendar days to forecast forward.

        Returns:
            ProductForecast dataclass.
        """
        from datetime import datetime as dt
        generated_at = dt.utcnow().isoformat()

        demand_history = history_df[TARGET_COLUMN].values.astype(float)

        if self._use_model:
            raw_preds, lower, upper = self._model_forecast(
                history_df, product_meta, horizon_days
            )
        else:
            raw_preds = baseline_predict(demand_history, horizon_days)
            std_val = float(np.std(demand_history[-30:])) if len(demand_history) >= 7 else 0.0
            lower = np.maximum(0, raw_preds - 1.5 * std_val)
            upper = raw_preds + 1.5 * std_val

        # Confidence score: inversely proportional to relative uncertainty
        confidence_scores = _compute_confidence(raw_preds, lower, upper)

        # Build per-day forecast list
        last_date = pd.to_datetime(history_df["date"].iloc[-1]).date()
        day_forecasts = [
            DayForecast(
                date=str(last_date + timedelta(days=i + 1)),
                predicted_demand=max(0.0, round(float(raw_preds[i]), 2)),
                lower_bound=max(0.0, round(float(lower[i]), 2)),
                upper_bound=max(0.0, round(float(upper[i]), 2)),
                confidence_score=round(float(confidence_scores[i]), 3),
            )
            for i in range(horizon_days)
        ]

        avg_demand = float(np.mean(raw_preds))
        total_demand = float(np.sum(raw_preds))
        std_demand = float(np.std(demand_history[-30:])) if len(demand_history) >= 7 else avg_demand * 0.2

        # Stock recommendations (EOQ-inspired simple formula)
        lead_time_days = 7
        service_level_z = 1.65   # 95 % service level
        safety_stock = round(service_level_z * std_demand * (lead_time_days ** 0.5), 2)
        recommended_stock = round(avg_demand * lead_time_days + safety_stock, 2)

        return ProductForecast(
            product_id=product_meta["product_id"],
            product_name=product_meta.get("product_name", ""),
            category=product_meta.get("category", ""),
            sku=product_meta.get("sku", ""),
            horizon_days=horizon_days,
            model_used=self._model_label,
            forecasts=day_forecasts,
            recommended_stock=max(0.0, recommended_stock),
            safety_stock=max(0.0, safety_stock),
            avg_predicted_demand=round(max(0.0, avg_demand), 2),
            total_predicted_demand=round(max(0.0, total_demand), 2),
            forecast_generated_at=generated_at,
        )

    # ── Private ───────────────────────────────────────────────────────────────

    def _model_forecast(
        self,
        history_df: pd.DataFrame,
        product_meta: Dict,
        horizon_days: int,
    ):
        """
        Autoregressive multi-step forecast using the trained GBR model.
        Iteratively builds feature rows and feeds predictions back as lags.
        """
        # Rolling buffer of demand values (actual + growing predictions)
        buffer = list(history_df[TARGET_COLUMN].values.astype(float))

        # Retrieve static product features once
        unit_price = float(product_meta.get("unit_price", 0.0))
        reorder_level = max(1, int(product_meta.get("reorder_level", 1)))
        quantity_available = float(product_meta.get("quantity_available", 0.0))
        category_enc = int(CATEGORY_ENCODING.get(product_meta.get("category", ""), -1))
        stock_ratio = quantity_available / reorder_level

        # Lifecycle baseline from history
        avg_proc = float(history_df.get("avg_procurement_time", pd.Series([24])).mean())
        avg_proc_time = avg_proc if not np.isnan(avg_proc) else 24.0
        avg_del = float(history_df.get("avg_delivery_time", pd.Series([72])).mean())
        avg_del_time = avg_del if not np.isnan(avg_del) else 72.0
        avg_total = avg_proc_time + avg_del_time
        sla_breach_rate = float(history_df.get("sla_breach_rate", pd.Series([0.0])).mean())

        # Compute global days_since_start baseline
        first_date = pd.to_datetime(history_df["date"].iloc[0])
        last_hist_date = pd.to_datetime(history_df["date"].iloc[-1])
        base_days_since_start = int((last_hist_date - first_date).days)

        predictions = []
        last_date = last_hist_date.date()

        for step in range(horizon_days):
            forecast_date = last_date + timedelta(days=step + 1)
            row = _build_feature_row(
                forecast_date=forecast_date,
                buffer=buffer,
                unit_price=unit_price,
                reorder_level=reorder_level,
                quantity_available=quantity_available,
                category_enc=category_enc,
                stock_ratio=stock_ratio,
                avg_procurement_time=avg_proc_time,
                avg_processing_time=avg_proc_time * 0.5,
                avg_delivery_time=avg_del_time,
                avg_total_time=avg_total,
                sla_breach_rate=sla_breach_rate,
                order_count=1,
                days_since_start=base_days_since_start + step + 1,
                price_bucket=min(4, max(0, int((unit_price - 1) / 200))),
            )

            feat_cols = self._predictor.feature_columns
            X = pd.DataFrame([row])
            # Align columns — fill missing with 0
            for c in feat_cols:
                if c not in X.columns:
                    X[c] = 0.0
            pred = float(self._predictor.predict(X[feat_cols])[0])
            pred = max(0.0, pred)
            predictions.append(pred)
            buffer.append(pred)

        preds = np.array(predictions)
        recent_std = float(np.std(buffer[-30:])) if len(buffer) >= 7 else float(np.mean(preds)) * 0.2
        lower = np.maximum(0, preds - 1.5 * recent_std)
        upper = preds + 1.5 * recent_std
        return preds, lower, upper


# ─────────────────────────────────────────────────────────────────────────────
# Feature row builder (single future date)
# ─────────────────────────────────────────────────────────────────────────────

def _build_feature_row(
    forecast_date,
    buffer: List[float],
    unit_price: float,
    reorder_level: int,
    quantity_available: float,
    category_enc: int,
    stock_ratio: float,
    avg_procurement_time: float,
    avg_processing_time: float,
    avg_delivery_time: float,
    avg_total_time: float,
    sla_breach_rate: float,
    order_count: float,
    days_since_start: int,
    price_bucket: int,
) -> Dict:
    """Build a single feature dict for one future forecast date."""
    import math

    d = pd.Timestamp(forecast_date)
    dow = d.dayofweek

    row = {
        # Time features
        "day_of_week": dow,
        "day_of_month": d.day,
        "week_of_year": d.isocalendar().week,
        "month": d.month,
        "quarter": d.quarter,
        "year": d.year,
        "is_weekend": int(dow >= 5),
        "is_month_start": int(d.is_month_start),
        "is_month_end": int(d.is_month_end),
        "days_since_start": days_since_start,
        # Cyclical
        "dow_sin": math.sin(2 * math.pi * dow / 7),
        "dow_cos": math.cos(2 * math.pi * dow / 7),
        "month_sin": math.sin(2 * math.pi * (d.month - 1) / 12),
        "month_cos": math.cos(2 * math.pi * (d.month - 1) / 12),
        # Product / inventory
        "unit_price": unit_price,
        "reorder_level": reorder_level,
        "category_encoded": category_enc,
        "quantity_available": quantity_available,
        "stock_ratio": stock_ratio,
        "price_bucket": price_bucket,
        # Order lifecycle
        "avg_procurement_time": avg_procurement_time,
        "avg_processing_time": avg_processing_time,
        "avg_delivery_time": avg_delivery_time,
        "avg_total_time": avg_total_time,
        "sla_breach_rate": sla_breach_rate,
        "order_count": order_count,
    }

    # Lag features
    for lag in LAG_DAYS:
        idx = -(lag)
        row[f"lag_{lag}"] = float(buffer[idx]) if len(buffer) >= lag else 0.0

    # Rolling features
    for w in ROLLING_WINDOWS:
        recent = buffer[-w:] if len(buffer) >= w else buffer
        arr = np.array(recent, dtype=float)
        row[f"rolling_mean_{w}"] = float(np.mean(arr)) if len(arr) > 0 else 0.0
        row[f"rolling_std_{w}"] = float(np.std(arr)) if len(arr) > 1 else 0.0
        row[f"rolling_min_{w}"] = float(np.min(arr)) if len(arr) > 0 else 0.0
        row[f"rolling_max_{w}"] = float(np.max(arr)) if len(arr) > 0 else 0.0

    return row


def _compute_confidence(
    preds: np.ndarray,
    lower: np.ndarray,
    upper: np.ndarray,
) -> np.ndarray:
    """
    Confidence score in [0, 1]: narrower prediction interval → higher confidence.
    Steps further into the future naturally widen intervals → lower confidence.
    """
    interval_width = upper - lower
    pred_magnitude = np.maximum(preds, 1.0)
    relative_width = interval_width / pred_magnitude   # 0 = perfect, large = uncertain
    # Map: 0 width → score 1.0,  width ≥ 2× prediction → score 0.1
    scores = 1.0 - np.clip(relative_width / 2.0, 0, 1) * 0.9
    # Decay confidence slightly for steps further in future
    horizon = len(preds)
    decay = np.linspace(0, 0.15, num=horizon)
    return np.clip(scores - decay, 0.1, 1.0)
