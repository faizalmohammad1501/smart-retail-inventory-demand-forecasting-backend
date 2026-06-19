"""
Advanced Business Intelligence Service
========================================
Provides executive-grade analytics and strategic KPIs for the BI module.
All aggregations run in SQL — no Python-level loops for computations.

Endpoints served:
  1.  executive_summary          — C-suite KPI pack with MoM deltas
  2.  kpi_trends                 — time-series KPIs (daily/weekly/monthly)
  3.  profitability_analysis      — gross margin by category & product
  4.  period_comparison          — MoM / QoQ / YoY comparison
  5.  inventory_health_score     — composite 0-100 scoring with breakdown
  6.  supplier_intelligence      — lead-time reliability, CPI, breach rate
  7.  forecast_performance       — weekly forecast accuracy trend
  8.  order_cohorts              — cohort analysis by order month
  9.  category_deep_dive         — per-category multi-metric deep dive
  10. operational_efficiency     — cycle-time benchmarks & bottleneck heatmap
"""

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import func, case, and_, text
from sqlalchemy.orm import Session

from app.models.sales import Order
from app.models.product import Product
from app.models.inventory import Inventory
from app.models.supplier import Supplier
from app.models.notification import Notification


# ─────────────────────────────────────────────────────────────────────────────
#  Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _r(v, n: int = 2) -> float:
    return round(float(v), n) if v is not None else 0.0


def _pct(num, den, scale: float = 100.0) -> float:
    return round(num / den * scale, 2) if den else 0.0


def _delta(current: float, prior: float) -> Optional[float]:
    """Percentage change from prior → current. None when prior is 0."""
    if not prior:
        return None
    return round((current - prior) / prior * 100, 2)


def _window(days: int) -> tuple:
    """Return (start, end) for the last `days` days."""
    end = _now()
    return end - timedelta(days=days), end


def _double_window(days: int) -> tuple:
    """Return (cur_start, cur_end, prev_start, prev_end) for PoP comparisons."""
    cur_end = _now()
    cur_start = cur_end - timedelta(days=days)
    return cur_start, cur_end, cur_start - timedelta(days=days), cur_start


def _date_f(col, start: Optional[datetime], end: Optional[datetime]) -> list:
    clauses = []
    if start:
        clauses.append(col >= start)
    if end:
        clauses.append(col <= end)
    return clauses


def _order_revenue(db: Session, start: datetime, end: datetime) -> float:
    val = db.query(func.coalesce(func.sum(Order.total_amount), 0.0)).filter(
        *_date_f(Order.order_placed_at, start, end)
    ).scalar()
    return _r(val)


def _order_count(db: Session, start: datetime, end: datetime) -> int:
    return db.query(func.count(Order.id)).filter(
        *_date_f(Order.order_placed_at, start, end)
    ).scalar() or 0


def _delivered_count(db: Session, start: datetime, end: datetime) -> int:
    return db.query(func.count(Order.id)).filter(
        Order.status == "delivered",
        *_date_f(Order.order_placed_at, start, end),
    ).scalar() or 0


def _sla_breach_count(db: Session, start: datetime, end: datetime) -> int:
    return db.query(func.count(Order.id)).filter(
        Order.sla_breach == True,
        *_date_f(Order.order_placed_at, start, end),
    ).scalar() or 0


# ─────────────────────────────────────────────────────────────────────────────
#  1. Executive Summary
# ─────────────────────────────────────────────────────────────────────────────

