"""
FeatureEngineer: transforms the cleaned daily demand DataFrame into a
model-ready feature matrix by adding time features, lag features,
rolling statistics, and product/inventory encodings.
"""
import logging
from typing import List

import numpy as np
import pandas as pd

from ml.config import (
    CATEGORY_ENCODING,
    LAG_DAYS,
    PRODUCT_CATEGORIES,
    ROLLING_WINDOWS,
    TARGET_COLUMN,
)

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Public entry-point
# ─────────────────────────────────────────────────────────────────────────────

def engineer_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Full feature engineering pipeline applied to the cleaned daily demand frame.

    Adds:
        - Time-based features
        - Lag demand features
        - Rolling window statistics
        - Product & inventory features (stock_ratio, category_encoded)
        - Trend index (days_since_start)

    Returns a new DataFrame with all original columns plus engineered features.
    The frame is sorted by (product_id, date) and reset-indexed.
    """
    df = df.copy()
    df = df.sort_values(["product_id", "date"]).reset_index(drop=True)

    df = _add_time_features(df)
    df = _add_lag_features(df)
    df = _add_rolling_features(df)
    df = _add_product_features(df)

    # Drop any rows that are still all-null for key feature groups
    # (can happen at the start of each product's history due to lags)
    critical = [f"lag_{LAG_DAYS[0]}", f"rolling_mean_{ROLLING_WINDOWS[0]}"]
    existing_critical = [c for c in critical if c in df.columns]
    before = len(df)
    df = df.dropna(subset=existing_critical).reset_index(drop=True)
    dropped = before - len(df)
    if dropped:
        logger.info("Dropped %d rows lacking lag/rolling context at series start.", dropped)

    logger.info(
        "Feature engineering complete: %d rows, %d columns (target: '%s').",
        len(df), df.shape[1], TARGET_COLUMN,
    )
    return df


# ─────────────────────────────────────────────────────────────────────────────
# Step implementations
# ─────────────────────────────────────────────────────────────────────────────

def _add_time_features(df: pd.DataFrame) -> pd.DataFrame:
    """Derive calendar and cyclical features from the 'date' column."""
    dt = pd.to_datetime(df["date"])

    df["day_of_week"] = dt.dt.dayofweek            # 0=Mon … 6=Sun
    df["day_of_month"] = dt.dt.day
    df["week_of_year"] = dt.dt.isocalendar().week.astype(int)
    df["month"] = dt.dt.month
    df["quarter"] = dt.dt.quarter
    df["year"] = dt.dt.year
    df["is_weekend"] = (dt.dt.dayofweek >= 5).astype(int)
    df["is_month_start"] = dt.dt.is_month_start.astype(int)
    df["is_month_end"] = dt.dt.is_month_end.astype(int)

    # Cyclical encoding for day-of-week (preserves periodicity)
    df["dow_sin"] = np.sin(2 * np.pi * df["day_of_week"] / 7)
    df["dow_cos"] = np.cos(2 * np.pi * df["day_of_week"] / 7)

    # Cyclical encoding for month
    df["month_sin"] = np.sin(2 * np.pi * (df["month"] - 1) / 12)
    df["month_cos"] = np.cos(2 * np.pi * (df["month"] - 1) / 12)

    # Integer trend index (across the whole dataset)
    min_date = df["date"].min()
    df["days_since_start"] = (pd.to_datetime(df["date"]) - pd.to_datetime(min_date)).dt.days

    return df


def _add_lag_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    For each product, create lagged demand columns (lag_1, lag_3, lag_7, …).
    Values are computed strictly within each product's time-series to avoid
    data leakage across products.
    """
    demand_col = TARGET_COLUMN

    for lag in LAG_DAYS:
        col_name = f"lag_{lag}"
        df[col_name] = (
            df.groupby("product_id")[demand_col]
            .shift(lag)
        )

    return df


def _add_rolling_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Add rolling mean, std, min and max over several windows, computed
    per product with min_periods=1 to allow partial windows.
    """
    demand_col = TARGET_COLUMN

    for w in ROLLING_WINDOWS:
        grp = df.groupby("product_id")[demand_col]

        df[f"rolling_mean_{w}"] = grp.transform(
            lambda s, _w=w: s.shift(1).rolling(window=_w, min_periods=1).mean()
        )
        df[f"rolling_std_{w}"] = grp.transform(
            lambda s, _w=w: s.shift(1).rolling(window=_w, min_periods=1).std().fillna(0)
        )
        df[f"rolling_min_{w}"] = grp.transform(
            lambda s, _w=w: s.shift(1).rolling(window=_w, min_periods=1).min()
        )
        df[f"rolling_max_{w}"] = grp.transform(
            lambda s, _w=w: s.shift(1).rolling(window=_w, min_periods=1).max()
        )

    return df


def _add_product_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Encode product-level and inventory features:
    - category_encoded  : integer label from CATEGORY_ENCODING (unknown → -1)
    - stock_ratio       : quantity_available / max(reorder_level, 1)
    - price_bucket      : 5-quantile bin of unit_price (0–4)
    """
    # Category encoding
    if "category" in df.columns:
        df["category_encoded"] = (
            df["category"]
            .map(CATEGORY_ENCODING)
            .fillna(-1)
            .astype(int)
        )

    # Stock ratio
    if "quantity_available" in df.columns and "reorder_level" in df.columns:
        df["stock_ratio"] = df["quantity_available"] / df["reorder_level"].clip(lower=1)

    # Price bucket (global quantile bins so it's consistent)
    if "unit_price" in df.columns:
        df["price_bucket"] = pd.qcut(
            df["unit_price"],
            q=5,
            labels=False,
            duplicates="drop",
        ).fillna(0).astype(int)

    return df


# ─────────────────────────────────────────────────────────────────────────────
# Utility: get ordered feature column list from a processed frame
# ─────────────────────────────────────────────────────────────────────────────

def get_feature_columns(df: pd.DataFrame) -> List[str]:
    """
    Return all engineered feature column names from `df`, excluding
    the target, meta columns, and raw timestamp columns.
    """
    exclude = {
        TARGET_COLUMN, "date", "product_id", "product_name", "sku",
        "category", "order_id", "order_number", "status",
        "order_placed_at", "procurement_completed_at",
        "processing_completed_at", "dispatched_at", "delivered_at",
        "created_at", "updated_at",
    }
    return [c for c in df.columns if c not in exclude]
