from datetime import datetime
from typing import Optional

def calculate_duration_hours(start_time: Optional[datetime], end_time: Optional[datetime]) -> Optional[float]:
    """
    Calculate duration between two timestamps in hours.
    
    Args:
        start_time: Start timestamp
        end_time: End timestamp
        
    Returns:
        Duration in hours (rounded to 2 decimal places) or None if invalid
    """
    if not start_time or not end_time:
        return None
    
    try:
        duration = (end_time - start_time).total_seconds() / 3600
        # Prevent negative durations
        if duration < 0:
            return None
        return round(duration, 2)
    except Exception:
        return None

def calculate_procurement_time(order_placed_at: Optional[datetime], 
                               procurement_completed_at: Optional[datetime]) -> Optional[float]:
    """Calculate time from order placement to procurement completion"""
    return calculate_duration_hours(order_placed_at, procurement_completed_at)

def calculate_processing_time(procurement_completed_at: Optional[datetime],
                              processing_completed_at: Optional[datetime]) -> Optional[float]:
    """Calculate time from procurement completion to processing completion"""
    return calculate_duration_hours(procurement_completed_at, processing_completed_at)

def calculate_dispatch_time(processing_completed_at: Optional[datetime],
                           dispatched_at: Optional[datetime]) -> Optional[float]:
    """Calculate time from processing completion to dispatch"""
    return calculate_duration_hours(processing_completed_at, dispatched_at)

def calculate_delivery_time(dispatched_at: Optional[datetime],
                           delivered_at: Optional[datetime]) -> Optional[float]:
    """Calculate time from dispatch to delivery"""
    return calculate_duration_hours(dispatched_at, delivered_at)

def calculate_total_time(order_placed_at: Optional[datetime],
                        delivered_at: Optional[datetime]) -> Optional[float]:
    """Calculate total time from order placement to delivery"""
    return calculate_duration_hours(order_placed_at, delivered_at)

def calculate_all_durations(order_data: dict) -> dict:
    """
    Calculate all duration metrics for an order lifecycle.
    
    Args:
        order_data: Dictionary containing lifecycle timestamps
        
    Returns:
        Dictionary with calculated duration fields
    """
    durations = {
        'procurement_time': calculate_procurement_time(
            order_data.get('order_placed_at'),
            order_data.get('procurement_completed_at')
        ),
        'processing_time': calculate_processing_time(
            order_data.get('procurement_completed_at'),
            order_data.get('processing_completed_at')
        ),
        'dispatch_time_duration': calculate_dispatch_time(
            order_data.get('processing_completed_at'),
            order_data.get('dispatched_at')
        ),
        'delivery_time_duration': calculate_delivery_time(
            order_data.get('dispatched_at'),
            order_data.get('delivered_at')
        ),
        'total_time': calculate_total_time(
            order_data.get('order_placed_at'),
            order_data.get('delivered_at')
        )
    }
    
    return durations
