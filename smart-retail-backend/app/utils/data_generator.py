import csv
from datetime import datetime, timedelta
from typing import List, Dict
import random

def generate_sample_orders(count: int = 50) -> List[Dict]:
    \"\"\"
    Generate sample order data for testing and demonstration.
    
    Args:
        count: Number of sample orders to generate
        
    Returns:
        List of order dictionaries with realistic lifecycle data
    \"\"\"
    orders = []
    statuses = [\"pending\", \"processing\", \"dispatched\", \"delivered\"]
    
    for i in range(1, count + 1):
        # Start with order placement
        order_placed = datetime.now() - timedelta(days=random.randint(1, 30))
        
        # Add realistic delays for each stage
        procurement_delay = random.randint(12, 72)  # 12-72 hours
        processing_delay = random.randint(6, 48)    # 6-48 hours
        dispatch_delay = random.randint(2, 24)      # 2-24 hours
        delivery_delay = random.randint(24, 120)    # 24-120 hours
        
        procurement_completed = order_placed + timedelta(hours=procurement_delay)
        processing_completed = procurement_completed + timedelta(hours=processing_delay)
        dispatched = processing_completed + timedelta(hours=dispatch_delay)
        delivered = dispatched + timedelta(hours=delivery_delay)
        
        order = {
            \"order_number\": f\"ORD-2026-{i:04d}\",
            \"product_id\": random.randint(1, 10),
            \"supplier_id\": random.randint(1, 5),
            \"quantity\": random.randint(10, 500),
            \"unit_price\": round(random.uniform(10.0, 500.0), 2),
            \"order_placed_at\": order_placed.isoformat(),
            \"procurement_completed_at\": procurement_completed.isoformat(),
            \"processing_completed_at\": processing_completed.isoformat(),
            \"dispatched_at\": dispatched.isoformat(),
            \"delivered_at\": delivered.isoformat(),
            \"status\": random.choice(statuses)
        }\n        \n        orders.append(order)\n    \n    return orders\n\ndef export_orders_to_csv(orders: List[Dict], filename: str = \"sample_orders.csv\"):\n    \"\"\"\n    Export orders to CSV file for Tableau integration.\n    \n    Args:\n        orders: List of order dictionaries\n        filename: Output CSV filename\n    \"\"\"\n    if not orders:\n        print(\"No orders to export\")\n        return\n    \n    # Define CSV fields\n    fieldnames = [\n        \"order_number\", \"product_id\", \"supplier_id\", \"quantity\", \"unit_price\",\n        \"order_placed_at\", \"procurement_completed_at\", \"processing_completed_at\",\n        \"dispatched_at\", \"delivered_at\", \"status\",\n        \"procurement_time\", \"processing_time\", \"dispatch_time_duration\",\n        \"delivery_time_duration\", \"total_time\",\n        \"sla_breach\", \"breached_stage\", \"bottleneck_stage\"\n    ]\n    \n    with open(filename, 'w', newline='', encoding='utf-8') as csvfile:\n        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)\n        writer.writeheader()\n        \n        for order in orders:\n            # Flatten order data for CSV\n            csv_row = {field: order.get(field, '') for field in fieldnames}\n            writer.writerow(csv_row)\n    \n    print(f\"Exported {len(orders)} orders to {filename}\")\n\ndef format_duration(hours: float) -> str:\n    \"\"\"Format duration in hours to human-readable string\"\"\"\n    if hours < 24:\n        return f\"{hours:.2f} hours\"\n    days = hours / 24\n    return f\"{days:.2f} days ({hours:.2f} hours)\"\n