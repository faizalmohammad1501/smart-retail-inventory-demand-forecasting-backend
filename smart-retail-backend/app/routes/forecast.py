from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import Dict, Any

from app.database.connection import get_db
from app.services.order_service import OrderService

router = APIRouter(prefix="/api/forecast", tags=["Forecast & Analytics"])

@router.get("/overview")
def get_forecast_overview(db: Session = Depends(get_db)) -> Dict[str, Any]:
    """
    Get overall analytics and forecast overview.
    Provides summary of order performance, SLA compliance, and bottlenecks.
    """
    try:
        service = OrderService(db)
        analytics = service.get_analytics_summary()
        
        return {
            "status": "success",
            "analytics": analytics,
            "message": "Analytics data retrieved successfully"
        }
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )

@router.get("/bottleneck-analysis")
def get_bottleneck_analysis(db: Session = Depends(get_db)) -> Dict[str, Any]:
    """
    Detailed bottleneck analysis across all orders.
    Identifies stages causing most delays.
    """
    try:
        service = OrderService(db)
        analytics = service.get_analytics_summary()
        
        bottleneck_summary = analytics.get('bottleneck_summary', {})
        total_orders = analytics.get('total_orders', 0)
        
        # Calculate percentages
        bottleneck_percentage = {}
        if total_orders > 0:
            for stage, count in bottleneck_summary.items():
                bottleneck_percentage[stage] = round((count / total_orders) * 100, 2)
        
        return {
            "status": "success",
            "total_orders_analyzed": total_orders,
            "bottleneck_count": bottleneck_summary,
            "bottleneck_percentage": bottleneck_percentage,
            "recommendations": generate_bottleneck_recommendations(bottleneck_percentage)
        }
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )

@router.get("/sla-compliance")
def get_sla_compliance(db: Session = Depends(get_db)) -> Dict[str, Any]:
    """
    SLA compliance analysis.
    Provides breach statistics and compliance rates.
    """
    try:
        service = OrderService(db)
        analytics = service.get_analytics_summary()
        
        return {
            "status": "success",
            "total_orders": analytics.get('total_orders', 0),
            "sla_breach_count": analytics.get('sla_breach_count', 0),
            "sla_compliance_rate": analytics.get('sla_compliance_rate', 0),
            "avg_total_time_hours": analytics.get('avg_total_time', 0),
            "compliance_status": "Good" if analytics.get('sla_compliance_rate', 0) > 90 else "Needs Improvement"
        }
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )

def generate_bottleneck_recommendations(bottleneck_percentage: Dict[str, float]) -> list:
    """Generate recommendations based on bottleneck analysis"""
    recommendations = []
    
    for stage, percentage in bottleneck_percentage.items():
        if percentage > 40:
            recommendations.append(f"Critical: {stage} stage needs immediate attention ({percentage}% of orders)")
        elif percentage > 25:
            recommendations.append(f"Warning: {stage} stage shows concerning delays ({percentage}% of orders)")
    
    if not recommendations:
        recommendations.append("All stages performing within acceptable parameters")
    
    return recommendations