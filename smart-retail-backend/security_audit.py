#!/usr/bin/env python3
"""
Smart Retail Platform — Security Audit
========================================
Automated validation of OWASP Top-10 protections, authentication hardening,
input sanitization, and rate limiting behaviour.

Usage:
    python security_audit.py [--url http://localhost:8000]

Exit codes:
    0   All checks passed
    1   One or more checks failed (details printed)
"""

import argparse
import json
import sys
import time

try:
    import requests
except ImportError:
    sys.exit("requests is not installed. Run: pip install requests")

# ── Colour helpers ────────────────────────────────────────────────────────────

_RESET  = "\033[0m"
_BOLD   = "\033[1m"
_GREEN  = "\033[92m"
_YELLOW = "\033[93m"
_RED    = "\033[91m"
_CYAN   = "\033[96m"
_GREY   = "\033[90m"

def _ok(msg: str)   -> str: return f"  \033[92m✓\033[0m  {msg}"
def _fail(msg: str) -> str: return f"  \033[91m✗\033[0m  {msg}"
def _warn(msg: str) -> str: return f"  \033[93m!\033[0m  {msg}"
def _head(msg: str) -> str: return f"\n\033[1m\033[96m{'─'*60}\n  {msg}\n{'─'*60}\033[0m"


# ── Audit state ───────────────────────────────────────────────────────────────

_passed = 0
_failed = 0
_warnings = 0


def _check(condition: bool, pass_msg: str, fail_msg: str, warn: bool = False) -> bool:
    global _passed, _failed, _warnings
    if condition:
        print(_ok(pass_msg))
        _passed += 1
        return True
    else:
        if warn:
            print(_warn(fail_msg))
            _warnings += 1
        else:
            print(_fail(fail_msg))
            _failed += 1
        return False


# ── Test groups ───────────────────────────────────────────────────────────────

def test_security_headers(base: str) -> None:
    print(_head("A — Security Headers (OWASP A05)"))
    r = requests.get(f"{base}/health", timeout=10)
    h = r.headers

    _check("X-Content-Type-Options" in h,
           "X-Content-Type-Options header present",
           "X-Content-Type-Options MISSING — MIME sniffing not prevented")
    _check(h.get("X-Content-Type-Options") == "nosniff",
           "X-Content-Type-Options: nosniff",
           f"X-Content-Type-Options unexpected value: {h.get('X-Content-Type-Options')}")
    _check("X-Frame-Options" in h,
           "X-Frame-Options header present",
           "X-Frame-Options MISSING — clickjacking not prevented")
    _check("Strict-Transport-Security" in h,
           "Strict-Transport-Security (HSTS) present",
           "HSTS header MISSING",
           warn=True)
    _check("Content-Security-Policy" in h,
           "Content-Security-Policy present",
           "CSP header MISSING")
    _check("Referrer-Policy" in h,
           "Referrer-Policy present",
           "Referrer-Policy MISSING",
           warn=True)
    _check("Permissions-Policy" in h,
           "Permissions-Policy present",
           "Permissions-Policy MISSING",
           warn=True)

    server = h.get("Server", "")
    _check(not any(x in server.lower() for x in ("uvicorn", "python", "nginx/", "apache")),
           f"Server header does not expose technology stack ({server!r})",
           f"Server header reveals technology: {server!r}",
           warn=True)
    _check("X-RateLimit-Limit" in h or "X-Ratelimit-Limit" in h,
           "X-RateLimit-Limit header present on responses",
           "Rate-limit headers not returned",
           warn=True)