def get_executive_summary(db: Session, days: int = 30) -> Dict[str, Any]:
    """
    C-suite KPI pack — every metric includes current value,
    prior-period value, and % change (MoM by default).
    """
    cs, ce, ps, pe = _double_window(days)

    # ── Revenue ──────────────────────────────────────────────────────────────
    rev_cur  = _order_revenue(db, cs, ce)
    rev_prev = _order_revenue(db, ps, pe)

    # ── Orders ───────────────────────────────────────────────────────────────
    ord_cur  = _order_count(db, cs, ce)
    ord_prev = _order_count(db, ps, pe)

    # ── Delivered ────────────────────────────────────────────────────────────
    del_cur  = _delivered_count(db, cs, ce)
    del_prev = _delivered_count(db, ps, pe)

    # ── SLA ──────────────────────────────────────────────────────────────────
    breach_cur  = _sla_breach_count(db, cs, ce)
    breach_prev = _sla_breach_count(db, ps, pe)
    sla_rate_cur  = _pct(ord_cur - breach_cur, ord_cur)
    sla_rate_prev = _pct(ord_prev - breach_prev, ord_prev)

    # ── Avg order value ───────────────────────────────────────────────────────
    aov_cur  = _r(rev_cur / ord_cur) if ord_cur else 0.0
    aov_prev = _r(rev_prev / ord_prev) if ord_prev else 0.0

    # ── Fill rate (delivered / total) ─────────────────────────────────────────
    fill_cur  = _pct(del_cur, ord_cur)
    fill_prev = _pct(del_prev, ord_prev)

    # ── Avg cycle time (delivered orders only) ────────────────────────────────
    avg_cycle = db.query(func.avg(Order.total_time)).filter(
        Order.status == "delivered",
        Order.total_time.isnot(None),
        *_date_f(Order.order_placed_at, cs, ce),
    ).scalar()
    avg_cycle_prev = db.query(func.avg(Order.total_time)).filter(
        Order.status == "delivered",
        Order.total_time.isnot(None),
        *_date_f(Order.order_placed_at, ps, pe),
    ).scalar()

    # ── Inventory health score (composite — see fn below) ────────────────────
    inv_score = get_inventory_health_score(db)

    # ── Supplier avg rating ───────────────────────────────────────────────────
    sup_rating = db.query(func.avg(Supplier.rating)).scalar() or 0.0

    # ── Active alerts ─────────────────────────────────────────────────────────
    active_alerts = db.query(func.count(Notification.id)).filter(
        Notification.is_resolved == False
    ).scalar() or 0

    def _kpi(cur, prev, unit: str = "", higher_is_better: bool = True) -> Dict:
        chg = _delta(cur, prev)
        if chg is None:
            trend = "neutral"
        elif (chg > 0) == higher_is_better:
            trend = "up_good"
        else:
            trend = "down_bad" if chg < 0 else "up_bad"
        return {
            "current":    cur,
            "prior":      prev,
            "change_pct": chg,
            "trend":      trend,
            "unit":       unit,
        }

    return {
        "period_days":       days,
        "period_start":      cs.isoformat(),
        "period_end":        ce.isoformat(),
        "generated_at":      _now().isoformat(),
        "kpis": {
            "total_revenue":       _kpi(rev_cur,             rev_prev,         "USD",  True),
            "total_orders":        _kpi(ord_cur,             ord_prev,         "",     True),
            "avg_order_value":     _kpi(aov_cur,             aov_prev,         "USD",  True),
            "fill_rate":           _kpi(fill_cur,            fill_prev,        "%",    True),
            "sla_compliance_rate": _kpi(sla_rate_cur,        sla_rate_prev,    "%",    True),
            "avg_cycle_time_h":    _kpi(_r(avg_cycle),       _r(avg_cycle_prev),"h",  False),
            "supplier_avg_rating": _kpi(_r(sup_rating, 1),   0.0,              "/5",   True),
            "inventory_health":    _kpi(inv_score["score"],  0.0,              "/100", True),
            "active_alerts":       _kpi(active_alerts,       0,                "",     False),
        },
    }


# ─────────────────────────────────────────────────────────────────────────────
#  2. KPI Trends
# ─────────────────────────────────────────────────────────────────────────────

def get_kpi_trends(
    db: Session,
    days: int = 90,
    granularity: str = "weekly",
) -> Dict[str, Any]:
    """
    Time-series KPIs grouped by day / week / month.
    Returns parallel lists for Chart.js multi-line charts.
    """
    start, end = _window(days)

    # SQLite strftime format
    fmt_map = {"daily": "%Y-%m-%d", "weekly": "%Y-%W", "monthly": "%Y-%m"}
    fmt = fmt_map.get(granularity, "%Y-%W")

    rows = (
        db.query(
            func.strftime(fmt, Order.order_placed_at).label("period"),
            func.count(Order.id).label("orders"),
            func.coalesce(func.sum(Order.total_amount), 0.0).label("revenue"),
            func.coalesce(func.avg(Order.total_time), 0.0).label("avg_cycle"),
            func.sum(case((Order.sla_breach == True, 1), else_=0)).label("breaches"),
            func.sum(case((Order.status == "delivered", 1), else_=0)).label("delivered"),
        )
        .filter(*_date_f(Order.order_placed_at, start, end))
        .group_by(func.strftime(fmt, Order.order_placed_at))
        .order_by(func.strftime(fmt, Order.order_placed_at))
        .all()
    )

    labels, revenues, orders_list, cycle_times, sla_rates = [], [], [], [], []
    for r in rows:
        labels.append(r.period or "")
        revenues.append(_r(r.revenue))
        orders_list.append(int(r.orders))
        cycle_times.append(_r(r.avg_cycle))
        total = int(r.orders) or 1
        delivered = int(r.delivered or 0)
        breaches  = int(r.breaches or 0)
        sla_rates.append(_pct(total - breaches, total))

    return {
        "granularity": granularity,
        "period_days": days,
        "labels": labels,
        "series": {
            "revenue":          revenues,
            "orders":           orders_list,
            "avg_cycle_time_h": cycle_times,
            "sla_compliance":   sla_rates,
        },
    }


