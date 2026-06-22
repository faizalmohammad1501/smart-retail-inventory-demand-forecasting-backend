# Smart Retail Platform — Complete API Reference

Base URL: `http://localhost:8000`  
Authentication: All endpoints (except `/health`, `/api/auth/login`, `/api/auth/register`) require:
```
Authorization: Bearer <access_token>
```

---

## Authentication — `/api/auth`

### POST `/api/auth/register`
Create a new user account.

**Request body:**
```json
{
  "username": "john",
  "email": "john@example.com",
  "full_name": "John Doe",
  "role": "user",
  "password": "Secret@123"
}
```
**Response:** `201` — User object (id, username, email, role, created_at)

---

### POST `/api/auth/login`
Authenticate and receive tokens.

**Request body:**
```json
{ "username": "admin", "password": "Admin@123" }
```
**Response:** `200`
```json
{
  "access_token": "eyJ...",
  "refresh_token": "eyJ...",
  "token_type": "bearer",
  "expires_in": 1800
}
```

---

### POST `/api/auth/refresh`
Rotate access token using refresh token.

**Header:** `Authorization: Bearer <refresh_token>`  
**Response:** `200` — New `{ access_token, expires_in }`

---

### POST `/api/auth/logout`
Invalidate refresh token.  
**Response:** `200` — `{ message: "Logged out" }`

---

### GET `/api/auth/profile`
Get current user's profile.  
**Response:** `200` — User object

---

### PUT `/api/auth/profile/change-password`
**Request body:** `{ current_password, new_password }`  
**Response:** `200`

---

## Inventory & Products — `/api/inventory`

### POST `/api/inventory/products/`
Create a product.
```json
{
  "product_name": "Wireless Headphones",
  "sku": "ELEC-001",
  "category": "Electronics",
  "unit_price": 149.99,
  "reorder_level": 20,
  "supplier_id": 1,
  "description": "Optional"
}
```

### GET `/api/inventory/products/`
List all products. Query params: `skip`, `limit`, `category`

### GET `/api/inventory/products/{id}`
Single product by ID.

### PUT `/api/inventory/products/{id}`
Update product fields.

### DELETE `/api/inventory/products/{id}`
Delete product. **Requires: admin**

### GET `/api/inventory/`
List inventory records. Query params: `skip`, `limit`

### PUT `/api/inventory/{id}`
Update stock levels.
```json
{ "quantity_available": 50, "quantity_reserved": 10, "warehouse_location": "Warehouse A" }
```

---

## Orders & Sales — `/api/sales`

### POST `/api/sales/orders/`
Create order. SLA breach and bottleneck fields auto-calculated.
```json
{
  "product_id": 1,
  "supplier_id": 1,
  "quantity": 50,
  "unit_price": 149.99,
  "total_amount": 7499.50,
  "status": "pending",
  "order_placed_at": "2026-06-01T10:00:00Z"
}
```

### GET `/api/sales/orders/`
List orders. Query params: `status`, `start_date`, `end_date`, `skip`, `limit`

### GET `/api/sales/orders/{id}`
Order detail with all lifecycle timestamps and analytics.

### PATCH `/api/sales/orders/{id}/status`
Advance order status. **Body:** `{ "status": "processing" }`

### DELETE `/api/sales/orders/{id}`
Delete order. **Requires: admin**

---

## Suppliers — `/api/suppliers`

### POST `/api/suppliers/`
```json
{
  "supplier_name": "TechSource Global",
  "email": "contact@techsource.com",
  "contact_person": "James T",
  "city": "San Francisco",
  "country": "USA",
  "rating": 5
}
```

### GET `/api/suppliers/` — List all
### GET `/api/suppliers/{id}` — Single supplier
### PUT `/api/suppliers/{id}` — Update
### DELETE `/api/suppliers/{id}` — Delete **(admin)**

---

## Analytics — `/api/analytics`

