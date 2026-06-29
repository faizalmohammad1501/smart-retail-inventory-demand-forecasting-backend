"""
AI-Powered Inventory Insights Service
=======================================
Rule-based and statistical analytics engine that transforms raw inventory,
sales, supplier, and demand data into actionable business intelligence.

Analytical methods used
-----------------------
- Z-score / IQR   → demand anomaly detection
- ABC analysis    → revenue-weighted product classification
- XYZ analysis    → demand variability classification (CV-based)
- Days-on-Hand    → slow-mover / dead-stock detection
- Composite score → inventory risk scoring (stockout + overstock + obsolescence)
- Lead-time std   → supplier reliability trend
- Opportunity gap → revenue leakage from stockouts + demand uplift
- Momentum rank   → fast/accelerating products

All heavy aggregation runs in SQL (single round-trip per analysis).
Python is only used for post-processing and statistical calculations.
"""

import math
import statistics
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import func, case, and_, distinct
from sqlalchemy.orm import Session

from app.models.inventory import Inventory
from app.models.product import Product
from app.models.sales import Order
from app.models.supplier import Supplier


# ── Helpers ───────────────────────────────────────────────────────────────────

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _r(v, n: int = 2) -> float:
    return round(float(v), n) if v is not None else 0.0


def _pct(n, d, scale: float = 100.0) -> float:
    return round(n / d * scale, 2) if d else 0.0


def _cv(values: List[float]) -> float:
    """Coefficient of variation (std / mean).  0 when mean == 0."""
    if not values or len(values) < 2:
        return 0.0
    mean = statistics.mean(values)
    if mean == 0:
        return 0.0
    return _r(statistics.stdev(values) / mean)


def _zscore(value: float, mean: float, std: float) -> float:
    return round((value - mean) / std, 3) if std else 0.0


def _window(days: int):
    end = _now()
    return end - timedelta(days=days), end


# ── 1. DEMAND ANOMALY DETECTION ───────────────────────────────────────────────

def detect_demand_anomalies(
    db: Session,
    lookback_days: int = 90,
    z_threshold: float = 2.5,
    min_orders: int = 5,
) -> Dict[str, Any]:
    """
    Identify products with statistically abnormal demand (spikes or drops).

    Method: for each product compute daily order-quantity buckets over the
    lookback window, then flag buckets whose Z-score exceeds *z_threshold*.
    Products with < *min_orders* data points are excluded (insufficient history).

    Returns:
        anomalies     — list of (product, date, quantity, z_score, direction)
        spike_products — products with positive demand spikes
        drop_products  — products with demand drops
        stats          — window stats
    """
    start, end = _window(lookback_days)

    # Raw daily demand per product
    rows = (
        db.query(
            Order.product_id,
            func.date(Order.order_placed_at).label("day"),
            func.sum(Order.quantity).label("qty"),
        )
        .filter(Order.order_placed_at.between(start, end))
        .group_by(Order.product_id, func.date(Order.order_placed_at))
        .all()
    )

    # Bucket by product
    by_product: Dict[int, List[Tuple]] = defaultdict(list)
    for pid, day, qty in rows:
        by_product[pid].append((str(day), float(qty or 0)))

    # Product name lookup
    product_map = {
        p.id: (p.product_name, p.sku, p.category)
        for p in db.query(Product.id, Product.product_name, Product.sku, Product.category).all()
    }

    anomalies: List[Dict] = []
    spike_pids: set = set()
    drop_pids: set = set()

    for pid, series in by_product.items():
        if len(series) < min_orders:
            continue
        qtys = [q for _, q in series]
        mean = statistics.mean(qtys)
        std  = statistics.stdev(qtys) if len(qtys) > 1 else 0.0
        if std == 0:
            continue

        for day, qty in series:
            z = _zscore(qty, mean, std)
            if abs(z) >= z_threshold:
                pname, sku, cat = product_map.get(pid, (f"Product {pid}", "", ""))
                direction = "spike" if z > 0 else "drop"
                anomalies.append({
                    "product_id":   pid,
                    "product_name": pname,
                    "sku":          sku,
                    "category":     cat,
                    "date":         day,
                    "quantity":     qty,
                    "mean_quantity": _r(mean),
                    "std_dev":      _r(std),
                    "z_score":      z,
                    "direction":    direction,
                    "severity":     "high" if abs(z) >= 3.5 else "medium",
                })
                if direction == "spike":
                    spike_pids.add(pid)
                else:
                    drop_pids.add(pid)

    anomalies.sort(key=lambda x: abs(x["z_score"]), reverse=True)

    return {
        "lookback_days":   lookback_days,
        "z_threshold":     z_threshold,
        "total_anomalies": len(anomalies),
        "spike_products":  len(spike_pids),
        "drop_products":   len(drop_pids),
        "anomalies":       anomalies[:100],  # top 100 by severity
    }


