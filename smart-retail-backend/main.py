from fastapi import FastAPI, status, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from contextlib import asynccontextmanager
import logging

from app.core.config import settings
from app.database.db_init import init_db
from app.database.connection import get_db
from app.middleware.error_handler import (
    logging_middleware,
    error_handling_middleware,
    validation_exception_handler,
    http_exception_handler,
)
from app.middleware.security_headers import SecurityHeadersMiddleware
from app.routes import sales, suppliers, inventory, forecast, auth
from app.routes import ml_pipeline
from app.routes import analytics_routes
from app.routes import prediction
from app.routes import inventory_recommendations
from app.routes import notifications
from app.routes import reports
from app.routes import dashboard

logger = logging.getLogger("smart_retail")


# ── Lifespan ──────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── Startup ──────────────────────────────────────────────────────────────
    logger.info(f"Starting {settings.APP_NAME} v{settings.APP_VERSION} [{settings.APP_ENV}]")
    init_db()
    logger.info("Database tables initialised")

    if settings.APP_ENV == "production" and "change-in-production" in settings.JWT_SECRET_KEY:
        logger.warning("SECURITY WARNING: Default JWT_SECRET_KEY detected in production!")

    logger.info(f"CORS origins: {settings.cors_origins_list}")
    logger.info(f"Rate limit: {settings.RATE_LIMIT_PER_MINUTE} req/min")
    logger.info("API is ready — http://localhost:8000/docs")
    yield
    # ── Shutdown ─────────────────────────────────────────────────────────────
    logger.info(f"Shutting down {settings.APP_NAME}")


# ── Application factory ───────────────────────────────────────────────────────

app = FastAPI(
    title=settings.APP_NAME,
    description=(
        "Smart Retail Platform — Full-stack supply chain analytics backend.\n\n"
        "**Modules:**\n"
        "- JWT Authentication & RBAC\n"
        "- Orders, Products, Suppliers, Inventory\n"
        "- ML Demand Forecasting Pipeline\n"
        "- Analytics, SLA & Bottleneck Monitoring\n"
        "- Smart Inventory Recommendations\n"
        "- Notification & Alert Automation\n"
        "- Reporting & Business Insights\n"
        "- Executive Dashboard & CSV/PDF Exports\n\n"
        "All endpoints require a **Bearer token** — register via `/api/auth/register` "
        "then login via `/api/auth/login`."
    ),
    version=settings.APP_VERSION,
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)

# ── Middleware stack (order matters — first added = outermost) ────────────────

# 1. Security headers on every response
app.add_middleware(SecurityHeadersMiddleware)

# 2. Request/Response logging (adds X-Request-ID header)
app.add_middleware(BaseHTTPMiddleware, dispatch=logging_middleware)

# 3. Global exception catch-all (innermost)
app.add_middleware(BaseHTTPMiddleware, dispatch=error_handling_middleware)

# 4. CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list if settings.is_production else ["*"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
    expose_headers=["X-Request-ID", "X-Process-Time-Ms"],
)

# ── Exception handlers ────────────────────────────────────────────────────────

app.add_exception_handler(RequestValidationError, validation_exception_handler)
app.add_exception_handler(StarletteHTTPException, http_exception_handler)

# ── Routers ───────────────────────────────────────────────────────────────────

app.include_router(auth.router)
app.include_router(sales.router)
app.include_router(suppliers.router)
app.include_router(inventory.router)
app.include_router(forecast.router)
app.include_router(ml_pipeline.router)
app.include_router(analytics_routes.router)
app.include_router(prediction.router)
app.include_router(inventory_recommendations.router)
app.include_router(notifications.router)
app.include_router(reports.router)
app.include_router(dashboard.router)


# ── System endpoints ──────────────────────────────────────────────────────────

@app.get("/", status_code=status.HTTP_200_OK, tags=["System"])
def root():
    """API root — lists available modules and quick-start links."""
    return {
        "status": "operational",
        "service": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "environment": settings.APP_ENV,
        "modules": [
            "Authentication & RBAC       /api/auth/",
            "Orders & Sales              /api/orders/ | /api/products/",
            "Suppliers                   /api/suppliers/",
            "Inventory                   /api/inventory/",
            "ML Pipeline                 /api/ml/pipeline/",
            "Demand Forecasting          /api/predictions/",
            "Analytics                   /api/analytics/",
            "Recommendations             /api/recommendations/",
            "Notifications               /api/notifications/",
            "Reporting                   /api/reports/",
            "Dashboard & Exports         /api/dashboard/",
        ],
        "docs": {
            "swagger": "/docs",
            "redoc": "/redoc",
            "openapi_json": "/openapi.json",
        },
    }


@app.get("/health", status_code=status.HTTP_200_OK, tags=["System"])
def health_liveness():
    """
    Liveness probe — lightweight check for load balancers and container orchestrators.
    Returns 200 as long as the process is running.
    """
    return {
        "status": "healthy",
        "service": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "environment": settings.APP_ENV,
    }


@app.get("/health/detailed", status_code=status.HTTP_200_OK, tags=["System"])
def health_readiness(db=Depends(get_db)):
    """
    Readiness probe — deep check across database, ML model, datasets, and disk.
    Returns 200 if all critical components are healthy, 503 if degraded.
    """
    from fastapi.responses import JSONResponse
    from app.core.health import full_health_check

    result = full_health_check(db)
    status_code = 200 if result["status"] == "healthy" else 503
    return JSONResponse(content=result, status_code=status_code)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=settings.APP_ENV != "production",
        log_level=settings.LOG_LEVEL.lower(),
    )
