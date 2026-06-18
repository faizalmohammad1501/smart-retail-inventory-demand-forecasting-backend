"""
Smart Retail Platform — End-to-End Demo Workflow Script
=======================================================
Walks through 6 business scenarios that showcase every major platform feature.
Designed for live demo, portfolio presentation, or evaluator walkthrough.

Scenarios:
  1. Inventory Command Centre — stock levels, low-stock alerts, recommendations
  2. Order Lifecycle & SLA Monitoring — create order, track lifecycle, SLA breach
  3. Demand Forecasting — ML pipeline, 30-day forecast, model accuracy
  4. Supplier Scorecard — performance comparison, bottleneck identification
  5. Executive Dashboard — KPIs, charts, all widgets in one call
  6. Business Reports & Exports — sales summary, CSV export, PDF trigger

Usage:
    python demo_workflow.py                        # all scenarios
    python demo_workflow.py --scenario 3           # run only scenario 3
    python demo_workflow.py --url http://localhost:8000
    python demo_workflow.py --pause                # pause between scenarios

Prerequisites:
    1. Server running: uvicorn main:app --reload
    2. Demo data loaded: python demo_seed.py --reset --verify
"""

import argparse
import json
import sys
import time
from typing import Any

try:
    import requests
except ImportError:
    print("Install requests: pip install requests")
    sys.exit(1)

BASE_URL = "http://localhost:8000"
TOKEN = ""

# ── colour helpers ────────────────────────────────────────────
_G = "\033[32m"; _R = "\033[31m"; _Y = "\033[33m"
_C = "\033[36m"; _B = "\033[1m";  _D = "\033[2m"; _X = "\033[0m"


def hdr(text: str) -> None:
    print(f"\n{_B}{_C}{'═' * 62}{_X}")
    print(f"{_B}{_C}  {text}{_X}")
    print(f"{_B}{_C}{'═' * 62}{_X}\n")


def step(n: int, text: str) -> None:
    print(f"  {_B}Step {n}.{_X} {text}")


def ok(label: str, value: Any = "") -> None:
    val = f"  → {_D}{value}{_X}" if value != "" else ""
    print(f"    {_G}✓{_X} {label}{val}")


def warn(label: str) -> None:
    print(f"    {_Y}⚠{_X} {label}")


def fail(label: str, detail: str = "") -> None:
    print(f"    {_R}✗{_X} {label}  {_D}{detail}{_X}")


def api(method: str, path: str, data: dict = None, params: dict = None) -> tuple[int, Any]:
    headers = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}
    url = f"{BASE_URL}{path}"
    try:
        r = getattr(requests, method)(url, json=data, params=params, headers=headers, timeout=30)
        try:
            body = r.json()
        except Exception:
            body = r.text
        return r.status_code, body
    except requests.exceptions.ConnectionError:
        fail(f"Cannot connect to {url} — is the server running?")
        sys.exit(1)


def pretty(body: Any, max_lines: int = 12) -> None:
    text = json.dumps(body, indent=2, default=str)
    lines = text.splitlines()
    if len(lines) > max_lines:
        lines = lines[:max_lines] + [f"  {_D}... ({len(lines) - max_lines} more lines){_X}"]
    for line in lines:
        print(f"    {_D}{line}{_X}")


# ════════════════════════════════════════════════════════════════
#  Auth
# ════════════════════════════════════════════════════════════════

def login(username: str = "admin", password: str = "Admin@123") -> bool:
    global TOKEN
    headers = {"Content-Type": "application/json"}
    r = requests.post(f"{BASE_URL}/api/auth/login",
                      json={"username": username, "password": password},
                      headers=headers, timeout=10)
    if r.status_code == 200:
        TOKEN = r.json().get("access_token", "")
        return True
    return False


# ════════════════════════════════════════════════════════════════
#  Scenario 1 — Inventory Command Centre
# ════════════════════════════════════════════════════════════════

