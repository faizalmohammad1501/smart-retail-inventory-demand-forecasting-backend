"""
Dashboard & Export Summary Service
=====================================
Provides consolidated, chart-ready data for the frontend dashboard.
All aggregations run in SQL — no Python-level loops for KPI computation.
Period-over-period (PoP) comparison built into every widget.
"""

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import func, case, and_
from sqlalchemy.orm import Session

from app.models.sales import Order
from app.models.product import Product
from app.models.inventory import Inventory
from app.models.supplier import Supplier
from app.models.notification import Notification


# ─────────────────────────────────────────────────────────────────────────────
#  Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _round(v, n: int = 2) -> float:
    return round(v, n) if v is not None else 0.0


def _safe_pct(num, den, scale: float = 100.0) -> float:
    if not den:
        return 0.0
    return round(num / den * scale, 2)


def _pct_change(current: float, prior: float) -> Optional[float]:
    """Return % change from prior to current, None when prior is zero."""
    if not prior:
        return None
    return round((current - prior) / prior * 100, 2)


def _date_filter(col, start: Optional[datetime], end: Optional[datetime]) -> list:
    clauses = []
    if start:
        clauses.append(col >= start)
    if end:
        clauses.append(col <= end)
    return clauses


def _period_bounds(days: int = 30) -> tuple:
    """Return (current_start, current_end, prior_start, prior_end)."""
    end = _now()
    start = end - timedelta(days=days)
    prior_end = start
    prior_start = prior_end - timedelta(days=days)
    return start, end, prior_start, prior_end


# ─────────────────────────────────────────────────────────────────────────────
#  Sales Widget
# ─────────────────────────────────────────────────────────────────────────────

def _sales_agg(db: Session, start: datetime, end: datetime) -> Dict[str, Any]:
    row = db.query(
        func.count(Order.id).label("orders"),
        func.sum(case((Order.status == "delivered", 1), else_=0)).label("delivered"),
        func.sum(case((Order.status == "cancelled", 1), else_=0)).label("cancelled"),
        func.coalesce(func.sum(Order.total_amount), 0.0).label("revenue"),
        func.coalesce(func.avg(Order.total_amount), 0.0).label("avg_order"),
        func.coalesce(func.sum(Order.quantity), 0).label("units"),
    ).filter(*_date_filter(Order.order_placed_at, start, end)).one()

    total = row.orders or 1
    return {
        "total_orders": row.orders or 0,
        "delivered": row.delivered or 0,
        "cancelled": row.cancelled or 0,
        "total_revenue": _round(row.revenue),
        "avg_order_value": _round(row.avg_order),
        "total_units": row.units or 0,
        "fulfillment_rate": _safe_pct(row.delivered or 0, total),
    }


def get_sales_widget(db: Session, days: int = 30) -> Dict[str, Any]:
    """Sales KPIs with period-over-period comparison and 7-day trend."""
    start, end, p_start, p_end = _period_bounds(days)
    current = _sales_agg(db, start, end)
    prior = _sales_agg(db, p_start, p_end)

    # 7-day daily revenue trend for sparkline
    trend_rows = (
        db.query(
            func.strftime("%Y-%m-%d", Order.order_placed_at).label("day"),
            func.coalesce(func.sum(Order.total_amount), 0.0).label("revenue"),
            func.count(Order.id).label("orders"),
        )
        .filter(Order.order_placed_at >= (_now() - timedelta(days=7)))
        .group_by("day")
        .order_by("day")
        .all()
    )
    trend = [{"date": r.day, "revenue": _round(r.revenue), "orders": r.orders}
             for r in trend_rows]

    # Top 5 categories by revenue in period
    cat_rows = (
        db.query(
            Product.category,
            func.coalesce(func.sum(Order.total_amount), 0.0).label("revenue"),
        )
        .join(Order, Order.product_id == Product.id)
        .filter(*_date_filter(Order.order_placed_at, start, end))
        .group_by(Product.category)
        .order_by(func.sum(Order.total_amount).desc())
        .limit(5)
        .all()
    )
    top_categories = [{"category": r.category or "Uncategorized",
                        "revenue": _round(r.revenue)} for r in cat_rows]

    return {
        "period_days": days,
        "period_start": start.isoformat(),
        "period_end": end.isoformat(),
        "kpis": {
            "total_revenue": current["total_revenue"],
            "revenue_change_pct": _pct_change(current["total_revenue"], prior["total_revenue"]),
            "total_orders": current["total_orders"],
            "orders_change_pct": _pct_change(current["total_orders"], prior["total_orders"]),
            "avg_order_value": current["avg_order_value"],
            "aov_change_pct": _pct_change(current["avg_order_value"], prior["avg_order_value"]),
            "total_units_sold": current["total_units"],
            "fulfillment_rate": current["fulfillment_rate"],
            "fulfillment_change_pct": _pct_change(
                current["fulfillment_rate"], prior["fulfillment_rate"]
            ),
            "delivered_orders": current["delivered"],
            "cancelled_orders": current["cancelled"],
        },
        "prior_period": prior,
        "daily_trend_7d": trend,
        "top_categories": top_categories,
    }


