"""
DatasetBuilder: end-to-end preprocessing orchestrator.

Runs the full pipeline:
    raw DB data  →  daily demand aggregation  →  validation  →
    cleaning  →  feature engineering  →  scaling  →
    train / val / test split  →  saved CSV artefacts

Exposes a single `build()` function for convenience and individual
`run_*` helpers for incremental use.
"""
import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional, Tuple

import numpy as np
import pandas as pd
from sqlalchemy.orm import Session

from ml.config import (
    PROCESSED_DIR,
    RAW_DIR,
    TARGET_COLUMN,
    TEST_RATIO,
    TRAIN_RATIO,
    VAL_RATIO,
)
from ml.datasets.data_loader import (
    build_daily_demand,
    load_all_raw,
    save_raw_snapshot,
)
from ml.preprocessing.data_cleaner import clean
from ml.preprocessing.data_validator import validate, ValidationReport
from ml.preprocessing.feature_engineer import engineer_features, get_feature_columns
from ml.preprocessing.scaler_service import ScalerService

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Result container
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class PipelineResult:
    success: bool
    message: str
    validation_report: Optional[dict] = None
    cleaning_report: Optional[dict] = None
    split_info: Optional[dict] = None
    feature_columns: Optional[list] = None
    output_paths: Optional[dict] = None
    ran_at: str = ""

    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "message": self.message,
            "validation_report": self.validation_report,
            "cleaning_report": self.cleaning_report,
            "split_info": self.split_info,
            "feature_columns": self.feature_columns,
            "output_paths": self.output_paths,
            "ran_at": self.ran_at,
        }


# ─────────────────────────────────────────────────────────────────────────────
# Public entry-point
# ─────────────────────────────────────────────────────────────────────────────

