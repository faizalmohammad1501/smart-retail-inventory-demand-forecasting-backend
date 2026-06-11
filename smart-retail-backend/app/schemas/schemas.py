from pydantic import BaseModel, Field, field_validator, EmailStr
from typing import Any, Dict, List, Optional, Literal
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


# ============= ML / Demand Forecasting Schemas =============

class PipelineRunRequest(BaseModel):
    """Request body for triggering the preprocessing pipeline."""
    use_synthetic: bool = Field(
        default=False,
        description="If True, generates and uses synthetic data instead of live DB data.",
    )
    scale: bool = Field(default=True, description="Fit and apply feature/target scalers.")
    save_artefacts: bool = Field(default=True, description="Persist processed CSVs and scalers.")


class ValidationIssue(BaseModel):
    column: Optional[str] = None
    message: str


class ValidationReportSchema(BaseModel):
    is_valid: bool
    errors: List[str]
    warnings: List[str]
    rows_total: int
    rows_valid: int
    null_summary: Dict[str, float]
    out_of_range_counts: Dict[str, int]


class CleaningReportSchema(BaseModel):
    original_rows: int
    duplicates_removed: int
    demand_clamped: int
    price_clamped: int
    nulls_imputed: Dict[str, int]
    outliers_capped: int
    final_rows: int


class SplitInfoSchema(BaseModel):
    total_rows: int
    train_rows: int
    val_rows: int
    test_rows: int
    train_pct: float
    val_pct: float
    test_pct: float
    n_products: int
    date_range: Dict[str, Optional[str]]


class PipelineRunResponse(BaseModel):
    """Response returned after a pipeline run."""
    success: bool
    message: str
    ran_at: str
    validation_report: Optional[Dict[str, Any]] = None
    cleaning_report: Optional[Dict[str, Any]] = None
    split_info: Optional[Dict[str, Any]] = None
    feature_columns: Optional[List[str]] = None
    output_paths: Optional[Dict[str, str]] = None


class DatasetStatsResponse(BaseModel):
    """Summary statistics about the most recent preprocessed datasets."""
    available: bool
    train: Optional[Dict[str, Any]] = None
    val: Optional[Dict[str, Any]] = None
    test: Optional[Dict[str, Any]] = None
    n_features: Optional[int] = None
    feature_columns: Optional[List[str]] = None


class SyntheticGenerateResponse(BaseModel):
    """Response after generating synthetic datasets."""
    success: bool
    message: str
    products_path: str
    inventory_path: str
    demand_path: str
    n_products: int
    n_days: int


class FeaturePreviewRow(BaseModel):
    """A single row from the feature-engineered dataset preview."""
    date: str
    product_id: int
    product_name: Optional[str] = None
    category: Optional[str] = None
    demand: float
    day_of_week: Optional[int] = None
    month: Optional[int] = None
    is_weekend: Optional[int] = None
    lag_7: Optional[float] = None
    rolling_mean_7: Optional[float] = None
    stock_ratio: Optional[float] = None
    category_encoded: Optional[int] = None


class FeaturePreviewResponse(BaseModel):
    """Preview of the engineered feature matrix."""
    rows_returned: int
    total_rows: int
    n_features: int
    feature_columns: List[str]
    preview: List[Dict[str, Any]]


# ============= Prediction / Forecasting Schemas =============

class ForecastRequest(BaseModel):
    """Request body for a single-product or batch forecast."""
    product_id: Optional[int] = Field(
        default=None,
        description="Product to forecast. Omit for all-products batch forecast.",
    )
    horizon_days: int = Field(
        default=30, ge=1, le=90,
        description="Number of calendar days to forecast ahead (1–90).",
    )


class TrainModelRequest(BaseModel):
    """Optional hyperparameter overrides for model training."""
    n_estimators: Optional[int] = Field(default=None, ge=50, le=1000)
    learning_rate: Optional[float] = Field(default=None, gt=0, le=1.0)
    max_depth: Optional[int] = Field(default=None, ge=2, le=10)


class DayForecastSchema(BaseModel):
    date: str
    predicted_demand: float
    lower_bound: float
    upper_bound: float
    confidence_score: float


class InventoryStatusSchema(BaseModel):
    current_stock: int
    reserved_stock: int
    available_stock: int
    reorder_level: int
    stock_status: str
    days_of_stock_remaining: float


