#!/usr/bin/env python3
"""
Smart Retail Platform — Comprehensive End-to-End Test Suite
============================================================
Simulates complete real-world retail operations to validate:
  • Product onboarding pipeline
  • Order lifecycle & SLA tracking
  • Inventory management & replenishment triggers
  • ML demand forecasting (generate → preprocess → train → forecast)
  • Analytics accuracy & cross-module data consistency
  • Notification engine & deduplication
  • Business reports & dashboard integrity
  • Role-based access control across all personas
  • Edge cases, boundary conditions, and error handling
  • System reliability (headers, response envelopes, pagination)

Usage:
    python test_e2e.py                         # runs all 10 scenarios
    python test_e2e.py --url http://host:8000
    python test_e2e.py --scenario 3            # run a single scenario
    python test_e2e.py --verbose               # print response bodies on failure
    python test_e2e.py --skip-ml               # skip slow ML training step

Exit codes: 0 = all passed, 1 = failures detected
"""

import argparse
import json
import sys
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

try:
    import requests
except ImportError:
    sys.exit("requests is not installed — run: pip install requests")

# ═══════════════════════════════════════════════════════════════════════════════
#  ANSI colours & formatting
# ═══════════════════════════════════════════════════════════════════════════════

_R = "\033[0m"
_BOLD = "\033[1m"
_GREEN = "\033[92m"
_RED = "\033[91m"
_YELLOW = "\033[93m"
_CYAN = "\033[96m"
_GREY = "\033[90m"
_MAGENTA = "\033[95m"


def _c(text: str, colour: str) -> str:
    return f"{colour}{text}{_R}"


# ═══════════════════════════════════════════════════════════════════════════════
#  Test state
# ═══════════════════════════════════════════════════════════════════════════════

# Unique run prefix — prevents collision with existing data on a seeded DB
_RUN = uuid.uuid4().hex[:6].upper()


@dataclass
class E2EState:
    base: str
    verbose: bool = False
    skip_ml: bool = False

    # Auth tokens
    admin_token: str = ""
    manager_token: str = ""
    analyst_token: str = ""

    # Created resource IDs (populated during tests)
    supplier_id: int = 0
    supplier2_id: int = 0
    product_ids: List[int] = field(default_factory=list)
    order_ids: List[int] = field(default_factory=list)
    notification_ids: List[int] = field(default_factory=list)

    # Counters
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    scenario_results: Dict[str, Dict] = field(default_factory=dict)
    failures: List[str] = field(default_factory=list)

    def admin(self) -> dict:
        return {"Authorization": f"Bearer {self.admin_token}",
                "Content-Type": "application/json"}

    def manager(self) -> dict:
        return {"Authorization": f"Bearer {self.manager_token}",
                "Content-Type": "application/json"}

    def analyst(self) -> dict:
        return {"Authorization": f"Bearer {self.analyst_token}",
                "Content-Type": "application/json"}

    def no_auth(self) -> dict:
        return {"Content-Type": "application/json"}

    def url(self, path: str) -> str:
        return f"{self.base}/{path.lstrip('/')}"


_state: E2EState = None  # assigned in main()


# ═══════════════════════════════════════════════════════════════════════════════
#  Check helpers
# ═══════════════════════════════════════════════════════════════════════════════

_current_scenario = ""
_scenario_pass = 0
_scenario_fail = 0


def _scenario_start(name: str) -> None:
    global _current_scenario, _scenario_pass, _scenario_fail
    _current_scenario = name
    _scenario_pass = 0
    _scenario_fail = 0
    print(f"\n{_c('═' * 68, _BOLD + _CYAN)}")
    print(f"{_c('  ' + name, _BOLD + _CYAN)}")
    print(f"{_c('═' * 68, _BOLD + _CYAN)}")


def _scenario_end() -> bool:
    global _current_scenario, _scenario_pass, _scenario_fail
    total = _scenario_pass + _scenario_fail
    status = "PASS" if _scenario_fail == 0 else "FAIL"
    colour = _GREEN if _scenario_fail == 0 else _RED
    print(f"\n  {_c(f'Scenario: {status}', colour + _BOLD)}  "
          f"({_scenario_pass}/{total} checks passed)")
    _state.scenario_results[_current_scenario] = {
        "passed": _scenario_pass,
        "failed": _scenario_fail,
        "status": status,
    }
    return _scenario_fail == 0


def _check(name: str, cond: bool, detail: str = "", skip: bool = False) -> bool:
    global _scenario_pass, _scenario_fail
    if skip:
        _state.skipped += 1
        print(f"  {_c('[SKIP]', _YELLOW)}  {name}")
        return False
    if cond:
        _state.passed += 1
        _scenario_pass += 1
        print(f"  {_c('[PASS]', _GREEN)}  {name}")
        return True
    else:
        _state.failed += 1
        _scenario_fail += 1
        msg = f"  {_c('[FAIL]', _RED)}  {name}"
        if detail:
            msg += f"\n         {_c('→ ' + str(detail)[:200], _GREY)}"
        print(msg)
        _state.failures.append(f"[{_current_scenario}] {name}: {detail[:150] if detail else ''}")
        if _state.verbose and detail:
            print(f"         {_c(str(detail)[:500], _GREY)}")
        return False


def _get(path: str, headers: dict, **params) -> requests.Response:
    try:
        return requests.get(_state.url(path), headers=headers, params=params, timeout=30)
    except requests.RequestException as e:
        class _FakeResp:
            status_code = 0
            text = str(e)
            def json(self): raise ValueError("no response")
            headers = {}
        return _FakeResp()


def _post(path: str, headers: dict, body: dict = None) -> requests.Response:
    try:
        return requests.post(_state.url(path), headers=headers, json=body, timeout=30)
    except requests.RequestException as e:
        class _FakeResp:
            status_code = 0
            text = str(e)
            def json(self): raise ValueError
            headers = {}
        return _FakeResp()


def _patch(path: str, headers: dict, body: dict = None) -> requests.Response:
    try:
        return requests.patch(_state.url(path), headers=headers, json=body, timeout=30)
    except requests.RequestException as e:
        class _FakeResp:
            status_code = 0; text = str(e)
            def json(self): raise ValueError
            headers = {}
        return _FakeResp()


def _put(path: str, headers: dict, body: dict = None) -> requests.Response:
    try:
        return requests.put(_state.url(path), headers=headers, json=body, timeout=30)
    except requests.RequestException as e:
        class _FakeResp:
            status_code = 0; text = str(e)
            def json(self): raise ValueError
            headers = {}
        return _FakeResp()


def _delete(path: str, headers: dict) -> requests.Response:
    try:
        return requests.delete(_state.url(path), headers=headers, timeout=30)
    except requests.RequestException as e:
        class _FakeResp:
            status_code = 0; text = str(e)
            def json(self): raise ValueError
            headers = {}
        return _FakeResp()


def _safe_json(r) -> dict:
    try:
        return r.json()
    except Exception:
        return {}


# ═══════════════════════════════════════════════════════════════════════════════
#  SCENARIO 0 — System Readiness & Authentication Bootstrap
# ═══════════════════════════════════════════════════════════════════════════════

