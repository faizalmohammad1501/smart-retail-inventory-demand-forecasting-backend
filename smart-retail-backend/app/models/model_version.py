"""
ModelVersion SQLAlchemy Model
================================
Registry of every trained model artefact with its performance metrics
and lifecycle status.

One row = one training run.  Only one version is `is_active=True` at a time.
When a new model is registered, the previous active version is retired.

Version tags are ISO-timestamp strings (``YYYYMMDD_HHMMSS``) plus a
monotonic counter to guarantee uniqueness within the same second.

Indexes
-------
  ix_mv_trained_at  — chronological queries
  ix_mv_is_active   — fast lookup of current active model
"""

import json
from datetime import datetime, timezone
from sqlalchemy import (
    Boolean, Column, DateTime, Float,
    Index, Integer, String, Text,
)
from app.database.connection import Base


def _utcnow():
    return datetime.now(timezone.utc)


class ModelVersion(Base):
    __tablename__ = "model_versions"

    # ── Identity ──────────────────────────────────────────────────────────────
    id          = Column(Integer, primary_key=True, index=True)
    version_tag = Column(String(100), unique=True, nullable=False, index=True)
    trained_at  = Column(DateTime, nullable=False, default=_utcnow)

    # ── Training configuration ────────────────────────────────────────────────
    n_train_samples = Column(Integer, nullable=True)
    n_val_samples   = Column(Integer, nullable=True)
    n_features      = Column(Integer, nullable=True)
    hyperparams     = Column(Text,    nullable=True)   # JSON string

    # ── Training-time metrics ─────────────────────────────────────────────────
    train_mae  = Column(Float, nullable=True)
    train_rmse = Column(Float, nullable=True)
    train_r2   = Column(Float, nullable=True)

    # ── Validation-time metrics ───────────────────────────────────────────────
    val_mae   = Column(Float, nullable=True)
    val_rmse  = Column(Float, nullable=True)
    val_mape  = Column(Float, nullable=True)
    val_smape = Column(Float, nullable=True)
    val_r2    = Column(Float, nullable=True)

    # ── Lifecycle ─────────────────────────────────────────────────────────────
    is_active      = Column(Boolean,  default=True,  nullable=False)
    retired_at     = Column(DateTime, nullable=True)
    retrain_reason = Column(String(500), nullable=True)

    # ── Artefact path ─────────────────────────────────────────────────────────
    model_path = Column(String(500), nullable=True)   # path to .pkl on disk

    created_at = Column(DateTime, nullable=False, default=_utcnow)

    # ── Composite indexes ─────────────────────────────────────────────────────
    __table_args__ = (
        Index("ix_mv_trained_at", "trained_at"),
        Index("ix_mv_is_active",  "is_active"),
    )

    # ── Helpers ───────────────────────────────────────────────────────────────
    def get_hyperparams(self) -> dict:
        try:
            return json.loads(self.hyperparams) if self.hyperparams else {}
        except Exception:
            return {}

    def set_hyperparams(self, params: dict) -> None:
        self.hyperparams = json.dumps(params)

    def __repr__(self) -> str:
        return (
            f"<ModelVersion tag={self.version_tag!r} "
            f"active={self.is_active} val_mape={self.val_mape}>"
        )
