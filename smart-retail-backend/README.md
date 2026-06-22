<div align="center">

# 🛒 Smart Retail Inventory & Demand Forecasting Platform

**Production-grade supply chain intelligence backend built with FastAPI, scikit-learn, and SQLAlchemy.**

[![Python](https://img.shields.io/badge/Python-3.11+-3776AB?style=flat&logo=python&logoColor=white)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.104.1-009688?style=flat&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![SQLAlchemy](https://img.shields.io/badge/SQLAlchemy-2.0-D71F00?style=flat)](https://sqlalchemy.org)
[![scikit-learn](https://img.shields.io/badge/scikit--learn-1.3.2-F7931E?style=flat&logo=scikit-learn&logoColor=white)](https://scikit-learn.org)
[![Docker](https://img.shields.io/badge/Docker-Ready-2496ED?style=flat&logo=docker&logoColor=white)](./Dockerfile)
[![License](https://img.shields.io/badge/License-MIT-green?style=flat)](./LICENSE)

[**Live Demo Setup**](#-demo-setup) · [**API Reference**](docs/API_REFERENCE.md) · [**Architecture**](ARCHITECTURE.md) · [**Deployment**](DEPLOYMENT.md)

</div>

---

## 🎯 Project Overview

The **Smart Retail Platform** is a full-stack backend system that transforms raw supply chain data into actionable business intelligence. It combines real-time inventory management, ML-powered demand forecasting, automated SLA monitoring, and executive-level analytics into a single cohesive API platform.

### What Makes This Platform Stand Out

| Capability | Detail |
|-----------|--------|
| **ML Demand Forecasting** | GradientBoostingRegressor with autoregressive 30-day prediction, lag + rolling features |
| **SLA Monitoring** | Per-stage breach detection (procurement 48h / processing 24h / dispatch 12h / delivery 72h) |
| **Bottleneck Detection** | Automatic identification of the slowest supply chain stage per order and in aggregate |
| **Inventory Intelligence** | EOQ-based replenishment, stockout risk scoring, composite health score (0–100) |
| **Executive BI** | MoM/QoQ/YoY comparisons, profitability analysis, cohort analysis, strategic auto-insights |
| **Production-Ready** | Docker + Nginx + Prometheus + Grafana, GitHub Actions CI/CD, Alembic migrations |

---

## 📐 System Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                     React Frontend / API Clients                  │
└───────────────────────────────┬──────────────────────────────────┘
                                │ HTTPS
┌───────────────────────────────▼──────────────────────────────────┐
│              Nginx  (TLS · Rate Limiting · Security Headers)      │
└───────────────────────────────┬──────────────────────────────────┘
                                │
┌───────────────────────────────▼──────────────────────────────────┐
│                        FastAPI Application                        │
│                                                                    │
│   ┌─────────────────────────────────────────────────────────┐    │
│   │ Middleware Stack                                          │    │
│   │  SecurityHeaders → StructuredLogging → ErrorHandler      │    │
│   │  → CORS → JWT Auth → Router → Service → Response         │    │
│   └─────────────────────────────────────────────────────────┘    │
│                                                                    │
│   API Modules (12 routers, 70+ endpoints)                        │
│   ┌────────┐ ┌──────────┐ ┌─────────┐ ┌──────────┐ ┌────────┐  │
│   │  Auth  │ │Inventory │ │ Orders  │ │Suppliers │ │Analytics│  │
│   └────────┘ └──────────┘ └─────────┘ └──────────┘ └────────┘  │
│   ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌───────────────────┐  │
│   │ML Pipeline│ │Forecasting│ │Recommend.│ │   Notifications   │  │
│   └──────────┘ └──────────┘ └──────────┘ └───────────────────┘  │
│   ┌──────────────────┐ ┌──────────────────┐ ┌─────────────────┐ │
│   │Reports (14 rpts) │ │Dashboard (widgets)│ │  BI / Executive  │ │
│   └──────────────────┘ └──────────────────┘ └─────────────────┘ │
└───────────────┬───────────────────────┬──────────────────────────┘
                │                       │
   ┌────────────▼──────────┐  ┌─────────▼─────────┐
   │  SQLite / PostgreSQL   │  │  ML Model Store    │
   │   supply_chain.db      │  │  saved_models/     │
   └───────────────────────┘  └───────────────────┘
                │
   ┌────────────▼─────────────────────────────────┐
   │     Prometheus + Grafana (Observability)      │
   └──────────────────────────────────────────────┘
```

See [ARCHITECTURE.md](ARCHITECTURE.md) for the full architecture deep-dive.

---

## ✨ Feature Highlights

### 🔐 Authentication & Security
- JWT access tokens (30 min) + refresh tokens (7 days) with rotation
- Role-Based Access Control: `admin` / `manager` / `user`
- OWASP security headers on every response (CSP, HSTS, X-Frame-Options)
- Structured JSON logging with `X-Request-ID` tracing per request

### 📦 Inventory Management
- Product catalogue with SKU, category, supplier linkage, reorder levels
- Real-time stock tracking across warehouse locations
- EOQ-based replenishment recommendations
- Composite inventory health score (0–100) from availability + turnover + aging

### 📋 Order Lifecycle & Analytics
- Full order pipeline: `placed → procurement → processing → dispatch → delivered`
- Automatic SLA breach detection at each stage
- Bottleneck identification (which stage causes the most delays)
- Analytics: fill rate, fulfillment stats, cycle time distributions

### 🤖 ML Demand Forecasting Pipeline
```
Synthetic Data (7,300 records)
  └─ Preprocessing: Validation → Cleaning → Feature Engineering → Scaling
       └─ Features: Lag [1,3,7,14,21,28d] + Rolling [7,14,30d] + Calendar
           └─ GradientBoostingRegressor (200 est, lr=0.05, depth=4)
               └─ Autoregressive 30-day forecast per product
```

### 📊 Business Intelligence (12 BI Endpoints)
- **Executive Summary** — Revenue, margin %, SLA rate, fill rate in one call
- **KPI Trends** — Daily / weekly / monthly time-series for 5 KPIs
- **Period Comparison** — MoM / QoQ / YoY with % change for all metrics
- **Profitability Analysis** — Gross margin by category, top/bottom products
- **Inventory Health Score** — Composite scoring with breakdown
- **Supplier Intelligence** — Lead time, reliability score, cost performance index
- **Cohort Analysis** — Order cohorts by week/month
- **Strategic Insights** — Auto-generated text recommendations from KPI thresholds

### 📈 Reports & Dashboard
- **14 Business Reports**: Sales, Inventory, Suppliers, Forecast, Operations
- **Executive Dashboard**: 5 KPI widgets + 6 Chart.js-ready charts
- **Data Exports**: CSV (5 types) + PDF (4 report types, requires fpdf2)

---

## 🛠️ Technology Stack

| Category | Technology | Version | Purpose |
|----------|-----------|---------|---------|
| **Web Framework** | FastAPI | 0.104.1 | Async REST API, auto OpenAPI docs |
| **ASGI Server** | Uvicorn | 0.24.0 | High-performance async server |
| **Validation** | Pydantic v2 | 2.5.0 | Request/response schema validation |
| **ORM** | SQLAlchemy | 2.0.23 | Database models & query builder |
| **Database** | SQLite / PostgreSQL | — | Dev / Production persistence |
| **Migrations** | Alembic | 1.13.0 | Schema version control |
| **Auth** | python-jose + passlib | 3.3.0 / 1.7.4 | JWT tokens + bcrypt hashing |
| **ML Model** | scikit-learn | 1.3.2 | GradientBoostingRegressor |
| **Data Processing** | pandas + numpy | 2.1.3 / 1.26.2 | Feature engineering, preprocessing |
| **Model Persistence** | joblib | 1.3.2 | Serialize/load ML model |
| **PDF Generation** | fpdf2 | 2.7.9 | Optional PDF report export |
| **Config** | pydantic-settings | 2.1.0 | `.env` file + type-safe config |
| **Reverse Proxy** | Nginx | 1.25 | TLS termination, rate limiting |
| **Monitoring** | Prometheus + Grafana | 2.49 / 10.2 | Metrics scraping + dashboards |
| **Containers** | Docker + Compose v2 | — | Reproducible deployments |
| **CI/CD** | GitHub Actions | — | Automated test + deploy pipeline |

---

## 🚀 Quick Start

```bash
# 1. Clone
git clone https://github.com/faizalmohammad1501/smart-retail-inventory-demand-forecasting-backend.git
cd smart-retail-inventory-demand-forecasting-backend/smart-retail-backend

# 2. Create virtual environment
python -m venv .venv
.venv\Scripts\Activate.ps1        # Windows
# source .venv/bin/activate       # Linux/macOS

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure
cp .env.example .env
# Set JWT_SECRET_KEY to a strong secret:
#   python -c "import secrets; print(secrets.token_hex(32))"

# 5. Run
uvicorn main:app --reload --port 8000
```

Open **http://localhost:8000/docs** for the interactive Swagger UI.

---

## 🎬 Demo Setup

Load realistic demo data (30 products · 8 suppliers · 200 orders · intentional low-stock & SLA scenarios):

```bash
python demo_seed.py --reset --verify
```

| Account | Password | Role |
|---------|----------|------|
| `admin` | `Admin@123` | Full access |
| `manager` | `Manager@123` | Reports + operations |
| `analyst` | `Analyst@123` | Read-only analytics |

Run the 6-scenario end-to-end demo workflow:
```bash
python demo_workflow.py           # all scenarios
python demo_workflow.py --scenario 3   # ML forecasting only
python demo_workflow.py --pause        # live presentation mode
```

See [docs/DEMO_GUIDE.md](docs/DEMO_GUIDE.md) for a full guided walkthrough.

---

## 📡 API Endpoints (70+)

| Module | Base Path | Endpoints |
|--------|-----------|-----------|
| Authentication | `/api/auth` | 6 |
| Inventory & Products | `/api/inventory` | 7 |
| Orders & Sales | `/api/sales` | 5 |
| Suppliers | `/api/suppliers` | 5 |
| Analytics | `/api/analytics` | 3 |
| ML Pipeline | `/api/ml/pipeline` | 6 |
| Demand Forecasting | `/api/predictions` | 5 |
| Recommendations | `/api/recommendations` | 5 |
| Notifications | `/api/notifications` | 7 |
| Business Reports | `/api/reports` | 16 |
| Executive Dashboard | `/api/dashboard` | 16 |
| Business Intelligence | `/api/bi` | 10 |
| Health | `/health` | 2 |

Full reference: [docs/API_REFERENCE.md](docs/API_REFERENCE.md)

---

## 🗄️ Database Schema

```
users ──────────────────────────────────────────────────────────────
  id · username · email · hashed_password · full_name
  role (admin|manager|user) · is_active · refresh_token · last_login

suppliers ──────────────────────────────────────────────────────────
  id · supplier_name · contact_person · email · phone
  address · city · country · rating (1-5)
    │
    ├──▶ products
    │     id · product_name · sku (unique) · category · description
    │     unit_price · reorder_level · supplier_id → suppliers.id
    │         │
    │         ├──▶ inventory
    │         │     id · product_id → products.id · warehouse_location
    │         │     quantity_available · quantity_reserved · last_restocked
    │         │
    │         └──▶ orders
    │               id · order_number (unique) · product_id · supplier_id
    │               quantity · unit_price · total_amount · status
    │               ── Lifecycle ──────────────────────────────────
    │               order_placed_at · procurement_completed_at
    │               processing_completed_at · dispatched_at · delivered_at
    │               ── Calculated ─────────────────────────────────
    │               procurement_time (h) · processing_time (h)
    │               dispatch_time_duration (h) · delivery_time_duration (h)
    │               total_time (h)
    │               ── Intelligence ────────────────────────────────
    │               sla_breach (bool) · breached_stage · bottleneck_stage

notifications ──────────────────────────────────────────────────────
  id · category · priority · title · message
  product_id · product_name · supplier_id · order_id
  metric_value · metric_label · is_read · is_resolved
  dedup_key (unique) · resolved_at
```

Full schema: [docs/DATABASE_SCHEMA.md](docs/DATABASE_SCHEMA.md)

---

## 🧪 Testing

```bash
python test_qa.py            # Full QA suite — 100+ checks, all modules
python test_integration.py   # 7 end-to-end workflow tests
python test_modules.py       # ~60 module-level tests
python test_api.py           # Auth & CRUD baseline
```

---

## 🚢 Deployment

```bash
# Docker full stack (API + Nginx + Prometheus + Grafana)
docker compose up -d
python deploy/healthcheck.py
```

Full guide: [DEPLOYMENT.md](DEPLOYMENT.md)

CI/CD: GitHub Actions pipeline at `.github/workflows/ci-cd.yml`
- **Push to main** → Lint + Test + Docker build
- **Tag `v*.*.*`** → Build + Push to GHCR + SSH deploy

---

## 📁 Repository Structure

```
smart-retail-backend/
├── 📄 main.py                    # FastAPI application entry point
├── 📄 requirements.txt           # Python dependencies
├── 📄 demo_seed.py               # Demo data seeder
├── 📄 demo_workflow.py           # 6-scenario demo runner
├── 📄 test_qa.py                 # Final QA suite
├── 📄 test_integration.py        # End-to-end workflow tests
├── 📄 Dockerfile                 # Multi-stage production image
├── 📄 docker-compose.yml         # Full-stack orchestration
│
├── 📂 app/
│   ├── 📂 core/       config · security · dependencies · health
│   ├── 📂 database/   connection · db_init
│   ├── 📂 middleware/  error_handler · security_headers
│   ├── 📂 models/     user · product · inventory · sales · supplier · notification
│   ├── 📂 routes/     12 API routers
│   ├── 📂 schemas/    schemas.py (all Pydantic v2 models)
│   ├── 📂 services/   10 business logic services
│   └── 📂 utils/      response · bottleneck · sla · time · lifecycle
│
├── 📂 ml/
│   ├── 📂 datasets/    synthetic data generator
│   ├── 📂 preprocessing/ validate · clean · features · scale · build
│   ├── 📂 training/   GradientBoosting trainer
│   ├── 📂 prediction/ predictor · autoregressive forecast engine
│   └── 📂 saved_models/ persisted model + metadata
│
├── 📂 deploy/         start · healthcheck · backup · nginx · monitoring
├── 📂 alembic/        DB migration scripts
├── 📂 docs/           API reference · DB schema · ML pipeline · demo guide
└── 📂 .github/        CI/CD workflow
```

---

## 🏆 Key Engineering Decisions

| Decision | Rationale |
|----------|-----------|
| **FastAPI over Django REST** | Async-native, auto OpenAPI docs, Pydantic v2 validation, minimal boilerplate |
| **SQLAlchemy 2.0 ORM** | Type-safe queries, easy SQLite→PostgreSQL migration, declarative models |
| **GradientBoosting over LSTM** | Superior performance on tabular retail data, faster training, interpretable features |
| **SQLite for dev, PostgreSQL for prod** | Zero-config development, enterprise-grade production with single env var change |
| **Structured JSON logging** | Machine-parseable logs, direct Loki/ELK ingestion, `X-Request-ID` end-to-end tracing |
| **Dedup key on notifications** | Prevents alert storms on repeated threshold breaches — idempotent alert engine |
| **Autoregressive forecasting** | Each predicted day feeds the next — captures momentum without sequence model complexity |
| **Composite inventory health score** | Single actionable number aggregating 4 dimensions — easy to threshold for dashboards |

---

## 📊 Business Impact

| Metric | Value |
|--------|-------|
| API endpoints | 70+ |
| Business reports | 14 |
| BI analytics endpoints | 10 |
| ML features engineered | 30+ |
| Test coverage (QA checks) | 100+ |
| Alert types automated | 5 |
| Supported export formats | CSV + PDF |
| Deployment targets | Docker · systemd · Cloud VM |

---

## 📚 Documentation Index

| Document | Description |
|----------|-------------|
| [README.md](README.md) | This file — project overview and quick start |
| [ARCHITECTURE.md](ARCHITECTURE.md) | Full system architecture, data flow, component design |
| [DEPLOYMENT.md](DEPLOYMENT.md) | Docker, production server, CI/CD, backup, monitoring |
| [QUICKSTART.md](QUICKSTART.md) | Step-by-step setup commands |
| [CHANGELOG.md](CHANGELOG.md) | Version history and feature timeline |
| [docs/API_REFERENCE.md](docs/API_REFERENCE.md) | Complete API endpoint reference (70+ endpoints) |
| [docs/DATABASE_SCHEMA.md](docs/DATABASE_SCHEMA.md) | Full DB schema with field descriptions and relationships |
| [docs/ML_PIPELINE.md](docs/ML_PIPELINE.md) | ML forecasting pipeline: data → features → model → forecast |
| [docs/DEMO_GUIDE.md](docs/DEMO_GUIDE.md) | Guided demo walkthrough for evaluators and interviewers |

---

## 👤 Author

**Faizal Mohammad**
- GitHub: [@faizalmohammad1501](https://github.com/faizalmohammad1501)

---

## 📄 License

This project is licensed under the MIT License — see [LICENSE](LICENSE) for details.