# ─────────────────────────────────────────────────────────────────────────────
#  Inventory Widget
# ─────────────────────────────────────────────────────────────────────────────

def get_inventory_widget(db: Session) -> Dict[str, Any]:
    """Inventory health KPIs, valuation, and health distribution."""
    row = db.query(
        func.count(func.distinct(Inventory.product_id)).label("total_skus"),
        func.coalesce(func.sum(Inventory.quantity_available), 0).label("total_available"),
        func.coalesce(func.sum(Inventory.quantity_reserved), 0).label("total_reserved"),
        func.sum(case((Inventory.quantity_available == 0, 1), else_=0)).label("out_of_stock"),
    ).one()

    # Inventory value
    inv_value = (
        db.query(
            func.coalesce(
                func.sum(Inventory.quantity_available * Product.unit_price), 0.0
            )
        )
        .join(Product, Product.id == Inventory.product_id)
        .scalar()
    ) or 0.0

    # Products below reorder level (critical)
    critical_count = (
        db.query(func.count(Inventory.id))
        .join(Product, Product.id == Inventory.product_id)
        .filter(
            Inventory.quantity_available > 0,
            Inventory.quantity_available <= Product.reorder_level,
        )
        .scalar()
    ) or 0

    # Low stock (≤ 2× reorder level but above)
    low_count = (
        db.query(func.count(Inventory.id))
        .join(Product, Product.id == Inventory.product_id)
        .filter(
            Inventory.quantity_available > Product.reorder_level,
            Inventory.quantity_available <= (Product.reorder_level * 2),
        )
        .scalar()
    ) or 0

    total_skus = row.total_skus or 1
    out_of_stock = row.out_of_stock or 0
    healthy = max(0, total_skus - out_of_stock - critical_count - low_count)

    # Recently restocked (last 7 days)
    recently_restocked = (
        db.query(func.count(Inventory.id))
        .filter(Inventory.last_restocked >= (_now() - timedelta(days=7)))
        .scalar()
    ) or 0

    # By-warehouse breakdown
    warehouse_rows = (
        db.query(
            Inventory.warehouse_location,
            func.count(Inventory.id).label("skus"),
            func.coalesce(func.sum(Inventory.quantity_available), 0).label("qty"),
        )
        .filter(Inventory.warehouse_location.isnot(None))
        .group_by(Inventory.warehouse_location)
        .all()
    )

    return {
        "total_skus": row.total_skus or 0,
        "total_available_units": row.total_available or 0,
        "total_reserved_units": row.total_reserved or 0,
        "total_inventory_value": _round(inv_value),
        "out_of_stock_count": out_of_stock,
        "critical_stock_count": critical_count,
        "low_stock_count": low_count,
        "healthy_stock_count": healthy,
        "recently_restocked_7d": recently_restocked,
        "health_distribution": {
            "healthy": healthy,
            "low": low_count,
            "critical": critical_count,
            "out_of_stock": out_of_stock,
        },
        "by_warehouse": [
            {"location": r.warehouse_location, "skus": r.skus, "quantity": r.qty}
            for r in warehouse_rows
        ],
    }


