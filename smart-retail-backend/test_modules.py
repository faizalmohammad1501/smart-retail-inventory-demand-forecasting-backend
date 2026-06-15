#!/usr/bin/env python3
"""
Full Module Test Suite — Smart Retail Platform
================================================
Covers all modules added after the base auth/CRUD layer:

  • ML Pipeline          /api/ml/pipeline/
  • Demand Forecasting   /api/predictions/
  • Analytics            /api/analytics/
  • Recommendations      /api/recommendations/
  • Notifications        /api/notifications/
  • Reporting            /api/reports/
  • Dashboard & Exports  /api/dashboard/

Prerequisites:
  1. API server running at BASE_URL (default http://localhost:8000)
  2. A manager-role account already registered (or the script auto-registers one)

Run:
    python test_modules.py
"""

import sys
import json
import requests
from datetime import datetime, timedelta

BASE_URL = "http://localhost:8000"
TEST_USER = "moduletest"
TEST_PASS = "Module1234!"
TEST_EMAIL = "moduletest@test.com"

# ─────────────────────────────────────────────────────────────────────────────
#  Utilities
# ─────────────────────────────────────────────────────────────────────────────

RESULTS = []


def section(title: str):
    print(f"\n{'='*65}\n  {title}\n{'='*65}")


def ok(label: str):
    print(f"    ✅  {label}")


def fail(label: str, detail: str = ""):
    msg = f"    ❌  {label}"
    if detail:
        msg += f" — {detail}"
    print(msg)


def record(label: str, passed: bool):
    RESULTS.append((label, passed))
    return passed


def auth_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def expect_ok(r: requests.Response, label: str, expected: int = 200) -> bool:
    if r.status_code == expected:
        ok(f"{label} ({r.status_code})")
        return True
    fail(label, f"status={r.status_code} body={r.text[:120]}")
    return False


def expect_json_key(r: requests.Response, key: str, label: str) -> bool:
    try:
        data = r.json()
        if key in data:
            ok(f"{label} — '{key}' present")
            return True
        fail(label, f"'{key}' missing from response: {list(data.keys())[:10]}")
        return False
    except Exception as exc:
        fail(label, str(exc))
        return False


# ─────────────────────────────────────────────────────────────────────────────
#  Auth bootstrap
# ─────────────────────────────────────────────────────────────────────────────

def bootstrap_token() -> str | None:
    """Register (if needed) and login, return access token."""
    section("Auth Bootstrap")
    requests.post(
        f"{BASE_URL}/api/auth/register",
        json={
            "username": TEST_USER,
            "email": TEST_EMAIL,
            "full_name": "Module Tester",
            "password": TEST_PASS,
            "role": "manager",
        },
    )
    r = requests.post(
        f"{BASE_URL}/api/auth/login",
        json={"username": TEST_USER, "password": TEST_PASS},
    )
    if r.status_code == 200:
        token = r.json()["access_token"]
        ok(f"Logged in as '{TEST_USER}' — token obtained")
        return token
    fail("Login failed", r.text[:80])
    return None


# ─────────────────────────────────────────────────────────────────────────────
#  ML Pipeline Tests
# ─────────────────────────────────────────────────────────────────────────────

def test_ml_pipeline(token: str):
    section("ML Pipeline  /api/ml/pipeline/")
    h = auth_headers(token)
    passed = True

    # Pipeline config
    r = requests.get(f"{BASE_URL}/api/ml/pipeline/config", headers=h)
    passed &= record("GET /config", expect_ok(r, "Pipeline config"))

    # Pipeline status
    r = requests.get(f"{BASE_URL}/api/ml/pipeline/status", headers=h)
    passed &= record("GET /status", expect_ok(r, "Pipeline status"))

    # Generate synthetic data
    r = requests.post(f"{BASE_URL}/api/ml/pipeline/generate", headers=h)
    record("POST /generate (synthetic data)", expect_ok(r, "Generate synthetic"))

    # Run pipeline with synthetic data
    r = requests.post(
        f"{BASE_URL}/api/ml/pipeline/run",
        json={"use_synthetic": True, "scale": True, "save_artefacts": True},
        headers=h,
    )
    run_ok = expect_ok(r, "POST /run (pipeline)")
    record("POST /run", run_ok)
    if run_ok:
        data = r.json()
        ok(f"  success={data.get('success')} | "
           f"features={len(data.get('feature_columns') or [])}")

    # Features preview
    r = requests.get(f"{BASE_URL}/api/ml/pipeline/features", headers=h)
    record("GET /features", expect_ok(r, "Feature preview"))

    return passed


