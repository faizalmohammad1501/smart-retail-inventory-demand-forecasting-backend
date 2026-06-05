"""
DataCleaner: handles null imputation, type coercion, duplicate removal,
and outlier capping on the daily demand DataFrame.
"""
import logging
from typing import Dict, Tuple

import numpy as np
import pandas as pd

from ml.config import (
    MAX_DEMAND_VALUE,
    MAX_UNIT_PRICE,
    MIN_DEMAND_VALUE,
    MIN_UNIT_PRICE,
    OUTLIER_IQR_MULTIPLIER,
    PRODUCT_CATEGORIES,
)

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def clean(df: pd.DataFrame) -> Tuple[pd.DataFrame, Dict]:
    """
    Full cleaning pipeline for the daily demand DataFrame.

    Steps:
        1. Drop exact duplicate rows
        2. Coerce column types
        3. Clamp demand and price to valid ranges
        4. Impute numeric nulls
        5. Impute categorical nulls
        6. Cap outliers in demand using IQR fence

    Returns:
        (cleaned_df, cleaning_report)  where cleaning_report is a dict
        summarising every change made.
    """
    report: Dict = {
        "original_rows": len(df),
        "duplicates_removed": 0,
        "demand_clamped": 0,
        "price_clamped": 0,
        "nulls_imputed": {},
        "outliers_capped": 0,
    }

    df = df.copy()

    # ── 1. Remove duplicates ─────────────────────────────────────────────────
    before = len(df)
    df = df.drop_duplicates(subset=["date", "product_id"])
    report["duplicates_removed"] = before - len(df)
    if report["duplicates_removed"]:
        logger.info("Removed %d duplicate rows.", report["duplicates_removed"])

    # ── 2. Type coercion ─────────────────────────────────────────────────────
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df["product_id"] = pd.to_numeric(df["product_id"], errors="coerce").astype("Int64")
    df["demand"] = pd.to_numeric(df["demand"], errors="coerce")
    df["order_count"] = pd.to_numeric(df["order_count"], errors="coerce")
    df["unit_price"] = pd.to_numeric(df["unit_price"], errors="coerce")
    df["reorder_level"] = pd.to_numeric(df["reorder_level"], errors="coerce")
    df["quantity_available"] = pd.to_numeric(df["quantity_available"], errors="coerce")
    df["sla_breach_rate"] = pd.to_numeric(df.get("sla_breach_rate", pd.Series(dtype=float)), errors="coerce")

    # ── 3. Clamp demand ──────────────────────────────────────────────────────
    mask = df["demand"].notna() & (
        (df["demand"] < MIN_DEMAND_VALUE) | (df["demand"] > MAX_DEMAND_VALUE)
    )
    report["demand_clamped"] = int(mask.sum())
    df.loc[mask, "demand"] = df.loc[mask, "demand"].clip(MIN_DEMAND_VALUE, MAX_DEMAND_VALUE)

    # ── 4. Clamp unit_price ──────────────────────────────────────────────────
    if "unit_price" in df.columns:
        mask_p = df["unit_price"].notna() & (
            (df["unit_price"] < MIN_UNIT_PRICE) | (df["unit_price"] > MAX_UNIT_PRICE)
        )
        report["price_clamped"] = int(mask_p.sum())
        df.loc[mask_p, "unit_price"] = df.loc[mask_p, "unit_price"].clip(
            MIN_UNIT_PRICE, MAX_UNIT_PRICE
        )

    # ── 5. Impute numeric nulls ──────────────────────────────────────────────
    numeric_fill_strategies = {
        "demand": ("product_id", "median"),
        "order_count": ("product_id", "median"),
        "unit_price": ("product_id", "median"),
        "reorder_level": ("global", 10),
        "quantity_available": ("global", 0),
        "quantity_reserved": ("global", 0),
        "avg_procurement_time": ("global", 0),
        "avg_processing_time": ("global", 0),
        "avg_delivery_time": ("global", 0),
        "avg_total_time": ("global", 0),
        "sla_breach_rate": ("global", 0),
    }

    for col, (strategy, value) in numeric_fill_strategies.items():
        if col not in df.columns:
            continue
        n_null = df[col].isna().sum()
        if n_null == 0:
            continue
        if strategy == "product_id":
            fill_val = df.groupby("product_id")[col].transform(value)
            # Any product with all-null falls back to global median
            global_fallback = df[col].median()
            df[col] = df[col].fillna(fill_val).fillna(global_fallback)
        else:
            df[col] = df[col].fillna(value)

        report["nulls_imputed"][col] = int(n_null)
        logger.debug("Imputed %d nulls in '%s'.", n_null, col)

    # ── 6. Impute categorical nulls ──────────────────────────────────────────
    if "category" in df.columns:
        n_null_cat = df["category"].isna().sum()
        if n_null_cat:
            df["category"] = df["category"].fillna("Unknown")
            report["nulls_imputed"]["category"] = int(n_null_cat)

    if "product_name" in df.columns:
        df["product_name"] = df["product_name"].fillna("Unknown Product")

    if "sku" in df.columns:
        df["sku"] = df["sku"].fillna("UNKNOWN")

    # ── 7. Cap demand outliers (IQR fence per product) ───────────────────────
    df, n_capped = _cap_outliers_iqr(df, col="demand", group_by="product_id")
    report["outliers_capped"] = n_capped

    # ── 8. Final sort ────────────────────────────────────────────────────────
    df = df.sort_values(["product_id", "date"]).reset_index(drop=True)

    report["final_rows"] = len(df)
    logger.info(
        "Cleaning complete: %d → %d rows. Outliers capped: %d.",
        report["original_rows"], report["final_rows"], report["outliers_capped"],
    )
    return df, report


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _cap_outliers_iqr(df: pd.DataFrame, col: str, group_by: str) -> Tuple[pd.DataFrame, int]:
    """
    Cap values in `col` beyond IQR fence (Q1 - k*IQR, Q3 + k*IQR)
    computed per `group_by` group.

    Returns (modified_df, number_of_rows_capped).
    """
    capped_total = 0
    k = OUTLIER_IQR_MULTIPLIER

    groups = []
    for _, group in df.groupby(group_by):
        q1 = group[col].quantile(0.25)
        q3 = group[col].quantile(0.75)
        iqr = q3 - q1
        lower = max(MIN_DEMAND_VALUE, q1 - k * iqr)
        upper = q3 + k * iqr

        before = group[col].copy()
        group[col] = group[col].clip(lower, upper)
        capped_total += int((group[col] != before).sum())
        groups.append(group)

    result = pd.concat(groups).sort_values(["product_id", "date"]).reset_index(drop=True)
    if capped_total:
        logger.info("Capped %d outlier values in '%s'.", capped_total, col)
    return result, capped_total