# ─────────────────────────────────────────────────────────────────────────────
#  Supplier Widget
# ─────────────────────────────────────────────────────────────────────────────

def get_supplier_widget(db: Session, days: int = 30) -> Dict[str, Any]:
    """Supplier performance snapshot with top/worst performers."""
    start, end, _, _ = _period_bounds(days)

    rows = (
        db.query(
            Order.supplier_id,
            Supplier.supplier_name,
            Supplier.rating,
            func.count(Order.id).label("orders"),
            func.sum(case((Order.status == "delivered", 1), else_=0)).label("delivered"),
            func.coalesce(func.sum(Order.total_amount), 0.0).label("revenue"),
            func.avg(Order.total_time).label("avg_lead_time"),
            func.sum(case((Order.sla_breach == True, 1), else_=0)).label("breaches"),
            func.sum(
                case(
                    (and_(Order.status == "delivered", Order.sla_breach == False), 1),
                    else_=0,
                )
            ).label("on_time"),
        )
        .join(Supplier, Supplier.id == Order.supplier_id)
        .filter(*_date_filter(Order.order_placed_at, start, end))
        .group_by(Order.supplier_id, Supplier.supplier_name, Supplier.rating)
        .all()
    )

    supplier_list = []
    for r in rows:
        total = r.orders or 1
        delivered = r.delivered or 0
        sla_compliance = _safe_pct(total - (r.breaches or 0), total)
        on_time_rate = _safe_pct(r.on_time or 0, delivered if delivered else 1)
        # Composite score: 40% SLA + 30% on-time + 20% lead-time eff + 10% rating
        lead_eff = max(0.0, 100.0 - (_round(r.avg_lead_time or 0) / 168.0 * 100.0))
        rating_score = ((r.rating - 1) / 4.0 * 100.0) if r.rating else 50.0
        score = round(
            0.40 * sla_compliance + 0.30 * on_time_rate
            + 0.20 * lead_eff + 0.10 * rating_score, 2
        )
        supplier_list.append({
            "supplier_id": r.supplier_id,
            "supplier_name": r.supplier_name,
            "rating": r.rating,
            "total_orders": r.orders or 0,
            "total_revenue": _round(r.revenue),
            "sla_compliance_rate": sla_compliance,
            "on_time_delivery_rate": on_time_rate,
            "avg_lead_time_hours": _round(r.avg_lead_time),
            "performance_score": score,
        })

    supplier_list.sort(key=lambda x: x["performance_score"], reverse=True)

    return {
        "period_days": days,
        "total_active_suppliers": len(supplier_list),
        "avg_performance_score": _round(
            sum(s["performance_score"] for s in supplier_list) / len(supplier_list)
            if supplier_list else 0.0
        ),
        "avg_sla_compliance": _round(
            sum(s["sla_compliance_rate"] for s in supplier_list) / len(supplier_list)
            if supplier_list else 0.0
        ),
        "top_performer": supplier_list[0] if supplier_list else None,
        "worst_performer": supplier_list[-1] if len(supplier_list) > 1 else None,
        "suppliers": supplier_list,
    }


# ─────────────────────────────────────────────────────────────────────────────
#  Forecast Widget
# ─────────────────────────────────────────────────────────────────────────────