def test_authentication(base: str, admin_token: str) -> None:
    print(_head("B — Authentication & JWT (OWASP A07)"))
    s = requests.Session()

    # Missing auth header
    r = s.get(f"{base}/api/products/", timeout=10)
    _check(r.status_code == 401,
           "Missing auth → 401 Unauthorized",
           f"Missing auth returned {r.status_code} (expected 401)")

    # Completely invalid token
    r = s.get(f"{base}/api/products/",
              headers={"Authorization": "Bearer not-a-real-token"}, timeout=10)
    _check(r.status_code == 401,
           "Garbage token → 401 Unauthorized",
           f"Garbage token returned {r.status_code}")

    # Expired-looking token (structurally valid but wrong secret)
    fake_token = (
        "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
        "eyJzdWIiOiJoYWNrZXIiLCJyb2xlIjoiYWRtaW4iLCJ0eXBlIjoiYWNjZXNzIn0."
        "INVALID_SIGNATURE_HERE"
    )
    r = s.get(f"{base}/api/products/",
              headers={"Authorization": f"Bearer {fake_token}"}, timeout=10)
    _check(r.status_code == 401,
           "Token with wrong signature → 401 Unauthorized",
           f"Token with wrong signature returned {r.status_code}")

    # Refresh token used as access token (token type enforcement)
    # First get a real refresh token
    login = s.post(f"{base}/api/auth/login",
                   json={"username": "admin", "password": "Admin@123"}, timeout=10)
    if login.status_code == 200:
        refresh_token = login.json().get("refresh_token", "")
        r = s.get(f"{base}/api/products/",
                  headers={"Authorization": f"Bearer {refresh_token}"}, timeout=10)
        _check(r.status_code == 401,
               "Refresh token used as access token → 401 (token-type check)",
               f"Refresh token used as access token returned {r.status_code} (expected 401)")
    else:
        print(_warn("Could not login as admin — skipping refresh-token-type check"))

    # Empty Authorization header value
    r = s.get(f"{base}/api/products/",
              headers={"Authorization": "Bearer "}, timeout=10)
    _check(r.status_code in (401, 403, 422),
           "Empty Bearer token → 4xx",
           f"Empty Bearer token returned {r.status_code}")


def test_rbac(base: str) -> None:
    print(_head("C — Role-Based Access Control (OWASP A01)"))
    s = requests.Session()

    # Login as analyst
    login = s.post(f"{base}/api/auth/login",
                   json={"username": "analyst", "password": "Analyst@123"}, timeout=10)
    if login.status_code != 200:
        print(_warn("Could not login as analyst — skipping RBAC checks (run demo_seed.py first)"))
        return

    analyst_token = login.json()["access_token"]
    analyst_headers = {"Authorization": f"Bearer {analyst_token}"}

    # Analyst should NOT be able to delete a product
    r = s.delete(f"{base}/api/products/1", headers=analyst_headers, timeout=10)
    _check(r.status_code == 403,
           "Analyst cannot DELETE /api/products/1 → 403 Forbidden",
           f"Analyst DELETE product returned {r.status_code} (expected 403)")

    # Analyst should NOT be able to create a product
    r = s.post(f"{base}/api/products/",
               headers={**analyst_headers, "Content-Type": "application/json"},
               json={"product_name": "Hack", "sku": "HACK-001", "unit_price": 1.0},
               timeout=10)
    _check(r.status_code == 403,
           "Analyst cannot POST /api/products/ → 403 Forbidden",
           f"Analyst POST product returned {r.status_code} (expected 403)")

    # Analyst should NOT be able to create orders
    r = s.post(f"{base}/api/orders/",
               headers={**analyst_headers, "Content-Type": "application/json"},
               json={"order_number": "HACK-001", "product_id": 1, "quantity": 1, "total_amount": 1},
               timeout=10)
    _check(r.status_code == 403,
           "Analyst cannot POST /api/orders/ → 403 Forbidden",
           f"Analyst POST order returned {r.status_code} (expected 403)")

    # Analyst CAN read products
    r = s.get(f"{base}/api/products/", headers=analyst_headers, timeout=10)
    _check(r.status_code == 200,
           "Analyst CAN read /api/products/ → 200 OK",
           f"Analyst GET products returned {r.status_code} (expected 200)")


