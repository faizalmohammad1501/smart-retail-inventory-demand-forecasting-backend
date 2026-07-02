"""
Inventory Simulation & What-If Analysis Engine
================================================
Provides a suite of simulation and scenario-analysis tools that allow
business analysts to evaluate the impact of strategic inventory decisions
before committing real resources.

Core engine: Discrete Event Simulation (DES)
--------------------------------------------
Every simulation steps through a virtual timeline one day at a time,
modelling:
  • Stochastic daily demand  (normal or seasonally-adjusted)
  • Stochastic supplier lead time
  • Reorder-point triggered restocking
  • Stockout events and their cost
  • Daily inventory carrying costs

This lets the engine answer questions like:
  "If lead time doubles, how often will we run out of stock?"
  "Would raising safety stock by 50 % actually reduce total cost?"
  "Which restocking strategy minimises cost at our current fill-rate target?"

Mathematical foundations
------------------------
  Safety stock (SS):
      SS = z × √(μ_L × σ_d² + σ_L² × μ_d²)

  Reorder point (ROP):
      ROP = μ_d × μ_L + SS

  EOQ:
      Q* = √(2 × D_annual × S / H)

  Service level (cycle service level):
      SL = 1 - stockout_days / simulation_days

  Fill rate:
      FR = total_units_fulfilled / total_units_demanded

  Annual inventory turns:
      turns = (sim_days / 365 × daily_demand × 365) / avg_stock

  Carrying cost (daily):
      CC_day = stock_on_hand × unit_price × holding_rate / 365

  Stockout cost (per unit short):
      SC_unit = unit_price × stockout_cost_rate

Simulation modes
----------------
  run_simulation()          — single scenario discrete-event simulation
  run_what_if()             — baseline vs one modified scenario
  compare_scenarios()       — baseline vs N named scenarios, ranked
  simulate_seasonal()       — demand driven by monthly seasonal index
  simulate_supplier_disruption() — lead-time stress test
  run_monte_carlo()         — 1 000-trial stochastic simulation
  compare_strategies()      — FOQ vs EOQ vs periodic-review
  sensitivity_analysis()    — total cost sensitivity to order-qty changes
"""

import json
import logging
import math
import random
import statistics
import uuid
from collections import defaultdict
from datetime import datetime, date, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.inventory import Inventory
from app.models.product import Product
from app.models.sales import Order
from app.models.supplier import Supplier
from app.models.simulation_run import SimulationRun

logger = logging.getLogger("smart_retail.simulation")

# ── Engine constants ──────────────────────────────────────────────────────────
DEFAULT_SIM_DAYS         = 180    # 6-month horizon
DEFAULT_HOLDING_RATE     = 0.25   # 25 % of unit value / year
DEFAULT_ORDERING_COST    = 50.0   # $ per purchase order
DEFAULT_STOCKOUT_RATE    = 2.0    # multiplier on unit price per short unit
DEFAULT_LEAD_MEAN        = 7.0    # days
DEFAULT_LEAD_STD         = 1.5    # days
DEFAULT_DEMAND_LOOKBACK  = 90     # days to estimate demand statistics
MONTE_CARLO_TRIALS       = 1_000
MAX_SIM_DAYS             = 730    # safety cap
SERVICE_LEVEL_Z = {
    0.90: 1.28, 0.95: 1.65, 0.98: 2.05, 0.99: 2.33,
}

