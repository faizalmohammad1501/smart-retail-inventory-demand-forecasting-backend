from sqlalchemy.orm import Session
from sqlalchemy import func, and_
from typing import List, Optional, Dict, Any
from datetime import datetime

from app.models.sales import Order
from app.schemas.schemas import OrderCreate
from app.services.preprocessing_service import OrderPreprocessingService
from app.utils.lifecycle_validator import LifecycleValidationError

class OrderService:
    """Service layer for order operations with integrated preprocessing"""
    
    def __init__(self, db: Session):
        self.db = db
        self.preprocessing_service = OrderPreprocessingService()
    
    def create_order(self, order_data: OrderCreate) -> Order:
        """
        Create new order with analytics preprocessing.
        
        Args:
            order_data: Order creation data
            
        Returns:
            Created order with analytics fields
            
        Raises:
            LifecycleValidationError: If lifecycle validation fails
            Exception: For other processing errors
        """
        try:
            # Convert Pydantic model to dict
            order_dict = order_data.dict()
            
            # Calculate total amount
            if order_dict.get('unit_price') and order_dict.get('quantity'):
                order_dict['total_amount'] = order_dict['unit_price'] * order_dict['quantity']
            
            # Execute preprocessing pipeline
            processed_data = self.preprocessing_service.preprocess_order(order_dict)
            
            # Create database model
            db_order = Order(**processed_data)
            
            # Save to database
            self.db.add(db_order)
            self.db.commit()
            self.db.refresh(db_order)
            
            return db_order
            
        except LifecycleValidationError:
            self.db.rollback()
            raise
        except Exception as e:
            self.db.rollback()
            raise Exception(f\"Failed to create order: {str(e)}\")
    
    def get_order_by_id(self, order_id: int) -> Optional[Order]:
        """Retrieve order by ID"""
        return self.db.query(Order).filter(Order.id == order_id).first()
    
    def get_order_by_number(self, order_number: str) -> Optional[Order]:
        """Retrieve order by order number"""
        return self.db.query(Order).filter(Order.order_number == order_number).first()
    
    def get_all_orders(self, skip: int = 0, limit: int = 100) -> List[Order]:
        """Retrieve all orders with pagination"""
        return self.db.query(Order).offset(skip).limit(limit).all()
    
    def get_orders_by_status(self, status: str) -> List[Order]:
        """Retrieve orders by status"""
        return self.db.query(Order).filter(Order.status == status).all()
    
    def get_orders_with_sla_breach(self) -> List[Order]:
        """Retrieve all orders with SLA breaches"""
        return self.db.query(Order).filter(Order.sla_breach == True).all()
    
    def get_orders_by_bottleneck(self, bottleneck_stage: str) -> List[Order]:
        """Retrieve orders by bottleneck stage"""
        return self.db.query(Order).filter(Order.bottleneck_stage == bottleneck_stage).all()
    
    def get_orders_by_date_range(self, start_date: datetime, end_date: datetime) -> List[Order]:
        """Retrieve orders within date range"""
        return self.db.query(Order).filter(
            and_(
                Order.order_placed_at >= start_date,
                Order.order_placed_at <= end_date
            )
        ).all()
    
    def update_order_status(self, order_id: int, status: str) -> Optional[Order]:
        """Update order status"""
        order = self.get_order_by_id(order_id)
        if order:
            order.status = status
            self.db.commit()
            self.db.refresh(order)
        return order
    
    def get_analytics_summary(self, filters: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Get analytics summary for orders with optional filters.
        
        Args:
            filters: Optional filtering criteria
            
        Returns:
            Analytics summary dictionary
        """
        query = self.db.query(Order)
        
        # Apply filters if provided
        if filters:
            if filters.get('start_date'):
                query = query.filter(Order.order_placed_at >= filters['start_date'])
            if filters.get('end_date'):
                query = query.filter(Order.order_placed_at <= filters['end_date'])
            if filters.get('status'):
                query = query.filter(Order.status == filters['status'])
            if filters.get('product_id'):
                query = query.filter(Order.product_id == filters['product_id'])
            if filters.get('supplier_id'):
                query = query.filter(Order.supplier_id == filters['supplier_id'])
        
        orders = query.all()
        
        # Convert to dict format for preprocessing service
        order_dicts = [
            {
                'sla_breach': order.sla_breach,
                'total_time': order.total_time,
                'bottleneck_stage': order.bottleneck_stage
            }
            for order in orders
        ]
        
        return self.preprocessing_service.calculate_analytics_summary(order_dicts)
    
    def delete_order(self, order_id: int) -> bool:
        """Delete order by ID"""
        order = self.get_order_by_id(order_id)
        if order:
            self.db.delete(order)
            self.db.commit()
            return True
        return False