def scenario_inventory() -> None:
    hdr("Scenario 1 — Inventory Command Centre")
    print("  Goal: Identify at-risk stock items, trigger alerts, get recommendations.\n")

    step(1, "Get inventory health overview")
    status, body = api("get", "/api/recommendations/health")
    if status == 200:
        ok("Inventory health fetched", f"HTTP {status}")
        pretty(body)
    else:
        fail("Health endpoint", f"HTTP {status}")

    step(2, "List active low-stock / stockout alerts")
    status, body = api("get", "/api/recommendations/alerts")
    if status == 200:
        alerts = body if isinstance(body, list) else body.get("data", [])
        ok(f"Alerts retrieved — {len(alerts)} active alert(s)", f"HTTP {status}")
        for a in alerts[:3]:
            print(f"      {_Y}•{_X} {a.get('product_name', a.get('sku',''))} "
                  f"— {a.get('alert_type', '')}  qty={a.get('quantity_available', '?')}")
    else:
        fail("Alerts endpoint", f"HTTP {status}")

    step(3, "Get replenishment recommendations (EOQ-based)")
    status, body = api("get", "/api/recommendations/replenishment")
    if status == 200:
        recs = body if isinstance(body, list) else body.get("data", [])
        ok(f"{len(recs)} replenishment recommendation(s) generated", f"HTTP {status}")
        for r in recs[:3]:
            print(f"      {_G}•{_X} {r.get('product_name', '')}  "
                  f"reorder qty={r.get('recommended_order_qty', r.get('reorder_quantity', '?'))}")
    else:
        fail("Replenishment endpoint", f"HTTP {status}")

    step(4, "Run notification engine — auto-generate alerts")
    status, body = api("post", "/api/notifications/run", {})
    if status == 200:
        ok("Notification engine executed", f"HTTP {status}")
        pretty(body, max_lines=6)
    else:
        fail("Notification run", f"HTTP {status}")

    step(5, "View notification summary")
    status, body = api("get", "/api/notifications/summary")
    if status == 200:
        ok("Notification summary", f"HTTP {status}")
        pretty(body, max_lines=8)
    else:
        fail("Notification summary", f"HTTP {status}")


# ════════════════════════════════════════════════════════════════
#  Scenario 2 — Order Lifecycle & SLA Monitoring
# ════════════════════════════════════════════════════════════════

def scenario_order_lifecycle() -> None:
    hdr("Scenario 2 — Order Lifecycle & SLA Monitoring")
    print("  Goal: Create an order, view SLA analytics, identify bottlenecks.\n")

    step(1, "Get available products")
    status, products = api("get", "/api/inventory/products/")
    if status != 200 or not isinstance(products, list) or len(products) == 0:
        fail("Cannot fetch products", f"HTTP {status}")
        return
    product = products[0]
    ok(f"Using product: '{product['product_name']}' (id={product['id']})")

    step(2, "Create a new purchase order")
    status, body = api("post", "/api/sales/orders/", {
        "product_id":   product["id"],
        "supplier_id":  product.get("supplier_id", 1),
        "quantity":     25,
        "unit_price":   product.get("unit_price", 50.0),
        "total_amount": round(25 * product.get("unit_price", 50.0), 2),
        "status":       "pending",
    })
    if status in (200, 201):
        order_id = body.get("id", body.get("order_id", "?"))
        ok(f"Order created — id={order_id}  status=pending")
    else:
        fail("Order creation failed", f"HTTP {status}  {str(body)[:100]}")
        order_id = None

    step(3, "Retrieve analytics summary (order KPIs)")
    status, body = api("get", "/api/analytics/summary")
    if status == 200:
        ok("Analytics summary fetched", f"HTTP {status}")
        pretty(body, max_lines=10)
    else:
        fail("Analytics summary", f"HTTP {status}")

    step(4, "Identify SLA breaches")
    status, body = api("get", "/api/analytics/sla-breaches")
    if status == 200:
        breaches = body if isinstance(body, list) else body.get("data", [])
        ok(f"{len(breaches)} SLA breach(es) found")
        for b in breaches[:3]:
            print(f"      {_R}•{_X} Order {b.get('order_number', b.get('id', '?'))} "
                  f"— breached at: {b.get('breached_stage', '?')}")
    else:
        fail("SLA breaches", f"HTTP {status}")

    step(5, "Detect supply chain bottlenecks")
    status, body = api("get", "/api/analytics/bottlenecks")
    if status == 200:
        bottlenecks = body if isinstance(body, list) else body.get("data", [])
        ok(f"{len(bottlenecks)} bottleneck record(s) identified")
        for b in bottlenecks[:3]:
            print(f"      {_Y}•{_X} Stage: {b.get('bottleneck_stage', '?')}  "
                  f"avg_delay={b.get('avg_delay_hours', '?')}h")
    else:
        fail("Bottlenecks", f"HTTP {status}")

    step(6, "SLA compliance report")
    status, body = api("get", "/api/reports/operations/sla-compliance")
    if status == 200:
        ok("SLA compliance report generated", f"HTTP {status}")
        pretty(body, max_lines=8)
    else:
        fail("SLA compliance report", f"HTTP {status}")