# ── 2. PRODUCT VELOCITY CLASSIFICATION (ABC × XYZ) ───────────────────────────

def classify_product_velocity(
    db: Session,
    lookback_days: int = 90,
) -> Dict[str, Any]:
    """
    ABC/XYZ product segmentation.

    ABC — revenue contribution:
      A = top 70 % of cumulative revenue
      B = next 20 %
      C = bottom 10 %

    XYZ — demand variability (coefficient of variation on weekly buckets):
      X = CV ≤ 0.5  (stable / predictable)
      Y = 0.5 < CV ≤ 1.0  (moderate variability)
      Z = CV > 1.0  (erratic / unpredictable)

    Returns per-product classification plus segment summaries.
    """
    start, end = _window(lookback_days)

    # Revenue per product
    rev_rows = (
        db.query(
            Order.product_id,
            func.sum(Order.total_amount).label("revenue"),
            func.sum(Order.quantity).label("total_qty"),
            func.count(Order.id).label("order_count"),
        )
        .filter(Order.order_placed_at.between(start, end))
        .group_by(Order.product_id)
        .all()
    )

    if not rev_rows:
        return {"lookback_days": lookback_days, "products": [], "segment_summary": {}}

    # Weekly demand per product (for CV)
    weekly_rows = (
        db.query(
            Order.product_id,
            func.strftime("%Y-%W", Order.order_placed_at).label("week"),
            func.sum(Order.quantity).label("qty"),
        )
        .filter(Order.order_placed_at.between(start, end))
        .group_by(Order.product_id, func.strftime("%Y-%W", Order.order_placed_at))
        .all()
    )

    weekly_by_pid: Dict[int, List[float]] = defaultdict(list)
    for pid, week, qty in weekly_rows:
        weekly_by_pid[pid].append(float(qty or 0))

    # Product info
    product_map = {
        p.id: (p.product_name, p.sku, p.category, p.unit_price)
        for p in db.query(
            Product.id, Product.product_name, Product.sku,
            Product.category, Product.unit_price,
        ).all()
    }

    # ABC classification
    total_rev = sum(r.revenue or 0 for r in rev_rows)
    sorted_rev = sorted(rev_rows, key=lambda r: r.revenue or 0, reverse=True)
    cumulative = 0.0
    abc_class: Dict[int, str] = {}
    for r in sorted_rev:
        cumulative += (r.revenue or 0) / total_rev * 100
        if cumulative <= 70:
            abc_class[r.product_id] = "A"
        elif cumulative <= 90:
            abc_class[r.product_id] = "B"
        else:
            abc_class[r.product_id] = "C"

    products = []
    seg_counts: Dict[str, int] = defaultdict(int)

    for r in rev_rows:
        pid = r.product_id
        pname, sku, cat, upr = product_map.get(pid, (f"Product {pid}", "", "", 0))
        weekly = weekly_by_pid.get(pid, [])
        cv = _cv(weekly)
        if cv <= 0.5:
            xyz = "X"
        elif cv <= 1.0:
            xyz = "Y"
        else:
            xyz = "Z"

        abc = abc_class.get(pid, "C")
        segment = f"{abc}{xyz}"
        seg_counts[segment] += 1

        # Velocity label
        if abc == "A" and xyz in ("X", "Y"):
            velocity = "fast_mover"
        elif abc == "C" and xyz == "Z":
            velocity = "slow_erratic"
        elif abc == "C":
            velocity = "slow_mover"
        elif abc == "B":
            velocity = "moderate_mover"
        else:
            velocity = "fast_erratic"

        products.append({
            "product_id":   pid,
            "product_name": pname,
            "sku":          sku,
            "category":     cat,
            "revenue":      _r(r.revenue or 0),
            "total_qty":    int(r.total_qty or 0),
            "order_count":  int(r.order_count or 0),
            "rev_pct":      _pct(r.revenue or 0, total_rev),
            "abc_class":    abc,
            "xyz_class":    xyz,
            "segment":      segment,
            "velocity":     velocity,
            "cv":           _r(cv),
            "avg_weekly_qty": _r(statistics.mean(weekly_by_pid[pid])) if weekly_by_pid.get(pid) else 0,
        })

    products.sort(key=lambda p: p["revenue"], reverse=True)

    return {
        "lookback_days":   lookback_days,
        "total_products":  len(products),
        "total_revenue":   _r(total_rev),
        "segment_summary": dict(seg_counts),
        "velocity_counts": {
            "fast_mover":    sum(1 for p in products if p["velocity"] == "fast_mover"),
            "moderate_mover":sum(1 for p in products if p["velocity"] == "moderate_mover"),
            "slow_mover":    sum(1 for p in products if p["velocity"] == "slow_mover"),
            "slow_erratic":  sum(1 for p in products if p["velocity"] == "slow_erratic"),
            "fast_erratic":  sum(1 for p in products if p["velocity"] == "fast_erratic"),
        },
        "products": products,
    }


