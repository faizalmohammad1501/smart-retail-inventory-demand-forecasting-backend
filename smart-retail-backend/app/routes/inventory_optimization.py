"""
Predictive Inventory Optimization API Routes
=============================================
Exposes the inventory optimization engine through a clean REST interface.

Endpoints
---------
  GET  /api/optimization/summary          — comprehensive one-screen report
  GET  /api/optimization/eoq              — Economic Order Quantity analysis
  GET  /api/optimization/safety-stock     — multi-service-level safety stock matrix
  GET  /api/optimization/reorder-points   — dynamic reorder points vs current levels
  GET  /api/optimization/turnover         — inventory turnover + GMROI + DIO
  GET  /api/optimization/holding-costs    — annual holding cost breakdown per product
  GET  /api/optimization/fill-rate        — demand satisfaction (fill rate) analysis
  GET  /api/optimization/reorder-calendar — projected order schedule (next N days)
  GET  /api/optimization/portfolio        — budget-constrained capital allocation
  GET  /api/optimization/scenarios/{id}   — EOQ + service-level sensitivity for one product

Authentication: Bearer JWT required.
Roles:          admin, manager, analyst (read-only on all endpoints).

Prefix: /api/optimization
Tag:    Inventory Optimization
"""

import logging
from typing import Any, Dict

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_active_user, require_roles
from app.database.connection import get_db
from app.models.user import User
from app.services.inventory_optimization_service import (
    get_optimization_summary,
    calculate_eoq_analysis,
    calculate_safety_stock_matrix,
    calculate_dynamic_reorder_points,
    calculate_inventory_turnover,
    calculate_holding_costs,
    calculate_fill_rate_analysis,
    generate_reorder_calendar,
    optimize_capital_allocation,
    calculate_order_quantity_scenarios,
    DEFAULT_ORDERING_COST,
    DEFAULT_HOLDING_COST_RATE,
)

logger = logging.getLogger("smart_retail.optimization_routes")

router = APIRouter(prefix="/api/optimization", tags=["Inventory Optimization"])

_READ = require_roles("admin", "manager", "analyst")


# ── GET /api/optimization/summary ────────────────────────────────────────────

