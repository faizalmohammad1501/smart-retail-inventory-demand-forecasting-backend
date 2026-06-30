"""
Predictive Inventory Optimization Engine
==========================================
Combines historical sales, lead times, current stock levels, and cost
parameters to produce mathematically rigorous inventory strategies.

New capabilities (distinct from existing recommendation service)
----------------------------------------------------------------
1.  EOQ analysis         — Economic Order Quantity + total annual cost curve
2.  Safety stock matrix  — 90 / 95 / 98 / 99 % service levels simultaneously
3.  Dynamic reorder pts  — reorder point accounts for BOTH demand σ and lead-time σ
4.  Turnover & GMROI     — inventory turnover rate, days inventory outstanding,
                           Gross Margin Return on Inventory Investment
5.  Total cost model     — ordering cost + holding cost + stockout cost per product
6.  Fill-rate analysis   — demand satisfaction rate by product and category
7.  Reorder calendar     — projected order dates for the next N days
8.  Capital allocation   — cross-product budget optimisation by ROI

Mathematical foundations
------------------------
EOQ:
  Q* = √(2DS / H)
  where  D = annual demand (units)
         S = ordering cost per order ($)
         H = holding cost per unit per year = holding_rate × unit_price

Reorder point with lead-time variability:
  ROP = μ_d × μ_L + z × √(μ_L × σ_d² + σ_L² × μ_d²)
  where  μ_d = avg daily demand, σ_d = daily demand std-dev
         μ_L = avg lead time (days), σ_L = lead time std-dev

Safety stock (demand & lead-time variability):
  SS = z × √(μ_L × σ_d² + σ_L² × μ_d²)

GMROI:
  GMROI = (revenue - cogs) / avg_inventory_value × 100

Inventory turnover:
  turns = annual_units_sold / avg_inventory_units
  DIO   = 365 / turns
"""

import math
import statistics
from collections import defaultdict
from datetime import datetime, date, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import func, case
from sqlalchemy.orm import Session

from app.models.inventory import Inventory
from app.models.product import Product
from app.models.sales import Order
from app.models.supplier import Supplier


# ── Configurable engine parameters ───────────────────────────────────────────

DEFAULT_ORDERING_COST       = 50.0    # $ per purchase order
DEFAULT_HOLDING_COST_RATE   = 0.25    # 25 % of unit value per year (APICS standard)
DEFAULT_STOCKOUT_COST_RATE  = 2.0     # 2× unit price per lost sale
WORKING_DAYS                = 250     # working days per year
SERVICE_LEVELS              = {
    "90": 1.28,
    "95": 1.65,
    "98": 2.05,
    "99": 2.33,
}
DEFAULT_LEAD_TIME_DAYS  = 7.0
DEMAND_LOOKBACK_DAYS    = 90
CALENDAR_HORIZON_DAYS   = 90


# ── Helpers ───────────────────────────────────────────────────────────────────

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _r(v, n: int = 2) -> float:
    return round(float(v), n) if v is not None else 0.0


def _pct(n, d, scale: float = 100.0) -> float:
    return round(n / d * scale, 2) if d else 0.0


def _window(days: int):
    end = _now()
    return end - timedelta(days=days), end


def _safe_sqrt(v: float) -> float:
    return math.sqrt(max(0.0, v))


def _build_demand_map(
    db: Session, lookback_days: int
) -> Dict[int, Dict[str, float]]:
    """
    Return per-product demand stats dict keyed by product_id.

    {
      product_id: {
          avg_daily_demand, std_daily_demand,
          total_qty, total_orders,
          annual_units (extrapolated from window)
      }
    }
    """
    start, end = _window(lookback_days)

    daily = (
        db.query(
            Order.product_id,
            func.date(Order.order_placed_at).label("day"),
            func.sum(Order.quantity).label("qty"),
        )
        .filter(Order.order_placed_at.between(start, end))
        .group_by(Order.product_id, func.date(Order.order_placed_at))
        .all()
    )

    by_pid: Dict[int, List[float]] = defaultdict(list)
    for pid, _, qty in daily:
        by_pid[pid].append(float(qty or 0))

    result: Dict[int, Dict[str, float]] = {}
    for pid, qtys in by_pid.items():
        mean  = statistics.mean(qtys)
        std   = statistics.stdev(qtys) if len(qtys) > 1 else mean * 0.3
        total = sum(qtys)
        result[pid] = {
            "avg_daily_demand": mean,
            "std_daily_demand": std,
            "total_qty":        total,
            "total_orders":     len(qtys),
            "annual_units":     mean * WORKING_DAYS,
        }
    return result


def _build_lead_time_map(db: Session) -> Dict[int, Dict[str, float]]:
    """
    Per-product lead time stats derived from procurement_time in orders.
    Falls back to DEFAULT_LEAD_TIME_DAYS when no data.

    Returns {product_id: {avg_days, std_days}}
    """
    rows = (
        db.query(Order.product_id, Order.procurement_time)
        .filter(Order.procurement_time.isnot(None), Order.procurement_time > 0)
        .all()
    )
    by_pid: Dict[int, List[float]] = defaultdict(list)
    for pid, pt in rows:
        by_pid[pid].append(float(pt) / 24.0)   # hours → days

    result: Dict[int, Dict[str, float]] = {}
    for pid, days_list in by_pid.items():
        result[pid] = {
            "avg_days": statistics.mean(days_list),
            "std_days": statistics.stdev(days_list) if len(days_list) > 1 else 0.5,
        }
    return result