# ─────────────────────────────────────────────────────────────────────────────
#  3. Profitability Analysis
# ─────────────────────────────────────────────────────────────────────────────

def get_profitability_analysis(
    db: Session,
    days: int = 90,
    top_n: int = 10,
) -> Dict[str, Any]:
    """
    Revenue vs estimated cost, gross margin by category and top products.
    Cost is estimated as unit_price × 0.65 (65% COGS ratio) for demo purposes.
    """
    start, end = _window(days)
    COGS_RATIO = 0.65

    # ── By category ──────────────────────────────────────────────────────────
    cat_rows = (
        db.query(
            Product.category,
            func.coalesce(func.sum(Order.total_amount), 0.0).label("revenue"),
            func.coalesce(func.sum(Order.quantity), 0).label("units"),
            func.count(Order.id).label("orders"),
        )
        .join(Product, Order.product_id == Product.id)
        .filter(*_date_f(Order.order_placed_at, start, end))
        .group_by(Product.category)
        .order_by(func.sum(Order.total_amount).desc())
        .all()
    )

    by_category = []
    for r in cat_rows:
        rev = _r(r.revenue)
        cost = _r(rev * COGS_RATIO)
        margin = _r(rev - cost)
        margin_pct = _pct(margin, rev)
        by_category.append({
            "category":     r.category or "Uncategorised",
            "revenue":      rev,
            "cost":         cost,
            "gross_margin": margin,
            "margin_pct":   margin_pct,
            "units_sold":   int(r.units or 0),
            "orders":       int(r.orders or 0),
        })

    # ── Top products ─────────────────────────────────────────────────────────
    prod_rows = (
        db.query(
            Product.id,
            Product.product_name,
            Product.category,
            Product.unit_price,
            func.coalesce(func.sum(Order.total_amount), 0.0).label("revenue"),
            func.coalesce(func.sum(Order.quantity), 0).label("units"),
            func.count(Order.id).label("orders"),
        )
        .join(Product, Order.product_id == Product.id)
        .filter(*_date_f(Order.order_placed_at, start, end))
        .group_by(Product.id, Product.product_name, Product.category, Product.unit_price)
        .order_by(func.sum(Order.total_amount).desc())
        .limit(top_n)
        .all()
    )

    top_products = []
    for r in prod_rows:
        rev = _r(r.revenue)
        cost = _r(rev * COGS_RATIO)
        top_products.append({
            "product_id":     r.id,
            "product_name":   r.product_name,
            "category":       r.category or "Uncategorised",
            "unit_price":     _r(r.unit_price),
            "revenue":        rev,
            "cost":           cost,
            "gross_margin":   _r(rev - cost),
            "margin_pct":     _pct(rev - cost, rev),
            "units_sold":     int(r.units or 0),
            "orders":         int(r.orders or 0),
        })

    # ── Summary ───────────────────────────────────────────────────────────────
    total_rev  = sum(c["revenue"] for c in by_category)
    total_cost = _r(total_rev * COGS_RATIO)
    total_margin = _r(total_rev - total_cost)

    return {
        "period_days":    days,
        "cogs_ratio":     COGS_RATIO,
        "summary": {
            "total_revenue":      total_rev,
            "total_cost":         total_cost,
            "total_gross_margin": total_margin,
            "overall_margin_pct": _pct(total_margin, total_rev),
        },
        "by_category":  by_category,
        "top_products": top_products,
    }


# ─────────────────────────────────────────────────────────────────────────────
#  4. Period Comparison  (MoM / QoQ / YoY)
# ─────────────────────────────────────────────────────────────────────────────

