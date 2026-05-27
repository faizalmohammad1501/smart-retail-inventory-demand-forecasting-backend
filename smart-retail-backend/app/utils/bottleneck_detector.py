from typing import Optional, Dict

def detect_bottleneck_stage(procurement_time: Optional[float],
                            processing_time: Optional[float],
                            dispatch_time: Optional[float],
                            delivery_time: Optional[float]) -> Optional[str]:
    """
    Identify the bottleneck stage with maximum delay.
    
    Args:
        procurement_time: Duration of procurement stage in hours
        processing_time: Duration of processing stage in hours
        dispatch_time: Duration of dispatch stage in hours
        delivery_time: Duration of delivery stage in hours
        
    Returns:
        Name of the bottleneck stage or None if no valid durations
    """
    stages = {
        'procurement': procurement_time,
        'processing': processing_time,
        'dispatch': dispatch_time,
        'delivery': delivery_time
    }
    
    # Filter out None values
    valid_stages = {stage: duration for stage, duration in stages.items() if duration is not None}
    
    if not valid_stages:
        return None
    
    # Find stage with maximum duration
    bottleneck = max(valid_stages.items(), key=lambda x: x[1])
    return bottleneck[0]

def analyze_bottleneck(durations: Dict[str, Optional[float]]) -> Dict[str, Optional[str]]:
    """
    Analyze durations to identify bottleneck stage.
    
    Args:
        durations: Dictionary containing all calculated duration metrics
        
    Returns:
        Dictionary with bottleneck_stage field
    """
    bottleneck_stage = detect_bottleneck_stage(
        procurement_time=durations.get('procurement_time'),
        processing_time=durations.get('processing_time'),
        dispatch_time=durations.get('dispatch_time_duration'),
        delivery_time=durations.get('delivery_time_duration')
    )
    
    return {
        'bottleneck_stage': bottleneck_stage
    }