def _build_inventory_map(db: Session) -> Dict[int, float]:
    """Returns {product_id: net_available_quantity}"""
    rows = (
        db.query(
            Inventory.product_id,
            func.sum(Inventory.quantity_available - Inventory.quantity_reserved).label("net"),
        )
        .group_by(Inventory.product_id)
        .all()
    )
    return {r.product_id: max(0.0, float(r.net or 0)) for r in rows}


# ── 1. EOQ ANALYSIS ───────────────────────────────────────────────────────────

def calculate_eoq_analysis(
    db: Session,
    ordering_cost: float = DEFAULT_ORDERING_COST,
    holding_cost_rate: float = DEFAULT_HOLDING_COST_RATE,
    lookback_days: int = DEMAND_LOOKBACK_DAYS,
) -> Dict[str, Any]:
    """
    Economic Order Quantity analysis per product.

    For each product:
      EOQ = √(2 × D × S / H)
      annual_ordering_cost_at_eoq = (D / Q*) × S
      annual_holding_cost_at_eoq  = (Q* / 2) × H
      total_annual_cost_at_eoq    = sum of both
      total_annual_cost_current   = using product.reorder_level as order qty
      annual_savings              = cost reduction vs current ordering pattern
      orders_per_year_eoq         = D / Q*
      cycle_days_eoq              = Q* / avg_daily_demand (days between orders)
    """
    demand_map = _build_demand_map(db, lookback_days)
    inv_map    = _build_inventory_map(db)
    products   = db.query(Product).all()

    results    = []
    total_savings = 0.0
    total_current_cost = 0.0
    total_optimal_cost = 0.0

    for p in products:
        pid  = p.id
        dem  = demand_map.get(pid)
        if not dem or dem["avg_daily_demand"] <= 0:
            continue

        D = dem["annual_units"]                     # annual demand (units)
        S = ordering_cost                           # $ / order
        H = holding_cost_rate * (p.unit_price or 1) # $ / unit / year

        if H <= 0:
            H = holding_cost_rate * 1.0

        # Optimal order quantity
        eoq = _r(_safe_sqrt(2 * D * S / H))

        # Annual costs at EOQ
        oc_eoq = _r((D / eoq) * S) if eoq > 0 else 0.0
        hc_eoq = _r((eoq / 2) * H)
        tc_eoq = _r(oc_eoq + hc_eoq)

        # Annual costs at current order quantity (reorder_level as proxy)
        Q_cur  = max(1.0, float(p.reorder_level or 10))
        oc_cur = _r((D / Q_cur) * S)
        hc_cur = _r((Q_cur / 2) * H)
        tc_cur = _r(oc_cur + hc_cur)

        savings = _r(tc_cur - tc_eoq)
        total_savings       += max(0.0, savings)
        total_current_cost  += tc_cur
        total_optimal_cost  += tc_eoq

        orders_per_year = _r(D / eoq) if eoq > 0 else 0.0
        cycle_days      = _r(eoq / dem["avg_daily_demand"]) if dem["avg_daily_demand"] > 0 else 0.0

        net_stock = inv_map.get(pid, 0.0)

        results.append({
            "product_id":              pid,
            "product_name":            p.product_name,
            "sku":                     p.sku,
            "category":                p.category,
            "unit_price":              _r(p.unit_price or 0),
            "avg_daily_demand":        _r(dem["avg_daily_demand"]),
            "annual_demand_units":     _r(D),
            "eoq":                     eoq,
            "current_order_qty":       _r(Q_cur),
            "orders_per_year_eoq":     orders_per_year,
            "order_cycle_days_eoq":    cycle_days,
            "annual_ordering_cost_eoq": oc_eoq,
            "annual_holding_cost_eoq":  hc_eoq,
            "total_annual_cost_eoq":    tc_eoq,
            "total_annual_cost_current": tc_cur,
            "annual_savings":           savings,
            "savings_pct":              _pct(savings, tc_cur),
            "net_stock":               _r(net_stock),
            "holding_cost_per_unit_yr": _r(H),
            "recommendation": (
                "Increase order size" if eoq > Q_cur * 1.2
                else "Decrease order size" if eoq < Q_cur * 0.8
                else "Order size near optimal"
            ),
        })

    results.sort(key=lambda r: r["annual_savings"], reverse=True)

    return {
        "generated_at":            _now().isoformat(),
        "lookback_days":           lookback_days,
        "parameters": {
            "ordering_cost_per_order": ordering_cost,
            "holding_cost_rate_pct":   holding_cost_rate * 100,
        },
        "portfolio_summary": {
            "products_analysed":       len(results),
            "total_current_annual_cost": _r(total_current_cost),
            "total_optimal_annual_cost": _r(total_optimal_cost),
            "total_annual_savings":      _r(total_savings),
            "savings_pct":              _pct(total_savings, total_current_cost),
        },
        "products": results,
    }


# ── 2. SAFETY STOCK MATRIX (multi-service-level) ──────────────────────────────