### GET `/api/analytics/summary`
KPI overview: total orders, delivered count, SLA compliance rate, avg cycle times, bottleneck distribution.

### GET `/api/analytics/bottlenecks`
Bottleneck analysis per stage. Query params: `start_date`, `end_date`

**Response:** `[{ stage, count, avg_delay_hours, percentage }]`

### GET `/api/analytics/sla-breaches`
All SLA-breached orders with breach stage. Query params: `start_date`, `end_date`, `stage`

---

## ML Pipeline — `/api/ml/pipeline`

### POST `/api/ml/pipeline/generate`
Generate synthetic training dataset (20 products × 365 days).  
**Response:** `{ records_generated, output_path, columns }`

### POST `/api/ml/pipeline/run`
Execute full preprocessing pipeline (validate → clean → features → scale → split).  
**Response:** `{ train_size, val_size, test_size, features, status }`

### GET `/api/ml/pipeline/status`
Pipeline status, dataset sizes, last run timestamp.

### GET `/api/ml/pipeline/features`
List of engineered feature names.

### GET `/api/ml/pipeline/config`
ML configuration: horizon, lookback, lag days, rolling windows.

### GET `/api/ml/pipeline/download/{split}`
Download split CSV. `split` ∈ `train | val | test`

---

## Demand Forecasting — `/api/predictions`

### POST `/api/predictions/train`
Train the GradientBoosting demand forecasting model.  
**Response:** `{ status, mae, rmse, r2, training_samples, feature_count }`

### GET `/api/predictions/model/status`
Model metadata: trained_at, MAE, R², feature importance.

### POST `/api/predictions/forecast`
Generate multi-day demand forecast.
```json
{ "product_id": 1, "days": 30 }
```
**Response:**
```json
{
  "product_id": 1,
  "predictions": [
    { "date": "2026-06-20", "predicted_demand": 42.3, "confidence_low": 35.9, "confidence_high": 48.6 }
  ]
}
```

### GET `/api/predictions/forecast/{product_id}`
Cached forecast for a specific product.

### GET `/api/predictions/forecast`
Forecasts for all products.

---

## Inventory Recommendations — `/api/recommendations`

### GET `/api/recommendations/`
All recommendations with risk scores.

### GET `/api/recommendations/health`
Overall inventory health summary.
```json
{ "health_score": 74, "grade": "B", "total_products": 30, "at_risk": 6 }
```

### GET `/api/recommendations/alerts`
Products below reorder level or out of stock.

### GET `/api/recommendations/replenishment`
EOQ-based reorder list sorted by urgency.
```json
[{ "product_id": 3, "sku": "ELEC-003", "quantity_available": 3, "recommended_order_qty": 45, "urgency": "critical" }]
```

### GET `/api/recommendations/{product_id}`
Single product recommendation with EOQ calculation.

---

## Notifications — `/api/notifications`

### POST `/api/notifications/run`
Execute the alert engine. Generates and deduplicates alerts for all 5 alert types.  
**Response:** `{ alerts_created, alerts_skipped, categories }`

### GET `/api/notifications/summary`
Alert counts by category and priority.

### GET `/api/notifications/`
List notifications. Query params: `category`, `priority`, `is_read`, `is_resolved`, `skip`, `limit`

### PATCH `/api/notifications/read-all`
Mark all unread notifications as read.

### PATCH `/api/notifications/{id}/read`
Mark single notification as read.

### PATCH `/api/notifications/{id}/resolve`
Resolve a notification. Body: `{ "resolution_note": "..." }` (optional)

### DELETE `/api/notifications/{id}`
Delete a notification.

---

## Business Reports — `/api/reports`

All report endpoints accept query params: `start_date`, `end_date` (ISO 8601), `category`, `supplier_id`

### GET `/api/reports/sales/summary`
Revenue, order count, AOV, delivered rate, cancelled rate.

### GET `/api/reports/sales/trends`
Daily/weekly revenue time series. Query: `granularity=daily|weekly|monthly`

