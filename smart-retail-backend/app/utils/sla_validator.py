from typing import Optional, Tuple, Dict

class SLAConfig:
    """Configurable SLA thresholds in hours"""
    PROCUREMENT_SLA = 48.0  # 2 days
    PROCESSING_SLA = 24.0   # 1 day
    DISPATCH_SLA = 12.0     # 12 hours
    DELIVERY_SLA = 72.0     # 3 days
    TOTAL_SLA = 156.0       # 6.5 days

def check_sla_breach(procurement_time: Optional[float],
                     processing_time: Optional[float],
                     dispatch_time: Optional[float],
                     delivery_time: Optional[float],
                     total_time: Optional[float]) -> Tuple[bool, Optional[str]]:
    """
    Check for SLA breaches across all stages.
    
    Args:
        procurement_time: Duration of procurement stage in hours
        processing_time: Duration of processing stage in hours
        dispatch_time: Duration of dispatch stage in hours
        delivery_time: Duration of delivery stage in hours
        total_time: Total order lifecycle duration in hours
        
    Returns:
        Tuple of (sla_breach: bool, breached_stage: str or None)
    """
    breaches = []
    
    # Check each stage against SLA threshold
    if procurement_time and procurement_time > SLAConfig.PROCUREMENT_SLA:
        breaches.append(('procurement', procurement_time))
    
    if processing_time and processing_time > SLAConfig.PROCESSING_SLA:
        breaches.append(('processing', processing_time))
    
    if dispatch_time and dispatch_time > SLAConfig.DISPATCH_SLA:
        breaches.append(('dispatch', dispatch_time))
    
    if delivery_time and delivery_time > SLAConfig.DELIVERY_SLA:
        breaches.append(('delivery', delivery_time))
    
    # Check total time
    if total_time and total_time > SLAConfig.TOTAL_SLA:
        breaches.append(('total', total_time))
    
    if breaches:
        # Return the stage with maximum breach
        max_breach_stage = max(breaches, key=lambda x: x[1])
        return True, max_breach_stage[0]
    
    return False, None

def validate_sla(durations: Dict[str, Optional[float]]) -> Dict[str, any]:
    """
    Validate SLA for all calculated durations.
    
    Args:
        durations: Dictionary containing all calculated duration metrics
        
    Returns:
        Dictionary with sla_breach and breached_stage fields
    """
    sla_breach, breached_stage = check_sla_breach(
        procurement_time=durations.get('procurement_time'),
        processing_time=durations.get('processing_time'),
        dispatch_time=durations.get('dispatch_time_duration'),
        delivery_time=durations.get('delivery_time_duration'),
        total_time=durations.get('total_time')
    )
    
    return {
        'sla_breach': sla_breach,
        'breached_stage': breached_stage
    }
