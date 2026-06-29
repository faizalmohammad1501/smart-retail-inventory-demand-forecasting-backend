"""
AI-Powered Inventory Insights API Routes
==========================================
Delivers intelligent analytics, decision support, and executive intelligence
for the Smart Retail platform.

Endpoints
---------
  GET  /api/insights/demand-anomalies       — Z-score demand spike/drop detection
  GET  /api/insights/product-velocity       — ABC×XYZ product segmentation
  GET  /api/insights/inventory-risks        — composite risk scoring per product
  GET  /api/insights/supplier-performance   — supplier scorecard with trend
  GET  /api/insights/revenue-opportunities  — stockout losses, demand gaps, markdowns
  GET  /api/insights/dead-stock             — zero-demand items with carrying costs
  GET  /api/insights/demand-patterns        — seasonality: day-of-week + monthly
  GET  /api/insights/reorder-urgency        — ranked reorder urgency + order qty
  GET  /api/insights/recommendations        — unified prioritised action list
  GET  /api/insights/kpi-scorecard          — structured KPIs for dashboard widgets
  GET  /api/insights/executive-summary      — full AI executive intelligence briefing
  GET  /api/insights/narratives             — domain-specific AI narrative paragraphs

All endpoints:
  - Require Bearer token authentication
  - Role: admin, manager, or analyst (read access)
  - Return deterministic JSON (re-running produces the same output for same data)
  - Include generated_at timestamp and lookback_days in every response

Prefix: /api/insights
Tag:    AI Insights & Decision Support
"""

import logging
from typing import Any, Dict

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_active_user, require_roles
from app.database.connection import get_db
from app.models.user import User
from app.services.insights_service import (
    detect_demand_anomalies,
    classify_product_velocity,
    score_inventory_risks,
    analyze_supplier_performance,
    identify_revenue_opportunities,
    detect_dead_stock,
    analyze_demand_patterns,
)
from app.services.decision_service import (
    generate_recommendations,
    rank_reorder_urgency,
)
from app.services.executive_intelligence_service import (
    get_kpi_scorecard,
    generate_executive_summary,
    generate_insight_narratives,
)

logger = logging.getLogger("smart_retail.insights_routes")

router = APIRouter(prefix="/api/insights", tags=["AI Insights & Decision Support"])

# Shared role requirement (admin, manager, analyst)
_READ = require_roles("admin", "manager", "analyst")


# ── GET /api/insights/demand-anomalies ───────────────────────────────────────

