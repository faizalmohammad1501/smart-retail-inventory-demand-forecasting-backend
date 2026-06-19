"""
Advanced Business Intelligence API Routes
==========================================
10 net-new endpoints providing executive-grade analytics that go beyond
the existing /api/reports and /api/dashboard modules.

All endpoints:
  - Require a valid Bearer token
  - Return structured JSON with standard response shapes
  - Support optional date-range / granularity / filter query params

Prefix: /api/bi
Tag:    Business Intelligence
"""

from typing import Literal, Optional

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_active_user
from app.database.connection import get_db
from app.models.user import User
from app.services.bi_service import (
    get_executive_summary,
    get_kpi_trends,
    get_profitability_analysis,
    get_period_comparison,
    get_inventory_health_score,
    get_supplier_intelligence,
    get_forecast_performance,
    get_order_cohorts,
    get_category_deep_dive,
    get_operational_efficiency,
)
from app.schemas.schemas import (
    BIExecutiveSummaryResponse,
    BITrendSeriesResponse,
    BIProfitabilityResponse,
    BIPeriodComparisonResponse,
    BIInventoryHealthResponse,
    BISupplierIntelligenceResponse,
    BIForecastPerformanceResponse,
    BIOrderCohortsResponse,
    BICategoryDeepDiveResponse,
    BIOperationalEfficiencyResponse,
)

router = APIRouter(prefix="/api/bi", tags=["Business Intelligence"])


# ─────────────────────────────────────────────────────────────────────────────
#  1. Executive Summary
# ─────────────────────────────────────────────────────────────────────────────

