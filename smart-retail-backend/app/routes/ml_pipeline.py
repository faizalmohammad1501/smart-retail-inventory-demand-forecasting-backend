"""
ML Pipeline API routes: expose the demand forecasting data processing
pipeline via HTTP endpoints (protected by JWT).

Endpoints:
  POST /api/ml/pipeline/run          – trigger full preprocessing pipeline
  POST /api/ml/pipeline/generate     – generate & save synthetic datasets
  GET  /api/ml/pipeline/status       – check if processed datasets exist
  GET  /api/ml/pipeline/features     – preview the engineered feature matrix
  GET  /api/ml/pipeline/config       – return ML config constants
  GET  /api/ml/pipeline/download/{split} – stream a processed CSV file
"""
import io
import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_active_user, require_roles
from app.database.connection import get_db
from app.models.user import User
from app.schemas.schemas import (
    DatasetStatsResponse,
    FeaturePreviewResponse,
    PipelineRunRequest,
    PipelineRunResponse,
    SyntheticGenerateResponse,
)
from ml.config import (
    ALL_FEATURES,
    FORECAST_HORIZON_DAYS,
    LAG_DAYS,
    MIN_HISTORY_DAYS,
    PROCESSED_DIR,
    PRODUCT_CATEGORIES,
    ROLLING_WINDOWS,
    SYNTHETIC_DAYS,
    SYNTHETIC_PRODUCTS,
    TARGET_COLUMN,
    TRAIN_RATIO,
    VAL_RATIO,
    TEST_RATIO,
)
from ml.datasets.dataset_generator import generate_and_save, generate_daily_demand
from ml.preprocessing.dataset_builder import build, get_dataset_stats
from ml.preprocessing.data_cleaner import clean
from ml.preprocessing.data_validator import validate
from ml.preprocessing.feature_engineer import engineer_features, get_feature_columns

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ml", tags=["ML Pipeline"])


# ─────────────────────────────────────────────────────────────────────────────
# 1. Run preprocessing pipeline
# ─────────────────────────────────────────────────────────────────────────────

@router.post(
    "/pipeline/run",
    response_model=PipelineRunResponse,
    status_code=status.HTTP_200_OK,
    summary="Run the full demand forecasting preprocessing pipeline",
)
def run_pipeline(
    request: PipelineRunRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles("admin", "analyst")),
) -> PipelineRunResponse:
    """
    Triggers the end-to-end pipeline:

    1. Ingest order / product / inventory data (live DB **or** synthetic)
    2. Validate data quality
    3. Clean and impute
    4. Engineer features (time, lag, rolling, product, inventory)
    5. Fit scalers on train split
    6. Save train / val / test CSVs and scaler artefacts

    Requires role: `admin` or `analyst`.
    """
    try:
        if request.use_synthetic:
            # Build feature-engineered frame from synthetic data
            logger.info("Pipeline: using synthetic data.")
            products_from_gen = None  # generator uses internal PRODUCTS list
            raw_df = generate_daily_demand()
            val_report = validate(raw_df)
            if not val_report.is_valid:
                return PipelineRunResponse(
                    success=False,
                    message="Synthetic data failed validation.",
                    validation_report=val_report.to_dict(),
                    ran_at=_now(),
                )
            clean_df, clean_report = clean(raw_df)
            feature_df = engineer_features(clean_df)
            # Delegate split + save to dataset_builder via a thin wrapper
            result = _build_from_frame(
                feature_df,
                clean_report=clean_report,
                val_report=val_report,
                scale=request.scale,
                save=request.save_artefacts,
            )
        else:
            result = build(db=db, scale=request.scale, save_artefacts=request.save_artefacts)

        return PipelineRunResponse(**result.to_dict())

    except Exception as exc:
        logger.exception("Pipeline run failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Pipeline failed: {exc}",
        )


# ─────────────────────────────────────────────────────────────────────────────
# 2. Generate synthetic datasets
# ─────────────────────────────────────────────────────────────────────────────

