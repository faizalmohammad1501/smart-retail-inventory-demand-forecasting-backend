"""
ScalerService: fits, applies, and persists StandardScaler (features)
and MinMaxScaler (target) as well as the LabelEncoder for categories.

Artefacts are saved to ml/saved_models/ so the same transformations
can be reloaded at prediction time.
"""
import json
import logging
import pickle
from pathlib import Path
from typing import Optional, Tuple

import numpy as np
import pandas as pd
from sklearn.preprocessing import LabelEncoder, MinMaxScaler, StandardScaler

from ml.config import (
    FEATURE_SCALER_FILE,
    LABEL_ENCODER_FILE,
    PIPELINE_METADATA_FILE,
    SAVED_MODELS_DIR,
    TARGET_COLUMN,
    TARGET_SCALER_FILE,
)

logger = logging.getLogger(__name__)


class ScalerService:
    """
    Wraps sklearn scalers/encoders with save/load helpers.

    Usage (training):
        svc = ScalerService()
        X_scaled, y_scaled = svc.fit_transform(X_df, y_series)
        svc.save()

    Usage (inference):
        svc = ScalerService.load()
        X_scaled = svc.transform_features(X_df)
        y_original = svc.inverse_transform_target(y_pred)
    """

    def __init__(self) -> None:
        self.feature_scaler: StandardScaler = StandardScaler()
        self.target_scaler: MinMaxScaler = MinMaxScaler()
        self.label_encoder: LabelEncoder = LabelEncoder()
        self._feature_columns: list = []
        self._is_fitted: bool = False

    # ── Fit + transform ───────────────────────────────────────────────────────

    def fit_transform(
        self,
        X: pd.DataFrame,
        y: pd.Series,
    ) -> Tuple[np.ndarray, np.ndarray]:
        """
        Fit all scalers on training data and return scaled arrays.

        Args:
            X: Feature DataFrame (numeric columns only; no meta/target cols)
            y: Target Series (demand values)

        Returns:
            (X_scaled, y_scaled) as numpy arrays
        """
        self._feature_columns = X.columns.tolist()

        X_scaled = self.feature_scaler.fit_transform(X.astype(float))
        y_scaled = self.target_scaler.fit_transform(
            y.values.reshape(-1, 1)
        ).ravel()

        self._is_fitted = True
        logger.info(
            "ScalerService fitted: %d features, %d samples.",
            len(self._feature_columns), len(y),
        )
        return X_scaled, y_scaled

    def transform_features(self, X: pd.DataFrame) -> np.ndarray:
        """Apply the fitted feature scaler to a new feature DataFrame."""
        self._check_fitted()
        X_aligned = X[self._feature_columns].astype(float)
        return self.feature_scaler.transform(X_aligned)

    def inverse_transform_target(self, y_scaled: np.ndarray) -> np.ndarray:
        """Reverse the target scaling to obtain demand in original units."""
        self._check_fitted()
        return self.target_scaler.inverse_transform(
            y_scaled.reshape(-1, 1)
        ).ravel()

    # ── Persist / reload ─────────────────────────────────────────────────────

    def save(self, directory: Optional[str] = None) -> None:
        """Serialize all artefacts to `directory` (defaults to SAVED_MODELS_DIR)."""
        dir_path = Path(directory or SAVED_MODELS_DIR)
        dir_path.mkdir(parents=True, exist_ok=True)

        _pickle_dump(self.feature_scaler, dir_path / FEATURE_SCALER_FILE)
        _pickle_dump(self.target_scaler, dir_path / TARGET_SCALER_FILE)
        _pickle_dump(self.label_encoder, dir_path / LABEL_ENCODER_FILE)

        metadata = {
            "feature_columns": self._feature_columns,
            "n_features": len(self._feature_columns),
        }
        with open(dir_path / PIPELINE_METADATA_FILE, "w") as f:
            json.dump(metadata, f, indent=2)

        logger.info("ScalerService artefacts saved to %s.", dir_path)

    @classmethod
    def load(cls, directory: Optional[str] = None) -> "ScalerService":
        """Load persisted artefacts and return a ready-to-use ScalerService."""
        dir_path = Path(directory or SAVED_MODELS_DIR)

        instance = cls()
        instance.feature_scaler = _pickle_load(dir_path / FEATURE_SCALER_FILE)
        instance.target_scaler = _pickle_load(dir_path / TARGET_SCALER_FILE)
        instance.label_encoder = _pickle_load(dir_path / LABEL_ENCODER_FILE)

        meta_path = dir_path / PIPELINE_METADATA_FILE
        if meta_path.exists():
            with open(meta_path) as f:
                metadata = json.load(f)
            instance._feature_columns = metadata.get("feature_columns", [])

        instance._is_fitted = True
        logger.info(
            "ScalerService loaded from %s (%d features).",
            dir_path, len(instance._feature_columns),
        )
        return instance

    @property
    def feature_columns(self) -> list:
        return self._feature_columns

    # ── Private ───────────────────────────────────────────────────────────────

    def _check_fitted(self) -> None:
        if not self._is_fitted:
            raise RuntimeError(
                "ScalerService is not fitted yet. Call fit_transform() or load()."
            )


# ─────────────────────────────────────────────────────────────────────────────
# Module-level helpers
# ─────────────────────────────────────────────────────────────────────────────

def _pickle_dump(obj: object, path: Path) -> None:
    with open(path, "wb") as f:
        pickle.dump(obj, f)


def _pickle_load(path: Path) -> object:
    if not path.exists():
        raise FileNotFoundError(f"Scaler artefact not found: {path}")
    with open(path, "rb") as f:
        return pickle.load(f)