### GET `/api/reports/sales/top-products`
Top N products by revenue. Query: `limit=10`

### GET `/api/reports/sales/by-category`
Revenue breakdown with % share per category.

### GET `/api/reports/sales/fulfillment`
Fill rate, on-time delivery rate, avg fulfillment time.

### GET `/api/reports/inventory/valuation`
Total stock value by category (qty × unit_price).

### GET `/api/reports/inventory/turnover`
Inventory turnover ratio = COGS / avg_inventory_value per product.

### GET `/api/reports/inventory/aging`
Stock age buckets: 0-30d / 31-60d / 61-90d / 90d+.

### GET `/api/reports/suppliers/performance`
Ranked list: order count, SLA breach rate, avg lead time, rating.

### GET `/api/reports/suppliers/{id}/scorecard`
Full scorecard for one supplier: all KPIs + performance trend.

### GET `/api/reports/forecast/accuracy`
Model accuracy metrics: MAE, RMSE, R², MAPE per product.

### GET `/api/reports/operations/kpis`
Avg procurement/processing/dispatch/delivery times, SLA rate, fill rate.

### GET `/api/reports/operations/sla-compliance`
SLA compliance % per stage with breach counts.

### GET `/api/reports/operations/bottlenecks`
Bottleneck frequency by stage with avg delay hours.

### GET `/api/reports/export/sales`
Download sales data as CSV. Accepts same date/category filters.

### GET `/api/reports/export/inventory`
Download inventory CSV.

### GET `/api/reports/export/suppliers`
Download supplier performance CSV.

---

## Executive Dashboard — `/api/dashboard`

### GET `/api/dashboard/summary`
Master dashboard — all 5 widgets + all 6 charts in one call. Query: `days=30`

### GET `/api/dashboard/widgets/sales`
Sales KPIs: revenue, orders, AOV, delivered rate + 7-day sparkline + PoP % change.

### GET `/api/dashboard/widgets/inventory`
Stock health: total products, low-stock count, out-of-stock count, avg coverage days.

### GET `/api/dashboard/widgets/suppliers`
Supplier KPIs: active count, avg rating, top performer, SLA compliance rate.

### GET `/api/dashboard/widgets/forecast`
Forecast widget: model accuracy, next 7-day demand summary.

### GET `/api/dashboard/widgets/alerts`
Alert counts by priority (critical/high/medium/low), unread count.

### GET `/api/dashboard/charts/revenue-trend`
Chart.js line data: daily revenue over `days` period.

### GET `/api/dashboard/charts/order-status`
Chart.js doughnut: order status distribution.

### GET `/api/dashboard/charts/top-products`
Chart.js bar: top 10 products by revenue.

### GET `/api/dashboard/charts/inventory-health`
Chart.js bar: stock levels vs reorder levels per product.

### GET `/api/dashboard/charts/supplier-performance`
Chart.js bar: supplier SLA compliance ranking.

### GET `/api/dashboard/charts/category-revenue`
Chart.js pie: revenue share by category.

### GET `/api/dashboard/export/forecast-accuracy`
Forecast accuracy CSV.

### GET `/api/dashboard/export/notifications`
Notifications export CSV.

### GET `/api/dashboard/export/full-report`
Combined full report CSV.

### GET `/api/dashboard/export/pdf/sales`
Sales PDF report. **Requires fpdf2.**

### GET `/api/dashboard/export/pdf/inventory`
Inventory PDF report.

### GET `/api/dashboard/export/pdf/suppliers`
Suppliers PDF report.

### GET `/api/dashboard/export/pdf/executive`
Executive summary PDF.

---

## Business Intelligence — `/api/bi`

### GET `/api/bi/executive-summary`
Single-call executive KPI pack.
```json
{
  "revenue": 485230.50,
  "revenue_growth_pct": 12.4,
  "gross_margin_pct": 34.2,
  "sla_compliance_rate": 78.5,
  "fill_rate": 91.3,
  "forecast_accuracy_score": 87.0,
  "total_orders": 1240,
  "active_alerts": 8
}
```

