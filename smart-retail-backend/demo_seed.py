"""
Smart Retail Platform — Demo Seed Data Script
==============================================
Populates the database with rich, realistic demo data covering:
  - 3 user roles (admin, manager, analyst)
  - 8 suppliers across different regions and performance tiers
  - 30 products across 6 retail categories
  - Inventory records with varied stock levels (including low-stock scenarios)
  - 180+ orders with full lifecycle timestamps, SLA breaches, bottlenecks
  - Realistic date distribution over the past 12 months

Usage:
    python demo_seed.py                    # populate with demo data
    python demo_seed.py --reset            # drop all data first, then seed
    python demo_seed.py --reset --verify   # seed + run verification report

After seeding:
    - Start the server: uvicorn main:app --reload
    - Login as admin:   POST /api/auth/login  {username: "admin", password: "Admin@123"}
    - Open docs:        http://localhost:8000/docs
"""

import argparse
import os
import sys
import random
from datetime import datetime, timedelta
from pathlib import Path

# ── Ensure project root is in sys.path ───────────────────────
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("DATABASE_URL", "sqlite:///./supply_chain.db")
os.environ.setdefault("JWT_SECRET_KEY", "demo-seed-jwt-key-change-in-production-32chars")

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

from sqlalchemy.orm import Session
from app.database.connection import SessionLocal, engine
from app.database.db_init import init_db
from app.models.user import User
from app.models.supplier import Supplier
from app.models.product import Product
from app.models.inventory import Inventory
from app.models.sales import Order
from app.core.security import get_password_hash

random.seed(42)  # reproducible data


# ════════════════════════════════════════════════════════════════
#  Helpers
# ════════════════════════════════════════════════════════════════

def rand_dt(days_ago_max: int, days_ago_min: int = 0) -> datetime:
    offset = random.randint(days_ago_min, days_ago_max)
    jitter = random.randint(0, 86399)
    return datetime.utcnow() - timedelta(days=offset, seconds=jitter)


def add_hours(dt: datetime, hours: float) -> datetime:
    return dt + timedelta(hours=hours)


def log(msg: str) -> None:
    print(f"  {msg}")


# ════════════════════════════════════════════════════════════════
#  Seed functions
# ════════════════════════════════════════════════════════════════

def seed_users(db: Session) -> list[User]:
    users_data = [
        {
            "username": "admin",
            "email": "admin@smartretail.com",
            "full_name": "Platform Administrator",
            "role": "admin",
            "password": "Admin@123",
        },
        {
            "username": "manager",
            "email": "manager@smartretail.com",
            "full_name": "Operations Manager",
            "role": "manager",
            "password": "Manager@123",
        },
        {
            "username": "analyst",
            "email": "analyst@smartretail.com",
            "full_name": "Data Analyst",
            "role": "user",
            "password": "Analyst@123",
        },
    ]
    created = []
    for ud in users_data:
        existing = db.query(User).filter(User.username == ud["username"]).first()
        if existing:
            log(f"User '{ud['username']}' already exists — skipping.")
            created.append(existing)
            continue
        user = User(
            username=ud["username"],
            email=ud["email"],
            full_name=ud["full_name"],
            role=ud["role"],
            hashed_password=get_password_hash(ud["password"]),
            is_active=True,
        )
        db.add(user)
        created.append(user)
        log(f"Created user: {ud['username']} ({ud['role']})")
    db.commit()
    return created


