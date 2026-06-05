"""
DataValidator: schema-level and statistical validation of the daily demand
DataFrame before it enters the cleaning and feature engineering stages.

Returns a ValidationReport describing every issue found.
"""
import logging
from dataclasses import dataclass, field
from typing import List

import pandas as pd
import numpy as np

from ml.config import (
    MAX_DEMAND_VALUE,
    MAX_NULL_RATIO,
    MAX_UNIT_PRICE,
    MIN_DEMAND_VALUE,
    MIN_UNIT_PRICE,
    MIN_HISTORY_DAYS,
)

logger = logging.getLogger(__name__)

# Columns that must be present in the daily demand frame
REQUIRED_COLUMNS = [
    "date",
    "product_id",
    "demand",
    "order_count",
    "unit_price",
    "reorder_level",
    "category",
    "quantity_available",
]


@dataclass
class ValidationReport:
    is_valid: bool = True
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    rows_total: int = 0
    rows_valid: int = 0
    null_summary: dict = field(default_factory=dict)
    out_of_range_counts: dict = field(default_factory=dict)

    def add_error(self, msg: str) -> None:
        self.errors.append(msg)
        self.is_valid = False

    def add_warning(self, msg: str) -> None:
        self.warnings.append(msg)

    def to_dict(self) -> dict:
        return {
            "is_valid": self.is_valid,
            "errors": self.errors,
            "warnings": self.warnings,
            "rows_total": self.rows_total,
            "rows_valid": self.rows_valid,
            "null_summary": self.null_summary,
            "out_of_range_counts": self.out_of_range_counts,
        }


def validate(df: pd.DataFrame) -> ValidationReport:
    """
    Run all validation checks on the daily demand DataFrame.

    Checks performed:
    1. Required columns present
    2. Non-empty frame
    3. Date column parseable
    4. Null ratios within threshold
    5. Demand values within allowed range
    6. unit_price within allowed range
    7. Sufficient history per product
    8. No duplicate (date, product_id) rows

    Returns:
        ValidationReport with is_valid=True if no errors were found.
    """
    report = ValidationReport(rows_total=len(df))

    # ── 1. Column presence ───────────────────────────────────────────────────
    missing_cols = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing_cols:
        report.add_error(f"Missing required columns: {missing_cols}")
        return report  # can't proceed further

    # ── 2. Non-empty ─────────────────────────────────────────────────────────
    if df.empty:
        report.add_error("DataFrame is empty – no data to process.")
        return report

    # ── 3. Date column ───────────────────────────────────────────────────────
    date_col = pd.to_datetime(df["date"], errors="coerce")
    n_invalid_dates = date_col.isna().sum()
    if n_invalid_dates > 0:
        report.add_error(f"{n_invalid_dates} rows have unparseable 'date' values.")

    # ── 4. Null ratios ───────────────────────────────────────────────────────
    null_ratios = df[REQUIRED_COLUMNS].isnull().mean()
    report.null_summary = null_ratios.round(4).to_dict()
    for col, ratio in null_ratios.items():
        if ratio > MAX_NULL_RATIO:
            report.add_error(
                f"Column '{col}' has {ratio:.1%} null values (threshold {MAX_NULL_RATIO:.0%})."
            )
        elif ratio > 0:
            report.add_warning(f"Column '{col}' has {ratio:.1%} null values.")

    # ── 5. Demand range ──────────────────────────────────────────────────────
    demand_oor = ((df["demand"] < MIN_DEMAND_VALUE) | (df["demand"] > MAX_DEMAND_VALUE)).sum()
    report.out_of_range_counts["demand"] = int(demand_oor)
    if demand_oor > 0:
        report.add_warning(
            f"{demand_oor} rows have demand outside [{MIN_DEMAND_VALUE}, {MAX_DEMAND_VALUE}]."
        )

    # ── 6. Unit price range ──────────────────────────────────────────────────
    if "unit_price" in df.columns:
        price_oor = (
            (df["unit_price"] < MIN_UNIT_PRICE) | (df["unit_price"] > MAX_UNIT_PRICE)
        ).sum()
        report.out_of_range_counts["unit_price"] = int(price_oor)
        if price_oor > 0:
            report.add_warning(
                f"{price_oor} rows have unit_price outside [{MIN_UNIT_PRICE}, {MAX_UNIT_PRICE}]."
            )

    # ── 7. Minimum history per product ───────────────────────────────────────
    history_counts = df.groupby("product_id")["date"].nunique()
    short_history = (history_counts < MIN_HISTORY_DAYS).sum()
    if short_history > 0:
        report.add_warning(
            f"{short_history} products have fewer than {MIN_HISTORY_DAYS} days of history."
        )

    # ── 8. Duplicate (date, product_id) ──────────────────────────────────────
    n_dups = df.duplicated(subset=["date", "product_id"]).sum()
    if n_dups > 0:
        report.add_error(
            f"{n_dups} duplicate (date, product_id) rows found."
        )

    # ── Summary ──────────────────────────────────────────────────────────────
    report.rows_valid = int(
        df.dropna(subset=REQUIRED_COLUMNS).shape[0]
    )

    if report.is_valid:
        logger.info("Validation passed: %d rows, %d warnings.", len(df), len(report.warnings))
    else:
        logger.warning(
            "Validation FAILED with %d errors, %d warnings.",
            len(report.errors), len(report.warnings)
        )

    return report
