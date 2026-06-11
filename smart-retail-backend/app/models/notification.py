from sqlalchemy import Column, Integer, String, Boolean, DateTime, Text, Float
from sqlalchemy.sql import func
from app.database.connection import Base


class Notification(Base):
    __tablename__ = "notifications"

    id = Column(Integer, primary_key=True, index=True)

    # Classification
    category = Column(String(50), nullable=False, index=True)
    # LOW_STOCK | REORDER_REQUIRED | DEMAND_SURGE | SLA_BREACH |
    # BOTTLENECK | SUPPLIER_DELAY | OVERSTOCK | SYSTEM

    priority = Column(String(20), nullable=False, index=True)
    # CRITICAL | HIGH | MEDIUM | LOW

    # Content
    title = Column(String(255), nullable=False)
    message = Column(Text, nullable=False)

    # Context references (all optional)
    product_id = Column(Integer, nullable=True, index=True)
    product_name = Column(String(255), nullable=True)
    supplier_id = Column(Integer, nullable=True)
    order_id = Column(Integer, nullable=True)

    # Numeric context (e.g. risk score, days remaining, breach hours)
    metric_value = Column(Float, nullable=True)
    metric_label = Column(String(100), nullable=True)

    # State
    is_read = Column(Boolean, default=False, nullable=False, index=True)
    is_resolved = Column(Boolean, default=False, nullable=False, index=True)
    resolved_at = Column(DateTime(timezone=True), nullable=True)

    # Deduplication key: prevents duplicate alerts for same event
    dedup_key = Column(String(255), nullable=True, unique=True, index=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
