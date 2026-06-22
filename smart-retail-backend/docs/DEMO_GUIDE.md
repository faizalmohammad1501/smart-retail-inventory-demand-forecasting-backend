# Smart Retail Platform — Demo Guide

This guide walks evaluators, interviewers, and academic reviewers through a live demo of the full platform.

**Total demo time:** ~20–30 minutes  
**Prerequisites:** Python 3.11+, dependencies installed

---

## Setup (5 minutes)

```bash
# 1. Navigate to project
cd smart-retail-backend

# 2. Install dependencies
pip install -r requirements.txt

# 3. Start the API server
python main.py
# Server starts at http://localhost:8000
# Swagger UI: http://localhost:8000/docs

# 4. Open a second terminal for demo commands
# 5. Seed demo data (in second terminal)
python demo_seed.py
```

**Expected output from demo_seed.py:**
```
[+] Created 3 users: admin / manager / analyst
[+] Created 8 suppliers (4 performance tiers)
[+] Created 30 products across 6 categories
[+] Seeded 300 inventory records
[+] Created 200 orders over 12 months
[+] Intentional: 12 low-stock, 3 out-of-stock products
[+] Intentional: 38 SLA breaches seeded
[✓] Demo database ready.
```

**Demo credentials:**
| User | Password | Role |
|------|----------|------|
| admin | Admin@123 | Full access |
| manager | Manager@123 | Analytics + orders |
| analyst | Analyst@123 | Read-only reports |

---

## Scenario 1: Authentication & Role-Based Access Control

**Talking points:** JWT tokens, refresh token rotation, role enforcement.

### 1.1 Login as admin
```bash
curl -X POST http://localhost:8000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username": "admin", "password": "Admin@123"}'
```
**Show:** access_token + refresh_token returned. Copy access_token as `TOKEN`.

### 1.2 Test RBAC — analyst cannot delete products
```bash
# Login as analyst first, use that token
curl -X DELETE http://localhost:8000/api/inventory/products/1 \
  -H "Authorization: Bearer <analyst_token>"
# Expected: 403 Forbidden
```

### 1.3 Refresh token rotation
```bash
curl -X POST http://localhost:8000/api/auth/refresh \
  -H "Authorization: Bearer <refresh_token>"
# Returns new access_token without re-login
```

---

## Scenario 2: ML Forecasting Pipeline (End-to-End)

**Talking points:** Data engineering → Feature engineering → GradientBoosting → Autoregressive forecast.

### 2.1 Generate synthetic training data
```bash
curl -X POST http://localhost:8000/api/ml/pipeline/generate \
  -H "Authorization: Bearer $TOKEN"
# Generates 7,300 records (20 products × 365 days)
```

### 2.2 Run preprocessing pipeline
```bash
curl -X POST http://localhost:8000/api/ml/pipeline/run \
  -H "Authorization: Bearer $TOKEN"
# Output: train_size, val_size, test_size, ~30 feature names
```

### 2.3 View engineered features (impressive talking point)
```bash
curl http://localhost:8000/api/ml/pipeline/features \
  -H "Authorization: Bearer $TOKEN"
# Shows: lag_1, lag_7, lag_28, rolling_mean_30, rolling_std_14, is_weekend, etc.
```

### 2.4 Train the model
```bash
curl -X POST http://localhost:8000/api/predictions/train \
  -H "Authorization: Bearer $TOKEN"
# Shows: MAE, RMSE, R² score (typically R²=0.75–0.92)
```

### 2.5 Generate 30-day forecast
```bash
curl -X POST http://localhost:8000/api/predictions/forecast \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"product_id": 1, "days": 30}'
# Returns daily predictions with confidence intervals
```

**Expected output highlight:**
```json
{
  "product_id": 1,
  "predictions": [
    { "date": "2026-06-23", "predicted_demand": 42.3, "confidence_low": 35.9, "confidence_high": 48.6 },
    ...
  ]
}
```

---

## Scenario 3: Inventory Command Centre

**Talking points:** Real-time stock monitoring, reorder recommendations, EOQ calculations.

### 3.1 Inventory health score
```bash
curl http://localhost:8000/api/bi/inventory-health-score \
  -H "Authorization: Bearer $TOKEN"
# Shows: overall score 0-100, grade A-F, sub-scores
```

### 3.2 Smart reorder recommendations
```bash
curl http://localhost:8000/api/recommendations/replenishment \
  -H "Authorization: Bearer $TOKEN"
# Shows: products sorted by urgency with EOQ-based quantities
```

### 3.3 Critical alerts
```bash
curl "http://localhost:8000/api/notifications/?priority=critical" \
  -H "Authorization: Bearer $TOKEN"
# Shows: out-of-stock + critical SLA breach notifications
```

**Talking point:** The system auto-deduplicates alerts — running the notification engine multiple times in one day produces exactly one alert per event.

---

## Scenario 4: Order Lifecycle & SLA Monitoring

**Talking points:** Full procurement lifecycle, bottleneck detection, SLA breach tracking.

### 4.1 Create an order and advance through lifecycle
```bash
# Create order
curl -X POST http://localhost:8000/api/sales/orders/ \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"product_id": 1, "supplier_id": 1, "quantity": 50, "total_amount": 7499.50, "status": "pending"}'

# Advance to processing (use id from response)
curl -X PATCH http://localhost:8000/api/sales/orders/1/status \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"status": "processing"}'
```

### 4.2 View SLA analytics
```bash
curl http://localhost:8000/api/analytics/sla-breaches \
  -H "Authorization: Bearer $TOKEN"
# Shows: 38 seeded breaches with stage breakdown
```

