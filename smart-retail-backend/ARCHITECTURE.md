# Smart Retail Platform — System Architecture

## 1. High-Level Architecture

```
┌──────────────────────────────────────────────────────────────────────┐
│                         CLIENT TIER                                   │
│                                                                        │
│   React SPA         Postman / cURL          External Systems          │
│   (Port 3000/5173)  (API Testing)           (Webhooks, BI Tools)      │
└────────────────────────────┬─────────────────────────────────────────┘
                             │ HTTPS (443)
┌────────────────────────────▼─────────────────────────────────────────┐
│                       INGRESS TIER                                    │
│                                                                        │
│   Nginx 1.25                                                           │
│   ├── TLS termination (TLS 1.2 / 1.3)                                 │
│   ├── HTTP → HTTPS redirect                                            │
│   ├── Rate limiting (60 req/min general · 10 req/min auth)            │
│   ├── OWASP security headers (HSTS, CSP, X-Frame-Options)            │
│   ├── Gzip compression for JSON/JS/CSS responses                      │
│   └── JSON-structured access logs                                      │
└────────────────────────────┬─────────────────────────────────────────┘
                             │ HTTP (8000)
┌────────────────────────────▼─────────────────────────────────────────┐
│                    APPLICATION TIER                                   │
│                                                                        │
│   FastAPI 0.104.1 + Uvicorn 0.24.0                                    │
│                                                                        │
│   ┌────────────────────────────────────────────────────────────────┐  │
│   │  Middleware Stack (outermost → innermost)                       │  │
│   │                                                                  │  │
│   │  1. SecurityHeadersMiddleware                                   │  │
│   │     → Adds OWASP headers to every response                     │  │
│   │  2. logging_middleware (BaseHTTPMiddleware)                     │  │
│   │     → Assigns X-Request-ID (UUID4)                             │  │
│   │     → Emits structured JSON log per request/response           │  │
│   │     → Adds X-Process-Time-Ms header                            │  │
│   │  3. error_handling_middleware (BaseHTTPMiddleware)              │  │
│   │     → Catches all unhandled exceptions                         │  │
│   │     → Returns standard error envelope:                         │  │
│   │       {"status":"error","detail":"...","code":"...","request_id":"..."} │
│   │  4. CORSMiddleware                                              │  │
│   │     → Wildcard in dev, explicit origins in production          │  │
│   └────────────────────────────────────────────────────────────────┘  │
│                                                                        │
│   ┌────────────────────────────────────────────────────────────────┐  │
│   │  Router Layer (12 modules, 70+ endpoints)                       │  │
│   │                                                                  │  │
│   │  /api/auth             JWT auth, RBAC                          │  │
│   │  /api/inventory        Products, stock management              │  │
│   │  /api/sales            Order lifecycle                         │  │
│   │  /api/suppliers        Supplier management                     │  │
│   │  /api/analytics        SLA, bottleneck, KPI summary            │  │
│   │  /api/ml/pipeline      Data generation, preprocessing          │  │
│   │  /api/predictions      Model training, demand forecast         │  │
│   │  /api/recommendations  EOQ, stockout alerts                    │  │
│   │  /api/notifications    Alert engine, CRUD                      │  │
│   │  /api/reports          14 business reports + CSV exports       │  │
│   │  /api/dashboard        Widgets, charts, PDF exports            │  │
│   │  /api/bi               Executive BI (10 advanced analytics)    │  │
│   └────────────────────────────────────────────────────────────────┘  │
│                                                                        │
│   ┌────────────────────────────────────────────────────────────────┐  │
│   │  Service Layer (Business Logic)                                  │  │
│   │                                                                  │  │
│   │  bi_service.py           → 10 BI aggregation functions         │  │
│   │  reporting_service.py    → 14 report SQL aggregations          │  │
│   │  dashboard_service.py    → Widget + chart builders             │  │
│   │  analytics_service.py    → SLA, bottleneck, KPI logic          │  │
│   │  notification_service.py → Alert engine + deduplication        │  │
│   │  inventory_recommendation_service.py → EOQ + risk scoring     │  │
│   │  forecast_prediction_service.py → Autoregressive forecasting   │  │
│   │  export_service.py       → CSV generation                      │  │
│   │  export_pdf_service.py   → PDF generation (fpdf2)              │  │
│   └────────────────────────────────────────────────────────────────┘  │
│                                                                        │
│   ┌────────────────────────────────────────────────────────────────┐  │
│   │  Auth & Dependencies                                             │  │
│   │                                                                  │  │
│   │  get_current_user()         → Decode JWT, load user from DB    │  │
│   │  get_current_active_user()  → Verify is_active flag            │  │
│   │  require_roles(*roles)      → RBAC enforcement decorator       │  │
│   └────────────────────────────────────────────────────────────────┘  │
└────────────────────────────┬─────────────────────────────────────────┘
                             │
┌────────────────────────────▼─────────────────────────────────────────┐
│                       DATA TIER                                        │
│                                                                        │
│   SQLAlchemy 2.0 ORM                                                   │
│   ├── SQLite (development) — zero config, file-based                  │
│   └── PostgreSQL (production) — single DATABASE_URL env var change    │
│                                                                        │
│   Tables: users · suppliers · products · inventory · orders           │
│            notifications                                               │
│                                                                        │
│   Alembic Migrations — schema version control                          │
└────────────────────────────┬─────────────────────────────────────────┘
                             │
┌────────────────────────────▼─────────────────────────────────────────┐
│                       ML TIER                                          │
│                                                                        │
│   ml/datasets/          → Synthetic retail demand generator           │
│   ml/preprocessing/     → Validate → Clean → Feature Eng → Scale      │
│   ml/training/          → GradientBoostingRegressor (sklearn)         │
│   ml/prediction/        → Single-step predictor + autoregressive loop │
│   ml/saved_models/      → model.pkl + training_metadata.json          │
└────────────────────────────┬─────────────────────────────────────────┘
                             │
┌────────────────────────────▼─────────────────────────────────────────┐
│                    OBSERVABILITY TIER                                  │
│                                                                        │
│   Prometheus 2.49  → Scrapes /metrics from API every 15s             │
│   Grafana 10.2     → Dashboards + Alert notifications                 │
│   Structured Logs  → JSON to stdout → Loki / ELK compatible          │
└──────────────────────────────────────────────────────────────────────┘
```

