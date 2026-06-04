#!/usr/bin/env python3
"""
JWT-authenticated test suite for the Supply Chain Analytics backend.
Covers: registration, login, token refresh, protected resource access,
role-based access control, and logout.
"""

import sys
import requests
from datetime import datetime, timedelta
import json

BASE_URL = "http://localhost:8000"

# ── Helpers ───────────────────────────────────────────────────────────────────

def auth_headers(access_token: str) -> dict:
    return {"Authorization": f"Bearer {access_token}"}


def section(title: str):
    print(f"\n{'='*60}\n  {title}\n{'='*60}")


def ok(label: str):
    print(f"  ✅  {label}")


def fail(label: str):
    print(f"  ❌  {label}")


# ── Auth Tests ────────────────────────────────────────────────────────────────

def test_health_check() -> bool:
    section("Health Check")
    r = requests.get(f"{BASE_URL}/health")
    if r.status_code == 200:
        ok(f"Health OK — {r.json()}")
        return True
    fail(f"Unexpected status {r.status_code}")
    return False


def test_register(username: str, password: str, role: str = "manager") -> bool:
    section(f"Register '{username}' (role={role})")
    payload = {
        "username": username,
        "email": f"{username}@test.com",
        "full_name": "Test User",
        "password": password,
        "role": role,
    }
    r = requests.post(f"{BASE_URL}/api/auth/register", json=payload)
    if r.status_code in (201, 400):  # 400 = already exists, acceptable on re-run
        ok(f"Register response {r.status_code}: {r.json().get('username', r.json().get('detail', ''))}")
        return True
    fail(f"Register failed {r.status_code}: {r.text}")
    return False


def test_login(username: str, password: str) -> dict | None:
    section(f"Login '{username}'")
    r = requests.post(
        f"{BASE_URL}/api/auth/login",
        json={"username": username, "password": password},
    )
    if r.status_code == 200:
        tokens = r.json()
        ok(f"Login OK — access_token: {tokens['access_token'][:40]}...")
        ok(f"Token type: {tokens['token_type']}  |  Expires in: {tokens['expires_in']}s")
        return tokens
    fail(f"Login failed {r.status_code}: {r.text}")
    return None


def test_wrong_password(username: str) -> bool:
    section("Login with Wrong Password (expect 401)")
    r = requests.post(
        f"{BASE_URL}/api/auth/login",
        json={"username": username, "password": "WrongPass999"},
    )
    if r.status_code == 401:
        ok("Correctly rejected with 401")
        return True
    fail(f"Expected 401, got {r.status_code}")
    return False


def test_get_me(access_token: str) -> bool:
    section("GET /api/auth/me")
    r = requests.get(f"{BASE_URL}/api/auth/me", headers=auth_headers(access_token))
    if r.status_code == 200:
        user = r.json()
        ok(f"Profile: {user['username']} | role={user['role']} | active={user['is_active']}")
        return True
    fail(f"Failed {r.status_code}: {r.text}")
    return False


def test_protected_no_token() -> bool:
    section("Access Protected Route Without Token (expect 403/401)")
    r = requests.get(f"{BASE_URL}/api/orders/")
    if r.status_code in (401, 403):
        ok(f"Correctly blocked with {r.status_code}")
        return True
    fail(f"Expected 401/403, got {r.status_code}")
    return False


def test_refresh_token(refresh_token: str) -> dict | None:
    section("Refresh Access Token")
    r = requests.post(
        f"{BASE_URL}/api/auth/refresh",
        json={"refresh_token": refresh_token},
    )
    if r.status_code == 200:
        tokens = r.json()
        ok(f"New access_token: {tokens['access_token'][:40]}...")
        return tokens
    fail(f"Refresh failed {r.status_code}: {r.text}")
    return None


# ── Resource Tests ────────────────────────────────────────────────────────────

def create_supplier(access_token: str) -> int | None:
    section("Create Supplier")
    payload = {
        "supplier_name": "JWT Test Supplier",
        "contact_person": "Jane Smith",
        "email": "jane@supplier.com",
        "phone": "+1-555-0100",
        "city": "Chicago",
        "country": "USA",
        "rating": 4,
    }
    r = requests.post(
        f"{BASE_URL}/api/suppliers/",
        json=payload,
        headers=auth_headers(access_token),
    )
    if r.status_code == 201:
        supplier = r.json()
        ok(f"Supplier created — id={supplier['id']}")
        return supplier["id"]
    fail(f"Failed {r.status_code}: {r.text}")
    return None


def create_product(access_token: str, supplier_id: int) -> int | None:
    section("Create Product")
    payload = {
        "product_name": "Smart Widget Pro",
        "sku": f"SWP-{int(datetime.now().timestamp())}",
        "category": "Electronics",
        "description": "A JWT-protected product",
        "unit_price": 99.99,
        "supplier_id": supplier_id,
        "reorder_level": 15,
    }
    r = requests.post(
        f"{BASE_URL}/api/products/",
        json=payload,
        headers=auth_headers(access_token),
    )
    if r.status_code == 201:
        product = r.json()
        ok(f"Product created — id={product['id']} | sku={product['sku']}")
        return product["id"]
    fail(f"Failed {r.status_code}: {r.text}")
    return None


