"""
AI Executive Intelligence Service
===================================
Generates natural-language-style executive summaries and AI narrative
insights by synthesising outputs from the insights and decision services.

What it produces
----------------
1. **AI Executive Summary** — a structured briefing with:
   - headline KPIs with delta vs prior period
   - top 3 risks (with narrative)
   - top 3 opportunities (with narrative)
   - recommended immediate actions (max 5)
   - overall inventory health grade (A–F)
   - AI commentary paragraph

2. **AI Insight Narratives** — per-domain natural-language paragraphs:
   - demand summary (anomalies, velocity leaders)
   - inventory health narrative
   - supplier health narrative
   - revenue opportunity narrative

3. **KPI Scorecard** — structured dict ready for dashboard widgets

All text generation is template-driven (no external LLM dependency) —
uses deterministic f-string templates populated with real data values so
the output is always factually grounded and auditable.
"""

import statistics
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import func, case
from sqlalchemy.orm import Session

from app.models.inventory import Inventory
from app.models.product import Product
from app.models.sales import Order
from app.models.supplier import Supplier
from app.models.notification import Notification
from app.services.insights_service import (
    score_inventory_risks,
    classify_product_velocity,
    analyze_supplier_performance,
    identify_revenue_opportunities,
    detect_dead_stock,
)
from app.services.decision_service import generate_recommendations


# ── Helpers ───────────────────────────────────────────────────────────────────

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _r(v, n: int = 2) -> float:
    return round(float(v), n) if v is not None else 0.0


def _pct(n, d, scale: float = 100.0) -> float:
    return round(n / d * scale, 2) if d else 0.0


def _delta(cur: float, prev: float) -> Optional[float]:
    return round((cur - prev) / prev * 100, 2) if prev else None


def _window(days: int):
    end = _now()
    return end - timedelta(days=days), end


def _grade(score: float) -> str:
    if score >= 85:   return "A"
    elif score >= 70: return "B"
    elif score >= 55: return "C"
    elif score >= 40: return "D"
    else:             return "F"


def _trend_icon(delta: Optional[float]) -> str:
    if delta is None:      return "→"
    elif delta > 5:        return "↑"
    elif delta < -5:       return "↓"
    else:                  return "→"


# ── KPI Scorecard ─────────────────────────────────────────────────────────────

def get_kpi_scorecard(db: Session, days: int = 30) -> Dict[str, Any]:
    """
    Structured KPI scorecard for dashboard widgets.

    Each metric has: value, prior_value, delta_pct, trend (up/down/stable),
    status (good/warning/critical), label.
    """
    start, end = _window(days)
    prev_start = start - timedelta(days=days)

    def _orders(s, e):
        return db.query(func.count(Order.id)).filter(
            Order.order_placed_at.between(s, e)
        ).scalar() or 0

    def _revenue(s, e):
        return _r(
            db.query(func.coalesce(func.sum(Order.total_amount), 0.0))
            .filter(Order.order_placed_at.between(s, e))
            .scalar()
        )

    def _breaches(s, e):
        return db.query(func.count(Order.id)).filter(
            Order.sla_breach == True,  # noqa: E712
            Order.order_placed_at.between(s, e),
        ).scalar() or 0

    cur_rev    = _revenue(start, end)
    prev_rev   = _revenue(prev_start, start)
    cur_orders = _orders(start, end)
    prev_orders= _orders(prev_start, start)
    cur_breach = _breaches(start, end)
    prev_breach= _breaches(prev_start, start)

    aov_cur  = _r(cur_rev  / cur_orders)  if cur_orders  else 0.0
    aov_prev = _r(prev_rev / prev_orders) if prev_orders else 0.0

    breach_rate_cur  = _pct(cur_breach,  cur_orders)
    breach_rate_prev = _pct(prev_breach, prev_orders)

    # Inventory health: % of products above reorder level
    total_products = db.query(func.count(Product.id)).scalar() or 1
    stocked_ok = (
        db.query(func.count(func.distinct(Inventory.product_id)))
        .join(Product, Product.id == Inventory.product_id)
        .filter(Inventory.quantity_available >= Product.reorder_level)
        .scalar() or 0
    )
    inv_health_pct = _pct(stocked_ok, total_products)

    # Active alerts
    active_alerts = (
        db.query(func.count(Notification.id))
        .filter(Notification.is_resolved == False)  # noqa: E712
        .scalar() or 0
    ) if _table_exists(db, "notifications") else 0

    def _kpi(label, val, prev_val, unit="", higher_is_good=True) -> Dict:
        delta = _delta(val, prev_val)
        trend  = _trend_icon(delta)
        if delta is None:
            status = "neutral"
        elif (delta > 0) == higher_is_good:
            status = "good" if abs(delta) > 5 else "neutral"
        else:
            status = "critical" if abs(delta) > 15 else "warning"
        return {
            "label":       label,
            "value":       val,
            "prior_value": prev_val,
            "delta_pct":   delta,
            "trend":       trend,
            "status":      status,
            "unit":        unit,
        }

    return {
        "period_days": days,
        "kpis": {
            "revenue":           _kpi("Total Revenue",      cur_rev,          prev_rev,        unit="USD"),
            "orders":            _kpi("Order Volume",       cur_orders,       prev_orders),
            "avg_order_value":   _kpi("Avg Order Value",    aov_cur,          aov_prev,        unit="USD"),
            "sla_breach_rate":   _kpi("SLA Breach Rate",    breach_rate_cur,  breach_rate_prev, unit="%",  higher_is_good=False),
            "inventory_health":  _kpi("Inventory Health",   inv_health_pct,   inv_health_pct,   unit="%"),
            "active_alerts":     _kpi("Active Alerts",      active_alerts,    0, higher_is_good=False),
        },
    }