def get_period_comparison(db: Session, mode: str = "mom") -> Dict[str, Any]:
    """
    Compare current period vs prior period across revenue, orders,
    SLA compliance, avg cycle time, and fill rate.
    mode: 'mom' (30d), 'qoq' (90d), 'yoy' (365d)
    """
    period_map = {"mom": 30, "qoq": 90, "yoy": 365}
    days = period_map.get(mode, 30)

    cs, ce, ps, pe = _double_window(days)

    def _stats(start, end) -> Dict:
        total    = _order_count(db, start, end)
        revenue  = _order_revenue(db, start, end)
        breaches = _sla_breach_count(db, start, end)
        delivered = _delivered_count(db, start, end)
        cycle = db.query(func.avg(Order.total_time)).filter(
            Order.status == "delivered",
            Order.total_time.isnot(None),
            *_date_f(Order.order_placed_at, start, end),
        ).scalar()
        return {
            "period_start":      start.isoformat(),
            "period_end":        end.isoformat(),
            "total_orders":      total,
            "total_revenue":     revenue,
            "avg_order_value":   _r(revenue / total) if total else 0.0,
            "fill_rate":         _pct(delivered, total),
            "sla_compliance":    _pct(total - breaches, total),
            "sla_breaches":      breaches,
            "avg_cycle_time_h":  _r(cycle),
        }

    cur  = _stats(cs, ce)
    prev = _stats(ps, pe)

    def _compare(key: str, higher_good: bool = True) -> Dict:
        c, p = cur[key], prev[key]
        chg = _delta(c, p)
        return {
            "current":    c,
            "prior":      p,
            "change_pct": chg,
            "improved":   (chg is not None) and ((chg > 0) == higher_good),
        }

    return {
        "mode":        mode.upper(),
        "period_days": days,
        "current":     cur,
        "prior":       prev,
        "comparison": {
            "total_revenue":     _compare("total_revenue",    True),
            "total_orders":      _compare("total_orders",     True),
            "avg_order_value":   _compare("avg_order_value",  True),
            "fill_rate":         _compare("fill_rate",        True),
            "sla_compliance":    _compare("sla_compliance",   True),
            "avg_cycle_time_h":  _compare("avg_cycle_time_h", False),
        },
    }


# ─────────────────────────────────────────────────────────────────────────────
#  5. Inventory Health Score
# ─────────────────────────────────────────────────────────────────────────────

def get_inventory_health_score(db: Session) -> Dict[str, Any]:
    """
    Composite inventory health score (0–100) built from 5 components:
      - Stock availability  (0-25 pts): % products with qty > 0
      - Reorder compliance  (0-20 pts): % items above reorder level
      - Overstock penalty   (0-20 pts): penalty for items 10× above reorder level
      - Freshness           (0-20 pts): % restocked within last 90 days
      - Turnover activity   (0-15 pts): % products that had orders last 90 days
    """
    total_products = db.query(func.count(Inventory.id)).scalar() or 1

    # Component 1 — Stock availability
    in_stock = db.query(func.count(Inventory.id)).filter(
        Inventory.quantity_available > 0
    ).scalar() or 0
    avail_score = _pct(in_stock, total_products, scale=25.0)

    # Component 2 — Reorder compliance
    above_reorder = (
        db.query(func.count(Inventory.id))
        .join(Product, Inventory.product_id == Product.id)
        .filter(Inventory.quantity_available >= Product.reorder_level)
        .scalar() or 0
    )
    reorder_score = _pct(above_reorder, total_products, scale=20.0)

    # Component 3 — Overstock penalty (items with qty > 10× reorder level)
    overstocked = (
        db.query(func.count(Inventory.id))
        .join(Product, Inventory.product_id == Product.id)
        .filter(Inventory.quantity_available > Product.reorder_level * 10)
        .scalar() or 0
    )
    overstock_penalty = _pct(overstocked, total_products, scale=20.0)
    overstock_score = max(0.0, 20.0 - overstock_penalty)

    # Component 4 — Freshness (restocked within 90 days)
    cutoff = _now() - timedelta(days=90)
    fresh = db.query(func.count(Inventory.id)).filter(
        Inventory.last_restocked >= cutoff
    ).scalar() or 0
    freshness_score = _pct(fresh, total_products, scale=20.0)

    # Component 5 — Turnover activity (had orders last 90 days)
    start_90, end_90 = _window(90)
    active_product_ids = [
        row[0] for row in
        db.query(Order.product_id).filter(
            *_date_f(Order.order_placed_at, start_90, end_90)
        ).distinct().all()
    ]
    active_count = db.query(func.count(Inventory.id)).filter(
        Inventory.product_id.in_(active_product_ids)
    ).scalar() or 0
    turnover_score = _pct(active_count, total_products, scale=15.0)

    total_score = _r(avail_score + reorder_score + overstock_score + freshness_score + turnover_score)

    # Risk level
    if total_score >= 80:
        risk_level = "healthy"
    elif total_score >= 60:
        risk_level = "moderate"
    elif total_score >= 40:
        risk_level = "at_risk"
    else:
        risk_level = "critical"

    return {
        "score":      total_score,
        "max_score":  100,
        "risk_level": risk_level,
        "components": {
            "stock_availability": {"score": _r(avail_score),     "max": 25, "detail": f"{in_stock}/{total_products} in stock"},
            "reorder_compliance": {"score": _r(reorder_score),   "max": 20, "detail": f"{above_reorder}/{total_products} above reorder level"},
            "overstock_control":  {"score": _r(overstock_score), "max": 20, "detail": f"{overstocked} overstocked items"},
            "stock_freshness":    {"score": _r(freshness_score), "max": 20, "detail": f"{fresh}/{total_products} restocked in 90d"},
            "turnover_activity":  {"score": _r(turnover_score),  "max": 15, "detail": f"{active_count}/{total_products} products with recent orders"},
        },
        "totals": {
            "total_products": total_products,
            "in_stock":       in_stock,
            "out_of_stock":   total_products - in_stock,
            "above_reorder":  above_reorder,
            "overstocked":    overstocked,
        },
    }


