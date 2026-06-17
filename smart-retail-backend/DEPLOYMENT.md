# Smart Retail Platform — Deployment Guide

## Table of Contents
1. [Overview](#overview)
2. [Prerequisites](#prerequisites)
3. [Quick Start (Local Development)](#quick-start-local-development)
4. [Docker Deployment](#docker-deployment)
5. [Production Deployment](#production-deployment)
6. [Environment Variables Reference](#environment-variables-reference)
7. [Database Migrations](#database-migrations)
8. [Monitoring & Observability](#monitoring--observability)
9. [Backup & Recovery](#backup--recovery)
10. [CI/CD Pipeline](#cicd-pipeline)
11. [Security Checklist](#security-checklist)
12. [Troubleshooting](#troubleshooting)

---

## Overview

| Component       | Technology                      | Port  |
|-----------------|----------------------------------|-------|
| API Server      | FastAPI + Uvicorn                | 8000  |
| Reverse Proxy   | Nginx 1.25                       | 80 / 443 |
| Database        | SQLite (dev) / PostgreSQL (prod) | —     |
| Metrics         | Prometheus                       | 9090  |
| Dashboards      | Grafana                          | 3001  |
| Container Orch. | Docker Compose v2                | —     |

---

## Prerequisites

| Requirement    | Minimum Version |
|----------------|-----------------|
| Python         | 3.11+           |
| Docker Engine  | 24.0+           |
| Docker Compose | v2.20+          |
| Git            | 2.40+           |

---

## Quick Start (Local Development)

```bash
# 1. Clone repository
git clone git@github.com:faizalmohammad1501/smart-retail-inventory-demand-forecasting-backend.git
cd smart-retail-inventory-demand-forecasting-backend/smart-retail-backend

# 2. Create virtual environment
python -m venv .venv
source .venv/bin/activate        # Linux/macOS
.venv\Scripts\Activate.ps1       # Windows PowerShell

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure environment
cp .env.example .env
# Edit .env — set JWT_SECRET_KEY to a strong random value:
#   python -c "import secrets; print(secrets.token_hex(32))"

# 5. Start the server
uvicorn main:app --reload --port 8000

# 6. Open API documentation
#   http://localhost:8000/docs
#   http://localhost:8000/redoc
```

---

## Docker Deployment

### Build & Run (Development)

```bash
# Build image
docker build -t smart-retail-backend .

# Run with .env file
docker run --env-file .env -p 8000:8000 smart-retail-backend
```

### Full Stack (API + Nginx + Prometheus + Grafana)

```bash
# 1. Create production env file
cp .env.production .env
# Fill in JWT_SECRET_KEY, GRAFANA_PASSWORD, CORS_ORIGINS

# 2. Place TLS certificates
mkdir -p deploy/nginx/ssl
cp /path/to/fullchain.pem deploy/nginx/ssl/
cp /path/to/privkey.pem   deploy/nginx/ssl/

# 3. Start all services
docker compose up -d

# 4. Check status
docker compose ps
docker compose logs -f api

# 5. Verify health
python deploy/healthcheck.py --url http://localhost:8000
```

### Useful Docker Commands

```bash
docker compose ps                  # service status
docker compose logs -f api         # tail API logs
docker compose restart api         # restart API only
docker compose exec api bash       # shell into container
docker compose down                # stop all services
docker compose down -v             # stop + delete volumes (data loss!)
```

---

## Production Deployment

### Server Setup (Ubuntu 22.04)

```bash
# Install Docker
curl -fsSL https://get.docker.com | sh
usermod -aG docker $USER

# Clone repository
git clone ... /opt/smart-retail
cd /opt/smart-retail/smart-retail-backend

# Configure environment
cp .env.production .env
nano .env   # Fill in all required values

# Obtain TLS certificate (Let's Encrypt)
apt install certbot
certbot certonly --standalone -d yourdomain.com
cp /etc/letsencrypt/live/yourdomain.com/fullchain.pem deploy/nginx/ssl/
cp /etc/letsencrypt/live/yourdomain.com/privkey.pem   deploy/nginx/ssl/

# Start services
docker compose up -d
```

### Zero-Downtime Updates

```bash
# Pull new image and restart only the API container
docker compose pull api
docker compose up -d --no-deps api

# Verify health
python deploy/healthcheck.py
```

### Systemd Service (Alternative to Docker)

```ini
# /etc/systemd/system/smart-retail.service
[Unit]
Description=Smart Retail Backend API
After=network.target

[Service]
Type=simple
User=www-data
WorkingDirectory=/opt/smart-retail/smart-retail-backend
EnvironmentFile=/opt/smart-retail/smart-retail-backend/.env
ExecStart=/opt/smart-retail/.venv/bin/python deploy/start.py --workers 4
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

```bash
systemctl daemon-reload
systemctl enable smart-retail
systemctl start smart-retail
journalctl -u smart-retail -f
```

---

## Environment Variables Reference

| Variable                        | Required | Default              | Description                          |
|---------------------------------|----------|----------------------|--------------------------------------|
| `APP_NAME`                      | No       | Smart Retail...      | Application display name             |
| `APP_VERSION`                   | No       | 2.0.0                | Application version                  |
| `APP_ENV`                       | No       | development          | `development` / `staging` / `production` |
| `LOG_LEVEL`                     | No       | INFO                 | `DEBUG` / `INFO` / `WARNING` / `ERROR` |
| `JWT_SECRET_KEY`                | **Yes**  | —                    | Min 32-char random secret            |
| `JWT_ALGORITHM`                 | No       | HS256                | JWT signing algorithm                |
| `JWT_ACCESS_TOKEN_EXPIRE_MINUTES` | No     | 30                   | Access token TTL in minutes          |
| `JWT_REFRESH_TOKEN_EXPIRE_DAYS` | No       | 7                    | Refresh token TTL in days            |
| `DATABASE_URL`                  | **Yes**  | sqlite:///./supply_chain.db | SQLAlchemy database URL       |
| `CORS_ORIGINS`                  | No       | http://localhost:3000,... | Comma-separated allowed origins |
| `RATE_LIMIT_PER_MINUTE`         | No       | 120                  | Max requests per minute per IP       |
| `GRAFANA_PASSWORD`              | No       | admin                | Grafana admin password               |

---

## Database Migrations

Alembic is configured for schema version control.

```bash
# Apply all pending migrations
alembic upgrade head

# Create a new migration after model changes
alembic revision --autogenerate -m "add product_category column"

# Show migration history
alembic history --verbose

# Rollback one revision
alembic downgrade -1

# Show current revision
alembic current
```

> **Note:** The initial baseline migration (`0001_initial`) is a no-op — all tables  
> are created by `Base.metadata.create_all()` in `db_init.py` on first startup.  
> Use Alembic for all future schema changes.

---

## Monitoring & Observability

### Health Endpoints

| Endpoint          | Purpose              | Auth Required |
|-------------------|----------------------|---------------|
| `GET /health`     | Liveness probe       | No            |
| `GET /health/detailed` | Readiness probe + component checks | No |

```bash
# Quick health check
python deploy/healthcheck.py --url http://localhost:8000

# One-liner
curl http://localhost:8000/health | python -m json.tool
```

### Prometheus Metrics

Access: `http://localhost:9090`

Key metrics (requires `prometheus-fastapi-instrumentator` — add to requirements.txt):
- `http_requests_total` — request count by method/path/status
- `http_request_duration_seconds` — response time histogram
- `process_resident_memory_bytes` — memory usage

### Grafana Dashboards

Access: `http://localhost:3001`  
Default login: `admin / <GRAFANA_PASSWORD from .env>`

Pre-provisioned dashboards are loaded from `deploy/monitoring/grafana/dashboards/`.

### Structured Logs

All API logs are emitted as JSON to stdout/stderr:

```json
{
  "timestamp": "2026-06-17T10:00:00.000Z",
  "level": "INFO",
  "logger": "smart_retail",
  "request_id": "a1b2c3d4-...",
  "method": "POST",
  "path": "/api/auth/login",
  "status_code": 200,
  "duration_ms": 42.3
}
```

Aggregate with `docker compose logs api | jq .` or ship to ELK / Loki.

---

## Backup & Recovery

### Automated Backup

```bash
# Run immediately
python deploy/backup.py

# Run with custom destination (keep 14 days)
python deploy/backup.py --dest /mnt/backup --retention 14
```

Backup includes:
- `supply_chain_YYYYMMDD_HHMMSS.db` — SQLite database snapshot
- `saved_models_YYYYMMDD_HHMMSS.tar.gz` — trained ML models
- `datasets_YYYYMMDD_HHMMSS.tar.gz` — ML training datasets
- `uploads_YYYYMMDD_HHMMSS.tar.gz` — uploaded files

### Cron Schedule (Linux)

```cron
# Daily backup at 02:00 UTC, retain 7 days
0 2 * * * /usr/bin/python3 /opt/smart-retail/smart-retail-backend/deploy/backup.py \
  --dest /mnt/backup/smart-retail \
  --retention 7 \
  >> /var/log/smart_retail_backup.log 2>&1
```

### Restore Procedure

```bash
# 1. Stop API
docker compose stop api

# 2. Restore database
cp backups/supply_chain_20260617_020000.db /data/supply_chain.db

# 3. Restore ML models
tar -xzf backups/saved_models_20260617_020000.tar.gz -C ml/

# 4. Restart API
docker compose start api

# 5. Verify
python deploy/healthcheck.py
```

---

## CI/CD Pipeline

The GitHub Actions workflow at `.github/workflows/ci-cd.yml` runs:

| Trigger               | Jobs                                  |
|-----------------------|---------------------------------------|
| PR to `main`          | Lint + Test                           |
| Push to `main`        | Lint + Test → Docker build + push     |
| Tag `v*.*.*`          | All above → SSH deploy to production  |

### Required GitHub Secrets

| Secret            | Description                        |
|-------------------|------------------------------------|
| `DEPLOY_HOST`     | Production server IP/hostname      |
| `DEPLOY_USER`     | SSH username                       |
| `DEPLOY_SSH_KEY`  | Private SSH key for deployment     |

### Release Process

```bash
# Bump version in .env / pyproject / docs, then:
git tag -a v2.1.0 -m "Release v2.1.0: add PostgreSQL support"
git push origin v2.1.0
# → CI builds + pushes image → SSH deploys → GitHub Release created
```

---

## Security Checklist

- [ ] `JWT_SECRET_KEY` is at least 32 random characters (never a placeholder)
- [ ] `.env` is in `.gitignore` and never committed
- [ ] `APP_ENV=production` in production environment
- [ ] TLS certificate installed and HTTPS enforced by Nginx
- [ ] `CORS_ORIGINS` set to exact production frontend domain(s)
- [ ] Default Grafana password changed
- [ ] API not exposed on public internet without Nginx (remove `ports: 8000:8000` in compose)
- [ ] Docker containers running as non-root (`appuser`)
- [ ] Regular backups scheduled and restore tested
- [ ] Alembic migrations used for all schema changes
- [ ] `GET /health/detailed` not publicly exposed (restrict in Nginx if needed)

---

## Troubleshooting

### API won't start

```bash
# Check logs
docker compose logs api

# Verify .env
python -c "from app.core.config import settings; print(settings.APP_ENV)"
```

### Database errors

```bash
# Re-initialize tables
python -c "from app.database.db_init import init_db; init_db()"

# Check migration state
alembic current
```

### ML model not found

```bash
# Trigger training via API
curl -X POST http://localhost:8000/api/ml/pipeline/generate \
  -H "Authorization: Bearer <token>"

curl -X POST http://localhost:8000/api/predictions/train \
  -H "Authorization: Bearer <token>"
```

### Port already in use

```bash
# Find and kill process on port 8000
# Windows PowerShell:
netstat -ano | findstr :8000
taskkill /PID <PID> /F

# Linux:
lsof -ti:8000 | xargs kill -9
```

### Reset everything (development)

```bash
docker compose down -v         # stop + remove all volumes
rm -f supply_chain.db
rm -rf ml/saved_models/*
docker compose up -d
```