---

## 2. Request Lifecycle

```
HTTP Request
    │
    ▼
SecurityHeadersMiddleware
    └── Adds OWASP headers to response (before any handler)
    │
    ▼
logging_middleware
    ├── Generates request_id = uuid4()
    ├── Logs: {timestamp, request_id, method, path, client_ip}
    └── After handler: logs {status_code, duration_ms}
    │
    ▼
error_handling_middleware
    └── Wraps entire handler in try/except
        └── On exception → standard error JSON + logs stack trace
    │
    ▼
CORSMiddleware
    └── Sets Access-Control-* headers
    │
    ▼
FastAPI Router (path + method matching)
    │
    ▼
Pydantic Request Validation
    └── On failure → ValidationException → per-field error response
    │
    ▼
Auth Dependency (get_current_active_user)
    ├── Extract Bearer token from Authorization header
    ├── Decode JWT (python-jose)
    ├── Load user from DB by username
    └── Check is_active == True
    │
    ▼
RBAC Dependency (require_roles, if applied)
    └── Verify user.role in allowed roles
    │
    ▼
Route Handler
    └── Calls Service Layer function(s)
    │
    ▼
Service Layer (SQL queries via SQLAlchemy)
    └── Returns Python dict / list
    │
    ▼
Pydantic Response Serialization
    │
    ▼
HTTP Response
    ├── X-Request-ID: <uuid>
    ├── X-Process-Time-Ms: <float>
    └── Body: {"status": "success", "data": {...}, "timestamp": "..."}
```

---

## 3. ML Forecasting Architecture