def seed_suppliers(db: Session) -> list[Supplier]:
    suppliers_data = [
        # High performers
        {"supplier_name": "TechSource Global",       "contact_person": "James Thornton",   "email": "james@techsource.com",    "phone": "+1-555-0101", "city": "San Francisco", "country": "USA",     "rating": 5},
        {"supplier_name": "PrimeParts Ltd",           "contact_person": "Emma Whitfield",   "email": "emma@primeparts.co.uk",   "phone": "+44-20-7946", "city": "London",        "country": "UK",      "rating": 5},
        {"supplier_name": "EliteDistributors Inc",    "contact_person": "Carlos Mendez",    "email": "carlos@elitedist.com",    "phone": "+1-555-0202", "city": "Chicago",       "country": "USA",     "rating": 4},
        # Mid performers
        {"supplier_name": "FastTrack Supplies",       "contact_person": "Yuki Tanaka",      "email": "yuki@fasttrack.jp",       "phone": "+81-3-1234", "city": "Tokyo",          "country": "Japan",   "rating": 4},
        {"supplier_name": "Continental Goods",        "contact_person": "Hans Mueller",     "email": "hans@continental.de",     "phone": "+49-30-9876", "city": "Berlin",        "country": "Germany", "rating": 3},
        {"supplier_name": "AsiaLink Trading Co",      "contact_person": "Wei Zhang",        "email": "wei@asialink.cn",         "phone": "+86-21-5678", "city": "Shanghai",      "country": "China",   "rating": 3},
        # Lower performers (generate SLA issues)
        {"supplier_name": "Bargain Bulk Wholesale",   "contact_person": "Bob Larsen",       "email": "bob@bargainbulk.com",     "phone": "+1-555-0303", "city": "Houston",       "country": "USA",     "rating": 2},
        {"supplier_name": "QuickShip Express",        "contact_person": "Priya Sharma",     "email": "priya@quickship.in",      "phone": "+91-22-8765", "city": "Mumbai",        "country": "India",   "rating": 2},
    ]
    created = []
    for sd in suppliers_data:
        existing = db.query(Supplier).filter(Supplier.email == sd["email"]).first()
        if existing:
            log(f"Supplier '{sd['supplier_name']}' already exists — skipping.")
            created.append(existing)
            continue
        supplier = Supplier(**sd)
        db.add(supplier)
        created.append(supplier)
    db.commit()
    log(f"Created {len([s for s in suppliers_data])} suppliers.")
    return db.query(Supplier).all()


