"""
Notification & Alert API routes.

Endpoints:
  POST /api/notifications/run            – trigger all alert checks
  GET  /api/notifications/               – list notifications (filterable)
  GET  /api/notifications/summary        – unread counts by category/priority
  PATCH /api/notifications/{id}/read     – mark one notification as read
  PATCH /api/notifications/read-all      – mark all notifications as read
  PATCH /api/notifications/{id}/resolve  – mark one notification as resolved
  DELETE /api/notifications/{id}         – delete a notification
"""
import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_active_user, require_roles
from app.database.connection import get_db
from app.models.user import User
from app.schemas.schemas import (
    AlertRunResponse,
    NotificationListResponse,
    NotificationSchema,
    NotificationSummaryResponse,
)
from app.services.notification_service import NotificationService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/notifications", tags=["Notifications & Alerts"])

# Valid filter values (documented in Swagger)
VALID_CATEGORIES = (
    "LOW_STOCK", "REORDER_REQUIRED", "DEMAND_SURGE",
    "SLA_BREACH", "BOTTLENECK", "SUPPLIER_DELAY", "OVERSTOCK", "SYSTEM",
)
VALID_PRIORITIES = ("CRITICAL", "HIGH", "MEDIUM", "LOW")


@router.post(
    "/run",
    response_model=AlertRunResponse,
    status_code=status.HTTP_200_OK,
    summary="Run all automated alert checks and generate new notifications",
)
def run_alert_checks(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles("admin", "analyst")),
) -> AlertRunResponse:
    """
    Triggers the full alert pipeline:
    - Low-stock & reorder alerts (from inventory recommendation engine)
    - SLA breach alerts (orders breached in last 24 h)
    - Bottleneck alerts (stages affecting ≥ 3 orders)
    - Supplier delay alerts (avg procurement time > 120 h)
    - Demand surge alerts (7-day avg > 1.5× 30-day avg)

    Deduplication prevents duplicate alerts per event per day.

    Requires role: `admin` or `analyst`.
    """
    try:
        svc = NotificationService(db)
        result = svc.run_all_checks()
        return AlertRunResponse(**result)
    except Exception as exc:
        logger.exception("Alert run failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Alert run failed: {exc}",
        )


@router.get(
    "/summary",
    response_model=NotificationSummaryResponse,
    status_code=status.HTTP_200_OK,
    summary="Get notification counts by category and priority",
)
def get_summary(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> NotificationSummaryResponse:
    """
    Returns unread / active notification counts grouped by category and
    priority. Suitable for dashboard badge counters.
    """
    try:
        svc = NotificationService(db)
        return NotificationSummaryResponse(**svc.get_summary())
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        )


@router.get(
    "/",
    status_code=status.HTTP_200_OK,
    summary="List notifications with optional filters",
)
def list_notifications(
    category: Optional[str] = Query(default=None, description=f"One of: {VALID_CATEGORIES}"),
    priority: Optional[str] = Query(default=None, description=f"One of: {VALID_PRIORITIES}"),
    is_read: Optional[bool] = Query(default=None),
    is_resolved: Optional[bool] = Query(default=False),
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> Dict[str, Any]:
    """
    Returns paginated notifications. Filters:
    - `category`    – alert type (LOW_STOCK, SLA_BREACH, etc.)
    - `priority`    – CRITICAL / HIGH / MEDIUM / LOW
    - `is_read`     – true / false
    - `is_resolved` – defaults to false (active alerts only)
    """
    if category and category.upper() not in VALID_CATEGORIES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid category. Must be one of: {VALID_CATEGORIES}",
        )
    if priority and priority.upper() not in VALID_PRIORITIES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid priority. Must be one of: {VALID_PRIORITIES}",
        )

    try:
        svc = NotificationService(db)
        return svc.list_notifications(
            category=category,
            priority=priority,
            is_read=is_read,
            is_resolved=is_resolved,
            skip=skip,
            limit=limit,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        )


@router.patch(
    "/read-all",
    status_code=status.HTTP_200_OK,
    summary="Mark all unread notifications as read",
)
def mark_all_read(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> Dict[str, Any]:
    svc = NotificationService(db)
    count = svc.mark_all_read()
    return {"marked_read": count}


@router.patch(
    "/{notification_id}/read",
    status_code=status.HTTP_200_OK,
    summary="Mark a notification as read",
)
def mark_read(
    notification_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> Dict[str, Any]:
    svc = NotificationService(db)
    result = svc.mark_read(notification_id)
    if not result:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Notification {notification_id} not found.",
        )
    return result


@router.patch(
    "/{notification_id}/resolve",
    status_code=status.HTTP_200_OK,
    summary="Mark a notification as resolved",
)
def mark_resolved(
    notification_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> Dict[str, Any]:
    svc = NotificationService(db)
    result = svc.mark_resolved(notification_id)
    if not result:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Notification {notification_id} not found.",
        )
    return result


@router.delete(
    "/{notification_id}",
    status_code=status.HTTP_200_OK,
    summary="Delete a notification",
)
def delete_notification(
    notification_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles("admin")),
) -> Dict[str, Any]:
    """Requires role: `admin`."""
    svc = NotificationService(db)
    deleted = svc.delete_notification(notification_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Notification {notification_id} not found.",
        )
    return {"deleted": True, "notification_id": notification_id}