# ─────────────────────────────────────────────────────────────────────────────
#  Demand Forecasting / Prediction Tests
# ─────────────────────────────────────────────────────────────────────────────

def test_predictions(token: str):
    section("Demand Forecasting  /api/predictions/")
    h = auth_headers(token)

    # Model status (before training)
    r = requests.get(f"{BASE_URL}/api/predictions/model/status", headers=h)
    record("GET /model/status", expect_ok(r, "Model status"))

    # Train model
    r = requests.post(
        f"{BASE_URL}/api/predictions/train",
        json={},
        headers=h,
    )
    train_ok = expect_ok(r, "POST /train")
    record("POST /train", train_ok)
    if train_ok:
        data = r.json()
        ok(f"  success={data.get('success')} | n_features={data.get('n_features')}")

    # Batch forecast (all products)
    r = requests.post(
        f"{BASE_URL}/api/predictions/forecast",
        json={"horizon_days": 14},
        headers=h,
    )
    batch_ok = expect_ok(r, "POST /forecast (batch, 14 days)")
    record("POST /forecast (batch)", batch_ok)
    if batch_ok:
        data = r.json()
        ok(f"  total_products={data.get('total_products')} | "
           f"forecasted={data.get('forecasted')}")

    # All product forecasts list
    r = requests.get(f"{BASE_URL}/api/predictions/forecast", headers=h)
    record("GET /forecast (list)", expect_ok(r, "GET all forecasts"))

    return True


# ─────────────────────────────────────────────────────────────────────────────
#  Analytics Tests
# ─────────────────────────────────────────────────────────────────────────────

def test_analytics(token: str):
    section("Analytics  /api/analytics/")
    h = auth_headers(token)
    passed = True

    for path, label in [
        ("/api/analytics/summary", "Summary KPIs"),
        ("/api/analytics/bottlenecks", "Bottleneck distribution"),
        ("/api/analytics/sla-breaches", "SLA breach table"),
    ]:
        r = requests.get(f"{BASE_URL}{path}", headers=h)
        p = expect_ok(r, label)
        record(f"GET {path}", p)
        passed &= p

    return passed


# ─────────────────────────────────────────────────────────────────────────────
#  Inventory Recommendations Tests
# ─────────────────────────────────────────────────────────────────────────────

def test_recommendations(token: str):
    section("Inventory Recommendations  /api/recommendations/")
    h = auth_headers(token)
    passed = True

    for path, label in [
        ("/api/recommendations/", "All recommendations"),
        ("/api/recommendations/health", "Inventory health"),
        ("/api/recommendations/alerts", "Critical alerts"),
        ("/api/recommendations/replenishment", "Replenishment list"),
    ]:
        r = requests.get(f"{BASE_URL}{path}", headers=h)
        p = expect_ok(r, label)
        record(f"GET {path}", p)
        passed &= p

    return passed


# ─────────────────────────────────────────────────────────────────────────────
#  Notifications Tests
# ─────────────────────────────────────────────────────────────────────────────