def seed_products(db: Session, suppliers: list[Supplier]) -> list[Product]:
    # Map supplier names to objects
    sup = {s.supplier_name: s for s in suppliers}

    products_data = [
        # ── Electronics ─────────────────────────────────────────────────────
        {"product_name": "Wireless Noise-Cancelling Headphones", "sku": "ELEC-001", "category": "Electronics",  "unit_price": 149.99, "reorder_level": 20, "supplier": "TechSource Global"},
        {"product_name": "Smart 4K Monitor 27-inch",             "sku": "ELEC-002", "category": "Electronics",  "unit_price": 399.99, "reorder_level": 10, "supplier": "TechSource Global"},
        {"product_name": "USB-C Docking Station",                "sku": "ELEC-003", "category": "Electronics",  "unit_price": 89.99,  "reorder_level": 25, "supplier": "EliteDistributors Inc"},
        {"product_name": "Mechanical Gaming Keyboard",           "sku": "ELEC-004", "category": "Electronics",  "unit_price": 79.99,  "reorder_level": 30, "supplier": "TechSource Global"},
        {"product_name": "Ergonomic Wireless Mouse",             "sku": "ELEC-005", "category": "Electronics",  "unit_price": 49.99,  "reorder_level": 40, "supplier": "EliteDistributors Inc"},
        # ── Clothing ─────────────────────────────────────────────────────────
        {"product_name": "Premium Cotton T-Shirt",               "sku": "CLTH-001", "category": "Clothing",     "unit_price": 29.99,  "reorder_level": 50, "supplier": "PrimeParts Ltd"},
        {"product_name": "Slim-Fit Denim Jeans",                 "sku": "CLTH-002", "category": "Clothing",     "unit_price": 59.99,  "reorder_level": 40, "supplier": "PrimeParts Ltd"},
        {"product_name": "Waterproof Hiking Jacket",             "sku": "CLTH-003", "category": "Clothing",     "unit_price": 119.99, "reorder_level": 15, "supplier": "Continental Goods"},
        {"product_name": "Running Shoes Pro",                    "sku": "CLTH-004", "category": "Clothing",     "unit_price": 89.99,  "reorder_level": 25, "supplier": "AsiaLink Trading Co"},
        {"product_name": "Merino Wool Sweater",                  "sku": "CLTH-005", "category": "Clothing",     "unit_price": 74.99,  "reorder_level": 20, "supplier": "PrimeParts Ltd"},
        # ── Home & Kitchen ────────────────────────────────────────────────────
        {"product_name": "Stainless Steel Cookware Set",         "sku": "HOME-001", "category": "Home & Kitchen","unit_price": 199.99, "reorder_level": 10, "supplier": "Continental Goods"},
        {"product_name": "Smart Coffee Maker",                   "sku": "HOME-002", "category": "Home & Kitchen","unit_price": 129.99, "reorder_level": 15, "supplier": "TechSource Global"},
        {"product_name": "Bamboo Cutting Board Set",             "sku": "HOME-003", "category": "Home & Kitchen","unit_price": 34.99,  "reorder_level": 30, "supplier": "AsiaLink Trading Co"},
        {"product_name": "Air Purifier HEPA",                    "sku": "HOME-004", "category": "Home & Kitchen","unit_price": 249.99, "reorder_level": 8,  "supplier": "EliteDistributors Inc"},
        {"product_name": "Insulated Water Bottle 1L",            "sku": "HOME-005", "category": "Home & Kitchen","unit_price": 24.99,  "reorder_level": 60, "supplier": "AsiaLink Trading Co"},
        # ── Sports & Fitness ─────────────────────────────────────────────────
        {"product_name": "Adjustable Dumbbell Set",              "sku": "SPRT-001", "category": "Sports",       "unit_price": 159.99, "reorder_level": 12, "supplier": "FastTrack Supplies"},
        {"product_name": "Yoga Mat Premium",                     "sku": "SPRT-002", "category": "Sports",       "unit_price": 44.99,  "reorder_level": 35, "supplier": "FastTrack Supplies"},
        {"product_name": "Resistance Bands Set",                 "sku": "SPRT-003", "category": "Sports",       "unit_price": 19.99,  "reorder_level": 50, "supplier": "Bargain Bulk Wholesale"},
        {"product_name": "Smart Fitness Tracker",                "sku": "SPRT-004", "category": "Sports",       "unit_price": 99.99,  "reorder_level": 20, "supplier": "TechSource Global"},
        {"product_name": "Foam Roller Massage",                  "sku": "SPRT-005", "category": "Sports",       "unit_price": 29.99,  "reorder_level": 40, "supplier": "Bargain Bulk Wholesale"},
        # ── Office Supplies ───────────────────────────────────────────────────
        {"product_name": "Ergonomic Office Chair",               "sku": "OFFC-001", "category": "Office",       "unit_price": 349.99, "reorder_level": 5,  "supplier": "EliteDistributors Inc"},
        {"product_name": "Standing Desk Converter",              "sku": "OFFC-002", "category": "Office",       "unit_price": 179.99, "reorder_level": 8,  "supplier": "EliteDistributors Inc"},
        {"product_name": "Whiteboard 4x3 ft",                    "sku": "OFFC-003", "category": "Office",       "unit_price": 69.99,  "reorder_level": 10, "supplier": "QuickShip Express"},
        {"product_name": "Label Printer Thermal",                "sku": "OFFC-004", "category": "Office",       "unit_price": 54.99,  "reorder_level": 15, "supplier": "QuickShip Express"},
        {"product_name": "Premium Notebook A5 Set",              "sku": "OFFC-005", "category": "Office",       "unit_price": 12.99,  "reorder_level": 100,"supplier": "PrimeParts Ltd"},
        # ── Food & Beverage ────────────────────────────────────────────────────
        {"product_name": "Organic Green Tea 100-pack",           "sku": "FOOD-001", "category": "Food & Bev",   "unit_price": 18.99,  "reorder_level": 80, "supplier": "FastTrack Supplies"},
        {"product_name": "Whey Protein Powder 2kg",              "sku": "FOOD-002", "category": "Food & Bev",   "unit_price": 49.99,  "reorder_level": 30, "supplier": "Bargain Bulk Wholesale"},
        {"product_name": "Dark Roast Coffee Beans 1kg",          "sku": "FOOD-003", "category": "Food & Bev",   "unit_price": 24.99,  "reorder_level": 50, "supplier": "QuickShip Express"},
        {"product_name": "Mixed Nuts Premium 500g",              "sku": "FOOD-004", "category": "Food & Bev",   "unit_price": 14.99,  "reorder_level": 70, "supplier": "QuickShip Express"},
        {"product_name": "Vitamin D3 Supplement 365-tab",        "sku": "FOOD-005", "category": "Food & Bev",   "unit_price": 22.99,  "reorder_level": 60, "supplier": "Bargain Bulk Wholesale"},
    ]

    created = []
    for pd in products_data:
        existing = db.query(Product).filter(Product.sku == pd["sku"]).first()
        if existing:
            created.append(existing)
            continue
        supplier_obj = sup.get(pd["supplier"])
        product = Product(
            product_name=pd["product_name"],
            sku=pd["sku"],
            category=pd["category"],
            unit_price=pd["unit_price"],
            reorder_level=pd["reorder_level"],
            supplier_id=supplier_obj.id if supplier_obj else None,
            description=f"High-quality {pd['product_name']} — {pd['category']} category.",
        )
        db.add(product)
        created.append(product)
    db.commit()
    log(f"Created {len(products_data)} products across 6 categories.")
    return db.query(Product).all()