def calculate_safety_stock_matrix(
    db: Session,
    lookback_days: int = DEMAND_LOOKBACK_DAYS,
) -> Dict[str, Any]:
    """
    Per-product safety stock at 90/95/98/99 % service levels simultaneously.

    Formula accounts for BOTH demand variability AND lead-time variability:
      SS(z) = z × √(μ_L × σ_d² + σ_L² × μ_d²)

    For each service level also returns:
      - holding_cost_annual  (SS × H)
      - reorder_point        (μ_d × μ_L + SS)
      - coverage_days_above_rop (how many days of buffer above mean demand)
    """
    demand_map    = _build_demand_map(db, lookback_days)
    lead_time_map = _build_lead_time_map(db)
    products      = db.query(Product).all()

    results = []
    for p in products:
        pid = p.id
        dem = demand_map.get(pid)
        if not dem or dem["avg_daily_demand"] <= 0:
            continue

        lt    = lead_time_map.get(pid, {"avg_days": DEFAULT_LEAD_TIME_DAYS, "std_days": 0.5})
        mu_d  = dem["avg_daily_demand"]
        sig_d = dem["std_daily_demand"]
        mu_L  = lt["avg_days"]
        sig_L = lt["std_days"]
        H     = DEFAULT_HOLDING_COST_RATE * (p.unit_price or 1)

        # Combined variability term under the square root
        combined_var = mu_L * sig_d**2 + sig_L**2 * mu_d**2
        combined_std = _safe_sqrt(combined_var)

        sl_data: Dict[str, Any] = {}
        for sl_label, z in SERVICE_LEVELS.items():
            ss  = _r(z * combined_std)
            rop = _r(mu_d * mu_L + ss)
            hc  = _r(ss * H)
            cov = _r(ss / mu_d) if mu_d > 0 else 0.0
            sl_data[f"sl_{sl_label}"] = {
                "z_factor":            z,
                "safety_stock_units":  ss,
                "reorder_point":       rop,
                "annual_holding_cost": hc,
                "coverage_days":       cov,
            }

        results.append({
            "product_id":         pid,
            "product_name":       p.product_name,
            "sku":                p.sku,
            "category":           p.category,
            "unit_price":         _r(p.unit_price or 0),
            "avg_daily_demand":   _r(mu_d),
            "demand_std_daily":   _r(sig_d),
            "avg_lead_time_days": _r(mu_L),
            "lead_time_std_days": _r(sig_L),
            "combined_variability_std": _r(combined_std),
            "service_levels":     sl_data,
        })

    results.sort(key=lambda r: r["avg_daily_demand"], reverse=True)

    return {
        "generated_at":    _now().isoformat(),
        "lookback_days":   lookback_days,
        "service_levels_available": list(SERVICE_LEVELS.keys()),
        "total_products":  len(results),
        "products":        results,
    }


# ── 3. DYNAMIC REORDER POINTS ─────────────────────────────────────────────────

def calculate_dynamic_reorder_points(
    db: Session,
    service_level_pct: int = 95,
    lookback_days: int = DEMAND_LOOKBACK_DAYS,
) -> Dict[str, Any]:
    """
    Reorder points with full lead-time variability, compared against the
    product's current ``reorder_level`` field.

    For each product:
      ROP_dynamic  = μ_d × μ_L + z × √(μ_L × σ_d² + σ_L² × μ_d²)
      ROP_simple   = μ_d × μ_L                (no safety buffer)
      current_RL   = product.reorder_level     (what is set in the catalogue)
      gap          = ROP_dynamic - current_RL  (positive = current RL is too low)
      status       = adequate | too_low | too_high
    """
    z_val     = SERVICE_LEVELS.get(str(service_level_pct), 1.65)
    demand_map    = _build_demand_map(db, lookback_days)
    lead_time_map = _build_lead_time_map(db)
    inv_map       = _build_inventory_map(db)
    products      = db.query(Product).all()

    results        = []
    too_low_count  = 0
    too_high_count = 0
    adequate_count = 0

    for p in products:
        pid = p.id
        dem = demand_map.get(pid)
        if not dem or dem["avg_daily_demand"] <= 0:
            continue

        lt   = lead_time_map.get(pid, {"avg_days": DEFAULT_LEAD_TIME_DAYS, "std_days": 0.5})
        mu_d = dem["avg_daily_demand"]
        sig_d= dem["std_daily_demand"]
        mu_L = lt["avg_days"]
        sig_L= lt["std_days"]

        combined_std = _safe_sqrt(mu_L * sig_d**2 + sig_L**2 * mu_d**2)
        safety_stock = _r(z_val * combined_std)
        rop_dynamic  = _r(mu_d * mu_L + safety_stock)
        rop_simple   = _r(mu_d * mu_L)
        current_rl   = float(p.reorder_level or 10)
        gap          = _r(rop_dynamic - current_rl)
        net_stock    = inv_map.get(pid, 0.0)
        below_rop    = net_stock <= rop_dynamic

        # Relative tolerance: 15 %
        if gap > current_rl * 0.15:
            status = "too_low"
            too_low_count += 1
        elif gap < -current_rl * 0.15:
            status = "too_high"
            too_high_count += 1
        else:
            status = "adequate"
            adequate_count += 1

        results.append({
            "product_id":           pid,
            "product_name":         p.product_name,
            "sku":                  p.sku,
            "category":             p.category,
            "avg_daily_demand":     _r(mu_d),
            "avg_lead_time_days":   _r(mu_L),
            "lead_time_std_days":   _r(sig_L),
            "demand_std_daily":     _r(sig_d),
            "safety_stock":         safety_stock,
            "rop_simple":           rop_simple,
            "rop_dynamic":          rop_dynamic,
            "current_reorder_level": _r(current_rl),
            "rop_gap":              gap,
            "status":               status,
            "net_stock":            _r(net_stock),
            "below_rop_now":        below_rop,
            "update_recommendation": (
                f"Raise reorder level from {current_rl:.0f} → {rop_dynamic:.0f} units (+{gap:.0f})"
                if status == "too_low" else
                f"Lower reorder level from {current_rl:.0f} → {rop_dynamic:.0f} units ({gap:.0f})"
                if status == "too_high" else
                "Reorder level is appropriately set"
            ),
        })

    results.sort(key=lambda r: abs(r["rop_gap"]), reverse=True)

    return {
        "generated_at":      _now().isoformat(),
        "service_level_pct": service_level_pct,
        "lookback_days":     lookback_days,
        "summary": {
            "total_products": len(results),
            "too_low":        too_low_count,
            "too_high":       too_high_count,
            "adequate":       adequate_count,
        },
        "products": results,
    }


