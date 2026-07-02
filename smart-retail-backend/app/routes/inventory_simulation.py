"""
Inventory Simulation & What-If Analysis API Routes
====================================================
Exposes the full simulation engine through a clean, well-documented REST API.

Endpoints
---------
  POST /api/simulation/run                    — run a single scenario simulation
  POST /api/simulation/what-if               — baseline vs one modified scenario
  POST /api/simulation/compare               — compare N named scenarios (ranked)
  POST /api/simulation/seasonal              — seasonal demand simulation
  POST /api/simulation/supplier-disruption   — supplier lead-time stress test
  POST /api/simulation/monte-carlo           — Monte Carlo stochastic simulation
  GET  /api/simulation/strategies/{pid}      — compare 5 restocking strategies
  GET  /api/simulation/sensitivity/{pid}     — cost/KPI sensitivity to one parameter
  GET  /api/simulation/runs                  — paginated simulation run history
  GET  /api/simulation/runs/{run_id}         — full result for a past simulation
  DELETE /api/simulation/runs/{run_id}       — delete a simulation run record

Authentication: Bearer JWT required.
Roles:
  read (GET)    — admin, manager, analyst
  run (POST)    — admin, manager, analyst
  delete        — admin only

Prefix: /api/simulation
Tag:    Inventory Simulation
"""

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Path, Query, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_active_user, require_roles
from app.database.connection import get_db
from app.models.user import User
from app.services.simulation_service import (
    run_simulation,
    run_what_if,
    compare_scenarios,
    simulate_seasonal,
    simulate_supplier_disruption,
    run_monte_carlo,
    compare_strategies,
    sensitivity_analysis,
    get_simulation_runs,
    get_simulation_run,
    delete_simulation_run,
    DEFAULT_SIM_DAYS,
    DEFAULT_SEASONAL_FACTORS,
    MONTE_CARLO_TRIALS,
)

logger = logging.getLogger("smart_retail.simulation_routes")

router = APIRouter(prefix="/api/simulation", tags=["Inventory Simulation"])

_READ   = require_roles("admin", "manager", "analyst")
_RUN    = require_roles("admin", "manager", "analyst")
_ADMIN  = require_roles("admin")


# ── Pydantic request bodies ───────────────────────────────────────────────────

class SimulationRunRequest(BaseModel):
    product_id:        int   = Field(..., gt=0, description="Product to simulate")
    simulation_days:   int   = Field(default=DEFAULT_SIM_DAYS, ge=7, le=730)
    initial_stock:     Optional[float] = Field(default=None, ge=0)
    avg_daily_demand:  Optional[float] = Field(default=None, ge=0)
    demand_std:        Optional[float] = Field(default=None, ge=0)
    reorder_point:     Optional[float] = Field(default=None, ge=0)
    order_quantity:    Optional[float] = Field(default=None, ge=1)
    lead_mean_days:    Optional[float] = Field(default=None, ge=0.5)
    lead_std_days:     Optional[float] = Field(default=None, ge=0)
    ordering_cost:     Optional[float] = Field(default=None, ge=0)
    holding_rate:      Optional[float] = Field(default=None, ge=0.01, le=1.0)
    stockout_cost_rate: Optional[float] = Field(default=None, ge=0)
    seasonal_factors:  Optional[Dict[int, float]] = Field(
        default=None,
        description="Monthly seasonal indices keyed by month (1–12). Default: flat demand.",
    )
    seed:              Optional[int]   = Field(default=None, description="Random seed for reproducibility")

    model_config = {"json_schema_extra": {
        "example": {
            "product_id": 1,
            "simulation_days": 180,
            "seed": 42,
        }
    }}