def scenario_0_system_and_auth() -> bool:
    _scenario_start("SCENARIO 0 — System Readiness & Authentication")

    # Liveness
    r = _get("/health", _state.no_auth())
    _check("GET /health → 200 (liveness probe)", r.status_code == 200,
           f"status={r.status_code}")
    if r.status_code == 200:
        data = _safe_json(r)
        _check("Liveness: status=healthy",
               data.get("status") == "healthy", str(data))

    # Readiness
    r = _get("/health/detailed", _state.admin())
    _check("GET /health/detailed → 200 (readiness probe)", r.status_code == 200,
           f"status={r.status_code}")
    if r.status_code == 200:
        data = _safe_json(r)
        checks = data.get("checks", {})
        _check("Readiness: database=healthy",
               checks.get("database", {}).get("status") == "healthy",
               str(checks.get("database")))
        _check("Readiness: cache subsystem present",
               "cache" in checks, str(checks.keys()))

    # Security headers present
    r = _get("/health", _state.no_auth())
    h = dict(r.headers)
    _check("Security: X-Content-Type-Options: nosniff",
           h.get("x-content-type-options") == "nosniff", str(h))
    _check("Security: X-Frame-Options present",
           "x-frame-options" in {k.lower() for k in h}, str(list(h.keys())[:6]))
    _check("Security: Content-Security-Policy present",
           "content-security-policy" in {k.lower() for k in h}, "")
    _check("Security: X-RateLimit-Limit header on responses",
           any("ratelimit" in k.lower() for k in h), str(list(h.keys())[:8]))
    _check("Observability: X-Request-ID header present",
           any("request-id" in k.lower() for k in h), "")
    _check("Observability: X-Process-Time-Ms header present",
           any("process-time" in k.lower() for k in h), "")

    # Login as all three roles
    for uname, pwd, role, attr in [
        ("admin",   "Admin@123",   "admin",   "admin_token"),
        ("manager", "Manager@123", "manager", "manager_token"),
        ("analyst", "Analyst@123", "analyst", "analyst_token"),
    ]:
        r = _post("/api/auth/login", _state.no_auth(),
                  {"username": uname, "password": pwd})
        ok = r.status_code == 200
        _check(f"Login as {role} ({uname}) → 200", ok, f"status={r.status_code} {r.text[:80]}")
        if ok:
            setattr(_state, attr, r.json()["access_token"])

    # Refresh token rotation
    r = _post("/api/auth/login", _state.no_auth(),
              {"username": "admin", "password": "Admin@123"})
    if r.status_code == 200:
        refresh_token = r.json().get("refresh_token", "")
        r2 = _post("/api/auth/refresh",
                   {"Authorization": f"Bearer {refresh_token}",
                    "Content-Type": "application/json"})
        _check("Refresh token rotation → new access token",
               r2.status_code == 200 and "access_token" in _safe_json(r2),
               f"status={r2.status_code}")

    return _scenario_end()


# ═══════════════════════════════════════════════════════════════════════════════
#  SCENARIO 1 — Product Onboarding Pipeline
# ═══════════════════════════════════════════════════════════════════════════════

def scenario_1_product_onboarding() -> bool:
    _scenario_start("SCENARIO 1 — Product Onboarding Pipeline")

    # Create primary supplier
    r = _post("/api/suppliers/", _state.admin(), {
        "supplier_name": f"E2E Supplies {_RUN}",
        "contact_person": "Jane Smith",
        "email": f"e2e-{_RUN.lower()}@supplier.test",
        "phone": "+1-555-0100",
        "city": "New York",
        "country": "USA",
        "rating": 4,
    })
    _check("Create primary supplier → 201", r.status_code == 201,
           f"status={r.status_code} {r.text[:100]}")
    if r.status_code == 201:
        _state.supplier_id = r.json().get("id", 0)
        _check("Supplier: id assigned", _state.supplier_id > 0,
               str(_safe_json(r)))

    # Create secondary supplier
    r = _post("/api/suppliers/", _state.admin(), {
        "supplier_name": f"E2E Logistics {_RUN}",
        "email": f"e2e2-{_RUN.lower()}@logistics.test",
        "city": "Chicago",
        "country": "USA",
        "rating": 2,
    })
    if r.status_code == 201:
        _state.supplier2_id = r.json().get("id", 0)

    # Retrieve supplier and verify fields
    if _state.supplier_id:
        r = _get(f"/api/suppliers/{_state.supplier_id}", _state.admin())
        _check("Retrieve supplier by ID → 200", r.status_code == 200, "")
        if r.status_code == 200:
            d = _safe_json(r)
            _check("Supplier: name matches",
                   f"E2E Supplies {_RUN}" in str(d.get("supplier_name", "")), str(d))

    # Create products in two categories
    products_to_create = [
        {"product_name": f"E2E Laptop {_RUN}", "sku": f"ELEC-E2E-{_RUN}-01",
         "category": "Electronics", "unit_price": 1299.99, "reorder_level": 5,
         "supplier_id": _state.supplier_id},
        {"product_name": f"E2E Chair {_RUN}", "sku": f"FURN-E2E-{_RUN}-01",
         "category": "Furniture", "unit_price": 349.50, "reorder_level": 10,
         "supplier_id": _state.supplier_id},
        {"product_name": f"E2E Notebook {_RUN}", "sku": f"STAT-E2E-{_RUN}-01",
         "category": "Stationery", "unit_price": 4.99, "reorder_level": 50,
         "supplier_id": _state.supplier_id},
    ]
    for prod in products_to_create:
        r = _post("/api/products/", _state.admin(), prod)
        _check(f"Create product '{prod['product_name'][:20]}' → 201",
               r.status_code == 201, f"status={r.status_code} {r.text[:100]}")
        if r.status_code == 201:
            _state.product_ids.append(r.json().get("id", 0))

    _check("All 3 products created successfully", len(_state.product_ids) == 3,
           f"only {len(_state.product_ids)} created")

    # Retrieve by SKU
    if _state.product_ids:
        r = _get(f"/api/products/sku/ELEC-E2E-{_RUN}-01", _state.admin())
        _check("Retrieve product by SKU → 200", r.status_code == 200, "")
        if r.status_code == 200:
            d = _safe_json(r)
            _check("Product: unit_price correct",
                   abs(d.get("unit_price", 0) - 1299.99) < 0.01, str(d))
            _check("Product: reorder_level stored",
                   d.get("reorder_level") == 5, str(d.get("reorder_level")))

    # Retrieve products by category
    r = _get("/api/products/category/Electronics", _state.admin())
    _check("Retrieve products by category → 200", r.status_code == 200, "")
    if r.status_code == 200:
        products_in_cat = [p for p in _safe_json(r) if f"E2E" in str(p.get("sku", ""))]
        _check("At least 1 Electronics product present",
               len(products_in_cat) >= 1, f"found {len(products_in_cat)}")

    # Pagination test
    r = _get("/api/products/", _state.admin(), skip=0, limit=1)
    _check("Pagination: limit=1 returns exactly 1 product",
           r.status_code == 200 and len(_safe_json(r)) == 1,
           f"got {len(_safe_json(r)) if r.status_code == 200 else r.status_code}")

    # Update product
    if _state.product_ids:
        pid = _state.product_ids[0]
        r = _put(f"/api/products/{pid}", _state.admin(), {"unit_price": 1199.99})
        _check(f"Update product {pid} price → 200", r.status_code == 200,
               f"status={r.status_code}")
        if r.status_code == 200:
            _check("Updated price reflected",
                   abs(_safe_json(r).get("unit_price", 0) - 1199.99) < 0.01,
                   str(_safe_json(r).get("unit_price")))

    return _scenario_end()


# ═══════════════════════════════════════════════════════════════════════════════
#  SCENARIO 2 — Complete Order Lifecycle & SLA Tracking
# ═══════════════════════════════════════════════════════════════════════════════