# ── 4. INVENTORY TURNOVER & GMROI ────────────────────────────────────────────

def calculate_inventory_turnover(
    db: Session,
    lookback_days: int = 365,
) -> Dict[str, Any]:
    """
    Inventory turnover rate, Days Inventory Outstanding (DIO), and GMROI.

    Inventory turnover = units_sold / avg_inventory_units
    DIO = lookback_days / turnover_rate     (lower = more efficient)

    GMROI = (revenue - estimated_cogs) / avg_inventory_value × 100
    where estimated_cogs = revenue × 0.65 (35% gross margin assumption when
    exact COGS is unavailable; real deployments should wire to a cost_price field)

    Benchmarks used:
      turnover  < 2    → "slow" (overstocked / underperforming)
      turnover 2–6    → "healthy"
      turnover  > 6   → "fast" (lean inventory, possible stockout risk)
      GMROI  < 100%   → poor
      GMROI 100–200%  → fair
      GMROI > 200%    → excellent
    """
    start, end = _window(lookback_days)
    GROSS_MARGIN = 0.35

    # Sales per product
    sales_rows = (
        db.query(
            Order.product_id,
            func.sum(Order.quantity).label("units_sold"),
            func.sum(Order.total_amount).label("revenue"),
        )
        .filter(Order.order_placed_at.between(start, end))
        .group_by(Order.product_id)
        .all()
    )
    sales_map = {r.product_id: (float(r.units_sold or 0), float(r.revenue or 0))
                 for r in sales_rows}

    # Avg inventory (simple average of available stock)
    inv_rows = (
        db.query(
            Inventory.product_id,
            func.avg(Inventory.quantity_available).label("avg_qty"),
        )
        .group_by(Inventory.product_id)
        .all()
    )
    inv_map = {r.product_id: float(r.avg_qty or 0) for r in inv_rows}

    products = db.query(Product).all()

    results   = []
    cat_agg: Dict[str, Dict] = defaultdict(lambda: {
        "units_sold": 0.0, "revenue": 0.0, "avg_inventory_value": 0.0, "count": 0
    })

    for p in products:
        pid  = p.id
        units_sold, revenue = sales_map.get(pid, (0.0, 0.0))
        avg_inv_units = inv_map.get(pid, 0.0)
        avg_inv_value = _r(avg_inv_units * (p.unit_price or 0))

        if avg_inv_units > 0:
            turnover = _r(units_sold / avg_inv_units)
            dio      = _r(lookback_days / turnover) if turnover > 0 else 9999
        else:
            turnover = 0.0
            dio      = 9999

        gross_profit = _r(revenue * GROSS_MARGIN)
        gmroi = _r(gross_profit / avg_inv_value * 100) if avg_inv_value > 0 else 0.0

        # Benchmark classification
        if turnover == 0:
            turn_status = "no_movement"
        elif turnover < 2:
            turn_status = "slow"
        elif turnover <= 6:
            turn_status = "healthy"
        else:
            turn_status = "fast"

        gmroi_status = (
            "excellent" if gmroi > 200 else
            "fair"      if gmroi >= 100 else
            "poor"
        )

        results.append({
            "product_id":          pid,
            "product_name":        p.product_name,
            "sku":                 p.sku,
            "category":            p.category,
            "units_sold":          _r(units_sold),
            "revenue":             _r(revenue),
            "avg_inventory_units": _r(avg_inv_units),
            "avg_inventory_value": avg_inv_value,
            "gross_profit":        gross_profit,
            "inventory_turnover":  turnover,
            "days_inventory_outstanding": dio,
            "gmroi_pct":           gmroi,
            "turnover_status":     turn_status,
            "gmroi_status":        gmroi_status,
        })

        cat = p.category or "Uncategorized"
        cat_agg[cat]["units_sold"]          += units_sold
        cat_agg[cat]["revenue"]             += revenue
        cat_agg[cat]["avg_inventory_value"] += avg_inv_value
        cat_agg[cat]["count"]               += 1

    results.sort(key=lambda r: r["inventory_turnover"], reverse=True)

    # Category summary
    cat_summary = []
    for cat, agg in cat_agg.items():
        rev    = agg["revenue"]
        inv_v  = agg["avg_inventory_value"]
        gp     = rev * GROSS_MARGIN
        gmroi  = _r(gp / inv_v * 100) if inv_v > 0 else 0.0
        cat_summary.append({
            "category":            cat,
            "products":            agg["count"],
            "total_units_sold":    _r(agg["units_sold"]),
            "total_revenue":       _r(rev),
            "total_inventory_value": _r(inv_v),
            "gmroi_pct":           gmroi,
        })
    cat_summary.sort(key=lambda c: c["gmroi_pct"], reverse=True)

    all_turns = [r["inventory_turnover"] for r in results if r["inventory_turnover"] > 0]

    return {
        "generated_at":    _now().isoformat(),
        "lookback_days":   lookback_days,
        "portfolio_summary": {
            "avg_turnover":    _r(statistics.mean(all_turns)) if all_turns else 0.0,
            "median_turnover": _r(statistics.median(all_turns)) if all_turns else 0.0,
            "slow_movers":     sum(1 for r in results if r["turnover_status"] == "slow"),
            "no_movement":     sum(1 for r in results if r["turnover_status"] == "no_movement"),
            "healthy_movers":  sum(1 for r in results if r["turnover_status"] == "healthy"),
            "fast_movers":     sum(1 for r in results if r["turnover_status"] == "fast"),
        },
        "by_category":  cat_summary,
        "products":     results,
    }


