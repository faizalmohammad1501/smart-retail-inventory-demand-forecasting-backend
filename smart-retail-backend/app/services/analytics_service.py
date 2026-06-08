from sqlalchemy.orm import Session
from sqlalchemy import func, case
from typing import Any, Dict, List

from app.models.sales import Order


def get_summary_analytics(db: Session) -> Dict[str, Any]:
    """
    Compute KPI summary using a single aggregated DB query.

    Returns:
        total_orders, average lifecycle stage times,
        sla_breach_count, sla_breach_percentage
    """
    row = db.query(
        func.count(Order.id).label("total_orders"),
        func.avg(Order.total_time).label("avg_total_time"),
        func.avg(Order.procurement_time).label("avg_procurement_time"),
        func.avg(Order.processing_time).label("avg_processing_time"),
        func.avg(Order.dispatch_time_duration).label("avg_dispatch_time"),
        func.avg(Order.delivery_time_duration).label("avg_delivery_time"),
        func.sum(
            case((Order.sla_breach == True, 1), else_=0)
        ).label("sla_breach_count"),
    ).one()

    total = row.total_orders or 0
    breach_count = row.sla_breach_count or 0
    sla_breach_pct = round((breach_count / total * 100), 2) if total > 0 else 0.0

    return {
        "total_orders": total,
        "average_total_time_hours": round(row.avg_total_time or 0, 2),
        "average_procurement_time_hours": round(row.avg_procurement_time or 0, 2),
        "average_processing_time_hours": round(row.avg_processing_time or 0, 2),
        "average_dispatch_time_hours": round(row.avg_dispatch_time or 0, 2),
        "average_delivery_time_hours": round(row.avg_delivery_time or 0, 2),
        "sla_breach_count": breach_count,
        "sla_breach_percentage": sla_breach_pct,
        "sla_compliance_percentage": round(100 - sla_breach_pct, 2),
    }


def get_bottleneck_analytics(db: Session) -> List[Dict[str, Any]]:
    """
    Group orders by bottleneck_stage and return stage name, count,
    and percentage share — ready for chart rendering.
    """
    total_orders = db.query(func.count(Order.id)).scalar() or 0

    rows = (
        db.query(
            Order.bottleneck_stage.label("stage"),
            func.count(Order.id).label("order_count"),
        )
        .filter(Order.bottleneck_stage.isnot(None))
        .group_by(Order.bottleneck_stage)
        .order_by(func.count(Order.id).desc())
        .all()
    )

    return [
        {
            "stage": row.stage,
            "order_count": row.order_count,
            "percentage": round((row.order_count / total_orders * 100), 2)
            if total_orders > 0
            else 0.0,
        }
        for row in rows
    ]


def get_sla_breach_analytics(db: Session) -> List[Dict[str, Any]]:
    """
    Return all SLA-breached orders with the fields needed for a
    dashboard breach table or Tableau export.

    Columns: order_id, order_number, breached_stage, bottleneck_stage,
             total_time_hours, status
    """
    rows = (
        db.query(
            Order.id.label("order_id"),
            Order.order_number,
            Order.breached_stage,
            Order.bottleneck_stage,
            Order.total_time,
            Order.status,
        )
        .filter(Order.sla_breach == True)
        .order_by(Order.total_time.desc())
        .all()
    )

    return [
        {
            "order_id": row.order_id,
            "order_number": row.order_number,
            "breached_stage": row.breached_stage,
            "bottleneck_stage": row.bottleneck_stage,
            "total_time_hours": round(row.total_time or 0, 2),
            "status": row.status,
        }
        for row in rows
    ]