# ── 3. INVENTORY RISK SCORING ─────────────────────────────────────────────────

def score_inventory_risks(db: Session, lookback_days: int = 30) -> Dict[str, Any]:
    """
    Composite inventory risk score per product (0–100).

    Component scores (each 0–100):
      stockout_score     — how close to zero inventory (weighted by reorder_level)
      overstock_score    — inventory >> reorder_level with low recent demand
      obsolescence_score — no orders in 60+ days for non-zero stock
      velocity_risk      — erratic demand (high CV) driving unpredictable stockouts

    Overall risk = weighted average (40/20/25/15).
    Risk tier: CRITICAL ≥ 75, HIGH ≥ 50, MEDIUM ≥ 25, LOW < 25.
    """
    start, end = _window(lookback_days)

    # Inventory state
    inv_rows = (
        db.query(
            Inventory.product_id,
            func.sum(Inventory.quantity_available).label("qty_avail"),
            func.sum(Inventory.quantity_reserved).label("qty_res"),
        )
        .group_by(Inventory.product_id)
        .all()
    )

    # Recent demand per product
    demand_rows = (
        db.query(
            Order.product_id,
            func.sum(Order.quantity).label("total_qty"),
            func.count(Order.id).label("order_count"),
        )
        .filter(Order.order_placed_at.between(start, end))
        .group_by(Order.product_id)
        .all()
    )
    demand_map = {r.product_id: (float(r.total_qty or 0), int(r.order_count or 0))
                  for r in demand_rows}

    # Last order date per product (for obsolescence)
    last_order_rows = (
        db.query(
            Order.product_id,
            func.max(Order.order_placed_at).label("last_order"),
        )
        .group_by(Order.product_id)
        .all()
    )
    last_order_map = {r.product_id: r.last_order for r in last_order_rows}

    # Weekly demand CV
    weekly_rows = (
        db.query(
            Order.product_id,
            func.strftime("%Y-%W", Order.order_placed_at).label("week"),
            func.sum(Order.quantity).label("qty"),
        )
        .filter(Order.order_placed_at.between(_now() - timedelta(days=90), _now()))
        .group_by(Order.product_id, func.strftime("%Y-%W", Order.order_placed_at))
        .all()
    )
    weekly_by_pid: Dict[int, List[float]] = defaultdict(list)
    for pid, _, qty in weekly_rows:
        weekly_by_pid[pid].append(float(qty or 0))

    # Product info
    products = db.query(Product).all()
    product_map = {p.id: p for p in products}

    now = _now()
    results = []

    for inv in inv_rows:
        pid = inv.product_id
        product = product_map.get(pid)
        if not product:
            continue

        qty_avail  = float(inv.qty_avail or 0)
        qty_res    = float(inv.qty_res   or 0)
        net_qty    = max(0.0, qty_avail - qty_res)
        reorder_lv = float(product.reorder_level or 10)

        total_demand, order_count = demand_map.get(pid, (0.0, 0))
        avg_daily_demand = total_demand / lookback_days if lookback_days else 0.0
        days_on_hand = net_qty / avg_daily_demand if avg_daily_demand > 0 else 9999

        last_order = last_order_map.get(pid)
        days_since_last_order = (
            (now - last_order.replace(tzinfo=timezone.utc)
             if last_order and last_order.tzinfo is None
             else now - last_order).days
        ) if last_order else 9999

        # ── Component scores ──────────────────────────────────────────────────
        # Stockout risk: 100 when qty ≤ 0; decreasing as qty grows past reorder_level
        if net_qty <= 0:
            stockout_score = 100.0
        elif net_qty <= reorder_lv:
            stockout_score = _r(100 * (1 - net_qty / reorder_lv))
        else:
            stockout_score = max(0.0, _r(100 * (1 - (net_qty - reorder_lv) / reorder_lv)))
            stockout_score = min(20.0, stockout_score)  # cap at 20 once above reorder

        # Overstock risk: high when qty >> 5× reorder_level but demand is low
        if net_qty > 5 * reorder_lv and avg_daily_demand < 1:
            overstock_score = min(100.0, _r((net_qty / (5 * reorder_lv)) * 50))
        elif net_qty > 3 * reorder_lv:
            overstock_score = min(60.0, _r((net_qty / (3 * reorder_lv)) * 30))
        else:
            overstock_score = 0.0

        # Obsolescence: 100 when stock > 0 but no order in 90+ days
        if net_qty > 0 and days_since_last_order >= 90:
            obsolescence_score = min(100.0, _r(days_since_last_order / 90 * 50))
        elif net_qty > 0 and days_since_last_order >= 60:
            obsolescence_score = 30.0
        else:
            obsolescence_score = 0.0

        # Velocity risk: high CV = unpredictable demand driving surprise stockouts
        cv = _cv(weekly_by_pid.get(pid, []))
        velocity_risk_score = min(100.0, _r(cv * 50))

        # ── Composite score ───────────────────────────────────────────────────
        composite = (
            stockout_score     * 0.40 +
            overstock_score    * 0.20 +
            obsolescence_score * 0.25 +
            velocity_risk_score * 0.15
        )
        composite = _r(composite)

        if composite >= 75:
            risk_tier = "CRITICAL"
        elif composite >= 50:
            risk_tier = "HIGH"
        elif composite >= 25:
            risk_tier = "MEDIUM"
        else:
            risk_tier = "LOW"

        # Dominant risk factor
        scores = {
            "stockout":     stockout_score,
            "overstock":    overstock_score,
            "obsolescence": obsolescence_score,
            "velocity":     velocity_risk_score,
        }
        dominant = max(scores, key=scores.get)

        results.append({
            "product_id":        pid,
            "product_name":      product.product_name,
            "sku":               product.sku,
            "category":          product.category,
            "risk_score":        composite,
            "risk_tier":         risk_tier,
            "dominant_risk":     dominant,
            "component_scores":  {k: _r(v) for k, v in scores.items()},
            "net_quantity":      _r(net_qty),
            "reorder_level":     _r(reorder_lv),
            "days_on_hand":      _r(min(days_on_hand, 999)),
            "avg_daily_demand":  _r(avg_daily_demand),
            "days_since_last_order": min(days_since_last_order, 9999),
        })

    results.sort(key=lambda x: x["risk_score"], reverse=True)

    tier_counts = defaultdict(int)
    for r in results:
        tier_counts[r["risk_tier"]] += 1

    return {
        "lookback_days":  lookback_days,
        "total_products": len(results),
        "tier_summary":   dict(tier_counts),
        "products":       results,
    }


