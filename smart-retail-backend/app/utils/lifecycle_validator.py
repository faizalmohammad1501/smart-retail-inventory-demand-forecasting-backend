from datetime import datetime
from typing import Optional, List

class LifecycleValidationError(Exception):
    """Custom exception for lifecycle validation errors"""
    pass

def validate_timestamp_sequence(order_placed_at: Optional[datetime],
                               procurement_completed_at: Optional[datetime],
                               processing_completed_at: Optional[datetime],
                               dispatched_at: Optional[datetime],
                               delivered_at: Optional[datetime]) -> List[str]:
    """
    Validate that lifecycle timestamps follow correct chronological order.
    
    Args:
        All lifecycle timestamps
        
    Returns:
        List of validation error messages (empty if valid)
    """
    errors = []
    
    timestamps = {
        'order_placed_at': order_placed_at,
        'procurement_completed_at': procurement_completed_at,
        'processing_completed_at': processing_completed_at,
        'dispatched_at': dispatched_at,
        'delivered_at': delivered_at
    }
    
    # Define expected sequence
    sequence = [
        ('order_placed_at', 'procurement_completed_at'),
        ('procurement_completed_at', 'processing_completed_at'),
        ('processing_completed_at', 'dispatched_at'),
        ('dispatched_at', 'delivered_at')
    ]
    
    for prev_stage, next_stage in sequence:
        prev_time = timestamps.get(prev_stage)
        next_time = timestamps.get(next_stage)
        
        # Only validate if both timestamps exist
        if prev_time and next_time:
            if next_time <= prev_time:
                errors.append(
                    f\"Invalid sequence: {next_stage} must be after {prev_stage}\"
                )
    
    return errors

def validate_order_lifecycle(order_data: dict) -> bool:
    """
    Validate order lifecycle data for consistency.
    
    Args:
        order_data: Dictionary containing order lifecycle information
        
    Returns:
        True if valid
        
    Raises:
        LifecycleValidationError if validation fails
    """
    errors = validate_timestamp_sequence(
        order_data.get('order_placed_at'),
        order_data.get('procurement_completed_at'),
        order_data.get('processing_completed_at'),
        order_data.get('dispatched_at'),
        order_data.get('delivered_at')
    )
    
    if errors:
        raise LifecycleValidationError(\"; \".join(errors))
    
    return True