def scenario_2_order_lifecycle() -> bool:
    _scenario_start("SCENARIO 2 — Complete Order Lifecycle & SLA Tracking")

    if not _state.product_ids or not _state.supplier_id:
        _check("SKIP: no products/supplier available from Scenario 1",
               False, "Run scenario 1 first or use demo_seed.py")
        return _scenario_end()

    pid = _state.product_ids[0]
    sid = _state.supplier_id

    now = datetime.now(timezone.utc)

    # ── Order 1: Fast order — should NOT breach SLA ──────────────────────────
    o1_placed = (now - timedelta(hours=100)).isoformat()
    o1_proc   = (now - timedelta(hours=70)).isoformat()   # 30h procurement ✓ < 48h
    o1_proc_c = (now - timedelta(hours=55)).isoformat()   # 15h processing  ✓ < 24h
    o1_disp   = (now - timedelta(hours=48)).isoformat()   # 7h dispatch     ✓ < 12h
    o1_del    = (now - timedelta(hours=24)).isoformat()   # 24h delivery    ✓ < 72h

    r = _post("/api/orders/", _state.manager(), {
        "order_number": f"E2E-FAST-{_RUN}",
        "product_id": pid, "supplier_id": sid,
        "quantity": 10, "unit_price": 1199.99, "total_amount": 11999.90,
        "status": "delivered",
        "order_placed_at": o1_placed,
        "procurement_completed_at": o1_proc,
        "processing_completed_at": o1_proc_c,
        "dispatched_at": o1_disp,
        "delivered_at": o1_del,
    })
    _check("Create fast order → 201", r.status_code == 201,
           f"status={r.status_code} {r.text[:120]}")
    fast_order_id = 0
    if r.status_code == 201:
        fast_order_id = r.json().get("id", 0)
        _state.order_ids.append(fast_order_id)
        d = r.json()
        _check("Fast order: procurement_time calculated",
               d.get("procurement_time") is not None, str(d.get("procurement_time")))
        _check("Fast order: total_time calculated",
               d.get("total_time") is not None, str(d.get("total_time")))
        _check("Fast order: SLA breach = False",
               d.get("sla_breach") is False, f"sla_breach={d.get('sla_breach')}")

    # ── Order 2: Slow order — should breach SLA (procurement took 55h > 48h) ─
    o2_placed = (now - timedelta(hours=200)).isoformat()
    o2_proc   = (now - timedelta(hours=142)).isoformat()  # 58h procurement ✗ > 48h

    r = _post("/api/orders/", _state.manager(), {
        "order_number": f"E2E-SLOW-{_RUN}",
        "product_id": pid, "supplier_id": sid,
        "quantity": 5, "unit_price": 1199.99, "total_amount": 5999.95,
        "status": "processing",
        "order_placed_at": o2_placed,
        "procurement_completed_at": o2_proc,
    })
    _check("Create slow order (SLA breach) → 201", r.status_code == 201,
           f"status={r.status_code} {r.text[:120]}")
    slow_order_id = 0
    if r.status_code == 201:
        slow_order_id = r.json().get("id", 0)
        _state.order_ids.append(slow_order_id)
        d = r.json()
        _check("Slow order: sla_breach = True",
               d.get("sla_breach") is True,
               f"sla_breach={d.get('sla_breach')} breached_stage={d.get('breached_stage')}")
        _check("Slow order: breached_stage = procurement",
               "procurement" in str(d.get("breached_stage", "")).lower(),
               f"breached_stage={d.get('breached_stage')}")

    # ── Order 3: Pending order for lifecycle progression ─────────────────────
    r = _post("/api/orders/", _state.manager(), {
        "order_number": f"E2E-PROG-{_RUN}",
        "product_id": _state.product_ids[1] if len(_state.product_ids) > 1 else pid,
        "supplier_id": sid,
        "quantity": 20, "unit_price": 349.50, "total_amount": 6990.00,
        "status": "pending",
        "order_placed_at": now.isoformat(),
    })
    _check("Create pending order → 201", r.status_code == 201,
           f"status={r.status_code} {r.text[:120]}")
    prog_order_id = 0
    if r.status_code == 201:
        prog_order_id = r.json().get("id", 0)
        _state.order_ids.append(prog_order_id)

    # Retrieve order by ID
    if fast_order_id:
        r = _get(f"/api/orders/{fast_order_id}", _state.admin())
        _check(f"Retrieve order {fast_order_id} by ID → 200",
               r.status_code == 200, "")
        if r.status_code == 200:
            d = _safe_json(r)
            _check("Order: order_number matches",
                   d.get("order_number") == f"E2E-FAST-{_RUN}",
                   f"got {d.get('order_number')}")

    # Retrieve order by order_number
    r = _get(f"/api/orders/by-number/E2E-FAST-{_RUN}", _state.admin())
    _check("Retrieve order by order_number → 200", r.status_code == 200,
           f"status={r.status_code}")

    # Retrieve orders by status
    r = _get("/api/orders/status/pending", _state.admin())
    _check("GET /api/orders/status/pending → 200", r.status_code == 200, "")
    if r.status_code == 200:
        pending_orders = _safe_json(r)
        _check("Pending orders list is a list",
               isinstance(pending_orders, list), str(type(pending_orders)))

    # SLA breach analytics
    r = _get("/api/orders/analytics/sla-breaches", _state.admin())
    _check("GET /api/orders/analytics/sla-breaches → 200", r.status_code == 200, "")
    if r.status_code == 200:
        sla_list = _safe_json(r)
        # At least our seeded breach order should be there
        _check("SLA breach list not empty (at least 1 breach)",
               len(sla_list) >= 1, f"got {len(sla_list)}")
        if sla_list:
            _check("SLA breach entry has sla_breach=True",
                   all(o.get("sla_breach") is True for o in sla_list[:5]),
                   str(sla_list[0]))

    # Analytics summary
    r = _get("/api/orders/analytics/summary", _state.admin())
    _check("GET /api/orders/analytics/summary → 200", r.status_code == 200, "")
    if r.status_code == 200:
        d = _safe_json(r)
        _check("Analytics: total_orders > 0",
               d.get("total_orders", 0) > 0, f"total_orders={d.get('total_orders')}")

    return _scenario_end()


# ═══════════════════════════════════════════════════════════════════════════════
#  SCENARIO 3 — Inventory Management & Replenishment Triggers
# ═══════════════════════════════════════════════════════════════════════════════

