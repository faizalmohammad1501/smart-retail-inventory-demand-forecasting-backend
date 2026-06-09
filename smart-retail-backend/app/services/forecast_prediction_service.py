"""
ForecastPredictionService: orchestrates the complete prediction workflow.

Responsibilities:
- Fetch product history from the DB (or processed CSV fallback)
- Validate request parameters
- Invoke ForecastEngine
- Format structured API response with stock recommendations
"""
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd
from sqlalchemy.orm import Session

from app.models.product import Product
from app.models.sales import Order
from app.models.inventory import Inventory
from ml.config import (
    FORECAST_HORIZON_DAYS,
    PROCESSED_DIR,
    TARGET_COLUMN,
)
from ml.prediction.forecast_engine import ForecastEngine, ProductForecast
from ml.prediction.predictor import ModelPredictor

logger = logging.getLogger(__name__)

# How many days of real history to load for lag / rolling features
LOOKBACK_DAYS_LOCAL = 60


class ForecastPredictionService:
    """
    Business-layer service for demand forecasting.

    Instantiated once per request; loads the model lazily.
    """

    def __init__(self, db: Session) -> None:
        self.db = db
        self._predictor: Optional[ModelPredictor] = ModelPredictor.load()
        self._engine = ForecastEngine(predictor=self._predictor)

    # ── Single product forecast ───────────────────────────────────────────────

    def forecast_product(
        self,
        product_id: int,
        horizon_days: int = FORECAST_HORIZON_DAYS,
    ) -> Dict[str, Any]:
        """
        Generate a demand forecast for a single product.

        Returns a structured dict ready to be serialised as a JSON response.
        """
        # Validate horizon
        horizon_days = max(1, min(horizon_days, 90))

        # Fetch product metadata
        product = self.db.query(Product).filter(Product.id == product_id).first()
        if not product:
            raise ValueError(f"Product with id={product_id} not found.")

        # Build history DataFrame
        history_df = self._get_product_history(product_id)
        if history_df.empty:
            history_df = self._synthetic_fallback(product)

        # Fetch latest inventory
        inv = (
            self.db.query(Inventory)
            .filter(Inventory.product_id == product_id)
            .order_by(Inventory.last_restocked.desc())
            .first()
        )
        qty_available = int(inv.quantity_available) if inv else 0
        qty_reserved = int(inv.quantity_reserved) if inv else 0

        product_meta = {
            "product_id": product.id,
            "product_name": product.product_name,
            "sku": product.sku,
            "category": product.category or "Unknown",
            "unit_price": float(product.unit_price),
            "reorder_level": int(product.reorder_level or 10),
            "quantity_available": qty_available,
        }

        forecast: ProductForecast = self._engine.forecast(
            history_df=history_df,
            product_meta=product_meta,
            horizon_days=horizon_days,
        )

        return _serialise_forecast(forecast, qty_available, qty_reserved, product.reorder_level or 10)

    # ── All-products forecast ─────────────────────────────────────────────────

    def forecast_all_products(
        self,
        horizon_days: int = FORECAST_HORIZON_DAYS,
    ) -> Dict[str, Any]:
        """
        Forecast demand for every product in the catalogue.
        Returns a summary list suitable for a dashboard overview.
        """
        horizon_days = max(1, min(horizon_days, 90))
        products = self.db.query(Product).all()

        if not products:
            return {
                "status": "no_products",
                "message": "No products found in the database.",
                "forecasts": [],
            }

        results = []
        errors = []

        for product in products:
            try:
                result = self.forecast_product(product.id, horizon_days)
                results.append(result)
            except Exception as exc:
                logger.warning("Forecast failed for product %d: %s", product.id, exc)
                errors.append({"product_id": product.id, "error": str(exc)})

        return {
            "status": "success",
            "horizon_days": horizon_days,
            "model_used": self._predictor_label(),
            "total_products": len(products),
            "forecasted": len(results),
            "failed": len(errors),
            "errors": errors,
            "forecasts": results,
            "generated_at": datetime.utcnow().isoformat(),
        }

    # ── Private helpers ───────────────────────────────────────────────────────

    def _get_product_history(self, product_id: int) -> pd.DataFrame:
        """
        Build a daily demand history DataFrame for the product from DB orders.
        Falls back to the processed train CSV if DB has too few records.
        """
        orders = (
            self.db.query(Order)
            .filter(Order.product_id == product_id)
            .order_by(Order.order_placed_at)
            .all()
        )

        if len(orders) < 7:
            # Try processed CSV
            csv_df = self._load_from_processed_csv(product_id)
            if not csv_df.empty:
                return csv_df
            # Will use synthetic fallback at call site
            return pd.DataFrame()

        records = []
        for o in orders:
            if o.order_placed_at:
                records.append(
                    {
                        "date": pd.Timestamp(o.order_placed_at).normalize(),
                        TARGET_COLUMN: float(o.quantity or 0),
                        "avg_procurement_time": float(o.procurement_time or 24),
                        "avg_processing_time": float(o.processing_time or 12),
                        "avg_delivery_time": float(o.delivery_time_duration or 72),
                        "avg_total_time": float(o.total_time or 108),
                        "sla_breach_rate": float(1 if o.sla_breach else 0),
                        "order_count": 1,
                    }
                )

        df = pd.DataFrame(records)
        if df.empty:
            return df

        # Aggregate to daily
        df = (
            df.groupby("date")
            .agg(
                demand=(TARGET_COLUMN, "sum"),
                avg_procurement_time=("avg_procurement_time", "mean"),
                avg_processing_time=("avg_processing_time", "mean"),
                avg_delivery_time=("avg_delivery_time", "mean"),
                avg_total_time=("avg_total_time", "mean"),
                sla_breach_rate=("sla_breach_rate", "mean"),
                order_count=("order_count", "sum"),
            )
            .reset_index()
            .sort_values("date")
        )

        # Fill gaps
        full_range = pd.date_range(df["date"].min(), df["date"].max(), freq="D")
        df = df.set_index("date").reindex(full_range).reset_index()
        df = df.rename(columns={"index": "date"})
        df["demand"] = df["demand"].fillna(0)
        for col in ["avg_procurement_time", "avg_processing_time",
                    "avg_delivery_time", "avg_total_time", "sla_breach_rate", "order_count"]:
            df[col] = df[col].fillna(0)

        return df.tail(LOOKBACK_DAYS_LOCAL).reset_index(drop=True)

    def _load_from_processed_csv(self, product_id: int) -> pd.DataFrame:
        """Load this product's rows from the processed train CSV."""
        train_path = Path(PROCESSED_DIR) / "train.csv"
        if not train_path.exists():
            return pd.DataFrame()

        df = pd.read_csv(train_path, parse_dates=["date"])
        product_df = df[df["product_id"] == product_id].copy()
        if product_df.empty:
            return pd.DataFrame()

        product_df = product_df.sort_values("date")
        keep_cols = ["date", TARGET_COLUMN,
                     "avg_procurement_time", "avg_processing_time",
                     "avg_delivery_time", "avg_total_time",
                     "sla_breach_rate", "order_count"]
        available = [c for c in keep_cols if c in product_df.columns]
        return product_df[available].tail(LOOKBACK_DAYS_LOCAL).reset_index(drop=True)

    def _synthetic_fallback(self, product: Product) -> pd.DataFrame:
        """
        Generate a minimal synthetic history so the engine always has
        something to work with even for brand-new products.
        """
        from ml.datasets.dataset_generator import generate_daily_demand
        import numpy as np

        rng = np.random.default_rng(product.id)
        base = (product.reorder_level or 10) * rng.uniform(0.5, 2.0)
        days = LOOKBACK_DAYS_LOCAL
        demand = np.maximum(0, base + rng.normal(0, base * 0.1, size=days))

        dates = pd.date_range(
            end=pd.Timestamp.utcnow().normalize(), periods=days, freq="D"
        )
        return pd.DataFrame(
            {
                "date": dates,
                TARGET_COLUMN: demand,
                "avg_procurement_time": 24.0,
                "avg_processing_time": 12.0,
                "avg_delivery_time": 72.0,
                "avg_total_time": 108.0,
                "sla_breach_rate": 0.0,
                "order_count": 1.0,
            }
        )

    def _predictor_label(self) -> str:
        if self._predictor and self._predictor.is_ready():
            return "GradientBoosting"
        return "WeightedMovingAverage (baseline)"


