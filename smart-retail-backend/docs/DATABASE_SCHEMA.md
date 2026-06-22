# Smart Retail Platform — Database Schema Reference

## Entity Relationship Overview

```
users
  │
  (no FK — auth only)

suppliers (1)
  │
  ├──────────── products (N)
  │               │
  │               ├──── inventory (1:1 per product)
  │               │
  │               └──── orders (N)  ◄── also references suppliers
  │
  └──────────── orders (N) ◄── via supplier_id

notifications (standalone — references product/supplier/order by id)
```

---

## Table: `users`

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | INTEGER | PK, autoincrement | Unique user identifier |
| `username` | VARCHAR(100) | UNIQUE, NOT NULL, indexed | Login username |
| `email` | VARCHAR(255) | UNIQUE, NOT NULL, indexed | Email address |
| `hashed_password` | VARCHAR(255) | NOT NULL | bcrypt hash |
| `full_name` | VARCHAR(255) | nullable | Display name |
| `role` | VARCHAR(50) | NOT NULL, default='user' | `admin` \| `manager` \| `user` |
| `is_active` | BOOLEAN | NOT NULL, default=True | Account enabled flag |
| `refresh_token` | TEXT | nullable | Hashed refresh token (cleared on logout) |
| `last_login` | DATETIME | nullable | Last successful login timestamp |
| `created_at` | DATETIME | server_default=now() | Account creation time |
| `updated_at` | DATETIME | onupdate=now() | Last modification time |

**Indexes:** `username`, `email`

**RBAC roles:**
- `admin` — Full access: create/delete users, suppliers, products, orders
- `manager` — Reports, analytics, order management, read-only on users
- `user` — Read-only analytics, view reports

---

## Table: `suppliers`

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | INTEGER | PK, autoincrement | Unique supplier ID |
| `supplier_name` | VARCHAR(255) | NOT NULL, indexed | Company name |
| `contact_person` | VARCHAR(255) | nullable | Primary contact name |
| `email` | VARCHAR(255) | UNIQUE, indexed | Contact email |
| `phone` | VARCHAR(50) | nullable | Contact phone |
| `address` | TEXT | nullable | Street address |
| `city` | VARCHAR(100) | nullable | City |
| `country` | VARCHAR(100) | nullable | Country |
| `rating` | INTEGER | nullable | Performance rating 1–5 |
| `created_at` | DATETIME | server_default=now() | |
| `updated_at` | DATETIME | onupdate=now() | |

**Referenced by:** `products.supplier_id`, `orders.supplier_id`

---

## Table: `products`

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | INTEGER | PK, autoincrement | Unique product ID |
| `product_name` | VARCHAR(255) | NOT NULL, indexed | Full product name |
| `sku` | VARCHAR(100) | UNIQUE, NOT NULL, indexed | Stock Keeping Unit code |
| `category` | VARCHAR(100) | indexed | Product category |
| `description` | VARCHAR(500) | nullable | Product description |
| `unit_price` | FLOAT | NOT NULL | Selling price per unit |
| `supplier_id` | INTEGER | FK → suppliers.id | Primary supplier |
| `reorder_level` | INTEGER | default=10 | Minimum stock before reorder alert |
| `created_at` | DATETIME | server_default=now() | |
| `updated_at` | DATETIME | onupdate=now() | |

**Indexes:** `product_name`, `sku`, `category`  
**Referenced by:** `inventory.product_id`, `orders.product_id`

---

## Table: `inventory`

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | INTEGER | PK, autoincrement | Unique inventory record ID |
| `product_id` | INTEGER | NOT NULL, FK → products.id, indexed | Product reference |
| `warehouse_location` | VARCHAR(255) | nullable | Warehouse name / location |
| `quantity_available` | INTEGER | default=0 | Units currently in stock |
| `quantity_reserved` | INTEGER | default=0 | Units reserved for pending orders |
| `last_restocked` | DATETIME | nullable | Timestamp of last restock |
| `created_at` | DATETIME | server_default=now() | |
| `updated_at` | DATETIME | onupdate=now() | |

**Derived fields (calculated, not stored):**
- `net_available = quantity_available - quantity_reserved`
- `days_of_coverage = quantity_available / avg_daily_demand`
- `stockout_risk_score` = calculated by recommendation service

---