def seed_inventory(db: Session, products: list[Product]) -> None:
    warehouses = ["Warehouse A - New York", "Warehouse B - Chicago", "Warehouse C - Los Angeles"]

    # Stock level scenarios for demo variety
    stock_profiles = {
        # SKU → (qty_available, qty_reserved)  — some critically low for alert demos
        "ELEC-001": (5,  2),   # Low stock — alert scenario
        "ELEC-002": (45, 8),
        "ELEC-003": (3,  1),   # Critical stock — stockout risk
        "ELEC-004": (72, 10),
        "ELEC-005": (88, 15),
        "CLTH-001": (2,  0),   # Critical — reorder needed
        "CLTH-002": (94, 20),
        "CLTH-003": (28, 5),
        "CLTH-004": (7,  3),   # Low stock
        "CLTH-005": (55, 12),
        "HOME-001": (18, 4),
        "HOME-002": (4,  2),   # Low stock
        "HOME-003": (110,25),
        "HOME-004": (12, 3),
        "HOME-005": (0,  0),   # Out of stock — demo scenario
        "SPRT-001": (22, 6),
        "SPRT-002": (67, 15),
        "SPRT-003": (3,  0),   # Low stock
        "SPRT-004": (31, 8),
        "SPRT-005": (92, 20),
        "OFFC-001": (9,  2),
        "OFFC-002": (14, 4),
        "OFFC-003": (28, 5),
        "OFFC-004": (1,  0),   # Critical
        "OFFC-005": (340,60),
        "FOOD-001": (6,  0),   # Low stock
        "FOOD-002": (44, 10),
        "FOOD-003": (2,  0),   # Critical
        "FOOD-004": (120,30),
        "FOOD-005": (88, 20),
    }

    created_count = 0
    for product in products:
        existing = db.query(Inventory).filter(Inventory.product_id == product.id).first()
        if existing:
            continue
        qty, reserved = stock_profiles.get(product.sku, (random.randint(10, 150), random.randint(0, 20)))
        last_restocked = rand_dt(90, 7) if qty > 5 else rand_dt(180, 91)
        inv = Inventory(
            product_id=product.id,
            warehouse_location=random.choice(warehouses),
            quantity_available=qty,
            quantity_reserved=reserved,
            last_restocked=last_restocked,
        )
        db.add(inv)
        created_count += 1
    db.commit()
    log(f"Created {created_count} inventory records (incl. low-stock & out-of-stock scenarios).")


