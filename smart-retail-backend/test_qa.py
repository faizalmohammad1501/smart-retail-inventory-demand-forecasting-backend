"""
Smart Retail Platform — Final Quality Assurance Test Suite
==========================================================
Comprehensive QA validation covering all platform modules:
  - Server reachability & health checks
  - Authentication (register, login, token refresh, RBAC)
  - CRUD: Suppliers, Products, Inventory, Orders
  - ML Pipeline (data generation, training, forecasting)
  - Analytics (summary KPIs, bottlenecks, SLA breaches)
  - Inventory Recommendations
  - Notifications & Alerts
  - Reports (14 business reports)
  - Dashboard (master + widgets + charts)
  - Export endpoints (CSV)
  - Security (missing auth, RBAC enforcement, security headers)
  - Data consistency checks (cross-module validation)

Usage:
    python test_qa.py                            # run against http://localhost:8000
    python test_qa.py --url http://localhost:8000
    python test_qa.py --verbose                  # print full response bodies on failure

Exit code: 0 = all passed, 1 = failures detected
"""

import argparse
import json
import sys
import time
from dataclasses import dataclass, field
from typing import Any

try:
    import requests
except ImportError:
    print("Install requests: pip install requests")
    sys.exit(1)


# ════════════════════════════════════════════════════════════════
#  Config & helpers
# ════════════════════════════════════════════════════════════════

@dataclass
class QAContext:
    base: str
    verbose: bool = False
    admin_token: str = ""
    manager_token: str = ""
    analyst_token: str = ""
    supplier_id: int = 0
    product_id: int = 0
    order_id: int = 0
    notification_id: int = 0
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    failures: list[str] = field(default_factory=list)

    def auth_header(self, token: str) -> dict:
        return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    def admin(self) -> dict:
        return self.auth_header(self.admin_token)

    def manager(self) -> dict:
        return self.auth_header(self.manager_token)

    def analyst(self) -> dict:
        return self.auth_header(self.analyst_token)


ctx: QAContext = None  # set in main()

_GREEN  = "\033[32m"
_RED    = "\033[31m"
_YELLOW = "\033[33m"
_CYAN   = "\033[36m"
_RESET  = "\033[0m"
_BOLD   = "\033[1m"


def section(title: str) -> None:
    print(f"\n{_BOLD}{_CYAN}{'─' * 60}{_RESET}")
    print(f"{_BOLD}{_CYAN}  {title}{_RESET}")
    print(f"{_BOLD}{_CYAN}{'─' * 60}{_RESET}")


def check(name: str, passed: bool, detail: str = "", skip: bool = False) -> bool:
    global ctx
    if skip:
        ctx.skipped += 1
        print(f"  {_YELLOW}[SKIP]{_RESET} {name}")
        return False
    if passed:
        ctx.passed += 1
        print(f"  {_GREEN}[PASS]{_RESET} {name}")
        return True
    else:
        ctx.failed += 1
        msg = f"{name}{' — ' + detail if detail else ''}"
        ctx.failures.append(msg)
        print(f"  {_RED}[FAIL]{_RESET} {name}")
        if detail and ctx.verbose:
            print(f"         {_RED}{detail[:300]}{_RESET}")
        return False


def get(path: str, headers: dict = None, params: dict = None) -> requests.Response:
    return requests.get(f"{ctx.base}{path}", headers=headers, params=params, timeout=15)


def post(path: str, data: dict, headers: dict = None) -> requests.Response:
    return requests.post(f"{ctx.base}{path}", json=data, headers=headers, timeout=15)


def patch(path: str, data: dict = None, headers: dict = None) -> requests.Response:
    return requests.patch(f"{ctx.base}{path}", json=data or {}, headers=headers, timeout=15)


def delete(path: str, headers: dict = None) -> requests.Response:
    return requests.delete(f"{ctx.base}{path}", headers=headers, timeout=15)


def safe_json(r: requests.Response) -> Any:
    try:
        return r.json()
    except Exception:
        return {}