@router.get(
    "/executive-summary",
    summary="C-Suite Executive KPI Pack",
    description=(
        "Returns a complete executive-level KPI pack for the requested period. "
        "Every metric includes the current value, prior-period value, percentage "
        "change, and a trend direction label (`up_good`, `down_bad`, `up_bad`, "
        "`neutral`). Covers revenue, order volume, AOV, fill rate, SLA compliance, "
        "avg cycle time, supplier rating, inventory health score, and active alerts."
    ),
)
def executive_summary(
    days: int = Query(30, ge=1, le=365, description="Lookback window in days (default 30)"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    return get_executive_summary(db, days=days)


# ─────────────────────────────────────────────────────────────────────────────
#  2. KPI Trends
# ─────────────────────────────────────────────────────────────────────────────

@router.get(
    "/kpi-trends",
    summary="Time-Series KPI Trends",
    description=(
        "Returns parallel time-series arrays for revenue, order count, avg cycle "
        "time, and SLA compliance rate, grouped by day / week / month. "
        "Ideal for Chart.js multi-line trend charts on the executive dashboard."
    ),
)
def kpi_trends(
    days: int = Query(90, ge=7, le=365, description="Lookback window in days"),
    granularity: Literal["daily", "weekly", "monthly"] = Query(
        "weekly", description="Grouping granularity"
    ),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    return get_kpi_trends(db, days=days, granularity=granularity)


# ─────────────────────────────────────────────────────────────────────────────
#  3. Profitability Analysis
# ─────────────────────────────────────────────────────────────────────────────

@router.get(
    "/profitability",
    summary="Gross Margin & Profitability Analysis",
    description=(
        "Breaks down revenue, estimated COGS (65% ratio), and gross margin by "
        "product category and top-N individual products. Returns both a high-level "
        "summary and ranked detail lists. Use `top_n` to control how many products "
        "appear in the ranked list."
    ),
)
def profitability(
    days: int = Query(90, ge=1, le=365),
    top_n: int = Query(10, ge=1, le=50, description="Number of top products to include"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    return get_profitability_analysis(db, days=days, top_n=top_n)


# ─────────────────────────────────────────────────────────────────────────────
#  4. Period Comparison (MoM / QoQ / YoY)
# ─────────────────────────────────────────────────────────────────────────────

@router.get(
    "/period-comparison",
    summary="MoM / QoQ / YoY Performance Comparison",
    description=(
        "Compares the current period against the equivalent prior period across "
        "6 KPIs: total revenue, order volume, AOV, fill rate, SLA compliance, "
        "and avg cycle time. Each metric includes an `improved` boolean.\n\n"
        "- `mom` = last 30 days vs prior 30 days\n"
        "- `qoq` = last 90 days vs prior 90 days\n"
        "- `yoy` = last 365 days vs prior 365 days"
    ),
)
def period_comparison(
    mode: Literal["mom", "qoq", "yoy"] = Query("mom", description="Comparison mode"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    return get_period_comparison(db, mode=mode)


# ─────────────────────────────────────────────────────────────────────────────
#  5. Inventory Health Score
# ─────────────────────────────────────────────────────────────────────────────

@router.get(
    "/inventory-health-score",
    summary="Composite Inventory Health Score (0–100)",
    description=(
        "Calculates a composite inventory health score from 5 weighted components:\n"
        "- **Stock availability** (25 pts): % of products with qty > 0\n"
        "- **Reorder compliance** (20 pts): % of items above reorder level\n"
        "- **Overstock control** (20 pts): penalty for items 10× above reorder level\n"
        "- **Stock freshness** (20 pts): % restocked within last 90 days\n"
        "- **Turnover activity** (15 pts): % products with orders in last 90 days\n\n"
        "Risk levels: `healthy` (≥80), `moderate` (60-79), `at_risk` (40-59), `critical` (<40)."
    ),
)
def inventory_health_score(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    return get_inventory_health_score(db)


# ─────────────────────────────────────────────────────────────────────────────
#  6. Supplier Intelligence
# ─────────────────────────────────────────────────────────────────────────────

@router.get(
    "/supplier-intelligence",
    summary="Advanced Supplier Intelligence & Reliability Scoring",
    description=(
        "Per-supplier analytics including: SLA compliance rate, order fill rate, "
        "avg procurement lead time, avg cycle time, total revenue, and a composite "
        "reliability score (0–100, weighted 40% SLA + 30% fill rate + 30% rating). "
        "Each supplier is classified into a performance tier: "
        "`elite` (≥85), `good` (70-84), `fair` (50-69), `poor` (<50)."
    ),
)
def supplier_intelligence(
    days: int = Query(90, ge=1, le=365),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    return get_supplier_intelligence(db, days=days)


# ─────────────────────────────────────────────────────────────────────────────
#  7. Forecast Performance
# ─────────────────────────────────────────────────────────────────────────────

@router.get(
    "/forecast-performance",
    summary="Weekly Demand Volume & Forecast Tracking",
    description=(
        "Returns weekly actual demand volume broken down by product category. "
        "Includes demand volatility metrics (coefficient of variation) to assess "
        "forecast difficulty. Use this data to overlay forecast predictions vs "
        "actual order volumes in the frontend charting layer."
    ),
)
def forecast_performance(
    weeks: int = Query(12, ge=4, le=52, description="Number of past weeks to analyse"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    return get_forecast_performance(db, weeks=weeks)


# ─────────────────────────────────────────────────────────────────────────────
#  8. Order Cohorts
# ─────────────────────────────────────────────────────────────────────────────

@router.get(
    "/order-cohorts",
    summary="Monthly Order Cohort Analysis",
    description=(
        "Groups orders by the calendar month they were placed and tracks per-cohort "
        "KPIs: total orders, delivery rate, SLA compliance rate, avg cycle time, "
        "and revenue. Also returns a trend summary indicating whether newer cohorts "
        "are improving or declining relative to the oldest cohort."
    ),
)
def order_cohorts(
    months: int = Query(6, ge=2, le=24, description="Number of past months to include"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    return get_order_cohorts(db, months=months)


# ─────────────────────────────────────────────────────────────────────────────
#  9. Category Deep Dive
# ─────────────────────────────────────────────────────────────────────────────

@router.get(
    "/category-deep-dive",
    summary="Multi-Metric Category Deep Dive",
    description=(
        "Per-category breakdown combining order analytics, inventory data, and "
        "profitability estimates:\n"
        "- Revenue, units sold, order count\n"
        "- Fill rate and SLA compliance\n"
        "- Avg cycle time\n"
        "- Gross margin estimate\n"
        "- Current stock value\n"
        "- Product count and top product by revenue\n\n"
        "Pass `category` to drill into a single category."
    ),
)
def category_deep_dive(
    days: int = Query(90, ge=1, le=365),
    category: Optional[str] = Query(None, description="Filter to a specific category name"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    return get_category_deep_dive(db, days=days, category=category)


# ─────────────────────────────────────────────────────────────────────────────
#  10. Operational Efficiency
# ─────────────────────────────────────────────────────────────────────────────

@router.get(
    "/operational-efficiency",
    summary="Operational Efficiency — Cycle Times, Bottlenecks & SLA Benchmarks",
    description=(
        "Comprehensive operational performance report:\n"
        "- **Stage benchmarks**: avg actual time vs SLA threshold for each stage "
        "(procurement 48h / processing 24h / dispatch 12h / delivery 72h) with "
        "status: `on_track`, `at_risk`, or `breached`\n"
        "- **Bottleneck heatmap**: frequency distribution of bottleneck stages "
        "across all orders in the period\n"
        "- **Breach by supplier**: top-5 suppliers ranked by SLA breach count "
        "with breach rate percentage"
    ),
)
def operational_efficiency(
    days: int = Query(90, ge=1, le=365),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    return get_operational_efficiency(db, days=days)