# ════════════════════════════════════════════════════════════════
#  Scenario 3 — Demand Forecasting
# ════════════════════════════════════════════════════════════════

def scenario_forecasting() -> None:
    hdr("Scenario 3 — ML Demand Forecasting Pipeline")
    print("  Goal: Generate synthetic data, train model, produce 30-day forecast.\n")

    step(1, "Generate synthetic training data")
    status, body = api("post", "/api/ml/pipeline/generate", {})
    if status == 200:
        ok("Training data generated", f"HTTP {status}")
        pretty(body, max_lines=6)
    else:
        fail("Data generation", f"HTTP {status}  {str(body)[:100]}")

    step(2, "Run full preprocessing pipeline")
    status, body = api("post", "/api/ml/pipeline/run", {})
    if status in (200, 202):
        ok("Preprocessing pipeline executed", f"HTTP {status}")
        pretty(body, max_lines=6)
    else:
        fail("ML pipeline", f"HTTP {status}  {str(body)[:100]}")

    step(3, "Inspect engineered features")
    status, body = api("get", "/api/ml/pipeline/features")
    if status == 200:
        ok("Feature list retrieved", f"HTTP {status}")
        features = body if isinstance(body, list) else body.get("features", [])
        print(f"      Features ({len(features)}): {', '.join(str(f) for f in features[:8])}…")
    else:
        fail("Feature inspection", f"HTTP {status}")

    step(4, "Train GradientBoosting demand forecast model")
    status, body = api("post", "/api/predictions/train", {})
    if status == 200:
        ok("Model trained successfully", f"HTTP {status}")
        pretty(body, max_lines=8)
    else:
        fail("Model training", f"HTTP {status}  {str(body)[:150]}")

    step(5, "Check model status & metrics")
    status, body = api("get", "/api/predictions/model/status")
    if status == 200:
        ok("Model status checked", f"HTTP {status}")
        pretty(body, max_lines=10)
    else:
        fail("Model status", f"HTTP {status}")

    step(6, "Generate 30-day demand forecast for product 1")
    status, body = api("post", "/api/predictions/forecast",
                       {"product_id": 1, "days": 30})
    if status == 200:
        ok("30-day forecast generated", f"HTTP {status}")
        pretty(body, max_lines=10)
    else:
        fail("Forecast generation", f"HTTP {status}  {str(body)[:150]}")

    step(7, "Forecast accuracy report")
    status, body = api("get", "/api/reports/forecast/accuracy")
    if status == 200:
        ok("Forecast accuracy report generated", f"HTTP {status}")
        pretty(body, max_lines=8)
    else:
        fail("Forecast accuracy", f"HTTP {status}")


# ════════════════════════════════════════════════════════════════
#  Scenario 4 — Supplier Scorecard
# ════════════════════════════════════════════════════════════════

def scenario_supplier_scorecard() -> None:
    hdr("Scenario 4 — Supplier Performance & Scorecard")
    print("  Goal: Rank suppliers, identify underperformers, view bottleneck report.\n")

    step(1, "Get all suppliers")
    status, suppliers = api("get", "/api/suppliers/")
    if status == 200:
        sup_list = suppliers if isinstance(suppliers, list) else suppliers.get("data", [])
        ok(f"{len(sup_list)} supplier(s) in system")
    else:
        fail("Suppliers list", f"HTTP {status}")
        sup_list = []

    step(2, "Supplier performance report (ranked)")
    status, body = api("get", "/api/reports/suppliers/performance")
    if status == 200:
        ok("Supplier performance report", f"HTTP {status}")
        perf = body if isinstance(body, list) else body.get("data", [])
        for s in perf[:4]:
            print(f"      {'🏆' if s.get('rating', 0) >= 4 else '⚠️ '} "
                  f"{s.get('supplier_name', '?'):<30} "
                  f"orders={s.get('total_orders', '?')}  "
                  f"sla_breach_rate={s.get('sla_breach_rate', '?')}")
    else:
        fail("Supplier performance", f"HTTP {status}")

    step(3, "Detailed scorecard for supplier 1")
    status, body = api("get", "/api/reports/suppliers/1/scorecard")
    if status == 200:
        ok("Supplier scorecard generated", f"HTTP {status}")
        pretty(body, max_lines=10)
    else:
        warn(f"Scorecard unavailable for supplier 1: HTTP {status}")

    step(4, "Bottleneck analysis across supply chain")
    status, body = api("get", "/api/reports/operations/bottlenecks")
    if status == 200:
        ok("Bottleneck report", f"HTTP {status}")
        pretty(body, max_lines=8)
    else:
        fail("Bottleneck report", f"HTTP {status}")