@router.post(
    "/pipeline/generate",
    response_model=SyntheticGenerateResponse,
    status_code=status.HTTP_200_OK,
    summary="Generate and save synthetic demand datasets",
)
def generate_synthetic(
    current_user: User = Depends(require_roles("admin", "analyst")),
) -> SyntheticGenerateResponse:
    """
    Generates `SYNTHETIC_PRODUCTS` products × `SYNTHETIC_DAYS` days of
    realistic synthetic demand data (with seasonality, trend, and spikes)
    and persists them as CSV files under `ml/datasets/raw/`.

    Useful for development / demo when the live database is empty.

    Requires role: `admin` or `analyst`.
    """
    try:
        prod_path, inv_path, demand_path = generate_and_save()
        return SyntheticGenerateResponse(
            success=True,
            message=f"Generated synthetic datasets for {SYNTHETIC_PRODUCTS} products over {SYNTHETIC_DAYS} days.",
            products_path=prod_path,
            inventory_path=inv_path,
            demand_path=demand_path,
            n_products=SYNTHETIC_PRODUCTS,
            n_days=SYNTHETIC_DAYS,
        )
    except Exception as exc:
        logger.exception("Synthetic generation failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Synthetic generation failed: {exc}",
        )


# ─────────────────────────────────────────────────────────────────────────────
# 3. Pipeline / dataset status
# ─────────────────────────────────────────────────────────────────────────────

@router.get(
    "/pipeline/status",
    response_model=DatasetStatsResponse,
    status_code=status.HTTP_200_OK,
    summary="Check processed dataset availability and statistics",
)
def pipeline_status(
    current_user: User = Depends(get_current_active_user),
) -> DatasetStatsResponse:
    """
    Returns statistics about the most recently built processed datasets
    (reads from saved CSVs; does **not** re-run the pipeline).

    `available: false` means the pipeline has not been run yet.
    """
    stats = get_dataset_stats()
    return DatasetStatsResponse(**stats)


# ─────────────────────────────────────────────────────────────────────────────
# 4. Feature matrix preview
# ─────────────────────────────────────────────────────────────────────────────

@router.get(
    "/pipeline/features",
    response_model=FeaturePreviewResponse,
    status_code=status.HTTP_200_OK,
    summary="Preview the engineered feature matrix",
)
def feature_preview(
    split: str = Query(default="train", pattern="^(train|val|test)$"),
    limit: int = Query(default=20, ge=1, le=200),
    product_id: Optional[int] = Query(default=None),
    current_user: User = Depends(get_current_active_user),
) -> FeaturePreviewResponse:
    """
    Returns the first `limit` rows of the engineered feature matrix
    from the `split` dataset (train / val / test).

    Optionally filter by `product_id`.
    """
    csv_path = Path(PROCESSED_DIR) / f"{split}.csv"
    if not csv_path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"Processed '{split}' dataset not found. "
                "Run POST /api/ml/pipeline/run first."
            ),
        )

    df = pd.read_csv(csv_path)
    total_rows = len(df)

    if product_id is not None:
        df = df[df["product_id"] == product_id]
        if df.empty:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No rows found for product_id={product_id} in '{split}' split.",
            )

    feature_cols = get_feature_columns(df)
    preview_df = df.head(limit)

    # Ensure date is serialisable
    if "date" in preview_df.columns:
        preview_df = preview_df.copy()
        preview_df["date"] = preview_df["date"].astype(str)

    return FeaturePreviewResponse(
        rows_returned=len(preview_df),
        total_rows=total_rows,
        n_features=len(feature_cols),
        feature_columns=feature_cols,
        preview=preview_df.where(pd.notnull(preview_df), None).to_dict(orient="records"),
    )


# ─────────────────────────────────────────────────────────────────────────────
# 5. ML config endpoint
# ─────────────────────────────────────────────────────────────────────────────