## Table: `orders`

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | INTEGER | PK, autoincrement | |
| `order_number` | VARCHAR(100) | UNIQUE, NOT NULL, indexed | Human-readable order ID (e.g. ORD-2025-00001) |
| `product_id` | INTEGER | NOT NULL, FK → products.id, indexed | |
| `supplier_id` | INTEGER | FK → suppliers.id, indexed | |
| `quantity` | INTEGER | NOT NULL | Units ordered |
| `unit_price` | FLOAT | nullable | Price at order time |
| `total_amount` | FLOAT | nullable | quantity × unit_price |
| `status` | VARCHAR(50) | default='pending', indexed | `pending` \| `processing` \| `dispatched` \| `delivered` |
| **Lifecycle Timestamps** | | | |
| `order_placed_at` | DATETIME | nullable | Stage 0: order created |
| `procurement_completed_at` | DATETIME | nullable | Stage 1: supplier confirmed |
| `processing_completed_at` | DATETIME | nullable | Stage 2: warehouse packed |
| `dispatched_at` | DATETIME | nullable | Stage 3: shipped |
| `delivered_at` | DATETIME | nullable | Stage 4: customer received |
| **Calculated Analytics** | | | |
| `procurement_time` | FLOAT | nullable | Hours: placed → procurement |
| `processing_time` | FLOAT | nullable | Hours: procurement → processing |
| `dispatch_time_duration` | FLOAT | nullable | Hours: processing → dispatch |
| `delivery_time_duration` | FLOAT | nullable | Hours: dispatch → delivery |
| `total_time` | FLOAT | nullable | Hours: placed → delivered |
| **SLA Fields** | | | |
| `sla_breach` | BOOLEAN | default=False | True if any stage exceeded SLA threshold |
| `breached_stage` | VARCHAR(100) | nullable | First stage that breached SLA |
| **Bottleneck Fields** | | | |
| `bottleneck_stage` | VARCHAR(100) | nullable | Stage with longest relative delay |
| `created_at` | DATETIME | server_default=now() | |
| `updated_at` | DATETIME | onupdate=now() | |

**SLA Thresholds:**
| Stage | Threshold |
|-------|-----------|
| Procurement | 48 hours |
| Processing | 24 hours |
| Dispatch | 12 hours |
| Delivery | 72 hours |

---

## Table: `notifications`

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | INTEGER | PK, autoincrement | |
| `category` | VARCHAR(100) | NOT NULL | `LOW_STOCK` \| `OUT_OF_STOCK` \| `SLA_BREACH` \| `SUPPLIER_PERFORMANCE` \| `FORECAST_DEVIATION` |
| `priority` | VARCHAR(50) | NOT NULL | `critical` \| `high` \| `medium` \| `low` |
| `title` | VARCHAR(255) | NOT NULL | Short alert title |
| `message` | TEXT | NOT NULL | Full alert description |
| `product_id` | INTEGER | nullable | Referenced product |
| `product_name` | VARCHAR(255) | nullable | Denormalized for display |
| `supplier_id` | INTEGER | nullable | Referenced supplier |
| `order_id` | INTEGER | nullable | Referenced order |
| `metric_value` | FLOAT | nullable | Numeric value that triggered alert |
| `metric_label` | VARCHAR(100) | nullable | Label for metric_value |
| `is_read` | BOOLEAN | default=False | Read status |
| `is_resolved` | BOOLEAN | default=False | Resolution status |
| `resolved_at` | DATETIME | nullable | When resolved |
| `dedup_key` | VARCHAR(255) | **UNIQUE** | Prevents duplicate alerts (hash of category+entity+date) |
| `created_at` | DATETIME | server_default=now() | |
| `updated_at` | DATETIME | onupdate=now() | |

**Key design:** `dedup_key` unique constraint prevents alert storms — running the notification engine multiple times in a day produces at most one alert per product/event per day.

---

## Key Query Patterns

### SLA Analytics
```sql
SELECT
    breached_stage,
    COUNT(*) as breach_count,
    AVG(total_time) as avg_total_hours
FROM orders
WHERE sla_breach = TRUE
  AND order_placed_at BETWEEN :start AND :end
GROUP BY breached_stage
ORDER BY breach_count DESC;
```

### Inventory Health
```sql
SELECT
    p.sku,
    p.product_name,
    p.category,
    i.quantity_available,
    p.reorder_level,
    CASE
        WHEN i.quantity_available = 0 THEN 'out_of_stock'
        WHEN i.quantity_available < p.reorder_level THEN 'low_stock'
        ELSE 'healthy'
    END as stock_status
FROM inventory i
JOIN products p ON i.product_id = p.id;
```

### Revenue Trends (SQLite-compatible)
```sql
SELECT
    strftime('%Y-%m-%d', order_placed_at) as date,
    SUM(total_amount) as revenue,
    COUNT(*) as orders
FROM orders
WHERE status = 'delivered'
  AND order_placed_at BETWEEN :start AND :end
GROUP BY date
ORDER BY date;
```

### Supplier Performance
```sql
SELECT
    s.supplier_name,
    COUNT(o.id) as total_orders,
    AVG(o.procurement_time) as avg_lead_time_hours,
    ROUND(100.0 * SUM(CASE WHEN o.sla_breach THEN 1 ELSE 0 END) / COUNT(o.id), 2) as sla_breach_rate
FROM orders o
JOIN suppliers s ON o.supplier_id = s.id
GROUP BY s.id, s.supplier_name
ORDER BY sla_breach_rate ASC;
```