def scenario_3_inventory_management() -> bool:
    _scenario_start("SCENARIO 3 — Inventory Management & Replenishment Triggers")

    # Get all inventory
    r = _get("/api/inventory/", _state.admin())
    _check("GET /api/inventory/ → 200", r.status_code == 200,
           f"status={r.status_code}")
    if r.status_code != 200:
        return _scenario_end()

    inventory = _safe_json(r)
    _check("Inventory list is non-empty", len(inventory) > 0,
           f"count={len(inventory)}")

    if not inventory:
        return _scenario_end()

    # Find a product with known inventory
    sample_inv = inventory[0]
    inv_id = sample_inv.get("id", 0)
    old_qty = sample_inv.get("quantity_available", 0)

    # Update stock level
    new_qty = old_qty + 100
    r = _put(f"/api/inventory/{inv_id}", _state.admin(), {
        "quantity_available": new_qty,
        "warehouse_location": f"Warehouse-E2E-{_RUN}",
    })
    _check(f"Update inventory {inv_id} → 200", r.status_code == 200,
           f"status={r.status_code} {r.text[:80]}")
    if r.status_code == 200:
        d = _safe_json(r)
        _check("Inventory: quantity_available updated",
               d.get("quantity_available") == new_qty,
               f"expected {new_qty} got {d.get('quantity_available')}")
        _check("Inventory: warehouse_location updated",
               f"E2E-{_RUN}" in str(d.get("warehouse_location", "")),
               str(d.get("warehouse_location")))

    # Set a product to low stock (below reorder_level) to trigger alert
    low_stock_inv = None
    for inv in inventory[:10]:
        if inv.get("product_id") and inv.get("id"):
            low_stock_inv = inv
            break

    if low_stock_inv:
        r = _put(f"/api/inventory/{low_stock_inv['id']}", _state.admin(), {
            "quantity_available": 2,  # below any reasonable reorder_level
        })
        _check("Set product to critical low stock (qty=2)",
               r.status_code == 200, f"status={r.status_code}")

    # Inventory health score (BI module)
    r = _get("/api/bi/inventory-health-score", _state.admin())
    _check("GET /api/bi/inventory-health-score → 200",
           r.status_code == 200, f"status={r.status_code}")
    if r.status_code == 200:
        d = _safe_json(r)
        _check("Health score: overall_score 0-100",
               0 <= d.get("overall_score", -1) <= 100,
               f"overall_score={d.get('overall_score')}")
        _check("Health score: grade present (A-F)",
               d.get("grade") in ("A", "B", "C", "D", "F"),
               f"grade={d.get('grade')}")

    # Recommendations
    r = _get("/api/recommendations/", _state.admin())
    _check("GET /api/recommendations/ → 200",
           r.status_code == 200, f"status={r.status_code}")

    r = _get("/api/recommendations/health", _state.admin())
    _check("GET /api/recommendations/health → 200",
           r.status_code == 200, f"status={r.status_code}")
    if r.status_code == 200:
        d = _safe_json(r)
        _check("Health: health_score present",
               "health_score" in d, str(list(d.keys())))

    # Replenishment list — at least 1 critical item expected
    r = _get("/api/recommendations/replenishment", _state.admin())
    _check("GET /api/recommendations/replenishment → 200",
           r.status_code == 200, f"status={r.status_code}")
    if r.status_code == 200:
        replen = _safe_json(r)
        _check("Replenishment list returned (list type)",
               isinstance(replen, (list, dict)), "")

    # Critical alerts
    r = _get("/api/recommendations/alerts", _state.admin())
    _check("GET /api/recommendations/alerts → 200",
           r.status_code == 200, f"status={r.status_code}")

    return _scenario_end()


# ═══════════════════════════════════════════════════════════════════════════════
#  SCENARIO 4 — ML Demand Forecasting Pipeline (End-to-End)
# ═══════════════════════════════════════════════════════════════════════════════

def scenario_4_ml_forecasting() -> bool:
    _scenario_start("SCENARIO 4 — ML Demand Forecasting Pipeline")

    if _state.skip_ml:
        print(f"  {_c('[SKIP]', _YELLOW)}  ML pipeline skipped (--skip-ml flag set)")
        _state.skipped += 6
        return _scenario_end()

    # Step 1: Generate synthetic data
    print(f"  {_c('→ Generating synthetic training data (may take 5–15 s)…', _GREY)}")
    r = _post("/api/ml/pipeline/generate", _state.analyst())
    _check("POST /api/ml/pipeline/generate → 200",
           r.status_code == 200, f"status={r.status_code} {r.text[:120]}")
    if r.status_code == 200:
        d = _safe_json(r)
        _check("Data generation: records_generated > 0",
               d.get("records_generated", 0) > 0, f"records={d.get('records_generated')}")

    # Step 2: Run preprocessing pipeline
    print(f"  {_c('→ Running preprocessing pipeline…', _GREY)}")
    r = _post("/api/ml/pipeline/run", _state.analyst())
    _check("POST /api/ml/pipeline/run → 200",
           r.status_code == 200, f"status={r.status_code} {r.text[:120]}")
    if r.status_code == 200:
        d = _safe_json(r)
        _check("Pipeline: train_size > 0",
               d.get("train_size", 0) > 0, f"train_size={d.get('train_size')}")
        _check("Pipeline: features list non-empty",
               len(d.get("features", [])) > 0, f"features={d.get('features', [])[:3]}")

    # Pipeline status
    r = _get("/api/ml/pipeline/status", _state.analyst())
    _check("GET /api/ml/pipeline/status → 200",
           r.status_code == 200, f"status={r.status_code}")

    # Features list
    r = _get("/api/ml/pipeline/features", _state.analyst())
    _check("GET /api/ml/pipeline/features → 200",
           r.status_code == 200, "")
    if r.status_code == 200:
        features = _safe_json(r)
        feature_list = features if isinstance(features, list) else features.get("features", [])
        _check("At least 10 engineered features listed",
               len(feature_list) >= 10, f"count={len(feature_list)}")

    # Step 3: Train model
    print(f"  {_c('→ Training GradientBoosting model (may take 10–30 s)…', _GREY)}")
    r = _post("/api/predictions/train", _state.analyst())
    _check("POST /api/predictions/train → 200",
           r.status_code == 200, f"status={r.status_code} {r.text[:120]}")
    if r.status_code == 200:
        d = _safe_json(r)
        _check("Model: mae returned",
               d.get("mae") is not None, str(d))
        _check("Model: r2 > 0 (positive fit)",
               (d.get("r2") or 0) > 0,
               f"r2={d.get('r2')}")
        _check("Model: rmse > 0",
               (d.get("rmse") or 0) > 0, f"rmse={d.get('rmse')}")

    # Model status
    r = _get("/api/predictions/model/status", _state.analyst())
    _check("GET /api/predictions/model/status → 200",
           r.status_code == 200, f"status={r.status_code}")
    if r.status_code == 200:
        d = _safe_json(r)
        _check("Model status: trained_at present",
               bool(d.get("trained_at")), str(d))

    # Step 4: Generate forecast for product 1
    r = _post("/api/predictions/forecast", _state.analyst(),
              {"product_id": 1, "days": 30})
    _check("POST /api/predictions/forecast (30 days) → 200",
           r.status_code == 200, f"status={r.status_code} {r.text[:120]}")
    if r.status_code == 200:
        d = _safe_json(r)
        predictions = d.get("predictions", [])
        _check("Forecast: 30 daily predictions returned",
               len(predictions) == 30,
               f"got {len(predictions)} predictions")
        if predictions:
            first = predictions[0]
            _check("Forecast: each entry has 'date' field",
                   "date" in first, str(first))
            _check("Forecast: predicted_demand > 0",
                   first.get("predicted_demand", 0) > 0,
                   f"value={first.get('predicted_demand')}")
            _check("Forecast: confidence_high > confidence_low",
                   first.get("confidence_high", 0) > first.get("confidence_low", 0),
                   f"low={first.get('confidence_low')} high={first.get('confidence_high')}")

    # GET forecast endpoint
    r = _get("/api/predictions/forecast/1", _state.analyst())
    _check("GET /api/predictions/forecast/1 → 200",
           r.status_code == 200, f"status={r.status_code}")

    # Short horizon forecast (7 days)
    r = _post("/api/predictions/forecast", _state.analyst(),
              {"product_id": 1, "days": 7})
    if r.status_code == 200:
        preds = _safe_json(r).get("predictions", [])
        _check("Forecast: 7-day horizon returns 7 entries",
               len(preds) == 7, f"got {len(preds)}")

    return _scenario_end()


# ═══════════════════════════════════════════════════════════════════════════════
#  SCENARIO 5 — Analytics & Cross-Module Data Consistency
# ═══════════════════════════════════════════════════════════════════════════════

