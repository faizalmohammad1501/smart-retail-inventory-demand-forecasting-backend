# Smart Retail Inventory & Demand Forecasting Platform

> **Production-grade FastAPI backend** for retail supply chain intelligence — featuring ML demand forecasting, real-time inventory management, SLA monitoring, bottleneck detection, automated alerting, executive dashboards, and comprehensive business reporting.

---

## Table of Contents
1. [System Architecture](#system-architecture)
2. [Feature Overview](#feature-overview)
3. [Technology Stack](#technology-stack)
4. [Database Schema](#database-schema)
5. [API Reference](#api-reference)
6. [Quick Start](#quick-start)
7. [Demo Setup](#demo-setup)
8. [ML Forecasting Pipeline](#ml-forecasting-pipeline)
9. [Running Tests](#running-tests)
10. [Deployment](#deployment)
11. [Project Structure](#project-structure)

---

## System Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                    Client (React / Postman)                      │
└────────────────────────────┬────────────────────────────────────┘
                             │ HTTPS
┌────────────────────────────▼────────────────────────────────────┐
│                      Nginx (Reverse Proxy)                       │
│       TLS termination · Rate limiting · Security headers         │
└────────────────────────────┬────────────────────────────────────┘
                             │
┌────────────────────────────▼────────────────────────────────────┐
│                    FastAPI Application                           │
│                                                                   │
│  Middleware Stack                                                 │
│  ┌──────────────┐ ┌──────────────┐ ┌──────────────┐             │
│  │Security Hdrs │ │Struct. Logging│ │ Error Handler│             │
│  └──────────────┘ └──────────────┘ └──────────────┘             │
│                                                                   │
│  API Routers (11 modules)                                        │
│  ┌──────┐ ┌─────────┐ ┌──────────┐ ┌───────────┐ ┌──────────┐  │
│  │ Auth │ │Inventory│ │  Orders  │ │ Suppliers │ │Analytics │  │
│  └──────┘ └─────────┘ └──────────┘ └───────────┘ └──────────┘  │
│  ┌──────────┐ ┌────────────┐ ┌──────────┐ ┌───────────────────┐ │
│  │ML Pipeline│ │Forecasting │ │Recommend.│ │ Notifications     │ │
│  └──────────┘ └────────────┘ └──────────┘ └───────────────────┘ │
│  ┌───────────────────────┐  ┌──────────────────────────────────┐ │
│  │  Reports (14 reports) │  │  Dashboard (widgets + charts)    │ │
│  └───────────────────────┘  └──────────────────────────────────┘ │
│                                                                   │
│  Service Layer · SQLAlchemy ORM · Pydantic v2                    │
└────────────────────────────┬────────────────────────────────────┘
                             │
         ┌───────────────────┴───────────────────┐
         │                                       │
┌────────▼──────────┐                 ┌──────────▼──────────┐
│   SQLite / PgSQL  │                 │   ML Model Store     │
│   supply_chain.db │                 │   ml/saved_models/   │
└───────────────────┘                 └─────────────────────┘
```

### Request Lifecycle

```
Request → SecurityHeadersMiddleware
        → logging_middleware (Request ID, structured JSON log)
        → error_handling_middleware (global exception → standard envelope)
        → CORSMiddleware
        → JWT Auth (get_current_user dependency)
        → Router handler
        → Service layer (business logic + DB queries)
        → Pydantic serialization
        → Response (with X-Request-ID, X-Process-Time-Ms headers)
```

---

## Feature Overview

| Module | Capabilities |
|--------|-------------|
| **JWT Auth & RBAC** | Register, login, refresh token, logout, role-based access (admin / manager / user) |
| **Inventory Management** | Products, stock levels, warehouse location, reorder tracking |
| **Order Lifecycle** | Full order pipeline: placed → procurement → processing → dispatch → delivered |
| **Supplier Management** | Supplier profiles, performance tracking, scorecards |
| **SLA Monitoring** | Per-stage SLA thresholds (48h / 24h / 12h / 72h), breach detection |
| **Bottleneck Detection** | Identifies the slowest stage per order and across the supply chain |
| **ML Forecasting** | GradientBoostingRegressor with 30-day autoregressive demand forecast |
| **Inventory Recommendations** | EOQ-based reorder recommendations, stockout risk scoring |
| **Notification Engine** | 5 alert types, deduplication, read/resolve/delete CRUD |
| **Business Reports** | 14 reports: sales, inventory, supplier, forecast, operations |
| **Executive Dashboard** | 5 KPI widgets + 6 Chart.js-ready charts + master summary |
| **Data Exports** | CSV (5 exports) + PDF (4 reports, requires fpdf2) |
| **Health Checks** | `/health` (liveness) + `/health/detailed` (readiness: DB, ML, disk) |
| **Security** | OWASP headers, request IDs, structured logging, HTTPS via Nginx |

---

## Technology Stack

| Layer | Technology | Version |
|-------|-----------|---------|
| Web Framework | FastAPI | 0.104.1 |
| ASGI Server | Uvicorn | 0.24.0 |
| Data Validation | Pydantic v2 | 2.5.0 |
| ORM | SQLAlchemy | 2.0.23 |
| Database | SQLite (dev) / PostgreSQL (prod) | — |
| DB Migrations | Alembic | 1.13.0 |
| Auth | python-jose + passlib[bcrypt] | 3.3.0 / 1.7.4 |
| ML | scikit-learn + pandas + numpy | 1.3.2 / 2.1.3 / 1.26.2 |
| Model Persistence | joblib | 1.3.2 |
| PDF Generation | fpdf2 (optional) | 2.7.9 |
| Config | pydantic-settings | 2.1.0 |
| Reverse Proxy | Nginx | 1.25 |
| Monitoring | Prometheus + Grafana | 2.49 / 10.2 |
| Containers | Docker + Compose v2 | — |
| CI/CD | GitHub Actions | — |

---

## Database Schema

```
users
  id · username · email · hashed_password · full_name
  role (admin|manager|user) · is_active · refresh_token · last_login

suppliers
  id · supplier_name · contact_person · email · phone
  address · city · country · rating (1-5)

products
  id · product_name · sku (unique) · category · description
  unit_price · supplier_id → suppliers.id · reorder_level

inventory
  id · product_id → products.id · warehouse_location
  quantity_available · quantity_reserved · last_restocked

orders
  id · order_number (unique) · product_id → products.id
  supplier_id → suppliers.id · quantity · unit_price · total_amount
  status (pending|processing|dispatched|delivered)
  ── Lifecycle Timestamps ──────────────────────────────────────
  order_placed_at · procurement_completed_at · processing_completed_at
  dispatched_at · delivered_at
  ── Calculated Analytics ──────────────────────────────────────
  procurement_time (h) · processing_time (h) · dispatch_time_duration (h)
  delivery_time_duration (h) · total_time (h)
  ── SLA & Bottleneck ──────────────────────────────────────────
  sla_breach (bool) · breached_stage · bottleneck_stage

notifications
  id · category · priority · title · message
  product_id · product_name · supplier_id · order_id
  metric_value · metric_label · is_read · is_resolved
  dedup_key (unique) · resolved_at
```

---

## API Reference

### Authentication — `/api/auth`

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| POST | `/register` | — | Create account |
| POST | `/login` | — | Login → access + refresh tokens |
| POST | `/refresh` | Refresh token | Rotate access token |
| POST | `/logout` | Bearer | Invalidate refresh token |
| GET | `/profile` | Bearer | Current user profile |
| PUT | `/profile/change-password` | Bearer | Change password |

### Inventory & Products — `/api/inventory`

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/products/` | Create product |
| GET | `/products/` | List all products |
| GET | `/products/{id}` | Product detail |
| PUT | `/products/{id}` | Update product |
| DELETE | `/products/{id}` | Delete product |
| GET | `/` | List inventory records |
| PUT | `/{id}` | Update stock levels |

### Orders & Sales — `/api/sales`

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/orders/` | Create order (auto-calculates SLA + bottleneck) |
| GET | `/orders/` | List orders (filterable by status, date) |
| GET | `/orders/{id}` | Order detail |
| PATCH | `/orders/{id}/status` | Advance order status |
| DELETE | `/orders/{id}` | Delete order |

### Suppliers — `/api/suppliers`

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/` | Create supplier |
| GET | `/` | List suppliers |
| GET | `/{id}` | Supplier detail |
| PUT | `/{id}` | Update supplier |
| DELETE | `/{id}` | Delete supplier |

### Analytics — `/api/analytics`

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/summary` | KPI summary (orders, SLA rate, avg times) |
| GET | `/bottlenecks` | Bottleneck analysis per stage |
| GET | `/sla-breaches` | All SLA-breached orders |

### ML Pipeline — `/api/ml/pipeline`

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/generate` | Generate synthetic training dataset |
| POST | `/run` | Run full preprocessing pipeline |
| GET | `/status` | Pipeline status + dataset sizes |
| GET | `/features` | Engineered feature names |
| GET | `/config` | ML configuration values |
| GET | `/download/{split}` | Download train/val/test CSV |

### Demand Forecasting — `/api/predictions`

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/train` | Train GradientBoosting model |
| GET | `/model/status` | Model status + metrics (MAE, R2) |
| POST | `/forecast` | Generate forecast `{product_id, days}` |
| GET | `/forecast/{product_id}` | Cached forecast for product |
| GET | `/forecast` | Forecasts for all products |

### Inventory Recommendations — `/api/recommendations`

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/` | All recommendations |
| GET | `/health` | Overall inventory health score |
| GET | `/alerts` | Low-stock / stockout alerts |
| GET | `/replenishment` | EOQ-based reorder list |
| GET | `/{product_id}` | Single product recommendation |

### Notifications — `/api/notifications`

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/run` | Execute notification engine |
| GET | `/summary` | Alert count by type and priority |
| GET | `/` | List notifications (filterable) |
| PATCH | `/read-all` | Mark all as read |
| PATCH | `/{id}/read` | Mark single as read |
| PATCH | `/{id}/resolve` | Resolve notification |
| DELETE | `/{id}` | Delete notification |

### Business Reports — `/api/reports`

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/sales/summary` | Revenue, orders, AOV |
| GET | `/sales/trends` | Daily/weekly revenue trend |
| GET | `/sales/top-products` | Top N products by revenue |
| GET | `/sales/by-category` | Revenue breakdown by category |
| GET | `/sales/fulfillment` | Fill rate & fulfillment stats |
| GET | `/inventory/valuation` | Total stock value by category |
| GET | `/inventory/turnover` | Inventory turnover ratio |
| GET | `/inventory/aging` | Aged stock analysis |
| GET | `/suppliers/performance` | Supplier rankings |
| GET | `/suppliers/{id}/scorecard` | Detailed supplier scorecard |
| GET | `/forecast/accuracy` | Model accuracy metrics |
| GET | `/operations/kpis` | Avg cycle times, SLA rate |
| GET | `/operations/sla-compliance` | SLA compliance breakdown |
| GET | `/operations/bottlenecks` | Bottleneck frequency report |
| GET | `/export/sales` | Sales CSV download |
| GET | `/export/inventory` | Inventory CSV download |
| GET | `/export/suppliers` | Suppliers CSV download |

### Dashboard — `/api/dashboard`

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/summary` | Master dashboard (all widgets + charts) |
| GET | `/widgets/sales` | Sales KPIs + 7-day sparkline |
| GET | `/widgets/inventory` | Stock health summary |
| GET | `/widgets/suppliers` | Supplier performance summary |
| GET | `/widgets/forecast` | Forecast accuracy widget |
| GET | `/widgets/alerts` | Active alert counts |
| GET | `/charts/revenue-trend` | Chart.js line data |
| GET | `/charts/order-status` | Chart.js doughnut data |
| GET | `/charts/top-products` | Chart.js bar data |
| GET | `/charts/inventory-health` | Chart.js bar data |
| GET | `/charts/supplier-performance` | Chart.js bar data |
| GET | `/charts/category-revenue` | Chart.js pie data |
| GET | `/export/forecast-accuracy` | Forecast CSV |
| GET | `/export/notifications` | Notifications CSV |
| GET | `/export/full-report` | Combined CSV export |
| GET | `/export/pdf/sales` | Sales PDF (fpdf2 required) |
| GET | `/export/pdf/inventory` | Inventory PDF |
| GET | `/export/pdf/suppliers` | Suppliers PDF |
| GET | `/export/pdf/executive` | Executive summary PDF |

### Health

| Endpoint | Description |
|----------|-------------|
| `GET /health` | Liveness probe (simple 200 OK) |
| `GET /health/detailed` | Readiness: DB ping, ML model, datasets, disk space |

---

## Quick Start

```bash
# 1. Clone repository
git clone git@github.com:faizalmohammad1501/smart-retail-inventory-demand-forecasting-backend.git
cd smart-retail-inventory-demand-forecasting-backend/smart-retail-backend

# 2. Create virtual environment
python -m venv .venv
.venv\Scripts\Activate.ps1       # Windows
# source .venv/bin/activate      # Linux/macOS

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure environment
cp .env.example .env
# Edit .env and set a strong JWT_SECRET_KEY:
#   python -c "import secrets; print(secrets.token_hex(32))"

# 5. Start the server
uvicorn main:app --reload --port 8000

# Open API docs: http://localhost:8000/docs
```

---

## Demo Setup

Load realistic demo data with 30 products, 8 suppliers, 200 orders, mixed stock levels, SLA breaches, and all demo user accounts:

```bash
# Seed fresh demo data (clears existing data first)
python demo_seed.py --reset --verify
```

**Demo accounts:**

| Username | Password | Role | Access |
|----------|----------|------|--------|
| `admin` | `Admin@123` | Admin | Full platform access |
| `manager` | `Manager@123` | Manager | Reports, orders, analytics |
| `analyst` | `Analyst@123` | User | Read-only analytics |

**Run the end-to-end demo workflow:**

```bash
# All 6 business scenarios
python demo_workflow.py

# Single scenario (e.g. forecasting only)
python demo_workflow.py --scenario 3

# Interactive live demo with pauses
python demo_workflow.py --pause
```

**Demo scenarios:**

| # | Scenario | Features Showcased |
|---|----------|--------------------|
| 1 | Inventory Command Centre | Stock alerts, low-stock detection, EOQ recommendations |
| 2 | Order Lifecycle & SLA | Order creation, SLA breach detection, bottleneck analysis |
| 3 | Demand Forecasting | ML pipeline, model training, 30-day forecast |
| 4 | Supplier Scorecard | Performance ranking, scorecard, bottleneck report |
| 5 | Executive Dashboard | All widgets, all Chart.js charts, master summary |
| 6 | Business Reports & Exports | All 13 reports, CSV exports, PDF generation |

---

## ML Forecasting Pipeline

```
Synthetic Data Generator
  └── 20 products × 365 days = 7,300 daily demand records
  └── Seasonal patterns · promotional spikes · supplier delays

Preprocessing Pipeline
  ├── Validation (schema, missing values, outliers)
  ├── Cleaning (imputation, normalization)
  ├── Feature Engineering
  │    ├── Lag features: [1, 3, 7, 14, 21, 28] days
  │    ├── Rolling stats: mean/std over [7, 14, 30] days
  │    ├── Day-of-week, month, quarter, is_weekend
  │    └── Supplier performance & price features
  ├── Scaling (StandardScaler)
  └── Train / Validation / Test split (70/15/15)

Model Training
  └── GradientBoostingRegressor
       n_estimators=200 · learning_rate=0.05 · max_depth=4

Forecasting
  └── Autoregressive prediction: day N feeds into day N+1
  └── Baseline fallback: 30-day rolling average (if model not trained)
  └── Outputs: predicted_demand, confidence_low, confidence_high per day
```

**Trigger via API:**
```bash
# 1. Generate data
curl -X POST http://localhost:8000/api/ml/pipeline/generate \
  -H "Authorization: Bearer <token>"

# 2. Run preprocessing
curl -X POST http://localhost:8000/api/ml/pipeline/run \
  -H "Authorization: Bearer <token>"

# 3. Train model
curl -X POST http://localhost:8000/api/predictions/train \
  -H "Authorization: Bearer <token>"

# 4. Get 30-day forecast
curl -X POST http://localhost:8000/api/predictions/forecast \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"product_id": 1, "days": 30}'
```

---

## Running Tests

| Script | Description | Command |
|--------|-------------|---------|
| `test_qa.py` | Final QA suite — 100+ checks across all modules | `python test_qa.py` |
| `test_integration.py` | 7 end-to-end workflow tests | `python test_integration.py` |
| `test_modules.py` | ~60 unit/integration tests per module | `python test_modules.py` |
| `test_api.py` | Auth, CRUD, RBAC baseline tests | `python test_api.py` |

```bash
# Full QA (recommended before demo or deployment)
python test_qa.py --url http://localhost:8000

# Verbose mode (show response details on failure)
python test_qa.py --verbose
```

Expected: **90%+ pass rate** with a seeded database and trained model.

---

## Deployment

See [DEPLOYMENT.md](DEPLOYMENT.md) for the complete guide covering:
- Docker single-container and full-stack (Nginx + Prometheus + Grafana)
- Production server setup (Ubuntu, systemd)
- Environment variable reference
- Alembic database migrations
- Backup and recovery procedures
- GitHub Actions CI/CD pipeline
- Security hardening checklist

**Quick Docker start:**
```bash
cp .env.production .env   # fill in JWT_SECRET_KEY and domain
docker compose up -d
python deploy/healthcheck.py --url http://localhost:8000
```

---

## Project Structure

```
smart-retail-backend/
├── main.py                         # FastAPI app entry point
├── requirements.txt                # Python dependencies
├── demo_seed.py                    # Demo data seeder (30 products, 200 orders)
├── demo_workflow.py                # End-to-end demo scenarios (6 scenarios)
├── test_qa.py                      # Final QA suite (100+ checks)
├── test_integration.py             # 7 end-to-end workflow tests
├── test_modules.py                 # Module-level unit/integration tests
├── test_api.py                     # Auth & CRUD baseline tests
├── alembic.ini                     # Alembic migration config
├── alembic/                        # DB migration scripts
├── Dockerfile                      # Multi-stage production image
├── docker-compose.yml              # Full stack: API + Nginx + Prometheus + Grafana
├── .env.example                    # Environment variable template
├── .github/workflows/ci-cd.yml     # GitHub Actions CI/CD pipeline
├── DEPLOYMENT.md                   # Full deployment guide
│
├── app/
│   ├── core/
│   │   ├── config.py               # pydantic-settings configuration
│   │   ├── security.py             # JWT + password hashing
│   │   ├── dependencies.py         # FastAPI auth dependencies
│   │   └── health.py               # Deep health check service
│   ├── database/
│   │   ├── connection.py           # SQLAlchemy engine + session
│   │   └── db_init.py              # Table auto-creation on startup
│   ├── middleware/
│   │   ├── error_handler.py        # Structured JSON logging + error envelopes
│   │   └── security_headers.py     # OWASP security headers
│   ├── models/                     # SQLAlchemy ORM models
│   │   ├── user.py · product.py · inventory.py
│   │   ├── sales.py · supplier.py · notification.py
│   ├── routes/                     # API routers (11 modules)
│   │   ├── auth.py · inventory.py · sales.py · suppliers.py
│   │   ├── analytics_routes.py · ml_pipeline.py · prediction.py
│   │   ├── inventory_recommendations.py · notifications.py
│   │   ├── reports.py · dashboard.py
│   ├── schemas/schemas.py          # All Pydantic v2 schemas
│   ├── services/                   # Business logic layer
│   │   ├── reporting_service.py    # SQL aggregations for 14 reports
│   │   ├── dashboard_service.py    # Widget + chart data builders
│   │   ├── export_pdf_service.py   # PDF generation (fpdf2)
│   │   ├── analytics_service.py    # KPI + SLA + bottleneck logic
│   │   ├── notification_service.py # Alert engine with deduplication
│   │   ├── forecast_prediction_service.py
│   │   └── inventory_recommendation_service.py
│   └── utils/
│       ├── response.py             # Standardised API response helpers
│       ├── bottleneck_detector.py · lifecycle_validator.py
│       ├── sla_validator.py · time_calculator.py · data_generator.py
│
├── ml/
│   ├── config.py                   # ML hyperparameters
│   ├── datasets/                   # Synthetic data generation
│   ├── preprocessing/              # Validation, cleaning, features, scaling
│   ├── training/model_trainer.py   # GradientBoostingRegressor
│   ├── prediction/                 # Predictor + autoregressive forecast engine
│   └── saved_models/               # Persisted model + metadata
│
└── deploy/
    ├── start.py · healthcheck.py · backup.py
    ├── nginx/nginx.conf · smart_retail.conf
    └── monitoring/prometheus.yml · alert_rules.yml · grafana/
```

---

## License

MIT License

## Contributors

Smart Retail Platform — faizalmohammad1501