def seed_orders(db: Session, products: list[Product], suppliers: list[Supplier]) -> None:
    """Generate 200 orders spanning the last 12 months with realistic lifecycle timing."""

    statuses = ["delivered", "delivered", "delivered", "delivered",
                "dispatched", "processing", "pending"]

    # SLA thresholds (hours) per stage
    SLA = {
        "procurement": 48,
        "processing":  24,
        "dispatch":    12,
        "delivery":    72,
    }

    # Supplier performance multipliers (higher = slower/worse)
    perf_mult = {
        "TechSource Global":      1.0,
        "PrimeParts Ltd":         1.0,
        "EliteDistributors Inc":  1.2,
        "FastTrack Supplies":     1.1,
        "Continental Goods":      1.3,
        "AsiaLink Trading Co":    1.4,
        "Bargain Bulk Wholesale": 1.9,   # Often breaches SLA
        "QuickShip Express":      1.8,   # Often breaches SLA
    }

    # supplier id → name lookup
    sup_name = {s.id: s.supplier_name for s in suppliers}

    order_count = 0
    existing_orders = db.query(Order).count()
    if existing_orders > 0:
        log(f"Orders already exist ({existing_orders} records) — skipping order seed.")
        return

    for i in range(1, 201):
        product = random.choice(products)
        # Prefer suppliers assigned to the product
        if product.supplier_id:
            supplier_id = product.supplier_id
        else:
            supplier_id = random.choice(suppliers).id

        s_name = sup_name.get(supplier_id, "")
        mult = perf_mult.get(s_name, 1.0)

        qty = random.randint(5, 100)
        unit_price = round(product.unit_price * random.uniform(0.9, 1.1), 2)
        total = round(unit_price * qty, 2)

        placed = rand_dt(365, 1)
        status = random.choice(statuses)

        # Generate lifecycle timestamps based on status
        proc_h  = round(random.uniform(8,  52) * mult, 1)
        procd_h = round(random.uniform(4,  28) * mult, 1)
        disp_h  = round(random.uniform(2,  16) * mult, 1)
        deliv_h = round(random.uniform(24, 96) * mult, 1)

        procurement_done = add_hours(placed, proc_h)
        processing_done  = add_hours(procurement_done, procd_h) if status not in ("pending",) else None
        dispatched       = add_hours(processing_done, disp_h)   if processing_done and status in ("dispatched", "delivered") else None
        delivered        = add_hours(dispatched, deliv_h)        if dispatched and status == "delivered" else None

        # SLA breach detection
        sla_breach = False
        breached_stage = None
        bottleneck_stage = None
        worst_ratio = 0.0

        stage_times = {
            "procurement": (proc_h,  SLA["procurement"]),
            "processing":  (procd_h, SLA["processing"]),
            "dispatch":    (disp_h,  SLA["dispatch"]),
            "delivery":    (deliv_h, SLA["delivery"]),
        }

        for stage, (actual, sla_limit) in stage_times.items():
            if actual > sla_limit:
                sla_breach = True
                if not breached_stage:
                    breached_stage = stage
            ratio = actual / sla_limit
            if ratio > worst_ratio:
                worst_ratio = ratio
                bottleneck_stage = stage

        total_h = proc_h + procd_h + disp_h + deliv_h if status == "delivered" else proc_h

        order = Order(
            order_number=f"ORD-2025-{i:05d}",
            product_id=product.id,
            supplier_id=supplier_id,
            quantity=qty,
            unit_price=unit_price,
            total_amount=total,
            status=status,
            order_placed_at=placed,
            procurement_completed_at=procurement_done,
            processing_completed_at=processing_done,
            dispatched_at=dispatched,
            delivered_at=delivered,
            procurement_time=proc_h,
            processing_time=procd_h if processing_done else None,
            dispatch_time_duration=disp_h if dispatched else None,
            delivery_time_duration=deliv_h if delivered else None,
            total_time=total_h,
            sla_breach=sla_breach,
            breached_stage=breached_stage,
            bottleneck_stage=bottleneck_stage,
        )
        db.add(order)
        order_count += 1

        if order_count % 50 == 0:
            db.commit()

    db.commit()
    log(f"Created {order_count} orders (12 months, mixed statuses, SLA breaches included).")


