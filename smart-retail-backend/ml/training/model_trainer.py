"""
ModelTrainer: trains a GradientBoostingRegressor on the preprocessed
train.csv produced by the preprocessing pipeline, evaluates it on the
val split, and persists the model artefact to ml/saved_models/.
"""
import json
import logging
import pickle
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingRegressor
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score

from ml.config import (
    PROCESSED_DIR,
    SAVED_MODELS_DIR,
    TARGET_COLUMN,
)
from ml.preprocessing.feature_engineer import get_feature_columns
from ml.preprocessing.scaler_service import ScalerService

logger = logging.getLogger(__name__)

MODEL_FILE = "model.pkl"
TRAINING_METADATA_FILE = "training_metadata.json"


# ─────────────────────────────────────────────────────────────────────────────
# Result container
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class TrainingResult:
    success: bool
    message: str
    train_metrics: Optional[Dict] = None
    val_metrics: Optional[Dict] = None
    n_features: int = 0
    n_train_samples: int = 0
    n_val_samples: int = 0
    model_path: str = ""
    trained_at: str = ""

    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "message": self.message,
            "train_metrics": self.train_metrics,
            "val_metrics": self.val_metrics,
            "n_features": self.n_features,
            "n_train_samples": self.n_train_samples,
            "n_val_samples": self.n_val_samples,
            "model_path": self.model_path,
            "trained_at": self.trained_at,
        }


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def train(hyperparams: Optional[Dict] = None) -> TrainingResult:
    """
    Load the preprocessed train/val CSVs, fit a GradientBoostingRegressor,
    evaluate on val set, and persist the model + metadata.

    Args:
        hyperparams: Optional dict to override default GBR parameters.

    Returns:
        TrainingResult with metrics and artefact paths.
    """
    trained_at = datetime.utcnow().isoformat()
    train_path = Path(PROCESSED_DIR) / "train.csv"
    val_path = Path(PROCESSED_DIR) / "val.csv"

    if not train_path.exists():
        return TrainingResult(
            success=False,
            message=(
                "Processed train.csv not found. "
                "Run POST /api/ml/pipeline/run first."
            ),
            trained_at=trained_at,
        )

    # ── Load data ─────────────────────────────────────────────────────────────
    train_df = pd.read_csv(train_path)
    val_df = pd.read_csv(val_path) if val_path.exists() else pd.DataFrame()

    feature_cols = get_feature_columns(train_df)
    if not feature_cols:
        return TrainingResult(
            success=False,
            message="No feature columns found in train.csv.",
            trained_at=trained_at,
        )

    X_train = train_df[feature_cols].fillna(0).astype(float)
    y_train = train_df[TARGET_COLUMN].astype(float)

    # ── Fit scaler ────────────────────────────────────────────────────────────
    scaler = ScalerService()
    X_train_scaled, y_train_scaled = scaler.fit_transform(X_train, y_train)

    # ── Build model ───────────────────────────────────────────────────────────
    default_params = {
        "n_estimators": 200,
        "learning_rate": 0.05,
        "max_depth": 4,
        "min_samples_leaf": 5,
        "subsample": 0.8,
        "random_state": 42,
    }
    if hyperparams:
        default_params.update(hyperparams)

    model = GradientBoostingRegressor(**default_params)
    model.fit(X_train_scaled, y_train_scaled)

    # ── Training metrics ──────────────────────────────────────────────────────
    y_train_pred = model.predict(X_train_scaled)
    train_metrics = _compute_metrics(y_train_scaled, y_train_pred, scaler, "train")

    # ── Validation metrics ────────────────────────────────────────────────────
    val_metrics = None
    if not val_df.empty and TARGET_COLUMN in val_df.columns:
        X_val = val_df[feature_cols].fillna(0).astype(float)
        y_val = val_df[TARGET_COLUMN].astype(float)
        X_val_scaled = scaler.transform_features(X_val)
        y_val_pred = model.predict(X_val_scaled)
        val_metrics = _compute_metrics(
            scaler.feature_scaler.transform(y_val.values.reshape(-1, 1)).ravel()
            if False else y_val.values,
            scaler.inverse_transform_target(y_val_pred),
            scaler=None,
            split="val",
            already_original=True,
            y_true_original=y_val.values,
            y_pred_original=scaler.inverse_transform_target(y_val_pred),
        )

    # ── Persist ───────────────────────────────────────────────────────────────
    model_dir = Path(SAVED_MODELS_DIR)
    model_dir.mkdir(parents=True, exist_ok=True)

    model_path = model_dir / MODEL_FILE
    with open(model_path, "wb") as f:
        pickle.dump(model, f)

    scaler.save()

    metadata = {
        "trained_at": trained_at,
        "model_type": "GradientBoostingRegressor",
        "hyperparams": default_params,
        "feature_columns": feature_cols,
        "n_features": len(feature_cols),
        "n_train_samples": len(X_train),
        "train_metrics": train_metrics,
        "val_metrics": val_metrics,
    }
    with open(model_dir / TRAINING_METADATA_FILE, "w") as f:
        json.dump(metadata, f, indent=2)

    logger.info(
        "Model trained: %d samples, %d features. Val MAE=%.2f",
        len(X_train),
        len(feature_cols),
        val_metrics.get("mae", 0) if val_metrics else 0,
    )

    return TrainingResult(
        success=True,
        message=f"Model trained on {len(X_train)} samples with {len(feature_cols)} features.",
        train_metrics=train_metrics,
        val_metrics=val_metrics,
        n_features=len(feature_cols),
        n_train_samples=len(X_train),
        n_val_samples=len(val_df),
        model_path=str(model_path),
        trained_at=trained_at,
    )


def load_training_metadata() -> Optional[Dict]:
    """Return persisted training metadata, or None if not trained yet."""
    path = Path(SAVED_MODELS_DIR) / TRAINING_METADATA_FILE
    if not path.exists():
        return None
    with open(path) as f:
        return json.load(f)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _compute_metrics(
    y_true,
    y_pred,
    scaler: Optional[ScalerService],
    split: str,
    already_original: bool = False,
    y_true_original=None,
    y_pred_original=None,
) -> Dict:
    if already_original:
        yt = np.array(y_true_original)
        yp = np.array(y_pred_original)
    elif scaler is not None:
        yt = scaler.inverse_transform_target(np.array(y_true))
        yp = scaler.inverse_transform_target(np.array(y_pred))
    else:
        yt = np.array(y_true)
        yp = np.array(y_pred)

    mae = float(mean_absolute_error(yt, yp))
    rmse = float(np.sqrt(mean_squared_error(yt, yp)))
    r2 = float(r2_score(yt, yp))
    mape = float(np.mean(np.abs((yt - yp) / np.maximum(yt, 1))) * 100)

    return {
        "split": split,
        "mae": round(mae, 4),
        "rmse": round(rmse, 4),
        "r2": round(r2, 4),
        "mape_pct": round(mape, 4),
    }
