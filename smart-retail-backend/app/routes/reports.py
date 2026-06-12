"""
Reporting & Business Insights API
===================================
Endpoints for sales, inventory, supplier, forecasting, and operational reports.
All endpoints support optional date-range and dimension filters.
Export endpoints stream CSV content for download.
"""

import csv
import io
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_active_user
from app.database.connection import get_db
from app.models.user import User
from app.schemas.schemas import (
    SalesSummaryResponse,
    RevenueTrendsResponse,
    TopProductsResponse,
    CategoryRevenueResponse,
    FulfillmentStatsResponse,
    InventoryValuationResponse,
    InventoryTurnoverResponse,
    InventoryAgingResponse,
    SupplierPerformanceResponse,
    SupplierScorecardResponse,
    ForecastAccuracyResponse,
    OperationalKPIsResponse,
    SLAComplianceResponse,
    BottleneckReportResponse,
    ExportResponse,
)
from app.services.reporting_service import (
    get_sales_summary,
    get_revenue_trends,
    get_top_products,
    get_category_revenue,
    get_fulfillment_stats,
    get_inventory_valuation,
    get_inventory_turnover,
    get_inventory_aging,
    get_supplier_performance,
    get_supplier_scorecard,
    get_forecast_accuracy,
    get_operational_kpis,
    get_sla_compliance_report,
    get_bottleneck_report,
    get_sales_export_data,
    get_inventory_export_data,
    get_supplier_export_data,
)

router = APIRouter(prefix="/api/reports", tags=["Reporting & Business Insights"])

# ─────────────────────────────────────────────────────────────────────────────
#  Common date-range query parameters
# ─────────────────────────────────────────────────────────────────────────────

def _parse_dates(
    start_date: Optional[str],
    end_date: Optional[str],
    endpoint: str,
):
    """Parse ISO date strings and return (start, end) datetime objects."""
    start, end = None, None
    try:
        if start_date:
            start = datetime.fromisoformat(start_date)
        if end_date:
            end = datetime.fromisoformat(end_date)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid date format for {endpoint}. Use ISO 8601 (e.g. 2024-01-01 or 2024-01-01T00:00:00).",
        ) from exc
    if start and end and start > end:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="start_date must be before end_date.",
        )
    return start, end


def _stream_csv(rows: list, filename: str) -> StreamingResponse:
    """Convert a list of dicts to a streaming CSV response."""
    if not rows:
        content = ""
    else:
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
        content = output.getvalue()

    return StreamingResponse(
        iter([content]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ─────────────────────────────────────────────────────────────────────────────
#  SALES REPORTS
# ─────────────────────────────────────────────────────────────────────────────

@router.get(
    "/sales/summary",
    response_model=SalesSummaryResponse,
    summary="Sales KPI Summary",
    description=(
        "Returns overall sales KPIs: total orders, revenue, average order value, "
        "fulfillment rate, and cancellation rate. Supports filtering by date range, "
        "category, supplier, and order status."
    ),
)
def sales_summary(
    start_date: Optional[str] = Query(None, description="ISO date, e.g. 2024-01-01"),
    end_date: Optional[str] = Query(None, description="ISO date, e.g. 2024-12-31"),
    category: Optional[str] = Query(None, description="Product category filter"),
    supplier_id: Optional[int] = Query(None, description="Supplier ID filter"),
    order_status: Optional[str] = Query(None, alias="status", description="Order status filter"),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_active_user),
):
    start, end = _parse_dates(start_date, end_date, "sales/summary")
    try:
        return get_sales_summary(db, start, end, category, supplier_id, order_status)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get(
    "/sales/trends",
    response_model=RevenueTrendsResponse,
    summary="Revenue Trends Over Time",
    description=(
        "Revenue and order volume grouped by day, week, or month. "
        "Useful for time-series charts on the executive dashboard."
    ),
)
def revenue_trends(
    granularity: str = Query("monthly", enum=["daily", "weekly", "monthly"], description="Time bucket"),
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
    supplier_id: Optional[int] = Query(None),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_active_user),
):
    start, end = _parse_dates(start_date, end_date, "sales/trends")
    try:
        return get_revenue_trends(db, granularity, start, end, category, supplier_id)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get(
    "/sales/top-products",
    response_model=TopProductsResponse,
    summary="Top Products by Revenue / Units / Orders",
    description=(
        "Returns the top-N products ranked by total revenue (default), units sold, "
        "or order count. Each entry includes revenue share percentage."
    ),
)
def top_products(
    top_n: int = Query(10, ge=1, le=100, description="Number of products to return"),
    sort_by: str = Query("revenue", enum=["revenue", "units", "orders"], description="Ranking metric"),
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_active_user),
):
    start, end = _parse_dates(start_date, end_date, "sales/top-products")
    try:
        return get_top_products(db, top_n, sort_by, start, end, category)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get(
    "/sales/by-category",
    response_model=CategoryRevenueResponse,
    summary="Revenue Breakdown by Product Category",
    description=(
        "Aggregates revenue, order count, and unit volume per product category "
        "with revenue share percentages for pie/bar chart rendering."
    ),
)
def sales_by_category(
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    supplier_id: Optional[int] = Query(None),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_active_user),
):
    start, end = _parse_dates(start_date, end_date, "sales/by-category")
    try:
        return get_category_revenue(db, start, end, supplier_id)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get(
    "/sales/fulfillment",
    response_model=FulfillmentStatsResponse,
    summary="Order Fulfillment & Lifecycle Statistics",
    description=(
        "Detailed fulfillment metrics: status breakdown, fulfillment rate, "
        "on-time delivery rate, SLA breach count, and average durations per lifecycle stage."
    ),
)
def fulfillment_stats(
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    supplier_id: Optional[int] = Query(None),
    category: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_active_user),
):
    start, end = _parse_dates(start_date, end_date, "sales/fulfillment")
    try:
        return get_fulfillment_stats(db, start, end, supplier_id, category)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ─────────────────────────────────────────────────────────────────────────────
