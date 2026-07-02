"""
SimulationRun Model
====================
Persists each simulation run so results can be retrieved, compared,
and audited later without re-running the computation.

Simulation types
----------------
  what_if             — single product, one scenario vs baseline
  scenario_compare    — single product, N named scenarios ranked side-by-side
  seasonal            — demand modulated by monthly seasonal factors
  supplier_disruption — supplier lead-time stress test
  monte_carlo         — stochastic run (1 000 trials) for probability estimates
  strategy_compare    — FOQ vs EOQ vs hybrid restocking strategy comparison
"""

import json
from sqlalchemy import (
    Column, Integer, String, Float, Boolean, DateTime,
    ForeignKey, Text, Index,
)
from sqlalchemy.sql import func

from app.database.connection import Base


class SimulationRun(Base):
    __tablename__ = "simulation_runs"

    __table_args__ = (
        Index("ix_sr_product_id",    "product_id"),
        Index("ix_sr_created_at",    "created_at"),
        Index("ix_sr_sim_type",      "simulation_type"),
        Index("ix_sr_created_by",    "created_by"),
    )

    # ── Identity ──────────────────────────────────────────────────────────────
    id              = Column(Integer, primary_key=True, index=True)
    run_id          = Column(String(64), unique=True, nullable=False, index=True)
    run_name        = Column(String(255))

    # ── Scope ─────────────────────────────────────────────────────────────────
    product_id      = Column(Integer, ForeignKey("products.id"), nullable=True)
    simulation_type = Column(String(50), nullable=False)   # see docstring above
    simulation_days = Column(Integer)

    # ── Input parameters (JSON) ───────────────────────────────────────────────
    # Stores the full parameter dict that was submitted so the run is
    # reproducible.  Schema varies by simulation_type.
    parameters      = Column(Text)   # JSON string

    # ── Results (JSON) ────────────────────────────────────────────────────────
    baseline_result  = Column(Text)   # JSON — metrics for the baseline scenario
    scenario_result  = Column(Text)   # JSON — metrics for the what-if scenario(s)
    comparison_summary = Column(Text) # JSON — ranked comparison / insights

    # ── Key aggregate metrics (indexed scalars for fast list queries) ─────────
    baseline_service_level  = Column(Float)   # % demand days with no stockout
    scenario_service_level  = Column(Float)   # best scenario service level
    baseline_total_cost     = Column(Float)   # total cost ($) baseline
    scenario_total_cost     = Column(Float)   # total cost ($) best scenario
    cost_savings            = Column(Float)   # baseline_cost - scenario_cost
    stockout_risk_pct       = Column(Float)   # % days with stockout (best scenario)

    # ── Metadata ─────────────────────────────────────────────────────────────
    created_by      = Column(String(150))
    notes           = Column(String(500))
    created_at      = Column(DateTime(timezone=True), server_default=func.now())

    # ── JSON helpers ─────────────────────────────────────────────────────────
    def get_parameters(self) -> dict:
        try:
            return json.loads(self.parameters) if self.parameters else {}
        except Exception:
            return {}

    def set_parameters(self, d: dict) -> None:
        self.parameters = json.dumps(d)

    def get_baseline_result(self) -> dict:
        try:
            return json.loads(self.baseline_result) if self.baseline_result else {}
        except Exception:
            return {}

    def get_scenario_result(self):
        try:
            return json.loads(self.scenario_result) if self.scenario_result else {}
        except Exception:
            return {}

    def get_comparison_summary(self) -> dict:
        try:
            return json.loads(self.comparison_summary) if self.comparison_summary else {}
        except Exception:
            return {}