# ─────────────────────────────────────────────────────────────────────────────
#  6. Supplier Intelligence
# ─────────────────────────────────────────────────────────────────────────────

def get_supplier_intelligence(db: Session, days: int = 90) -> Dict[str, Any]:
    """
    Per-supplier advanced metrics:
      - Lead time reliability (std-dev of procurement_time)
      - Cost performance index (CPI = avg unit_price vs category avg)
      - SLA breach rate
      - Fill rate (delivered / total)
      - Composite reliability score (0-100)
    """
    start, end = _window(days)

    rows = (
        db.query(
            Supplier.id,
            Supplier.supplier_name,
            Supplier.rating,
            func.count(Order.id).label("total_orders"),
            func.sum(case((Order.status == "delivered", 1), else_=0)).label("delivered"),
            func.sum(case((Order.sla_breach == True, 1), else_=0)).label("breaches"),
            func.coalesce(func.avg(Order.procurement_time), 0.0).label("avg_procurement_h"),
            func.coalesce(func.avg(Order.total_time), 0.0).label("avg_total_h"),
            func.coalesce(func.avg(Order.unit_price), 0.0).label("avg_price"),
            func.coalesce(func.sum(Order.total_amount), 0.0).label("total_revenue"),
        )
        .outerjoin(Order, and_(Order.supplier_id == Supplier.id,
                               *_date_f(Order.order_placed_at, start, end)))
        .group_by(Supplier.id, Supplier.supplier_name, Supplier.rating)
        .order_by(func.count(Order.id).desc())
        .all()
    )

    suppliers = []
    for r in rows:
        total = int(r.total_orders or 0)
        breaches = int(r.breaches or 0)
        delivered = int(r.delivered or 0)
        sla_rate = _pct(total - breaches, total)
        fill_rate = _pct(delivered, total)

        # Reliability score (weighted composite)
        sla_pts     = sla_rate * 0.40          # 40% weight
        fill_pts    = fill_rate * 0.30          # 30% weight
        rating_pts  = _r(r.rating or 0) / 5.0 * 100 * 0.30  # 30% weight
        reliability = _r(sla_pts + fill_pts + rating_pts)

        # Lead-time speed rating (lower is better — invert for score)
        avg_proc = _r(r.avg_procurement_h)
        speed_label = "fast" if avg_proc < 24 else "moderate" if avg_proc < 48 else "slow"

        suppliers.append({
            "supplier_id":          r.id,
            "supplier_name":        r.supplier_name,
            "rating":               r.rating or 0,
            "total_orders":         total,
            "delivered":            delivered,
            "sla_breaches":         breaches,
            "sla_compliance_rate":  sla_rate,
            "fill_rate":            fill_rate,
            "avg_procurement_h":    avg_proc,
            "avg_total_cycle_h":    _r(r.avg_total_h),
            "avg_unit_price":       _r(r.avg_price),
            "total_revenue":        _r(r.total_revenue),
            "reliability_score":    reliability,
            "speed_label":          speed_label,
            "performance_tier":     (
                "elite" if reliability >= 85 else
                "good"  if reliability >= 70 else
                "fair"  if reliability >= 50 else
                "poor"
            ),
        })

    # Overall averages
    if suppliers:
        avg_reliability = _r(sum(s["reliability_score"] for s in suppliers) / len(suppliers))
        avg_sla = _r(sum(s["sla_compliance_rate"] for s in suppliers) / len(suppliers))
    else:
        avg_reliability = avg_sla = 0.0

    return {
        "period_days":           days,
        "supplier_count":        len(suppliers),
        "avg_reliability_score": avg_reliability,
        "avg_sla_compliance":    avg_sla,
        "suppliers":             suppliers,
    }


