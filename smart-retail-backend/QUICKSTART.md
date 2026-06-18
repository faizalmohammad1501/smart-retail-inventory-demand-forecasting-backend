# Smart Retail Platform — Quick Start Guide

## 1. Install & Configure

```bash
# Install dependencies
pip install -r requirements.txt

# Set up environment
cp .env.example .env
# Edit .env — generate a strong JWT secret:
#   python -c "import secrets; print(secrets.token_hex(32))"
```

## 2. Start the Server

```bash
uvicorn main:app --reload --port 8000
```

| URL | Purpose |
|-----|---------|
| http://localhost:8000/docs | Swagger UI (interactive API docs) |
| http://localhost:8000/redoc | ReDoc documentation |
| http://localhost:8000/health | Liveness probe |
| http://localhost:8000/health/detailed | Readiness probe (DB + ML + disk) |

---

## 3. Load Demo Data

```bash
# Seed 3 users · 8 suppliers · 30 products · 200 orders
python demo_seed.py --reset --verify
```

**Demo accounts:**

| Username | Password | Role |
|----------|----------|------|
| `admin` | `Admin@123` | Full access |
| `manager` | `Manager@123` | Reports + ops |
| `analyst` | `Analyst@123` | Read-only |

---

## 4. Run Demo Workflows

```bash
# All 6 business scenarios end-to-end
python demo_workflow.py

# Single scenario (1=Inventory, 2=Orders, 3=Forecasting,
#                  4=Suppliers, 5=Dashboard, 6=Reports)
python demo_workflow.py --scenario 3

# Live presentation mode (pauses between scenarios)
python demo_workflow.py --pause
```

---

## 5. Train the ML Model

```bash
# 1. Generate synthetic dataset
curl -X POST http://localhost:8000/api/ml/pipeline/generate \
  -H "Authorization: Bearer <token>"

# 2. Run preprocessing pipeline
curl -X POST http://localhost:8000/api/ml/pipeline/run \
  -H "Authorization: Bearer <token>"

# 3. Train GradientBoosting model
curl -X POST http://localhost:8000/api/predictions/train \
  -H "Authorization: Bearer <token>"

# 4. Get 30-day forecast
curl -X POST http://localhost:8000/api/predictions/forecast \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"product_id": 1, "days": 30}'
```

---

## 6. Run Tests

```bash
# Final QA suite — 100+ checks, all modules
python test_qa.py

# Verbose output (show failures in detail)
python test_qa.py --verbose

# End-to-end workflow tests
python test_integration.py

# Module-level tests
python test_modules.py

# Auth & CRUD baseline
python test_api.py
```

---

## 7. Backup & Health Check

```bash
# CLI health report (DB + ML + disk checks)
python deploy/healthcheck.py

# Backup DB + ML models + datasets
python deploy/backup.py --retention 7
```

---

## 8. Docker (Full Stack)

```bash
# Start API + Nginx + Prometheus + Grafana
docker compose up -d

# Tail API logs
docker compose logs -f api

# Stop everything
docker compose down
```

| Service | URL |
|---------|-----|
| API | http://localhost:8000/docs |
| Prometheus | http://localhost:9090 |
| Grafana | http://localhost:3001 |

---

## Key Modules

| Module | Base Path |
|--------|-----------|
| Auth & RBAC | `/api/auth` |
| Products & Inventory | `/api/inventory` |
| Orders & Sales | `/api/sales` |
| Suppliers | `/api/suppliers` |
| Analytics (SLA, Bottlenecks) | `/api/analytics` |
| ML Pipeline | `/api/ml/pipeline` |
| Demand Forecasting | `/api/predictions` |
| Inventory Recommendations | `/api/recommendations` |
| Notifications & Alerts | `/api/notifications` |
| Business Reports (14) | `/api/reports` |
| Executive Dashboard | `/api/dashboard` |

See **README.md** for the full API reference and **DEPLOYMENT.md** for production setup.