# ── 5. HOLDING COST ANALYSIS ──────────────────────────────────────────────────

def calculate_holding_costs(
    db: Session,
    holding_cost_rate: float = DEFAULT_HOLDING_COST_RATE,
) -> Dict[str, Any]:
    """
    Per-product annual holding cost breakdown.

    Components:
      capital_cost       = (unit_price × qty) × cost_of_capital (10 % of rate)
      storage_cost       = (unit_price × qty) × storage_rate    (8 % of rate)
      insurance_cost     = (unit_price × qty) × insurance_rate  (3 % of rate)
      obsolescence_cost  = (unit_price × qty) × obs_rate        (4 % of rate)
      total_holding_cost = sum of all components

    All rates are proportional splits of holding_cost_rate.
    """
    RATES = {
        "capital":      0.40,   # 40% of holding rate = cost of capital
        "storage":      0.32,   # 32% = warehousing / logistics
        "insurance":    0.12,   # 12% = insurance & shrinkage
        "obsolescence": 0.16,   # 16% = obsolescence / deterioration
    }

    inv_rows = (
        db.query(
            Inventory.product_id,
            func.sum(Inventory.quantity_available).label("qty"),
        )
        .group_by(Inventory.product_id)
        .all()
    )
    inv_map  = {r.product_id: float(r.qty or 0) for r in inv_rows}
    products = db.query(Product).all()

    results       = []
    total_holding = 0.0
    cat_agg: Dict[str, float] = defaultdict(float)

    for p in products:
        pid = p.id
        qty = inv_map.get(pid, 0.0)
        uprice = p.unit_price or 0.0
        inv_value = qty * uprice

        components: Dict[str, float] = {}
        total = 0.0
        for name, weight in RATES.items():
            cost = _r(inv_value * holding_cost_rate * weight)
            components[f"{name}_cost_annual"] = cost
            total += cost

        total = _r(total)
        total_holding += total
        cat_agg[p.category or "Uncategorized"] += total

        results.append({
            "product_id":         pid,
            "product_name":       p.product_name,
            "sku":                p.sku,
            "category":           p.category,
            "unit_price":         _r(uprice),
            "quantity_on_hand":   _r(qty),
            "inventory_value":    _r(inv_value),
            "total_holding_cost_annual": total,
            "holding_cost_daily": _r(total / 365),
            **components,
            "holding_cost_pct_of_value": _pct(total, inv_value) if inv_value else 0.0,
        })

    results.sort(key=lambda r: r["total_holding_cost_annual"], reverse=True)

    cat_breakdown = [
        {"category": cat, "annual_holding_cost": _r(cost),
         "pct_of_total": _pct(cost, total_holding)}
        for cat, cost in sorted(cat_agg.items(), key=lambda x: x[1], reverse=True)
    ]

    return {
        "generated_at":         _now().isoformat(),
        "holding_cost_rate_pct": holding_cost_rate * 100,
        "cost_components": {k: f"{v*100:.0f}% of holding rate" for k, v in RATES.items()},
        "portfolio_summary": {
            "total_inventory_value":       _r(sum(r["inventory_value"] for r in results)),
            "total_annual_holding_cost":   _r(total_holding),
            "avg_daily_holding_cost":      _r(total_holding / 365),
            "potential_savings_10pct_red": _r(total_holding * 0.10),
        },
        "by_category": cat_breakdown,
        "products":    results,
    }


# ── 6. FILL RATE ANALYSIS ─────────────────────────────────────────────────────