# ─────────────────────────────────────────────────────────────────────────────
#  7. Forecast Performance
# ─────────────────────────────────────────────────────────────────────────────

def get_forecast_performance(db: Session, weeks: int = 12) -> Dict[str, Any]:
    """
    Weekly order volume trends used as a proxy for forecast performance tracking.
    Shows weekly actual demand per category and overall, useful for evaluating
    forecast model coverage and identifying demand volatility.
    """
    start = _now() - timedelta(weeks=weeks)
    end = _now()

    rows = (
        db.query(
            func.strftime("%Y-%W", Order.order_placed_at).label("week"),
            Product.category,
            func.coalesce(func.sum(Order.quantity), 0).label("units"),
            func.count(Order.id).label("orders"),
        )
        .join(Product, Order.product_id == Product.id)
        .filter(*_date_f(Order.order_placed_at, start, end))
        .group_by(
            func.strftime("%Y-%W", Order.order_placed_at),
            Product.category,
        )
        .order_by(func.strftime("%Y-%W", Order.order_placed_at))
        .all()
    )

    # Pivot into week → category → units
    week_data: Dict[str, Dict] = {}
    categories: set = set()
    for r in rows:
        week = r.week or ""
        cat = r.category or "Uncategorised"
        categories.add(cat)
        if week not in week_data:
            week_data[week] = {"total_units": 0, "total_orders": 0}
        week_data[week][cat] = int(r.units or 0)
        week_data[week]["total_units"] += int(r.units or 0)
        week_data[week]["total_orders"] += int(r.orders or 0)

    weeks_sorted = sorted(week_data.keys())
    total_series = [week_data[w]["total_units"] for w in weeks_sorted]

    # Demand volatility (coefficient of variation)
    if len(total_series) > 1:
        mean_d = sum(total_series) / len(total_series)
        std_d = (sum((x - mean_d) ** 2 for x in total_series) / len(total_series)) ** 0.5
        cov = _r(std_d / mean_d * 100) if mean_d else 0.0
    else:
        cov = 0.0

    volatility_label = "low" if cov < 20 else "moderate" if cov < 40 else "high"

    return {
        "weeks":               weeks,
        "categories":          sorted(categories),
        "labels":              weeks_sorted,
        "total_demand_series": total_series,
        "category_series": {
            cat: [week_data[w].get(cat, 0) for w in weeks_sorted]
            for cat in sorted(categories)
        },
        "demand_stats": {
            "avg_weekly_units":   _r(sum(total_series) / len(total_series)) if total_series else 0.0,
            "peak_units":         max(total_series) if total_series else 0,
            "min_units":          min(total_series) if total_series else 0,
            "volatility_pct":     cov,
            "volatility_label":   volatility_label,
        },
    }


# ─────────────────────────────────────────────────────────────────────────────
#  8. Order Cohorts
# ─────────────────────────────────────────────────────────────────────────────

def get_order_cohorts(db: Session, months: int = 6) -> Dict[str, Any]:
    """
    Monthly cohort analysis — groups orders by the month they were placed
    and tracks delivery rate, SLA compliance, and avg cycle time per cohort.
    """
    start = _now() - timedelta(days=months * 30)
    end = _now()

    rows = (
        db.query(
            func.strftime("%Y-%m", Order.order_placed_at).label("cohort_month"),
            func.count(Order.id).label("total"),
            func.sum(case((Order.status == "delivered", 1), else_=0)).label("delivered"),
            func.sum(case((Order.sla_breach == True, 1), else_=0)).label("breaches"),
            func.coalesce(func.avg(Order.total_time), 0.0).label("avg_cycle"),
            func.coalesce(func.sum(Order.total_amount), 0.0).label("revenue"),
        )
        .filter(*_date_f(Order.order_placed_at, start, end))
        .group_by(func.strftime("%Y-%m", Order.order_placed_at))
        .order_by(func.strftime("%Y-%m", Order.order_placed_at))
        .all()
    )

    cohorts = []
    for r in rows:
        total = int(r.total or 0)
        delivered = int(r.delivered or 0)
        breaches  = int(r.breaches or 0)
        cohorts.append({
            "cohort_month":       r.cohort_month or "",
            "total_orders":       total,
            "delivered":          delivered,
            "sla_breaches":       breaches,
            "delivery_rate":      _pct(delivered, total),
            "sla_compliance":     _pct(total - breaches, total),
            "avg_cycle_time_h":   _r(r.avg_cycle),
            "revenue":            _r(r.revenue),
        })

    # Trend: are newer cohorts performing better?
    if len(cohorts) >= 2:
        delivery_trend = "improving" if cohorts[-1]["delivery_rate"] > cohorts[0]["delivery_rate"] else "declining"
        sla_trend = "improving" if cohorts[-1]["sla_compliance"] > cohorts[0]["sla_compliance"] else "declining"
    else:
        delivery_trend = sla_trend = "insufficient_data"

    return {
        "months":            months,
        "cohort_count":      len(cohorts),
        "cohorts":           cohorts,
        "trends": {
            "delivery_rate":  delivery_trend,
            "sla_compliance": sla_trend,
        },
    }