# ─────────────────────────────────────────────────────────────────────────────
# Serialisation helper
# ─────────────────────────────────────────────────────────────────────────────

def _serialise_forecast(
    forecast: ProductForecast,
    qty_available: int,
    qty_reserved: int,
    reorder_level: int,
) -> Dict[str, Any]:
    """Convert a ProductForecast dataclass to a JSON-serialisable dict."""
    stock_status = _stock_status(qty_available, reorder_level, forecast.avg_predicted_demand)

    return {
        "product_id": forecast.product_id,
        "product_name": forecast.product_name,
        "category": forecast.category,
        "sku": forecast.sku,
        "model_used": forecast.model_used,
        "horizon_days": forecast.horizon_days,
        "forecast_generated_at": forecast.forecast_generated_at,
        "summary": {
            "avg_predicted_demand_per_day": forecast.avg_predicted_demand,
            "total_predicted_demand": forecast.total_predicted_demand,
            "recommended_stock_level": forecast.recommended_stock,
            "safety_stock": forecast.safety_stock,
        },
        "inventory": {
            "current_stock": qty_available,
            "reserved_stock": qty_reserved,
            "available_stock": qty_available - qty_reserved,
            "reorder_level": reorder_level,
            "stock_status": stock_status,
            "days_of_stock_remaining": round(
                (qty_available - qty_reserved) / max(forecast.avg_predicted_demand, 0.01), 1
            ),
        },
        "daily_forecasts": [
            {
                "date": df.date,
                "predicted_demand": df.predicted_demand,
                "lower_bound": df.lower_bound,
                "upper_bound": df.upper_bound,
                "confidence_score": df.confidence_score,
            }
            for df in forecast.forecasts
        ],
    }


def _stock_status(qty_available: int, reorder_level: int, avg_demand: float) -> str:
    if qty_available <= 0:
        return "OUT_OF_STOCK"
    days_remaining = qty_available / max(avg_demand, 0.01)
    if days_remaining <= 3:
        return "CRITICAL"
    if qty_available <= reorder_level:
        return "LOW"
    if qty_available >= reorder_level * 3:
        return "OVERSTOCKED"
    return "ADEQUATE"
