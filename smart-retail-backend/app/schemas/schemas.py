from pydantic import BaseModel, Field, field_validator, EmailStr
from typing import Optional, Literal
from datetime import datetime

# ============= User Schemas =============
class UserBase(BaseModel):
    username: str = Field(..., min_length=3, max_length=100)
    email: str = Field(..., min_length=5, max_length=255)
    full_name: Optional[str] = None
    role: str = "user"


class UserCreate(UserBase):
    password: str = Field(..., min_length=8, max_length=128)

    @field_validator("password")
    @classmethod
    def password_strength(cls, v: str) -> str:
        if not any(c.isupper() for c in v):
            raise ValueError("Password must contain at least one uppercase letter")
        if not any(c.isdigit() for c in v):
            raise ValueError("Password must contain at least one digit")
        return v


class UserLogin(BaseModel):
    username: str
    password: str


class UserUpdate(BaseModel):
    full_name: Optional[str] = None
    email: Optional[str] = Field(None, min_length=5, max_length=255)


class ChangePassword(BaseModel):
    current_password: str
    new_password: str = Field(..., min_length=8, max_length=128)

    @field_validator("new_password")
    @classmethod
    def password_strength(cls, v: str) -> str:
        if not any(c.isupper() for c in v):
            raise ValueError("Password must contain at least one uppercase letter")
        if not any(c.isdigit() for c in v):
            raise ValueError("Password must contain at least one digit")
        return v


class UserResponse(UserBase):
    id: int
    is_active: bool
    last_login: Optional[datetime] = None
    created_at: datetime

    class Config:
        from_attributes = True


# ============= JWT / Token Schemas =============
class Token(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int  # access token TTL in seconds


class TokenRefresh(BaseModel):
    refresh_token: str


class TokenData(BaseModel):
    username: Optional[str] = None
    user_id: Optional[int] = None
    role: Optional[str] = None

# ============= Supplier Schemas =============
class SupplierBase(BaseModel):
    supplier_name: str = Field(..., min_length=2, max_length=255)
    contact_person: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    address: Optional[str] = None
    city: Optional[str] = None
    country: Optional[str] = None
    rating: Optional[int] = Field(None, ge=1, le=5)

class SupplierCreate(SupplierBase):
    pass

class SupplierResponse(SupplierBase):
    id: int
    created_at: datetime
    
    class Config:
        from_attributes = True

# ============= Product Schemas =============
class ProductBase(BaseModel):
    product_name: str = Field(..., min_length=2, max_length=255)
    sku: str = Field(..., min_length=2, max_length=100)
    category: Optional[str] = None
    description: Optional[str] = None
    unit_price: float = Field(..., gt=0)
    supplier_id: Optional[int] = None
    reorder_level: int = Field(default=10, ge=0)

class ProductCreate(ProductBase):
    pass

class ProductResponse(ProductBase):
    id: int
    created_at: datetime
    
    class Config:
        from_attributes = True

# ============= Order Schemas =============
class OrderBase(BaseModel):
    order_number: str = Field(..., min_length=3, max_length=100)
    product_id: int
    supplier_id: Optional[int] = None
    quantity: int = Field(..., gt=0)
    unit_price: Optional[float] = Field(None, gt=0)
    
    # Lifecycle Timestamps
    order_placed_at: Optional[datetime] = None
    procurement_completed_at: Optional[datetime] = None
    processing_completed_at: Optional[datetime] = None
    dispatched_at: Optional[datetime] = None
    delivered_at: Optional[datetime] = None
    
    status: str = \"pending\"

class OrderCreate(OrderBase):
    pass

class OrderResponse(OrderBase):
    id: int
    total_amount: Optional[float]
    
    # Analytics Fields
    procurement_time: Optional[float] = None
    processing_time: Optional[float] = None
    dispatch_time_duration: Optional[float] = None
    delivery_time_duration: Optional[float] = None
    total_time: Optional[float] = None
    
    sla_breach: Optional[bool] = False
    breached_stage: Optional[str] = None
    bottleneck_stage: Optional[str] = None
    
    created_at: datetime
    updated_at: Optional[datetime]
    
    class Config:
        from_attributes = True

# ============= Analytics Schemas =============
class OrderAnalytics(BaseModel):
    total_orders: int
    sla_breach_count: int
    sla_compliance_rate: float
    avg_total_time: float
    bottleneck_summary: dict

class AnalyticsQueryParams(BaseModel):
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    status: Optional[str] = None
    product_id: Optional[int] = None
    supplier_id: Optional[int] = None