#  INVENTORY REPORTS
# ─────────────────────────────────────────────────────────────────────────────

@router.get(
    "/inventory/valuation",
    response_model=InventoryValuationResponse,
    summary="Inventory Valuation Report",
    description=(
        "Current stock valuation (quantity × unit_price) per SKU, "
        "with category-level aggregation and grand totals."
    ),
)
def inventory_valuation(
    category: Optional[str] = Query(None),
    supplier_id: Optional[int] = Query(None),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_active_user),
):
    try:
        return get_inventory_valuation(db, category, supplier_id)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get(
    "/inventory/turnover",
    response_model=InventoryTurnoverResponse,
    summary="Inventory Turnover Ratio",
    description=(
        "Calculates inventory turnover ratio = units_sold / avg_stock per product. "
        "Includes days-to-sell metric. Defaults to the last 365 days."
    ),
)
def inventory_turnover(
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_active_user),
):
    start, end = _parse_dates(start_date, end_date, "inventory/turnover")
    try:
        return get_inventory_turnover(db, start, end, category)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get(
    "/inventory/aging",
    response_model=InventoryAgingResponse,
    summary="Inventory Aging Analysis",
    description=(
        "Classifies stock by days since last restock: FRESH (0–30), NORMAL (31–60), "
        "AGING (61–stale_days), STALE (>stale_days). Helps identify dead stock."
    ),
)
def inventory_aging(
    category: Optional[str] = Query(None),
    stale_days: int = Query(90, ge=30, le=365, description="Days threshold for STALE classification"),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_active_user),
):
    try:
        return get_inventory_aging(db, category, stale_days)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ─────────────────────────────────────────────────────────────────────────────
#  SUPPLIER REPORTS
# ─────────────────────────────────────────────────────────────────────────────

@router.get(
    "/suppliers/performance",
    response_model=SupplierPerformanceResponse,
    summary="Supplier Performance Comparison",
    description=(
        "Aggregated performance metrics per supplier: delivery times, SLA compliance, "
        "on-time rate, revenue, and a composite performance score (0–100)."
    ),
)
def supplier_performance(
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_active_user),
):
    start, end = _parse_dates(start_date, end_date, "suppliers/performance")
    try:
        return get_supplier_performance(db, start, end)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get(
    "/suppliers/{supplier_id}/scorecard",
    response_model=SupplierScorecardResponse,
    summary="Individual Supplier Scorecard",
    description=(
        "Deep-dive scorecard for a single supplier including performance metrics, "
        "monthly revenue trend, and top products supplied."
    ),
)
def supplier_scorecard(
    supplier_id: int,
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_active_user),
):
    start, end = _parse_dates(start_date, end_date, f"suppliers/{supplier_id}/scorecard")
    try:
        result = get_supplier_scorecard(db, supplier_id, start, end)
        if result is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Supplier {supplier_id} not found.",
            )
        return result
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ─────────────────────────────────────────────────────────────────────────────
#  FORECAST ACCURACY REPORT
# ─────────────────────────────────────────────────────────────────────────────