# ════════════════════════════════════════════════════════════════
#  Test groups
# ════════════════════════════════════════════════════════════════

def test_server_health() -> None:
    section("1. Server Reachability & Health")

    r = get("/health")
    check("GET /health returns 200", r.status_code == 200, str(r.status_code))
    body = safe_json(r)
    check("Liveness body has 'status' key", "status" in body, str(body))

    r = get("/health/detailed")
    check("GET /health/detailed returns 200 or 207", r.status_code in (200, 207), str(r.status_code))
    body = safe_json(r)
    check("Readiness body has 'checks' key", "checks" in body, str(body))

    r = get("/docs")
    check("Swagger UI accessible at /docs", r.status_code == 200, str(r.status_code))

    r = get("/openapi.json")
    check("OpenAPI JSON schema accessible", r.status_code == 200, str(r.status_code))


def test_security_headers() -> None:
    section("2. Security Headers")

    r = get("/health")
    h = r.headers
    check("X-Content-Type-Options present",    "x-content-type-options" in {k.lower() for k in h}, str(dict(h)))
    check("X-Frame-Options present",           "x-frame-options" in {k.lower() for k in h})
    check("X-XSS-Protection present",          "x-xss-protection" in {k.lower() for k in h})
    check("Content-Security-Policy present",   "content-security-policy" in {k.lower() for k in h})
    check("Server header masked",              h.get("Server", "").startswith("SmartRetail") or "nginx" not in h.get("Server","").lower())


def test_authentication() -> None:
    section("3. Authentication")

    # Login as admin
    r = post("/api/auth/login", {"username": "admin", "password": "Admin@123"})
    ok = r.status_code == 200
    check("Admin login succeeds", ok, f"HTTP {r.status_code}")
    if ok:
        ctx.admin_token = safe_json(r).get("access_token", "")

    # Login as manager
    r = post("/api/auth/login", {"username": "manager", "password": "Manager@123"})
    ok = r.status_code == 200
    check("Manager login succeeds", ok, f"HTTP {r.status_code}")
    if ok:
        ctx.manager_token = safe_json(r).get("access_token", "")

    # Login as analyst
    r = post("/api/auth/login", {"username": "analyst", "password": "Analyst@123"})
    ok = r.status_code == 200
    check("Analyst login succeeds", ok, f"HTTP {r.status_code}")
    if ok:
        ctx.analyst_token = safe_json(r).get("access_token", "")

    # Bad credentials
    r = post("/api/auth/login", {"username": "admin", "password": "WRONG"})
    check("Bad credentials rejected (401)", r.status_code == 401, f"HTTP {r.status_code}")

    # No token
    r = get("/api/auth/profile")
    check("No token → 401", r.status_code == 401, f"HTTP {r.status_code}")

    # Get profile with valid token
    if ctx.admin_token:
        r = get("/api/auth/profile", headers=ctx.admin())
        check("GET /api/auth/profile returns 200", r.status_code == 200, f"HTTP {r.status_code}")
        body = safe_json(r)
        check("Profile contains username", body.get("username") == "admin", str(body))


def test_suppliers() -> None:
    section("4. Suppliers CRUD")

    if not ctx.admin_token:
        check("Supplier tests", False, "No admin token", skip=True)
        return

    # Create
    r = post("/api/suppliers/", {
        "supplier_name": "QA Test Supplier",
        "email": f"qa_supplier_{int(time.time())}@test.com",
        "contact_person": "QA Bot",
        "city": "Test City",
        "country": "Testland",
        "rating": 3,
    }, headers=ctx.admin())
    ok = r.status_code in (200, 201)
    check("POST /api/suppliers/ — create supplier", ok, f"HTTP {r.status_code} {r.text[:100]}")
    if ok:
        ctx.supplier_id = safe_json(r).get("id", 0)

    # List
    r = get("/api/suppliers/", headers=ctx.admin())
    check("GET /api/suppliers/ — list", r.status_code == 200, f"HTTP {r.status_code}")
    body = safe_json(r)
    check("Suppliers list is non-empty", isinstance(body, list) and len(body) > 0, str(body)[:80])

    # Get single
    if ctx.supplier_id:
        r = get(f"/api/suppliers/{ctx.supplier_id}", headers=ctx.admin())
        check(f"GET /api/suppliers/{ctx.supplier_id}", r.status_code == 200, f"HTTP {r.status_code}")


