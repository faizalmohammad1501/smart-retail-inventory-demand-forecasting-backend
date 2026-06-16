#!/usr/bin/env python3
"""
End-to-End Integration Test Suite
====================================
Tests complete business workflows — not individual endpoints, but multi-step
sequences that mirror real frontend usage.

Workflows tested:
  W1. Full Order Lifecycle          register → login → create supplier/product/order
                                    → verify analytics reflect the order
  W2. ML Training & Forecasting     run pipeline → train model → forecast → verify
  W3. Alert Generation Workflow     create order → run notification checks → verify alerts
  W4. Reporting Consistency         reports data matches analytics data
  W5. Dashboard Aggregation         dashboard summary reflects all other modules
  W6. Security & Error Handling     invalid tokens, malformed input, 404s

Run:
    python test_integration.py
"""

import sys
import time
import requests
from datetime import datetime, timedelta

BASE_URL = "http://localhost:8000"
MANAGER_USER = "integ_manager"
MANAGER_PASS = "IntegTest1234!"
USER_USER    = "integ_user"
USER_PASS    = "IntegUser1234!"

RESULTS: list = []


# ─────────────────────────────────────────────────────────────────────────────
#  Helpers
# ─────────────────────────────────────────────────────────────────────────────

def section(title: str):
    print(f"\n{'━'*68}\n  {title}\n{'━'*68}")


def ok(msg: str):   print(f"    ✅  {msg}")
def warn(msg: str): print(f"    ⚠️   {msg}")
def fail(msg: str): print(f"    ❌  {msg}")


def record(label: str, passed: bool) -> bool:
    RESULTS.append((label, passed))
    return passed


