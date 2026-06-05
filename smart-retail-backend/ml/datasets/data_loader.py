"""
DataLoader: ingests raw order + inventory + product data from the SQLite database
and returns tidy Pandas DataFrames ready for the preprocessing pipeline.
"""
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Tuple

import pandas as pd
from sqlalchemy.orm import Session

from app.database.connection import SessionLocal
from app.models.sales import Order
from app.models.product import Product
from app.models.inventory import Inventory
from app.models.supplier import Supplier
from ml.config import (
    AGGREGATION_FREQ,
    MIN_HISTORY_DAYS,
    RAW_DIR,
    TARGET_COLUMN,
)

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

def _ensure_dirs() -> None:
    Path(RAW_DIR).mkdir(parents=True, exist_ok=True)


def _get_db() -> Session:
    return SessionLocal()


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def load_orders_df(db: Optional[Session] = None) -> pd.DataFrame:
    """
    Load all orders from the database into a flat DataFrame.

    Columns returned:
        order_id, order_number, product_id, supplier_id, quantity,
        unit_price, total_amount, status, order_placed_at,
        procurement_completed_at, processing_completed_at,
        dispatched_at, delivered_at,
        procurement_time, processing_time, dispatch_time_duration,
        delivery_time_duration, total_time,
        sla_breach, breached_stage, bottleneck_stage, created_at
    """
    close_after = db is None
    if db is None:
        db = _get_db()

    try:
        rows = db.query(Order).all()
        if not rows:
            logger.warning("No orders found in database.")
            return pd.DataFrame()

        records = [
            {
                "order_id": o.id,
                "order_number": o.order_number,
                "product_id": o.product_id,
                "supplier_id": o.supplier_id,
                "quantity": o.quantity,
                "unit_price": o.unit_price,
                "total_amount": o.total_amount,
                "status": o.status,
                "order_placed_at": o.order_placed_at,
                "procurement_completed_at": o.procurement_completed_at,
                "processing_completed_at": o.processing_completed_at,
                "dispatched_at": o.dispatched_at,
                "delivered_at": o.delivered_at,
                "procurement_time": o.procurement_time,
                "processing_time": o.processing_time,
                "dispatch_time_duration": o.dispatch_time_duration,
                "delivery_time_duration": o.delivery_time_duration,
                "total_time": o.total_time,
                "sla_breach": o.sla_breach,
                "breached_stage": o.breached_stage,
                "bottleneck_stage": o.bottleneck_stage,
                "created_at": o.created_at,
            }
            for o in rows
        ]
    finally:
        if close_after:
            db.close()

    df = pd.DataFrame(records)
    # Parse datetime columns
    dt_cols = [
        "order_placed_at", "procurement_completed_at",
        "processing_completed_at", "dispatched_at",
        "delivered_at", "created_at",
    ]
    for col in dt_cols:
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors="coerce", utc=True)
            df[col] = df[col].dt.tz_localize(None)  # strip tz → naive

    logger.info("Loaded %d orders from database.", len(df))
    return df


def load_products_df(db: Optional[Session] = None) -> pd.DataFrame:
    """
    Load product catalogue from the database.

    Columns: product_id, product_name, sku, category, unit_price,
             supplier_id, reorder_level, description
    """
    close_after = db is None
    if db is None:
        db = _get_db()

    try:
        rows = db.query(Product).all()
        if not rows:
            logger.warning("No products found in database.")
            return pd.DataFrame()

        records = [
            {
                "product_id": p.id,
                "product_name": p.product_name,
                "sku": p.sku,
                "category": p.category,
                "unit_price": p.unit_price,
                "supplier_id": p.supplier_id,
                "reorder_level": p.reorder_level,
                "description": p.description,
            }
            for p in rows
        ]
    finally:
        if close_after:
            db.close()

    df = pd.DataFrame(records)
    logger.info("Loaded %d products from database.", len(df))
    return df


def load_inventory_df(db: Optional[Session] = None) -> pd.DataFrame:
    """
    Load current inventory snapshot from the database.

    Columns: inventory_id, product_id, warehouse_location,
             quantity_available, quantity_reserved, last_restocked
    """
    close_after = db is None
    if db is None:
        db = _get_db()

    try:
        rows = db.query(Inventory).all()
        if not rows:
            logger.warning("No inventory records found in database.")
            return pd.DataFrame()

        records = [
            {
                "inventory_id": inv.id,
                "product_id": inv.product_id,
                "warehouse_location": inv.warehouse_location,
                "quantity_available": inv.quantity_available,
                "quantity_reserved": inv.quantity_reserved,
                "last_restocked": inv.last_restocked,
            }
            for inv in rows
        ]
    finally:
        if close_after:
            db.close()

    df = pd.DataFrame(records)
    if "last_restocked" in df.columns:
        df["last_restocked"] = pd.to_datetime(df["last_restocked"], errors="coerce")
    logger.info("Loaded %d inventory records from database.", len(df))
    return df


def load_all_raw(db: Optional[Session] = None) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """
    Convenience wrapper: load orders, products, and inventory in one call.

    Returns:
        (orders_df, products_df, inventory_df)
    """
    close_after = db is None
    if db is None:
        db = _get_db()

    try:
        orders_df = load_orders_df(db)
        products_df = load_products_df(db)
        inventory_df = load_inventory_df(db)
    finally:
        if close_after:
            db.close()

    return orders_df, products_df, inventory_df