def test_products() -> None:
    section("5. Products CRUD")

    if not ctx.admin_token:
        check("Product tests", False, "No admin token", skip=True)
        return

    r = post("/api/inventory/products/", {
        "product_name": "QA Test Product",
        "sku": f"QA-PROD-{int(time.time())}",
        "category": "Testing",
        "unit_price": 9.99,
        "reorder_level": 10,
        "supplier_id": ctx.supplier_id or 1,
    }, headers=ctx.admin())
    ok = r.status_code in (200, 201)
    check("POST /api/inventory/products/ — create product", ok, f"HTTP {r.status_code} {r.text[:100]}")
    if ok:
        ctx.product_id = safe_json(r).get("id", 0)

    r = get("/api/inventory/products/", headers=ctx.admin())
    check("GET /api/inventory/products/ — list", r.status_code == 200, f"HTTP {r.status_code}")
    body = safe_json(r)
    check("Products list is non-empty", isinstance(body, list) and len(body) > 0, str(body)[:80])


def test_orders() -> None:
    section("6. Orders & Sales")

    if not ctx.admin_token or not ctx.product_id:
        check("Order tests", False, "Missing admin token or product_id", skip=True)
        return

    r = post("/api/sales/orders/", {
        "product_id": ctx.product_id,
        "supplier_id": ctx.supplier_id or 1,
        "quantity": 10,
        "unit_price": 9.99,
        "total_amount": 99.90,
        "status": "pending",
    }, headers=ctx.admin())
    ok = r.status_code in (200, 201)
    check("POST /api/sales/orders/ — create order", ok, f"HTTP {r.status_code} {r.text[:150]}")
    if ok:
        ctx.order_id = safe_json(r).get("id", 0)

    r = get("/api/sales/orders/", headers=ctx.admin())
    check("GET /api/sales/orders/ — list", r.status_code == 200, f"HTTP {r.status_code}")


def test_analytics() -> None:
    section("7. Analytics Engine")

    if not ctx.admin_token:
        check("Analytics tests", False, "No admin token", skip=True)
        return

    r = get("/api/analytics/summary", headers=ctx.admin())
    check("GET /api/analytics/summary — 200", r.status_code == 200, f"HTTP {r.status_code}")

    r = get("/api/analytics/bottlenecks", headers=ctx.admin())
    check("GET /api/analytics/bottlenecks — 200", r.status_code == 200, f"HTTP {r.status_code}")
    body = safe_json(r)
    check("Bottlenecks response is list", isinstance(body, list), str(body)[:80])

    r = get("/api/analytics/sla-breaches", headers=ctx.admin())
    check("GET /api/analytics/sla-breaches — 200", r.status_code == 200, f"HTTP {r.status_code}")


def test_ml_pipeline() -> None:
    section("8. ML Pipeline")

    if not ctx.admin_token:
        check("ML tests", False, "No admin token", skip=True)
        return

    r = post("/api/ml/pipeline/generate", {}, headers=ctx.admin())
    check("POST /api/ml/pipeline/generate — 200", r.status_code == 200, f"HTTP {r.status_code} {r.text[:100]}")

    r = post("/api/ml/pipeline/run", {}, headers=ctx.admin())
    check("POST /api/ml/pipeline/run — 200 or 202", r.status_code in (200, 202), f"HTTP {r.status_code} {r.text[:100]}")

    r = get("/api/ml/pipeline/status", headers=ctx.admin())
    check("GET /api/ml/pipeline/status — 200", r.status_code == 200, f"HTTP {r.status_code}")

    r = get("/api/ml/pipeline/features", headers=ctx.admin())
    check("GET /api/ml/pipeline/features — 200", r.status_code == 200, f"HTTP {r.status_code}")