@router.get(
    "/forecast/accuracy",
    response_model=ForecastAccuracyResponse,
    summary="Demand Forecast Accuracy Report",
    description=(
        "Evaluates demand forecast accuracy by comparing actual delivered demand "
        "against a rolling-mean baseline from the prior equal-length period. "
        "Reports MAE, MAPE, RMSE, and accuracy % per product."
    ),
)
def forecast_accuracy(
    start_date: Optional[str] = Query(None, description="Evaluation period start (defaults to 30 days ago)"),
    end_date: Optional[str] = Query(None, description="Evaluation period end (defaults to now)"),
    category: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_active_user),
):
    start, end = _parse_dates(start_date, end_date, "forecast/accuracy")
    try:
        return get_forecast_accuracy(db, start, end, category)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ─────────────────────────────────────────────────────────────────────────────
#  OPERATIONAL REPORTS
# ─────────────────────────────────────────────────────────────────────────────

@router.get(
    "/operations/kpis",
    response_model=OperationalKPIsResponse,
    summary="Executive Operational KPI Dashboard",
    description=(
        "Single endpoint returning all critical business KPIs: "
        "sales, inventory, supplier, SLA, and bottleneck metrics. "
        "Designed for executive-level dashboards."
    ),
)
def operational_kpis(
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_active_user),
):
    start, end = _parse_dates(start_date, end_date, "operations/kpis")
    try:
        return get_operational_kpis(db, start, end)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get(
    "/operations/sla-compliance",
    response_model=SLAComplianceResponse,
    summary="SLA Compliance Report by Stage",
    description=(
        "SLA compliance analysis broken down by breach stage "
        "(procurement / processing / dispatch / delivery) with breach rates "
        "and average stage durations."
    ),
)
def sla_compliance(
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    supplier_id: Optional[int] = Query(None),
    category: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_active_user),
):
    start, end = _parse_dates(start_date, end_date, "operations/sla-compliance")
    try:
        return get_sla_compliance_report(db, start, end, supplier_id, category)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get(
    "/operations/bottlenecks",
    response_model=BottleneckReportResponse,
    summary="Bottleneck Distribution Report",
    description=(
        "Identifies supply-chain bottleneck stages with frequency counts, "
        "percentage of affected orders, and average cycle time. "
        "Supports filtering by date range, supplier, and category."
    ),
)
def bottleneck_report(
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    supplier_id: Optional[int] = Query(None),
    category: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_active_user),
):
    start, end = _parse_dates(start_date, end_date, "operations/bottlenecks")
    try:
        return get_bottleneck_report(db, start, end, supplier_id, category)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ─────────────────────────────────────────────────────────────────────────────
#  EXPORT ENDPOINTS (CSV download)
# ─────────────────────────────────────────────────────────────────────────────

@router.get(
    "/export/sales",
    summary="Export Sales Data as CSV",
    description="Downloads a CSV file of raw order data with product, supplier, lifecycle, and SLA columns.",
    response_class=StreamingResponse,
)
def export_sales(
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    order_status: Optional[str] = Query(None, alias="status"),
    supplier_id: Optional[int] = Query(None),
    category: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_active_user),
):
    start, end = _parse_dates(start_date, end_date, "export/sales")
    try:
        rows = get_sales_export_data(db, start, end, order_status, supplier_id, category)
        from datetime import datetime as _dt
        filename = f"sales_export_{_dt.utcnow().strftime('%Y%m%d_%H%M%S')}.csv"
        return _stream_csv(rows, filename)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get(
    "/export/inventory",
    summary="Export Inventory Snapshot as CSV",
    description="Downloads a CSV file of current inventory with valuation, stock levels, and restock info.",
    response_class=StreamingResponse,
)
def export_inventory(
    category: Optional[str] = Query(None),
    supplier_id: Optional[int] = Query(None),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_active_user),
):
    try:
        rows = get_inventory_export_data(db, category, supplier_id)
        from datetime import datetime as _dt
        filename = f"inventory_export_{_dt.utcnow().strftime('%Y%m%d_%H%M%S')}.csv"
        return _stream_csv(rows, filename)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get(
    "/export/suppliers",
    summary="Export Supplier Performance as CSV",
    description="Downloads a CSV of supplier performance metrics including SLA, lead times, and performance scores.",
    response_class=StreamingResponse,
)
def export_suppliers(
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_active_user),
):
    start, end = _parse_dates(start_date, end_date, "export/suppliers")
    try:
        rows = get_supplier_export_data(db, start, end)
        from datetime import datetime as _dt
        filename = f"supplier_performance_{_dt.utcnow().strftime('%Y%m%d_%H%M%S')}.csv"
        return _stream_csv(rows, filename)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