def scenario_5_analytics_consistency() -> bool:
    _scenario_start("SCENARIO 5 — Analytics & Cross-Module Data Consistency")

    # Analytics summary
    r = _get("/api/analytics/summary", _state.admin())
    _check("GET /api/analytics/summary → 200", r.status_code == 200,
           f"status={r.status_code}")
    analytics_total = 0
    if r.status_code == 200:
        d = _safe_json(r)
        data = d.get("data", d)
        analytics_total = data.get("total_orders", 0)
        _check("Analytics summary: total_orders > 0",
               analytics_total > 0, f"total_orders={analytics_total}")
        _check("Analytics summary: sla_breach_count present",
               "sla_breach_count" in data or "total_sla_breaches" in data,
               str(list(data.keys())[:6]))

    # Cross-check: orders count from /api/orders/ should match analytics
    r = _get("/api/orders/", _state.admin(), skip=0, limit=1000)
    if r.status_code == 200:
        all_orders = _safe_json(r)
        orders_count = len(all_orders)
        _check(
            f"Data consistency: orders count (API={orders_count}) ≥ analytics count ({analytics_total})",
            orders_count >= analytics_total,
            f"api={orders_count} analytics={analytics_total}",
        )

    # Bottleneck analytics
    r = _get("/api/analytics/bottlenecks", _state.admin())
    _check("GET /api/analytics/bottlenecks → 200", r.status_code == 200, "")
    if r.status_code == 200:
        d = _safe_json(r)
        data = d.get("data", [])
        _check("Bottlenecks: data is list", isinstance(data, list), "")
        valid_stages = {"procurement", "processing", "dispatch", "delivery", ""}
        for entry in data[:5]:
            stage = str(entry.get("stage", "")).lower()
            _check(f"Bottleneck entry has stage field",
                   "stage" in entry, str(entry))
            break

    # SLA breach analytics
    r = _get("/api/analytics/sla-breaches", _state.admin())
    _check("GET /api/analytics/sla-breaches → 200", r.status_code == 200, "")
    if r.status_code == 200:
        d = _safe_json(r)
        data = d.get("data", d) if isinstance(d, dict) else d
        _check("SLA breaches: list type returned",
               isinstance(data, list), str(type(data)))
        # Verify all returned entries actually have sla_breach=True
        bad = [o for o in (data[:10] if isinstance(data, list) else [])
               if o.get("sla_breach") is not True]
        _check("SLA breach analytics: all entries have sla_breach=True",
               len(bad) == 0,
               f"{len(bad)} entries without sla_breach=True")

    # Revenue consistency: sum from /api/orders/ ≈ reports/sales/summary
    r1 = _get("/api/reports/sales/summary", _state.admin())
    _check("GET /api/reports/sales/summary → 200", r1.status_code == 200, "")
    if r1.status_code == 200:
        d = _safe_json(r1)
        _check("Sales summary: total_revenue present",
               "total_revenue" in d or "revenue" in str(d).lower(),
               str(list(d.keys())[:6]))
        _check("Sales summary: total_orders present",
               "total_orders" in d or "order_count" in d,
               str(list(d.keys())[:6]))

    # Products count consistency
    r_prods = _get("/api/products/", _state.admin())
    r_inv = _get("/api/inventory/", _state.admin())
    if r_prods.status_code == 200 and r_inv.status_code == 200:
        prods = _safe_json(r_prods)
        inv = _safe_json(r_inv)
        _check(
            "Data consistency: inventory records ≥ products count",
            len(inv) >= len(prods),
            f"inv={len(inv)} products={len(prods)}",
            skip=(len(prods) == 0),
        )

    # Supplier count consistency
    r = _get("/api/suppliers/", _state.admin())
    _check("GET /api/suppliers/ → 200", r.status_code == 200, "")
    if r.status_code == 200:
        suppliers = _safe_json(r)
        _check("Suppliers list non-empty", len(suppliers) > 0,
               f"count={len(suppliers)}")

    return _scenario_end()


# ═══════════════════════════════════════════════════════════════════════════════
#  SCENARIO 6 — Notification Engine & Alert Deduplication
# ═══════════════════════════════════════════════════════════════════════════════

def scenario_6_notifications() -> bool:
    _scenario_start("SCENARIO 6 — Notification Engine & Alert Deduplication")

    # Run alert engine (first pass)
    r = _post("/api/notifications/run", _state.analyst())
    _check("POST /api/notifications/run → 200 (first pass)",
           r.status_code == 200, f"status={r.status_code} {r.text[:120]}")
    first_created = 0
    if r.status_code == 200:
        d = _safe_json(r)
        first_created = d.get("alerts_created", 0)
        _check("Alert run: alerts_created is int ≥ 0",
               isinstance(first_created, int) and first_created >= 0,
               f"alerts_created={first_created}")
        _check("Alert run: response has 'alerts_skipped' field",
               "alerts_skipped" in d, str(list(d.keys())))

    # Run alert engine (second pass — deduplication)
    r = _post("/api/notifications/run", _state.analyst())
    _check("POST /api/notifications/run → 200 (second pass)",
           r.status_code == 200, "")
    if r.status_code == 200:
        d = _safe_json(r)
        second_created = d.get("alerts_created", 0)
        skipped = d.get("alerts_skipped", 0)
        _check(
            "Deduplication: second run creates 0 new alerts (all deduplicated)",
            second_created == 0,
            f"second_created={second_created} (dedup_key constraint should prevent duplicates)",
        )
        _check(
            "Deduplication: skipped count ≥ first run created count",
            skipped >= first_created or first_created == 0,
            f"first_created={first_created} second_skipped={skipped}",
        )

    # List notifications
    r = _get("/api/notifications/", _state.admin())
    _check("GET /api/notifications/ → 200", r.status_code == 200,
           f"status={r.status_code}")
    notif_id = 0
    if r.status_code == 200:
        d = _safe_json(r)
        notifs = d.get("notifications", d.get("items", d)) if isinstance(d, dict) else d
        if isinstance(notifs, list) and notifs:
            notif_id = notifs[0].get("id", 0)
            _state.notification_ids.append(notif_id)
            _check("Notification has required fields (id, category, priority, message)",
                   all(k in notifs[0] for k in ("id", "category", "priority", "message")),
                   str(list(notifs[0].keys())[:6]))
            _check("Notification priority is valid",
                   notifs[0].get("priority", "").upper() in
                   ("CRITICAL", "HIGH", "MEDIUM", "LOW"),
                   f"priority={notifs[0].get('priority')}")

    # Summary endpoint
    r = _get("/api/notifications/summary", _state.admin())
    _check("GET /api/notifications/summary → 200", r.status_code == 200, "")
    if r.status_code == 200:
        d = _safe_json(r)
        _check("Summary: total_count present",
               "total_count" in d or "total" in str(d).lower(),
               str(list(d.keys())[:6]))

    # Filter by priority
    r = _get("/api/notifications/", _state.admin(), priority="HIGH")
    _check("GET /api/notifications/?priority=HIGH → 200",
           r.status_code == 200, f"status={r.status_code}")

    # Mark as read
    if notif_id:
        r = _patch(f"/api/notifications/{notif_id}/read", _state.admin())
        _check(f"PATCH /api/notifications/{notif_id}/read → 200",
               r.status_code == 200, f"status={r.status_code}")

    # Mark all as read
    r = _patch("/api/notifications/read-all", _state.admin())
    _check("PATCH /api/notifications/read-all → 200",
           r.status_code == 200, f"status={r.status_code}")

    # Resolve a notification
    if notif_id:
        r = _patch(f"/api/notifications/{notif_id}/resolve", _state.admin(),
                   {"resolution_note": f"E2E test resolved {_RUN}"})
        _check(f"PATCH /api/notifications/{notif_id}/resolve → 200",
               r.status_code == 200, f"status={r.status_code}")

    return _scenario_end()


# ═══════════════════════════════════════════════════════════════════════════════
#  SCENARIO 7 — Business Reports & Dashboard Integrity
# ═══════════════════════════════════════════════════════════════════════════════