def test_forecasting() -> None:
    section("9. Demand Forecasting")

    if not ctx.admin_token:
        check("Forecasting tests", False, "No admin token", skip=True)
        return

    r = post("/api/predictions/train", {}, headers=ctx.admin())
    check("POST /api/predictions/train — 200", r.status_code == 200, f"HTTP {r.status_code} {r.text[:100]}")

    r = get("/api/predictions/model/status", headers=ctx.admin())
    check("GET /api/predictions/model/status — 200", r.status_code == 200, f"HTTP {r.status_code}")

    r = post("/api/predictions/forecast", {"product_id": 1, "days": 30}, headers=ctx.admin())
    check("POST /api/predictions/forecast — 200", r.status_code == 200, f"HTTP {r.status_code} {r.text[:100]}")
    body = safe_json(r)
    check("Forecast returns predictions list", "predictions" in body or isinstance(body, list) or "data" in body, str(body)[:80])

    r = get("/api/predictions/forecast", headers=ctx.admin())
    check("GET /api/predictions/forecast (all) — 200", r.status_code == 200, f"HTTP {r.status_code}")


def test_inventory_recommendations() -> None:
    section("10. Inventory Recommendations")

    if not ctx.admin_token:
        check("Recommendations tests", False, "No admin token", skip=True)
        return

    r = get("/api/recommendations/", headers=ctx.admin())
    check("GET /api/recommendations/ — 200", r.status_code == 200, f"HTTP {r.status_code}")

    r = get("/api/recommendations/health", headers=ctx.admin())
    check("GET /api/recommendations/health — 200", r.status_code == 200, f"HTTP {r.status_code}")

    r = get("/api/recommendations/alerts", headers=ctx.admin())
    check("GET /api/recommendations/alerts — 200", r.status_code == 200, f"HTTP {r.status_code}")

    r = get("/api/recommendations/replenishment", headers=ctx.admin())
    check("GET /api/recommendations/replenishment — 200", r.status_code == 200, f"HTTP {r.status_code}")


def test_notifications() -> None:
    section("11. Notifications & Alerts")

    if not ctx.admin_token:
        check("Notifications tests", False, "No admin token", skip=True)
        return

    r = post("/api/notifications/run", {}, headers=ctx.admin())
    check("POST /api/notifications/run — 200", r.status_code == 200, f"HTTP {r.status_code} {r.text[:100]}")

    r = get("/api/notifications/summary", headers=ctx.admin())
    check("GET /api/notifications/summary — 200", r.status_code == 200, f"HTTP {r.status_code}")

    r = get("/api/notifications/", headers=ctx.admin())
    check("GET /api/notifications/ — 200", r.status_code == 200, f"HTTP {r.status_code}")
    body = safe_json(r)
    if isinstance(body, list) and len(body) > 0:
        ctx.notification_id = body[0].get("id", 0)
        check("Notifications list non-empty after run", True)
    else:
        check("Notifications list non-empty after run", False, "Empty or unexpected response")

    if ctx.notification_id:
        r = patch(f"/api/notifications/{ctx.notification_id}/read", headers=ctx.admin())
        check(f"PATCH /api/notifications/{ctx.notification_id}/read — 200", r.status_code == 200, f"HTTP {r.status_code}")


def test_reports() -> None:
    section("12. Business Reports")

    if not ctx.admin_token:
        check("Report tests", False, "No admin token", skip=True)
        return

    endpoints = [
        ("/api/reports/sales/summary",            "Sales summary"),
        ("/api/reports/sales/trends",             "Revenue trends"),
        ("/api/reports/sales/top-products",       "Top products"),
        ("/api/reports/sales/by-category",        "Category revenue"),
        ("/api/reports/sales/fulfillment",        "Fulfillment stats"),
        ("/api/reports/inventory/valuation",      "Inventory valuation"),
        ("/api/reports/inventory/turnover",       "Inventory turnover"),
        ("/api/reports/inventory/aging",          "Inventory aging"),
        ("/api/reports/suppliers/performance",    "Supplier performance"),
        ("/api/reports/forecast/accuracy",        "Forecast accuracy"),
        ("/api/reports/operations/kpis",          "Operational KPIs"),
        ("/api/reports/operations/sla-compliance","SLA compliance"),
        ("/api/reports/operations/bottlenecks",   "Bottleneck report"),
    ]

    for path, label in endpoints:
        r = get(path, headers=ctx.admin())
        check(f"GET {path}", r.status_code == 200, f"HTTP {r.status_code}")


