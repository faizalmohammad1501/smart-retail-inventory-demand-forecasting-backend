"""
Inventory Recommendation API routes.

Endpoints:
  GET /api/recommendations/                 – all products with recommendations
  GET /api/recommendations/health           – inventory health dashboard
  GET /api/recommendations/alerts           – CRITICAL & HIGH priority only
  GET /api/recommendations/replenishment    – items to reorder + supplier info
  GET /api/recommendations/{product_id}     – single product recommendation
"""
import logging
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_active_user
from app.database.connection import get_db
from app.models.user import User
from app.schemas.schemas import (
    AllRecommendationsResponse,
    CriticalAlertsResponse,
    InventoryHealthResponse,
    ReplenishmentListResponse,
)
from app.services.inventory_recommendation_service import InventoryRecommendationService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/recommendations", tags=["Inventory Recommendations"])


@router.get(
    "/",
    status_code=status.HTTP_200_OK,
    summary="Get restocking recommendations for all products",
)
def get_all_recommendations(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> Dict[str, Any]:
    """
    Returns demand-driven restocking recommendations for every product.

    Each recommendation includes:
    - avg daily demand (30-day rolling)
    - lead time estimate from order history
    - safety stock, reorder point, and recommended reorder quantity
    - stockout risk score (0-100) and estimated stockout date
    - priority (CRITICAL / HIGH / MEDIUM / LOW)
    - action (REORDER_NOW / REORDER_SOON / MONITOR / NO_ACTION / REDUCE_STOCK)
    """
    try:
        svc = InventoryRecommendationService(db)
        return svc.get_all_recommendations()
    except Exception as exc:
        logger.exception("Failed to generate recommendations: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Recommendation engine error: {exc}",
        )


@router.get(
    "/health",
    status_code=status.HTTP_200_OK,
    summary="Inventory health dashboard — KPIs and category breakdown",
)
def get_inventory_health(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> Dict[str, Any]:
    """
    Dashboard-ready inventory health overview.

    Returns:
    - KPI summary (total products, out-of-stock count, by-priority counts)
    - Per-category health breakdown with average risk scores
    - Priority distribution for chart rendering
    """
    try:
        svc = InventoryRecommendationService(db)
        return svc.get_inventory_health()
    except Exception as exc:
        logger.exception("Inventory health check failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Health check error: {exc}",
        )


@router.get(
    "/alerts",
    status_code=status.HTTP_200_OK,
    summary="Critical and high-priority stock alerts",
)
def get_critical_alerts(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> Dict[str, Any]:
    """
    Returns only CRITICAL and HIGH priority products, sorted by risk score
    descending. Designed for alert panels and notification triggers.
    """
    try:
        svc = InventoryRecommendationService(db)
        return svc.get_critical_alerts()
    except Exception as exc:
        logger.exception("Critical alerts fetch failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Alerts error: {exc}",
        )


@router.get(
    "/replenishment",
    status_code=status.HTTP_200_OK,
    summary="Replenishment list with supplier contact details",
)
def get_replenishment_list(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> Dict[str, Any]:
    """
    Items requiring REORDER_NOW or REORDER_SOON actions, enriched with
    supplier contact details (name, email, phone, rating) and estimated
    total reorder value.

    Suitable for generating purchase orders or Tableau export.
    """
    try:
        svc = InventoryRecommendationService(db)
        return svc.get_replenishment_list()
    except Exception as exc:
        logger.exception("Replenishment list failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Replenishment error: {exc}",
        )


@router.get(
    "/{product_id}",
    status_code=status.HTTP_200_OK,
    summary="Get recommendation for a specific product",
)
def get_product_recommendation(
    product_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> Dict[str, Any]:
    """
    Full recommendation detail for a single product including demand
    statistics, lead time, safety stock calculation, risk score,
    and inventory action.
    """
    try:
        svc = InventoryRecommendationService(db)
        return svc.get_product_recommendation(product_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except Exception as exc:
        logger.exception("Product recommendation failed for id=%d: %s", product_id, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Recommendation error: {exc}",
        )