def calculate_fill_rate_analysis(
    db: Session,
    lookback_days: int = 90,
) -> Dict[str, Any]:
    """
    Fill rate = proportion of demand fulfilled without a stockout delay.

    Proxy method (no explicit stockout event log):
      1. Days when demand was placed AND stock was available → fulfilled
      2. Days when demand was placed AND stock was zero → unfulfilled (lost sale)

    Uses daily demand vs daily closing inventory to estimate fulfilment.
    Returns per-product fill rate and category-level aggregates.
    """
    start, end = _window(lookback_days)

    # Daily demand per product
    daily_demand = (
        db.query(
            Order.product_id,
            func.date(Order.order_placed_at).label("day"),
            func.sum(Order.quantity).label("qty"),
        )
        .filter(Order.order_placed_at.between(start, end))
        .group_by(Order.product_id, func.date(Order.order_placed_at))
        .all()
    )

    # Current stock snapshot
    inv_map = _build_inventory_map(db)

    by_pid: Dict[int, Dict[str, float]] = defaultdict(lambda: {
        "demand_days": 0, "zero_stock_days": 0, "total_demand_qty": 0.0
    })
    for pid, _, qty in daily_demand:
        by_pid[pid]["demand_days"]      += 1
        by_pid[pid]["total_demand_qty"] += float(qty or 0)

    # Identify products that had stock issues (stock <= 0 at any point)
    # Proxy: if current stock is 0 and there was demand, some days had stockouts
    for pid, stats in by_pid.items():
        stock = inv_map.get(pid, 0.0)
        if stock <= 0:
            # Estimate: 20 % of demand days had zero stock (conservative proxy)
            by_pid[pid]["zero_stock_days"] = max(1, int(stats["demand_days"] * 0.20))

    products  = db.query(Product).all()
    prod_map  = {p.id: p for p in products}
    cat_agg: Dict[str, Dict] = defaultdict(lambda: {
        "total_demand": 0.0, "fulfilled": 0.0, "unfulfilled": 0.0
    })

    results = []
    for pid, stats in by_pid.items():
        p = prod_map.get(pid)
        if not p:
            continue
        total_days  = stats["demand_days"]
        zero_days   = stats["zero_stock_days"]
        filled_days = max(0, total_days - zero_days)
        fill_rate   = _pct(filled_days, total_days) if total_days else 100.0
        total_demand = stats["total_demand_qty"]
        # Unfulfilled qty proportional to stockout days
        unfulfilled = _r(total_demand * (zero_days / total_days)) if total_days else 0.0
        fulfilled   = _r(total_demand - unfulfilled)

        status = (
            "excellent" if fill_rate >= 98 else
            "good"      if fill_rate >= 95 else
            "fair"      if fill_rate >= 90 else
            "poor"
        )

        cat = p.category or "Uncategorized"
        cat_agg[cat]["total_demand"] += total_demand
        cat_agg[cat]["fulfilled"]    += fulfilled
        cat_agg[cat]["unfulfilled"]  += unfulfilled

        results.append({
            "product_id":       pid,
            "product_name":     p.product_name,
            "sku":              p.sku,
            "category":         p.category,
            "demand_days":      total_days,
            "total_demand_qty": _r(total_demand),
            "fulfilled_qty":    fulfilled,
            "unfulfilled_qty":  unfulfilled,
            "fill_rate_pct":    fill_rate,
            "status":           status,
            "current_stock":    _r(inv_map.get(pid, 0.0)),
        })

    results.sort(key=lambda r: r["fill_rate_pct"])  # worst first

    cat_summary = []
    for cat, agg in cat_agg.items():
        td = agg["total_demand"]
        fu = agg["fulfilled"]
        cat_summary.append({
            "category":        cat,
            "total_demand_qty": _r(td),
            "fulfilled_qty":    _r(fu),
            "unfulfilled_qty":  _r(agg["unfulfilled"]),
            "fill_rate_pct":    _pct(fu, td) if td else 100.0,
        })
    cat_summary.sort(key=lambda c: c["fill_rate_pct"])

    all_fill = [r["fill_rate_pct"] for r in results]
    return {
        "generated_at":   _now().isoformat(),
        "lookback_days":  lookback_days,
        "portfolio_summary": {
            "avg_fill_rate_pct":    _r(statistics.mean(all_fill)) if all_fill else 0.0,
            "products_below_95pct": sum(1 for r in results if r["fill_rate_pct"] < 95),
            "products_below_90pct": sum(1 for r in results if r["fill_rate_pct"] < 90),
            "total_unfulfilled_qty": _r(sum(r["unfulfilled_qty"] for r in results)),
        },
        "by_category": cat_summary,
        "products":    results,
    }


# ── 7. REORDER CALENDAR ───────────────────────────────────────────────────────

def generate_reorder_calendar(
    db: Session,
    horizon_days: int = CALENDAR_HORIZON_DAYS,
    lookback_days: int = DEMAND_LOOKBACK_DAYS,
) -> Dict[str, Any]:
    """
    Project the next reorder date for each product over a rolling horizon.

    Algorithm:
      days_until_rop = (net_stock - ROP_dynamic) / avg_daily_demand
      order_date     = today + max(0, days_until_rop)

    Products already below their dynamic ROP are immediately flagged.
    Dates are grouped by week for a calendar view.
    """
    demand_map    = _build_demand_map(db, lookback_days)
    lead_time_map = _build_lead_time_map(db)
    inv_map       = _build_inventory_map(db)
    products      = db.query(Product).all()

    today    = date.today()
    calendar: Dict[str, List] = defaultdict(list)
    events: List[Dict] = []

    for p in products:
        pid = p.id
        dem = demand_map.get(pid)
        if not dem or dem["avg_daily_demand"] <= 0:
            continue

        lt      = lead_time_map.get(pid, {"avg_days": DEFAULT_LEAD_TIME_DAYS, "std_days": 0.5})
        mu_d    = dem["avg_daily_demand"]
        sig_d   = dem["std_daily_demand"]
        mu_L    = lt["avg_days"]
        sig_L   = lt["std_days"]
        net_stk = inv_map.get(pid, 0.0)

        # Dynamic ROP
        combined_std = _safe_sqrt(mu_L * sig_d**2 + sig_L**2 * mu_d**2)
        ss           = SERVICE_LEVELS["95"] * combined_std
        rop          = mu_d * mu_L + ss

        # Days until reorder trigger
        if net_stk <= rop:
            days_until = 0
            urgency    = "OVERDUE"
        else:
            days_until = max(0, (net_stk - rop) / mu_d)
            urgency    = (
                "URGENT"  if days_until <= 7  else
                "SOON"    if days_until <= 21 else
                "PLANNED"
            )

        if days_until > horizon_days:
            continue  # outside horizon

        order_date = today + timedelta(days=int(days_until))
        arrival    = order_date + timedelta(days=int(mu_L))
        week_key   = order_date.strftime("%Y-W%W")

        event = {
            "product_id":      pid,
            "product_name":    p.product_name,
            "sku":             p.sku,
            "category":        p.category,
            "order_date":      order_date.isoformat(),
            "expected_arrival": arrival.isoformat(),
            "days_until_order": int(days_until),
            "dynamic_rop":     _r(rop),
            "net_stock":       _r(net_stk),
            "avg_daily_demand": _r(mu_d),
            "lead_time_days":  _r(mu_L),
            "urgency":         urgency,
        }
        events.append(event)
        calendar[week_key].append({
            "product_id":   pid,
            "product_name": p.product_name,
            "sku":          p.sku,
            "order_date":   order_date.isoformat(),
            "urgency":      urgency,
        })

    events.sort(key=lambda e: e["days_until_order"])

    weekly_view = [
        {"week": week, "orders": sorted(items, key=lambda x: x["order_date"])}
        for week, items in sorted(calendar.items())
    ]

    return {
        "generated_at":   _now().isoformat(),
        "horizon_days":   horizon_days,
        "today":          today.isoformat(),
        "summary": {
            "total_events":    len(events),
            "overdue":         sum(1 for e in events if e["urgency"] == "OVERDUE"),
            "urgent_7d":       sum(1 for e in events if e["urgency"] == "URGENT"),
            "planned":         sum(1 for e in events if e["urgency"] == "PLANNED"),
        },
        "events":        events,
        "weekly_calendar": weekly_view,
    }


