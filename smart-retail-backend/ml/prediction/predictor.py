"""
ModelPredictor: loads the trained model + scaler artefacts and exposes
a clean interface for single-row and batch inference.

Falls back to a statistical baseline (weighted moving average) when
no trained model exists, so the API is always usable.
"""
import logging
import pickle
from pathlib import Path
from typing import List, Optional

import numpy as np
import pandas as pd

from ml.config import SAVED_MODELS_DIR, TARGET_COLUMN
from ml.preprocessing.feature_engineer import get_feature_columns
from ml.preprocessing.scaler_service import ScalerService
from ml.training.model_trainer import MODEL_FILE

logger = logging.getLogger(__name__)


class ModelPredictor:
    """
    Thin wrapper around the persisted GBR model + scalers.

    Usage:
        predictor = ModelPredictor.load()
        predictions = predictor.predict(X_df)   # returns original-scale demand
    """

    def __init__(self, model, scaler: ScalerService, feature_columns: List[str]) -> None:
        self._model = model
        self._scaler = scaler
        self._feature_columns = feature_columns

    # ── Factory ───────────────────────────────────────────────────────────────

    @classmethod
    def load(cls, directory: Optional[str] = None) -> Optional["ModelPredictor"]:
        """
        Load model + scaler from `directory`.  Returns None if artefacts
        are missing (caller should fall back to baseline).
        """
        model_dir = Path(directory or SAVED_MODELS_DIR)
        model_path = model_dir / MODEL_FILE

        if not model_path.exists():
            logger.warning("Trained model not found at %s. Using baseline.", model_path)
            return None

        try:
            with open(model_path, "rb") as f:
                model = pickle.load(f)

            scaler = ScalerService.load(str(model_dir))
            return cls(model, scaler, scaler.feature_columns)
        except Exception as exc:
            logger.error("Failed to load model artefacts: %s", exc)
            return None

    # ── Inference ─────────────────────────────────────────────────────────────

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        """
        Predict demand for each row in `X`.

        Args:
            X: DataFrame with columns matching the training feature set.

        Returns:
            numpy array of demand predictions in original (unscaled) units.
        """
        X_aligned = X[self._feature_columns].fillna(0).astype(float)
        X_scaled = self._scaler.transform_features(X_aligned)
        y_scaled = self._model.predict(X_scaled)
        return self._scaler.inverse_transform_target(y_scaled)

    @property
    def feature_columns(self) -> List[str]:
        return self._feature_columns

    def is_ready(self) -> bool:
        return self._model is not None


# ─────────────────────────────────────────────────────────────────────────────
# Statistical baseline (no trained model required)
# ─────────────────────────────────────────────────────────────────────────────

def baseline_predict(history: np.ndarray, n_steps: int) -> np.ndarray:
    """
    Simple weighted moving average baseline.

    Weights: more recent observations get higher weight.
    Uses up to the last 30 days; falls back to mean for short histories.

    Args:
        history: 1-D array of past demand values (oldest first).
        n_steps: number of future steps to predict.

    Returns:
        numpy array of length n_steps.
    """
    window = min(30, len(history))
    if window == 0:
        return np.zeros(n_steps)

    recent = history[-window:]
    weights = np.linspace(1, 3, num=window)          # linear ramp
    weights /= weights.sum()
    wma = float(np.dot(weights, recent))
    wma = max(0.0, wma)
    return np.full(n_steps, wma)