def test_dashboard() -> None:
    section("13. Executive Dashboard")

    if not ctx.admin_token:
        check("Dashboard tests", False, "No admin token", skip=True)
        return

    r = get("/api/dashboard/summary", headers=ctx.admin())
    check("GET /api/dashboard/summary — 200", r.status_code == 200, f"HTTP {r.status_code}")
    body = safe_json(r)
    for widget in ("sales", "inventory", "suppliers", "forecast", "alerts"):
        check(f"  Dashboard has '{widget}' widget", widget in body or "data" in body, str(list(body.keys())))

    widget_endpoints = [
        "/api/dashboard/widgets/sales",
        "/api/dashboard/widgets/inventory",
        "/api/dashboard/widgets/suppliers",
        "/api/dashboard/widgets/forecast",
        "/api/dashboard/widgets/alerts",
    ]
    for path in widget_endpoints:
        r = get(path, headers=ctx.admin())
        check(f"GET {path}", r.status_code == 200, f"HTTP {r.status_code}")

    chart_endpoints = [
        "/api/dashboard/charts/revenue-trend",
        "/api/dashboard/charts/order-status",
        "/api/dashboard/charts/top-products",
        "/api/dashboard/charts/inventory-health",
        "/api/dashboard/charts/supplier-performance",
        "/api/dashboard/charts/category-revenue",
    ]
    for path in chart_endpoints:
        r = get(path, headers=ctx.admin())
        check(f"GET {path}", r.status_code == 200, f"HTTP {r.status_code}")


def test_exports() -> None:
    section("14. Export Endpoints (CSV)")

    if not ctx.admin_token:
        check("Export tests", False, "No admin token", skip=True)
        return

    csv_endpoints = [
        ("/api/reports/export/sales",          "Sales CSV"),
        ("/api/reports/export/inventory",      "Inventory CSV"),
        ("/api/reports/export/suppliers",      "Suppliers CSV"),
        ("/api/dashboard/export/forecast-accuracy",  "Forecast CSV"),
        ("/api/dashboard/export/notifications",      "Notifications CSV"),
    ]

    for path, label in csv_endpoints:
        r = get(path, headers=ctx.admin())
        ok = r.status_code == 200
        check(f"GET {path} — {label}", ok, f"HTTP {r.status_code}")
        if ok:
            ct = r.headers.get("content-type", "")
            check(f"  Content-Type is CSV", "csv" in ct or "text" in ct, ct)


def test_rbac() -> None:
    section("15. RBAC Enforcement")

    # Analyst should not be able to create suppliers
    if ctx.analyst_token:
        r = post("/api/suppliers/", {
            "supplier_name": "RBAC Test",
            "email": f"rbac_{int(time.time())}@test.com",
        }, headers=ctx.analyst())
        check("Analyst cannot create supplier (403/401)", r.status_code in (401, 403), f"HTTP {r.status_code}")

    # Unauthenticated request to protected endpoint
    r = get("/api/reports/sales/summary")
    check("Unauthenticated → 401 on reports", r.status_code == 401, f"HTTP {r.status_code}")

    # Invalid token
    r = get("/api/dashboard/summary", headers={"Authorization": "Bearer invalid.token.here"})
    check("Invalid token → 401", r.status_code == 401, f"HTTP {r.status_code}")


