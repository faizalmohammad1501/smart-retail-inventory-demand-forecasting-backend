# Changelog

All notable changes to this project are documented in this file.

Format: [Version] — Date — Description  
Versioning: [Semantic Versioning](https://semver.org)

---

## [2.0.0] — 2026-06-22

### Added — Portfolio & Documentation Package
- `docs/API_REFERENCE.md` — Complete 70+ endpoint reference with request/response examples
- `docs/DATABASE_SCHEMA.md` — Full schema docs: 6 tables, field-by-field descriptions, query patterns
- `docs/ML_PIPELINE.md` — Deep-dive into 8-stage ML pipeline: data gen → features → GBR → autoregressive forecast
- `docs/DEMO_GUIDE.md` — Evaluator/interviewer walkthrough with 7 scenarios, talking points, Q&A
- `LICENSE` — MIT License
- Portfolio-grade `README.md` with badges, architecture diagram, feature table, engineering decisions
- `ARCHITECTURE.md` — 7 sections with full ASCII architecture diagrams (request lifecycle, ML pipeline, security, deployment)

---

## [1.9.0] — 2026-06-19

### Added — Advanced Business Intelligence Module
- `app/routes/bi_routes.py` — 10 BI endpoints under `/api/bi/`
- `app/services/bi_service.py` — 10 aggregation functions for executive analytics
- BI endpoints: `/executive-summary`, `/kpi-trends`, `/profitability`, `/period-comparison`, `/inventory-health-score`, `/supplier-intelligence`, `/forecast-performance`, `/cohort-analysis`, `/alerts-intelligence`, `/strategic-insights`
- 10 Pydantic v2 BI response schemas in `app/schemas/schemas.py`

---

## [1.8.0] — 2026-06-18

### Added — Demo & QA Package
- `demo_seed.py` — Seeds 3 users, 8 suppliers (4 tiers), 30 products, 200 orders, intentional SLA breaches
- `demo_workflow.py` — 6 business scenario automation runner with `--scenario` and `--pause` flags
- `test_qa.py` — 100+ QA checks across 16 test groups with color-coded pass/fail output

### Added — Deployment & Infrastructure
- `Dockerfile` — Multi-stage (builder→runtime), non-root `appuser`, Docker HEALTHCHECK
- `docker-compose.yml` — api + nginx + prometheus + grafana services, 6 named volumes
- `deploy/nginx/nginx.conf` + `smart_retail.conf` — JSON access logs, gzip, rate zones, TLS, OWASP headers
- `deploy/monitoring/prometheus.yml` + `alert_rules.yml` — 5 alert rules (API down, 5xx rate, P95 latency, disk, ML staleness)
- `deploy/monitoring/grafana/provisioning/` — Auto-provisioned datasource + dashboard
- `deploy/start.py` — Production startup: env validation → Alembic migrations → uvicorn
- `deploy/healthcheck.py` — CLI health reporter
- `deploy/backup.py` — Timestamped DB + models + datasets backup with retention pruning
- `alembic.ini` + `alembic/env.py` + `alembic/versions/0001_initial.py` — Database migration scaffold
- `.github/workflows/ci-cd.yml` — 3-job CI/CD: lint/test → Docker build/push to GHCR → SSH deploy on `v*.*.*` tags
- `DEPLOYMENT.md` — Comprehensive deployment guide

---

## [1.7.0] — 2026-06-18

### Added — Export & Dashboard Module
- `app/routes/dashboard.py` — 20 endpoints under `/api/dashboard/`
- `app/services/dashboard_service.py` — Master dashboard, 5 widgets, 6 Chart.js-compatible charts
- `app/services/export_pdf_service.py` — PDF report generation using fpdf2 (graceful 501 fallback)
- Dashboard endpoints: master summary, KPI widgets (sales, inventory, suppliers, forecast, alerts), charts (revenue trend, order status, top products, inventory health, supplier performance, category revenue)
- CSV and PDF export endpoints

---

## [1.6.0] — 2026-06-17

### Added — Business Reports Module
- `app/routes/reports.py` — 17 endpoints under `/api/reports/`
- `app/services/reporting_service.py` — 14 SQL aggregation functions
- Reports: sales summary, trends, top products, by-category, fulfillment, inventory valuation, turnover, aging, supplier performance, supplier scorecard, forecast accuracy, operations KPIs, SLA compliance, bottleneck report
- CSV exports: sales, inventory, suppliers

---

## [1.5.0] — 2026-06-17

### Added — Final Integration & Production Readiness
- `app/core/config.py` — pydantic-settings `Settings` class (env-driven configuration)
- `app/core/health.py` — Detailed health checks (DB, ML model, datasets, disk space)
- `app/middleware/security_headers.py` — OWASP security headers middleware
- `app/utils/response.py` — Standardized response envelope helpers
- Structured JSON logging with `X-Request-ID` and `X-Process-Time-Ms` response headers
- `/health/detailed` endpoint — readiness probe

---

## [1.4.0] — 2026-06-16

### Added — Notifications & Alert Engine
- `app/models/notification.py` — Notification SQLAlchemy model with `dedup_key` unique constraint
- `app/routes/notifications.py` — 8 CRUD + management endpoints under `/api/notifications/`
- Alert engine: 5 alert types (LOW_STOCK, OUT_OF_STOCK, SLA_BREACH, SUPPLIER_PERFORMANCE, FORECAST_DEVIATION)
- Alert deduplication via SHA-256 `dedup_key` (DB-enforced, prevents alert storms)
- Resolve/read endpoints with resolution notes

---

## [1.3.0] — 2026-06-15

### Added — Demand Forecasting & Recommendations
- `ml/` directory structure (datasets, preprocessing, training, prediction, saved_models)
- `app/routes/predictions.py` — Training + forecasting endpoints under `/api/predictions/`
- `app/routes/ml_pipeline.py` — Pipeline management endpoints under `/api/ml/pipeline/`
- GradientBoostingRegressor (200 estimators, lr=0.05, depth=4)
- 30-feature engineering pipeline (6 lag features, 6 rolling features, calendar features)
- Autoregressive 30-day forecast with confidence intervals
- `app/routes/recommendations.py` — Inventory recommendations under `/api/recommendations/`
- EOQ-based reorder quantity calculation
- Composite inventory health score (0–100, grade A–F)

---

## [1.2.0] — 2026-06-14

### Added — Analytics Module
- `app/routes/analytics.py` — Analytics endpoints under `/api/analytics/`
- `app/utils/bottleneck_detector.py` — Stage-level bottleneck detection
- `app/utils/sla_validator.py` — SLA threshold validation per stage
- `app/utils/lifecycle_validator.py` — Order lifecycle timestamp validation
- `app/utils/time_calculator.py` — Duration calculation between lifecycle stages
- Analytics endpoints: summary KPIs, bottleneck analysis, SLA breach report

---

## [1.1.0] — 2026-06-13

### Added — Core CRUD Modules
- `app/routes/inventory.py` — Product + inventory management (10 endpoints)
- `app/routes/sales.py` — Order lifecycle management (5 endpoints)
- `app/routes/suppliers.py` — Supplier CRUD (5 endpoints)
- `app/models/` — All SQLAlchemy models: user, product, inventory, sales (Order), supplier
- `app/schemas/schemas.py` — All Pydantic v2 request/response schemas
- `app/services/product_service.py`, `supplier_service.py`, `order_service.py`
- SQLAlchemy 2.0 session management + `Base.metadata.create_all()` on startup

---

## [1.0.0] — 2026-06-12

### Added — Foundation
- FastAPI project scaffold (`main.py`, `app/__init__.py`)
- `app/database/connection.py` — SQLAlchemy engine + session factory
- `app/database/db_init.py` — `init_db()` on startup
- `app/routes/auth.py` — JWT authentication (register, login, refresh, logout, profile)
- `app/controllers/auth_controller.py` — JWT issuance and validation
- `app/middleware/error_handler.py` — Global exception handler with structured JSON errors
- `requirements.txt` — All pinned dependencies
- `README.md` — Initial project description