# ── AI Executive Summary ──────────────────────────────────────────────────────

def generate_executive_summary(db: Session, days: int = 30) -> Dict[str, Any]:
    """
    Full AI-generated executive summary.

    Combines KPI scorecard, risk analysis, opportunity identification,
    and decision recommendations into a single cohesive briefing.
    """
    scorecard = get_kpi_scorecard(db, days=days)
    kpis      = scorecard["kpis"]

    risk_data  = score_inventory_risks(db, lookback_days=days)
    opp_data   = identify_revenue_opportunities(db, lookback_days=days)
    sup_data   = analyze_supplier_performance(db, lookback_days=max(days, 60))
    vel_data   = classify_product_velocity(db, lookback_days=days)
    recs_data  = generate_recommendations(db, lookback_days=days, max_per_type=5)
    dead_data  = detect_dead_stock(db, days_threshold=90)

    # ── Inventory health grade ────────────────────────────────────────────────
    critical_pct = _pct(
        risk_data["tier_summary"].get("CRITICAL", 0),
        risk_data["total_products"] or 1,
    )
    high_pct = _pct(
        risk_data["tier_summary"].get("HIGH", 0),
        risk_data["total_products"] or 1,
    )
    health_score = max(0, 100 - critical_pct * 2 - high_pct)
    health_grade = _grade(health_score)

    # ── Top risks ─────────────────────────────────────────────────────────────
    critical_products = [p for p in risk_data["products"] if p["risk_tier"] == "CRITICAL"][:3]
    top_risks = []
    for p in critical_products:
        top_risks.append({
            "title":       f"{p['product_name']} — {p['dominant_risk'].replace('_', ' ').title()} Risk",
            "description": _risk_narrative(p),
            "severity":    p["risk_tier"],
            "risk_score":  p["risk_score"],
            "product_id":  p["product_id"],
        })

    # ── Top opportunities ─────────────────────────────────────────────────────
    top_opps = []
    for opp in opp_data["opportunities"][:3]:
        top_opps.append({
            "title":             opp.get("product_name") or opp.get("category", ""),
            "type":              opp["type"].replace("_", " ").title(),
            "opportunity_value": opp["opportunity_value"],
            "action":            opp["action"],
            "description":       opp["description"],
            "priority":          opp["priority"],
        })

    # ── Immediate actions (top 5 CRITICAL/HIGH recs) ──────────────────────────
    immediate_actions = [
        {
            "action":        r["action"],
            "title":         r["title"],
            "priority":      r["priority"],
            "resource_name": r["resource_name"],
            "estimated_value": r["estimated_value"],
        }
        for r in recs_data["recommendations"]
        if r["priority"] in ("CRITICAL", "HIGH")
    ][:5]

    # ── AI commentary paragraph ───────────────────────────────────────────────
    rev_kpi      = kpis["revenue"]
    breach_kpi   = kpis["sla_breach_rate"]
    fast_movers  = vel_data["velocity_counts"].get("fast_mover", 0)
    slow_movers  = vel_data["velocity_counts"].get("slow_mover", 0)
    top_supplier = sup_data.get("top_performer", "your top supplier")
    total_opps   = opp_data["total_opportunity_value"]
    dead_value   = dead_data["total_book_value"]
    n_critical   = risk_data["tier_summary"].get("CRITICAL", 0)

    commentary = _build_commentary(
        days=days,
        rev_kpi=rev_kpi,
        breach_kpi=breach_kpi,
        health_grade=health_grade,
        health_score=health_score,
        fast_movers=fast_movers,
        slow_movers=slow_movers,
        n_critical=n_critical,
        total_opps=total_opps,
        dead_value=dead_value,
        top_supplier=top_supplier,
        n_recs=recs_data["total"],
    )

    return {
        "generated_at":      _now().isoformat(),
        "period_days":       days,
        "health_score":      _r(health_score),
        "health_grade":      health_grade,
        "kpi_scorecard":     kpis,
        "top_risks":         top_risks,
        "top_opportunities": top_opps,
        "immediate_actions": immediate_actions,
        "ai_commentary":     commentary,
        "summary_stats": {
            "critical_products":    risk_data["tier_summary"].get("CRITICAL", 0),
            "high_risk_products":   risk_data["tier_summary"].get("HIGH", 0),
            "total_opportunity":    opp_data["total_opportunity_value"],
            "dead_stock_value":     dead_data["total_book_value"],
            "total_recommendations":recs_data["total"],
            "fast_movers":          fast_movers,
            "slow_movers":          slow_movers,
            "underperforming_suppliers": sum(
                1 for s in sup_data["suppliers"] if s["performance_score"] < 60
            ),
        },
    }


