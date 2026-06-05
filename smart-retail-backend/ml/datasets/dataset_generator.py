"""
SyntheticDataGenerator: generates realistic synthetic sales, inventory,
and product records for local development and pipeline smoke-testing
when the live database is empty.

Seeded with SYNTHETIC_SEED for full reproducibility.
"""
import logging
import random
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from ml.config import (
    DATASETS_DIR,
    PRODUCT_CATEGORIES,
    RAW_DIR,
    SYNTHETIC_DAYS,
    SYNTHETIC_PRODUCTS,
    SYNTHETIC_SEED,
    TARGET_COLUMN,
)

logger = logging.getLogger(__name__)

rng = np.random.default_rng(SYNTHETIC_SEED)
random.seed(SYNTHETIC_SEED)

# ─────────────────────────────────────────────────────────────────────────────
# Static product catalogue
# ─────────────────────────────────────────────────────────────────────────────

PRODUCT_TEMPLATES = [
    {"name": "Laptop Pro 15", "category": "Electronics", "base_price": 1200.0, "reorder": 20},
    {"name": "Wireless Headphones", "category": "Electronics", "base_price": 180.0, "reorder": 50},
    {"name": "USB-C Hub", "category": "Electronics", "base_price": 45.0, "reorder": 100},
    {"name": "Running Shoes X1", "category": "Sports", "base_price": 95.0, "reorder": 60},
    {"name": "Yoga Mat Premium", "category": "Sports", "base_price": 35.0, "reorder": 80},
    {"name": "Denim Jacket", "category": "Clothing", "base_price": 75.0, "reorder": 40},
    {"name": "Cotton T-Shirt Pack", "category": "Clothing", "base_price": 25.0, "reorder": 150},
    {"name": "Organic Coffee Beans 1kg", "category": "Groceries", "base_price": 18.0, "reorder": 200},
    {"name": "Almond Milk 1L", "category": "Groceries", "base_price": 4.5, "reorder": 300},
    {"name": "Office Chair Ergonomic", "category": "Furniture", "base_price": 350.0, "reorder": 15},
    {"name": "Standing Desk", "category": "Furniture", "base_price": 550.0, "reorder": 10},
    {"name": "Python Programming Book", "category": "Books", "base_price": 42.0, "reorder": 30},
    {"name": "Science Fiction Novel", "category": "Books", "base_price": 16.0, "reorder": 50},
    {"name": "LEGO City Set", "category": "Toys", "base_price": 65.0, "reorder": 35},
    {"name": "Board Game Strategy", "category": "Toys", "base_price": 38.0, "reorder": 25},
    {"name": "Car Phone Mount", "category": "Automotive", "base_price": 22.0, "reorder": 90},
    {"name": "Dash Cam HD", "category": "Automotive", "base_price": 120.0, "reorder": 20},
    {"name": "Vitamin D3 Supplement", "category": "Health", "base_price": 14.0, "reorder": 100},
    {"name": "Protein Powder 2kg", "category": "Health", "base_price": 55.0, "reorder": 60},
    {"name": "Face Moisturiser SPF50", "category": "Beauty", "base_price": 32.0, "reorder": 80},
]

# Subset to the configured product count
PRODUCTS = PRODUCT_TEMPLATES[:SYNTHETIC_PRODUCTS]


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def generate_products() -> List[Dict]:
    """Return a list of product dicts compatible with the Product DB model."""
    products = []
    for idx, tmpl in enumerate(PRODUCTS, start=1):
        noise = rng.uniform(0.95, 1.05)
        products.append(
            {
                "product_id": idx,
                "product_name": tmpl["name"],
                "sku": f"SKU-{idx:04d}",
                "category": tmpl["category"],
                "unit_price": round(tmpl["base_price"] * noise, 2),
                "supplier_id": ((idx - 1) % 5) + 1,   # 5 suppliers
                "reorder_level": tmpl["reorder"],
                "description": f"Auto-generated product: {tmpl['name']}",
            }
        )
    return products


def generate_inventory(products: Optional[List[Dict]] = None) -> List[Dict]:
    """Return inventory snapshot rows (one per product)."""
    if products is None:
        products = generate_products()

    inventory = []
    for prod in products:
        stock = int(rng.integers(prod["reorder_level"], prod["reorder_level"] * 5))
        inventory.append(
            {
                "inventory_id": prod["product_id"],
                "product_id": prod["product_id"],
                "warehouse_location": f"WH-{((prod['product_id'] - 1) % 3) + 1}",
                "quantity_available": stock,
                "quantity_reserved": int(stock * rng.uniform(0.05, 0.20)),
                "last_restocked": (
                    datetime.utcnow() - timedelta(days=int(rng.integers(1, 30)))
                ).isoformat(),
            }
        )
    return inventory