# ── 4. SUPPLIER PERFORMANCE INTELLIGENCE ─────────────────────────────────────

def analyze_supplier_performance(
    db: Session,
    lookback_days: int = 90,
) -> Dict[str, Any]:
    """
    Per-supplier performance scorecard with trend analysis.

    Metrics:
      - avg_procurement_time  (hours)
      - lead_time_std         (reliability — lower is better)
      - on_time_rate          (orders delivered without SLA breach)
      - sla_breach_rate
      - order_volume          (total orders in window)
      - total_revenue
      - avg_unit_price        (cost trend)
      - performance_score     (composite 0–100, higher = better)
      - trend                 ("improving" | "declining" | "stable")
    """
    start, end = _window(lookback_days)
    mid = start + (end - start) / 2  # split window for trend

    def _supplier_stats(s, e):
        return (
            db.query(
                Order.supplier_id,
                func.count(Order.id).label("orders"),
                func.avg(Order.procurement_time).label("avg_proc"),
                func.coalesce(
                    func.avg(
                        case(
                            (Order.procurement_time.isnot(None), Order.procurement_time),
                            else_=None,
                        )
                    ), 0.0
                ).label("proc_mean"),
                func.sum(Order.total_amount).label("revenue"),
                func.sum(
                    case((Order.sla_breach == True, 1), else_=0)
                ).label("breaches"),
                func.avg(Order.unit_price).label("avg_price"),
            )
            .filter(
                Order.supplier_id.isnot(None),
                Order.order_placed_at.between(s, e),
            )
            .group_by(Order.supplier_id)
            .all()
        )

    cur_stats  = {r.supplier_id: r for r in _supplier_stats(start, end)}
    prev_stats = {r.supplier_id: r for r in _supplier_stats(
        start - timedelta(days=lookback_days), start
    )}

    # Procurement-time std-dev per supplier (for reliability)
    proc_times = (
        db.query(Order.supplier_id, Order.procurement_time)
        .filter(
            Order.supplier_id.isnot(None),
            Order.procurement_time.isnot(None),
            Order.order_placed_at.between(start, end),
        )
        .all()
    )
    proc_by_supplier: Dict[int, List[float]] = defaultdict(list)
    for sid, pt in proc_times:
        proc_by_supplier[sid].append(float(pt))

    suppliers = db.query(Supplier).all()
    sup_map   = {s.id: s for s in suppliers}

    results = []
    for sid, cur in cur_stats.items():
        supplier = sup_map.get(sid)
        if not supplier:
            continue

        orders   = int(cur.orders or 0)
        breaches = int(cur.breaches or 0)
        revenue  = _r(cur.revenue or 0)
        avg_proc = _r(cur.avg_proc or 0)
        avg_price= _r(cur.avg_price or 0)

        proc_list = proc_by_supplier.get(sid, [])
        proc_std  = _r(statistics.stdev(proc_list)) if len(proc_list) > 1 else 0.0

        on_time_rate  = _pct(orders - breaches, orders)
        sla_breach_rt = _pct(breaches, orders)

        # Performance score (0–100): on-time rate weighted more heavily
        perf_score = _r(
            on_time_rate * 0.50
            + max(0, (1 - proc_std / max(avg_proc, 1)) * 100) * 0.25
            + min(100, (supplier.rating or 3) / 5 * 100) * 0.25
        )

        # Trend: compare on-time rate vs prior period
        prev = prev_stats.get(sid)
        if prev and prev.orders:
            prev_orders   = int(prev.orders)
            prev_breaches = int(prev.breaches or 0)
            prev_otr = _pct(prev_orders - prev_breaches, prev_orders)
            delta = on_time_rate - prev_otr
            trend = "improving" if delta > 5 else "declining" if delta < -5 else "stable"
        else:
            trend = "stable"

        results.append({
            "supplier_id":         sid,
            "supplier_name":       supplier.supplier_name,
            "city":                supplier.city,
            "country":             supplier.country,
            "supplier_rating":     supplier.rating,
            "order_volume":        orders,
            "total_revenue":       revenue,
            "avg_procurement_hours": avg_proc,
            "procurement_std_hours": proc_std,
            "on_time_rate_pct":    on_time_rate,
            "sla_breach_rate_pct": sla_breach_rt,
            "avg_unit_price":      avg_price,
            "performance_score":   perf_score,
            "trend":               trend,
            "recommendation":      (
                "Preferred supplier — maintain relationship"
                if perf_score >= 80 else
                "Monitor — performance has room for improvement"
                if perf_score >= 60 else
                "At risk — consider alternative suppliers"
            ),
        })

    results.sort(key=lambda x: x["performance_score"], reverse=True)

    return {
        "lookback_days":   lookback_days,
        "total_suppliers": len(results),
        "avg_performance": _r(statistics.mean([r["performance_score"] for r in results])) if results else 0,
        "top_performer":   results[0]["supplier_name"] if results else None,
        "worst_performer": results[-1]["supplier_name"] if results else None,
        "suppliers":       results,
    }