# ── AI Insight Narratives ─────────────────────────────────────────────────────

def generate_insight_narratives(db: Session, days: int = 30) -> Dict[str, Any]:
    """
    Per-domain AI narrative paragraphs for insight cards in the dashboard.
    """
    vel_data  = classify_product_velocity(db, lookback_days=days)
    risk_data = score_inventory_risks(db, lookback_days=days)
    sup_data  = analyze_supplier_performance(db, lookback_days=max(days, 60))
    opp_data  = identify_revenue_opportunities(db, lookback_days=days)

    # Demand narrative
    fast = [p for p in vel_data["products"] if p["velocity"] == "fast_mover"][:3]
    slow = [p for p in vel_data["products"] if p["velocity"] == "slow_mover"][:3]
    fast_names = ", ".join(p["product_name"] for p in fast) or "none identified"
    slow_names = ", ".join(p["product_name"] for p in slow) or "none identified"

    demand_narrative = (
        f"Over the past {days} days, {vel_data['velocity_counts'].get('fast_mover', 0)} products "
        f"classified as fast movers (ABC class A, stable demand). "
        f"Top performers: {fast_names}. "
        f"{vel_data['velocity_counts'].get('slow_mover', 0)} slow-moving products identified — "
        f"including {slow_names} — which may benefit from promotional activity or markdown pricing. "
        f"Total category revenue: ${vel_data['total_revenue']:,.0f}."
    )

    # Inventory health narrative
    critical_n = risk_data["tier_summary"].get("CRITICAL", 0)
    high_n     = risk_data["tier_summary"].get("HIGH", 0)
    total_n    = risk_data["total_products"] or 1
    healthy_n  = risk_data["tier_summary"].get("LOW", 0)
    top_risk   = risk_data["products"][0] if risk_data["products"] else None

    inventory_narrative = (
        f"{critical_n} products ({_pct(critical_n, total_n):.1f}%) are at CRITICAL inventory risk, "
        f"{high_n} ({_pct(high_n, total_n):.1f}%) at HIGH risk, and "
        f"{healthy_n} ({_pct(healthy_n, total_n):.1f}%) have LOW risk. "
        + (
            f"Highest risk item: {top_risk['product_name']} "
            f"(risk score {top_risk['risk_score']:.0f}/100, dominant factor: {top_risk['dominant_risk']})."
            if top_risk else ""
        )
    )

    # Supplier health narrative
    n_suppliers    = sup_data["total_suppliers"]
    avg_perf       = sup_data["avg_performance"]
    top_sup        = sup_data.get("top_performer", "N/A")
    worst_sup      = sup_data.get("worst_performer", "N/A")
    at_risk_sups   = sum(1 for s in sup_data["suppliers"] if s["performance_score"] < 50)
    improving_sups = sum(1 for s in sup_data["suppliers"] if s["trend"] == "improving")

    supplier_narrative = (
        f"Across {n_suppliers} active suppliers, the average performance score is "
        f"{avg_perf:.0f}/100. "
        f"Top performer: {top_sup}. "
        + (f"Underperforming (score < 50): {worst_sup}. " if worst_sup != top_sup else "")
        + f"{at_risk_sups} supplier(s) are at risk and warrant review. "
        f"{improving_sups} supplier(s) show improving delivery trends."
    )

    # Revenue opportunity narrative
    total_opp   = opp_data["total_opportunity_value"]
    n_critical  = opp_data["priority_summary"].get("CRITICAL", 0)
    n_high      = opp_data["priority_summary"].get("HIGH", 0)
    top_opp     = opp_data["opportunities"][0] if opp_data["opportunities"] else None

    revenue_narrative = (
        f"A total of ${total_opp:,.0f} in revenue opportunity has been identified across "
        f"{opp_data['total_opportunities']} scenarios "
        f"({n_critical} critical, {n_high} high priority). "
        + (
            f"Largest single opportunity: {top_opp['description'][:150]}..."
            if top_opp else ""
        )
    )

    return {
        "generated_at": _now().isoformat(),
        "period_days":  days,
        "narratives": {
            "demand":     demand_narrative,
            "inventory":  inventory_narrative,
            "supplier":   supplier_narrative,
            "revenue":    revenue_narrative,
        },
    }