@router.get("/summary", status_code=status.HTTP_200_OK)
def optimization_summary(
    ordering_cost: float = Query(
        default=DEFAULT_ORDERING_COST, ge=0,
        description="Cost per purchase order in $ (default $50)"),
    holding_cost_rate: float = Query(
        default=DEFAULT_HOLDING_COST_RATE, ge=0.01, le=1.0,
        description="Annual holding cost as fraction of unit value (default 0.25 = 25%)"),
    lookback_days: int = Query(default=90, ge=14, le=365),
    _: User = Depends(_READ),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """
    **Comprehensive optimization dashboard** — single-call overview.

    Combines all optimization engines into one response:
    - EOQ portfolio savings opportunity
    - Reorder point calibration status (too_low / adequate / too_high counts)
    - Inventory turnover portfolio summary
    - Total annual holding cost and potential savings
    - Portfolio fill rate
    - Upcoming reorder events (next 30 days)
    - Top 5 products with greatest EOQ savings
    - Top 10 urgent/overdue reorder events
    - Bottom 5 products by fill rate
    - Top 5 products by holding cost

    Use this as the "first screen" of the optimization module.
    """
    return get_optimization_summary(
        db,
        ordering_cost=ordering_cost,
        holding_cost_rate=holding_cost_rate,
        lookback_days=lookback_days,
    )


# ── GET /api/optimization/eoq ─────────────────────────────────────────────────

@router.get("/eoq", status_code=status.HTTP_200_OK)
def eoq_analysis(
    ordering_cost: float = Query(
        default=DEFAULT_ORDERING_COST, ge=0,
        description="Fixed cost per purchase order ($)"),
    holding_cost_rate: float = Query(
        default=DEFAULT_HOLDING_COST_RATE, ge=0.01, le=1.0,
        description="Annual holding cost rate (fraction of unit value)"),
    lookback_days: int = Query(default=90, ge=14, le=365),
    _: User = Depends(_READ),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """
    **Economic Order Quantity (EOQ) analysis** — optimal order sizes to minimise
    total annual inventory costs.

    Formula: **Q\* = √(2DS/H)**
    where D = annual demand, S = ordering cost/order, H = holding cost/unit/year.

    For each product returns:
    - `eoq` — optimal order quantity (units)
    - `orders_per_year_eoq` — how many purchase orders per year at EOQ
    - `order_cycle_days_eoq` — days between orders at EOQ
    - `total_annual_cost_eoq` — minimum achievable total cost
    - `total_annual_cost_current` — current cost at `reorder_level` quantity
    - `annual_savings` — potential saving by switching to EOQ
    - `recommendation` — "Increase / Decrease / Near optimal"

    Portfolio summary shows total annual savings opportunity.
    Tip: adjust `ordering_cost` and `holding_cost_rate` to match your
    actual costs — results are highly sensitive to these parameters.
    """
    return calculate_eoq_analysis(
        db,
        ordering_cost=ordering_cost,
        holding_cost_rate=holding_cost_rate,
        lookback_days=lookback_days,
    )


# ── GET /api/optimization/safety-stock ────────────────────────────────────────

@router.get("/safety-stock", status_code=status.HTTP_200_OK)
def safety_stock_matrix(
    lookback_days: int = Query(default=90, ge=14, le=365),
    _: User = Depends(_READ),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """
    **Multi-level safety stock matrix** — 90/95/98/99% service levels per product.

    Uses the full lead-time + demand variability formula:

    **SS(z) = z × √(μ_L × σ_d² + σ_L² × μ_d²)**

    where μ_d = avg daily demand, σ_d = demand std-dev,
    μ_L = avg lead time (days), σ_L = lead time std-dev.

    For each service level returns:
    - `safety_stock_units` — buffer stock required
    - `reorder_point` — trigger point for placing a new order
    - `annual_holding_cost` — cost of carrying this safety stock
    - `coverage_days` — how many extra days of buffer above mean demand

    Use this to make an informed trade-off between service level
    (customer satisfaction) and holding cost (capital).
    """
    return calculate_safety_stock_matrix(db, lookback_days=lookback_days)


# ── GET /api/optimization/reorder-points ─────────────────────────────────────

@router.get("/reorder-points", status_code=status.HTTP_200_OK)
def dynamic_reorder_points(
    service_level_pct: int = Query(
        default=95, ge=80, le=99,
        description="Target service level percentage (80–99)"),
    lookback_days: int = Query(default=90, ge=14, le=365),
    _: User = Depends(_READ),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """
    **Dynamic reorder point calibration** — compares mathematically derived
    reorder points against the values currently set in the product catalogue.

    Dynamic ROP accounts for both demand variability **and** lead-time variability
    (unlike a simple `avg_demand × lead_time` formula).

    For each product:
    - `rop_dynamic` — statistically correct reorder point at the chosen service level
    - `rop_simple` — naive ROP (no safety stock)
    - `current_reorder_level` — what is set in the catalogue today
    - `rop_gap` — positive means the current level is dangerously low
    - `status` — `too_low` | `adequate` | `too_high`
    - `below_rop_now` — True if current stock has already passed the trigger
    - `update_recommendation` — plain-language advice

    Summary shows counts of products needing calibration updates.
    """
    return calculate_dynamic_reorder_points(
        db,
        service_level_pct=service_level_pct,
        lookback_days=lookback_days,
    )


# ── GET /api/optimization/turnover ───────────────────────────────────────────

@router.get("/turnover", status_code=status.HTTP_200_OK)
def inventory_turnover(
    lookback_days: int = Query(default=365, ge=30, le=730),
    _: User = Depends(_READ),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """
    **Inventory turnover rate, Days Inventory Outstanding (DIO), and GMROI.**

    Metrics per product:
    - `inventory_turnover` — units sold / avg inventory units in the window
    - `days_inventory_outstanding` — lookback_days / turnover (lower = better)
    - `gmroi_pct` — Gross Margin Return on Inventory Investment (%)
    - `turnover_status` — `no_movement` | `slow` (<2) | `healthy` (2–6) | `fast` (>6)
    - `gmroi_status` — `poor` (<100%) | `fair` (100–200%) | `excellent` (>200%)

    Benchmarks: turnover < 2 → overstocked; > 6 → lean but stockout risk.
    GMROI: > 200% is excellent; < 100% signals poor inventory ROI.

    Category summary shows which categories are driving the best/worst returns.
    """
    return calculate_inventory_turnover(db, lookback_days=lookback_days)


# ── GET /api/optimization/holding-costs ──────────────────────────────────────

@router.get("/holding-costs", status_code=status.HTTP_200_OK)
def holding_costs(
    holding_cost_rate: float = Query(
        default=DEFAULT_HOLDING_COST_RATE, ge=0.01, le=1.0),
    _: User = Depends(_READ),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """
    **Annual holding cost breakdown** per product and category.

    Decomposes total holding cost (25% of inventory value/year by default) into:
    - `capital_cost_annual` (40% of holding rate) — cost of capital tied up
    - `storage_cost_annual` (32%) — warehousing, logistics, handling
    - `insurance_cost_annual` (12%) — insurance and shrinkage
    - `obsolescence_cost_annual` (16%) — obsolescence and deterioration risk

    Also returns:
    - `inventory_value` — current stock × unit price
    - `total_holding_cost_annual` — sum of all components
    - `holding_cost_daily` — daily carrying cost (useful for stockout penalty calc)
    - `holding_cost_pct_of_value` — sanity-check that matches the input rate

    Portfolio summary shows total holding cost and potential 10% reduction savings.
    Use this to identify which products are consuming the most capital.
    """
    return calculate_holding_costs(db, holding_cost_rate=holding_cost_rate)


# ── GET /api/optimization/fill-rate ──────────────────────────────────────────

@router.get("/fill-rate", status_code=status.HTTP_200_OK)
def fill_rate_analysis(
    lookback_days: int = Query(default=90, ge=14, le=365),
    _: User = Depends(_READ),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """
    **Demand fill rate analysis** — proportion of demand fulfilled without delay.

    Fill rate = fulfilled demand days / total demand days × 100.

    Products with zero current stock receive a conservative 20% stockout-day
    estimate as a proxy for actual unfulfilled demand (since the platform does
    not record explicit lost-sale events).

    Status tiers:
    - `excellent` ≥ 98% | `good` ≥ 95% | `fair` ≥ 90% | `poor` < 90%

    Results are sorted worst-first so actionable items appear at the top.
    Category summary shows which categories have systemic fulfilment issues.
    """
    return calculate_fill_rate_analysis(db, lookback_days=lookback_days)


# ── GET /api/optimization/reorder-calendar ────────────────────────────────────

@router.get("/reorder-calendar", status_code=status.HTTP_200_OK)
def reorder_calendar(
    horizon_days: int = Query(default=90, ge=7, le=180,
                              description="Planning horizon in days"),
    lookback_days: int = Query(default=90, ge=14, le=365),
    _: User = Depends(_READ),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """
    **Projected reorder calendar** for the next `horizon_days` days.

    For each product, calculates:
    - `order_date` — when to place the next purchase order
    - `expected_arrival` — estimated delivery date (order_date + lead_time)
    - `urgency` — `OVERDUE` (already past ROP) | `URGENT` (≤7 days) |
                  `SOON` (8–21 days) | `PLANNED` (>21 days)

    Events are returned in chronological order AND grouped by ISO week for
    a calendar-view integration (`weekly_calendar` field).

    Only products within the horizon window are included — long-shelf-life
    items ordered infrequently will not appear in a short horizon.
    """
    return generate_reorder_calendar(
        db,
        horizon_days=horizon_days,
        lookback_days=lookback_days,
    )


# ── GET /api/optimization/portfolio ──────────────────────────────────────────

@router.get("/portfolio", status_code=status.HTTP_200_OK)
def capital_allocation(
    budget: float = Query(
        ..., gt=0,
        description="Total procurement budget available ($) — required"),
    ordering_cost: float = Query(default=DEFAULT_ORDERING_COST, ge=0),
    holding_cost_rate: float = Query(default=DEFAULT_HOLDING_COST_RATE, ge=0.01, le=1.0),
    lookback_days: int = Query(default=90, ge=14, le=365),
    _: User = Depends(_READ),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """
    **Budget-constrained capital allocation optimisation.**

    Given a fixed procurement budget, allocates spending across products in
    ROI priority order.  Products are ranked by:
      ROI score = annual_revenue / (holding_cost × EOQ)
    with a 2× urgency boost for products currently below their reorder level.

    Returns:
    - Full allocation list (which products to buy, how many units, at what cost)
    - Budget utilisation % and remaining budget
    - Expected revenue lift from the allocation
    - Unmet demand value (products that couldn't be fully funded)

    The `budget` parameter is **required** — pass it as a query param.

    Example: `GET /api/optimization/portfolio?budget=50000`
    """
    return optimize_capital_allocation(
        db,
        budget=budget,
        lookback_days=lookback_days,
        ordering_cost=ordering_cost,
        holding_cost_rate=holding_cost_rate,
    )


# ── GET /api/optimization/scenarios/{product_id} ─────────────────────────────

@router.get("/scenarios/{product_id}", status_code=status.HTTP_200_OK)
def order_quantity_scenarios(
    product_id: int,
    ordering_cost: float = Query(default=DEFAULT_ORDERING_COST, ge=0),
    holding_cost_rate: float = Query(default=DEFAULT_HOLDING_COST_RATE, ge=0.01, le=1.0),
    lookback_days: int = Query(default=90, ge=14, le=365),
    _: User = Depends(_READ),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """
    **EOQ + service-level sensitivity analysis** for a single product.

    Returns two views:

    **1. Cost curve** (`cost_curve`) — annual total cost at 8 order-quantity
    multiples of EOQ (0.25×, 0.5×, 0.75×, 1.0×, 1.25×, 1.5×, 2.0×, 3.0×).
    Demonstrates the characteristic U-shape: ordering too little or too much
    both increase total cost.  `is_optimal=true` marks the EOQ point.

    **2. Service level scenarios** (`service_level_scenarios`) — safety stock
    requirements and annual holding costs at 90/95/98/99% service levels,
    letting managers make an informed trade-off between customer service and
    holding cost.

    Useful for product-level deep-dive in the procurement planning workflow.
    """
    from fastapi import HTTPException
    try:
        return calculate_order_quantity_scenarios(
            db,
            product_id=product_id,
            lookback_days=lookback_days,
            ordering_cost=ordering_cost,
            holding_cost_rate=holding_cost_rate,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