# ── 5. REVENUE OPPORTUNITY ANALYSIS ──────────────────────────────────────────

def identify_revenue_opportunities(
    db: Session,
    lookback_days: int = 30,
) -> Dict[str, Any]:
    """
    Identify revenue leakage and uplift opportunities.

    Categories:
      stockout_losses     — high-demand products with zero/critical stock
                            (estimated lost revenue = avg_daily_demand × days_at_zero × unit_price)
      underperforming_cat — categories with declining revenue trend
      overpriced_slow     — slow-movers with unit_price > category median
      high_demand_gap     — products where demand consistently outstrips supply
      quick_win_restock   — items with pending demand but low stock + supplier available

    Returns total_opportunity_value and ranked opportunity list.
    """
    start, end = _window(lookback_days)
    prev_start = start - timedelta(days=lookback_days)

    # Demand per product
    demand_rows = (
        db.query(
            Order.product_id,
            func.sum(Order.quantity).label("total_qty"),
            func.avg(Order.unit_price).label("avg_price"),
        )
        .filter(Order.order_placed_at.between(start, end))
        .group_by(Order.product_id)
        .all()
    )
    demand_map = {r.product_id: (float(r.total_qty or 0), float(r.avg_price or 0))
                  for r in demand_rows}

    # Current inventory
    inv_rows = (
        db.query(Inventory.product_id,
                 func.sum(Inventory.quantity_available).label("qty"))
        .group_by(Inventory.product_id)
        .all()
    )
    inv_map = {r.product_id: float(r.qty or 0) for r in inv_rows}

    # Product info
    products = db.query(Product).all()

    # Category revenue: current vs prior period
    cat_cur = (
        db.query(Product.category, func.sum(Order.total_amount).label("rev"))
        .join(Order, Order.product_id == Product.id)
        .filter(Order.order_placed_at.between(start, end))
        .group_by(Product.category)
        .all()
    )
    cat_prev = (
        db.query(Product.category, func.sum(Order.total_amount).label("rev"))
        .join(Order, Order.product_id == Product.id)
        .filter(Order.order_placed_at.between(prev_start, start))
        .group_by(Product.category)
        .all()
    )
    cur_rev_by_cat  = {r.category: float(r.rev or 0) for r in cat_cur}
    prev_rev_by_cat = {r.category: float(r.rev or 0) for r in cat_prev}

    # Category median unit price
    cat_prices: Dict[str, List[float]] = defaultdict(list)
    for p in products:
        cat_prices[p.category or "Uncategorized"].append(p.unit_price or 0)
    cat_median = {cat: statistics.median(prices) for cat, prices in cat_prices.items() if prices}

    opportunities: List[Dict] = []
    total_opportunity = 0.0

    for product in products:
        pid = product.id
        total_qty, avg_price = demand_map.get(pid, (0.0, product.unit_price or 0))
        avg_daily_demand = total_qty / lookback_days
        net_stock = inv_map.get(pid, 0.0)
        reorder_lv = float(product.reorder_level or 10)

        # --- Stockout loss opportunity ---
        if net_stock <= 0 and avg_daily_demand > 0.5:
            # Estimate days product was out of stock (proxy: days with 0 inv)
            est_lost_days = min(lookback_days * 0.3, 10)  # conservative 30 % estimate
            lost_rev = _r(avg_daily_demand * est_lost_days * avg_price)
            if lost_rev > 0:
                opportunities.append({
                    "type":          "stockout_loss",
                    "product_id":    pid,
                    "product_name":  product.product_name,
                    "sku":           product.sku,
                    "category":      product.category,
                    "opportunity_value": lost_rev,
                    "description":   (
                        f"Product is out of stock. Estimated {est_lost_days:.0f} days of "
                        f"lost demand @ {avg_daily_demand:.1f} units/day = ${lost_rev:,.0f} revenue at risk."
                    ),
                    "action":        "RESTOCK_IMMEDIATELY",
                    "priority":      "CRITICAL",
                })
                total_opportunity += lost_rev

        # --- High-demand gap (demand > 2× average stock replenishment) ---
        elif net_stock < reorder_lv * 0.5 and avg_daily_demand > 1.0:
            gap_rev = _r(avg_daily_demand * 7 * avg_price)  # 7-day gap value
            opportunities.append({
                "type":          "demand_supply_gap",
                "product_id":    pid,
                "product_name":  product.product_name,
                "sku":           product.sku,
                "category":      product.category,
                "opportunity_value": gap_rev,
                "description":   (
                    f"Stock critically low ({net_stock:.0f} units) against avg demand of "
                    f"{avg_daily_demand:.1f}/day. 7-day revenue at risk: ${gap_rev:,.0f}."
                ),
                "action":        "EXPEDITE_REORDER",
                "priority":      "HIGH",
            })
            total_opportunity += gap_rev

        # --- Overpriced slow-mover markdown opportunity ---
        cat_med = cat_median.get(product.category or "Uncategorized", 0)
        if (product.unit_price or 0) > cat_med * 1.3 and avg_daily_demand < 0.2 and net_stock > reorder_lv * 2:
            markdown_rev = _r(net_stock * product.unit_price * 0.15)  # 15 % uplift from markdown
            opportunities.append({
                "type":          "markdown_opportunity",
                "product_id":    pid,
                "product_name":  product.product_name,
                "sku":           product.sku,
                "category":      product.category,
                "opportunity_value": markdown_rev,
                "description":   (
                    f"Unit price ${product.unit_price:.2f} is {_pct(product.unit_price - cat_med, cat_med):.0f}% "
                    f"above category median ${cat_med:.2f}. A 15% markdown on {net_stock:.0f} units "
                    f"could unlock ${markdown_rev:,.0f} in stalled inventory."
                ),
                "action":        "CONSIDER_MARKDOWN",
                "priority":      "MEDIUM",
            })
            total_opportunity += markdown_rev * 0.5   # haircut — not all will sell

    # --- Declining category opportunities ---
    for cat, cur_rev in cur_rev_by_cat.items():
        prev_rev = prev_rev_by_cat.get(cat, 0)
        if prev_rev > 0:
            decline = _pct(prev_rev - cur_rev, prev_rev)
            if decline > 20:  # >20 % revenue decline in category
                opportunities.append({
                    "type":          "category_decline",
                    "product_id":    None,
                    "product_name":  None,
                    "sku":           None,
                    "category":      cat,
                    "opportunity_value": _r(prev_rev - cur_rev),
                    "description":   (
                        f"Category '{cat}' revenue declined {decline:.1f}% period-over-period "
                        f"(${cur_rev:,.0f} vs ${prev_rev:,.0f}). Review pricing, stock levels, and demand drivers."
                    ),
                    "action":        "INVESTIGATE_CATEGORY",
                    "priority":      "HIGH",
                })
                total_opportunity += (prev_rev - cur_rev) * 0.3

    opportunities.sort(key=lambda o: o["opportunity_value"], reverse=True)

    priority_counts = defaultdict(int)
    for o in opportunities:
        priority_counts[o["priority"]] += 1

    return {
        "lookback_days":         lookback_days,
        "total_opportunities":   len(opportunities),
        "total_opportunity_value": _r(total_opportunity),
        "priority_summary":      dict(priority_counts),
        "opportunities":         opportunities[:50],
    }