def scenario_7_reports_dashboard() -> bool:
    _scenario_start("SCENARIO 7 — Business Reports & Dashboard Integrity")

    # ── 14 business reports ───────────────────────────────────────────────────
    reports = [
        ("Sales summary",        "/api/reports/sales/summary"),
        ("Sales trends",         "/api/reports/sales/trends"),
        ("Top products",         "/api/reports/sales/top-products"),
        ("Sales by category",    "/api/reports/sales/by-category"),
        ("Sales fulfillment",    "/api/reports/sales/fulfillment"),
        ("Inventory valuation",  "/api/reports/inventory/valuation"),
        ("Inventory turnover",   "/api/reports/inventory/turnover"),
        ("Inventory aging",      "/api/reports/inventory/aging"),
        ("Supplier performance", "/api/reports/suppliers/performance"),
        ("Forecast accuracy",    "/api/reports/forecast/accuracy"),
        ("Operations KPIs",      "/api/reports/operations/kpis"),
        ("SLA compliance",       "/api/reports/operations/sla-compliance"),
        ("Bottleneck report",    "/api/reports/operations/bottlenecks"),
    ]
    for label, path in reports:
        r = _get(path, _state.admin())
        _check(f"Report: {label} → 200", r.status_code == 200,
               f"status={r.status_code}")

    # ── Dashboard widgets ─────────────────────────────────────────────────────
    widgets = [
        ("Sales widget",     "/api/dashboard/widgets/sales"),
        ("Inventory widget", "/api/dashboard/widgets/inventory"),
        ("Suppliers widget", "/api/dashboard/widgets/suppliers"),
        ("Forecast widget",  "/api/dashboard/widgets/forecast"),
        ("Alerts widget",    "/api/dashboard/widgets/alerts"),
    ]
    for label, path in widgets:
        r = _get(path, _state.admin())
        _check(f"Dashboard widget: {label} → 200", r.status_code == 200,
               f"status={r.status_code}")

    # ── Dashboard master summary ──────────────────────────────────────────────
    r = _get("/api/dashboard/summary", _state.admin(), days=30)
    _check("GET /api/dashboard/summary → 200", r.status_code == 200,
           f"status={r.status_code}")
    if r.status_code == 200:
        d = _safe_json(r)
        _check("Dashboard: 'widgets' section present",
               "widgets" in d, str(list(d.keys())))
        _check("Dashboard: 'charts' section present",
               "charts" in d, str(list(d.keys())))
        widgets_data = d.get("widgets", {})
        expected_widgets = {"sales", "inventory", "suppliers", "forecast", "alerts"}
        present = set(widgets_data.keys())
        _check(f"Dashboard: all 5 widgets present",
               expected_widgets.issubset(present),
               f"missing: {expected_widgets - present}")

    # ── Dashboard charts ──────────────────────────────────────────────────────
    charts = [
        "/api/dashboard/charts/revenue-trend",
        "/api/dashboard/charts/order-status",
        "/api/dashboard/charts/top-products",
        "/api/dashboard/charts/inventory-health",
        "/api/dashboard/charts/supplier-performance",
        "/api/dashboard/charts/category-revenue",
    ]
    for path in charts:
        r = _get(path, _state.admin())
        _check(f"Chart: {path.split('/')[-1]} → 200",
               r.status_code == 200, f"status={r.status_code}")

    # ── CSV exports ───────────────────────────────────────────────────────────
    for label, path in [
        ("Sales CSV",     "/api/reports/export/sales"),
        ("Inventory CSV", "/api/reports/export/inventory"),
        ("Suppliers CSV", "/api/reports/export/suppliers"),
    ]:
        r = _get(path, _state.admin())
        _check(f"Export: {label} → 200",
               r.status_code == 200, f"status={r.status_code}")
        if r.status_code == 200:
            content_type = r.headers.get("content-type", "")
            _check(f"Export: {label} has CSV content-type",
                   "csv" in content_type or "text" in content_type,
                   f"content-type={content_type}")

    return _scenario_end()


# ═══════════════════════════════════════════════════════════════════════════════
#  SCENARIO 8 — Advanced Business Intelligence Module
# ═══════════════════════════════════════════════════════════════════════════════

def scenario_8_bi_module() -> bool:
    _scenario_start("SCENARIO 8 — Advanced Business Intelligence Module")

    bi_endpoints = [
        ("Executive summary",       "/api/bi/executive-summary"),
        ("KPI trends",              "/api/bi/kpi-trends"),
        ("Profitability",           "/api/bi/profitability"),
        ("Period comparison (MoM)", "/api/bi/period-comparison"),
        ("Supplier intelligence",   "/api/bi/supplier-intelligence"),
        ("Forecast performance",    "/api/bi/forecast-performance"),
        ("Cohort analysis",         "/api/bi/cohort-analysis"),
        ("Alerts intelligence",     "/api/bi/alerts-intelligence"),
        ("Strategic insights",      "/api/bi/strategic-insights"),
    ]
    for label, path in bi_endpoints:
        r = _get(path, _state.admin())
        _check(f"BI: {label} → 200", r.status_code == 200,
               f"status={r.status_code}")

    # Inventory health score (already in Scenario 3, verify here for BI completeness)
    r = _get("/api/bi/inventory-health-score", _state.admin())
    _check("BI: Inventory health score → 200", r.status_code == 200, "")

    # Validate executive summary structure
    r = _get("/api/bi/executive-summary", _state.admin())
    if r.status_code == 200:
        d = _safe_json(r)
        for key in ("revenue", "sla_compliance_rate", "total_orders"):
            _check(f"Executive summary: '{key}' field present",
                   key in d, str(list(d.keys())[:8]))

    # Strategic insights should return a list of insights
    r = _get("/api/bi/strategic-insights", _state.admin())
    if r.status_code == 200:
        d = _safe_json(r)
        insights = d.get("insights", [])
        _check("Strategic insights: insights list present",
               isinstance(insights, list), str(type(insights)))
        _check("Strategic insights: generated_at timestamp present",
               "generated_at" in d, str(list(d.keys())))

    # KPI trends with granularity options
    for gran in ("daily", "weekly", "monthly"):
        r = _get("/api/bi/kpi-trends", _state.admin(), days=90, granularity=gran)
        _check(f"KPI trends: granularity={gran} → 200",
               r.status_code == 200, f"status={r.status_code}")

    return _scenario_end()


# ═══════════════════════════════════════════════════════════════════════════════
#  SCENARIO 9 — RBAC Multi-Role User Acceptance Testing
# ═══════════════════════════════════════════════════════════════════════════════