@router.get("/demand-anomalies", status_code=status.HTTP_200_OK)
def demand_anomalies(
    lookback_days: int = Query(default=90, ge=7, le=365,
                               description="Analysis window in days"),
    z_threshold: float = Query(default=2.5, ge=1.0, le=5.0,
                               description="Z-score threshold for anomaly classification"),
    min_orders: int = Query(default=5, ge=2, le=50,
                            description="Minimum data points required per product"),
    _: User = Depends(_READ),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """
    **Demand anomaly detection** using Z-score statistical analysis.

    Identifies products with statistically abnormal demand events (spikes or drops)
    over the lookback window.  Useful for:
    - Detecting supply disruptions (demand drops)
    - Identifying viral/trending products (demand spikes)
    - Spotting data entry errors in order records

    Each anomaly includes: product, date, quantity, Z-score, severity, direction.
    Results are sorted by |Z-score| descending (most extreme first).
    """
    return detect_demand_anomalies(
        db,
        lookback_days=lookback_days,
        z_threshold=z_threshold,
        min_orders=min_orders,
    )


# ── GET /api/insights/product-velocity ───────────────────────────────────────

@router.get("/product-velocity", status_code=status.HTTP_200_OK)
def product_velocity(
    lookback_days: int = Query(default=90, ge=14, le=365),
    _: User = Depends(_READ),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """
    **ABC × XYZ product velocity segmentation.**

    - **ABC** — revenue contribution (A=top 70%, B=next 20%, C=bottom 10%)
    - **XYZ** — demand variability via coefficient of variation on weekly buckets
      (X=stable CV≤0.5, Y=moderate 0.5–1.0, Z=erratic CV>1.0)

    Combined segment (e.g. AX = high-revenue stable, CZ = low-revenue erratic)
    drives replenishment strategy, safety stock sizing, and promotional decisions.

    Includes per-product revenue %, order count, CV, and velocity label.
    """
    return classify_product_velocity(db, lookback_days=lookback_days)


# ── GET /api/insights/inventory-risks ────────────────────────────────────────

@router.get("/inventory-risks", status_code=status.HTTP_200_OK)
def inventory_risks(
    lookback_days: int = Query(default=30, ge=7, le=180),
    _: User = Depends(_READ),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """
    **Composite inventory risk scoring** per product (0–100).

    Four risk components (weights):
    - Stockout risk      (40%) — proximity to zero stock relative to reorder level
    - Overstock risk     (20%) — excess stock vs low demand
    - Obsolescence risk  (25%) — non-zero stock with no recent orders
    - Velocity risk      (15%) — erratic demand driving surprise stockouts (high CV)

    Risk tiers: CRITICAL ≥ 75 | HIGH ≥ 50 | MEDIUM ≥ 25 | LOW < 25.
    Also identifies the dominant risk factor per product.
    """
    return score_inventory_risks(db, lookback_days=lookback_days)


# ── GET /api/insights/supplier-performance ───────────────────────────────────

@router.get("/supplier-performance", status_code=status.HTTP_200_OK)
def supplier_performance(
    lookback_days: int = Query(default=90, ge=14, le=365),
    _: User = Depends(_READ),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """
    **Supplier performance intelligence** with trend analysis.

    Per-supplier scorecard:
    - On-time delivery rate (% of orders without SLA breach)
    - Avg procurement time (hours) and std-dev (reliability proxy)
    - SLA breach rate
    - Performance score (0–100) — composite of on-time rate, reliability, supplier rating
    - Trend: improving | stable | declining (vs prior period)
    - Recommendation: Preferred / Monitor / At risk

    Sorted by performance score descending (best first).
    """
    return analyze_supplier_performance(db, lookback_days=lookback_days)


# ── GET /api/insights/revenue-opportunities ──────────────────────────────────

@router.get("/revenue-opportunities", status_code=status.HTTP_200_OK)
def revenue_opportunities(
    lookback_days: int = Query(default=30, ge=7, le=180),
    _: User = Depends(_READ),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """
    **Revenue opportunity identification.**

    Scans for four categories of revenue leakage and uplift:
    1. **Stockout losses** — high-demand products with zero stock
       (estimated lost revenue = demand × days_out × unit_price)
    2. **Demand-supply gaps** — critically low stock against high demand rate
    3. **Markdown opportunities** — overpriced slow-movers with excess inventory
    4. **Category declines** — categories with >20% revenue drop period-over-period

    Returns opportunities ranked by estimated_value descending with
    actionable recommendations (RESTOCK_IMMEDIATELY, EXPEDITE_REORDER,
    CONSIDER_MARKDOWN, INVESTIGATE_CATEGORY).
    """
    return identify_revenue_opportunities(db, lookback_days=lookback_days)


# ── GET /api/insights/dead-stock ─────────────────────────────────────────────

@router.get("/dead-stock", status_code=status.HTTP_200_OK)
def dead_stock(
    days_threshold: int = Query(default=90, ge=30, le=365,
                                description="Days with no demand to classify as dead stock"),
    _: User = Depends(_READ),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """
    **Dead-stock detection** — inventory items with zero demand.

    Identifies products with non-zero stock that have had no orders in the
    threshold window.  Returns:
    - Quantity on hand and book value
    - Monthly carrying cost estimate (2% of book value/month)
    - Days idle (since last restock or threshold)
    - Recommendation: Liquidate | Markdown | Monitor

    Total dead-stock book value and monthly carrying cost shown in summary.
    """
    return detect_dead_stock(db, days_threshold=days_threshold)


# ── GET /api/insights/demand-patterns ────────────────────────────────────────

@router.get("/demand-patterns", status_code=status.HTTP_200_OK)
def demand_patterns(
    lookback_days: int = Query(default=365, ge=30, le=730),
    _: User = Depends(_READ),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """
    **Demand seasonality and pattern analysis.**

    Returns:
    - Day-of-week demand distribution (order count + quantity per weekday)
    - Monthly revenue and order volume trend
    - Peak trading day and peak revenue month
    - Top-selling category per month (useful for seasonal buying plans)

    Use this to optimise procurement timing, staffing, and promotional calendars.
    """
    return analyze_demand_patterns(db, lookback_days=lookback_days)


# ── GET /api/insights/reorder-urgency ────────────────────────────────────────

@router.get("/reorder-urgency", status_code=status.HTTP_200_OK)
def reorder_urgency(
    lookback_days: int = Query(default=30, ge=7, le=180),
    _: User = Depends(_READ),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """
    **Reorder urgency ranking** with recommended order quantities.

    Combines days-on-hand and inventory risk score to produce a per-product
    urgency score (0–100).

    Recommended order quantity uses the safety-stock formula:
      `Q = avg_daily_demand × lead_time + Z × σ_demand × √lead_time`
    where Z = 1.65 (95% service level) and lead_time = 14 days.

    Urgency tiers: CRITICAL ≥ 75 | HIGH ≥ 50 | MEDIUM ≥ 25 | LOW < 25.
    Results sorted by urgency_score descending.
    """
    return rank_reorder_urgency(db, lookback_days=lookback_days)


# ── GET /api/insights/recommendations ────────────────────────────────────────

@router.get("/recommendations", status_code=status.HTTP_200_OK)
def recommendations(
    lookback_days: int = Query(default=30, ge=7, le=180),
    priority: str = Query(default="all",
                          description="Filter by priority: all | CRITICAL | HIGH | MEDIUM | LOW"),
    rec_type: str = Query(default="all",
                          description="Filter by type: all | procurement | inventory | supplier | pricing | category | inventory_strategy"),
    _: User = Depends(_READ),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """
    **Unified AI-generated recommendation list** ranked by priority score.

    Aggregates signals from all insight engines (risk scoring, dead stock,
    supplier performance, revenue opportunities, velocity analysis) into a
    single, deduplicated, ranked list of actionable recommendations.

    Each recommendation includes:
    - type, priority, priority_score (sort key)
    - title + full description narrative
    - action verb (machine-readable)
    - resource_id, resource_type, resource_name, SKU
    - context dict (supporting numbers)
    - estimated_value (monetary impact, 0 if not quantifiable)
    - expires_in_days (recommendation freshness)

    Filter by `priority` or `rec_type` to narrow the list.
    """
    data = generate_recommendations(db, lookback_days=lookback_days)

    recs = data["recommendations"]
    if priority != "all":
        recs = [r for r in recs if r["priority"] == priority.upper()]
    if rec_type != "all":
        recs = [r for r in recs if r["type"] == rec_type.lower()]

    return {
        **{k: v for k, v in data.items() if k != "recommendations"},
        "filter_priority": priority,
        "filter_type":     rec_type,
        "returned":        len(recs),
        "recommendations": recs,
    }


# ── GET /api/insights/kpi-scorecard ──────────────────────────────────────────

@router.get("/kpi-scorecard", status_code=status.HTTP_200_OK)
def kpi_scorecard(
    days: int = Query(default=30, ge=7, le=365),
    _: User = Depends(_READ),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """
    **Structured KPI scorecard** for dashboard widgets.

    Each of the 6 KPIs includes:
    - value, prior_value, delta_pct, trend (↑/↓/→)
    - status (good / warning / critical / neutral)
    - unit

    KPIs covered:
      Total Revenue | Order Volume | Avg Order Value |
      SLA Breach Rate | Inventory Health % | Active Alerts
    """
    return get_kpi_scorecard(db, days=days)


# ── GET /api/insights/executive-summary ──────────────────────────────────────

@router.get("/executive-summary", status_code=status.HTTP_200_OK)
def executive_summary(
    days: int = Query(default=30, ge=7, le=180),
    _: User = Depends(_READ),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """
    **Full AI Executive Intelligence Briefing.**

    The platform's most comprehensive single endpoint — synthesises all insight
    engines into a C-suite-ready briefing:

    - **health_score** (0–100) and **health_grade** (A–F)
    - **kpi_scorecard** — 6 headline KPIs with period-over-period deltas
    - **top_risks** — top 3 CRITICAL inventory risks with AI narrative
    - **top_opportunities** — top 3 revenue opportunities with value estimates
    - **immediate_actions** — top 5 CRITICAL/HIGH priority actions to take now
    - **ai_commentary** — 250-word executive summary paragraph
    - **summary_stats** — quick-glance numbers for the executive dashboard

    Designed to be the "first screen" a manager sees each morning.
    """
    return generate_executive_summary(db, days=days)


# ── GET /api/insights/narratives ─────────────────────────────────────────────

@router.get("/narratives", status_code=status.HTTP_200_OK)
def insight_narratives(
    days: int = Query(default=30, ge=7, le=180),
    _: User = Depends(_READ),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """
    **Domain-specific AI narrative paragraphs** for insight cards.

    Returns four data-grounded narrative paragraphs:
    - **demand**    — velocity leaders, slow movers, total revenue context
    - **inventory** — risk distribution, highest-risk product narrative
    - **supplier**  — performance distribution, at-risk supplier callout
    - **revenue**   — total opportunity value, largest single opportunity

    Use these to populate insight card text on the dashboard or
    to generate automated email briefings.
    """
    return generate_insight_narratives(db, days=days)