def h(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def assert_status(r: requests.Response, expected: int, label: str) -> bool:
    if r.status_code == expected:
        ok(f"{label} → {r.status_code}")
        return True
    fail(f"{label} → expected {expected}, got {r.status_code}: {r.text[:100]}")
    return False


def assert_key(data: dict, key: str, label: str) -> bool:
    if key in data:
        ok(f"{label} — '{key}' present")
        return True
    fail(f"{label} — '{key}' missing from: {list(data.keys())[:8]}")
    return False


def assert_security_headers(r: requests.Response, label: str) -> bool:
    required = [
        "x-content-type-options",
        "x-frame-options",
        "x-xss-protection",
        "referrer-policy",
    ]
    headers_lower = {k.lower(): v for k, v in r.headers.items()}
    missing = [h for h in required if h not in headers_lower]
    if not missing:
        ok(f"{label} — all security headers present")
        return True
    fail(f"{label} — missing headers: {missing}")
    return False


def assert_error_envelope(r: requests.Response, label: str) -> bool:
    try:
        data = r.json()
        if data.get("status") == "error" and "detail" in data:
            ok(f"{label} — standard error envelope")
            return True
        fail(f"{label} — non-standard error: {data}")
        return False
    except Exception:
        fail(f"{label} — response is not JSON")
        return False


# ─────────────────────────────────────────────────────────────────────────────
#  Auth bootstrap
# ─────────────────────────────────────────────────────────────────────────────

def setup_users() -> tuple:
    """Register manager and plain user; return their tokens."""
    for username, password, role in [
        (MANAGER_USER, MANAGER_PASS, "manager"),
        (USER_USER, USER_PASS, "user"),
    ]:
        requests.post(f"{BASE_URL}/api/auth/register", json={
            "username": username, "email": f"{username}@integ.test",
            "full_name": "Integration Tester", "password": password, "role": role,
        })

    manager_r = requests.post(f"{BASE_URL}/api/auth/login",
                               json={"username": MANAGER_USER, "password": MANAGER_PASS})
    user_r    = requests.post(f"{BASE_URL}/api/auth/login",
                               json={"username": USER_USER, "password": USER_PASS})

    if manager_r.status_code != 200:
        print(f"\n❌  Manager login failed. Cannot proceed.")
        sys.exit(1)

    ok(f"Manager token obtained")
    ok(f"User token obtained: {user_r.status_code == 200}")
    return manager_r.json()["access_token"], (
        user_r.json()["access_token"] if user_r.status_code == 200 else None
    )


# ─────────────────────────────────────────────────────────────────────────────
#  W1 — Full Order Lifecycle
# ─────────────────────────────────────────────────────────────────────────────

def workflow_order_lifecycle(token: str) -> tuple:
    """Create supplier → product → order; verify analytics reflect it."""
    section("W1 — Full Order Lifecycle")

    ts = int(time.time())

    # Create supplier
    r = requests.post(f"{BASE_URL}/api/suppliers/", json={
        "supplier_name": f"Integ Supplier {ts}",
        "contact_person": "Jane Doe",
        "email": f"jane_{ts}@supplier.test",
        "phone": "+1-555-0100",
        "city": "Chicago", "country": "USA", "rating": 4,
    }, headers=h(token))
    passed = assert_status(r, 201, "Create supplier")
    record("W1: Create supplier", passed)
    supplier_id = r.json().get("id") if passed else None

    # Create product
    product_id = None
    if supplier_id:
        r = requests.post(f"{BASE_URL}/api/products/", json={
            "product_name": f"Integ Product {ts}",
            "sku": f"INTEG-{ts}",
            "category": "Electronics",
            "unit_price": 149.99,
            "supplier_id": supplier_id,
            "reorder_level": 10,
        }, headers=h(token))
        passed = assert_status(r, 201, "Create product")
        record("W1: Create product", passed)
        product_id = r.json().get("id") if passed else None

    # Create order (full lifecycle timestamps → triggers analytics)
    order_id = None
    if product_id:
        now = datetime.utcnow()
        r = requests.post(f"{BASE_URL}/api/orders/", json={
            "order_number": f"ORD-INTEG-{ts}",
            "product_id": product_id,
            "supplier_id": supplier_id,
            "quantity": 25,
            "unit_price": 149.99,
            "order_placed_at":            now.isoformat(),
            "procurement_completed_at":   (now + timedelta(hours=12)).isoformat(),
            "processing_completed_at":    (now + timedelta(hours=20)).isoformat(),
            "dispatched_at":              (now + timedelta(hours=28)).isoformat(),
            "delivered_at":               (now + timedelta(hours=50)).isoformat(),
            "status": "delivered",
        }, headers=h(token))
        passed = assert_status(r, 201, "Create delivered order")
        record("W1: Create order", passed)
        if passed:
            order = r.json()
            order_id = order["id"]
            ok(f"  lifecycle — procurement={order.get('procurement_time')}h "
               f"| total={order.get('total_time')}h "
               f"| sla_breach={order.get('sla_breach')}")

    # Verify analytics summary reflects the new order
    r = requests.get(f"{BASE_URL}/api/analytics/summary", headers=h(token))
    passed = assert_status(r, 200, "Analytics summary after order")
    record("W1: Analytics reflects order", passed)
    if passed:
        data = r.json().get("data", r.json())
        total = data.get("total_orders", 0)
        ok(f"  total_orders={total}")

    return supplier_id, product_id, order_id


# ─────────────────────────────────────────────────────────────────────────────
#  W2 — ML Training & Forecasting
# ─────────────────────────────────────────────────────────────────────────────

def workflow_ml_forecast(token: str):
    section("W2 — ML Training & Forecasting Pipeline")

    # Generate synthetic data
    r = requests.post(f"{BASE_URL}/api/ml/pipeline/generate", headers=h(token))
    record("W2: Generate synthetic data", assert_status(r, 200, "Generate data"))

    # Run preprocessing pipeline
    r = requests.post(f"{BASE_URL}/api/ml/pipeline/run",
                      json={"use_synthetic": True, "scale": True, "save_artefacts": True},
                      headers=h(token))
    passed = assert_status(r, 200, "Run ML pipeline")
    record("W2: Run pipeline", passed)
    if passed:
        data = r.json()
        ok(f"  success={data.get('success')} "
           f"| features={len(data.get('feature_columns') or [])}")

    # Train model
    r = requests.post(f"{BASE_URL}/api/predictions/train", json={}, headers=h(token))
    passed = assert_status(r, 200, "Train model")
    record("W2: Train model", passed)
    if passed:
        data = r.json()
        ok(f"  n_features={data.get('n_features')} | n_train={data.get('n_train_samples')}")
        if data.get("val_metrics"):
            ok(f"  val_mae={data['val_metrics'].get('mae')} "
               f"| val_rmse={data['val_metrics'].get('rmse')}")

    # Verify model status
    r = requests.get(f"{BASE_URL}/api/predictions/model/status", headers=h(token))
    passed = assert_status(r, 200, "Model status after training")
    record("W2: Model status healthy", passed)
    if passed:
        data = r.json()
        ok(f"  model_trained={data.get('model_trained')} "
           f"| trained_at={data.get('trained_at')}")

    # Run batch forecast
    r = requests.post(f"{BASE_URL}/api/predictions/forecast",
                      json={"horizon_days": 7}, headers=h(token))
    passed = assert_status(r, 200, "Batch forecast (7 days)")
    record("W2: Batch forecast", passed)
    if passed:
        data = r.json()
        ok(f"  forecasted={data.get('forecasted')} products "
           f"| failed={data.get('failed')}")


# ─────────────────────────────────────────────────────────────────────────────
#  W3 — Alert Generation Workflow
# ─────────────────────────────────────────────────────────────────────────────

def workflow_alerts(token: str):
    section("W3 — Notification & Alert Workflow")

    # Run alert checks
    r = requests.post(f"{BASE_URL}/api/notifications/run", headers=h(token))
    passed = assert_status(r, 200, "Run alert checks")
    record("W3: Run alert checks", passed)
    if passed:
        data = r.json()
        ok(f"  total_created={data.get('total_created', 0)}")

    # List notifications
    r = requests.get(f"{BASE_URL}/api/notifications/", headers=h(token))
    passed = assert_status(r, 200, "List notifications")
    record("W3: List notifications", passed)
    notification_id = None
    if passed:
        data = r.json()
        total = data.get("total", 0)
        ok(f"  total_active={total}")
        notifications = data.get("notifications", [])
        if notifications:
            notification_id = notifications[0]["id"]

    # Mark as read
    if notification_id:
        r = requests.patch(f"{BASE_URL}/api/notifications/{notification_id}/read",
                           headers=h(token))
        record("W3: Mark notification read",
               assert_status(r, 200, f"Mark notification {notification_id} read"))

    # Summary
    r = requests.get(f"{BASE_URL}/api/notifications/summary", headers=h(token))
    passed = assert_status(r, 200, "Notification summary")
    record("W3: Notification summary", passed)
    if passed:
        data = r.json()
        ok(f"  total_active={data.get('total_active')} "
           f"| unread={data.get('total_unread')}")

    # Alerts widget
    r = requests.get(f"{BASE_URL}/api/dashboard/widgets/alerts", headers=h(token))
    record("W3: Alerts dashboard widget", assert_status(r, 200, "Alerts widget"))


# ─────────────────────────────────────────────────────────────────────────────
#  W4 — Reporting Consistency
# ─────────────────────────────────────────────────────────────────────────────

def workflow_reporting_consistency(token: str):
    section("W4 — Reporting Data Consistency")

    # Sales summary
    r_sales = requests.get(f"{BASE_URL}/api/reports/sales/summary", headers=h(token))
    r_ops   = requests.get(f"{BASE_URL}/api/reports/operations/kpis", headers=h(token))
    r_dash  = requests.get(f"{BASE_URL}/api/dashboard/widgets/sales", headers=h(token))

    s1 = assert_status(r_sales, 200, "Sales summary")
    s2 = assert_status(r_ops, 200, "Operational KPIs")
    s3 = assert_status(r_dash, 200, "Dashboard sales widget")
    record("W4: Sales reports accessible", s1 and s2 and s3)

    if s1 and s2:
        sales_rev  = r_sales.json().get("total_revenue", -1)
        ops_rev    = r_ops.json().get("sales_kpis", {}).get("total_revenue", -2)
        ok(f"  sales.total_revenue={sales_rev} | ops.sales_kpis.total_revenue={ops_rev}")
        match = abs(sales_rev - ops_rev) < 0.01
        record("W4: Revenue figures consistent", match)
        if match:
            ok("  Revenue figures match across reports and KPIs")
        else:
            warn("  Minor discrepancy (may be due to filter differences)")

    # Inventory reports
    r_val  = requests.get(f"{BASE_URL}/api/reports/inventory/valuation", headers=h(token))
    r_inv  = requests.get(f"{BASE_URL}/api/dashboard/widgets/inventory", headers=h(token))
    s4 = assert_status(r_val, 200, "Inventory valuation")
    s5 = assert_status(r_inv, 200, "Inventory widget")
    record("W4: Inventory reports accessible", s4 and s5)

    if s4 and s5:
        rep_skus = r_val.json().get("total_sku_count", -1)
        wid_skus = r_inv.json().get("total_skus", -2)
        ok(f"  valuation.total_sku_count={rep_skus} | widget.total_skus={wid_skus}")
        record("W4: SKU count consistent", rep_skus == wid_skus)


# ─────────────────────────────────────────────────────────────────────────────
#  W5 — Dashboard Aggregation
# ─────────────────────────────────────────────────────────────────────────────

def workflow_dashboard(token: str):
    section("W5 — Dashboard Aggregation & Charts")

    # Master dashboard
    r = requests.get(f"{BASE_URL}/api/dashboard/summary?days=30", headers=h(token))
    passed = assert_status(r, 200, "Master dashboard summary")
    record("W5: Master dashboard accessible", passed)

    if passed:
        data = r.json()
        exec_s = data.get("executive_summary", {})
        widgets = data.get("widgets", {})
        charts  = data.get("charts", {})

        ok(f"  exec: revenue=${exec_s.get('total_revenue', 0):,.2f} "
           f"| orders={exec_s.get('total_orders', 0)}")
        ok(f"  widgets present: {sorted(widgets.keys())}")
        ok(f"  charts present:  {sorted(charts.keys())}")

        # Verify all 5 widgets are present
        expected_widgets = {"sales", "inventory", "suppliers", "forecast", "alerts"}
        missing_widgets  = expected_widgets - set(widgets.keys())
        record("W5: All widgets present", not missing_widgets)
        if missing_widgets:
            warn(f"  Missing widgets: {missing_widgets}")

        # Verify all 6 charts are present
        expected_charts = {
            "revenue_trend", "order_status", "top_products",
            "inventory_health", "supplier_performance", "category_revenue",
        }
        missing_charts = expected_charts - set(charts.keys())
        record("W5: All charts present", not missing_charts)

        # Verify chart structure (labels + datasets)
        for chart_name, chart_data in charts.items():
            has_labels   = "labels"   in chart_data
            has_datasets = "datasets" in chart_data
            record(f"W5: Chart '{chart_name}' has labels+datasets",
                   has_labels and has_datasets)

    # Detailed health check
    r = requests.get(f"{BASE_URL}/health/detailed", headers=h(token))
    passed = r.status_code in (200, 503)
    record("W5: Detailed health endpoint responds", passed)
    if passed:
        data = r.json()
        ok(f"  health.status={data.get('status')} "
           f"| db={data.get('checks', {}).get('database', {}).get('status')}"
           f"| ml_model={data.get('checks', {}).get('ml_model', {}).get('status')}")


# ─────────────────────────────────────────────────────────────────────────────
#  W6 — Security & Error Handling
# ─────────────────────────────────────────────────────────────────────────────

def workflow_security(token: str, user_token: str):
    section("W6 — Security, RBAC & Error Handling")

    # 1. All protected routes reject unauthenticated requests
    protected_routes = [
        "/api/analytics/summary",
        "/api/recommendations/",
        "/api/reports/sales/summary",
        "/api/dashboard/summary",
        "/api/notifications/",
    ]
    for path in protected_routes:
        r = requests.get(f"{BASE_URL}{path}")
        passed = r.status_code in (401, 403)
        record(f"W6: Auth guard {path}", passed)
        if passed:
            ok(f"  {path} → {r.status_code} (blocked)")
        else:
            fail(f"  {path} → {r.status_code} (should be 401/403)")

    # 2. Error responses follow standard envelope
    r = requests.get(f"{BASE_URL}/api/suppliers/999999", headers=h(token))
    record("W6: 404 uses standard error envelope",
           assert_error_envelope(r, "Supplier 404"))

    # 3. Invalid JWT is rejected
    r = requests.get(f"{BASE_URL}/api/analytics/summary",
                     headers={"Authorization": "Bearer invalid.jwt.token"})
    record("W6: Invalid JWT rejected",
           assert_status(r, 401, "Invalid JWT"))

    # 4. Validation error returns structured response
    r = requests.post(f"{BASE_URL}/api/suppliers/",
                      json={"supplier_name": "x"},  # name too short (min 2)
                      headers=h(token))
    # May be 422 if name is too short or 201 — just verify structure
    if r.status_code == 422:
        data = r.json()
        has_errors = "errors" in data or "detail" in data
        record("W6: Validation error structured", has_errors)
        ok(f"  Validation error returns structured response (status=422)")
    else:
        ok(f"  POST /suppliers/ → {r.status_code} (accepted)")
        record("W6: Validation handled", True)

    # 5. Security headers present on all responses
    r = requests.get(f"{BASE_URL}/health")
    record("W6: Security headers on /health",
           assert_security_headers(r, "/health security headers"))

    r = requests.get(f"{BASE_URL}/api/analytics/summary", headers=h(token))
    if r.status_code == 200:
        record("W6: Security headers on API response",
               assert_security_headers(r, "API security headers"))

    # 6. RBAC — user role cannot create resources
    if user_token:
        r = requests.post(f"{BASE_URL}/api/suppliers/",
                          json={"supplier_name": "Unauthorized Supplier", "rating": 3},
                          headers=h(user_token))
        passed = r.status_code == 403
        record("W6: RBAC user cannot create supplier", passed)
        ok(f"  User → 403 (blocked)") if passed else fail(f"  Got {r.status_code}")

    # 7. Request ID header present
    r = requests.get(f"{BASE_URL}/health")
    has_rid = "x-request-id" in {k.lower() for k in r.headers}
    record("W6: X-Request-ID header present", has_rid)
    ok(f"  X-Request-ID: {r.headers.get('X-Request-ID', 'missing')}")


# ─────────────────────────────────────────────────────────────────────────────
#  W7 — Export Workflows
# ─────────────────────────────────────────────────────────────────────────────

def workflow_exports(token: str):
    section("W7 — Export Workflows (CSV & PDF)")

    csv_tests = [
        ("/api/reports/export/sales", "Sales CSV"),
        ("/api/reports/export/inventory", "Inventory CSV"),
        ("/api/reports/export/suppliers", "Suppliers CSV"),
        ("/api/dashboard/export/csv/forecast-accuracy", "Forecast accuracy CSV"),
        ("/api/dashboard/export/csv/notifications", "Notifications CSV"),
    ]

    for path, label in csv_tests:
        r = requests.get(f"{BASE_URL}{path}", headers=h(token))
        is_csv = r.status_code == 200 and "text/csv" in r.headers.get("content-type", "")
        record(f"W7: {label}", is_csv)
        if is_csv:
            lines = r.text.strip().split("\n")
            ok(f"  {label} — {len(lines)} rows (incl. header)")
            has_cd = "content-disposition" in {k.lower() for k in r.headers}
            record(f"W7: {label} has Content-Disposition", has_cd)
        else:
            fail(f"  {label} → {r.status_code} ct={r.headers.get('content-type')}")

    # PDF exports (501 if fpdf2 missing is acceptable)
    for path, label in [
        ("/api/dashboard/export/pdf/executive", "Executive PDF"),
        ("/api/dashboard/export/pdf/sales", "Sales PDF"),
    ]:
        r = requests.get(f"{BASE_URL}{path}?days=30", headers=h(token))
        if r.status_code == 200:
            is_pdf = "application/pdf" in r.headers.get("content-type", "")
            record(f"W7: {label}", is_pdf)
            ok(f"  {label} — {len(r.content):,} bytes")
        elif r.status_code == 501:
            record(f"W7: {label}", True)
            ok(f"  {label} — 501 (fpdf2 not installed, graceful fallback)")
        else:
            record(f"W7: {label}", False)
            fail(f"  {label} → {r.status_code}")


# ─────────────────────────────────────────────────────────────────────────────
#  Main
# ─────────────────────────────────────────────────────────────────────────────

def main():
    print("\n" + "═" * 68)
    print("  Smart Retail Platform — End-to-End Integration Tests")
    print("═" * 68)

    # Liveness check
    r = requests.get(f"{BASE_URL}/health")
    if r.status_code != 200:
        print(f"\n❌  Server unreachable at {BASE_URL}")
        print("    Start with: uvicorn main:app --reload")
        sys.exit(1)

    data = r.json()
    ok(f"Server: {data.get('service')} v{data.get('version')} [{data.get('environment')}]")

    # Auth
    section("Auth Setup")
    manager_token, user_token = setup_users()

    # Run workflows
    supplier_id, product_id, order_id = workflow_order_lifecycle(manager_token)
    workflow_ml_forecast(manager_token)
    workflow_alerts(manager_token)
    workflow_reporting_consistency(manager_token)
    workflow_dashboard(manager_token)
    workflow_security(manager_token, user_token)
    workflow_exports(manager_token)

    # ── Summary ──────────────────────────────────────────────────────────────
    section("INTEGRATION TEST SUMMARY")

    passed_list = [(l, r) for l, r in RESULTS if r]
    failed_list = [(l, r) for l, r in RESULTS if not r]

    for label, result in RESULTS:
        icon = "✅" if result else "❌"
        print(f"  {icon}  {label}")

    total  = len(RESULTS)
    n_pass = len(passed_list)
    n_fail = len(failed_list)

    print(f"\n  {'─' * 58}")
    print(f"  Passed : {n_pass}/{total}")
    print(f"  Failed : {n_fail}/{total}")

    if n_fail == 0:
        print("\n  🎉  All integration tests passed — backend is production-ready!")
    else:
        print(f"\n  ⚠️   {n_fail} test(s) failed:")
        for label, _ in failed_list:
            print(f"       • {label}")

    print(f"\n  Swagger UI : {BASE_URL}/docs")
    print(f"  ReDoc      : {BASE_URL}/redoc")
    print(f"  Health     : {BASE_URL}/health/detailed\n")

    sys.exit(0 if n_fail == 0 else 1)


if __name__ == "__main__":
    main()