# ════════════════════════════════════════════════════════════════
#  Scenario 5 — Executive Dashboard
# ════════════════════════════════════════════════════════════════

def scenario_dashboard() -> None:
    hdr("Scenario 5 — Executive Dashboard")
    print("  Goal: Load master dashboard, all widgets, all charts in one view.\n")

    step(1, "Master dashboard summary (all widgets in one call)")
    status, body = api("get", "/api/dashboard/summary")
    if status == 200:
        ok("Master dashboard loaded", f"HTTP {status}")
        top_keys = list(body.keys())[:8]
        print(f"      Top-level keys: {', '.join(top_keys)}")
    else:
        fail("Master dashboard", f"HTTP {status}  {str(body)[:100]}")

    step(2, "Individual widget data")
    widgets = [
        ("Sales KPIs",        "/api/dashboard/widgets/sales"),
        ("Inventory Health",  "/api/dashboard/widgets/inventory"),
        ("Supplier Ratings",  "/api/dashboard/widgets/suppliers"),
        ("Forecast Summary",  "/api/dashboard/widgets/forecast"),
        ("Active Alerts",     "/api/dashboard/widgets/alerts"),
    ]
    for label, path in widgets:
        status, body = api("get", path)
        if status == 200:
            ok(f"{label} widget loaded")
        else:
            fail(f"{label} widget", f"HTTP {status}")

    step(3, "Chart data (Chart.js compatible format)")
    charts = [
        ("Revenue Trend",         "/api/dashboard/charts/revenue-trend"),
        ("Order Status Pie",      "/api/dashboard/charts/order-status"),
        ("Top Products Bar",      "/api/dashboard/charts/top-products"),
        ("Inventory Health",      "/api/dashboard/charts/inventory-health"),
        ("Supplier Performance",  "/api/dashboard/charts/supplier-performance"),
        ("Category Revenue",      "/api/dashboard/charts/category-revenue"),
    ]
    for label, path in charts:
        status, body = api("get", path)
        if status == 200:
            labels_count = len((body.get("labels") or body.get("data", {}).get("labels", [])))
            ok(f"{label} chart — {labels_count} data point(s)")
        else:
            fail(f"{label} chart", f"HTTP {status}")


# ════════════════════════════════════════════════════════════════
#  Scenario 6 — Reports & Exports
# ════════════════════════════════════════════════════════════════