class ForecastSummarySchema(BaseModel):
    avg_predicted_demand_per_day: float
    total_predicted_demand: float
    recommended_stock_level: float
    safety_stock: float


class ProductForecastResponse(BaseModel):
    product_id: int
    product_name: str
    category: str
    sku: str
    model_used: str
    horizon_days: int
    forecast_generated_at: str
    summary: ForecastSummarySchema
    inventory: InventoryStatusSchema
    daily_forecasts: List[DayForecastSchema]


class BatchForecastResponse(BaseModel):
    status: str
    horizon_days: int
    model_used: str
    total_products: int
    forecasted: int
    failed: int
    errors: List[Dict[str, Any]]
    forecasts: List[Dict[str, Any]]
    generated_at: str


class TrainModelResponse(BaseModel):
    success: bool
    message: str
    trained_at: str
    n_train_samples: int
    n_val_samples: int
    n_features: int
    model_path: str
    train_metrics: Optional[Dict[str, Any]] = None
    val_metrics: Optional[Dict[str, Any]] = None


class ModelStatusResponse(BaseModel):
    model_trained: bool
    model_type: Optional[str] = None
    trained_at: Optional[str] = None
    n_features: Optional[int] = None
    n_train_samples: Optional[int] = None
    val_metrics: Optional[Dict[str, Any]] = None
    message: str


# ============= Inventory Recommendation Schemas =============

class RecommendationSummary(BaseModel):
    total_products: int
    out_of_stock: int
    critical: int
    high: int
    medium: int
    low: int
    require_immediate_reorder: int
    require_reorder_soon: int
    avg_stockout_risk_score: float
    estimated_total_reorder_value: float


class ProductRecommendation(BaseModel):
    product_id: int
    product_name: str
    sku: str
    category: Optional[str] = None
    unit_price: float
    supplier_id: Optional[int] = None
    current_stock: int
    reserved_stock: int
    available_stock: int
    reorder_level: int
    last_restocked: Optional[str] = None
    avg_daily_demand: float
    std_daily_demand: float
    total_orders_last_30d: int
    total_qty_sold_last_30d: int
    lead_time_days: float
    safety_stock: float
    reorder_point: float
    reorder_quantity: int
    estimated_reorder_value: float
    days_of_stock_remaining: float
    stockout_risk_score: float
    estimated_stockout_date: Optional[str] = None
    priority: str
    action: str


class AllRecommendationsResponse(BaseModel):
    generated_at: str
    total_products: int
    summary: RecommendationSummary
    recommendations: List[Dict[str, Any]]


class InventoryHealthResponse(BaseModel):
    generated_at: str
    kpis: RecommendationSummary
    by_category: List[Dict[str, Any]]
    by_priority: List[Dict[str, Any]]


class CriticalAlertsResponse(BaseModel):
    generated_at: str
    total_alerts: int
    alerts: List[Dict[str, Any]]


class SupplierInfoSchema(BaseModel):
    supplier_name: str
    contact_person: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    rating: Optional[int] = None


class ReplenishmentItemSchema(BaseModel):
    product_id: int
    product_name: str
    sku: str
    reorder_quantity: int
    estimated_reorder_value: float
    priority: str
    action: str
    supplier_id: Optional[int] = None
    supplier_info: Optional[SupplierInfoSchema] = None


class ReplenishmentListResponse(BaseModel):
    generated_at: str
    total_items_to_reorder: int
    estimated_total_reorder_value: float
    replenishment_list: List[Dict[str, Any]]


# ============= Notification Schemas =============

class NotificationSchema(BaseModel):
    id: int
    category: str
    priority: str
    title: str
    message: str
    product_id: Optional[int] = None
    product_name: Optional[str] = None
    supplier_id: Optional[int] = None
    order_id: Optional[int] = None
    metric_value: Optional[float] = None
    metric_label: Optional[str] = None
    is_read: bool
    is_resolved: bool
    resolved_at: Optional[str] = None
    created_at: Optional[str] = None

    class Config:
        from_attributes = True


class NotificationListResponse(BaseModel):
    total: int
    skip: int
    limit: int
    notifications: List[NotificationSchema]


class NotificationSummaryResponse(BaseModel):
    total_active: int
    total_unread: int
    by_category: Dict[str, int]
    by_priority: Dict[str, int]


class AlertRunResponse(BaseModel):
    run_at: str
    total_created: int
    notifications: List[Dict[str, Any]]