### GET `/api/bi/kpi-trends`
Time-series for 5 KPIs. Query: `days=90`, `granularity=daily|weekly|monthly`
Returns: `{ labels: [...], revenue: [...], orders: [...], aov: [...], sla_rate: [...], fill_rate: [...] }`

### GET `/api/bi/profitability`
Gross margin analysis. Query: `days=90`
Returns: category margins + top 10 / bottom 10 products by margin %.

### GET `/api/bi/period-comparison`
MoM / QoQ / YoY comparison. Query: `period=monthly|quarterly|yearly`
Returns: `{ current, prior, changes: { revenue_pct, orders_pct, aov_pct, sla_rate_pct } }`

### GET `/api/bi/inventory-health-score`
Composite 0–100 health score with sub-scores.
```json
{
  "overall_score": 72,
  "grade": "B",
  "availability_score": 80,
  "turnover_score": 65,
  "aging_score": 70,
  "reorder_risk_score": 74,
  "at_risk_products": 6,
  "critical_products": 2
}
```

### GET `/api/bi/supplier-intelligence`
Advanced supplier metrics. Query: `days=90`, `limit=10`
Returns per supplier: avg_lead_time, reliability_score, cost_performance_index, sla_breach_index.

### GET `/api/bi/forecast-performance`
Forecast accuracy over time per product. Query: `days=90`, `product_id` (optional)

### GET `/api/bi/cohort-analysis`
Order cohort breakdown. Query: `days=180`, `groupby=weekly|monthly`
Returns: `{ cohort_label, orders, revenue, sla_compliance_rate }` per cohort.

### GET `/api/bi/alerts-intelligence`
Alert pattern analytics.
```json
{
  "total_alerts": 142,
  "resolved_rate": 68.3,
  "mean_time_to_resolve_hours": 4.2,
  "top_category": "LOW_STOCK",
  "trend": [{ "date": "2026-06-15", "count": 12 }]
}
```

### GET `/api/bi/strategic-insights`
Auto-generated text recommendations triggered by KPI threshold analysis.
```json
{
  "insights": [
    { "category": "inventory", "severity": "high", "insight": "6 products below reorder level — initiate replenishment immediately.", "action": "Review /api/recommendations/replenishment" },
    { "category": "sla", "severity": "medium", "insight": "SLA compliance at 78.5% — below 85% target.", "action": "Review supplier performance at /api/bi/supplier-intelligence" }
  ],
  "generated_at": "2026-06-22T10:00:00Z"
}
```

---

## Health Endpoints

### GET `/health`
Liveness probe. Always returns `200` if server is running.
```json
{ "status": "healthy", "service": "Smart Retail Analytics API", "version": "2.0.0" }
```

### GET `/health/detailed`
Readiness probe. Checks DB connectivity, ML model, datasets, disk space.
```json
{
  "status": "healthy",
  "checks": {
    "database": { "status": "healthy", "tables": 6, "total_rows": 1540 },
    "ml_model": { "status": "healthy", "model_exists": true, "trained_at": "..." },
    "ml_datasets": { "status": "healthy", "train_rows": 5110 },
    "disk_space": { "status": "healthy", "available_gb": 45.2 }
  },
  "system": { "python_version": "3.11.9", "platform": "Linux" }
}
```

---

## Standard Response Envelope

**Success:**
```json
{ "status": "success", "data": { ... }, "timestamp": "2026-06-22T10:00:00Z" }
```

**Error:**
```json
{ "status": "error", "detail": "Not found", "code": "NOT_FOUND", "request_id": "a1b2c3d4-..." }
```

**Validation error:**
```json
{
  "status": "error",
  "detail": "Validation failed",
  "code": "VALIDATION_ERROR",
  "errors": [{ "field": "unit_price", "message": "must be greater than 0" }]
}
```