def test_notifications(token: str):
    section("Notifications  /api/notifications/")
    h = auth_headers(token)
    passed = True

    # Run alert checks
    r = requests.post(f"{BASE_URL}/api/notifications/run", headers=h)
    run_ok = expect_ok(r, "POST /run (generate alerts)")
    record("POST /notifications/run", run_ok)
    if run_ok:
        data = r.json()
        ok(f"  total_created={data.get('total_created', 0)}")

    # Summary
    r = requests.get(f"{BASE_URL}/api/notifications/summary", headers=h)
    record("GET /summary", expect_ok(r, "Notification summary"))

    # List
    r = requests.get(f"{BASE_URL}/api/notifications/", headers=h)
    list_ok = expect_ok(r, "GET /notifications/ (list)")
    record("GET /notifications/", list_ok)

    notification_id = None
    if list_ok:
        data = r.json()
        notifications = data.get("notifications", [])
        ok(f"  total={data.get('total', 0)} active notifications")
        if notifications:
            notification_id = notifications[0]["id"]

    # Mark one as read
    if notification_id:
        r = requests.patch(
            f"{BASE_URL}/api/notifications/{notification_id}/read", headers=h
        )
        record(f"PATCH /{notification_id}/read",
               expect_ok(r, f"Mark notification {notification_id} read"))

    # Mark all read
    r = requests.patch(f"{BASE_URL}/api/notifications/read-all", headers=h)
    record("PATCH /read-all", expect_ok(r, "Mark all read"))

    return passed


# ─────────────────────────────────────────────────────────────────────────────
#  Reporting Tests
# ─────────────────────────────────────────────────────────────────────────────

def test_reports(token: str):
    section("Reporting  /api/reports/")
    h = auth_headers(token)
    passed = True

    report_endpoints = [
        ("/api/reports/sales/summary", "Sales summary"),
        ("/api/reports/sales/trends?granularity=monthly", "Revenue trends"),
        ("/api/reports/sales/top-products?top_n=5", "Top 5 products"),
        ("/api/reports/sales/by-category", "Sales by category"),
        ("/api/reports/sales/fulfillment", "Fulfillment stats"),
        ("/api/reports/inventory/valuation", "Inventory valuation"),
        ("/api/reports/inventory/turnover", "Inventory turnover"),
        ("/api/reports/inventory/aging", "Inventory aging"),
        ("/api/reports/suppliers/performance", "Supplier performance"),
        ("/api/reports/forecast/accuracy", "Forecast accuracy"),
        ("/api/reports/operations/kpis", "Operational KPIs"),
        ("/api/reports/operations/sla-compliance", "SLA compliance"),
        ("/api/reports/operations/bottlenecks", "Bottleneck report"),
    ]

    for path, label in report_endpoints:
        r = requests.get(f"{BASE_URL}{path}", headers=h)
        p = expect_ok(r, label)
        record(f"GET {path.split('?')[0]}", p)
        passed &= p

    # CSV export endpoints
    section("Reports — CSV Exports")
    csv_endpoints = [
        ("/api/reports/export/sales", "Export sales CSV"),
        ("/api/reports/export/inventory", "Export inventory CSV"),
        ("/api/reports/export/suppliers", "Export suppliers CSV"),
    ]
    for path, label in csv_endpoints:
        r = requests.get(f"{BASE_URL}{path}", headers=h)
        p = r.status_code == 200 and "text/csv" in r.headers.get("content-type", "")
        record(f"GET {path} (CSV)", p)
        if p:
            lines = r.text.strip().split("\n")
            ok(f"{label} — {len(lines)} rows (incl. header)")
        else:
            fail(label, f"status={r.status_code} ct={r.headers.get('content-type')}")

    return passed


# ─────────────────────────────────────────────────────────────────────────────
#  Dashboard & Export Tests
# ─────────────────────────────────────────────────────────────────────────────