class WhatIfRequest(BaseModel):
    product_id:           int   = Field(..., gt=0)
    simulation_days:      int   = Field(default=DEFAULT_SIM_DAYS, ge=7, le=730)
    demand_multiplier:    Optional[float] = Field(default=None, ge=0.1, le=5.0,
        description="Scale factor on average daily demand. 1.3 = 30% demand increase.")
    safety_stock_add:     Optional[float] = Field(default=None, ge=0,
        description="Extra units to add to initial stock (simulated safety stock increase).")
    reorder_point_override: Optional[float] = Field(default=None, ge=0)
    order_quantity_override: Optional[float] = Field(default=None, ge=1)
    lead_multiplier:      Optional[float] = Field(default=None, ge=0.1, le=10.0,
        description="Scale factor on lead time. 2.0 = supplier lead time doubles.")
    ordering_cost_override: Optional[float] = Field(default=None, ge=0)
    holding_rate_override:  Optional[float] = Field(default=None, ge=0.01, le=1.0)
    seasonal_factors:     Optional[Dict[int, float]] = None
    run_name:             Optional[str] = Field(default=None, max_length=255)
    save:                 bool = Field(default=True, description="Persist result to DB")

    model_config = {"json_schema_extra": {
        "example": {
            "product_id": 1,
            "simulation_days": 180,
            "demand_multiplier": 1.3,
            "safety_stock_add": 50,
            "run_name": "Q4 Demand Surge + Extra Safety Stock",
        }
    }}


class ScenarioItem(BaseModel):
    name:                 str   = Field(default="Scenario", max_length=100)
    demand_multiplier:    Optional[float] = Field(default=None, ge=0.1, le=5.0)
    safety_stock_add:     Optional[float] = Field(default=None, ge=0)
    reorder_point_override: Optional[float] = Field(default=None, ge=0)
    order_quantity_override: Optional[float] = Field(default=None, ge=1)
    lead_multiplier:      Optional[float] = Field(default=None, ge=0.1, le=10.0)
    ordering_cost_override: Optional[float] = Field(default=None, ge=0)
    holding_rate_override:  Optional[float] = Field(default=None, ge=0.01, le=1.0)
    seasonal_factors:     Optional[Dict[int, float]] = None


class CompareRequest(BaseModel):
    product_id:      int            = Field(..., gt=0)
    simulation_days: int            = Field(default=DEFAULT_SIM_DAYS, ge=7, le=730)
    scenarios:       List[ScenarioItem] = Field(..., min_length=1, max_length=10)
    run_name:        Optional[str]  = Field(default=None, max_length=255)
    save:            bool           = Field(default=True)

    model_config = {"json_schema_extra": {
        "example": {
            "product_id": 1,
            "simulation_days": 180,
            "scenarios": [
                {"name": "Increase ROP by 50%", "reorder_point_override": 75},
                {"name": "Double order quantity",  "order_quantity_override": 200},
                {"name": "Demand surge +40%",       "demand_multiplier": 1.4},
            ],
        }
    }}


class SeasonalRequest(BaseModel):
    product_id:      int                      = Field(..., gt=0)
    simulation_days: int                      = Field(default=DEFAULT_SIM_DAYS, ge=7, le=730)
    seasonal_factors: Optional[Dict[int, float]] = Field(
        default=None,
        description="Custom monthly factors (keys 1–12). Omit to use retail defaults.",
    )
    save:            bool = Field(default=True)

    model_config = {"json_schema_extra": {
        "example": {
            "product_id": 1,
            "simulation_days": 365,
            "seasonal_factors": {
                "11": 1.8, "12": 2.1, "1": 0.6
            },
        }
    }}


class SupplierDisruptionRequest(BaseModel):
    product_id:             int              = Field(..., gt=0)
    simulation_days:        int              = Field(default=DEFAULT_SIM_DAYS, ge=7, le=730)
    lead_time_multipliers:  Optional[List[float]] = Field(
        default=None,
        description="List of lead-time multipliers to test. Default: [1.0, 1.5, 2.0, 3.0].",
        max_length=8,
    )
    save:                   bool = Field(default=True)

    model_config = {"json_schema_extra": {
        "example": {
            "product_id": 1,
            "lead_time_multipliers": [1.0, 1.5, 2.0, 2.5, 3.0],
        }
    }}


