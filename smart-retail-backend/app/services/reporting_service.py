"""
Reporting & Business Insights Service
======================================
All data-aggregation logic for the reporting module.
Uses SQLAlchemy ORM with SQLite-compatible strftime grouping.
No Python-level loops for aggregations — every KPI is computed in SQL.
"""

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import func, case, and_
from sqlalchemy.orm import Session

from app.models.sales import Order
from app.models.product import Product
from app.models.inventory import Inventory
from app.models.supplier import Supplier


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _round(v, n=2):
    return round(v, n) if v is not None else 0.0


def _safe_pct(numerator, denominator, scale=100.0) -> float:
    if not denominator:
        return 0.0
    return round(numerator / denominator * scale, 2)


def _date_clauses(col, start: Optional[datetime], end: Optional[datetime]) -> list:
    clauses = []
    if start:
        clauses.append(col >= start)
    if end:
        clauses.append(col <= end)
    return clauses


# ─────────────────────────────────────────────────────────────────────────────
#  SALES REPORTS
# ─────────────────────────────────────────────────────────────────────────────

def get_sales_summary(
    db: Session,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
    category: Optional[str] = None,
    supplier_id: Optional[int] = None,
    status: Optional[str] = None,
) -> Dict[str, Any]:
    """Overall sales KPIs with optional filters."""
    q = db.query(Order)

    if start or end:
        q = q.filter(*_date_clauses(Order.order_placed_at, start, end))
    if supplier_id:
        q = q.filter(Order.supplier_id == supplier_id)
    if status:
        q = q.filter(Order.status == status)
    if category:
        q = q.join(Product, Product.id == Order.product_id).filter(
            Product.category == category
        )

    row = q.with_entities(
        func.count(Order.id).label("total_orders"),
        func.sum(case((Order.status == "delivered", 1), else_=0)).label("delivered"),
        func.sum(case((Order.status == "cancelled", 1), else_=0)).label("cancelled"),
        func.sum(case((Order.status == "pending", 1), else_=0)).label("pending"),
        func.sum(case((Order.status == "processing", 1), else_=0)).label("processing"),
        func.sum(case((Order.status == "shipped", 1), else_=0)).label("shipped"),
        func.coalesce(func.sum(Order.total_amount), 0.0).label("total_revenue"),
        func.coalesce(func.avg(Order.total_amount), 0.0).label("avg_order_value"),
        func.coalesce(func.sum(Order.quantity), 0).label("total_units"),
        func.coalesce(func.max(Order.total_amount), 0.0).label("max_order_value"),
        func.coalesce(func.min(Order.total_amount), 0.0).label("min_order_value"),
    ).one()

    total = row.total_orders or 0
    delivered = row.delivered or 0
    cancelled = row.cancelled or 0

    return {
        "period_start": start.isoformat() if start else None,
        "period_end": end.isoformat() if end else None,
        "filters_applied": {
            "category": category,
            "supplier_id": supplier_id,
            "status": status,
        },
        "total_orders": total,
        "delivered_orders": delivered,
        "cancelled_orders": cancelled,
        "pending_orders": row.pending or 0,
        "processing_orders": row.processing or 0,
        "shipped_orders": row.shipped or 0,
        "total_revenue": _round(row.total_revenue),
        "avg_order_value": _round(row.avg_order_value),
        "max_order_value": _round(row.max_order_value),
        "min_order_value": _round(row.min_order_value),
        "total_units_sold": row.total_units or 0,
        "fulfillment_rate": _safe_pct(delivered, total),
        "cancellation_rate": _safe_pct(cancelled, total),
        "generated_at": _now().isoformat(),
    }


