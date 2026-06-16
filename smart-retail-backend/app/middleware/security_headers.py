"""
Security Headers Middleware
============================
Applies OWASP-recommended HTTP security headers to every response.
Protects against clickjacking, MIME sniffing, XSS, and information disclosure.
"""

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """
    Injects security headers on every HTTP response.

    OWASP Top-10 mitigations covered:
      A05 Security Misconfiguration  — X-Content-Type-Options, X-Frame-Options
      A03 Injection / XSS            — X-XSS-Protection, CSP (docs excluded)
      A02 Cryptographic Failures     — Strict-Transport-Security (HTTPS hint)
      Info disclosure                — Server header removed, Cache-Control
    """

    # Paths that need a relaxed CSP (Swagger/ReDoc inline scripts & styles)
    _DOCS_PREFIXES = ("/docs", "/redoc", "/openapi.json")

    async def dispatch(self, request: Request, call_next) -> Response:
        response: Response = await call_next(request)

        is_docs = request.url.path.startswith(self._DOCS_PREFIXES)

        # Prevent MIME-type sniffing
        response.headers["X-Content-Type-Options"] = "nosniff"

        # Block iframe embedding (clickjacking)
        response.headers["X-Frame-Options"] = "DENY"

        # Legacy XSS filter (IE/old Chrome)
        response.headers["X-XSS-Protection"] = "1; mode=block"

        # Control referrer information
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"

        # Hint to browsers to only use HTTPS
        response.headers["Strict-Transport-Security"] = (
            "max-age=31536000; includeSubDomains"
        )

        # Suppress server version disclosure
        response.headers["Server"] = "SmartRetail"

        # No caching for API responses (docs pages may cache assets)
        if not is_docs:
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
            response.headers["Pragma"] = "no-cache"

        # Content-Security-Policy — relaxed for Swagger/ReDoc
        if is_docs:
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; "
                "script-src 'self' 'unsafe-inline' cdn.jsdelivr.net; "
                "style-src 'self' 'unsafe-inline' cdn.jsdelivr.net; "
                "img-src 'self' data:;"
            )
        else:
            response.headers["Content-Security-Policy"] = (
                "default-src 'none'; frame-ancestors 'none';"
            )

        # Permissions policy — disable unnecessary browser features
        response.headers["Permissions-Policy"] = (
            "camera=(), microphone=(), geolocation=(), payment=()"
        )

        return response
