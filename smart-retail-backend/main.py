from fastapi import FastAPI, status
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager

from app.database.db_init import init_db
from app.routes import sales, suppliers, inventory, forecast, auth
from app.routes import ml_pipeline
from app.routes import analytics_routes
from app.routes import prediction
from app.routes import inventory_recommendations
from app.routes import notifications
from app.routes import reports

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan event handler for startup and shutdown"""
    # Startup
    print("Starting Supply Chain Analytics API...")
    init_db()
    print("Database initialized successfully")
    yield
    # Shutdown
    print("Shutting down Supply Chain Analytics API...")

app = FastAPI(
    title="Supply Chain Analytics API",
    description="Complete supply chain analytics platform with order lifecycle tracking, SLA monitoring, and bottleneck detection",
    version="1.0.0",
    lifespan=lifespan
)

# CORS Configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Configure appropriately for production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
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

@app.get("/", status_code=status.HTTP_200_OK)
def root():
    """API root endpoint"""
    return {
        "message": "Supply Chain Analytics API",
        "version": "1.0.0",
        "status": "operational",
        "features": [
            "Order lifecycle tracking",
            "Automated duration calculation",
            "SLA breach detection",
            "Bottleneck analysis",
            "Real-time analytics"
        ]
    }

@app.get("/health", status_code=status.HTTP_200_OK)
def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "service": "Supply Chain Analytics API"
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)