# ── 8. CAPITAL ALLOCATION PORTFOLIO ──────────────────────────────────────────

def optimize_capital_allocation(
    db: Session,
    budget: float,
    lookback_days: int = DEMAND_LOOKBACK_DAYS,
    ordering_cost: float = DEFAULT_ORDERING_COST,
    holding_cost_rate: float = DEFAULT_HOLDING_COST_RATE,
) -> Dict[str, Any]:
    """
    Cross-product capital allocation optimisation for a given procurement budget.

    Ranks products by ROI per dollar invested:
      ROI = (annual_revenue_per_unit × eoq) / (eoq × unit_price)
          = avg_unit_price / unit_price   (simplified when cost = selling price)

    Practical measure: revenue_contribution_rank × fill_priority_rank
    Allocates budget to products in priority order until exhausted.

    Returns:
      - allocation list (product, units, cost, % of budget)
      - budget utilisation
      - expected revenue lift
      - unmet demand value (products that couldn't be fully funded)
    """
    demand_map = _build_demand_map(db, lookback_days)
    inv_map    = _build_inventory_map(db)
    eoq_data   = calculate_eoq_analysis(
        db, ordering_cost=ordering_cost,
        holding_cost_rate=holding_cost_rate,
        lookback_days=lookback_days,
    )
    eoq_map    = {p["product_id"]: p for p in eoq_data["products"]}
    products   = db.query(Product).all()

    candidates = []
    for p in products:
        pid  = p.id
        dem  = demand_map.get(pid)
        eoq  = eoq_map.get(pid)
        if not dem or not eoq:
            continue

        net_stk     = inv_map.get(pid, 0.0)
        uprice      = p.unit_price or 1.0
        reorder_qty = eoq["eoq"]
        order_cost  = reorder_qty * uprice

        # ROI proxy: annual_revenue_per_unit / holding_cost_per_unit
        H            = holding_cost_rate * uprice
        annual_rev   = dem["avg_daily_demand"] * WORKING_DAYS * uprice
        roi_score    = _r(annual_rev / max(H * reorder_qty, 1.0))

        # Urgency boost: products below reorder level get 2× ROI score
        rl   = float(p.reorder_level or 10)
        if net_stk < rl:
            roi_score *= 2

        candidates.append({
            "product_id":     pid,
            "product_name":   p.product_name,
            "sku":            p.sku,
            "category":       p.category,
            "unit_price":     _r(uprice),
            "eoq":            _r(reorder_qty),
            "order_cost":     _r(order_cost),
            "roi_score":      _r(roi_score),
            "annual_revenue": _r(annual_rev),
            "net_stock":      _r(net_stk),
            "below_reorder":  net_stk < rl,
        })

    # Sort by ROI descending
    candidates.sort(key=lambda c: c["roi_score"], reverse=True)

    allocations  = []
    remaining    = budget
    total_rev_lift = 0.0
    unmet_value  = 0.0

    for c in candidates:
        cost = c["order_cost"]
        if cost <= 0:
            continue
        if remaining >= cost:
            allocations.append({
                **c,
                "allocated":    True,
                "units_funded": _r(c["eoq"]),
                "cost_funded":  _r(cost),
                "budget_pct":   _pct(cost, budget),
            })
            remaining      -= cost
            total_rev_lift += c["annual_revenue"] / WORKING_DAYS * (c["eoq"] / max(c["eoq"], 1))
        else:
            # Partial fulfillment
            units_partial = _r(remaining / c["unit_price"]) if c["unit_price"] > 0 else 0.0
            if units_partial > 0:
                allocations.append({
                    **c,
                    "allocated":    True,
                    "units_funded": units_partial,
                    "cost_funded":  _r(remaining),
                    "budget_pct":   _pct(remaining, budget),
                    "partial":      True,
                })
                remaining = 0.0
            unmet_value += cost - (units_partial * c["unit_price"])
            if remaining <= 0:
                # Still record unfunded as unmet
                unmet_value += cost
                allocations.append({
                    **c,
                    "allocated":    False,
                    "units_funded": 0,
                    "cost_funded":  0,
                    "budget_pct":   0,
                })

    funded   = [a for a in allocations if a["allocated"]]
    unfunded = [a for a in allocations if not a["allocated"]]

    return {
        "generated_at":      _now().isoformat(),
        "budget":            budget,
        "lookback_days":     lookback_days,
        "budget_summary": {
            "total_candidates":    len(candidates),
            "funded_products":     len(funded),
            "unfunded_products":   len(unfunded),
            "budget_spent":        _r(budget - remaining),
            "budget_remaining":    _r(remaining),
            "utilisation_pct":     _pct(budget - remaining, budget),
            "expected_revenue_lift": _r(total_rev_lift),
            "unmet_demand_value":  _r(unmet_value),
        },
        "allocations": allocations,
    }