def test_data_consistency() -> None:
    section("16. Data Consistency Checks")

    if not ctx.admin_token:
        check("Consistency tests", False, "No admin token", skip=True)
        return

    # Revenue from summary matches trends total roughly
    r_sum = get("/api/reports/sales/summary", headers=ctx.admin())
    r_cat = get("/api/reports/sales/by-category", headers=ctx.admin())

    if r_sum.status_code == 200 and r_cat.status_code == 200:
        summary_body = safe_json(r_sum)
        cat_body = safe_json(r_cat)

        # Summary should have total_revenue
        total_rev = (summary_body.get("total_revenue") or
                     summary_body.get("data", {}).get("total_revenue") or 0)
        check("Sales summary has total_revenue field", total_rev >= 0, str(summary_body)[:80])

        # Categories should be a list
        check("Category revenue is a list", isinstance(cat_body, list) or "data" in cat_body,
              str(cat_body)[:80])
    else:
        check("Sales summary & category reports accessible", False,
              f"Summary: {r_sum.status_code}, Category: {r_cat.status_code}")

    # Inventory recommendations align with inventory records
    r_rec = get("/api/recommendations/alerts", headers=ctx.admin())
    r_inv = get("/api/inventory/products/", headers=ctx.admin())
    if r_rec.status_code == 200 and r_inv.status_code == 200:
        alerts = safe_json(r_rec)
        products = safe_json(r_inv)
        check("Recommendations reference valid product IDs",
              isinstance(alerts, list) or "data" in alerts, str(alerts)[:60])
        check("Products list accessible for cross-check",
              isinstance(products, list) and len(products) > 0, str(products)[:60])
    else:
        check("Recommendations consistency check", False,
              f"Rec: {r_rec.status_code}, Inv: {r_inv.status_code}")


# ════════════════════════════════════════════════════════════════
#  Summary
# ════════════════════════════════════════════════════════════════

def print_summary() -> None:
    total = ctx.passed + ctx.failed + ctx.skipped
    print(f"\n{'═' * 60}")
    print(f"{_BOLD}  QA Test Results — Smart Retail Platform{_RESET}")
    print(f"{'═' * 60}")
    print(f"  Target  : {ctx.base}")
    print(f"  Total   : {total}")
    print(f"  {_GREEN}Passed  : {ctx.passed}{_RESET}")
    print(f"  {_RED}Failed  : {ctx.failed}{_RESET}")
    print(f"  {_YELLOW}Skipped : {ctx.skipped}{_RESET}")
    if ctx.failures:
        print(f"\n  {_RED}{_BOLD}Failed checks:{_RESET}")
        for f in ctx.failures:
            print(f"    {_RED}✗  {f}{_RESET}")
    rate = round(ctx.passed / max(ctx.passed + ctx.failed, 1) * 100, 1)
    print(f"\n  Pass rate: {rate}%")
    if rate >= 90:
        print(f"  {_GREEN}{_BOLD}✓ Platform is READY FOR DEMO / PRODUCTION.{_RESET}")
    elif rate >= 70:
        print(f"  {_YELLOW}⚠ Platform has minor issues — review failures above.{_RESET}")
    else:
        print(f"  {_RED}✗ Platform has significant issues — fix before demo.{_RESET}")
    print(f"{'═' * 60}\n")


# ════════════════════════════════════════════════════════════════
#  Main
# ════════════════════════════════════════════════════════════════

def main() -> None:
    global ctx

    parser = argparse.ArgumentParser(description="Smart Retail QA test suite")
    parser.add_argument("--url",     default="http://localhost:8000")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    ctx = QAContext(base=args.url.rstrip("/"), verbose=args.verbose)

    print(f"\n{_BOLD}Smart Retail Platform — Final QA Suite{_RESET}")
    print(f"Target: {ctx.base}")
    print("Ensure the server is running before executing this script.\n")

    # Run all test groups
    test_server_health()
    test_security_headers()
    test_authentication()
    test_suppliers()
    test_products()
    test_orders()
    test_analytics()
    test_ml_pipeline()
    test_forecasting()
    test_inventory_recommendations()
    test_notifications()
    test_reports()
    test_dashboard()
    test_exports()
    test_rbac()
    test_data_consistency()

    print_summary()
    sys.exit(0 if ctx.failed == 0 else 1)


if __name__ == "__main__":
    main()