```
Step 1 — Data Generation
    POST /api/ml/pipeline/generate
    └── dataset_generator.py
        ├── 20 synthetic products
        ├── 365 days of daily demand records (7,300 rows)
        ├── Seasonal patterns (weekly + monthly cycles)
        ├── Promotional demand spikes
        └── Supplier delay correlation features

Step 2 — Preprocessing Pipeline
    POST /api/ml/pipeline/run
    ├── data_validator.py      → Schema checks, missing value detection
    ├── data_cleaner.py        → IQR-based outlier capping, imputation
    ├── feature_engineer.py
    │   ├── Lag features:     demand_{lag}d for lag in [1,3,7,14,21,28]
    │   ├── Rolling features: mean/std over windows [7,14,30] days
    │   ├── Calendar:         day_of_week, month, quarter, is_weekend
    │   └── Business:         unit_price, supplier_rating, reorder_level
    ├── scaler_service.py      → StandardScaler (fit on train, apply to all)
    └── dataset_builder.py     → 70/15/15 train/val/test split + CSV export

Step 3 — Model Training
    POST /api/predictions/train
    └── model_trainer.py
        ├── GradientBoostingRegressor(
        │     n_estimators=200,
        │     learning_rate=0.05,
        │     max_depth=4,
        │     subsample=0.8
        │   )
        ├── Trains on train split
        ├── Evaluates on val split: MAE, RMSE, R²
        └── Persists: model.pkl + training_metadata.json

Step 4 — Forecast Generation
    POST /api/predictions/forecast {product_id, days}
    └── forecast_engine.py (autoregressive loop)
        ├── Load last 28 days of known demand (seed window)
        ├── For each future day t = 1..N:
        │   ├── Build feature vector from current window
        │   ├── model.predict(features) → demand_t
        │   ├── Append demand_t to window (sliding)
        │   └── confidence_low = demand_t * 0.85
        │       confidence_high = demand_t * 1.15
        └── Returns: [{date, predicted_demand, confidence_low, confidence_high}]
```

---

## 4. Notification Engine Architecture

```
POST /api/notifications/run
    └── NotificationService.run()
        ├── Alert Type 1: LOW_STOCK
        │   → Inventory.quantity_available < Product.reorder_level
        │
        ├── Alert Type 2: OUT_OF_STOCK
        │   → Inventory.quantity_available == 0
        │
        ├── Alert Type 3: SLA_BREACH
        │   → Order.sla_breach == True AND Order.delivered_at recent
        │
        ├── Alert Type 4: SUPPLIER_PERFORMANCE
        │   → Supplier SLA breach rate > 30% in last 30 days
        │
        └── Alert Type 5: FORECAST_DEVIATION
            → |actual_demand - predicted_demand| / predicted_demand > 20%

        Deduplication:
            dedup_key = hash(alert_type + product_id + date)
            → INSERT OR IGNORE by unique constraint on dedup_key
            → Prevents duplicate alerts from repeated runs
```

---

## 5. Component Dependency Map

```
main.py
 ├── app/core/config.py        (Settings via pydantic-settings)
 ├── app/database/db_init.py   (Table creation on startup)
 ├── app/middleware/
 │   ├── security_headers.py
 │   └── error_handler.py
 └── app/routes/  (12 routers)
      └── app/services/  (10 services)
           └── app/models/  (6 SQLAlchemy models)
                └── app/database/connection.py  (engine + session)
```

---

## 6. Security Architecture

| Layer | Mechanism |
|-------|-----------|
| Transport | TLS 1.2/1.3 via Nginx (HSTS enabled) |
| Auth | JWT HS256 (30 min access + 7 day refresh with rotation) |
| Passwords | bcrypt hash via passlib |
| Authorization | Role-based (admin/manager/user) per endpoint |
| Input validation | Pydantic v2 strict schemas at every boundary |
| Response headers | OWASP set: CSP, X-Frame-Options, X-Content-Type-Options, Referrer-Policy |
| Rate limiting | Nginx: 60 req/min (API), 10 req/min (auth endpoints) |
| Secrets | `.env` file, never committed; `JWT_SECRET_KEY` min 32 chars enforced |
| Container | Non-root `appuser` in Docker image |
| Logging | Structured JSON with `X-Request-ID` — no PII in logs |

---

## 7. Deployment Architecture

```
Production Server
├── Docker Compose Services:
│   ├── api       (FastAPI + Uvicorn, port 8000 internal)
│   ├── nginx     (ports 80/443 external → 8000 internal)
│   ├── prometheus (port 9090)
│   └── grafana   (port 3001)
│
├── Persistent Volumes:
│   ├── db_data      → /data/supply_chain.db
│   ├── model_data   → /app/ml/saved_models/
│   ├── upload_data  → /app/uploads/
│   ├── log_data     → /app/logs/  and  /var/log/nginx/
│   ├── prometheus_data
│   └── grafana_data
│
└── GitHub Actions CI/CD:
    ├── On PR to main → Lint + Tests
    ├── On push to main → Tests + Docker build/push to GHCR
    └── On tag v*.*.* → All above + SSH deploy + GitHub Release
```