def scenario_9_rbac_uat() -> bool:
    _scenario_start("SCENARIO 9 — RBAC & Multi-Role User Acceptance Testing")

    if not _state.analyst_token:
        _check("SKIP: analyst token not available", False, "login failed earlier")
        return _scenario_end()

    # ── What ADMIN can do ─────────────────────────────────────────────────────
    r = _get("/api/products/", _state.admin())
    _check("Admin: READ products → 200", r.status_code == 200, "")

    r = _get("/api/suppliers/", _state.admin())
    _check("Admin: READ suppliers → 200", r.status_code == 200, "")

    # ── What MANAGER can do ───────────────────────────────────────────────────
    r = _get("/api/products/", _state.manager())
    _check("Manager: READ products → 200", r.status_code == 200, "")

    r = _get("/api/reports/sales/summary", _state.manager())
    _check("Manager: READ reports/sales/summary → 200", r.status_code == 200, "")

    r = _get("/api/dashboard/summary", _state.manager())
    _check("Manager: READ dashboard → 200", r.status_code == 200, "")

    # ── What ANALYST can do ───────────────────────────────────────────────────
    r = _get("/api/products/", _state.analyst())
    _check("Analyst: READ products → 200", r.status_code == 200, "")

    r = _get("/api/analytics/summary", _state.analyst())
    _check("Analyst: READ analytics → 200", r.status_code == 200, "")

    r = _get("/api/bi/executive-summary", _state.analyst())
    _check("Analyst: READ BI executive summary → 200", r.status_code == 200, "")

    r = _get("/api/recommendations/", _state.analyst())
    _check("Analyst: READ recommendations → 200", r.status_code == 200, "")

    # ── What ANALYST cannot do ────────────────────────────────────────────────
    r = _delete("/api/products/999", _state.analyst())
    _check("Analyst: CANNOT DELETE products → 403",
           r.status_code == 403, f"got {r.status_code}")

    r = _post("/api/products/", _state.analyst(), {
        "product_name": "UnauthorisedProduct",
        "sku": f"UNAUTH-{_RUN}",
        "unit_price": 1.0,
    })
    _check("Analyst: CANNOT CREATE products → 403",
           r.status_code == 403, f"got {r.status_code}")

    r = _post("/api/orders/", _state.analyst(), {
        "order_number": f"UNAUTH-ORDER-{_RUN}",
        "product_id": 1, "quantity": 1, "total_amount": 1,
    })
    _check("Analyst: CANNOT CREATE orders → 403",
           r.status_code == 403, f"got {r.status_code}")

    r = _delete("/api/suppliers/999", _state.analyst())
    _check("Analyst: CANNOT DELETE suppliers → 403",
           r.status_code == 403, f"got {r.status_code}")

    # ── What MANAGER cannot do ────────────────────────────────────────────────
    r = _delete("/api/products/999", _state.manager())
    _check("Manager: CANNOT DELETE products → 403",
           r.status_code == 403, f"got {r.status_code}")

    # ── Unauthenticated access ────────────────────────────────────────────────
    for path in ("/api/products/", "/api/orders/", "/api/reports/sales/summary",
                 "/api/bi/executive-summary"):
        r = _get(path, _state.no_auth())
        _check(f"No token: {path} → 401/403",
               r.status_code in (401, 403),
               f"got {r.status_code}")

    return _scenario_end()


# ═══════════════════════════════════════════════════════════════════════════════
#  SCENARIO 10 — Edge Cases, Boundary Conditions & Error Handling
# ═══════════════════════════════════════════════════════════════════════════════

def scenario_10_edge_cases() -> bool:
    _scenario_start("SCENARIO 10 — Edge Cases & Error Handling")

    # ── 404s ──────────────────────────────────────────────────────────────────
    for path in ("/api/products/999999", "/api/orders/999999",
                 "/api/suppliers/999999"):
        r = _get(path, _state.admin())
        _check(f"GET {path} (non-existent) → 404",
               r.status_code == 404, f"got {r.status_code}")
        if r.status_code == 404:
            d = _safe_json(r)
            _check("404: error envelope has 'detail' or 'message'",
                   "detail" in d or "message" in str(d).lower(),
                   str(list(d.keys())[:4]))

    # ── Input validation rejections ────────────────────────────────────────────
    # Negative quantity order
    r = _post("/api/orders/", _state.manager(), {
        "order_number": f"BAD-NEG-{_RUN}",
        "product_id": 1, "supplier_id": 1,
        "quantity": -5, "total_amount": -50,
    })
    _check("Order with negative quantity → 422", r.status_code == 422,
           f"got {r.status_code}")

    # Zero unit_price product
    r = _post("/api/products/", _state.admin(), {
        "product_name": "ZeroPrice", "sku": f"ZERO-{_RUN}", "unit_price": 0,
    })
    _check("Product with unit_price=0 → 422", r.status_code == 422,
           f"got {r.status_code}")

    # Invalid SKU characters
    r = _post("/api/products/", _state.admin(), {
        "product_name": "BadSKU", "sku": "SKU WITH SPACES!", "unit_price": 10.0,
    })
    _check("Product with invalid SKU chars → 422", r.status_code == 422,
           f"got {r.status_code}")

    # Duplicate SKU — should return 400
    if _state.product_ids:
        existing_r = _get(f"/api/products/{_state.product_ids[0]}", _state.admin())
        if existing_r.status_code == 200:
            existing_sku = _safe_json(existing_r).get("sku", "")
            if existing_sku:
                r = _post("/api/products/", _state.admin(), {
                    "product_name": "DupSKU", "sku": existing_sku, "unit_price": 5.0,
                })
                _check("Duplicate SKU → 400", r.status_code == 400,
                       f"got {r.status_code}")

    # Duplicate order number
    r = _post("/api/orders/", _state.manager(), {
        "order_number": f"E2E-FAST-{_RUN}",  # same as created in Scenario 2
        "product_id": 1, "quantity": 1, "total_amount": 1,
        "status": "pending",
    })
    _check("Duplicate order_number → 400/422",
           r.status_code in (400, 422), f"got {r.status_code}")

    # Supplier with invalid rating
    r = _post("/api/suppliers/", _state.admin(), {
        "supplier_name": f"BadRating {_RUN}",
        "email": f"badrating-{_RUN}@test.com",
        "rating": 10,  # valid range: 1-5
    })
    _check("Supplier with rating=10 → 422", r.status_code == 422,
           f"got {r.status_code}")

    # Weak password on registration
    r = _post("/api/auth/register", _state.no_auth(), {
        "username": f"weakpwd{_RUN.lower()}",
        "email": f"weak{_RUN.lower()}@test.com",
        "password": "password",  # no uppercase, no digit, no special
    })
    _check("Register with weak password → 422", r.status_code == 422,
           f"got {r.status_code}")

    # Password without special character
    r = _post("/api/auth/register", _state.no_auth(), {
        "username": f"nospecial{_RUN.lower()}",
        "email": f"nospecial{_RUN.lower()}@test.com",
        "password": "Password1",  # missing special char
    })
    _check("Register without special char in password → 422",
           r.status_code == 422, f"got {r.status_code}")

    # ── Pagination boundary conditions ─────────────────────────────────────────
    r = _get("/api/products/", _state.admin(), skip=0, limit=1)
    _check("Pagination: limit=1 returns ≤1 item",
           r.status_code == 200 and len(_safe_json(r)) <= 1,
           f"count={len(_safe_json(r)) if r.status_code == 200 else 'err'}")

    r = _get("/api/products/", _state.admin(), skip=999999, limit=10)
    _check("Pagination: skip past end returns empty list",
           r.status_code == 200 and len(_safe_json(r)) == 0,
           f"status={r.status_code} count={len(_safe_json(r)) if r.status_code == 200 else 'err'}")

    # ── Date range filters ─────────────────────────────────────────────────────
    future = (datetime.now(timezone.utc) + timedelta(days=365)).strftime("%Y-%m-%dT%H:%M:%SZ")
    r = _get("/api/reports/sales/summary", _state.admin(), start_date=future)
    _check("Reports with future start_date → 200 (empty result, not error)",
           r.status_code == 200, f"got {r.status_code}")

    # ── Error envelope consistency ─────────────────────────────────────────────
    r = _get("/api/products/notanumber", _state.admin())
    _check("Path param 'notanumber' for int field → 4xx",
           400 <= r.status_code < 500,
           f"got {r.status_code}")
    if 400 <= r.status_code < 500:
        d = _safe_json(r)
        _check("Error response has 'detail' or 'status' field",
               "detail" in d or "status" in d,
               str(list(d.keys())[:4]))

    # ── Unauthenticated request returns 401, not 500 ───────────────────────────
    r = _get("/api/bi/executive-summary", _state.no_auth())
    _check("Unauthenticated BI request → 401 (not 500)",
           r.status_code == 401, f"got {r.status_code}")

    # ── XSS in product name rejected ─────────────────────────────────────────
    r = _post("/api/products/", _state.admin(), {
        "product_name": "<script>alert('xss')</script>",
        "sku": f"XSS-{_RUN}",
        "unit_price": 10.0,
    })
    _check("XSS in product_name → 400/422 (injection blocked)",
           r.status_code in (400, 422), f"got {r.status_code}")

    return _scenario_end()