# ── Internal text helpers ─────────────────────────────────────────────────────

def _risk_narrative(p: Dict) -> str:
    dom = p["dominant_risk"]
    if dom == "stockout":
        return (
            f"Only {p['net_quantity']:.0f} units remain against a reorder level of "
            f"{p['reorder_level']:.0f}. At {p['avg_daily_demand']:.1f} units/day, "
            f"stock lasts approximately {p['days_on_hand']:.0f} more days. "
            f"Immediate replenishment required."
        )
    elif dom == "overstock":
        return (
            f"{p['net_quantity']:.0f} units on hand ({p['days_on_hand']:.0f} days of supply) "
            f"against daily demand of {p['avg_daily_demand']:.2f} units. "
            f"Excess inventory is tying up capital and increasing carrying costs."
        )
    elif dom == "obsolescence":
        return (
            f"No orders placed in {p['days_since_last_order']} days despite "
            f"{p['net_quantity']:.0f} units in stock. This product may be approaching "
            f"end-of-life. Review for markdown or liquidation."
        )
    else:
        return (
            f"Highly erratic demand (CV > 1.0) makes this product prone to "
            f"unexpected stockouts. Increase safety stock buffer."
        )


def _build_commentary(
    *,
    days: int,
    rev_kpi: Dict,
    breach_kpi: Dict,
    health_grade: str,
    health_score: float,
    fast_movers: int,
    slow_movers: int,
    n_critical: int,
    total_opps: float,
    dead_value: float,
    top_supplier: str,
    n_recs: int,
) -> str:
    rev_val  = rev_kpi["value"]
    rev_delta = rev_kpi["delta_pct"]
    breach_val = breach_kpi["value"]

    rev_sentiment = (
        f"Revenue is trending positively (+{rev_delta:.1f}% vs prior period). "
        if rev_delta and rev_delta > 0 else
        f"Revenue has declined {abs(rev_delta):.1f}% vs the prior period — "
        f"attention to demand generation may be warranted. "
        if rev_delta and rev_delta < -5 else
        "Revenue is broadly stable period-over-period. "
    )

    health_comment = (
        f"Inventory health is rated {health_grade} ({health_score:.0f}/100). "
        + (
            f"There are {n_critical} products at CRITICAL risk requiring immediate action. "
            if n_critical > 0 else
            "No critical inventory risks detected — maintain current replenishment cadence. "
        )
    )

    breach_comment = (
        f"SLA compliance is under pressure with a {breach_val:.1f}% breach rate. "
        if breach_val > 10 else
        f"SLA performance is healthy at {breach_val:.1f}% breach rate. "
    )

    opp_comment = (
        f"${total_opps:,.0f} in revenue opportunities have been identified across "
        f"stockout recovery, demand-supply gaps, and markdown scenarios. "
        if total_opps > 0 else ""
    )

    dead_comment = (
        f"${dead_value:,.0f} in dead-stock book value is consuming warehouse space and capital — "
        f"a liquidation or markdown plan is recommended. "
        if dead_value > 0 else ""
    )

    velocity_comment = (
        f"{fast_movers} fast-moving products are driving the majority of revenue; "
        f"{slow_movers} slow movers should be reviewed for promotional activity. "
        if fast_movers + slow_movers > 0 else ""
    )

    cta = (
        f"The system has generated {n_recs} actionable recommendations. "
        f"Review the /api/insights/recommendations endpoint for the full prioritised list."
        if n_recs > 0 else ""
    )

    return (
        f"Executive Inventory Intelligence — {days}-day period ending {_now().strftime('%B %d, %Y')}. "
        + rev_sentiment
        + health_comment
        + breach_comment
        + opp_comment
        + dead_comment
        + velocity_comment
        + cta
    ).strip()


def _table_exists(db: Session, table_name: str) -> bool:
    try:
        from sqlalchemy import text
        result = db.execute(
            text(f"SELECT name FROM sqlite_master WHERE type='table' AND name='{table_name}'")
        ).fetchone()
        return result is not None
    except Exception:
        return False