def build(
    db: Optional[Session] = None,
    scale: bool = True,
    save_artefacts: bool = True,
) -> PipelineResult:
    """
    Run the complete preprocessing pipeline.

    Args:
        db: Optional SQLAlchemy session.  A new one is opened if None.
        scale: If True, fit & apply feature/target scalers and save them.
        save_artefacts: If True, write processed CSVs to PROCESSED_DIR.

    Returns:
        PipelineResult with status, reports, and output paths.
    """
    ran_at = datetime.utcnow().isoformat()
    _ensure_dirs()

    # ── 1. Ingest ────────────────────────────────────────────────────────────
    logger.info("Pipeline step 1/6: ingesting raw data from database.")
    orders_df, products_df, inventory_df = load_all_raw(db)

    if orders_df.empty:
        return PipelineResult(
            success=False,
            message="No order data found in the database. Seed the DB first.",
            ran_at=ran_at,
        )

    raw_demand = build_daily_demand(orders_df, products_df, inventory_df)
    if raw_demand.empty:
        return PipelineResult(
            success=False,
            message="Daily demand aggregation produced an empty frame.",
            ran_at=ran_at,
        )

    raw_path = save_raw_snapshot(raw_demand, "raw_demand.csv")

    # ── 2. Validate ──────────────────────────────────────────────────────────
    logger.info("Pipeline step 2/6: validating raw demand frame.")
    val_report: ValidationReport = validate(raw_demand)
    if not val_report.is_valid:
        return PipelineResult(
            success=False,
            message="Validation failed – see validation_report for details.",
            validation_report=val_report.to_dict(),
            ran_at=ran_at,
        )

    # ── 3. Clean ─────────────────────────────────────────────────────────────
    logger.info("Pipeline step 3/6: cleaning data.")
    clean_df, clean_report = clean(raw_demand)

    # ── 4. Feature engineering ───────────────────────────────────────────────
    logger.info("Pipeline step 4/6: engineering features.")
    feature_df = engineer_features(clean_df)
    feature_cols = get_feature_columns(feature_df)

    # ── 5. Train / Val / Test split (chronological per product) ─────────────
    logger.info("Pipeline step 5/6: splitting dataset.")
    train_df, val_df, test_df = _chronological_split(feature_df)
    split_info = {
        "total_rows": len(feature_df),
        "train_rows": len(train_df),
        "val_rows": len(val_df),
        "test_rows": len(test_df),
        "train_pct": round(len(train_df) / max(len(feature_df), 1) * 100, 1),
        "val_pct": round(len(val_df) / max(len(feature_df), 1) * 100, 1),
        "test_pct": round(len(test_df) / max(len(feature_df), 1) * 100, 1),
        "n_products": int(feature_df["product_id"].nunique()),
        "date_range": {
            "start": str(feature_df["date"].min().date()),
            "end": str(feature_df["date"].max().date()),
        },
    }

    # ── 6. Scale & save ──────────────────────────────────────────────────────
    output_paths: Dict[str, str] = {"raw": raw_path}

    if scale and len(train_df) > 0:
        logger.info("Pipeline step 6/6: scaling and saving artefacts.")
        X_train = train_df[feature_cols].astype(float)
        y_train = train_df[TARGET_COLUMN].astype(float)

        scaler_svc = ScalerService()
        scaler_svc.fit_transform(X_train, y_train)

        if save_artefacts:
            scaler_svc.save()

        # Attach scaled arrays as extra columns for convenience
        for split_name, split_df in [("train", train_df), ("val", val_df), ("test", test_df)]:
            X = split_df[feature_cols].astype(float)
            # replace NaN introduced by lag/rolling at series boundaries
            X = X.fillna(0)
            split_df = split_df.copy()
            # Store scaled X back – useful for downstream training scripts
            scaled_X = scaler_svc.transform_features(X)
            scaled_cols = [f"scaled_{c}" for c in feature_cols]
            scaled_df = pd.DataFrame(scaled_X, columns=scaled_cols, index=split_df.index)
            split_df = pd.concat([split_df, scaled_df], axis=1)

            if save_artefacts:
                path = _save_split(split_df, split_name)
                output_paths[split_name] = path
    else:
        logger.info("Pipeline step 6/6: saving artefacts (no scaling).")
        if save_artefacts:
            for split_name, split_df in [("train", train_df), ("val", val_df), ("test", test_df)]:
                path = _save_split(split_df, split_name)
                output_paths[split_name] = path

    # Save feature column list
    if save_artefacts:
        meta_path = Path(PROCESSED_DIR) / "feature_columns.json"
        with open(meta_path, "w") as f:
            json.dump(feature_cols, f, indent=2)
        output_paths["feature_columns"] = str(meta_path)

    logger.info("Pipeline finished successfully. Outputs: %s", output_paths)
    return PipelineResult(
        success=True,
        message=(
            f"Pipeline completed: {split_info['total_rows']} rows, "
            f"{split_info['n_products']} products."
        ),
        validation_report=val_report.to_dict(),
        cleaning_report=clean_report,
        split_info=split_info,
        feature_columns=feature_cols,
        output_paths=output_paths,
        ran_at=ran_at,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Split logic
# ─────────────────────────────────────────────────────────────────────────────

def _chronological_split(
    df: pd.DataFrame,
) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """
    Split each product's time-series chronologically into train/val/test.
    The split is applied per product so every product has representation
    in all three sets regardless of its history length.
    """
    train_parts, val_parts, test_parts = [], [], []

    for _, grp in df.groupby("product_id"):
        grp = grp.sort_values("date")
        n = len(grp)
        i_train = int(n * TRAIN_RATIO)
        i_val = int(n * (TRAIN_RATIO + VAL_RATIO))

        train_parts.append(grp.iloc[:i_train])
        val_parts.append(grp.iloc[i_train:i_val])
        test_parts.append(grp.iloc[i_val:])

    def _concat(parts):
        if parts:
            return pd.concat(parts, ignore_index=True)
        return pd.DataFrame()

    return _concat(train_parts), _concat(val_parts), _concat(test_parts)


# ─────────────────────────────────────────────────────────────────────────────
# File helpers
# ─────────────────────────────────────────────────────────────────────────────

def _save_split(df: pd.DataFrame, name: str) -> str:
    out_path = Path(PROCESSED_DIR) / f"{name}.csv"
    df.to_csv(out_path, index=False)
    logger.info("Saved %s split → %s (%d rows).", name, out_path, len(df))
    return str(out_path)


def _ensure_dirs() -> None:
    for d in [RAW_DIR, PROCESSED_DIR]:
        Path(d).mkdir(parents=True, exist_ok=True)


# ─────────────────────────────────────────────────────────────────────────────
# Dataset statistics helper (for the API layer)
# ─────────────────────────────────────────────────────────────────────────────

def get_dataset_stats() -> dict:
    """
    Return statistics about the most recently built processed datasets
    (reads from saved CSVs, does not re-run the pipeline).
    """
    stats: dict = {"available": False}
    train_path = Path(PROCESSED_DIR) / "train.csv"
    if not train_path.exists():
        return stats

    train_df = pd.read_csv(train_path, parse_dates=["date"])
    val_path = Path(PROCESSED_DIR) / "val.csv"
    test_path = Path(PROCESSED_DIR) / "test.csv"

    val_df = pd.read_csv(val_path, parse_dates=["date"]) if val_path.exists() else pd.DataFrame()
    test_df = pd.read_csv(test_path, parse_dates=["date"]) if test_path.exists() else pd.DataFrame()

    feat_path = Path(PROCESSED_DIR) / "feature_columns.json"
    feature_cols = []
    if feat_path.exists():
        with open(feat_path) as f:
            feature_cols = json.load(f)

    stats = {
        "available": True,
        "train": {
            "rows": len(train_df),
            "products": int(train_df["product_id"].nunique()) if not train_df.empty else 0,
            "date_range": {
                "start": str(train_df["date"].min().date()) if not train_df.empty else None,
                "end": str(train_df["date"].max().date()) if not train_df.empty else None,
            },
        },
        "val": {"rows": len(val_df)},
        "test": {"rows": len(test_df)},
        "n_features": len(feature_cols),
        "feature_columns": feature_cols,
    }
    return stats