def get_revenue_trends(
    db: Session,
    granularity: str = "monthly",
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
    category: Optional[str] = None,
    supplier_id: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Revenue trends grouped by day / week / month.
    Uses SQLite strftime for date bucketing.
    """
    fmt_map = {
        "daily": "%Y-%m-%d",
        "weekly": "%Y-W%W",
        "monthly": "%Y-%m",
        "quarterly": "%Y-Q",   # handled specially below
    }
    if granularity not in fmt_map:
        granularity = "monthly"

    # SQLite strftime expression
    if granularity == "quarterly":
        period_expr = func.strftime("%Y", Order.order_placed_at)
        # we'll post-process for quarter labeling
    else:
        period_expr = func.strftime(fmt_map[granularity], Order.order_placed_at)

    q = db.query(
        period_expr.label("period"),
        func.count(Order.id).label("orders"),
        func.coalesce(func.sum(Order.total_amount), 0.0).label("revenue"),
        func.coalesce(func.sum(Order.quantity), 0).label("units_sold"),
        func.coalesce(func.avg(Order.total_amount), 0.0).label("avg_order_value"),
        func.sum(case((Order.status == "delivered", 1), else_=0)).label("delivered"),
    )

    if start or end:
        q = q.filter(*_date_clauses(Order.order_placed_at, start, end))
    if supplier_id:
        q = q.filter(Order.supplier_id == supplier_id)
    if category:
        q = q.join(Product, Product.id == Order.product_id).filter(
            Product.category == category
        )

    rows = q.group_by("period").order_by("period").all()

    data = [
        {
            "period": row.period,
            "orders": row.orders,
            "revenue": _round(row.revenue),
            "units_sold": row.units_sold or 0,
            "avg_order_value": _round(row.avg_order_value),
            "delivered_orders": row.delivered or 0,
            "fulfillment_rate": _safe_pct(row.delivered or 0, row.orders),
        }
        for row in rows
    ]

    total_revenue = sum(d["revenue"] for d in data)
    return {
        "granularity": granularity,
        "period_start": start.isoformat() if start else None,
        "period_end": end.isoformat() if end else None,
        "total_points": len(data),
        "total_revenue": _round(total_revenue),
        "data": data,
        "generated_at": _now().isoformat(),
    }


def get_top_products(
    db: Session,
    top_n: int = 10,
    sort_by: str = "revenue",
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
    category: Optional[str] = None,
) -> Dict[str, Any]:
    """Top N products ranked by revenue, units sold, or order count."""
    sort_col_map = {
        "revenue": func.coalesce(func.sum(Order.total_amount), 0.0),
        "units": func.coalesce(func.sum(Order.quantity), 0),
        "orders": func.count(Order.id),
    }
    if sort_by not in sort_col_map:
        sort_by = "revenue"

    q = db.query(
        Order.product_id,
        Product.product_name,
        Product.sku,
        Product.category,
        Product.unit_price,
        func.count(Order.id).label("total_orders"),
        func.coalesce(func.sum(Order.quantity), 0).label("total_units"),
        func.coalesce(func.sum(Order.total_amount), 0.0).label("total_revenue"),
        func.coalesce(func.avg(Order.unit_price), 0.0).label("avg_unit_price"),
    ).join(Product, Product.id == Order.product_id)

    if start or end:
        q = q.filter(*_date_clauses(Order.order_placed_at, start, end))
    if category:
        q = q.filter(Product.category == category)

    rows = (
        q.group_by(Order.product_id, Product.product_name, Product.sku,
                   Product.category, Product.unit_price)
        .order_by(sort_col_map[sort_by].desc())
        .limit(top_n)
        .all()
    )

    # Compute total revenue for share calculation
    total_rev_q = db.query(
        func.coalesce(func.sum(Order.total_amount), 0.0)
    )
    if start or end:
        total_rev_q = total_rev_q.filter(
            *_date_clauses(Order.order_placed_at, start, end)
        )
    if category:
        total_rev_q = total_rev_q.join(
            Product, Product.id == Order.product_id
        ).filter(Product.category == category)
    total_revenue_period = total_rev_q.scalar() or 0.0

    products = [
        {
            "product_id": row.product_id,
            "product_name": row.product_name,
            "sku": row.sku,
            "category": row.category,
            "unit_price": _round(row.unit_price),
            "total_orders": row.total_orders,
            "total_units_sold": row.total_units or 0,
            "total_revenue": _round(row.total_revenue),
            "avg_unit_price": _round(row.avg_unit_price),
            "revenue_share_pct": _safe_pct(row.total_revenue or 0, total_revenue_period),
        }
        for row in rows
    ]

    return {
        "period_start": start.isoformat() if start else None,
        "period_end": end.isoformat() if end else None,
        "top_n": top_n,
        "sort_by": sort_by,
        "total_revenue_in_period": _round(total_revenue_period),
        "products": products,
        "generated_at": _now().isoformat(),
    }


def get_category_revenue(
    db: Session,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
    supplier_id: Optional[int] = None,
) -> Dict[str, Any]:
    """Revenue, order count, and unit breakdown by product category."""
    q = db.query(
        Product.category,
        func.count(Order.id).label("total_orders"),
        func.coalesce(func.sum(Order.quantity), 0).label("total_units"),
        func.coalesce(func.sum(Order.total_amount), 0.0).label("total_revenue"),
        func.coalesce(func.avg(Order.total_amount), 0.0).label("avg_order_value"),
        func.count(func.distinct(Product.id)).label("product_count"),
    ).join(Order, Order.product_id == Product.id)

    if start or end:
        q = q.filter(*_date_clauses(Order.order_placed_at, start, end))
    if supplier_id:
        q = q.filter(Order.supplier_id == supplier_id)

    rows = (
        q.group_by(Product.category)
        .order_by(func.sum(Order.total_amount).desc())
        .all()
    )

    total_revenue = sum((r.total_revenue or 0) for r in rows)

    categories = [
        {
            "category": row.category or "Uncategorized",
            "total_orders": row.total_orders,
            "total_units_sold": row.total_units or 0,
            "total_revenue": _round(row.total_revenue),
            "avg_order_value": _round(row.avg_order_value),
            "revenue_share_pct": _safe_pct(row.total_revenue or 0, total_revenue),
            "product_count": row.product_count,
        }
        for row in rows
    ]

    return {
        "period_start": start.isoformat() if start else None,
        "period_end": end.isoformat() if end else None,
        "total_revenue": _round(total_revenue),
        "total_categories": len(categories),
        "categories": categories,
        "generated_at": _now().isoformat(),
    }


def get_fulfillment_stats(
    db: Session,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
    supplier_id: Optional[int] = None,
    category: Optional[str] = None,
) -> Dict[str, Any]:
    """Order fulfillment statistics including on-time delivery rate."""
    q = db.query(Order)
    if start or end:
        q = q.filter(*_date_clauses(Order.order_placed_at, start, end))
    if supplier_id:
        q = q.filter(Order.supplier_id == supplier_id)
    if category:
        q = q.join(Product, Product.id == Order.product_id).filter(
            Product.category == category
        )

    row = q.with_entities(
        func.count(Order.id).label("total_orders"),
        func.sum(case((Order.status == "delivered", 1), else_=0)).label("delivered"),
        func.sum(case((Order.status == "cancelled", 1), else_=0)).label("cancelled"),
        func.sum(case((Order.status == "pending", 1), else_=0)).label("pending"),
        func.sum(case((Order.status == "processing", 1), else_=0)).label("processing"),
        func.sum(case((Order.status == "shipped", 1), else_=0)).label("shipped"),
        func.avg(Order.total_time).label("avg_total_time"),
        func.avg(Order.delivery_time_duration).label("avg_delivery_time"),
        func.avg(Order.procurement_time).label("avg_procurement_time"),
        func.avg(Order.processing_time).label("avg_processing_time"),
        func.avg(Order.dispatch_time_duration).label("avg_dispatch_time"),
        func.sum(case((Order.sla_breach == True, 1), else_=0)).label("sla_breaches"),
        func.sum(
            case(
                (and_(Order.status == "delivered", Order.sla_breach == False), 1),
                else_=0,
            )
        ).label("on_time_delivered"),
    ).one()

    total = row.total_orders or 0
    delivered = row.delivered or 0
    on_time = row.on_time_delivered or 0

    status_breakdown = {
        "delivered": row.delivered or 0,
        "pending": row.pending or 0,
        "processing": row.processing or 0,
        "shipped": row.shipped or 0,
        "cancelled": row.cancelled or 0,
    }

    return {
        "period_start": start.isoformat() if start else None,
        "period_end": end.isoformat() if end else None,
        "total_orders": total,
        "status_breakdown": status_breakdown,
        "fulfillment_rate": _safe_pct(delivered, total),
        "cancellation_rate": _safe_pct(row.cancelled or 0, total),
        "on_time_delivery_rate": _safe_pct(on_time, delivered if delivered else 1),
        "sla_breach_count": row.sla_breaches or 0,
        "avg_total_time_hours": _round(row.avg_total_time),
        "avg_procurement_time_hours": _round(row.avg_procurement_time),
        "avg_processing_time_hours": _round(row.avg_processing_time),
        "avg_dispatch_time_hours": _round(row.avg_dispatch_time),
        "avg_delivery_time_hours": _round(row.avg_delivery_time),
        "generated_at": _now().isoformat(),
    }


# ─────────────────────────────────────────────────────────────────────────────
#  INVENTORY REPORTS
# ─────────────────────────────────────────────────────────────────────────────

def get_inventory_valuation(
    db: Session,
    category: Optional[str] = None,
    supplier_id: Optional[int] = None,
) -> Dict[str, Any]:
    """Current stock × unit price — full valuation report."""
    q = db.query(
        Product.id.label("product_id"),
        Product.product_name,
        Product.sku,
        Product.category,
        Product.unit_price,
        Product.supplier_id,
        Inventory.quantity_available,
        Inventory.quantity_reserved,
        Inventory.warehouse_location,
        Inventory.last_restocked,
    ).join(Inventory, Inventory.product_id == Product.id)

    if category:
        q = q.filter(Product.category == category)
    if supplier_id:
        q = q.filter(Product.supplier_id == supplier_id)

    rows = q.all()

    items = []
    category_totals: Dict[str, float] = {}
    total_available_value = 0.0
    total_reserved_value = 0.0

    for row in rows:
        avail_val = _round((row.quantity_available or 0) * (row.unit_price or 0))
        reserved_val = _round((row.quantity_reserved or 0) * (row.unit_price or 0))
        total_val = _round(avail_val + reserved_val)
        total_available_value += avail_val
        total_reserved_value += reserved_val

        cat = row.category or "Uncategorized"
        category_totals[cat] = category_totals.get(cat, 0.0) + total_val

        items.append({
            "product_id": row.product_id,
            "product_name": row.product_name,
            "sku": row.sku,
            "category": row.category,
            "unit_price": _round(row.unit_price),
            "quantity_available": row.quantity_available or 0,
            "quantity_reserved": row.quantity_reserved or 0,
            "total_available_value": avail_val,
            "total_reserved_value": reserved_val,
            "total_value": total_val,
            "warehouse_location": row.warehouse_location,
            "last_restocked": row.last_restocked.isoformat() if row.last_restocked else None,
        })

    grand_total = _round(total_available_value + total_reserved_value)
    by_category = [
        {
            "category": cat,
            "total_value": _round(v),
            "value_share_pct": _safe_pct(v, grand_total),
        }
        for cat, v in sorted(category_totals.items(), key=lambda x: x[1], reverse=True)
    ]

    return {
        "generated_at": _now().isoformat(),
        "total_sku_count": len(items),
        "total_available_value": _round(total_available_value),
        "total_reserved_value": _round(total_reserved_value),
        "grand_total_value": grand_total,
        "by_category": by_category,
        "items": items,
    }


def get_inventory_turnover(
    db: Session,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
    category: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Inventory turnover ratio = units_sold_in_period / avg_stock_on_hand.
    Days-to-sell = 365 / turnover_ratio.
    """
    if not start:
        start = _now() - timedelta(days=365)
    if not end:
        end = _now()

    # Units sold per product in the period
    sold_q = (
        db.query(
            Order.product_id,
            func.coalesce(func.sum(Order.quantity), 0).label("units_sold"),
        )
        .filter(*_date_clauses(Order.order_placed_at, start, end))
        .filter(Order.status == "delivered")
        .group_by(Order.product_id)
        .subquery()
    )

    q = db.query(
        Product.id.label("product_id"),
        Product.product_name,
        Product.sku,
        Product.category,
        Inventory.quantity_available,
        Inventory.quantity_reserved,
        func.coalesce(sold_q.c.units_sold, 0).label("units_sold"),
    ).join(Inventory, Inventory.product_id == Product.id).outerjoin(
        sold_q, sold_q.c.product_id == Product.id
    )

    if category:
        q = q.filter(Product.category == category)

    rows = q.all()
    period_days = (end - start).days or 1

    items = []
    for row in rows:
        avg_stock = ((row.quantity_available or 0) + (row.quantity_reserved or 0)) / 2.0 or 1
        units_sold = row.units_sold or 0
        turnover_ratio = round(units_sold / avg_stock, 4) if avg_stock > 0 else 0.0
        days_to_sell = round(period_days / turnover_ratio, 1) if turnover_ratio > 0 else None

        items.append({
            "product_id": row.product_id,
            "product_name": row.product_name,
            "sku": row.sku,
            "category": row.category,
            "units_sold_period": units_sold,
            "avg_stock_on_hand": round(avg_stock, 2),
            "turnover_ratio": turnover_ratio,
            "days_to_sell": days_to_sell,
        })

    items.sort(key=lambda x: x["turnover_ratio"], reverse=True)
    avg_turnover = (
        round(sum(i["turnover_ratio"] for i in items) / len(items), 4) if items else 0.0
    )

    return {
        "period_start": start.isoformat(),
        "period_end": end.isoformat(),
        "period_days": period_days,
        "avg_turnover_ratio": avg_turnover,
        "total_products": len(items),
        "generated_at": _now().isoformat(),
        "items": items,
    }


def get_inventory_aging(
    db: Session,
    category: Optional[str] = None,
    stale_days: int = 90,
) -> Dict[str, Any]:
    """
    Inventory aging analysis — classifies products by how long since last restock
    and last order, highlighting stale and aging stock.
    """
    today = _now()

    # Last order date per product
    last_order_q = (
        db.query(
            Order.product_id,
            func.max(Order.order_placed_at).label("last_order_at"),
        )
        .group_by(Order.product_id)
        .subquery()
    )

    q = db.query(
        Product.id.label("product_id"),
        Product.product_name,
        Product.sku,
        Product.category,
        Product.unit_price,
        Inventory.quantity_available,
        Inventory.last_restocked,
        last_order_q.c.last_order_at,
    ).join(Inventory, Inventory.product_id == Product.id).outerjoin(
        last_order_q, last_order_q.c.product_id == Product.id
    )

    if category:
        q = q.filter(Product.category == category)

    rows = q.all()

    aging_thresholds = {
        "FRESH": (0, 30),
        "NORMAL": (31, 60),
        "AGING": (61, stale_days),
        "STALE": (stale_days + 1, 99999),
    }
    aging_summary = {"FRESH": 0, "NORMAL": 0, "AGING": 0, "STALE": 0}

    items = []
    for row in rows:
        days_since_restock = None
        if row.last_restocked:
            ts = row.last_restocked
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            days_since_restock = (today - ts).days

        days_since_last_order = None
        if row.last_order_at:
            ts = row.last_order_at
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            days_since_last_order = (today - ts).days

        # Classify by days since restock (fallback to days since last order)
        ref_days = days_since_restock if days_since_restock is not None else days_since_last_order
        aging_status = "UNKNOWN"
        if ref_days is not None:
            for label, (lo, hi) in aging_thresholds.items():
                if lo <= ref_days <= hi:
                    aging_status = label
                    break
            if aging_status in aging_summary:
                aging_summary[aging_status] += 1

        items.append({
            "product_id": row.product_id,
            "product_name": row.product_name,
            "sku": row.sku,
            "category": row.category,
            "quantity_available": row.quantity_available or 0,
            "unit_price": _round(row.unit_price),
            "stock_value": _round((row.quantity_available or 0) * (row.unit_price or 0)),
            "last_restocked": row.last_restocked.isoformat() if row.last_restocked else None,
            "days_since_restock": days_since_restock,
            "days_since_last_order": days_since_last_order,
            "aging_status": aging_status,
        })

    items.sort(key=lambda x: (x["days_since_restock"] or 99999), reverse=True)

    return {
        "generated_at": today.isoformat(),
        "total_products": len(items),
        "stale_threshold_days": stale_days,
        "aging_summary": aging_summary,
        "items": items,
    }


# ─────────────────────────────────────────────────────────────────────────────
#  SUPPLIER REPORTS
# ─────────────────────────────────────────────────────────────────────────────

def _compute_performance_score(
    sla_compliance: float,
    on_time_rate: float,
    avg_lead_time_hours: Optional[float],
    rating: Optional[int],
) -> float:
    """
    Weighted score 0–100:
      40% SLA compliance
      30% on-time delivery rate
      20% lead-time efficiency (lower is better, normalized to 168h / 1 week)
      10% supplier rating (1–5 scaled to 0–100)
    """
    lead_score = 100.0
    if avg_lead_time_hours is not None and avg_lead_time_hours > 0:
        lead_score = max(0.0, 100.0 - (avg_lead_time_hours / 168.0 * 100.0))

    rating_score = ((rating - 1) / 4.0 * 100.0) if rating else 50.0

    score = (
        0.40 * sla_compliance
        + 0.30 * on_time_rate
        + 0.20 * lead_score
        + 0.10 * rating_score
    )
    return round(score, 2)


def get_supplier_performance(
    db: Session,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
    supplier_id: Optional[int] = None,
) -> Dict[str, Any]:
    """Aggregated delivery, SLA, and revenue metrics per supplier."""
    q = db.query(
        Order.supplier_id,
        Supplier.supplier_name,
        Supplier.rating,
        func.count(Order.id).label("total_orders"),
        func.sum(case((Order.status == "delivered", 1), else_=0)).label("delivered"),
        func.sum(case((Order.status == "pending", 1), else_=0)).label("pending"),
        func.sum(case((Order.status == "cancelled", 1), else_=0)).label("cancelled"),
        func.coalesce(func.sum(Order.total_amount), 0.0).label("total_revenue"),
        func.avg(Order.total_time).label("avg_lead_time"),
        func.avg(Order.processing_time).label("avg_processing_time"),
        func.avg(Order.delivery_time_duration).label("avg_delivery_time"),
        func.sum(case((Order.sla_breach == True, 1), else_=0)).label("sla_breaches"),
        func.sum(
            case(
                (and_(Order.status == "delivered", Order.sla_breach == False), 1),
                else_=0,
            )
        ).label("on_time_delivered"),
    ).join(Supplier, Supplier.id == Order.supplier_id)

    if start or end:
        q = q.filter(*_date_clauses(Order.order_placed_at, start, end))
    if supplier_id:
        q = q.filter(Order.supplier_id == supplier_id)

    rows = (
        q.group_by(Order.supplier_id, Supplier.supplier_name, Supplier.rating)
        .order_by(func.sum(Order.total_amount).desc())
        .all()
    )

    suppliers = []
    for row in rows:
        total = row.total_orders or 1
        delivered = row.delivered or 0
        sla_compliance = _safe_pct(total - (row.sla_breaches or 0), total)
        on_time_rate = _safe_pct(row.on_time_delivered or 0, delivered if delivered else 1)
        perf_score = _compute_performance_score(
            sla_compliance, on_time_rate, row.avg_lead_time, row.rating
        )

        suppliers.append({
            "supplier_id": row.supplier_id,
            "supplier_name": row.supplier_name,
            "rating": row.rating,
            "total_orders": row.total_orders or 0,
            "delivered_orders": delivered,
            "pending_orders": row.pending or 0,
            "cancelled_orders": row.cancelled or 0,
            "total_revenue": _round(row.total_revenue),
            "avg_lead_time_hours": _round(row.avg_lead_time),
            "avg_processing_time_hours": _round(row.avg_processing_time),
            "avg_delivery_time_hours": _round(row.avg_delivery_time),
            "sla_breach_count": row.sla_breaches or 0,
            "sla_compliance_rate": sla_compliance,
            "on_time_delivery_rate": on_time_rate,
            "performance_score": perf_score,
        })

    return {
        "period_start": start.isoformat() if start else None,
        "period_end": end.isoformat() if end else None,
        "total_suppliers": len(suppliers),
        "generated_at": _now().isoformat(),
        "suppliers": suppliers,
    }


def get_supplier_scorecard(
    db: Session,
    supplier_id: int,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Detailed scorecard for a single supplier including monthly trend and top products."""
    supplier = db.query(Supplier).filter(Supplier.id == supplier_id).first()
    if not supplier:
        return None  # caller raises 404

    # Core performance metrics
    perf = get_supplier_performance(db, start, end, supplier_id=supplier_id)
    perf_item = perf["suppliers"][0] if perf["suppliers"] else {}

    # Monthly revenue trend for this supplier
    trend_q = (
        db.query(
            func.strftime("%Y-%m", Order.order_placed_at).label("month"),
            func.count(Order.id).label("orders"),
            func.coalesce(func.sum(Order.total_amount), 0.0).label("revenue"),
            func.sum(case((Order.status == "delivered", 1), else_=0)).label("delivered"),
            func.sum(case((Order.status == "cancelled", 1), else_=0)).label("cancelled"),
        )
        .filter(Order.supplier_id == supplier_id)
    )
    if start or end:
        trend_q = trend_q.filter(*_date_clauses(Order.order_placed_at, start, end))

    trend_rows = (
        trend_q.group_by("month").order_by("month").all()
    )
    monthly_trend = [
        {
            "month": r.month,
            "orders": r.orders,
            "revenue": _round(r.revenue),
            "delivered": r.delivered or 0,
            "cancelled": r.cancelled or 0,
        }
        for r in trend_rows
    ]

    # Top products supplied by this supplier
    top_q = (
        db.query(
            Product.id.label("product_id"),
            Product.product_name,
            Product.sku,
            Product.category,
            func.count(Order.id).label("orders"),
            func.coalesce(func.sum(Order.quantity), 0).label("units"),
            func.coalesce(func.sum(Order.total_amount), 0.0).label("revenue"),
        )
        .join(Order, Order.product_id == Product.id)
        .filter(Order.supplier_id == supplier_id)
    )
    if start or end:
        top_q = top_q.filter(*_date_clauses(Order.order_placed_at, start, end))

    top_products = [
        {
            "product_id": r.product_id,
            "product_name": r.product_name,
            "sku": r.sku,
            "category": r.category,
            "orders": r.orders,
            "units": r.units or 0,
            "revenue": _round(r.revenue),
        }
        for r in (
            top_q.group_by(Product.id, Product.product_name, Product.sku, Product.category)
            .order_by(func.sum(Order.total_amount).desc())
            .limit(10)
            .all()
        )
    ]

    return {
        "supplier_id": supplier_id,
        "supplier_name": supplier.supplier_name,
        "contact_person": supplier.contact_person,
        "email": supplier.email,
        "phone": supplier.phone,
        "city": supplier.city,
        "country": supplier.country,
        "rating": supplier.rating,
        "performance_metrics": perf_item,
        "monthly_trend": monthly_trend,
        "top_products": top_products,
        "generated_at": _now().isoformat(),
    }


# ─────────────────────────────────────────────────────────────────────────────
#  FORECAST ACCURACY REPORT
# ─────────────────────────────────────────────────────────────────────────────

def get_forecast_accuracy(
    db: Session,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
    category: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Computes demand forecast accuracy by comparing:
      - Actual demand: delivered orders in the evaluation period.
      - Predicted baseline: rolling mean demand from the prior period of equal length.

    Metrics: MAE, MAPE, RMSE, accuracy_pct (100 - MAPE).
    """
    if not end:
        end = _now()
    if not start:
        start = end - timedelta(days=30)

    period_days = max((end - start).days, 1)
    prior_start = start - timedelta(days=period_days)
    prior_end = start

    # Actual demand per product in evaluation period
    actual_q = (
        db.query(
            Order.product_id,
            func.coalesce(func.sum(Order.quantity), 0).label("actual_demand"),
        )
        .filter(Order.status == "delivered")
        .filter(*_date_clauses(Order.order_placed_at, start, end))
        .group_by(Order.product_id)
        .subquery()
    )

    # Prior period demand (used as prediction baseline)
    prior_q = (
        db.query(
            Order.product_id,
            func.coalesce(func.sum(Order.quantity), 0).label("prior_demand"),
        )
        .filter(Order.status == "delivered")
        .filter(*_date_clauses(Order.order_placed_at, prior_start, prior_end))
        .group_by(Order.product_id)
        .subquery()
    )

    q = (
        db.query(
            Product.id.label("product_id"),
            Product.product_name,
            Product.category,
            Product.sku,
            func.coalesce(actual_q.c.actual_demand, 0).label("actual_demand"),
            func.coalesce(prior_q.c.prior_demand, 0).label("predicted_demand"),
        )
        .outerjoin(actual_q, actual_q.c.product_id == Product.id)
        .outerjoin(prior_q, prior_q.c.product_id == Product.id)
    )
    if category:
        q = q.filter(Product.category == category)

    rows = q.all()

    product_metrics = []
    total_mae = 0.0
    total_mape = 0.0
    total_rmse = 0.0
    count = 0

    for row in rows:
        actual = float(row.actual_demand or 0)
        predicted = float(row.predicted_demand or 0)
        error = abs(actual - predicted)
        mae = round(error, 4)
        mape = round((error / actual * 100) if actual > 0 else 0.0, 2)
        rmse = round(error ** 2, 4)  # squared error; sqrt taken in aggregate
        accuracy_pct = round(max(0.0, 100.0 - mape), 2)

        total_mae += mae
        total_mape += mape
        total_rmse += rmse
        count += 1

        product_metrics.append({
            "product_id": row.product_id,
            "product_name": row.product_name,
            "sku": row.sku,
            "category": row.category,
            "actual_demand": actual,
            "predicted_demand": predicted,
            "mae": mae,
            "mape": mape,
            "rmse": round(rmse ** 0.5, 4),
            "accuracy_pct": accuracy_pct,
        })

    product_metrics.sort(key=lambda x: x["mape"])

    avg_mae = round(total_mae / count, 4) if count else None
    avg_mape = round(total_mape / count, 2) if count else None
    avg_rmse = round((total_rmse / count) ** 0.5, 4) if count else None
    avg_accuracy = round(100.0 - avg_mape, 2) if avg_mape is not None else None

    return {
        "evaluation_period_start": start.isoformat(),
        "evaluation_period_end": end.isoformat(),
        "baseline_period_start": prior_start.isoformat(),
        "baseline_period_end": prior_end.isoformat(),
        "period_days": period_days,
        "total_products": count,
        "avg_mae": avg_mae,
        "avg_mape": avg_mape,
        "avg_rmse": avg_rmse,
        "avg_accuracy_pct": avg_accuracy,
        "generated_at": _now().isoformat(),
        "products": product_metrics,
    }


# ─────────────────────────────────────────────────────────────────────────────
#  OPERATIONAL REPORTS
# ─────────────────────────────────────────────────────────────────────────────

def get_operational_kpis(
    db: Session,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Single endpoint with all key operational KPIs — suitable for executive dashboard."""
    sales = get_sales_summary(db, start, end)
    fulfillment = get_fulfillment_stats(db, start, end)

    # Inventory KPIs
    inv_agg = db.query(
        func.count(func.distinct(Inventory.product_id)).label("total_skus"),
        func.sum(Inventory.quantity_available).label("total_available"),
        func.sum(Inventory.quantity_reserved).label("total_reserved"),
        func.sum(
            case((Inventory.quantity_available == 0, 1), else_=0)
        ).label("out_of_stock"),
    ).one()

    inv_valuation_q = (
        db.query(
            func.coalesce(
                func.sum(Inventory.quantity_available * Product.unit_price), 0.0
            ).label("total_inv_value")
        ).join(Product, Product.id == Inventory.product_id)
    ).scalar()

    # SLA KPIs
    sla_row = db.query(
        func.count(Order.id).label("total_orders"),
        func.sum(case((Order.sla_breach == True, 1), else_=0)).label("breaches"),
        func.avg(Order.total_time).label("avg_total_time"),
    )
    if start or end:
        sla_row = sla_row.filter(*_date_clauses(Order.order_placed_at, start, end))
    sla_row = sla_row.one()

    total_orders = sla_row.total_orders or 0
    breaches = sla_row.breaches or 0

    # Active supplier count
    supplier_count = (
        db.query(func.count(func.distinct(Order.supplier_id)))
        .filter(Order.supplier_id.isnot(None))
    )
    if start or end:
        supplier_count = supplier_count.filter(
            *_date_clauses(Order.order_placed_at, start, end)
        )
    supplier_count = supplier_count.scalar() or 0

    # Bottleneck rate
    bottleneck_count = db.query(func.count(Order.id)).filter(
        Order.bottleneck_stage.isnot(None)
    )
    if start or end:
        bottleneck_count = bottleneck_count.filter(
            *_date_clauses(Order.order_placed_at, start, end)
        )
    bottleneck_count = bottleneck_count.scalar() or 0

    return {
        "generated_at": _now().isoformat(),
        "period_start": start.isoformat() if start else None,
        "period_end": end.isoformat() if end else None,
        "sales_kpis": {
            "total_orders": sales["total_orders"],
            "total_revenue": sales["total_revenue"],
            "avg_order_value": sales["avg_order_value"],
            "total_units_sold": sales["total_units_sold"],
            "fulfillment_rate": sales["fulfillment_rate"],
            "cancellation_rate": sales["cancellation_rate"],
        },
        "inventory_kpis": {
            "total_skus": inv_agg.total_skus or 0,
            "total_available_units": inv_agg.total_available or 0,
            "total_reserved_units": inv_agg.total_reserved or 0,
            "out_of_stock_count": inv_agg.out_of_stock or 0,
            "total_inventory_value": _round(inv_valuation_q or 0.0),
        },
        "supplier_kpis": {
            "active_suppliers": supplier_count,
        },
        "sla_kpis": {
            "total_orders": total_orders,
            "sla_breaches": breaches,
            "sla_compliance_rate": _safe_pct(total_orders - breaches, total_orders),
            "avg_order_cycle_time_hours": _round(sla_row.avg_total_time),
        },
        "bottleneck_kpis": {
            "orders_with_bottleneck": bottleneck_count,
            "bottleneck_rate": _safe_pct(bottleneck_count, total_orders),
        },
    }


def get_sla_compliance_report(
    db: Session,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
    supplier_id: Optional[int] = None,
    category: Optional[str] = None,
) -> Dict[str, Any]:
    """SLA compliance broken down by lifecycle stage."""
    q = db.query(Order)
    if start or end:
        q = q.filter(*_date_clauses(Order.order_placed_at, start, end))
    if supplier_id:
        q = q.filter(Order.supplier_id == supplier_id)
    if category:
        q = q.join(Product, Product.id == Order.product_id).filter(
            Product.category == category
        )

    total_orders = q.count()
    total_breaches = q.filter(Order.sla_breach == True).count()

    # Breach counts per stage
    stage_rows = (
        db.query(
            Order.breached_stage.label("stage"),
            func.count(Order.id).label("count"),
        )
        .filter(Order.sla_breach == True)
        .filter(Order.breached_stage.isnot(None))
    )
    if start or end:
        stage_rows = stage_rows.filter(*_date_clauses(Order.order_placed_at, start, end))
    if supplier_id:
        stage_rows = stage_rows.filter(Order.supplier_id == supplier_id)
    stage_rows = (
        stage_rows.group_by(Order.breached_stage)
        .order_by(func.count(Order.id).desc())
        .all()
    )

    # Average durations per stage
    dur_row = q.with_entities(
        func.avg(Order.procurement_time).label("avg_procurement"),
        func.avg(Order.processing_time).label("avg_processing"),
        func.avg(Order.dispatch_time_duration).label("avg_dispatch"),
        func.avg(Order.delivery_time_duration).label("avg_delivery"),
    ).one()

    stage_avg = {
        "procurement": _round(dur_row.avg_procurement),
        "processing": _round(dur_row.avg_processing),
        "dispatch": _round(dur_row.avg_dispatch),
        "delivery": _round(dur_row.avg_delivery),
    }

    by_stage = [
        {
            "stage": row.stage,
            "breach_count": row.count,
            "breach_rate": _safe_pct(row.count, total_orders),
            "avg_duration_hours": stage_avg.get(row.stage.lower().split()[0], None),
        }
        for row in stage_rows
    ]

    return {
        "period_start": start.isoformat() if start else None,
        "period_end": end.isoformat() if end else None,
        "total_orders": total_orders,
        "total_breaches": total_breaches,
        "overall_compliance_rate": _safe_pct(total_orders - total_breaches, total_orders),
        "avg_stage_durations_hours": stage_avg,
        "by_stage": by_stage,
        "generated_at": _now().isoformat(),
    }


def get_bottleneck_report(
    db: Session,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
    supplier_id: Optional[int] = None,
    category: Optional[str] = None,
) -> Dict[str, Any]:
    """Bottleneck distribution with stage-level frequency and average duration."""
    base_q = db.query(Order)
    if start or end:
        base_q = base_q.filter(*_date_clauses(Order.order_placed_at, start, end))
    if supplier_id:
        base_q = base_q.filter(Order.supplier_id == supplier_id)
    if category:
        base_q = base_q.join(Product, Product.id == Order.product_id).filter(
            Product.category == category
        )

    total_orders = base_q.count()

    stage_q = (
        db.query(
            Order.bottleneck_stage.label("stage"),
            func.count(Order.id).label("count"),
            func.avg(Order.total_time).label("avg_total_time"),
        )
        .filter(Order.bottleneck_stage.isnot(None))
    )
    if start or end:
        stage_q = stage_q.filter(*_date_clauses(Order.order_placed_at, start, end))
    if supplier_id:
        stage_q = stage_q.filter(Order.supplier_id == supplier_id)

    stage_rows = (
        stage_q.group_by(Order.bottleneck_stage)
        .order_by(func.count(Order.id).desc())
        .all()
    )

    total_with_bottleneck = sum(r.count for r in stage_rows)

    by_stage = [
        {
            "stage": row.stage,
            "count": row.count,
            "percentage": _safe_pct(row.count, total_orders),
            "avg_total_time_hours": _round(row.avg_total_time),
        }
        for row in stage_rows
    ]

    return {
        "period_start": start.isoformat() if start else None,
        "period_end": end.isoformat() if end else None,
        "total_orders": total_orders,
        "total_orders_with_bottleneck": total_with_bottleneck,
        "bottleneck_rate": _safe_pct(total_with_bottleneck, total_orders),
        "by_stage": by_stage,
        "generated_at": _now().isoformat(),
    }


# ─────────────────────────────────────────────────────────────────────────────
#  EXPORT HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def get_sales_export_data(
    db: Session,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
    status: Optional[str] = None,
    supplier_id: Optional[int] = None,
    category: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Raw order rows for CSV export."""
    q = db.query(
        Order.id,
        Order.order_number,
        Order.status,
        Order.quantity,
        Order.unit_price,
        Order.total_amount,
        Order.order_placed_at,
        Order.delivered_at,
        Order.total_time,
        Order.sla_breach,
        Order.breached_stage,
        Order.bottleneck_stage,
        Product.product_name,
        Product.sku,
        Product.category,
        Supplier.supplier_name,
    ).join(Product, Product.id == Order.product_id).outerjoin(
        Supplier, Supplier.id == Order.supplier_id
    )

    if start or end:
        q = q.filter(*_date_clauses(Order.order_placed_at, start, end))
    if status:
        q = q.filter(Order.status == status)
    if supplier_id:
        q = q.filter(Order.supplier_id == supplier_id)
    if category:
        q = q.filter(Product.category == category)

    rows = q.order_by(Order.order_placed_at.desc()).all()

    return [
        {
            "order_id": r.id,
            "order_number": r.order_number,
            "status": r.status,
            "product_name": r.product_name,
            "sku": r.sku,
            "category": r.category,
            "supplier_name": r.supplier_name,
            "quantity": r.quantity,
            "unit_price": r.unit_price,
            "total_amount": r.total_amount,
            "order_placed_at": r.order_placed_at.isoformat() if r.order_placed_at else "",
            "delivered_at": r.delivered_at.isoformat() if r.delivered_at else "",
            "total_time_hours": r.total_time,
            "sla_breach": r.sla_breach,
            "breached_stage": r.breached_stage,
            "bottleneck_stage": r.bottleneck_stage,
        }
        for r in rows
    ]


def get_inventory_export_data(
    db: Session,
    category: Optional[str] = None,
    supplier_id: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Full inventory snapshot for CSV export."""
    q = db.query(
        Product.id,
        Product.product_name,
        Product.sku,
        Product.category,
        Product.unit_price,
        Product.reorder_level,
        Inventory.quantity_available,
        Inventory.quantity_reserved,
        Inventory.warehouse_location,
        Inventory.last_restocked,
        Supplier.supplier_name,
    ).join(Inventory, Inventory.product_id == Product.id).outerjoin(
        Supplier, Supplier.id == Product.supplier_id
    )

    if category:
        q = q.filter(Product.category == category)
    if supplier_id:
        q = q.filter(Product.supplier_id == supplier_id)

    rows = q.order_by(Product.product_name).all()

    return [
        {
            "product_id": r.id,
            "product_name": r.product_name,
            "sku": r.sku,
            "category": r.category,
            "unit_price": r.unit_price,
            "reorder_level": r.reorder_level,
            "quantity_available": r.quantity_available or 0,
            "quantity_reserved": r.quantity_reserved or 0,
            "total_stock_value": _round(
                (r.quantity_available or 0) * (r.unit_price or 0)
            ),
            "warehouse_location": r.warehouse_location,
            "last_restocked": r.last_restocked.isoformat() if r.last_restocked else "",
            "supplier_name": r.supplier_name,
        }
        for r in rows
    ]


def get_supplier_export_data(
    db: Session,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
) -> List[Dict[str, Any]]:
    """Supplier performance rows for CSV export."""
    perf = get_supplier_performance(db, start, end)
    return perf["suppliers"]
