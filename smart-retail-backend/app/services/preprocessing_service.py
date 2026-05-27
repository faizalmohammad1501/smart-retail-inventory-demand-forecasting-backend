from typing import Dict, Any
from app.utils.time_calculator import calculate_all_durations
from app.utils.sla_validator import validate_sla
from app.utils.bottleneck_detector import analyze_bottleneck
from app.utils.lifecycle_validator import validate_order_lifecycle, LifecycleValidationError

class OrderPreprocessingService:
    """
    Service for preprocessing order lifecycle data before database save.
    Handles duration calculation, SLA validation, and bottleneck detection.
    """
    
    @staticmethod
    def preprocess_order(order_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Complete preprocessing pipeline for order lifecycle data.
        
        Args:
            order_data: Raw order data with lifecycle timestamps
            
        Returns:
            Enhanced order data with analytics fields
            
        Raises:
            LifecycleValidationError: If lifecycle validation fails
        """
        try:
            # Step 1: Validate lifecycle timestamp sequence
            validate_order_lifecycle(order_data)
            
            # Step 2: Calculate all duration metrics
            durations = calculate_all_durations(order_data)
            
            # Step 3: Perform SLA validation
            sla_results = validate_sla(durations)
            
            # Step 4: Detect bottleneck stage
            bottleneck_results = analyze_bottleneck(durations)
            
            # Step 5: Merge all analytics into order data
            processed_data = {
                **order_data,
                **durations,
                **sla_results,
                **bottleneck_results
            }
            
            return processed_data
            
        except LifecycleValidationError:
            raise
        except Exception as e:
            raise Exception(f\"Preprocessing failed: {str(e)}\")
    
    @staticmethod
    def calculate_analytics_summary(processed_orders: list) -> Dict[str, Any]:
        """
        Calculate aggregate analytics for multiple orders.
        
        Args:
            processed_orders: List of processed order dictionaries
            
        Returns:
            Dictionary with aggregate analytics metrics
        """
        if not processed_orders:
            return {
                'total_orders': 0,
                'sla_breach_count': 0,
                'sla_compliance_rate': 0,
                'avg_total_time': 0,
                'bottleneck_summary': {}
            }
        
        total_orders = len(processed_orders)
        sla_breaches = sum(1 for order in processed_orders if order.get('sla_breach'))
        
        # Calculate average total time
        total_times = [order.get('total_time') for order in processed_orders if order.get('total_time')]
        avg_total_time = round(sum(total_times) / len(total_times), 2) if total_times else 0
        
        # Bottleneck analysis
        bottleneck_count = {}
        for order in processed_orders:
            bottleneck = order.get('bottleneck_stage')
            if bottleneck:
                bottleneck_count[bottleneck] = bottleneck_count.get(bottleneck, 0) + 1
        
        return {
            'total_orders': total_orders,
            'sla_breach_count': sla_breaches,
            'sla_compliance_rate': round((1 - sla_breaches / total_orders) * 100, 2),
            'avg_total_time': avg_total_time,
            'bottleneck_summary': bottleneck_count
        }
