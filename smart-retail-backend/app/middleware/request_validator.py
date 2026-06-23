"""
Request Size Limiting Middleware
==================================
Rejects any request whose Content-Length exceeds MAX_REQUEST_SIZE_BYTES.

This prevents:
  - Denial-of-service via enormous JSON bodies
  - Memory exhaustion attacks

Configuration (app/core/config.py):
  MAX_REQUEST_SIZE_BYTES = 1_048_576  (1 MB default)

Note: Only the Content-Length header is checked.  Requests without
Content-Length (e.g. chunked transfer) are allowed through; the body
is never read here to avoid buffering issues.
"""

from fastapi import Request, status
from fastapi.responses import JSONResponse

from app.core.config import settings


async def request_size_middleware(request: Request, call_next):
    """Reject oversized requests before the body is processed."""
    content_length_header = request.headers.get("content-length")

    if content_length_header is not None:
        try:
            content_length = int(content_length_header)
        except ValueError:
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={
                    "status": "error",
                    "detail": "Invalid Content-Length header.",
                    "code": "INVALID_CONTENT_LENGTH",
                },
            )

        if content_length > settings.MAX_REQUEST_SIZE_BYTES:
            max_mb = settings.MAX_REQUEST_SIZE_BYTES / (1024 * 1024)
            return JSONResponse(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                content={
                    "status": "error",
                    "detail": f"Request body exceeds the {max_mb:.1f} MB limit.",
                    "code": "REQUEST_TOO_LARGE",
                },
            )

    return await call_next(request)