# ── 6. DEAD STOCK DETECTION ───────────────────────────────────────────────────

def detect_dead_stock(db: Session, days_threshold: int = 90) -> Dict[str, Any]:
    """
    Identify inventory items with zero demand for >= *days_threshold* days.

    Returns products with non-zero stock that have had no orders in the
    threshold window, together with carrying-cost estimate (2 % of value/month).
    """
    cutoff = _now() - timedelta(days=days_threshold)

    # Products with recent orders
    active_pids = set(
        r[0] for r in db.query(distinct(Order.product_id))
        .filter(Order.order_placed_at >= cutoff)
        .all()
    )

    # Inventory rows with stock
    inv_rows = (
        db.query(
            Inventory.product_id,
            func.sum(Inventory.quantity_available).label("qty"),
            func.max(Inventory.last_restocked).label("last_restock"),
        )
        .filter(Inventory.quantity_available > 0)
        .group_by(Inventory.product_id)
        .all()
    )

    product_map = {p.id: p for p in db.query(Product).all()}
    now = _now()
    dead_stock = []

    for inv in inv_rows:
        pid = inv.product_id
        if pid in active_pids:
            continue
        product = product_map.get(pid)
        if not product:
            continue

        qty = float(inv.qty or 0)
        book_value = qty * (product.unit_price or 0)
        # Carrying cost: 2 % of book value per month
        carrying_cost_monthly = _r(book_value * 0.02)

        # Days since last restock
        last_restock = inv.last_restock
        if last_restock:
            ts = last_restock if last_restock.tzinfo else last_restock.replace(tzinfo=timezone.utc)
            days_idle = (now - ts).days
        else:
            days_idle = days_threshold

        dead_stock.append({
            "product_id":             pid,
            "product_name":           product.product_name,
            "sku":                    product.sku,
            "category":               product.category,
            "quantity_on_hand":       _r(qty),
            "unit_price":             _r(product.unit_price or 0),
            "book_value":             _r(book_value),
            "carrying_cost_monthly":  carrying_cost_monthly,
            "days_idle":              days_idle,
            "recommendation":         (
                "Liquidate or discount" if days_idle > 180 else
                "Review & promote"      if days_idle > 90  else
                "Monitor"
            ),
        })

    dead_stock.sort(key=lambda d: d["book_value"], reverse=True)
    total_value  = _r(sum(d["book_value"] for d in dead_stock))
    total_carry  = _r(sum(d["carrying_cost_monthly"] for d in dead_stock))

    return {
        "days_threshold":            days_threshold,
        "total_dead_stock_items":    len(dead_stock),
        "total_book_value":          total_value,
        "total_monthly_carry_cost":  total_carry,
        "items":                     dead_stock,
    }