def create_order(access_token: str, product_id: int, supplier_id: int) -> int | None:
    section("Create Order (with Analytics)")
    now = datetime.now()
    payload = {
        "order_number": f"ORD-JWT-{int(now.timestamp())}",
        "product_id": product_id,
        "supplier_id": supplier_id,
        "quantity": 50,
        "unit_price": 99.99,
        "order_placed_at": now.isoformat(),
        "procurement_completed_at": (now + timedelta(hours=36)).isoformat(),
        "processing_completed_at": (now + timedelta(hours=58)).isoformat(),
        "dispatched_at": (now + timedelta(hours=70)).isoformat(),
        "delivered_at": (now + timedelta(hours=130)).isoformat(),
        "status": "delivered",
    }
    r = requests.post(
        f"{BASE_URL}/api/orders/",
        json=payload,
        headers=auth_headers(access_token),
    )
    if r.status_code == 201:
        order = r.json()
        ok(f"Order created — id={order['id']} | order_number={order['order_number']}")
        ok(f"Analytics: procurement={order['procurement_time']}h | "
           f"processing={order['processing_time']}h | "
           f"total={order['total_time']}h")
        ok(f"SLA breach={order['sla_breach']} | bottleneck={order['bottleneck_stage']}")
        return order["id"]
    fail(f"Failed {r.status_code}: {r.text}")
    return None


def test_analytics(access_token: str) -> bool:
    section("Analytics Endpoints")
    headers = auth_headers(access_token)
    all_ok = True

    for path, label in [
        ("/api/orders/analytics/summary", "Orders Summary"),
        ("/api/forecast/overview", "Forecast Overview"),
        ("/api/forecast/bottleneck-analysis", "Bottleneck Analysis"),
        ("/api/forecast/sla-compliance", "SLA Compliance"),
    ]:
        r = requests.get(f"{BASE_URL}{path}", headers=headers)
        if r.status_code == 200:
            ok(f"{label} — 200 OK")
        else:
            fail(f"{label} — {r.status_code}: {r.text}")
            all_ok = False

    return all_ok


def test_rbac_user_cannot_create(user_token: str) -> bool:
    """A plain 'user' role should be rejected when trying to create a supplier."""
    section("RBAC: 'user' role cannot create supplier (expect 403)")
    r = requests.post(
        f"{BASE_URL}/api/suppliers/",
        json={"supplier_name": "Unauthorized Supplier", "rating": 3},
        headers=auth_headers(user_token),
    )
    if r.status_code == 403:
        ok("Correctly rejected with 403")
        return True
    fail(f"Expected 403, got {r.status_code}: {r.text}")
    return False


def test_logout(access_token: str) -> bool:
    section("Logout")
    r = requests.post(f"{BASE_URL}/api/auth/logout", headers=auth_headers(access_token))
    if r.status_code == 200:
        ok("Logged out successfully")
        return True
    fail(f"Logout failed {r.status_code}: {r.text}")
    return False


def test_refresh_after_logout(old_refresh: str) -> bool:
    section("Refresh After Logout (expect 401 — token invalidated)")
    r = requests.post(
        f"{BASE_URL}/api/auth/refresh",
        json={"refresh_token": old_refresh},
    )
    if r.status_code == 401:
        ok("Correctly rejected with 401 after logout")
        return True
    fail(f"Expected 401, got {r.status_code}")
    return False


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print("\n" + "="*60)
    print("  Supply Chain API — JWT Authentication Test Suite")
    print("="*60)

    results = []

    # 1. Health
    results.append(("Health Check", test_health_check()))

    # 2. Register manager and plain user
    results.append(("Register manager", test_register("testmanager", "Manager1234", "manager")))
    results.append(("Register user", test_register("testuser", "User1234!", "user")))

    # 3. Login
    manager_tokens = test_login("testmanager", "Manager1234")
    results.append(("Manager login", manager_tokens is not None))
    if not manager_tokens:
        print("\n❌ Cannot continue without manager tokens.")
        sys.exit(1)

    user_tokens = test_login("testuser", "User1234!")
    results.append(("User login", user_tokens is not None))

    # 4. Wrong password
    results.append(("Wrong password", test_wrong_password("testmanager")))

    # 5. No-token guard
    results.append(("No-token guard", test_protected_no_token()))

    # 6. Profile
    results.append(("GET /me", test_get_me(manager_tokens["access_token"])))

    # 7. Token refresh
    new_tokens = test_refresh_token(manager_tokens["refresh_token"])
    results.append(("Token refresh", new_tokens is not None))
    working_token = new_tokens["access_token"] if new_tokens else manager_tokens["access_token"]

    # 8. Create resources (manager)
    supplier_id = create_supplier(working_token)
    results.append(("Create supplier", supplier_id is not None))

    product_id = None
    order_id = None
    if supplier_id:
        product_id = create_product(working_token, supplier_id)
        results.append(("Create product", product_id is not None))

        if product_id:
            order_id = create_order(working_token, product_id, supplier_id)
            results.append(("Create order", order_id is not None))

    # 9. Analytics
    results.append(("Analytics endpoints", test_analytics(working_token)))

    # 10. RBAC
    if user_tokens:
        results.append(("RBAC user blocked", test_rbac_user_cannot_create(user_tokens["access_token"])))

    # 11. Logout + refresh invalidation
    results.append(("Logout", test_logout(working_token)))
    if new_tokens:
        results.append(("Refresh after logout", test_refresh_after_logout(new_tokens["refresh_token"])))

    # ── Summary ───────────────────────────────────────────────────────────────
    section("Test Summary")
    passed = sum(1 for _, r in results if r)
    failed = len(results) - passed
    for label, result in results:
        status_icon = "✅" if result else "❌"
        print(f"  {status_icon}  {label}")

    print(f"\n  Passed: {passed}/{len(results)}", end="")
    if failed:
        print(f"  |  Failed: {failed}")
    else:
        print("  — All tests passed! 🎉")

    print(f"\n  Swagger UI:  {BASE_URL}/docs")
    print(f"  ReDoc:       {BASE_URL}/redoc\n")


if __name__ == "__main__":
    main()