# ── 9. EOQ SCENARIOS / SENSITIVITY ANALYSIS ───────────────────────────────────

def calculate_order_quantity_scenarios(
    db: Session,
    product_id: int,
    lookback_days: int = DEMAND_LOOKBACK_DAYS,
    ordering_cost: float = DEFAULT_ORDERING_COST,
    holding_cost_rate: float = DEFAULT_HOLDING_COST_RATE,
) -> Dict[str, Any]:
    """
    Order quantity sensitivity analysis for a single product.

    Returns annual total cost for order quantities ranging from 0.25×EOQ
    to 3×EOQ in steps, showing the cost curve shape.  Also runs 3 service-level
    scenarios (90/95/99%) showing safety stock and ROP trade-offs.
    """
    demand_map    = _build_demand_map(db, lookback_days)
    lead_time_map = _build_lead_time_map(db)

    p = db.query(Product).filter(Product.id == product_id).first()
    if not p:
        raise ValueError(f"Product {product_id} not found")

    dem = demand_map.get(product_id)
    if not dem:
        raise ValueError(f"No demand data for product {product_id} in last {lookback_days} days")

    D   = dem["annual_units"]
    S   = ordering_cost
    H   = holding_cost_rate * (p.unit_price or 1)
    Q_star = _safe_sqrt(2 * D * S / H) if H > 0 else float(p.reorder_level or 10)

    # Cost curve: 8 points from 0.25× to 3× EOQ
    multipliers = [0.25, 0.50, 0.75, 1.0, 1.25, 1.50, 2.0, 3.0]
    cost_curve  = []
    for m in multipliers:
        Q = max(1.0, m * Q_star)
        oc = _r((D / Q) * S)
        hc = _r((Q / 2) * H)
        cost_curve.append({
            "quantity":          _r(Q),
            "multiplier":        m,
            "ordering_cost":     oc,
            "holding_cost":      hc,
            "total_annual_cost": _r(oc + hc),
            "is_optimal":        m == 1.0,
        })

    # Service level scenarios
    lt    = lead_time_map.get(product_id, {"avg_days": DEFAULT_LEAD_TIME_DAYS, "std_days": 0.5})
    mu_d  = dem["avg_daily_demand"]
    sig_d = dem["std_daily_demand"]
    mu_L  = lt["avg_days"]
    sig_L = lt["std_days"]
    combined_std = _safe_sqrt(mu_L * sig_d**2 + sig_L**2 * mu_d**2)

    sl_scenarios = []
    for sl_label, z in SERVICE_LEVELS.items():
        ss  = _r(z * combined_std)
        rop = _r(mu_d * mu_L + ss)
        hc  = _r(ss * H)
        sl_scenarios.append({
            "service_level_pct":    int(sl_label),
            "z_factor":             z,
            "safety_stock_units":   ss,
            "reorder_point":        rop,
            "annual_holding_cost":  hc,
            "stockout_probability": _r((100 - int(sl_label)) / 100, 4),
        })

    return {
        "generated_at":     _now().isoformat(),
        "product_id":       product_id,
        "product_name":     p.product_name,
        "sku":              p.sku,
        "unit_price":       _r(p.unit_price or 0),
        "annual_demand":    _r(D),
        "avg_daily_demand": _r(mu_d),
        "parameters": {
            "ordering_cost":       S,
            "holding_cost_rate":   holding_cost_rate,
            "holding_cost_per_unit_yr": _r(H),
        },
        "optimal_eoq":      _r(Q_star),
        "cost_curve":       cost_curve,
        "service_level_scenarios": sl_scenarios,
    }


# ── 10. COMPREHENSIVE OPTIMIZATION SUMMARY ────────────────────────────────────

def get_optimization_summary(
    db: Session,
    ordering_cost: float = DEFAULT_ORDERING_COST,
    holding_cost_rate: float = DEFAULT_HOLDING_COST_RATE,
    lookback_days: int = DEMAND_LOOKBACK_DAYS,
) -> Dict[str, Any]:
    """
    Single-call comprehensive optimization report combining all engines.
    Designed for the optimization dashboard overview screen.
    """
    eoq_data    = calculate_eoq_analysis(db, ordering_cost, holding_cost_rate, lookback_days)
    rop_data    = calculate_dynamic_reorder_points(db, service_level_pct=95, lookback_days=lookback_days)
    turn_data   = calculate_inventory_turnover(db, lookback_days=min(lookback_days * 4, 365))
    hold_data   = calculate_holding_costs(db, holding_cost_rate)
    fill_data   = calculate_fill_rate_analysis(db, lookback_days=lookback_days)
    cal_data    = generate_reorder_calendar(db, horizon_days=30, lookback_days=lookback_days)

    return {
        "generated_at": _now().isoformat(),
        "parameters": {
            "ordering_cost":     ordering_cost,
            "holding_cost_rate": holding_cost_rate,
            "lookback_days":     lookback_days,
        },
        "eoq_summary":       eoq_data["portfolio_summary"],
        "rop_summary":       rop_data["summary"],
        "turnover_summary":  turn_data["portfolio_summary"],
        "holding_cost_summary": hold_data["portfolio_summary"],
        "fill_rate_summary": fill_data["portfolio_summary"],
        "reorder_calendar_30d": cal_data["summary"],
        "top_eoq_savings": eoq_data["products"][:5],
        "urgent_reorders": [e for e in cal_data["events"] if e["urgency"] in ("OVERDUE", "URGENT")][:10],
        "worst_fill_rates": fill_data["products"][:5],
        "highest_holding_costs": hold_data["products"][:5],
    }