# ════════════════════════════════════════════════════════════════
#  Reset
# ════════════════════════════════════════════════════════════════

def reset_data(db: Session) -> None:
    log("Resetting all data…")
    db.execute(Order.__table__.delete())
    db.execute(Inventory.__table__.delete())
    db.execute(Product.__table__.delete())
    db.execute(Supplier.__table__.delete())
    db.execute(User.__table__.delete())
    db.commit()
    log("All tables cleared.")


# ════════════════════════════════════════════════════════════════
#  Verification report
# ════════════════════════════════════════════════════════════════

def verify(db: Session) -> None:
    print("\n" + "═" * 55)
    print("  Seed Verification Report")
    print("═" * 55)
    counts = {
        "Users":     db.query(User).count(),
        "Suppliers": db.query(Supplier).count(),
        "Products":  db.query(Product).count(),
        "Inventory": db.query(Inventory).count(),
        "Orders":    db.query(Order).count(),
    }
    for table, count in counts.items():
        print(f"  {table:<15} {count:>5} records")

    breached = db.query(Order).filter(Order.sla_breach == True).count()
    delivered = db.query(Order).filter(Order.status == "delivered").count()
    low_stock = db.query(Inventory).filter(Inventory.quantity_available < 10).count()
    out_of_stock = db.query(Inventory).filter(Inventory.quantity_available == 0).count()

    print("─" * 55)
    print(f"  Orders delivered        {delivered:>5}")
    print(f"  SLA breaches            {breached:>5}")
    print(f"  Low-stock items (<10)   {low_stock:>5}")
    print(f"  Out-of-stock items      {out_of_stock:>5}")
    print("═" * 55)
    print("\n  Demo Credentials:")
    print("    admin    / Admin@123   (full access)")
    print("    manager  / Manager@123 (reports + ops)")
    print("    analyst  / Analyst@123 (read-only analytics)")
    print("\n  API Docs:  http://localhost:8000/docs")
    print("  Health:    http://localhost:8000/health/detailed")
    print("═" * 55 + "\n")


# ════════════════════════════════════════════════════════════════
#  Main
# ════════════════════════════════════════════════════════════════

def main() -> None:
    parser = argparse.ArgumentParser(description="Smart Retail demo seed data")
    parser.add_argument("--reset",  action="store_true", help="Clear all data before seeding")
    parser.add_argument("--verify", action="store_true", help="Print verification report after seeding")
    args = parser.parse_args()

    print("\n" + "═" * 55)
    print("  Smart Retail — Demo Seed Data")
    print("═" * 55)

    # Init tables
    init_db()

    db: Session = SessionLocal()
    try:
        if args.reset:
            reset_data(db)

        print("\n[1/5] Seeding users…")
        seed_users(db)

        print("\n[2/5] Seeding suppliers…")
        suppliers = seed_suppliers(db)

        print("\n[3/5] Seeding products…")
        products = seed_products(db, suppliers)

        print("\n[4/5] Seeding inventory…")
        seed_inventory(db, products)

        print("\n[5/5] Seeding orders (200 records)…")
        seed_orders(db, products, suppliers)

        print("\n  Seed complete!\n")

        if args.verify:
            verify(db)

    finally:
        db.close()


if __name__ == "__main__":
    main()