def test_input_validation(base: str, admin_token: str) -> None:
    print(_head("D — Input Validation & Injection Prevention (OWASP A03)"))
    s = requests.Session()
    auth_headers = {
        "Authorization": f"Bearer {admin_token}",
        "Content-Type": "application/json",
    }

    # SQL injection attempt in supplier name
    r = s.post(
        f"{base}/api/suppliers/",
        headers=auth_headers,
        json={
            "supplier_name": "'; DROP TABLE suppliers; --",
            "email": "evil@example.com",
        },
        timeout=10,
    )
    _check(r.status_code in (400, 422),
           "SQL injection in supplier_name → 400/422 rejected",
           f"SQL injection in supplier_name returned {r.status_code} (expected 400/422 — check sanitizer)")

    # XSS in product name
    r = s.post(
        f"{base}/api/products/",
        headers=auth_headers,
        json={
            "product_name": "<script>alert('xss')</script>",
            "sku": "XSS-001",
            "unit_price": 10.0,
        },
        timeout=10,
    )
    _check(r.status_code in (400, 422),
           "XSS script tag in product_name → 400/422 rejected",
           f"XSS in product_name returned {r.status_code} (check validator)")

    # Invalid SKU characters
    r = s.post(
        f"{base}/api/products/",
        headers=auth_headers,
        json={
            "product_name": "Test Product",
            "sku": "SKU WITH SPACES!",
            "unit_price": 10.0,
        },
        timeout=10,
    )
    _check(r.status_code == 422,
           "Invalid SKU characters → 422 Validation Error",
           f"Invalid SKU returned {r.status_code} (expected 422)")

    # Negative unit price
    r = s.post(
        f"{base}/api/products/",
        headers=auth_headers,
        json={"product_name": "Test", "sku": "TEST-001", "unit_price": -50.0},
        timeout=10,
    )
    _check(r.status_code == 422,
           "Negative unit_price → 422 Validation Error",
           f"Negative price returned {r.status_code} (expected 422)")

    # Weak password on registration
    r = s.post(
        f"{base}/api/auth/register",
        headers={"Content-Type": "application/json"},
        json={"username": "weakuser", "email": "weak@example.com", "password": "password"},
        timeout=10,
    )
    _check(r.status_code == 422,
           "Weak password 'password' → 422 Validation Error",
           f"Weak password returned {r.status_code} (expected 422 — check password validator)")


def test_rate_limiting(base: str) -> None:
    print(_head("E — Rate Limiting (OWASP A04 / DoS Prevention)"))
    s = requests.Session()

    # Fire 8 rapid login attempts — should hit the 5/min limit
    hit_429 = False
    for i in range(8):
        r = s.post(
            f"{base}/api/auth/login",
            json={"username": "nonexistent_user_for_rate_test", "password": "Wrong@Pass1"},
            timeout=10,
        )
        if r.status_code == 429:
            hit_429 = True
            break

    _check(hit_429,
           "Login endpoint rate-limited after rapid requests → 429 Too Many Requests",
           "Rate limit NOT triggered on login (check rate_limiter middleware is registered)")

    if hit_429:
        has_retry_after = "retry-after" in {k.lower() for k in r.headers}
        _check(has_retry_after,
               "429 response includes Retry-After header",
               "429 response missing Retry-After header",
               warn=True)


def test_request_size(base: str) -> None:
    print(_head("F — Request Size Limit (DoS Prevention)"))
    s = requests.Session()

    # Send a 2 MB body
    large_body = "x" * (2 * 1024 * 1024)
    r = s.post(
        f"{base}/api/auth/login",
        data=large_body,
        headers={"Content-Type": "application/json", "Content-Length": str(len(large_body))},
        timeout=15,
    )
    _check(r.status_code == 413,
           "2 MB body → 413 Request Entity Too Large",
           f"2 MB body returned {r.status_code} (expected 413 — check request_validator middleware)")