def test_dashboard(token: str):
    section("Dashboard  /api/dashboard/")
    h = auth_headers(token)
    passed = True

    # Master summary
    r = requests.get(f"{BASE_URL}/api/dashboard/summary?days=30", headers=h)
    summary_ok = expect_ok(r, "GET /summary (master dashboard)")
    record("GET /dashboard/summary", summary_ok)
    if summary_ok:
        data = r.json()
        exec_s = data.get("executive_summary", {})
        ok(f"  revenue=${exec_s.get('total_revenue', 0):,.2f} | "
           f"orders={exec_s.get('total_orders', 0)} | "
           f"fulfillment={exec_s.get('fulfillment_rate', 0):.1f}%")
        ok(f"  widgets: {list(data.get('widgets', {}).keys())}")
        ok(f"  charts:  {list(data.get('charts', {}).keys())}")

    # Widget endpoints
    section("Dashboard — Individual Widgets")
    widget_endpoints = [
        ("/api/dashboard/widgets/sales?days=30", "Sales widget"),
        ("/api/dashboard/widgets/inventory", "Inventory widget"),
        ("/api/dashboard/widgets/suppliers?days=30", "Supplier widget"),
        ("/api/dashboard/widgets/forecast?days=30", "Forecast widget"),
        ("/api/dashboard/widgets/alerts", "Alerts widget"),
    ]
    for path, label in widget_endpoints:
        r = requests.get(f"{BASE_URL}{path}", headers=h)
        p = expect_ok(r, label)
        record(f"GET {path.split('?')[0]}", p)
        passed &= p

    # Chart endpoints
    section("Dashboard — Chart Data")
    chart_endpoints = [
        ("/api/dashboard/charts/revenue-trend?days=30&granularity=daily", "Revenue trend chart"),
        ("/api/dashboard/charts/order-status?days=30", "Order status chart"),
        ("/api/dashboard/charts/top-products?top_n=5", "Top products chart"),
        ("/api/dashboard/charts/inventory-health", "Inventory health chart"),
        ("/api/dashboard/charts/supplier-performance?top_n=5", "Supplier performance chart"),
        ("/api/dashboard/charts/category-revenue?days=30", "Category revenue chart"),
    ]
    for path, label in chart_endpoints:
        r = requests.get(f"{BASE_URL}{path}", headers=h)
        p = expect_ok(r, label)
        record(f"GET {path.split('?')[0]}", p)
        passed &= p
        if p:
            data = r.json()
            ok(f"  chart_type={data.get('chart_type')} | "
               f"labels={len(data.get('labels', []))} | "
               f"datasets={len(data.get('datasets', []))}")

    # CSV export endpoints
    section("Dashboard — CSV Exports")
    csv_endpoints = [
        ("/api/dashboard/export/csv/forecast-accuracy?days=30", "Forecast accuracy CSV"),
        ("/api/dashboard/export/csv/notifications", "Notifications CSV"),
        ("/api/dashboard/export/csv/full-report?days=30", "Full report CSV"),
    ]
    for path, label in csv_endpoints:
        r = requests.get(f"{BASE_URL}{path}", headers=h)
        p = r.status_code == 200 and "text/csv" in r.headers.get("content-type", "")
        record(f"GET {path.split('?')[0]} (CSV)", p)
        if p:
            lines = r.text.strip().split("\n")
            ok(f"{label} — {len(lines)} rows (incl. header)")
        else:
            fail(label, f"status={r.status_code} ct={r.headers.get('content-type')}")

    # PDF export endpoints
    section("Dashboard — PDF Exports")
    pdf_endpoints = [
        ("/api/dashboard/export/pdf/sales?days=30", "Sales PDF"),
        ("/api/dashboard/export/pdf/inventory", "Inventory PDF"),
        ("/api/dashboard/export/pdf/suppliers?days=30", "Supplier PDF"),
        ("/api/dashboard/export/pdf/executive?days=30", "Executive PDF"),
    ]
    for path, label in pdf_endpoints:
        r = requests.get(f"{BASE_URL}{path}", headers=h)
        if r.status_code == 200:
            p = "application/pdf" in r.headers.get("content-type", "")
            record(f"GET {path.split('?')[0]} (PDF)", p)
            ok(f"{label} — {len(r.content):,} bytes") if p else fail(label, "wrong content-type")
        elif r.status_code == 501:
            # fpdf2 not installed — expected, not a failure
            record(f"GET {path.split('?')[0]} (PDF)", True)
            ok(f"{label} — 501 (fpdf2 not installed, expected)")
        else:
            record(f"GET {path.split('?')[0]} (PDF)", False)
            fail(label, f"status={r.status_code}")

    return passed