def build_daily_demand(
    orders_df: pd.DataFrame,
    products_df: pd.DataFrame,
    inventory_df: pd.DataFrame,
) -> pd.DataFrame:
    """
    Aggregate raw order rows into a daily demand time-series per product.

    Output columns:
        date, product_id, product_name, sku, category,
        unit_price, reorder_level, quantity_available,
        demand (= total quantity ordered),
        order_count, avg_procurement_time, avg_processing_time,
        avg_delivery_time, avg_total_time, sla_breach_rate
    """
    if orders_df.empty or products_df.empty:
        logger.warning("Cannot build daily demand: missing orders or products data.")
        return pd.DataFrame()

    df = orders_df.copy()

    # Use order_placed_at as the demand date
    df["date"] = pd.to_datetime(df["order_placed_at"]).dt.normalize()

    # Aggregate per (date, product_id)
    agg = (
        df.groupby(["date", "product_id"])
        .agg(
            demand=("quantity", "sum"),
            order_count=("order_id", "count"),
            avg_procurement_time=("procurement_time", "mean"),
            avg_processing_time=("processing_time", "mean"),
            avg_delivery_time=("delivery_time_duration", "mean"),
            avg_total_time=("total_time", "mean"),
            sla_breach_rate=("sla_breach", "mean"),
        )
        .reset_index()
    )

    # Merge product metadata
    prod_cols = ["product_id", "product_name", "sku", "category", "unit_price", "reorder_level"]
    agg = agg.merge(products_df[prod_cols], on="product_id", how="left")

    # Merge latest inventory snapshot (most recent record per product)
    if not inventory_df.empty:
        latest_inv = (
            inventory_df.sort_values("last_restocked", ascending=False)
            .drop_duplicates(subset=["product_id"])
            [["product_id", "quantity_available", "quantity_reserved"]]
        )
        agg = agg.merge(latest_inv, on="product_id", how="left")
        agg["quantity_available"] = agg["quantity_available"].fillna(0).astype(int)
        agg["quantity_reserved"] = agg["quantity_reserved"].fillna(0).astype(int)
    else:
        agg["quantity_available"] = 0
        agg["quantity_reserved"] = 0

    # Sort by product → date for correct lag/rolling computation downstream
    agg = agg.sort_values(["product_id", "date"]).reset_index(drop=True)

    # Fill gaps: ensure continuous daily date range per product
    agg = _fill_date_gaps(agg)

    logger.info(
        "Built daily demand table: %d rows, %d unique products, date range %s → %s",
        len(agg),
        agg["product_id"].nunique(),
        agg["date"].min().date() if not agg.empty else "N/A",
        agg["date"].max().date() if not agg.empty else "N/A",
    )
    return agg


def _fill_date_gaps(df: pd.DataFrame) -> pd.DataFrame:
    """
    For each product, fill missing calendar days with demand=0 so the
    time-series is contiguous (required for lag/rolling features).
    """
    if df.empty:
        return df

    filled_parts = []
    date_min = df["date"].min()
    date_max = df["date"].max()
    full_range = pd.date_range(date_min, date_max, freq="D")

    for pid, group in df.groupby("product_id"):
        group = group.set_index("date").reindex(full_range).reset_index()
        group = group.rename(columns={"index": "date"})
        group["product_id"] = pid
        group["demand"] = group["demand"].fillna(0)
        group["order_count"] = group["order_count"].fillna(0)
        # Forward-fill static product metadata for gap rows
        meta_cols = ["product_name", "sku", "category", "unit_price", "reorder_level",
                     "quantity_available", "quantity_reserved"]
        for col in meta_cols:
            if col in group.columns:
                group[col] = group[col].ffill().bfill()
        # Fill numeric lifecycle cols with 0 for gap rows
        lifecycle_cols = [
            "avg_procurement_time", "avg_processing_time",
            "avg_delivery_time", "avg_total_time", "sla_breach_rate",
        ]
        for col in lifecycle_cols:
            if col in group.columns:
                group[col] = group[col].fillna(0)
        filled_parts.append(group)

    return pd.concat(filled_parts, ignore_index=True)


def load_from_csv(filepath: str) -> pd.DataFrame:
    """
    Load a previously exported raw dataset from CSV.
    Datetime columns are parsed automatically.
    """
    path = Path(filepath)
    if not path.exists():
        raise FileNotFoundError(f"CSV file not found: {filepath}")

    df = pd.read_csv(filepath)
    for col in df.columns:
        if "date" in col.lower() or "at" in col.lower() or "time" in col.lower():
            try:
                df[col] = pd.to_datetime(df[col], errors="coerce")
            except Exception:
                pass

    logger.info("Loaded %d rows from CSV: %s", len(df), filepath)
    return df


def save_raw_snapshot(df: pd.DataFrame, filename: str = "raw_demand.csv") -> str:
    """
    Persist the raw daily demand DataFrame to the raw datasets directory.

    Returns the absolute path to the saved file.
    """
    _ensure_dirs()
    out_path = Path(RAW_DIR) / filename
    df.to_csv(out_path, index=False)
    logger.info("Saved raw snapshot to %s (%d rows).", out_path, len(df))
    return str(out_path)