def test_information_disclosure(base: str) -> None:
    print(_head("G — Information Disclosure (OWASP A05)"))
    s = requests.Session()

    # Non-existent route should return 404, not a stack trace
    r = s.get(f"{base}/nonexistent-route-12345", timeout=10)
    _check(r.status_code == 404,
           "Non-existent route → 404",
           f"Non-existent route returned {r.status_code}")

    # Check 404 body does not contain stack trace keywords
    body = r.text.lower()
    no_traceback = "traceback" not in body and "file \"" not in body
    _check(no_traceback,
           "404 body does not expose stack trace",
           "404 body may contain stack trace — review error handler")

    # Check 401 body does not expose internal details
    r = s.get(f"{base}/api/products/", timeout=10)
    body = r.text.lower()
    no_internal = "sqlalchemy" not in body and "traceback" not in body
    _check(no_internal,
           "401 body does not expose internal framework details",
           "401 body may expose internal details")

    # Ensure CORS wildcard is not set in production mode
    r = s.options(f"{base}/api/products/", timeout=10)
    acao = r.headers.get("Access-Control-Allow-Origin", "")
    _check(acao != "*",
           f"CORS is not wildcard (*) — Access-Control-Allow-Origin: {acao!r}",
           "CORS allows all origins (*) — restrict in production",
           warn=True)


def test_health_endpoints(base: str) -> None:
    print(_head("H — Health & Readiness Endpoints"))
    s = requests.Session()

    r = s.get(f"{base}/health", timeout=10)
    _check(r.status_code == 200,
           "GET /health → 200 OK (liveness)",
           f"GET /health returned {r.status_code}")

    if r.status_code == 200:
        data = r.json()
        _check(data.get("status") == "healthy",
               "Liveness probe reports status=healthy",
               f"Liveness probe status: {data.get('status')}")


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Smart Retail Security Audit")
    parser.add_argument("--url", default="http://localhost:8000")
    args = parser.parse_args()
    base = args.url.rstrip("/")

    print(f"\n\033[1m\033[96m{'═' * 62}")
    print("  Smart Retail Platform — Security Audit")
    print(f"  Target: {base}")
    print(f"{'═' * 62}\033[0m")

    # Obtain admin token once for all tests
    admin_token = ""
    try:
        r = requests.post(
            f"{base}/api/auth/login",
            json={"username": "admin", "password": "Admin@123"},
            timeout=10,
        )
        if r.status_code == 200:
            admin_token = r.json().get("access_token", "")
            print(f"\n  Admin login: \033[92mOK\033[0m (token obtained)")
        else:
            print(f"\n  Admin login: \033[93mFAILED ({r.status_code})\033[0m — some checks will be skipped")
    except Exception as exc:
        print(f"\n  Could not reach server at {base}: {exc}")
        sys.exit(2)

    # Run all test groups
    test_security_headers(base)
    test_authentication(base, admin_token)
    test_rbac(base)
    test_input_validation(base, admin_token)
    test_rate_limiting(base)
    test_request_size(base)
    test_information_disclosure(base)
    test_health_endpoints(base)

    # ── Summary ──────────────────────────────────────────────────────────────
    total = _passed + _failed + _warnings
    print(f"\n\033[1m{'─' * 62}")
    print(f"  Results: {total} checks")
    print(f"  \033[92m✓ Passed:   {_passed}\033[0m")
    if _warnings:
        print(f"  \033[93m! Warnings: {_warnings}\033[0m")
    if _failed:
        print(f"  \033[91m✗ Failed:   {_failed}\033[0m")
    print(f"{'─' * 62}\033[0m")

    if _failed == 0:
        print(f"\n  \033[1m\033[92mAll security checks passed.\033[0m\n")
        sys.exit(0)
    else:
        print(f"\n  \033[1m\033[91m{_failed} check(s) failed — review items marked ✗ above.\033[0m\n")
        sys.exit(1)


if __name__ == "__main__":
    main()
