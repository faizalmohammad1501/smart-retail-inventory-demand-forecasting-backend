"""
Dashboard & Export API Routes
================================
Endpoints for consolidated dashboard widgets, chart data, and downloadable exports
(CSV and PDF). Designed as the primary data source for the frontend dashboard.
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
from app.services.dashboard_service import (
    get_master_dashboard,
    get_sales_widget,
    get_inventory_widget,
    get_supplier_widget,
    get_forecast_widget,
    get_alerts_widget,
    get_revenue_trend_chart,
    get_order_status_chart,
    get_top_products_chart,
    get_inventory_health_chart,
    get_supplier_performance_chart,
    get_category_revenue_chart,
    get_forecast_export_data,
    get_notifications_export_data,
    get_full_report_data,
)

router = APIRouter(prefix="/api/dashboard", tags=["Dashboard & Exports"])


# ─────────────────────────────────────────────────────────────────────────────
#  Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _validate_days(days: int) -> int:
    if days < 1 or days > 365:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="days must be between 1 and 365.",
        )
    return days


def _stream_csv(rows: list, filename: str) -> StreamingResponse:
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


def _stream_pdf(pdf_bytes: bytes, filename: str) -> StreamingResponse:
    return StreamingResponse(
        iter([pdf_bytes]),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _ts() -> str:
    return datetime.utcnow().strftime("%Y%m%d_%H%M%S")


# ─────────────────────────────────────────────────────────────────────────────
#  MASTER DASHBOARD
# ─────────────────────────────────────────────────────────────────────────────

@router.get(
    "/summary",
    summary="Master Dashboard Summary",
    description=(
        "Single consolidated endpoint returning all dashboard data: "
        "executive KPIs, sales/inventory/supplier/forecast/alerts widgets, "
        "and six Chart.js-ready chart datasets. "
        "Powers the entire frontend dashboard with one API call."
    ),
)
def master_dashboard(
    days: int = Query(30, ge=1, le=365, description="Rolling window in days"),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_active_user),
):
    try:
        return get_master_dashboard(db, days)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ─────────────────────────────────────────────────────────────────────────────
#  INDIVIDUAL WIDGETS
# ─────────────────────────────────────────────────────────────────────────────

@router.get(
    "/widgets/sales",
    summary="Sales Widget Data",
    description=(
        "Sales KPIs with period-over-period % change, 7-day daily revenue sparkline, "
        "and top 5 categories. Suitable for the sales summary card."
    ),
)
def sales_widget(
    days: int = Query(30, ge=1, le=365),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_active_user),
):
    try:
        return get_sales_widget(db, days)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get(
    "/widgets/inventory",
    summary="Inventory Widget Data",
    description=(
        "Inventory health KPIs: total SKUs, inventory value, out-of-stock/critical/low counts, "
        "health distribution, warehouse breakdown, and recently restocked count."
    ),
)
def inventory_widget(
    db: Session = Depends(get_db),
    _: User = Depends(get_current_active_user),
):
    try:
        return get_inventory_widget(db)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get(
    "/widgets/suppliers",
    summary="Supplier Widget Data",
    description=(
        "Supplier performance snapshot: active count, avg performance score, "
        "top and worst performer cards, SLA compliance average."
    ),
)
def supplier_widget(
    days: int = Query(30, ge=1, le=365),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_active_user),
):
    try:
        return get_supplier_widget(db, days)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get(
    "/widgets/forecast",
    summary="Forecast Accuracy Widget Data",
    description=(
        "Demand forecast accuracy metrics: avg MAPE, avg accuracy %, "
        "total predicted demand, and products at stockout risk."
    ),
)
def forecast_widget(
    days: int = Query(30, ge=1, le=365),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_active_user),
):
    try:
        return get_forecast_widget(db, days)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get(
    "/widgets/alerts",
    summary="Alerts & Notifications Widget Data",
    description=(
        "Active notification summary: total active, unread count, breakdown by priority "
        "and category, plus the 5 most recent alerts."
    ),
)
def alerts_widget(
    db: Session = Depends(get_db),
    _: User = Depends(get_current_active_user),
):
    try:
        return get_alerts_widget(db)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ─────────────────────────────────────────────────────────────────────────────
#  CHART DATA ENDPOINTS
# ─────────────────────────────────────────────────────────────────────────────

@router.get(
    "/charts/revenue-trend",
    summary="Revenue Trend Chart Data",
    description=(
        "Chart.js-compatible line chart dataset for revenue over time. "
        "Granularity: daily / weekly / monthly. Includes orders and units sold datasets."
    ),
)
def chart_revenue_trend(
    days: int = Query(30, ge=7, le=365),
    granularity: str = Query("daily", enum=["daily", "weekly", "monthly"]),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_active_user),
):
    try:
        return get_revenue_trend_chart(db, days, granularity)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get(
    "/charts/order-status",
    summary="Order Status Distribution Chart Data",
    description=(
        "Chart.js doughnut chart dataset showing order status distribution "
        "(delivered, pending, processing, shipped, cancelled) with branded colors."
    ),
)
def chart_order_status(
    days: int = Query(30, ge=1, le=365),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_active_user),
):
    try:
        return get_order_status_chart(db, days)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get(
    "/charts/top-products",
    summary="Top Products Chart Data",
    description=(
        "Chart.js horizontal bar chart for top-N products. "
        "Metric options: revenue (default), units, orders."
    ),
)
def chart_top_products(
    top_n: int = Query(10, ge=3, le=25),
    days: int = Query(30, ge=1, le=365),
    metric: str = Query("revenue", enum=["revenue", "units", "orders"]),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_active_user),
):
    try:
        return get_top_products_chart(db, top_n, days, metric)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get(
    "/charts/inventory-health",
    summary="Inventory Health Donut Chart Data",
    description=(
        "Chart.js doughnut chart dataset for inventory health distribution: "
        "Healthy / Low / Critical / Out-of-Stock with health score."
    ),
)
def chart_inventory_health(
    db: Session = Depends(get_db),
    _: User = Depends(get_current_active_user),
):
    try:
        return get_inventory_health_chart(db)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get(
    "/charts/supplier-performance",
    summary="Supplier Performance Bar Chart Data",
    description=(
        "Multi-axis Chart.js bar chart comparing top suppliers on "
        "revenue, average lead time, and SLA breach count."
    ),
)
def chart_supplier_performance(
    top_n: int = Query(10, ge=3, le=20),
    days: int = Query(30, ge=1, le=365),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_active_user),
):
    try:
        return get_supplier_performance_chart(db, days, top_n)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get(
    "/charts/category-revenue",
    summary="Category Revenue Pie Chart Data",
    description=(
        "Chart.js pie chart dataset for revenue share by product category, "
        "including total revenue and per-category share percentages."
    ),
)
def chart_category_revenue(
    days: int = Query(30, ge=1, le=365),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_active_user),
):
    try:
        return get_category_revenue_chart(db, days)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ─────────────────────────────────────────────────────────────────────────────
#  CSV EXPORTS
# ─────────────────────────────────────────────────────────────────────────────

@router.get(
    "/export/csv/forecast-accuracy",
    summary="Export Forecast Accuracy as CSV",
    description="Downloads a CSV of product-level demand forecast accuracy metrics (MAE, MAPE, RMSE, accuracy %).",
    response_class=StreamingResponse,
)
def export_csv_forecast(
    days: int = Query(30, ge=1, le=365),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_active_user),
):
    try:
        rows = get_forecast_export_data(db, days)
        return _stream_csv(rows, f"forecast_accuracy_{_ts()}.csv")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get(
    "/export/csv/notifications",
    summary="Export Active Notifications as CSV",
    description="Downloads a CSV of all active (unresolved) notifications and alerts.",
    response_class=StreamingResponse,
)
def export_csv_notifications(
    db: Session = Depends(get_db),
    _: User = Depends(get_current_active_user),
):
    try:
        rows = get_notifications_export_data(db)
        return _stream_csv(rows, f"notifications_{_ts()}.csv")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get(
    "/export/csv/full-report",
    summary="Export Full Business Report as CSV (Sales)",
    description=(
        "Downloads a comprehensive CSV bundle of sales, revenue trends, "
        "top products, and category breakdown for the selected period."
    ),
    response_class=StreamingResponse,
)
def export_csv_full_report(
    days: int = Query(30, ge=1, le=365),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_active_user),
):
    try:
        data = get_full_report_data(db, days)
        # Flatten revenue trend rows for CSV
        trends = data.get("revenue_trends", {}).get("data", [])
        top_products = data.get("top_products", {}).get("products", [])
        # Combine into one flat sheet (trends + products sections, labelled)
        rows = []
        for t in trends:
            rows.append({
                "section": "Revenue Trend",
                "period": t.get("period", ""),
                "name": "",
                "category": "",
                "revenue": t.get("revenue", 0),
                "orders": t.get("orders", 0),
                "units": t.get("units_sold", 0),
                "fulfillment_rate": t.get("fulfillment_rate", 0),
                "share_pct": "",
            })
        for p in top_products:
            rows.append({
                "section": "Top Products",
                "period": "",
                "name": p.get("product_name", ""),
                "category": p.get("category", ""),
                "revenue": p.get("total_revenue", 0),
                "orders": p.get("total_orders", 0),
                "units": p.get("total_units_sold", 0),
                "fulfillment_rate": "",
                "share_pct": p.get("revenue_share_pct", 0),
            })
        return _stream_csv(rows, f"full_report_{_ts()}.csv")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ─────────────────────────────────────────────────────────────────────────────
#  PDF EXPORTS
# ─────────────────────────────────────────────────────────────────────────────

@router.get(
    "/export/pdf/sales",
    summary="Export Sales Report as PDF",
    description=(
        "Downloads a branded PDF sales report containing executive KPIs, "
        "monthly revenue trend, top 10 products table, and category breakdown."
    ),
    response_class=StreamingResponse,
)
def export_pdf_sales(
    days: int = Query(30, ge=1, le=365),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_active_user),
):
    try:
        from app.services.export_pdf_service import build_sales_pdf
        data = get_full_report_data(db, days)
        pdf_bytes = build_sales_pdf(data)
        return _stream_pdf(pdf_bytes, f"sales_report_{_ts()}.pdf")
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail=str(exc),
        ) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get(
    "/export/pdf/inventory",
    summary="Export Inventory Report as PDF",
    description=(
        "Downloads a branded PDF inventory report with valuation summary, "
        "category breakdown, and per-SKU detail (top 50 by value)."
    ),
    response_class=StreamingResponse,
)
def export_pdf_inventory(
    db: Session = Depends(get_db),
    _: User = Depends(get_current_active_user),
):
    try:
        from app.services.export_pdf_service import build_inventory_pdf
        data = get_full_report_data(db, days=30)
        pdf_bytes = build_inventory_pdf(data)
        return _stream_pdf(pdf_bytes, f"inventory_report_{_ts()}.pdf")
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail=str(exc),
        ) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get(
    "/export/pdf/suppliers",
    summary="Export Supplier Performance Report as PDF",
    description=(
        "Downloads a branded PDF supplier report with overall metrics "
        "and a full supplier scorecard table."
    ),
    response_class=StreamingResponse,
)
def export_pdf_suppliers(
    days: int = Query(30, ge=1, le=365),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_active_user),
):
    try:
        from app.services.export_pdf_service import build_supplier_pdf
        data = get_full_report_data(db, days)
        pdf_bytes = build_supplier_pdf(data)
        return _stream_pdf(pdf_bytes, f"supplier_report_{_ts()}.pdf")
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail=str(exc),
        ) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get(
    "/export/pdf/executive",
    summary="Export Full Executive Report as PDF",
    description=(
        "Downloads a comprehensive branded PDF with executive KPIs, "
        "sales summary, top products, supplier scorecard, "
        "and demand forecast accuracy — all in one document."
    ),
    response_class=StreamingResponse,
)
def export_pdf_executive(
    days: int = Query(30, ge=1, le=365),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_active_user),
):
    try:
        from app.services.export_pdf_service import build_executive_pdf
        data = get_full_report_data(db, days)
        pdf_bytes = build_executive_pdf(data)
        return _stream_pdf(pdf_bytes, f"executive_report_{_ts()}.pdf")
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail=str(exc),
        ) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