class MonteCarloRequest(BaseModel):
    product_id:        int   = Field(..., gt=0)
    simulation_days:   int   = Field(default=DEFAULT_SIM_DAYS, ge=7, le=730)
    n_trials:          int   = Field(default=500, ge=100, le=MONTE_CARLO_TRIALS)
    demand_multiplier: float = Field(default=1.0, ge=0.1, le=5.0)
    lead_multiplier:   float = Field(default=1.0, ge=0.1, le=10.0)
    save:              bool  = Field(default=True)

    model_config = {"json_schema_extra": {
        "example": {
            "product_id": 1,
            "simulation_days": 180,
            "n_trials": 500,
            "demand_multiplier": 1.2,
        }
    }}


# ── POST /api/simulation/run ───────────────────────────────────────────────────

@router.post("/run", status_code=status.HTTP_200_OK)
def run_single_simulation(
    body: SimulationRunRequest,
    current_user: User = Depends(_RUN),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """
    **Run a single scenario simulation** for one product.

    All parameters except `product_id` are optional — omitted values are
    derived automatically from the product's live DB data (current stock,
    historical demand, observed lead times).

    Supply parameter overrides to answer specific questions:
    - _"What happens if we start with 200 units instead of 50?"_ → `initial_stock=200`
    - _"What if daily demand is 25 instead of 15?"_ → `avg_daily_demand=25`
    - _"What if we order 150 units at a time?"_ → `order_quantity=150`

    Supply `seasonal_factors` (dict of month → multiplier) to simulate
    seasonal demand patterns.

    Supply `seed` for a deterministic, reproducible run.

    Returns day-by-day KPIs: service level, fill rate, stockout risk,
    carrying/ordering/stockout cost breakdown, inventory turns, days of supply.

    Requires role: **admin**, **manager**, or **analyst**.
    """
    try:
        return run_simulation(
            db=db,
            product_id=body.product_id,
            simulation_days=body.simulation_days,
            initial_stock=body.initial_stock,
            avg_daily_demand=body.avg_daily_demand,
            demand_std=body.demand_std,
            reorder_point=body.reorder_point,
            order_quantity=body.order_quantity,
            lead_mean_days=body.lead_mean_days,
            lead_std_days=body.lead_std_days,
            ordering_cost=body.ordering_cost,
            holding_rate=body.holding_rate,
            stockout_cost_rate=body.stockout_cost_rate,
            seasonal_factors=body.seasonal_factors,
            seed=body.seed,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


# ── POST /api/simulation/what-if ──────────────────────────────────────────────

@router.post("/what-if", status_code=status.HTTP_200_OK)
def what_if_analysis(
    body: WhatIfRequest,
    current_user: User = Depends(_RUN),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """
    **What-If Analysis** — compare the current baseline vs one modified scenario.

    Runs both the baseline (current DB parameters) and a modified scenario,
    then produces a delta analysis showing how each KPI changes.

    Common use cases:
    - `demand_multiplier=1.3` — "What if demand rises 30% (e.g. new promotion)?"
    - `lead_multiplier=2.0` — "What if our supplier's delivery time doubles?"
    - `safety_stock_add=100` — "What if we hold 100 extra units as safety stock?"
    - `reorder_point_override=150` — "What if we raise our reorder trigger to 150 units?"
    - `order_quantity_override=300` — "What if we switch to 300-unit batch orders?"

    Returns:
    - `baseline` — current scenario metrics
    - `scenario` — modified scenario metrics
    - `deltas` — per-metric delta with `improved`/`worsened`/`unchanged` label
    - `insights` — 3–5 actionable plain-language insights
    - `scenario_overrides` — the exact parameter changes applied

    Set `save=false` to skip persisting the result.

    Requires role: **admin**, **manager**, or **analyst**.
    """
    try:
        return run_what_if(
            db=db,
            product_id=body.product_id,
            simulation_days=body.simulation_days,
            demand_multiplier=body.demand_multiplier,
            safety_stock_add=body.safety_stock_add,
            reorder_point_override=body.reorder_point_override,
            order_quantity_override=body.order_quantity_override,
            lead_multiplier=body.lead_multiplier,
            ordering_cost_override=body.ordering_cost_override,
            holding_rate_override=body.holding_rate_override,
            seasonal_factors=body.seasonal_factors,
            run_name=body.run_name,
            created_by=current_user.username,
            save=body.save,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


# ── POST /api/simulation/compare ─────────────────────────────────────────────

@router.post("/compare", status_code=status.HTTP_200_OK)
def compare_scenarios_endpoint(
    body: CompareRequest,
    current_user: User = Depends(_RUN),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """
    **Multi-Scenario Comparison** — rank up to 10 named scenarios side-by-side.

    Each scenario entry can override any combination of:
    demand_multiplier, safety_stock_add, reorder_point_override,
    order_quantity_override, lead_multiplier, ordering_cost_override,
    holding_rate_override, seasonal_factors.

    All scenarios are compared against the same baseline (current DB parameters).
    Scenarios are scored using a composite metric:

    > score = 0.50 × service_level + 0.30 × (1 – stockout_risk) + 0.20 × (1 – normalised_cost)

    Returns:
    - `ranked_scenarios` — all scenarios (+ baseline) sorted by composite score
    - `best_scenario` — name of the top-ranked scenario
    - `summary` — best service level, lowest cost, lowest stockout risk
    - `scoring_weights` — the weight breakdown used for ranking

    Requires role: **admin**, **manager**, or **analyst**.
    """
    try:
        return compare_scenarios(
            db=db,
            product_id=body.product_id,
            scenarios=[s.model_dump(exclude_none=True) for s in body.scenarios],
            simulation_days=body.simulation_days,
            created_by=current_user.username,
            run_name=body.run_name,
            save=body.save,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


# ── POST /api/simulation/seasonal ────────────────────────────────────────────

@router.post("/seasonal", status_code=status.HTTP_200_OK)
def seasonal_simulation(
    body: SeasonalRequest,
    current_user: User = Depends(_RUN),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """
    **Seasonal Demand Simulation** — compare flat-demand baseline vs
    seasonally-modulated demand.

    If `seasonal_factors` is omitted, the retail-standard indices are used:

    | Month | Factor | Label   |
    |-------|--------|---------|
    | Jan   | 0.70   | Low     |
    | Jun   | 1.00   | Normal  |
    | Nov   | 1.40   | Peak    |
    | Dec   | 1.60   | Peak    |

    Returns:
    - `baseline` — flat demand simulation
    - `seasonal_scenario` — seasonally-adjusted simulation
    - `monthly_insights` — per-month demand, factor, and risk label
    - `deltas` — KPI comparison between the two runs
    - `insights` — actionable recommendations for peak-season preparation

    Requires role: **admin**, **manager**, or **analyst**.
    """
    try:
        return simulate_seasonal(
            db=db,
            product_id=body.product_id,
            simulation_days=body.simulation_days,
            seasonal_factors=body.seasonal_factors,
            created_by=current_user.username,
            save=body.save,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


# ── POST /api/simulation/supplier-disruption ─────────────────────────────────

@router.post("/supplier-disruption", status_code=status.HTTP_200_OK)
def supplier_disruption_simulation(
    body: SupplierDisruptionRequest,
    current_user: User = Depends(_RUN),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """
    **Supplier Lead-Time Stress Test** — simulate the inventory impact of
    supplier delays.

    Runs the simulation at each lead-time multiplier (default: 1×, 1.5×, 2×, 3×)
    and compares service levels, stockout risk, and total cost.

    Returns:
    - `scenarios` — all multiplier scenarios with full KPIs
    - `worst_case` — name of the highest-risk scenario
    - `tipping_point_scenario` — first scenario where service level drops below 95%
    - `insights` — stockout risk at 2× lead time, cost impact, mitigation advice

    Use this to determine:
    - At what lead-time delay does service level fall below acceptable thresholds?
    - How much does a 2× lead-time delay cost in stockout expenses?
    - Is the current safety stock buffer sufficient against disruptions?

    Requires role: **admin**, **manager**, or **analyst**.
    """
    try:
        return simulate_supplier_disruption(
            db=db,
            product_id=body.product_id,
            simulation_days=body.simulation_days,
            lead_time_multipliers=body.lead_time_multipliers,
            created_by=current_user.username,
            save=body.save,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


# ── POST /api/simulation/monte-carlo ─────────────────────────────────────────

@router.post("/monte-carlo", status_code=status.HTTP_200_OK)
def monte_carlo_simulation(
    body: MonteCarloRequest,
    current_user: User = Depends(_RUN),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """
    **Monte Carlo Stochastic Simulation** — quantify uncertainty in inventory outcomes.

    Runs `n_trials` independent replications (each with a different random seed)
    and computes probability distributions over all KPIs.

    Returns for each KPI (service_level, fill_rate, stockout_risk, total_cost,
    carrying_cost, ordering_cost, avg_stock_level):
    - mean, std
    - percentiles: p5, p25, p50 (median), p75, p95

    Key outputs:
    - `probability_of_stockout_pct` — % of trials with at least one stockout
    - `expected_service_level_pct` — mean service level across all trials
    - `value_at_risk_95_pct` — 95th-percentile total cost (worst-expected cost)
    - `insights` — probability interpretation and improvement recommendations

    Use `demand_multiplier > 1.0` or `lead_multiplier > 1.0` to stress-test
    the distribution under adverse conditions.

    Requires role: **admin**, **manager**, or **analyst**.
    """
    try:
        return run_monte_carlo(
            db=db,
            product_id=body.product_id,
            simulation_days=body.simulation_days,
            n_trials=body.n_trials,
            demand_multiplier=body.demand_multiplier,
            lead_multiplier=body.lead_multiplier,
            created_by=current_user.username,
            save=body.save,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


# ── GET /api/simulation/strategies/{product_id} ───────────────────────────────

@router.get("/strategies/{product_id}", status_code=status.HTTP_200_OK)
def restocking_strategy_comparison(
    product_id: int = Path(..., gt=0),
    simulation_days: int = Query(default=DEFAULT_SIM_DAYS, ge=7, le=730),
    current_user: User = Depends(_READ),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """
    **Restocking Strategy Comparison** — evaluate 5 inventory strategies.

    Strategies compared:
    1. **FOQ** — Fixed Order Quantity (2-week supply per order)
    2. **EOQ** — Economic Order Quantity (Q*=√(2DS/H), minimises total cost)
    3. **EOQ + Safety Stock (95%)** — EOQ with safety buffer for 95% service level
    4. **EOQ + Safety Stock (99%)** — EOQ with safety buffer for 99% service level
    5. **Min-Max Policy** — order when below minimum level, up to maximum level

    Each strategy is scored using the composite metric:
    > 0.50 × service_level + 0.30 × stockout_avoidance + 0.20 × cost_efficiency

    Returns:
    - `ranked_strategies` — all 5 strategies sorted by composite score
    - `recommended_strategy` — the top-ranked strategy name
    - `eoq` — calculated Economic Order Quantity for this product
    - `summary` — best service level, lowest cost, best fill rate
    - `insights` — plain-language recommendation and key findings

    Requires role: **admin**, **manager**, or **analyst**.
    """
    try:
        return compare_strategies(db=db, product_id=product_id, simulation_days=simulation_days)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


# ── GET /api/simulation/sensitivity/{product_id} ──────────────────────────────

@router.get("/sensitivity/{product_id}", status_code=status.HTTP_200_OK)
def parameter_sensitivity(
    product_id: int = Path(..., gt=0),
    parameter: str = Query(
        default="order_quantity",
        description=(
            "Parameter to vary. Options: order_quantity | reorder_point | "
            "safety_stock_add | demand_multiplier | lead_multiplier | holding_rate"
        ),
    ),
    simulation_days: int = Query(default=DEFAULT_SIM_DAYS, ge=7, le=730),
    current_user: User = Depends(_READ),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """
    **Sensitivity Analysis** — vary one parameter and observe how KPIs change.

    Sweeps the chosen parameter across multipliers: 0.5×, 0.75×, 1.0× (baseline),
    1.25×, 1.5×, 2.0× and records how service level, fill rate, stockout risk,
    carrying cost, ordering cost, and total cost respond.

    Supported parameters:
    - `order_quantity`    — effect of ordering more or fewer units per order
    - `reorder_point`     — effect of triggering orders earlier or later
    - `safety_stock_add`  — effect of holding additional buffer stock
    - `demand_multiplier` — effect of demand volume changes
    - `lead_multiplier`   — effect of lead time changes
    - `holding_rate`      — effect of changing the annual holding cost rate

    Returns:
    - `data_points` — one row per multiplier with all KPI values + composite score
    - `optimal_multiplier` — the multiplier with the best composite score
    - `optimal_value` — the actual parameter value at the optimum
    - `insights` — plain-language interpretation

    Requires role: **admin**, **manager**, or **analyst**.
    """
    valid = ("order_quantity", "reorder_point", "safety_stock_add",
             "demand_multiplier", "lead_multiplier", "holding_rate")
    if parameter not in valid:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid parameter '{parameter}'. Valid options: {list(valid)}",
        )
    try:
        return sensitivity_analysis(
            db=db,
            product_id=product_id,
            simulation_days=simulation_days,
            parameter=parameter,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


# ── GET /api/simulation/runs ──────────────────────────────────────────────────

@router.get("/runs", status_code=status.HTTP_200_OK)
def list_simulation_runs(
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    product_id: Optional[int] = Query(default=None),
    simulation_type: Optional[str] = Query(
        default=None,
        description=(
            "Filter by type: what_if | scenario_compare | seasonal | "
            "supplier_disruption | monte_carlo | strategy_compare"
        ),
    ),
    current_user: User = Depends(_READ),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """
    **Paginated simulation run history.**

    Returns a lightweight summary of each past run (no full timeseries):
    run_id, run_name, product_id, simulation_type, baseline vs scenario
    service levels, cost savings, stockout risk, created_by, created_at.

    Filter by `product_id` or `simulation_type` to narrow results.
    Use `GET /runs/{run_id}` to fetch the full result for a specific run.

    Requires role: **admin**, **manager**, or **analyst**.
    """
    return get_simulation_runs(
        db=db,
        skip=skip,
        limit=limit,
        product_id=product_id,
        simulation_type=simulation_type,
        created_by=None,
    )


# ── GET /api/simulation/runs/{run_id} ─────────────────────────────────────────

@router.get("/runs/{run_id}", status_code=status.HTTP_200_OK)
def get_run_detail(
    run_id: str = Path(..., min_length=10, max_length=64),
    current_user: User = Depends(_READ),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """
    **Full simulation run result** by run_id.

    Returns the complete result including:
    - Input `parameters`
    - Full `baseline_result` and `scenario_result` JSON
    - `comparison_summary` (insights list)
    - All scalar KPI columns

    Requires role: **admin**, **manager**, or **analyst**.
    """
    result = get_simulation_run(db=db, run_id=run_id)
    if not result:
        raise HTTPException(
            status_code=404,
            detail=f"Simulation run '{run_id}' not found.",
        )
    return result


# ── DELETE /api/simulation/runs/{run_id} ──────────────────────────────────────

@router.delete("/runs/{run_id}", status_code=status.HTTP_200_OK)
def delete_run(
    run_id: str = Path(..., min_length=10, max_length=64),
    current_user: User = Depends(_ADMIN),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """
    **Delete a simulation run record.** Requires role: **admin**.

    Permanently removes the simulation run and its stored results from the DB.
    This action is irreversible.
    """
    deleted = delete_simulation_run(db=db, run_id=run_id)
    if not deleted:
        raise HTTPException(
            status_code=404,
            detail=f"Simulation run '{run_id}' not found.",
        )
    return {"deleted": True, "run_id": run_id}