def generate_daily_demand(
    products: Optional[List[Dict]] = None,
    days: int = SYNTHETIC_DAYS,
    start_date: Optional[datetime] = None,
) -> pd.DataFrame:
    """
    Produce a daily demand DataFrame for all products over `days` calendar days.

    Demand is simulated with:
    - Base demand proportional to reorder_level
    - Weekly seasonality (higher Fri/Sat)
    - Monthly seasonality (higher end-of-month)
    - Additive Gaussian noise
    - Random spikes (~5 % of days)

    Returns DataFrame with columns matching the output of
    DataLoader.build_daily_demand(), making it a drop-in replacement.
    """
    if products is None:
        products = generate_products()

    inventory = generate_inventory(products)
    inv_map = {inv["product_id"]: inv for inv in inventory}

    if start_date is None:
        start_date = datetime.utcnow() - timedelta(days=days)

    date_range = [start_date + timedelta(days=i) for i in range(days)]

    records = []
    for prod in products:
        pid = prod["product_id"]
        base = prod["reorder_level"] * rng.uniform(0.5, 2.0)     # product-level base
        trend = rng.uniform(-0.001, 0.003)                         # slight linear trend
        inv = inv_map[pid]

        for i, date in enumerate(date_range):
            # Seasonality
            weekly = 1.0 + 0.25 * np.sin(2 * np.pi * date.weekday() / 7)
            monthly = 1.0 + 0.15 * np.sin(2 * np.pi * date.day / 28)

            # Trend
            trend_factor = 1.0 + trend * i

            # Spike
            spike = rng.uniform(2.0, 4.0) if rng.random() < 0.05 else 1.0

            # Raw demand
            raw = base * weekly * monthly * trend_factor * spike
            noise = rng.normal(0, base * 0.10)
            demand = max(0.0, raw + noise)

            # Simulated order lifecycle durations (hours)
            procurement_h = rng.uniform(12, 72)
            processing_h = rng.uniform(6, 48)
            delivery_h = rng.uniform(24, 120)
            total_h = procurement_h + processing_h + delivery_h
            sla_breach = int(total_h > 156)

            records.append(
                {
                    "date": date.date(),
                    "product_id": pid,
                    "product_name": prod["product_name"],
                    "sku": prod["sku"],
                    "category": prod["category"],
                    "unit_price": prod["unit_price"],
                    "reorder_level": prod["reorder_level"],
                    "quantity_available": inv["quantity_available"],
                    "quantity_reserved": inv["quantity_reserved"],
                    TARGET_COLUMN: round(demand, 2),
                    "order_count": max(1, int(demand / max(prod["reorder_level"] * 0.1, 1))),
                    "avg_procurement_time": round(procurement_h, 2),
                    "avg_processing_time": round(processing_h, 2),
                    "avg_delivery_time": round(delivery_h, 2),
                    "avg_total_time": round(total_h, 2),
                    "sla_breach_rate": float(sla_breach),
                }
            )

    df = pd.DataFrame(records)
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values(["product_id", "date"]).reset_index(drop=True)

    logger.info(
        "Generated synthetic demand: %d rows, %d products, %d days.",
        len(df), len(products), days,
    )
    return df


def generate_and_save(output_dir: Optional[str] = None) -> Tuple[str, str, str]:
    """
    Generate all synthetic datasets and save as CSV files.

    Returns:
        (products_path, inventory_path, demand_path)
    """
    out = Path(output_dir or RAW_DIR)
    out.mkdir(parents=True, exist_ok=True)

    products = generate_products()
    inventory = generate_inventory(products)
    demand_df = generate_daily_demand(products)

    prod_path = out / "synthetic_products.csv"
    inv_path = out / "synthetic_inventory.csv"
    demand_path = out / "synthetic_demand.csv"

    pd.DataFrame(products).to_csv(prod_path, index=False)
    pd.DataFrame(inventory).to_csv(inv_path, index=False)
    demand_df.to_csv(demand_path, index=False)

    logger.info("Synthetic datasets saved to %s.", out)
    return str(prod_path), str(inv_path), str(demand_path)
