from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import Any, Dict

from app.core.dependencies import get_current_active_user
from app.database.connection import get_db
from app.models.user import User
from app.services.analytics_service import (
    get_bottleneck_analytics,
    get_sla_breach_analytics,
    get_summary_analytics,
)

router = APIRouter(prefix="/api/analytics", tags=["Analytics"])


@router.get("/summary", status_code=status.HTTP_200_OK)
def summary(
    db: Session = Depends(get_db),
    _: User = Depends(get_current_active_user),
) -> Dict[str, Any]:
    """
    KPI summary card data.

    Returns aggregate metrics across all orders:
    total orders, average lifecycle stage durations,
    SLA breach count and percentage.
    """
    try:
        data = get_summary_analytics(db)
        return {"status": "success", "data": data}
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        )


@router.get("/bottlenecks", status_code=status.HTTP_200_OK)
def bottlenecks(
    db: Session = Depends(get_db),
    _: User = Depends(get_current_active_user),
) -> Dict[str, Any]:
    """
    Bottleneck distribution chart data.

    Groups orders by their detected bottleneck stage and returns
    stage name, order count, and percentage share.
    """
    try:
        data = get_bottleneck_analytics(db)
        return {
            "status": "success",
            "total_stages": len(data),
            "data": data,
        }
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        )


@router.get("/sla-breaches", status_code=status.HTTP_200_OK)
def sla_breaches(
    db: Session = Depends(get_db),
    _: User = Depends(get_current_active_user),
) -> Dict[str, Any]:
    """
    SLA breach table data for dashboards and Tableau exports.

    Returns every breached order with order ID, order number,
    breached stage, bottleneck stage, total time, and status.
    """
    try:
        data = get_sla_breach_analytics(db)
        return {
            "status": "success",
            "total_breaches": len(data),
            "data": data,
        }
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        )