def get_forecast_widget(db: Session, days: int = 30) -> Dict[str, Any]:
    """Demand forecast accuracy snapshot using prior-period baseline comparison."""
    end = _now()
    start = end - timedelta(days=days)
    prior_start = start - timedelta(days=days)

    # Actual demand in evaluation period
    actual_q = (
        db.query(
            Order.product_id,
            func.coalesce(func.sum(Order.quantity), 0).label("actual"),
        )
        .filter(Order.status == "delivered")
        .filter(*_date_filter(Order.order_placed_at, start, end))
        .group_by(Order.product_id)
        .subquery()
    )

    # Prior-period demand as baseline
    prior_q = (
        db.query(
            Order.product_id,
            func.coalesce(func.sum(Order.quantity), 0).label("predicted"),
        )
        .filter(Order.status == "delivered")
        .filter(*_date_filter(Order.order_placed_at, prior_start, start))
        .group_by(Order.product_id)
        .subquery()
    )

    rows = (
        db.query(
            Product.id,
            func.coalesce(actual_q.c.actual, 0).label("actual"),
            func.coalesce(prior_q.c.predicted, 0).label("predicted"),
        )
        .outerjoin(actual_q, actual_q.c.product_id == Product.id)
        .outerjoin(prior_q, prior_q.c.product_id == Product.id)
        .all()
    )

    mape_sum = 0.0
    count = 0
    total_actual = 0.0
    total_predicted = 0.0

    for r in rows:
        actual = float(r.actual or 0)
        predicted = float(r.predicted or 0)
        total_actual += actual
        total_predicted += predicted
        if actual > 0:
            mape_sum += abs(actual - predicted) / actual * 100
            count += 1

    avg_mape = _round(mape_sum / count) if count else None
    avg_accuracy = _round(100.0 - avg_mape) if avg_mape is not None else None

    # Products with low stock (stockout risk)
    stockout_risk = (
        db.query(func.count(Inventory.id))
        .join(Product, Product.id == Inventory.product_id)
        .filter(Inventory.quantity_available <= Product.reorder_level)
        .scalar()
    ) or 0

    return {
        "period_days": days,
        "evaluation_start": start.isoformat(),
        "evaluation_end": end.isoformat(),
        "total_products_evaluated": count,
        "avg_mape": avg_mape,
        "avg_accuracy_pct": avg_accuracy,
        "total_actual_demand": _round(total_actual),
        "total_predicted_demand": _round(total_predicted),
        "demand_forecast_error": _round(abs(total_actual - total_predicted)),
        "products_with_stockout_risk": stockout_risk,
    }


# ─────────────────────────────────────────────────────────────────────────────
#  Alerts Widget
# ─────────────────────────────────────────────────────────────────────────────