@router.get(
    "/pipeline/config",
    status_code=status.HTTP_200_OK,
    summary="Return the ML pipeline configuration constants",
)
def get_ml_config(
    current_user: User = Depends(get_current_active_user),
) -> Dict[str, Any]:
    """
    Exposes the ML pipeline configuration so the frontend / data scientists
    know exactly what features, thresholds, and split ratios are in use.
    """
    return {
        "forecast_horizon_days": FORECAST_HORIZON_DAYS,
        "min_history_days": MIN_HISTORY_DAYS,
        "target_column": TARGET_COLUMN,
        "split_ratios": {
            "train": TRAIN_RATIO,
            "val": VAL_RATIO,
            "test": TEST_RATIO,
        },
        "lag_days": LAG_DAYS,
        "rolling_windows": ROLLING_WINDOWS,
        "product_categories": PRODUCT_CATEGORIES,
        "n_all_features": len(ALL_FEATURES),
        "all_features": ALL_FEATURES,
        "synthetic": {
            "products": SYNTHETIC_PRODUCTS,
            "days": SYNTHETIC_DAYS,
        },
    }


# ─────────────────────────────────────────────────────────────────────────────
# 6. Download processed CSV
# ─────────────────────────────────────────────────────────────────────────────

@router.get(
    "/pipeline/download/{split}",
    status_code=status.HTTP_200_OK,
    summary="Download a processed dataset split as CSV",
)
def download_split(
    split: str,
    current_user: User = Depends(require_roles("admin", "analyst")),
):
    """
    Stream the processed `train`, `val`, or `test` CSV file for download.

    Requires role: `admin` or `analyst`.
    """
    if split not in ("train", "val", "test", "raw_demand"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="split must be one of: train, val, test, raw_demand",
        )

    csv_path = Path(PROCESSED_DIR) / f"{split}.csv"
    if split == "raw_demand":
        from ml.config import RAW_DIR
        csv_path = Path(RAW_DIR) / "raw_demand.csv"

    if not csv_path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"File '{split}.csv' not found. Run the pipeline first.",
        )

    def _iter_csv():
        with open(csv_path, "rb") as f:
            yield from f

    return StreamingResponse(
        _iter_csv(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{split}.csv"'},
    )


# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

def _now() -> str:
    from datetime import datetime
    return datetime.utcnow().isoformat()


def _build_from_frame(
    feature_df: pd.DataFrame,
    clean_report: Dict,
    val_report,
    scale: bool,
    save: bool,
):
    """
    Reuse the dataset_builder split/scale/save logic on an already-engineered
    frame (used for the synthetic path).
    """
    import numpy as np
    from ml.config import PROCESSED_DIR, SAVED_MODELS_DIR
    from ml.preprocessing.dataset_builder import (
        PipelineResult,
        _chronological_split,
        _ensure_dirs,
        _save_split,
    )
    from ml.preprocessing.feature_engineer import get_feature_columns
    from ml.preprocessing.scaler_service import ScalerService
    from datetime import datetime

    _ensure_dirs()
    ran_at = datetime.utcnow().isoformat()

    feature_cols = get_feature_columns(feature_df)
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

    output_paths: Dict[str, str] = {}

    if scale and len(train_df) > 0:
        X_train = train_df[feature_cols].fillna(0).astype(float)
        y_train = train_df[TARGET_COLUMN].astype(float)
        scaler_svc = ScalerService()
        scaler_svc.fit_transform(X_train, y_train)
        if save:
            scaler_svc.save()

    if save:
        for name, split_df in [("train", train_df), ("val", val_df), ("test", test_df)]:
            path = _save_split(split_df, name)
            output_paths[name] = path
        feat_path = Path(PROCESSED_DIR) / "feature_columns.json"
        import json
        with open(feat_path, "w") as f:
            json.dump(feature_cols, f, indent=2)
        output_paths["feature_columns"] = str(feat_path)

    return PipelineResult(
        success=True,
        message=(
            f"Synthetic pipeline completed: {split_info['total_rows']} rows, "
            f"{split_info['n_products']} products."
        ),
        validation_report=val_report.to_dict(),
        cleaning_report=clean_report,
        split_info=split_info,
        feature_columns=feature_cols,
        output_paths=output_paths,
        ran_at=ran_at,
    )