# Monthly seasonal indices (retail defaults; can be overridden per call)
DEFAULT_SEASONAL_FACTORS: Dict[int, float] = {
    1: 0.70, 2: 0.75, 3: 0.85, 4: 0.90, 5: 0.95, 6: 1.00,
    7: 0.95, 8: 0.90, 9: 1.00, 10: 1.10, 11: 1.40, 12: 1.60,
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _r(v, n: int = 4) -> float:
    return round(float(v), n) if v is not None and math.isfinite(float(v)) else 0.0


def _r2(v) -> float:
    return _r(v, 2)


def _safe_sqrt(v: float) -> float:
    return math.sqrt(max(0.0, v))


def _run_id() -> str:
    return str(uuid.uuid4()).replace("-", "")[:24]


# ── Step 0: load product parameters from DB ──────────────────────────────────

def _load_product_params(product_id: int, db: Session) -> Dict[str, Any]:
    """
    Derive simulation baseline parameters for a product from real DB data.

    Returns a dict with:
      unit_price, reorder_level, initial_stock, avg_daily_demand,
      demand_std, lead_mean_days, lead_std_days, category, product_name, sku
    """
    product = db.query(Product).filter(Product.id == product_id).first()
    if not product:
        raise ValueError(f"Product {product_id} not found.")

    # ── Current stock ─────────────────────────────────────────────────────────
    inv = (
        db.query(Inventory)
        .filter(Inventory.product_id == product_id)
        .first()
    )
    initial_stock = int(inv.quantity_available) if inv else 0

    # ── Historical daily demand (last 90 days) ────────────────────────────────
    start = _now() - timedelta(days=DEFAULT_DEMAND_LOOKBACK)
    rows = (
        db.query(
            func.date(Order.order_placed_at).label("day"),
            func.sum(Order.quantity).label("qty"),
        )
        .filter(
            Order.product_id == product_id,
            Order.order_placed_at >= start,
        )
        .group_by(func.date(Order.order_placed_at))
        .all()
    )
    daily_qtys = [float(r.qty or 0) for r in rows]
    if len(daily_qtys) >= 2:
        avg_demand = statistics.mean(daily_qtys)
        demand_std = statistics.stdev(daily_qtys)
    elif len(daily_qtys) == 1:
        avg_demand = daily_qtys[0]
        demand_std = avg_demand * 0.2
    else:
        avg_demand = max(1.0, (product.reorder_level or 10) / 7.0)
        demand_std = avg_demand * 0.3

    # ── Historical lead times from orders ─────────────────────────────────────
    lead_rows = (
        db.query(Order.procurement_time)
        .filter(
            Order.supplier_id.isnot(None),
            Order.procurement_time.isnot(None),
            Order.order_placed_at >= start,
        )
        .all()
    )
    lead_hours = [r[0] for r in lead_rows if r[0] and r[0] > 0]
    if len(lead_hours) >= 2:
        lead_mean_days = statistics.mean(lead_hours) / 24
        lead_std_days  = statistics.stdev(lead_hours) / 24
    else:
        lead_mean_days = DEFAULT_LEAD_MEAN
        lead_std_days  = DEFAULT_LEAD_STD

    # ── EOQ-based order quantity ──────────────────────────────────────────────
    annual_demand = avg_demand * 365
    h = (product.unit_price or 1.0) * DEFAULT_HOLDING_RATE
    eoq = _safe_sqrt(2 * annual_demand * DEFAULT_ORDERING_COST / h) if h > 0 else 100.0

    # ── Safety stock & reorder point ──────────────────────────────────────────
    z = SERVICE_LEVEL_Z[0.95]
    safety_stock = z * _safe_sqrt(
        lead_mean_days * demand_std ** 2
        + lead_std_days ** 2 * avg_demand ** 2
    )
    rop = avg_demand * lead_mean_days + safety_stock

    return {
        "product_id":       product.id,
        "product_name":     product.product_name,
        "sku":              product.sku,
        "category":         product.category or "Unknown",
        "unit_price":       float(product.unit_price or 1.0),
        "initial_stock":    initial_stock,
        "avg_daily_demand": _r2(avg_demand),
        "demand_std":       _r2(max(0.1, demand_std)),
        "lead_mean_days":   _r2(lead_mean_days),
        "lead_std_days":    _r2(max(0.1, lead_std_days)),
        "reorder_point":    _r2(max(rop, product.reorder_level or rop)),
        "order_quantity":   _r2(max(1.0, eoq)),
        "safety_stock":     _r2(safety_stock),
        "ordering_cost":    DEFAULT_ORDERING_COST,
        "holding_rate":     DEFAULT_HOLDING_RATE,
        "stockout_cost_rate": DEFAULT_STOCKOUT_RATE,
    }


# ── CORE: Discrete Event Simulation ──────────────────────────────────────────

class _SimResult:
    """Mutable accumulator for one simulation run."""
    __slots__ = (
        "daily_stock", "daily_demand", "daily_fulfilled",
        "stockout_days", "stockout_units",
        "orders_placed", "total_carrying_cost", "total_ordering_cost",
        "total_stockout_cost", "pending_orders",
    )

    def __init__(self):
        self.daily_stock:         List[float] = []
        self.daily_demand:        List[float] = []
        self.daily_fulfilled:     List[float] = []
        self.stockout_days:       int   = 0
        self.stockout_units:      float = 0.0
        self.orders_placed:       int   = 0
        self.total_carrying_cost: float = 0.0
        self.total_ordering_cost: float = 0.0
        self.total_stockout_cost: float = 0.0
        self.pending_orders:      Dict  = {}   # {arrival_day: qty}


def _discrete_event_simulation(
    initial_stock:    float,
    avg_daily_demand: float,
    demand_std:       float,
    reorder_point:    float,
    order_quantity:   float,
    lead_mean_days:   float,
    lead_std_days:    float,
    unit_price:       float,
    ordering_cost:    float,
    holding_rate:     float,
    stockout_cost_rate: float,
    simulation_days:  int,
    seasonal_factors: Optional[Dict[int, float]] = None,
    seed:             Optional[int] = None,
    start_date:       Optional[date] = None,
) -> Dict[str, Any]:
    """
    Run a single discrete-event inventory simulation.

    Returns a rich metrics dict including daily timeseries and aggregate KPIs.
    """
    if seed is not None:
        random.seed(seed)

    sim_start = start_date or date.today()
    r = _SimResult()
    stock = max(0.0, float(initial_stock))
    daily_holding = unit_price * holding_rate / 365.0
    stockout_cost_per_unit = unit_price * stockout_cost_rate
    n = min(int(simulation_days), MAX_SIM_DAYS)
    order_in_transit = False  # prevent double-ordering

    pending: Dict[int, float] = {}   # {day_index: qty_arriving}

    for day_idx in range(n):
        current_date = sim_start + timedelta(days=day_idx)
        month = current_date.month

        # ── 1. Receive arriving orders ────────────────────────────────────────
        if day_idx in pending:
            stock += pending.pop(day_idx)
            order_in_transit = False

        # ── 2. Realise demand ─────────────────────────────────────────────────
        sf = (seasonal_factors or {}).get(month, 1.0)
        raw_demand = random.gauss(avg_daily_demand * sf, demand_std * sf)
        demand = max(0.0, raw_demand)

        fulfilled = min(stock, demand)
        short     = demand - fulfilled
        stock    -= fulfilled

        r.daily_demand.append(_r2(demand))
        r.daily_fulfilled.append(_r2(fulfilled))

        if short > 0.001:
            r.stockout_days  += 1
            r.stockout_units += short
            r.total_stockout_cost += short * stockout_cost_per_unit

        # ── 3. Daily carrying cost ────────────────────────────────────────────
        r.total_carrying_cost += stock * daily_holding

        r.daily_stock.append(_r2(stock))

        # ── 4. Reorder check ──────────────────────────────────────────────────
        if stock <= reorder_point and not order_in_transit:
            raw_lt  = random.gauss(lead_mean_days, lead_std_days)
            lt_days = max(1, round(raw_lt))
            arrival = day_idx + lt_days
            pending[arrival] = pending.get(arrival, 0) + order_quantity
            r.orders_placed       += 1
            r.total_ordering_cost += ordering_cost
            order_in_transit = True

    # ── Aggregate KPIs ────────────────────────────────────────────────────────
    total_demand    = sum(r.daily_demand)
    total_fulfilled = sum(r.daily_fulfilled)
    avg_stock       = statistics.mean(r.daily_stock) if r.daily_stock else 0.0
    max_stock       = max(r.daily_stock) if r.daily_stock else 0.0
    min_stock       = min(r.daily_stock) if r.daily_stock else 0.0

    service_level   = _r2(1 - r.stockout_days / n) * 100 if n > 0 else 100.0
    fill_rate       = _r2(total_fulfilled / total_demand * 100) if total_demand > 0 else 100.0
    stockout_risk   = _r2(r.stockout_days / n * 100) if n > 0 else 0.0

    total_cost = (
        r.total_carrying_cost
        + r.total_ordering_cost
        + r.total_stockout_cost
    )
    avg_cycle_days = _r2(n / r.orders_placed) if r.orders_placed > 0 else n
    inv_turns = _r2((total_demand / n * 365) / avg_stock) if avg_stock > 0 else 0.0
    days_of_supply = _r2(avg_stock / (avg_daily_demand or 1))

    return {
        "simulation_days":      n,
        "initial_stock":        _r2(initial_stock),
        "reorder_point":        _r2(reorder_point),
        "order_quantity":       _r2(order_quantity),
        "avg_daily_demand":     _r2(avg_daily_demand),
        "demand_std":           _r2(demand_std),
        "lead_mean_days":       _r2(lead_mean_days),
        "lead_std_days":        _r2(lead_std_days),
        # KPIs
        "service_level_pct":    service_level,
        "fill_rate_pct":        fill_rate,
        "stockout_risk_pct":    stockout_risk,
        "stockout_days":        r.stockout_days,
        "stockout_units":       _r2(r.stockout_units),
        "avg_stock_level":      _r2(avg_stock),
        "max_stock_level":      _r2(max_stock),
        "min_stock_level":      _r2(min_stock),
        "orders_placed":        r.orders_placed,
        "avg_cycle_days":       avg_cycle_days,
        "inventory_turns":      inv_turns,
        "days_of_supply":       days_of_supply,
        "total_carrying_cost":  _r2(r.total_carrying_cost),
        "total_ordering_cost":  _r2(r.total_ordering_cost),
        "total_stockout_cost":  _r2(r.total_stockout_cost),
        "total_cost":           _r2(total_cost),
        # Timeseries (last 30 days for response payload)
        "daily_stock_tail":     r.daily_stock[-30:],
        "daily_demand_tail":    r.daily_demand[-30:],
    }


# ── 1. run_simulation ─────────────────────────────────────────────────────────

def run_simulation(
    db: Session,
    product_id: int,
    simulation_days: int = DEFAULT_SIM_DAYS,
    initial_stock: Optional[float]   = None,
    avg_daily_demand: Optional[float]= None,
    demand_std: Optional[float]      = None,
    reorder_point: Optional[float]   = None,
    order_quantity: Optional[float]  = None,
    lead_mean_days: Optional[float]  = None,
    lead_std_days: Optional[float]   = None,
    ordering_cost: Optional[float]   = None,
    holding_rate: Optional[float]    = None,
    stockout_cost_rate: Optional[float] = None,
    seasonal_factors: Optional[Dict[int, float]] = None,
    seed: Optional[int]              = None,
) -> Dict[str, Any]:
    """
    Run a single scenario simulation for a product.  Any parameter not
    supplied is derived automatically from live DB data.
    """
    params = _load_product_params(product_id, db)

    # Apply caller overrides
    p = {
        "initial_stock":     initial_stock    if initial_stock    is not None else params["initial_stock"],
        "avg_daily_demand":  avg_daily_demand if avg_daily_demand is not None else params["avg_daily_demand"],
        "demand_std":        demand_std       if demand_std       is not None else params["demand_std"],
        "reorder_point":     reorder_point    if reorder_point    is not None else params["reorder_point"],
        "order_quantity":    order_quantity   if order_quantity   is not None else params["order_quantity"],
        "lead_mean_days":    lead_mean_days   if lead_mean_days   is not None else params["lead_mean_days"],
        "lead_std_days":     lead_std_days    if lead_std_days    is not None else params["lead_std_days"],
        "unit_price":        params["unit_price"],
        "ordering_cost":     ordering_cost    if ordering_cost    is not None else params["ordering_cost"],
        "holding_rate":      holding_rate     if holding_rate     is not None else params["holding_rate"],
        "stockout_cost_rate": stockout_cost_rate if stockout_cost_rate is not None else params["stockout_cost_rate"],
        "simulation_days":   simulation_days,
        "seasonal_factors":  seasonal_factors,
        "seed":              seed,
    }

    result = _discrete_event_simulation(**p)
    result["product"] = {
        "id": params["product_id"], "name": params["product_name"],
        "sku": params["sku"], "category": params["category"],
    }
    result["db_baseline"] = {k: params[k] for k in (
        "avg_daily_demand", "demand_std", "lead_mean_days",
        "reorder_point", "order_quantity", "initial_stock",
    )}
    return result


# ── 2. run_what_if ────────────────────────────────────────────────────────────

def run_what_if(
    db: Session,
    product_id: int,
    simulation_days: int = DEFAULT_SIM_DAYS,
    # Scenario overrides (None = same as baseline)
    demand_multiplier:   Optional[float] = None,
    safety_stock_add:    Optional[float] = None,   # units to add to initial stock
    reorder_point_override: Optional[float] = None,
    order_quantity_override: Optional[float] = None,
    lead_multiplier:     Optional[float] = None,   # scale factor on lead time
    ordering_cost_override: Optional[float] = None,
    holding_rate_override: Optional[float] = None,
    seasonal_factors:    Optional[Dict[int, float]] = None,
    run_name:            Optional[str] = None,
    created_by:          Optional[str] = None,
    save:                bool = True,
) -> Dict[str, Any]:
    """
    Run baseline + one modified scenario side-by-side.

    Scenario is defined by override parameters; all others stay at baseline.
    Returns a comparison dict with delta metrics and change summary.
    """
    base_params = _load_product_params(product_id, db)

    # ── Baseline run ──────────────────────────────────────────────────────────
    baseline = _discrete_event_simulation(
        initial_stock    = base_params["initial_stock"],
        avg_daily_demand = base_params["avg_daily_demand"],
        demand_std       = base_params["demand_std"],
        reorder_point    = base_params["reorder_point"],
        order_quantity   = base_params["order_quantity"],
        lead_mean_days   = base_params["lead_mean_days"],
        lead_std_days    = base_params["lead_std_days"],
        unit_price       = base_params["unit_price"],
        ordering_cost    = base_params["ordering_cost"],
        holding_rate     = base_params["holding_rate"],
        stockout_cost_rate = base_params["stockout_cost_rate"],
        simulation_days  = simulation_days,
        seed             = 42,
    )

    # ── Apply overrides ───────────────────────────────────────────────────────
    sc_demand     = base_params["avg_daily_demand"] * (demand_multiplier or 1.0)
    sc_demand_std = base_params["demand_std"] * (demand_multiplier or 1.0)
    sc_init_stock = base_params["initial_stock"] + (safety_stock_add or 0.0)
    sc_rop        = reorder_point_override if reorder_point_override is not None \
                    else base_params["reorder_point"]
    sc_oq         = order_quantity_override if order_quantity_override is not None \
                    else base_params["order_quantity"]
    sc_lead_mean  = base_params["lead_mean_days"] * (lead_multiplier or 1.0)
    sc_lead_std   = base_params["lead_std_days"] * (lead_multiplier or 1.0)
    sc_oc         = ordering_cost_override if ordering_cost_override is not None \
                    else base_params["ordering_cost"]
    sc_hr         = holding_rate_override if holding_rate_override is not None \
                    else base_params["holding_rate"]

    scenario = _discrete_event_simulation(
        initial_stock    = sc_init_stock,
        avg_daily_demand = sc_demand,
        demand_std       = sc_demand_std,
        reorder_point    = sc_rop,
        order_quantity   = sc_oq,
        lead_mean_days   = sc_lead_mean,
        lead_std_days    = sc_lead_std,
        unit_price       = base_params["unit_price"],
        ordering_cost    = sc_oc,
        holding_rate     = sc_hr,
        stockout_cost_rate = base_params["stockout_cost_rate"],
        simulation_days  = simulation_days,
        seasonal_factors = seasonal_factors,
        seed             = 42,
    )

    # ── Delta analysis ────────────────────────────────────────────────────────
    deltas = _compute_deltas(baseline, scenario)
    insights = _generate_what_if_insights(baseline, scenario, deltas, base_params)

    product_info = {
        "id": base_params["product_id"],
        "name": base_params["product_name"],
        "sku":  base_params["sku"],
        "category": base_params["category"],
    }

    result = {
        "run_id":         _run_id(),
        "run_name":       run_name or f"What-If: {base_params['product_name']}",
        "simulation_type": "what_if",
        "product":        product_info,
        "simulation_days": simulation_days,
        "baseline":       baseline,
        "scenario":       scenario,
        "deltas":         deltas,
        "insights":       insights,
        "scenario_overrides": {
            k: v for k, v in {
                "demand_multiplier":       demand_multiplier,
                "safety_stock_add":        safety_stock_add,
                "reorder_point_override":  reorder_point_override,
                "order_quantity_override": order_quantity_override,
                "lead_multiplier":         lead_multiplier,
                "ordering_cost_override":  ordering_cost_override,
                "holding_rate_override":   holding_rate_override,
            }.items() if v is not None
        },
    }

    if save:
        _persist_run(
            db, result, product_id, created_by,
            base_sl=baseline["service_level_pct"],
            sc_sl=scenario["service_level_pct"],
            base_cost=baseline["total_cost"],
            sc_cost=scenario["total_cost"],
            stockout_risk=scenario["stockout_risk_pct"],
        )
    return result


# ── 3. compare_scenarios ──────────────────────────────────────────────────────

def compare_scenarios(
    db: Session,
    product_id: int,
    scenarios: List[Dict[str, Any]],
    simulation_days: int = DEFAULT_SIM_DAYS,
    created_by: Optional[str] = None,
    run_name: Optional[str] = None,
    save: bool = True,
) -> Dict[str, Any]:
    """
    Run multiple named scenarios and rank them by a composite score.

    Each entry in `scenarios` is a dict with an optional ``name`` key plus
    any of the parameter override keys accepted by `run_what_if`.

    Composite score = 0.5 × service_level + 0.3 × (1 - stockout_risk)
                    + 0.2 × (1 - normalised_cost)
    """
    base_params = _load_product_params(product_id, db)

    # ── Baseline ──────────────────────────────────────────────────────────────
    baseline = _discrete_event_simulation(
        initial_stock    = base_params["initial_stock"],
        avg_daily_demand = base_params["avg_daily_demand"],
        demand_std       = base_params["demand_std"],
        reorder_point    = base_params["reorder_point"],
        order_quantity   = base_params["order_quantity"],
        lead_mean_days   = base_params["lead_mean_days"],
        lead_std_days    = base_params["lead_std_days"],
        unit_price       = base_params["unit_price"],
        ordering_cost    = base_params["ordering_cost"],
        holding_rate     = base_params["holding_rate"],
        stockout_cost_rate = base_params["stockout_cost_rate"],
        simulation_days  = simulation_days,
        seed             = 42,
    )
    baseline["scenario_name"] = "Baseline (current)"

    # ── Each named scenario ───────────────────────────────────────────────────
    scenario_results = []
    for idx, sc in enumerate(scenarios):
        name   = sc.get("name", f"Scenario {idx + 1}")
        dm     = sc.get("demand_multiplier",        1.0)
        ss_add = sc.get("safety_stock_add",         0.0)
        rop    = sc.get("reorder_point_override",   base_params["reorder_point"])
        oq     = sc.get("order_quantity_override",  base_params["order_quantity"])
        lm     = sc.get("lead_multiplier",          1.0)
        oc     = sc.get("ordering_cost_override",   base_params["ordering_cost"])
        hr     = sc.get("holding_rate_override",    base_params["holding_rate"])
        sf     = sc.get("seasonal_factors")

        r = _discrete_event_simulation(
            initial_stock    = base_params["initial_stock"] + ss_add,
            avg_daily_demand = base_params["avg_daily_demand"] * dm,
            demand_std       = base_params["demand_std"] * dm,
            reorder_point    = rop,
            order_quantity   = oq,
            lead_mean_days   = base_params["lead_mean_days"] * lm,
            lead_std_days    = base_params["lead_std_days"] * lm,
            unit_price       = base_params["unit_price"],
            ordering_cost    = oc,
            holding_rate     = hr,
            stockout_cost_rate = base_params["stockout_cost_rate"],
            simulation_days  = simulation_days,
            seasonal_factors = sf,
            seed             = 42,
        )
        r["scenario_name"]  = name
        r["scenario_params"] = {k: v for k, v in sc.items() if k != "name"}
        scenario_results.append(r)

    # ── Composite ranking ─────────────────────────────────────────────────────
    all_results = [baseline] + scenario_results
    max_cost = max(r["total_cost"] for r in all_results) or 1.0
    min_cost = min(r["total_cost"] for r in all_results)
    cost_range = (max_cost - min_cost) or 1.0

    for r in all_results:
        sl_norm   = r["service_level_pct"] / 100
        so_norm   = 1 - r["stockout_risk_pct"] / 100
        cost_norm = 1 - (r["total_cost"] - min_cost) / cost_range
        r["composite_score"] = _r2(
            0.50 * sl_norm + 0.30 * so_norm + 0.20 * cost_norm
        )

    all_results.sort(key=lambda x: x["composite_score"], reverse=True)
    for rank, r in enumerate(all_results, 1):
        r["rank"] = rank

    best = all_results[0]
    product_info = {
        "id": base_params["product_id"], "name": base_params["product_name"],
        "sku": base_params["sku"], "category": base_params["category"],
    }
    result = {
        "run_id":          _run_id(),
        "run_name":        run_name or f"Scenario Compare: {base_params['product_name']}",
        "simulation_type": "scenario_compare",
        "product":         product_info,
        "simulation_days": simulation_days,
        "n_scenarios":     len(all_results),
        "ranked_scenarios": all_results,
        "best_scenario":   best.get("scenario_name"),
        "scoring_weights": {"service_level": 0.50, "stockout_avoidance": 0.30, "cost_minimisation": 0.20},
        "summary": {
            "best_service_level": max(r["service_level_pct"] for r in all_results),
            "lowest_total_cost":  min(r["total_cost"] for r in all_results),
            "lowest_stockout_risk": min(r["stockout_risk_pct"] for r in all_results),
        },
    }

    if save:
        best_sc = next((r for r in all_results if r.get("rank") == 1), all_results[0])
        _persist_run(
            db, result, product_id, created_by,
            base_sl=baseline["service_level_pct"],
            sc_sl=best_sc["service_level_pct"],
            base_cost=baseline["total_cost"],
            sc_cost=best_sc["total_cost"],
            stockout_risk=best_sc["stockout_risk_pct"],
        )
    return result


# ── 4. simulate_seasonal ──────────────────────────────────────────────────────

def simulate_seasonal(
    db: Session,
    product_id: int,
    simulation_days: int = DEFAULT_SIM_DAYS,
    seasonal_factors: Optional[Dict[int, float]] = None,
    created_by: Optional[str] = None,
    save: bool = True,
) -> Dict[str, Any]:
    """
    Simulate with and without seasonal demand fluctuations.

    Compares the flat-demand baseline against a seasonally-adjusted run.
    """
    base_params = _load_product_params(product_id, db)
    sf = seasonal_factors or DEFAULT_SEASONAL_FACTORS

    baseline = _discrete_event_simulation(
        **_to_sim_kwargs(base_params, simulation_days, seed=42)
    )
    seasonal = _discrete_event_simulation(
        **_to_sim_kwargs(base_params, simulation_days, seed=42, seasonal_factors=sf)
    )

    # Month-by-month stockout risk projections
    monthly_insights = []
    today = date.today()
    for m in range(1, 13):
        factor = sf.get(m, 1.0)
        risk_label = (
            "HIGH"   if factor >= 1.30 else
            "MEDIUM" if factor >= 1.10 else
            "LOW"
        )
        monthly_insights.append({
            "month":          m,
            "seasonal_factor": factor,
            "adjusted_demand": _r2(base_params["avg_daily_demand"] * factor),
            "stockout_risk_label": risk_label,
        })

    deltas = _compute_deltas(baseline, seasonal)
    product_info = {
        "id": base_params["product_id"], "name": base_params["product_name"],
        "sku": base_params["sku"], "category": base_params["category"],
    }
    result = {
        "run_id":            _run_id(),
        "simulation_type":   "seasonal",
        "product":           product_info,
        "simulation_days":   simulation_days,
        "baseline":          baseline,
        "seasonal_scenario": seasonal,
        "deltas":            deltas,
        "monthly_insights":  monthly_insights,
        "seasonal_factors":  sf,
        "insights": [
            f"Peak demand months: {', '.join(str(m['month']) for m in monthly_insights if m['seasonal_factor'] >= 1.30)}",
            f"Seasonal scenario stockout risk: {seasonal['stockout_risk_pct']}% vs baseline {baseline['stockout_risk_pct']}%",
            "Consider pre-positioning stock before peak months to maintain service level.",
        ],
    }

    if save:
        _persist_run(
            db, result, product_id, created_by,
            base_sl=baseline["service_level_pct"],
            sc_sl=seasonal["service_level_pct"],
            base_cost=baseline["total_cost"],
            sc_cost=seasonal["total_cost"],
            stockout_risk=seasonal["stockout_risk_pct"],
        )
    return result


# ── 5. simulate_supplier_disruption ──────────────────────────────────────────

def simulate_supplier_disruption(
    db: Session,
    product_id: int,
    simulation_days: int = DEFAULT_SIM_DAYS,
    lead_time_multipliers: Optional[List[float]] = None,
    created_by: Optional[str] = None,
    save: bool = True,
) -> Dict[str, Any]:
    """
    Stress-test what happens when supplier lead times increase.

    Runs the baseline plus each lead-time multiplier scenario
    (default: 1×, 1.5×, 2×, 3×) and ranks them.
    """
    base_params = _load_product_params(product_id, db)
    multipliers = lead_time_multipliers or [1.0, 1.5, 2.0, 3.0]

    scenarios = []
    for lm in multipliers:
        r = _discrete_event_simulation(
            **_to_sim_kwargs(
                base_params, simulation_days, seed=42,
                lead_mean=base_params["lead_mean_days"] * lm,
                lead_std =base_params["lead_std_days"]  * lm,
            )
        )
        r["lead_multiplier"]  = lm
        r["lead_mean_actual"] = _r2(base_params["lead_mean_days"] * lm)
        r["scenario_name"]    = (
            "Baseline" if lm == 1.0 else f"{lm:.1f}× Lead Time"
        )
        scenarios.append(r)

    baseline = scenarios[0]
    worst    = max(scenarios, key=lambda x: x["stockout_risk_pct"])
    best     = min(scenarios, key=lambda x: x["stockout_risk_pct"])

    tipping = next(
        (s for s in scenarios if s["service_level_pct"] < 95.0), None
    )

    product_info = {
        "id": base_params["product_id"], "name": base_params["product_name"],
        "sku": base_params["sku"], "category": base_params["category"],
    }
    result = {
        "run_id":                 _run_id(),
        "simulation_type":        "supplier_disruption",
        "product":                product_info,
        "simulation_days":        simulation_days,
        "baseline_lead_time_days": base_params["lead_mean_days"],
        "scenarios":              scenarios,
        "worst_case":             worst["scenario_name"],
        "best_case":              best["scenario_name"],
        "tipping_point_scenario": tipping["scenario_name"] if tipping else "None identified",
        "insights": [
            f"At 2× lead time, stockout risk rises to {next((s['stockout_risk_pct'] for s in scenarios if s['lead_multiplier']==2.0), 'N/A')}%.",
            f"Service level tipping point: {tipping['scenario_name'] if tipping else 'remains above 95% in all scenarios'}.",
            "Recommend pre-negotiating secondary supplier contracts for disruption events.",
            f"Total cost at 2× lead time vs baseline: ${next((s['total_cost'] for s in scenarios if s['lead_multiplier']==2.0), 0):,.2f} vs ${baseline['total_cost']:,.2f}",
        ],
    }

    if save:
        _persist_run(
            db, result, product_id, created_by,
            base_sl=baseline["service_level_pct"],
            sc_sl=worst["service_level_pct"],
            base_cost=baseline["total_cost"],
            sc_cost=worst["total_cost"],
            stockout_risk=worst["stockout_risk_pct"],
        )
    return result


# ── 6. run_monte_carlo ────────────────────────────────────────────────────────

def run_monte_carlo(
    db: Session,
    product_id: int,
    simulation_days: int = DEFAULT_SIM_DAYS,
    n_trials: int = MONTE_CARLO_TRIALS,
    demand_multiplier: float = 1.0,
    lead_multiplier: float = 1.0,
    created_by: Optional[str] = None,
    save: bool = True,
) -> Dict[str, Any]:
    """
    Monte Carlo simulation: run `n_trials` independent replications with
    randomised demand and lead-time draws to compute probability distributions
    over key KPIs.

    Returns mean, std, percentiles (5th, 25th, 50th, 75th, 95th) for:
    service_level, fill_rate, stockout_risk, total_cost.
    """
    base_params = _load_product_params(product_id, db)
    n_trials = min(n_trials, MONTE_CARLO_TRIALS)  # cap for safety

    results: Dict[str, List[float]] = defaultdict(list)

    for trial in range(n_trials):
        r = _discrete_event_simulation(
            initial_stock    = base_params["initial_stock"],
            avg_daily_demand = base_params["avg_daily_demand"] * demand_multiplier,
            demand_std       = base_params["demand_std"] * demand_multiplier,
            reorder_point    = base_params["reorder_point"],
            order_quantity   = base_params["order_quantity"],
            lead_mean_days   = base_params["lead_mean_days"] * lead_multiplier,
            lead_std_days    = base_params["lead_std_days"] * lead_multiplier,
            unit_price       = base_params["unit_price"],
            ordering_cost    = base_params["ordering_cost"],
            holding_rate     = base_params["holding_rate"],
            stockout_cost_rate = base_params["stockout_cost_rate"],
            simulation_days  = simulation_days,
            seed             = trial,   # each trial gets a different seed
        )
        for key in ("service_level_pct", "fill_rate_pct", "stockout_risk_pct",
                    "total_cost", "total_carrying_cost", "total_ordering_cost",
                    "avg_stock_level", "stockout_days", "orders_placed"):
            results[key].append(r[key])

    def _stats(vals: List[float]) -> Dict:
        sv = sorted(vals)
        n  = len(sv)
        def pct(p): return sv[max(0, min(n-1, int(p/100*n)))]
        return {
            "mean":  _r2(statistics.mean(vals)),
            "std":   _r2(statistics.stdev(vals)) if len(vals) > 1 else 0.0,
            "p5":    _r2(pct(5)),  "p25":  _r2(pct(25)),
            "p50":   _r2(pct(50)), "p75":  _r2(pct(75)),
            "p95":   _r2(pct(95)),
        }

    stockout_runs = sum(1 for v in results["stockout_days"] if v > 0)
    prob_stockout = _r2(stockout_runs / n_trials * 100)

    distribution = {k: _stats(v) for k, v in results.items()}

    product_info = {
        "id": base_params["product_id"], "name": base_params["product_name"],
        "sku": base_params["sku"], "category": base_params["category"],
    }
    result = {
        "run_id":              _run_id(),
        "simulation_type":     "monte_carlo",
        "product":             product_info,
        "simulation_days":     simulation_days,
        "n_trials":            n_trials,
        "demand_multiplier":   demand_multiplier,
        "lead_multiplier":     lead_multiplier,
        "probability_of_stockout_pct": prob_stockout,
        "expected_service_level_pct": distribution["service_level_pct"]["mean"],
        "expected_fill_rate_pct":     distribution["fill_rate_pct"]["mean"],
        "expected_total_cost":        distribution["total_cost"]["mean"],
        "value_at_risk_95_pct":       distribution["total_cost"]["p95"],  # 95th-pct cost
        "distribution":        distribution,
        "insights": [
            f"Probability of at least one stockout in {simulation_days} days: {prob_stockout:.1f}%.",
            f"Expected service level: {distribution['service_level_pct']['mean']:.1f}% "
            f"(95th-pct worst case: {distribution['service_level_pct']['p5']:.1f}%).",
            f"95th-percentile total cost: ${distribution['total_cost']['p95']:,.2f}.",
            "Increase safety stock or reduce lead time variance to shift the distribution toward better outcomes.",
        ],
    }

    if save:
        _persist_run(
            db, result, product_id, created_by,
            base_sl=distribution["service_level_pct"]["mean"],
            sc_sl=distribution["service_level_pct"]["p50"],
            base_cost=distribution["total_cost"]["mean"],
            sc_cost=distribution["total_cost"]["p95"],
            stockout_risk=prob_stockout,
        )
    return result


# ── 7. compare_strategies ─────────────────────────────────────────────────────

def compare_strategies(
    db: Session,
    product_id: int,
    simulation_days: int = DEFAULT_SIM_DAYS,
    created_by: Optional[str] = None,
    save: bool = True,
) -> Dict[str, Any]:
    """
    Compare three restocking strategies for a product:

    1. **FOQ (Fixed Order Quantity)** — reorder_level trigger + fixed 2-week supply
    2. **EOQ** — optimal order quantity (economic order quantity formula)
    3. **Safety-Stock Enhanced** — EOQ + extra safety stock buffer (99% service level)
    4. **Min-Max** — order up to max level when below min
    5. **Periodic Review (weekly)** — place orders every 7 days regardless of stock level
    """
    base_params = _load_product_params(product_id, db)

    # ── Precompute strategy parameters ────────────────────────────────────────
    daily_d = base_params["avg_daily_demand"]
    annual_d = daily_d * 365
    unit_p  = base_params["unit_price"]
    h       = unit_p * base_params["holding_rate"]
    oc      = base_params["ordering_cost"]
    z_95    = SERVICE_LEVEL_Z[0.95]
    z_99    = SERVICE_LEVEL_Z[0.99]

    eoq = _safe_sqrt(2 * annual_d * oc / h) if h > 0 else base_params["order_quantity"]
    ss_95 = z_95 * _safe_sqrt(
        base_params["lead_mean_days"] * base_params["demand_std"] ** 2
        + base_params["lead_std_days"] ** 2 * daily_d ** 2
    )
    ss_99 = z_99 * _safe_sqrt(
        base_params["lead_mean_days"] * base_params["demand_std"] ** 2
        + base_params["lead_std_days"] ** 2 * daily_d ** 2
    )
    rop_95 = daily_d * base_params["lead_mean_days"] + ss_95
    rop_99 = daily_d * base_params["lead_mean_days"] + ss_99

    strategies = [
        {
            "name":           "FOQ (Fixed Order Qty — 2-week supply)",
            "order_quantity": daily_d * 14,
            "reorder_point":  base_params["reorder_level"] if base_params.get("reorder_level") else rop_95 * 0.8,
            "description":    "Place a fixed 2-week supply order every time stock hits the reorder level.",
        },
        {
            "name":           "EOQ (Economic Order Quantity)",
            "order_quantity": eoq,
            "reorder_point":  rop_95,
            "description":    "Minimises total ordering + holding cost. Uses Q*=√(2DS/H).",
        },
        {
            "name":           "EOQ + Safety Stock (95% service level)",
            "order_quantity": eoq,
            "reorder_point":  rop_95,
            "initial_stock_boost": ss_95,
            "description":    "EOQ order quantity with safety stock for 95% cycle service level.",
        },
        {
            "name":           "EOQ + Safety Stock (99% service level)",
            "order_quantity": eoq,
            "reorder_point":  rop_99,
            "initial_stock_boost": ss_99,
            "description":    "EOQ order quantity with safety stock for 99% cycle service level.",
        },
        {
            "name":           "Min-Max Policy",
            "order_quantity": eoq * 1.5,
            "reorder_point":  rop_95 * 0.9,
            "description":    "Order when below ROP (min level); order up to max = ROP + 1.5×EOQ.",
        },
    ]

    max_cost = 1.0
    results = []
    for st in strategies:
        r = _discrete_event_simulation(
            initial_stock    = base_params["initial_stock"] + st.get("initial_stock_boost", 0.0),
            avg_daily_demand = base_params["avg_daily_demand"],
            demand_std       = base_params["demand_std"],
            reorder_point    = st["reorder_point"],
            order_quantity   = st["order_quantity"],
            lead_mean_days   = base_params["lead_mean_days"],
            lead_std_days    = base_params["lead_std_days"],
            unit_price       = base_params["unit_price"],
            ordering_cost    = base_params["ordering_cost"],
            holding_rate     = base_params["holding_rate"],
            stockout_cost_rate = base_params["stockout_cost_rate"],
            simulation_days  = simulation_days,
            seed             = 42,
        )
        r["strategy_name"]   = st["name"]
        r["strategy_desc"]   = st["description"]
        r["strategy_params"] = {
            "order_quantity": _r2(st["order_quantity"]),
            "reorder_point":  _r2(st["reorder_point"]),
        }
        max_cost = max(max_cost, r["total_cost"])
        results.append(r)

    min_cost   = min(r["total_cost"] for r in results)
    cost_range = (max_cost - min_cost) or 1.0
    for r in results:
        sl_norm   = r["service_level_pct"] / 100
        so_norm   = 1 - r["stockout_risk_pct"] / 100
        cost_norm = 1 - (r["total_cost"] - min_cost) / cost_range
        r["composite_score"] = _r2(0.50 * sl_norm + 0.30 * so_norm + 0.20 * cost_norm)

    results.sort(key=lambda x: x["composite_score"], reverse=True)
    for rank, r in enumerate(results, 1):
        r["rank"] = rank

    best    = results[0]
    product_info = {
        "id": base_params["product_id"], "name": base_params["product_name"],
        "sku": base_params["sku"], "category": base_params["category"],
    }
    result = {
        "run_id":            _run_id(),
        "run_name":          f"Strategy Compare: {base_params['product_name']}",
        "simulation_type":   "strategy_compare",
        "product":           product_info,
        "simulation_days":   simulation_days,
        "eoq":               _r2(eoq),
        "ranked_strategies": results,
        "recommended_strategy": best["strategy_name"],
        "summary": {
            "best_service_level":  max(r["service_level_pct"] for r in results),
            "lowest_total_cost":   min(r["total_cost"] for r in results),
            "best_fill_rate":      max(r["fill_rate_pct"] for r in results),
        },
        "insights": [
            f"Recommended strategy: '{best['strategy_name']}'.",
            f"EOQ for this product: {_r2(eoq):.0f} units (Q*=√(2DS/H)).",
            f"Best service level achievable: {max(r['service_level_pct'] for r in results):.1f}%.",
            f"Cost spread across strategies: ${min_cost:,.2f} – ${max_cost:,.2f}.",
        ],
    }

    if save:
        _persist_run(
            db, result, product_id, created_by,
            base_sl=results[-1]["service_level_pct"],
            sc_sl=best["service_level_pct"],
            base_cost=results[-1]["total_cost"],
            sc_cost=best["total_cost"],
            stockout_risk=best["stockout_risk_pct"],
        )
    return result


# ── 8. sensitivity_analysis ───────────────────────────────────────────────────

def sensitivity_analysis(
    db: Session,
    product_id: int,
    simulation_days: int = DEFAULT_SIM_DAYS,
    parameter: str = "order_quantity",
    multipliers: Optional[List[float]] = None,
) -> Dict[str, Any]:
    """
    Sensitivity analysis: vary one parameter and plot how KPIs change.

    Supported parameters:
      order_quantity, reorder_point, safety_stock_add,
      demand_multiplier, lead_multiplier, holding_rate
    """
    base_params = _load_product_params(product_id, db)
    mults = multipliers or [0.50, 0.75, 1.00, 1.25, 1.50, 2.00]

    PARAM_MAP = {
        "order_quantity":    ("order_quantity",   "order_quantity"),
        "reorder_point":     ("reorder_point",    "reorder_point"),
        "safety_stock_add":  ("initial_stock",    None),
        "demand_multiplier": ("avg_daily_demand", None),
        "lead_multiplier":   ("lead_mean_days",   None),
        "holding_rate":      ("holding_rate",     None),
    }
    if parameter not in PARAM_MAP:
        raise ValueError(
            f"Unknown parameter '{parameter}'. "
            f"Valid: {list(PARAM_MAP)}"
        )

    base_val = {
        "order_quantity":   base_params["order_quantity"],
        "reorder_point":    base_params["reorder_point"],
        "safety_stock_add": 0.0,
        "demand_multiplier":1.0,
        "lead_multiplier":  1.0,
        "holding_rate":     base_params["holding_rate"],
    }[parameter]

    points = []
    for m in mults:
        val = base_val * m
        kwargs = _to_sim_kwargs(base_params, simulation_days, seed=42)
        # Apply the parameter variation
        if parameter == "order_quantity":
            kwargs["order_quantity"] = val
        elif parameter == "reorder_point":
            kwargs["reorder_point"] = val
        elif parameter == "safety_stock_add":
            kwargs["initial_stock"] = base_params["initial_stock"] + val
        elif parameter == "demand_multiplier":
            kwargs["avg_daily_demand"] = base_params["avg_daily_demand"] * m
            kwargs["demand_std"]       = base_params["demand_std"] * m
        elif parameter == "lead_multiplier":
            kwargs["lead_mean_days"] = base_params["lead_mean_days"] * m
            kwargs["lead_std_days"]  = base_params["lead_std_days"] * m
        elif parameter == "holding_rate":
            kwargs["holding_rate"] = val

        r = _discrete_event_simulation(**kwargs)
        points.append({
            "multiplier":         m,
            "parameter_value":    _r2(val),
            "service_level_pct":  r["service_level_pct"],
            "fill_rate_pct":      r["fill_rate_pct"],
            "stockout_risk_pct":  r["stockout_risk_pct"],
            "total_cost":         r["total_cost"],
            "total_carrying_cost": r["total_carrying_cost"],
            "total_ordering_cost": r["total_ordering_cost"],
            "total_stockout_cost": r["total_stockout_cost"],
            "avg_stock_level":    r["avg_stock_level"],
            "inventory_turns":    r["inventory_turns"],
        })

    # Find optimal (best composite)
    min_c = min(p["total_cost"] for p in points)
    max_c = max(p["total_cost"] for p in points) or 1.0
    c_range = (max_c - min_c) or 1.0
    for p in points:
        sl_n = p["service_level_pct"] / 100
        so_n = 1 - p["stockout_risk_pct"] / 100
        cost_n = 1 - (p["total_cost"] - min_c) / c_range
        p["composite_score"] = _r2(0.50*sl_n + 0.30*so_n + 0.20*cost_n)

    optimal = max(points, key=lambda x: x["composite_score"])

    product_info = {
        "id": base_params["product_id"], "name": base_params["product_name"],
        "sku": base_params["sku"], "category": base_params["category"],
    }
    return {
        "simulation_type":    "sensitivity",
        "product":            product_info,
        "parameter":          parameter,
        "base_value":         _r2(base_val),
        "simulation_days":    simulation_days,
        "data_points":        points,
        "optimal_multiplier": optimal["multiplier"],
        "optimal_value":      optimal["parameter_value"],
        "insights": [
            f"Optimal {parameter} value: {optimal['parameter_value']:.2f} (×{optimal['multiplier']}) "
            f"— service level {optimal['service_level_pct']:.1f}%, total cost ${optimal['total_cost']:,.2f}.",
            f"Current value ({_r2(base_val)}) maps to service level "
            f"{next((p['service_level_pct'] for p in points if p['multiplier'] == 1.00), '?')}%.",
        ],
    }


# ── 9. get_simulation_runs / get_simulation_run ────────────────────────────────

def get_simulation_runs(
    db: Session,
    skip: int = 0,
    limit: int = 50,
    product_id: Optional[int] = None,
    simulation_type: Optional[str] = None,
    created_by: Optional[str] = None,
) -> Dict[str, Any]:
    q = db.query(SimulationRun)
    if product_id:
        q = q.filter(SimulationRun.product_id == product_id)
    if simulation_type:
        q = q.filter(SimulationRun.simulation_type == simulation_type)
    if created_by:
        q = q.filter(SimulationRun.created_by == created_by)

    total = q.count()
    rows = q.order_by(SimulationRun.created_at.desc()).offset(skip).limit(limit).all()
    return {
        "total": total, "skip": skip, "limit": limit,
        "runs": [_serialize_run(r) for r in rows],
    }


def get_simulation_run(db: Session, run_id: str) -> Optional[Dict[str, Any]]:
    r = db.query(SimulationRun).filter(SimulationRun.run_id == run_id).first()
    if not r:
        return None
    d = _serialize_run(r)
    d["baseline_result"]    = r.get_baseline_result()
    d["scenario_result"]    = r.get_scenario_result()
    d["comparison_summary"] = r.get_comparison_summary()
    d["parameters"]         = r.get_parameters()
    return d


def delete_simulation_run(db: Session, run_id: str) -> bool:
    r = db.query(SimulationRun).filter(SimulationRun.run_id == run_id).first()
    if not r:
        return False
    db.delete(r)
    db.commit()
    return True


# ── Internal helpers ──────────────────────────────────────────────────────────

def _to_sim_kwargs(
    p: Dict,
    simulation_days: int,
    seed: Optional[int] = None,
    seasonal_factors: Optional[Dict] = None,
    lead_mean: Optional[float] = None,
    lead_std: Optional[float] = None,
) -> Dict:
    return {
        "initial_stock":     p["initial_stock"],
        "avg_daily_demand":  p["avg_daily_demand"],
        "demand_std":        p["demand_std"],
        "reorder_point":     p["reorder_point"],
        "order_quantity":    p["order_quantity"],
        "lead_mean_days":    lead_mean if lead_mean is not None else p["lead_mean_days"],
        "lead_std_days":     lead_std  if lead_std  is not None else p["lead_std_days"],
        "unit_price":        p["unit_price"],
        "ordering_cost":     p["ordering_cost"],
        "holding_rate":      p["holding_rate"],
        "stockout_cost_rate": p["stockout_cost_rate"],
        "simulation_days":   simulation_days,
        "seasonal_factors":  seasonal_factors,
        "seed":              seed,
    }


def _compute_deltas(baseline: Dict, scenario: Dict) -> Dict[str, Any]:
    metrics = [
        "service_level_pct", "fill_rate_pct", "stockout_risk_pct",
        "total_cost", "total_carrying_cost", "total_ordering_cost",
        "total_stockout_cost", "avg_stock_level", "orders_placed",
        "stockout_days",
    ]
    deltas = {}
    for m in metrics:
        bv = baseline.get(m, 0) or 0
        sv = scenario.get(m, 0) or 0
        abs_delta = sv - bv
        pct_delta = (abs_delta / bv * 100) if bv else 0
        deltas[m] = {
            "baseline":   _r2(bv),
            "scenario":   _r2(sv),
            "delta":      _r2(abs_delta),
            "delta_pct":  _r2(pct_delta),
            "direction":  "improved" if (
                (m in ("service_level_pct", "fill_rate_pct") and abs_delta > 0)
                or (m not in ("service_level_pct", "fill_rate_pct") and abs_delta < 0)
            ) else "worsened" if abs_delta != 0 else "unchanged",
        }
    return deltas


def _generate_what_if_insights(
    baseline: Dict, scenario: Dict, deltas: Dict, params: Dict
) -> List[str]:
    insights = []
    sl_d  = deltas["service_level_pct"]["delta"]
    cost_d = deltas["total_cost"]["delta"]
    so_d  = deltas["stockout_risk_pct"]["delta"]

    if sl_d > 2:
        insights.append(f"Service level improves by {sl_d:.1f}% — the scenario significantly reduces stockout risk.")
    elif sl_d < -2:
        insights.append(f"WARNING: Service level drops {abs(sl_d):.1f}% — scenario increases stockout exposure.")

    if cost_d < 0:
        insights.append(f"Total cost decreases by ${abs(cost_d):,.2f} ({abs(deltas['total_cost']['delta_pct']):.1f}%).")
    elif cost_d > 0:
        insights.append(f"Total cost increases by ${cost_d:,.2f} ({deltas['total_cost']['delta_pct']:.1f}%) — ensure service level improvement justifies the cost.")

    sc_so = scenario["stockout_risk_pct"]
    if sc_so < 5:
        insights.append("Scenario achieves < 5% stockout risk — very strong inventory coverage.")
    elif sc_so > 20:
        insights.append("Scenario still shows elevated stockout risk (>20%) — further parameter adjustments recommended.")

    if scenario["avg_stock_level"] > baseline["avg_stock_level"] * 1.3:
        insights.append("Average stock level increases significantly — monitor for excess carrying cost.")

    return insights or ["Scenario produces similar results to baseline. Consider more aggressive parameter changes."]


def _persist_run(
    db: Session,
    result: Dict,
    product_id: Optional[int],
    created_by: Optional[str],
    base_sl: float,
    sc_sl: float,
    base_cost: float,
    sc_cost: float,
    stockout_risk: float,
) -> SimulationRun:
    """Persist a simulation result to the database."""
    run = SimulationRun(
        run_id          = result["run_id"],
        run_name        = result.get("run_name", result["simulation_type"]),
        product_id      = product_id,
        simulation_type = result["simulation_type"],
        simulation_days = result.get("simulation_days"),
        parameters      = json.dumps(result.get("scenario_overrides", result.get("seasonal_factors", {}))),
        baseline_result = json.dumps(result.get("baseline", {})),
        scenario_result = json.dumps(
            result.get("scenario", result.get("seasonal_scenario", result.get("scenarios", {})))
        ),
        comparison_summary = json.dumps(result.get("insights", [])),
        baseline_service_level = base_sl,
        scenario_service_level = sc_sl,
        baseline_total_cost    = base_cost,
        scenario_total_cost    = sc_cost,
        cost_savings           = _r2(base_cost - sc_cost),
        stockout_risk_pct      = stockout_risk,
        created_by             = created_by,
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


def _serialize_run(r: SimulationRun) -> Dict[str, Any]:
    return {
        "run_id":                  r.run_id,
        "run_name":                r.run_name,
        "product_id":              r.product_id,
        "simulation_type":         r.simulation_type,
        "simulation_days":         r.simulation_days,
        "baseline_service_level":  r.baseline_service_level,
        "scenario_service_level":  r.scenario_service_level,
        "baseline_total_cost":     r.baseline_total_cost,
        "scenario_total_cost":     r.scenario_total_cost,
        "cost_savings":            r.cost_savings,
        "stockout_risk_pct":       r.stockout_risk_pct,
        "created_by":              r.created_by,
        "created_at":              r.created_at.isoformat() if r.created_at else None,
    }
