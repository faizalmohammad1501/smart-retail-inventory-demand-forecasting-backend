"""
ML Configuration: constants, feature definitions, thresholds, and model parameters
for the Smart Retail Demand Forecasting pipeline.
"""
from typing import List, Dict

# ─────────────────────────────────────────────
# Dataset paths (relative to smart-retail-backend/)
# ─────────────────────────────────────────────
DATASETS_DIR = "ml/datasets"
PROCESSED_DIR = "ml/datasets/processed"
RAW_DIR = "ml/datasets/raw"
SAVED_MODELS_DIR = "ml/saved_models"

# ─────────────────────────────────────────────
# Temporal aggregation
# ─────────────────────────────────────────────
FORECAST_HORIZON_DAYS: int = 30          # how many days ahead to forecast
AGGREGATION_FREQ: str = "D"              # "D" = daily, "W" = weekly
MIN_HISTORY_DAYS: int = 60              # minimum history required per product
TRAIN_RATIO: float = 0.70
VAL_RATIO: float = 0.15
TEST_RATIO: float = 0.15                 # remainder

# ─────────────────────────────────────────────
# Product categories (must match DB)
# ─────────────────────────────────────────────
PRODUCT_CATEGORIES: List[str] = [
    "Electronics",
    "Clothing",
    "Groceries",
    "Furniture",
    "Sports",
    "Books",
    "Toys",
    "Automotive",
    "Health",
    "Beauty",
]

CATEGORY_ENCODING: Dict[str, int] = {cat: i for i, cat in enumerate(PRODUCT_CATEGORIES)}

# ─────────────────────────────────────────────
# Feature column names
# ─────────────────────────────────────────────

# Time-based features
TIME_FEATURES: List[str] = [
    "day_of_week",       # 0=Monday … 6=Sunday
    "day_of_month",      # 1-31
    "week_of_year",      # 1-53
    "month",             # 1-12
    "quarter",           # 1-4
    "year",
    "is_weekend",        # 0/1
    "is_month_start",    # 0/1
    "is_month_end",      # 0/1
    "days_since_start",  # integer index for trend
]

# Lag demand features (days)
LAG_DAYS: List[int] = [1, 3, 7, 14, 21, 28]
LAG_FEATURES: List[str] = [f"lag_{d}" for d in LAG_DAYS]

# Rolling window statistics
ROLLING_WINDOWS: List[int] = [7, 14, 30]
ROLLING_FEATURES: List[str] = (
    [f"rolling_mean_{w}" for w in ROLLING_WINDOWS]
    + [f"rolling_std_{w}" for w in ROLLING_WINDOWS]
    + [f"rolling_min_{w}" for w in ROLLING_WINDOWS]
    + [f"rolling_max_{w}" for w in ROLLING_WINDOWS]
)

# Product / inventory features
PRODUCT_FEATURES: List[str] = [
    "unit_price",
    "reorder_level",
    "category_encoded",
    "quantity_available",
    "stock_ratio",          # quantity_available / reorder_level
]

# Order lifecycle / SLA features (aggregated per product per day)
ORDER_FEATURES: List[str] = [
    "avg_procurement_time",
    "avg_processing_time",
    "avg_delivery_time",
    "avg_total_time",
    "sla_breach_rate",      # fraction of orders breaching SLA
    "order_count",          # number of orders on that day
]

# All input features used by the model
ALL_FEATURES: List[str] = (
    TIME_FEATURES
    + LAG_FEATURES
    + ROLLING_FEATURES
    + PRODUCT_FEATURES
    + ORDER_FEATURES
)

# Target variable
TARGET_COLUMN: str = "demand"            # total quantity ordered per product per day

# Columns kept in the processed dataset beyond features+target
META_COLUMNS: List[str] = [
    "date",
    "product_id",
    "product_name",
    "category",
    "sku",
]

# ─────────────────────────────────────────────
# Data quality thresholds
# ─────────────────────────────────────────────
MAX_NULL_RATIO: float = 0.30             # drop rows if > 30 % of core features are null
OUTLIER_IQR_MULTIPLIER: float = 3.0     # IQR fence multiplier for outlier detection
MIN_DEMAND_VALUE: float = 0.0
MAX_DEMAND_VALUE: float = 100_000.0
MIN_UNIT_PRICE: float = 0.01
MAX_UNIT_PRICE: float = 1_000_000.0

# ─────────────────────────────────────────────
# Synthetic data generation parameters
# ─────────────────────────────────────────────
SYNTHETIC_PRODUCTS: int = 20
SYNTHETIC_DAYS: int = 365              # 1 year of daily history
SYNTHETIC_SEED: int = 42

# ─────────────────────────────────────────────
# Scaler / encoder artefact names
# ─────────────────────────────────────────────
FEATURE_SCALER_FILE: str = "feature_scaler.pkl"
TARGET_SCALER_FILE: str = "target_scaler.pkl"
LABEL_ENCODER_FILE: str = "category_encoder.pkl"
PIPELINE_METADATA_FILE: str = "pipeline_metadata.json"