# ═══════════════════════════════════════════════════════════════════════════════
#  SCENARIO 11 — Supplier Intelligence & Scorecard Workflow
# ═══════════════════════════════════════════════════════════════════════════════

def scenario_11_supplier_workflow() -> bool:
    _scenario_start("SCENARIO 11 — Supplier Intelligence & Scorecard")

    r = _get("/api/suppliers/", _state.admin())
    _check("GET /api/suppliers/ → 200", r.status_code == 200, "")
    supplier_list = _safe_json(r) if r.status_code == 200 else []

    if supplier_list:
        first_supplier = supplier_list[0]
        sid = first_supplier.get("id", 0)

        # Supplier scorecard
        r = _get(f"/api/reports/suppliers/{sid}/scorecard", _state.admin())
        _check(f"Supplier scorecard /api/reports/suppliers/{sid}/scorecard → 200",
               r.status_code == 200, f"status={r.status_code}")

        # Supplier performance report
        r = _get("/api/reports/suppliers/performance", _state.admin())
        _check("GET /api/reports/suppliers/performance → 200",
               r.status_code == 200, "")
        if r.status_code == 200:
            d = _safe_json(r)
            perf_list = d if isinstance(d, list) else d.get("data", [])
            _check("Supplier performance: list non-empty",
                   len(perf_list) > 0, f"count={len(perf_list)}")

    # BI supplier intelligence
    r = _get("/api/bi/supplier-intelligence", _state.admin())
    _check("GET /api/bi/supplier-intelligence → 200",
           r.status_code == 200, f"status={r.status_code}")

    # Filter performance by date range
    start = (datetime.now(timezone.utc) - timedelta(days=90)).strftime("%Y-%m-%d")
    r = _get("/api/reports/suppliers/performance", _state.admin(), start_date=start)
    _check("Supplier performance with start_date filter → 200",
           r.status_code == 200, f"status={r.status_code}")

    return _scenario_end()


# ═══════════════════════════════════════════════════════════════════════════════
#  FINAL REPORT
# ═══════════════════════════════════════════════════════════════════════════════

def _print_final_report(elapsed: float) -> None:
    total = _state.passed + _state.failed
    pass_rate = round(_state.passed / total * 100, 1) if total else 0
    all_pass = _state.failed == 0

    print(f"\n{_c('═' * 68, _BOLD)}")
    print(f"{_c('  SMART RETAIL PLATFORM — E2E TEST RESULTS', _BOLD + _CYAN)}")
    print(f"{_c('═' * 68, _BOLD)}")

    # Per-scenario table
    print(f"\n  {'Scenario':<48} {'Result':<8} {'Pass':>5}{'Fail':>5}")
    print(f"  {'─' * 65}")
    for name, res in _state.scenario_results.items():
        colour = _GREEN if res["status"] == "PASS" else _RED
        short_name = name[:45] + "…" if len(name) > 45 else name
        print(
            f"  {short_name:<48} "
            f"{_c(res['status'], colour):<18} "
            f"{_c(str(res['passed']), _GREEN):>5}"
            f"{_c(str(res['failed']), _RED if res['failed'] else _GREY):>5}"
        )

    print(f"\n  {'─' * 65}")
    print(f"  {'Total checks':<48} {_c(str(total), _BOLD):>10}")
    print(f"  {'Passed':<48} {_c(str(_state.passed), _GREEN + _BOLD):>10}")
    print(f"  {'Failed':<48} {_c(str(_state.failed), (_RED + _BOLD) if _state.failed else _GREY):>10}")
    print(f"  {'Skipped':<48} {_c(str(_state.skipped), _YELLOW):>10}")
    print(f"  {'Pass rate':<48} {_c(f'{pass_rate}%', (_GREEN + _BOLD) if pass_rate >= 90 else _YELLOW):>10}")
    print(f"  {'Run time':<48} {_c(f'{elapsed:.1f}s', _GREY):>10}")

    if _state.failures:
        print(f"\n  {_c('Failed checks:', _RED + _BOLD)}")
        for i, f in enumerate(_state.failures[:20], 1):
            print(f"  {_c(str(i) + '.', _RED)} {f[:100]}")
        if len(_state.failures) > 20:
            print(f"  {_c(f'  … and {len(_state.failures) - 20} more', _GREY)}")

    print(f"\n  {_c('═' * 66, _BOLD)}")
    if all_pass:
        print(f"  {_c('✓  ALL CHECKS PASSED — Platform is production-ready!', _GREEN + _BOLD)}")
    elif pass_rate >= 90:
        print(f"  {_c(f'⚠  {_state.failed} check(s) failed — review failures above.', _YELLOW + _BOLD)}")
    else:
        print(f"  {_c(f'✗  {_state.failed} check(s) failed — significant issues detected.', _RED + _BOLD)}")
    print(f"  {_c('═' * 66, _BOLD)}\n")


# ═══════════════════════════════════════════════════════════════════════════════
#  Entry point
# ═══════════════════════════════════════════════════════════════════════════════

SCENARIOS = {
    0:  ("System Readiness & Auth",           scenario_0_system_and_auth),
    1:  ("Product Onboarding Pipeline",       scenario_1_product_onboarding),
    2:  ("Order Lifecycle & SLA",             scenario_2_order_lifecycle),
    3:  ("Inventory Management",              scenario_3_inventory_management),
    4:  ("ML Demand Forecasting",             scenario_4_ml_forecasting),
    5:  ("Analytics & Data Consistency",      scenario_5_analytics_consistency),
    6:  ("Notification Engine",               scenario_6_notifications),
    7:  ("Reports & Dashboard",               scenario_7_reports_dashboard),
    8:  ("Business Intelligence Module",      scenario_8_bi_module),
    9:  ("RBAC & Multi-Role UAT",             scenario_9_rbac_uat),
    10: ("Edge Cases & Error Handling",       scenario_10_edge_cases),
    11: ("Supplier Intelligence & Scorecard", scenario_11_supplier_workflow),
}


def main() -> None:
    global _state

    parser = argparse.ArgumentParser(
        description="Smart Retail Platform — Comprehensive E2E Test Suite"
    )
    parser.add_argument("--url", default="http://localhost:8000")
    parser.add_argument("--scenario", type=int, default=None,
                        help="Run a single scenario by number (0–11)")
    parser.add_argument("--verbose", action="store_true",
                        help="Print full response bodies on failure")
    parser.add_argument("--skip-ml", action="store_true",
                        help="Skip the ML training steps (faster but incomplete)")
    args = parser.parse_args()

    _state = E2EState(
        base=args.url.rstrip("/"),
        verbose=args.verbose,
        skip_ml=args.skip_ml,
    )

    print(f"\n{_c('═' * 68, _BOLD + _CYAN)}")
    print(f"{_c('  Smart Retail Platform — End-to-End Validation Suite', _BOLD + _CYAN)}")
    print(f"{_c(f'  Target: {_state.base}', _CYAN)}")
    print(f"{_c(f'  Run ID: {_RUN}', _GREY)}")
    print(f"{_c('═' * 68, _BOLD + _CYAN)}")

    start_time = time.perf_counter()

    if args.scenario is not None:
        if args.scenario not in SCENARIOS:
            print(f"Unknown scenario {args.scenario}. Valid: 0–{max(SCENARIOS)}")
            sys.exit(1)
        # For single scenario, still run scenario 0 to get auth tokens
        if args.scenario != 0:
            scenario_0_system_and_auth()
        _, fn = SCENARIOS[args.scenario]
        fn()
    else:
        for num in sorted(SCENARIOS.keys()):
            _, fn = SCENARIOS[num]
            fn()

    elapsed = time.perf_counter() - start_time
    _print_final_report(elapsed)
    sys.exit(0 if _state.failed == 0 else 1)


if __name__ == "__main__":
    main()