### 4.3 Bottleneck analysis
```bash
curl http://localhost:8000/api/analytics/bottlenecks \
  -H "Authorization: Bearer $TOKEN"
# Shows: which stage causes most delays (procurement / dispatch / etc.)
```

---

## Scenario 5: Supplier Intelligence & Scorecard

**Talking points:** Multi-tier supplier ranking, SLA reliability scoring, data-driven procurement decisions.

### 5.1 Supplier performance ranking
```bash
curl http://localhost:8000/api/reports/suppliers/performance \
  -H "Authorization: Bearer $TOKEN"
# Shows: ranked by SLA compliance — Tier 1 (elite) vs Tier 4 (poor)
```

### 5.2 Advanced supplier intelligence (BI module)
```bash
curl http://localhost:8000/api/bi/supplier-intelligence \
  -H "Authorization: Bearer $TOKEN"
# Shows: reliability_score, cost_performance_index, avg_lead_time
```

### 5.3 Individual supplier scorecard
```bash
curl http://localhost:8000/api/reports/suppliers/1/scorecard \
  -H "Authorization: Bearer $TOKEN"
# Shows: full KPI breakdown for supplier 1
```

---

## Scenario 6: Executive Dashboard & BI Reports

**Talking points:** C-suite analytics, period-over-period comparison, strategic AI-generated insights.

### 6.1 Executive summary (single API call)
```bash
curl http://localhost:8000/api/bi/executive-summary \
  -H "Authorization: Bearer $TOKEN"
# Revenue, growth %, gross margin, SLA rate, fill rate, forecast accuracy
```

### 6.2 Period-over-period comparison (MoM)
```bash
curl "http://localhost:8000/api/bi/period-comparison?period=monthly" \
  -H "Authorization: Bearer $TOKEN"
# Shows: current vs prior month with % change in all KPIs
```

### 6.3 AI-generated strategic insights
```bash
curl http://localhost:8000/api/bi/strategic-insights \
  -H "Authorization: Bearer $TOKEN"
# Auto-generated recommendations based on KPI thresholds
```

**Expected highlight:**
```json
{
  "insights": [
    { "severity": "high", "insight": "6 products below reorder level — initiate replenishment immediately." },
    { "severity": "medium", "insight": "SLA compliance at 78.5% — below 85% target. Review supplier performance." }
  ]
}
```

### 6.4 Master dashboard (all widgets + charts in one call)
```bash
curl "http://localhost:8000/api/dashboard/summary?days=30" \
  -H "Authorization: Bearer $TOKEN"
# Returns 5 KPI widgets + 6 Chart.js-ready datasets
```

---

## Scenario 7: Data Exports

**Talking points:** Business-ready exports for offline analysis.

```bash
# CSV exports
curl "http://localhost:8000/api/reports/export/sales" \
  -H "Authorization: Bearer $TOKEN" \
  -o sales_export.csv

curl "http://localhost:8000/api/reports/export/inventory" \
  -H "Authorization: Bearer $TOKEN" \
  -o inventory_export.csv

# Download training dataset (ML transparency)
curl "http://localhost:8000/api/ml/pipeline/download/train" \
  -H "Authorization: Bearer $TOKEN" \
  -o training_data.csv
```

---

## Automated QA Suite

Run the full 100+ test quality assurance suite:

```bash
python test_qa.py
```

**What it checks:**
- Health endpoints + security headers
- Auth flows (login, refresh, RBAC)
- All CRUD operations
- Analytics accuracy
- ML pipeline end-to-end
- All 14 reports
- 20 dashboard endpoints
- Data consistency (DB vs API responses)

**Expected output:**
```
Running 16 test groups...
[✓] Health & readiness
[✓] Security headers (OWASP)
[✓] Authentication flows
[✓] RBAC enforcement
[✓] Inventory CRUD
[✓] Order lifecycle
[✓] ML pipeline
[✓] Demand forecasting
[✓] Recommendations
[✓] Notifications
[✓] 14 business reports
[✓] Dashboard (20 endpoints)
[✓] CSV exports
[✓] Data consistency

Passed: 104/104  (100%)
```

---

## Automated Demo Runner

Run all 6 scenarios automatically with pause prompts:

```bash
# Full auto-run (no pauses)
python demo_workflow.py

# With pauses between scenarios (for live demos)
python demo_workflow.py --pause

# Run a single scenario
python demo_workflow.py --scenario 2
```

---

## Common Interview Questions

**Q: Why GradientBoosting and not LSTM?**  
Retail demand data is tabular with engineered lag/rolling features. GBR achieves comparable accuracy on this structure while being 10× faster to train, requiring no GPU, and providing interpretable feature importance. LSTM would be preferred if raw sequences without feature engineering were the input.

**Q: How does the deduplication in notifications work?**  
A SHA-256 hash of `category + entity_id + date` is stored in the `dedup_key` column with a UNIQUE constraint. The database enforces exactly one alert per event per day — no application-level deduplication code needed.

**Q: How does the autoregressive forecast work for multi-step predictions?**  
The model predicts one step at a time. Each predicted value is appended to the input window, and the next prediction uses that predicted value as a lag feature. This compounds — error accumulates over longer horizons, which is why confidence bands widen as you forecast further out.

**Q: How is data leakage prevented in ML training?**  
The `StandardScaler` is fitted only on the training set, then applied to validation and test sets. Dataset splits are chronological (not random), so the model never sees future data during training.

**Q: What's the health score algorithm?**  
Four sub-scores: availability (out-of-stock %), turnover rate (vs category benchmark), aging inventory %, and reorder risk. Each normalized 0–100. Weighted average: 35% availability + 25% turnover + 20% aging + 20% reorder risk.