def scenario_reports_exports() -> None:
    hdr("Scenario 6 — Business Reports & Data Exports")
    print("  Goal: Demonstrate all 13 reports and CSV export capabilities.\n")

    step(1, "Sales intelligence reports")
    sales_reports = [
        ("Sales Summary",     "/api/reports/sales/summary"),
        ("Revenue Trends",    "/api/reports/sales/trends"),
        ("Top 10 Products",   "/api/reports/sales/top-products"),
        ("Category Revenue",  "/api/reports/sales/by-category"),
        ("Fulfillment Stats", "/api/reports/sales/fulfillment"),
    ]
    for label, path in sales_reports:
        status, body = api("get", path)
        ok(f"{label}") if status == 200 else fail(f"{label}", f"HTTP {status}")

    step(2, "Inventory intelligence reports")
    inv_reports = [
        ("Inventory Valuation", "/api/reports/inventory/valuation"),
        ("Inventory Turnover",  "/api/reports/inventory/turnover"),
        ("Inventory Aging",     "/api/reports/inventory/aging"),
    ]
    for label, path in inv_reports:
        status, body = api("get", path)
        ok(f"{label}") if status == 200 else fail(f"{label}", f"HTTP {status}")

    step(3, "Operations reports")
    ops_reports = [
        ("Operational KPIs",  "/api/reports/operations/kpis"),
        ("SLA Compliance",    "/api/reports/operations/sla-compliance"),
        ("Bottleneck Report", "/api/reports/operations/bottlenecks"),
        ("Forecast Accuracy", "/api/reports/forecast/accuracy"),
    ]
    for label, path in ops_reports:
        status, body = api("get", path)
        ok(f"{label}") if status == 200 else fail(f"{label}", f"HTTP {status}")

    step(4, "CSV exports")
    csv_exports = [
        ("Sales CSV",             "/api/reports/export/sales"),
        ("Inventory CSV",         "/api/reports/export/inventory"),
        ("Suppliers CSV",         "/api/reports/export/suppliers"),
        ("Forecast Accuracy CSV", "/api/dashboard/export/forecast-accuracy"),
        ("Notifications CSV",     "/api/dashboard/export/notifications"),
    ]
    for label, path in csv_exports:
        status, body = api("get", path)
        if status == 200:
            ok(f"{label} downloaded")
        else:
            fail(f"{label}", f"HTTP {status}")

    step(5, "PDF export endpoints (requires fpdf2)")
    pdf_exports = [
        ("Sales PDF",     "/api/dashboard/export/pdf/sales"),
        ("Inventory PDF", "/api/dashboard/export/pdf/inventory"),
    ]
    for label, path in pdf_exports:
        status, _ = api("get", path)
        if status == 200:
            ok(f"{label} generated")
        elif status == 501:
            warn(f"{label} — fpdf2 not installed (501 — expected without fpdf2)")
        else:
            fail(f"{label}", f"HTTP {status}")


# ════════════════════════════════════════════════════════════════
#  Main
# ════════════════════════════════════════════════════════════════

SCENARIOS = {
    1: ("Inventory Command Centre",      scenario_inventory),
    2: ("Order Lifecycle & SLA",         scenario_order_lifecycle),
    3: ("Demand Forecasting",            scenario_forecasting),
    4: ("Supplier Scorecard",            scenario_supplier_scorecard),
    5: ("Executive Dashboard",           scenario_dashboard),
    6: ("Business Reports & Exports",    scenario_reports_exports),
}


def main() -> None:
    global BASE_URL

    parser = argparse.ArgumentParser(description="Smart Retail demo workflow")
    parser.add_argument("--url",      default="http://localhost:8000")
    parser.add_argument("--scenario", type=int, choices=list(SCENARIOS.keys()),
                        help="Run a single scenario (1-6)")
    parser.add_argument("--pause",    action="store_true",
                        help="Pause between scenarios for live demo")
    parser.add_argument("--username", default="admin")
    parser.add_argument("--password", default="Admin@123")
    args = parser.parse_args()

    BASE_URL = args.url.rstrip("/")

    print(f"\n{_B}Smart Retail Platform — End-to-End Demo Workflow{_X}")
    print(f"Target: {BASE_URL}\n")

    print("Authenticating…")
    if not login(args.username, args.password):
        print(f"{_R}Login failed. Ensure the server is running and demo data is seeded.{_X}")
        print("Seed data: python demo_seed.py --reset --verify")
        sys.exit(1)
    print(f"{_G}✓ Authenticated as '{args.username}'{_X}\n")

    scenarios_to_run = ([args.scenario] if args.scenario
                        else list(SCENARIOS.keys()))

    for n in scenarios_to_run:
        label, fn = SCENARIOS[n]
        fn()
        if args.pause and n != scenarios_to_run[-1]:
            input(f"\n  {_Y}Press Enter to continue to Scenario {n + 1}…{_X}")

    print(f"\n{_B}{_G}{'═' * 62}{_X}")
    print(f"{_B}{_G}  Demo workflow complete.{_X}")
    print(f"{_G}  API Docs:   {BASE_URL}/docs{_X}")
    print(f"{_G}  Health:     {BASE_URL}/health/detailed{_X}")
    print(f"{_B}{_G}{'═' * 62}{_X}\n")


if __name__ == "__main__":
    main()