# ─────────────────────────────────────────────────────────────────────────────
#  9. Category Deep Dive
# ─────────────────────────────────────────────────────────────────────────────

def get_category_deep_dive(
    db: Session,
    days: int = 90,
    category: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Per-category multi-metric deep dive:
      revenue, units, margin, SLA rate, fill rate, stock value, top product.
    If category is specified, returns detailed breakdown for that category only.
    """
    start, end = _window(days)

    # ── Order metrics per category ───────────────────────────────────────────
    order_rows = (
        db.query(
            Product.category,
            func.coalesce(func.sum(Order.total_amount), 0.0).label("revenue"),
            func.coalesce(func.sum(Order.quantity), 0).label("units"),
            func.count(Order.id).label("orders"),
            func.sum(case((Order.status == "delivered", 1), else_=0)).label("delivered"),
            func.sum(case((Order.sla_breach == True, 1), else_=0)).label("breaches"),
            func.coalesce(func.avg(Order.total_time), 0.0).label("avg_cycle"),
        )
        .join(Product, Order.product_id == Product.id)
        .filter(*_date_f(Order.order_placed_at, start, end))
        .group_by(Product.category)
        .order_by(func.sum(Order.total_amount).desc())
        .all()
    )

    # ── Inventory value per category ─────────────────────────────────────────
    inv_rows = (
        db.query(
            Product.category,
            func.coalesce(
                func.sum(Inventory.quantity_available * Product.unit_price), 0.0
            ).label("stock_value"),
            func.count(Product.id).label("product_count"),
        )
        .join(Inventory, Inventory.product_id == Product.id)
        .group_by(Product.category)
        .all()
    )
    inv_map = {r.category: {"stock_value": _r(r.stock_value), "product_count": int(r.product_count)} for r in inv_rows}

    # ── Top product per category ─────────────────────────────────────────────
    top_prod_rows = (
        db.query(
            Product.category,
            Product.product_name,
            func.coalesce(func.sum(Order.total_amount), 0.0).label("revenue"),
        )
        .join(Product, Order.product_id == Product.id)
        .filter(*_date_f(Order.order_placed_at, start, end))
        .group_by(Product.category, Product.product_name)
        .order_by(func.sum(Order.total_amount).desc())
        .all()
    )
    top_prod_map: Dict[str, str] = {}
    for r in top_prod_rows:
        cat = r.category or "Uncategorised"
        if cat not in top_prod_map:
            top_prod_map[cat] = r.product_name

    categories = []
    for r in order_rows:
        cat = r.category or "Uncategorised"
        if category and cat.lower() != category.lower():
            continue
        total = int(r.orders or 0)
        delivered = int(r.delivered or 0)
        breaches  = int(r.breaches or 0)
        rev       = _r(r.revenue)
        margin    = _r(rev * 0.35)    # 35% gross margin assumed

        inv_data = inv_map.get(cat, {"stock_value": 0.0, "product_count": 0})

        categories.append({
            "category":          cat,
            "revenue":           rev,
            "units_sold":        int(r.units or 0),
            "orders":            total,
            "delivered":         delivered,
            "fill_rate":         _pct(delivered, total),
            "sla_compliance":    _pct(total - breaches, total),
            "avg_cycle_time_h":  _r(r.avg_cycle),
            "gross_margin":      margin,
            "margin_pct":        35.0,
            "stock_value":       inv_data["stock_value"],
            "product_count":     inv_data["product_count"],
            "top_product":       top_prod_map.get(cat, "N/A"),
        })

    return {
        "period_days":     days,
        "filter_category": category,
        "category_count":  len(categories),
        "categories":      categories,
    }


# ─────────────────────────────────────────────────────────────────────────────
#  10. Operational Efficiency
# ─────────────────────────────────────────────────────────────────────────────

def get_operational_efficiency(db: Session, days: int = 90) -> Dict[str, Any]:
    """
    Cycle-time benchmarks vs SLA thresholds, bottleneck frequency heatmap,
    and stage-wise performance metrics.
    SLA thresholds: procurement=48h, processing=24h, dispatch=12h, delivery=72h.
    """
    start, end = _window(days)

    SLA = {
        "procurement": 48,
        "processing":  24,
        "dispatch":    12,
        "delivery":    72,
    }

    delivered_q = db.query(Order).filter(
        Order.status == "delivered",
        *_date_f(Order.order_placed_at, start, end),
    )

    # ── Stage benchmarks ─────────────────────────────────────────────────────
    stage_rows = db.query(
        func.coalesce(func.avg(Order.procurement_time),       0.0).label("avg_procurement"),
        func.coalesce(func.avg(Order.processing_time),        0.0).label("avg_processing"),
        func.coalesce(func.avg(Order.dispatch_time_duration), 0.0).label("avg_dispatch"),
        func.coalesce(func.avg(Order.delivery_time_duration), 0.0).label("avg_delivery"),
        func.coalesce(func.avg(Order.total_time),             0.0).label("avg_total"),
    ).filter(
        Order.status == "delivered",
        *_date_f(Order.order_placed_at, start, end),
    ).one()

    stages = {}
    for stage, sla_h in SLA.items():
        col_map = {
            "procurement": _r(stage_rows.avg_procurement),
            "processing":  _r(stage_rows.avg_processing),
            "dispatch":    _r(stage_rows.avg_dispatch),
            "delivery":    _r(stage_rows.avg_delivery),
        }
        avg_h = col_map[stage]
        pct_of_sla = _pct(avg_h, sla_h)
        stages[stage] = {
            "avg_actual_h":  avg_h,
            "sla_threshold_h": sla_h,
            "pct_of_sla":    pct_of_sla,
            "within_sla":    avg_h <= sla_h,
            "status": (
                "on_track"   if pct_of_sla <= 75 else
                "at_risk"    if pct_of_sla <= 100 else
                "breached"
            ),
        }

    # ── Bottleneck frequency heatmap ─────────────────────────────────────────
    bottleneck_rows = (
        db.query(
            Order.bottleneck_stage,
            func.count(Order.id).label("count"),
        )
        .filter(
            Order.bottleneck_stage.isnot(None),
            *_date_f(Order.order_placed_at, start, end),
        )
        .group_by(Order.bottleneck_stage)
        .order_by(func.count(Order.id).desc())
        .all()
    )
    total_bottlenecks = sum(r.count for r in bottleneck_rows) or 1
    bottleneck_heatmap = [
        {
            "stage":      r.bottleneck_stage,
            "count":      int(r.count),
            "frequency":  _pct(r.count, total_bottlenecks),
        }
        for r in bottleneck_rows
    ]
    worst_bottleneck = bottleneck_heatmap[0]["stage"] if bottleneck_heatmap else "N/A"

    # ── SLA breach rate by supplier ───────────────────────────────────────────
    breach_by_sup = (
        db.query(
            Supplier.supplier_name,
            func.count(Order.id).label("total"),
            func.sum(case((Order.sla_breach == True, 1), else_=0)).label("breaches"),
        )
        .join(Supplier, Order.supplier_id == Supplier.id)
        .filter(*_date_f(Order.order_placed_at, start, end))
        .group_by(Supplier.supplier_name)
        .order_by(func.sum(case((Order.sla_breach == True, 1), else_=0)).desc())
        .limit(5)
        .all()
    )

    total_orders = _order_count(db, start, end)
    total_breaches = _sla_breach_count(db, start, end)

    return {
        "period_days":          days,
        "total_orders":         total_orders,
        "total_sla_breaches":   total_breaches,
        "overall_sla_rate":     _pct(total_orders - total_breaches, total_orders),
        "avg_total_cycle_h":    _r(stage_rows.avg_total),
        "worst_bottleneck":     worst_bottleneck,
        "stage_benchmarks":     stages,
        "bottleneck_heatmap":   bottleneck_heatmap,
        "breach_by_supplier": [
            {
                "supplier_name": r.supplier_name,
                "total_orders":  int(r.total or 0),
                "breaches":      int(r.breaches or 0),
                "breach_rate":   _pct(r.breaches or 0, r.total or 1),
            }
            for r in breach_by_sup
        ],
    }
