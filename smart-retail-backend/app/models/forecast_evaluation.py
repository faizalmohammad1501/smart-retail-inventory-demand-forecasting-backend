"""
ForecastEvaluation SQLAlchemy Model
=====================================
Persists each model accuracy evaluation run as an immutable record.

One row = one evaluation run, which may be:
  - portfolio-level (product_id IS NULL, covers all products)
  - per-product (product_id set, from a per-product hindcast)

Evaluation types
----------------
  hindcast   — model predictions vs actual orders from the DB for a
               recent time window (most useful for production monitoring)
  test_split — model evaluated on the held-out test.csv produced by the
               preprocessing pipeline
  val_split  — uses val_metrics from the saved training_metadata.json
               (a quick sanity check without needing real-time data)

Drift detection
---------------
  drift_detected  — True when the current MAPE exceeds the baseline by
                    more than drift_threshold_pct
  drift_score     — percentage degradation vs baseline MAPE (0 = no drift)

Indexes
-------
  ix_fe_evaluated_at   — time-series queries
  ix_fe_product_id     — per-product filter
  ix_fe_model_version  — filter by model version
  ix_fe_eval_type      — filter by evaluation type
"""

from datetime import datetime, timezone
from sqlalchemy import (
    Boolean, Column, DateTime, Float, Index,
    Integer, String, Text, ForeignKey,
)
from app.database.connection import Base


def _utcnow():
    return datetime.now(timezone.utc)


class ForecastEvaluation(Base):
    __tablename__ = "forecast_evaluations"

    # ── Identity ──────────────────────────────────────────────────────────────
    id           = Column(Integer, primary_key=True, index=True)
    evaluated_at = Column(DateTime, nullable=False, default=_utcnow)

    # ── Scope ─────────────────────────────────────────────────────────────────
    product_id             = Column(Integer, ForeignKey("products.id"), nullable=True)
    evaluation_window_days = Column(Integer, nullable=False)   # days of data used
    n_samples              = Column(Integer, nullable=False, default=0)
    n_products             = Column(Integer, nullable=False, default=0)  # portfolio-level

    # ── Error metrics ─────────────────────────────────────────────────────────
    mae   = Column(Float, nullable=True)   # Mean Absolute Error
    rmse  = Column(Float, nullable=True)   # Root Mean Squared Error
    mape  = Column(Float, nullable=True)   # Mean Absolute Percentage Error (%)
    smape = Column(Float, nullable=True)   # Symmetric MAPE (%)
    r2    = Column(Float, nullable=True)   # R² coefficient of determination

    # ── Drift ─────────────────────────────────────────────────────────────────
    drift_detected          = Column(Boolean, default=False, nullable=False)
    drift_score             = Column(Float,   nullable=True)   # % MAPE degradation
    baseline_mape           = Column(Float,   nullable=True)   # MAPE at training time

    # ── Version & context ─────────────────────────────────────────────────────
    model_version    = Column(String(100), nullable=True)   # matches ModelVersion.version_tag
    evaluation_type  = Column(String(50),  nullable=False, default="hindcast")
    triggered_by     = Column(String(50),  nullable=False, default="manual")
    notes            = Column(Text,        nullable=True)

    created_at = Column(DateTime, nullable=False, default=_utcnow)

    # ── Composite indexes ─────────────────────────────────────────────────────
    __table_args__ = (
        Index("ix_fe_evaluated_at",  "evaluated_at"),
        Index("ix_fe_product_id",    "product_id"),
        Index("ix_fe_model_version", "model_version"),
        Index("ix_fe_eval_type",     "evaluation_type"),
    )

    def __repr__(self) -> str:
        scope = f"product={self.product_id}" if self.product_id else "portfolio"
        return (
            f"<ForecastEvaluation id={self.id} {scope} "
            f"mape={self.mape} drift={self.drift_detected}>"
        )