# ── 7. DEMAND SEASONALITY PATTERNS ───────────────────────────────────────────

def analyze_demand_patterns(db: Session, lookback_days: int = 365) -> Dict[str, Any]:
    """
    Day-of-week and monthly demand patterns.

    Returns:
      - by_day_of_week: avg order volume per weekday (0=Mon … 6=Sun)
      - by_month: avg daily order volume per calendar month
      - peak_day: day name with highest avg demand
      - peak_month: month name with highest avg demand
      - category_seasonality: top-selling category per month
    """
    start, end = _window(lookback_days)

    # Day-of-week pattern
    dow_rows = (
        db.query(
            func.strftime("%w", Order.order_placed_at).label("dow"),
            func.count(Order.id).label("orders"),
            func.sum(Order.quantity).label("qty"),
        )
        .filter(Order.order_placed_at.between(start, end))
        .group_by(func.strftime("%w", Order.order_placed_at))
        .all()
    )

    day_names = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]
    dow_data  = []
    for row in sorted(dow_rows, key=lambda r: int(r.dow or 0)):
        dow_data.append({
            "day_index":   int(row.dow or 0),
            "day_name":    day_names[int(row.dow or 0)],
            "order_count": int(row.orders or 0),
            "total_qty":   int(row.qty    or 0),
        })

    peak_day = max(dow_data, key=lambda d: d["order_count"])["day_name"] if dow_data else None

    # Monthly pattern
    month_rows = (
        db.query(
            func.strftime("%Y-%m", Order.order_placed_at).label("ym"),
            func.count(Order.id).label("orders"),
            func.sum(Order.quantity).label("qty"),
            func.sum(Order.total_amount).label("revenue"),
        )
        .filter(Order.order_placed_at.between(start, end))
        .group_by(func.strftime("%Y-%m", Order.order_placed_at))
        .order_by(func.strftime("%Y-%m", Order.order_placed_at))
        .all()
    )

    monthly_data = [
        {
            "year_month":  row.ym,
            "order_count": int(row.orders  or 0),
            "total_qty":   int(row.qty     or 0),
            "revenue":     _r(row.revenue  or 0),
        }
        for row in month_rows
    ]

    peak_month = max(monthly_data, key=lambda m: m["revenue"])["year_month"] if monthly_data else None

    # Top category per month
    cat_month_rows = (
        db.query(
            func.strftime("%Y-%m", Order.order_placed_at).label("ym"),
            Product.category,
            func.sum(Order.total_amount).label("revenue"),
        )
        .join(Product, Product.id == Order.product_id)
        .filter(Order.order_placed_at.between(start, end))
        .group_by(func.strftime("%Y-%m", Order.order_placed_at), Product.category)
        .all()
    )

    month_cat: Dict[str, Dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for ym, cat, rev in cat_month_rows:
        month_cat[ym][cat or "Uncategorized"] += float(rev or 0)

    top_cat_by_month = {
        ym: max(cats, key=cats.get)
        for ym, cats in month_cat.items()
    }

    return {
        "lookback_days":       lookback_days,
        "by_day_of_week":      dow_data,
        "by_month":            monthly_data,
        "peak_day":            peak_day,
        "peak_month":          peak_month,
        "top_category_by_month": top_cat_by_month,
    }