# ─────────────────────────────────────────────────────────────────────────────
#  Auth guards (no token → 401/403)
# ─────────────────────────────────────────────────────────────────────────────

def test_auth_guards():
    section("Auth Guards — All modules reject unauthenticated requests")
    protected = [
        "/api/analytics/summary",
        "/api/recommendations/",
        "/api/notifications/",
        "/api/reports/sales/summary",
        "/api/dashboard/summary",
    ]
    passed = True
    for path in protected:
        r = requests.get(f"{BASE_URL}{path}")
        p = r.status_code in (401, 403)
        record(f"No-token guard {path}", p)
        if p:
            ok(f"{path} → {r.status_code}")
        else:
            fail(path, f"Expected 401/403, got {r.status_code}")
            passed = False
    return passed


# ─────────────────────────────────────────────────────────────────────────────
#  Filtered queries
# ─────────────────────────────────────────────────────────────────────────────

def test_filters(token: str):
    section("Filter Parameters — Date range, category, granularity")
    h = auth_headers(token)

    filter_cases = [
        (
            "/api/reports/sales/summary?start_date=2024-01-01&end_date=2024-12-31",
            "Sales summary with date range",
        ),
        (
            "/api/reports/sales/trends?granularity=daily&start_date=2024-01-01&end_date=2024-03-31",
            "Daily revenue trend with date range",
        ),
        (
            "/api/reports/sales/top-products?top_n=3&sort_by=units",
            "Top 3 products sorted by units",
        ),
        (
            "/api/reports/inventory/aging?stale_days=60",
            "Inventory aging with 60-day stale threshold",
        ),
        (
            "/api/dashboard/charts/revenue-trend?days=7&granularity=daily",
            "7-day daily revenue chart",
        ),
        (
            "/api/dashboard/widgets/sales?days=7",
            "Sales widget — last 7 days",
        ),
    ]

    passed = True
    for url, label in filter_cases:
        r = requests.get(f"{BASE_URL}{url}", headers=h)
        p = expect_ok(r, label)
        record(f"Filter: {label}", p)
        passed &= p

    return passed


# ─────────────────────────────────────────────────────────────────────────────
#  Main runner
# ─────────────────────────────────────────────────────────────────────────────

def main():
    print("\n" + "=" * 65)
    print("  Smart Retail Platform — Full Module Test Suite")
    print("=" * 65)

    # Health check
    r = requests.get(f"{BASE_URL}/health")
    if r.status_code != 200:
        print(f"\n❌  Server not reachable at {BASE_URL}. Start with: uvicorn main:app --reload")
        sys.exit(1)
    ok(f"Server reachable — {r.json().get('status')}")

    # Auth
    token = bootstrap_token()
    if not token:
        print("\n❌  Cannot obtain auth token. Aborting.")
        sys.exit(1)

    # Run all module tests
    test_auth_guards()
    test_ml_pipeline(token)
    test_predictions(token)
    test_analytics(token)
    test_recommendations(token)
    test_notifications(token)
    test_reports(token)
    test_dashboard(token)
    test_filters(token)

    # ── Summary ──────────────────────────────────────────────────────────────
    section("FULL TEST SUMMARY")
    passed_list = [(l, r) for l, r in RESULTS if r]
    failed_list = [(l, r) for l, r in RESULTS if not r]

    for label, result in RESULTS:
        icon = "✅" if result else "❌"
        print(f"  {icon}  {label}")

    total = len(RESULTS)
    n_pass = len(passed_list)
    n_fail = len(failed_list)

    print(f"\n  {'─'*55}")
    print(f"  Passed : {n_pass}/{total}")
    print(f"  Failed : {n_fail}/{total}")

    if n_fail == 0:
        print("\n  🎉  All tests passed!")
    else:
        print(f"\n  ⚠️   {n_fail} test(s) failed:")
        for label, _ in failed_list:
            print(f"       • {label}")

    print(f"\n  Swagger UI : {BASE_URL}/docs")
    print(f"  ReDoc      : {BASE_URL}/redoc\n")

    sys.exit(0 if n_fail == 0 else 1)


if __name__ == "__main__":
    main()