def get_alerts_widget(db: Session) -> Dict[str, Any]:
    """Active, unread notifications summary for the dashboard alerts panel."""
    try:
        total_active = db.query(func.count(Notification.id)).filter(
            Notification.is_resolved == False
        ).scalar() or 0

        unread = db.query(func.count(Notification.id)).filter(
            Notification.is_resolved == False,
            Notification.is_read == False,
        ).scalar() or 0

        priority_rows = (
            db.query(
                Notification.priority,
                func.count(Notification.id).label("count"),
            )
            .filter(Notification.is_resolved == False)
            .group_by(Notification.priority)
            .all()
        )
        by_priority = {r.priority: r.count for r in priority_rows}

        category_rows = (
            db.query(
                Notification.category,
                func.count(Notification.id).label("count"),
            )
            .filter(Notification.is_resolved == False)
            .group_by(Notification.category)
            .all()
        )
        by_category = {r.category: r.count for r in category_rows}

        recent = (
            db.query(
                Notification.id,
                Notification.category,
                Notification.priority,
                Notification.title,
                Notification.message,
                Notification.created_at,
            )
            .filter(Notification.is_resolved == False)
            .order_by(Notification.created_at.desc())
            .limit(5)
            .all()
        )

        recent_alerts = [
            {
                "id": r.id,
                "category": r.category,
                "priority": r.priority,
                "title": r.title,
                "message": r.message,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in recent
        ]

        return {
            "total_active": total_active,
            "total_unread": unread,
            "critical": by_priority.get("CRITICAL", 0),
            "high": by_priority.get("HIGH", 0),
            "medium": by_priority.get("MEDIUM", 0),
            "low": by_priority.get("LOW", 0),
            "by_category": by_category,
            "recent_alerts": recent_alerts,
        }
    except Exception:
        return {
            "total_active": 0, "total_unread": 0,
            "critical": 0, "high": 0, "medium": 0, "low": 0,
            "by_category": {}, "recent_alerts": [],
        }


# ─────────────────────────────────────────────────────────────────────────────
#  Chart Data (Chart.js-compatible format)
# ─────────────────────────────────────────────────────────────────────────────

def get_revenue_trend_chart(
    db: Session,
    days: int = 30,
    granularity: str = "daily",
) -> Dict[str, Any]:
    """Revenue trend formatted as Chart.js line/bar dataset."""
    end = _now()
    start = end - timedelta(days=days)

    fmt_map = {"daily": "%Y-%m-%d", "weekly": "%Y-W%W", "monthly": "%Y-%m"}
    fmt = fmt_map.get(granularity, "%Y-%m-%d")

    rows = (
        db.query(
            func.strftime(fmt, Order.order_placed_at).label("period"),
            func.coalesce(func.sum(Order.total_amount), 0.0).label("revenue"),
            func.count(Order.id).label("orders"),
            func.coalesce(func.sum(Order.quantity), 0).label("units"),
        )
        .filter(*_date_filter(Order.order_placed_at, start, end))
        .group_by("period")
        .order_by("period")
        .all()
    )

    labels = [r.period for r in rows]
    return {
        "chart_type": "line",
        "title": f"Revenue Trend ({granularity.capitalize()})",
        "labels": labels,
        "datasets": [
            {
                "label": "Revenue",
                "data": [_round(r.revenue) for r in rows],
                "borderColor": "#3B82F6",
                "backgroundColor": "rgba(59,130,246,0.1)",
            },
        ],
        "secondary_datasets": [
            {
                "label": "Orders",
                "data": [r.orders for r in rows],
            },
            {
                "label": "Units Sold",
                "data": [r.units or 0 for r in rows],
            },
        ],
        "period_start": start.isoformat(),
        "period_end": end.isoformat(),
    }


def get_order_status_chart(
    db: Session,
    days: int = 30,
) -> Dict[str, Any]:
    """Order status distribution for donut/pie chart."""
    end = _now()
    start = end - timedelta(days=days)

    rows = (
        db.query(
            Order.status,
            func.count(Order.id).label("count"),
        )
        .filter(*_date_filter(Order.order_placed_at, start, end))
        .group_by(Order.status)
        .all()
    )

    status_colors = {
        "delivered": "#22C55E",
        "pending": "#F59E0B",
        "processing": "#3B82F6",
        "shipped": "#8B5CF6",
        "cancelled": "#EF4444",
    }

    labels = [r.status for r in rows]
    data = [r.count for r in rows]
    colors = [status_colors.get(r.status, "#6B7280") for r in rows]

    return {
        "chart_type": "doughnut",
        "title": "Order Status Distribution",
        "labels": labels,
        "datasets": [
            {
                "label": "Orders",
                "data": data,
                "backgroundColor": colors,
            }
        ],
        "total_orders": sum(data),
    }


def get_top_products_chart(
    db: Session,
    top_n: int = 10,
    days: int = 30,
    metric: str = "revenue",
) -> Dict[str, Any]:
    """Top products horizontal bar chart."""
    end = _now()
    start = end - timedelta(days=days)

    metric_col = {
        "revenue": func.coalesce(func.sum(Order.total_amount), 0.0),
        "units": func.coalesce(func.sum(Order.quantity), 0),
        "orders": func.count(Order.id),
    }.get(metric, func.coalesce(func.sum(Order.total_amount), 0.0))

    rows = (
        db.query(
            Product.product_name,
            Product.category,
            func.coalesce(func.sum(Order.total_amount), 0.0).label("revenue"),
            func.coalesce(func.sum(Order.quantity), 0).label("units"),
            func.count(Order.id).label("orders"),
        )
        .join(Order, Order.product_id == Product.id)
        .filter(*_date_filter(Order.order_placed_at, start, end))
        .group_by(Product.id, Product.product_name, Product.category)
        .order_by(metric_col.desc())
        .limit(top_n)
        .all()
    )

    # Reverse for horizontal bar (highest at top)
    rows_rev = list(reversed(rows))
    return {
        "chart_type": "horizontalBar",
        "title": f"Top {top_n} Products by {metric.capitalize()}",
        "labels": [r.product_name for r in rows_rev],
        "datasets": [
            {
                "label": "Revenue",
                "data": [_round(r.revenue) for r in rows_rev],
                "backgroundColor": "#3B82F6",
            },
            {
                "label": "Units Sold",
                "data": [r.units or 0 for r in rows_rev],
                "backgroundColor": "#8B5CF6",
            },
        ],
        "categories": [r.category for r in rows_rev],
        "sort_by": metric,
    }


def get_inventory_health_chart(db: Session) -> Dict[str, Any]:
    """Inventory health distribution for donut chart."""
    total_skus = db.query(func.count(Inventory.id)).scalar() or 0

    out_of_stock = (
        db.query(func.count(Inventory.id))
        .filter(Inventory.quantity_available == 0)
        .scalar()
    ) or 0

    critical = (
        db.query(func.count(Inventory.id))
        .join(Product, Product.id == Inventory.product_id)
        .filter(
            Inventory.quantity_available > 0,
            Inventory.quantity_available <= Product.reorder_level,
        )
        .scalar()
    ) or 0

    low = (
        db.query(func.count(Inventory.id))
        .join(Product, Product.id == Inventory.product_id)
        .filter(
            Inventory.quantity_available > Product.reorder_level,
            Inventory.quantity_available <= (Product.reorder_level * 2),
        )
        .scalar()
    ) or 0

    healthy = max(0, total_skus - out_of_stock - critical - low)

    return {
        "chart_type": "doughnut",
        "title": "Inventory Health Distribution",
        "labels": ["Healthy", "Low Stock", "Critical", "Out of Stock"],
        "datasets": [
            {
                "label": "SKUs",
                "data": [healthy, low, critical, out_of_stock],
                "backgroundColor": ["#22C55E", "#F59E0B", "#F97316", "#EF4444"],
            }
        ],
        "total_skus": total_skus,
        "health_score": _safe_pct(healthy, total_skus if total_skus else 1),
    }


def get_supplier_performance_chart(
    db: Session,
    days: int = 30,
    top_n: int = 10,
) -> Dict[str, Any]:
    """Supplier performance comparison bar chart."""
    end = _now()
    start = end - timedelta(days=days)

    rows = (
        db.query(
            Supplier.supplier_name,
            func.count(Order.id).label("orders"),
            func.sum(case((Order.sla_breach == True, 1), else_=0)).label("breaches"),
            func.avg(Order.total_time).label("avg_lead"),
            func.coalesce(func.sum(Order.total_amount), 0.0).label("revenue"),
        )
        .join(Order, Order.supplier_id == Supplier.id)
        .filter(*_date_filter(Order.order_placed_at, start, end))
        .group_by(Supplier.id, Supplier.supplier_name)
        .order_by(func.sum(Order.total_amount).desc())
        .limit(top_n)
        .all()
    )

    labels = [r.supplier_name for r in rows]

    return {
        "chart_type": "bar",
        "title": f"Supplier Performance — Top {top_n}",
        "labels": labels,
        "datasets": [
            {
                "label": "Revenue",
                "data": [_round(r.revenue) for r in rows],
                "backgroundColor": "#3B82F6",
                "yAxisID": "y",
            },
            {
                "label": "Avg Lead Time (hrs)",
                "data": [_round(r.avg_lead) for r in rows],
                "backgroundColor": "#F59E0B",
                "yAxisID": "y1",
            },
            {
                "label": "SLA Breaches",
                "data": [r.breaches or 0 for r in rows],
                "backgroundColor": "#EF4444",
                "yAxisID": "y1",
            },
        ],
    }


def get_category_revenue_chart(
    db: Session,
    days: int = 30,
) -> Dict[str, Any]:
    """Category revenue breakdown pie chart."""
    end = _now()
    start = end - timedelta(days=days)

    rows = (
        db.query(
            Product.category,
            func.coalesce(func.sum(Order.total_amount), 0.0).label("revenue"),
            func.count(Order.id).label("orders"),
        )
        .join(Order, Order.product_id == Product.id)
        .filter(*_date_filter(Order.order_placed_at, start, end))
        .group_by(Product.category)
        .order_by(func.sum(Order.total_amount).desc())
        .all()
    )

    colors = [
        "#3B82F6", "#8B5CF6", "#22C55E", "#F59E0B", "#EF4444",
        "#06B6D4", "#F97316", "#EC4899", "#84CC16", "#6366F1",
    ]

    labels = [r.category or "Uncategorized" for r in rows]
    total_rev = sum(r.revenue or 0 for r in rows)

    return {
        "chart_type": "pie",
        "title": "Revenue by Category",
        "labels": labels,
        "datasets": [
            {
                "label": "Revenue",
                "data": [_round(r.revenue) for r in rows],
                "backgroundColor": colors[: len(rows)],
            }
        ],
        "total_revenue": _round(total_rev),
        "share_pct": [_safe_pct(r.revenue or 0, total_rev) for r in rows],
    }


# ─────────────────────────────────────────────────────────────────────────────
#  Master Dashboard Summary
# ─────────────────────────────────────────────────────────────────────────────

def get_master_dashboard(db: Session, days: int = 30) -> Dict[str, Any]:
    """
    Single consolidated endpoint.
    Returns all widget data + chart data in one SQL-efficient call.
    Designed to power the entire frontend dashboard with a single request.
    """
    start, end, _, _ = _period_bounds(days)

    sales = get_sales_widget(db, days)
    inventory = get_inventory_widget(db)
    suppliers = get_supplier_widget(db, days)
    forecast = get_forecast_widget(db, days)
    alerts = get_alerts_widget(db)

    # Executive summary — one line per KPI for hero cards
    executive_summary = {
        "total_revenue": sales["kpis"]["total_revenue"],
        "revenue_change_pct": sales["kpis"]["revenue_change_pct"],
        "total_orders": sales["kpis"]["total_orders"],
        "orders_change_pct": sales["kpis"]["orders_change_pct"],
        "fulfillment_rate": sales["kpis"]["fulfillment_rate"],
        "inventory_value": inventory["total_inventory_value"],
        "out_of_stock_count": inventory["out_of_stock_count"],
        "active_alerts": alerts["total_active"],
        "critical_alerts": alerts["critical"],
        "avg_supplier_score": suppliers["avg_performance_score"],
        "avg_forecast_accuracy": forecast["avg_accuracy_pct"],
        "avg_sla_compliance": suppliers["avg_sla_compliance"],
    }

    return {
        "generated_at": _now().isoformat(),
        "period_days": days,
        "period_start": start.isoformat(),
        "period_end": end.isoformat(),
        "executive_summary": executive_summary,
        "widgets": {
            "sales": sales,
            "inventory": inventory,
            "suppliers": suppliers,
            "forecast": forecast,
            "alerts": alerts,
        },
        "charts": {
            "revenue_trend": get_revenue_trend_chart(db, days),
            "order_status": get_order_status_chart(db, days),
            "top_products": get_top_products_chart(db, days=days),
            "inventory_health": get_inventory_health_chart(db),
            "supplier_performance": get_supplier_performance_chart(db, days),
            "category_revenue": get_category_revenue_chart(db, days),
        },
    }


# ─────────────────────────────────────────────────────────────────────────────
#  Export data helpers (used by export endpoints)
# ─────────────────────────────────────────────────────────────────────────────

def get_forecast_export_data(
    db: Session,
    days: int = 30,
) -> List[Dict[str, Any]]:
    """Product-level forecast accuracy rows for CSV / PDF export."""
    end = _now()
    start = end - timedelta(days=days)
    prior_start = start - timedelta(days=days)

    actual_q = (
        db.query(
            Order.product_id,
            func.coalesce(func.sum(Order.quantity), 0).label("actual"),
        )
        .filter(Order.status == "delivered")
        .filter(*_date_filter(Order.order_placed_at, start, end))
        .group_by(Order.product_id)
        .subquery()
    )

    prior_q = (
        db.query(
            Order.product_id,
            func.coalesce(func.sum(Order.quantity), 0).label("predicted"),
        )
        .filter(Order.status == "delivered")
        .filter(*_date_filter(Order.order_placed_at, prior_start, start))
        .group_by(Order.product_id)
        .subquery()
    )

    rows = (
        db.query(
            Product.id.label("product_id"),
            Product.product_name,
            Product.sku,
            Product.category,
            func.coalesce(actual_q.c.actual, 0).label("actual_demand"),
            func.coalesce(prior_q.c.predicted, 0).label("predicted_demand"),
        )
        .outerjoin(actual_q, actual_q.c.product_id == Product.id)
        .outerjoin(prior_q, prior_q.c.product_id == Product.id)
        .all()
    )

    result = []
    for r in rows:
        actual = float(r.actual_demand or 0)
        predicted = float(r.predicted_demand or 0)
        error = abs(actual - predicted)
        mape = _round(error / actual * 100) if actual > 0 else 0.0
        result.append({
            "product_id": r.product_id,
            "product_name": r.product_name,
            "sku": r.sku,
            "category": r.category,
            "actual_demand": actual,
            "predicted_demand": predicted,
            "absolute_error": _round(error),
            "mape_pct": mape,
            "accuracy_pct": _round(max(0.0, 100.0 - mape)),
        })

    result.sort(key=lambda x: x["mape_pct"])
    return result


def get_notifications_export_data(db: Session) -> List[Dict[str, Any]]:
    """All active notifications for CSV export."""
    try:
        rows = (
            db.query(Notification)
            .filter(Notification.is_resolved == False)
            .order_by(Notification.created_at.desc())
            .all()
        )
        return [
            {
                "id": r.id,
                "category": r.category,
                "priority": r.priority,
                "title": r.title,
                "message": r.message,
                "product_id": r.product_id,
                "product_name": r.product_name,
                "supplier_id": r.supplier_id,
                "metric_value": r.metric_value,
                "metric_label": r.metric_label,
                "is_read": r.is_read,
                "created_at": r.created_at.isoformat() if r.created_at else "",
            }
            for r in rows
        ]
    except Exception:
        return []


def get_full_report_data(db: Session, days: int = 30) -> Dict[str, Any]:
    """All-in-one structured data bundle for PDF generation."""
    from app.services.reporting_service import (
        get_sales_summary,
        get_revenue_trends,
        get_top_products,
        get_category_revenue,
        get_fulfillment_stats,
        get_inventory_valuation,
        get_supplier_performance,
        get_operational_kpis,
    )
    end = _now()
    start = end - timedelta(days=days)

    return {
        "generated_at": _now().isoformat(),
        "period_days": days,
        "period_start": start.isoformat(),
        "period_end": end.isoformat(),
        "sales_summary": get_sales_summary(db, start, end),
        "revenue_trends": get_revenue_trends(db, "monthly", start, end),
        "top_products": get_top_products(db, top_n=10, start=start, end=end),
        "category_revenue": get_category_revenue(db, start, end),
        "fulfillment_stats": get_fulfillment_stats(db, start, end),
        "inventory_valuation": get_inventory_valuation(db),
        "supplier_performance": get_supplier_performance(db, start, end),
        "operational_kpis": get_operational_kpis(db, start, end),
        "forecast_accuracy": get_forecast_export_data(db, days),
    }
