from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session
from typing import List, Optional
from datetime import datetime

from app.database.connection import get_db
from app.schemas.schemas import OrderCreate, OrderResponse, OrderAnalytics
from app.services.order_service import OrderService
from app.utils.lifecycle_validator import LifecycleValidationError

router = APIRouter(prefix="/api/orders", tags=["Orders"])

@router.post("/", response_model=OrderResponse, status_code=status.HTTP_201_CREATED)
def create_order(order: OrderCreate, db: Session = Depends(get_db)):
    """
    Create new order with automated analytics processing.
    Calculates durations, validates SLA, and detects bottlenecks.
    """
    try:
        service = OrderService(db)
        
        # Check for duplicate order number
        existing = service.get_order_by_number(order.order_number)
        if existing:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Order number already exists"
            )
        
        return service.create_order(order)
        
    except LifecycleValidationError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Lifecycle validation failed: {str(e)}"
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )

@router.get("/", response_model=List[OrderResponse])
def get_all_orders(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    db: Session = Depends(get_db)
):
    """Retrieve all orders with pagination"""
    service = OrderService(db)
    return service.get_all_orders(skip=skip, limit=limit)

@router.get("/{order_id}", response_model=OrderResponse)
def get_order(order_id: int, db: Session = Depends(get_db)):
    """Retrieve order by ID"""
    service = OrderService(db)
    order = service.get_order_by_id(order_id)
    if not order:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Order not found"
        )
    return order

@router.get("/by-number/{order_number}", response_model=OrderResponse)
def get_order_by_number(order_number: str, db: Session = Depends(get_db)):
    """Retrieve order by order number"""
    service = OrderService(db)
    order = service.get_order_by_number(order_number)
    if not order:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Order not found"
        )
    return order

@router.get("/status/{status}", response_model=List[OrderResponse])
def get_orders_by_status(status: str, db: Session = Depends(get_db)):
    """Retrieve orders by status"""
    service = OrderService(db)
    return service.get_orders_by_status(status)

@router.get("/analytics/sla-breaches", response_model=List[OrderResponse])
def get_sla_breach_orders(db: Session = Depends(get_db)):
    """Retrieve all orders with SLA breaches"""
    service = OrderService(db)
    return service.get_orders_with_sla_breach()

@router.get("/analytics/bottleneck/{stage}", response_model=List[OrderResponse])
def get_orders_by_bottleneck(stage: str, db: Session = Depends(get_db)):
    """Retrieve orders by bottleneck stage"""
    service = OrderService(db)
    return service.get_orders_by_bottleneck(stage)

@router.get("/analytics/summary", response_model=OrderAnalytics)
def get_analytics_summary(
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    status: Optional[str] = None,
    product_id: Optional[int] = None,
    supplier_id: Optional[int] = None,
    db: Session = Depends(get_db)
):
    """Get analytics summary with optional filters"""
    service = OrderService(db)
    filters = {
        'start_date': start_date,
        'end_date': end_date,
        'status': status,
        'product_id': product_id,
        'supplier_id': supplier_id
    }
    # Remove None values
    filters = {k: v for k, v in filters.items() if v is not None}
    
    return service.get_analytics_summary(filters)

@router.patch("/{order_id}/status")
def update_order_status(
    order_id: int,
    status: str,
    db: Session = Depends(get_db)
):
    """Update order status"""
    service = OrderService(db)
    order = service.update_order_status(order_id, status)
    if not order:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Order not found"
        )
    return {"message": "Order status updated", "order_id": order_id, "status": status}

@router.delete("/{order_id}")
def delete_order(order_id: int, db: Session = Depends(get_db)):
    """Delete order by ID"""
    service = OrderService(db)
    if not service.delete_order(order_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Order not found"
        )
    return {"message": "Order deleted successfully"}