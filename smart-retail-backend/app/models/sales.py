from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey, Boolean
from sqlalchemy.sql import func
from app.database.connection import Base

class Order(Base):
    __tablename__ = "orders"
    
    id = Column(Integer, primary_key=True, index=True)
    order_number = Column(String(100), unique=True, nullable=False, index=True)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False, index=True)
    supplier_id = Column(Integer, ForeignKey("suppliers.id"), index=True)
    quantity = Column(Integer, nullable=False)
    unit_price = Column(Float)
    total_amount = Column(Float)
    
    # Lifecycle Timestamps
    order_placed_at = Column(DateTime(timezone=True))
    procurement_completed_at = Column(DateTime(timezone=True))
    processing_completed_at = Column(DateTime(timezone=True))
    dispatched_at = Column(DateTime(timezone=True))
    delivered_at = Column(DateTime(timezone=True))
    
    # Calculated Analytics Fields
    procurement_time = Column(Float)  # hours
    processing_time = Column(Float)  # hours
    dispatch_time_duration = Column(Float)  # hours
    delivery_time_duration = Column(Float)  # hours
    total_time = Column(Float)  # hours
    
    # SLA Analytics
    sla_breach = Column(Boolean, default=False)
    breached_stage = Column(String(100))
    
    # Bottleneck Detection
    bottleneck_stage = Column(String(100))
    
    # Order Status
    status = Column(String(50), default="pending", index=True)
    
    # Metadata
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())