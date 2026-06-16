"""
Standardized API Response Utilities
=====================================
All endpoints use these helpers to ensure a consistent JSON envelope:

  Success:  {"status": "success", "data": {...}, "timestamp": "..."}
  Paginated:{"status": "success", "data": [...], "pagination": {...}, "timestamp": "..."}
  Error:    {"status": "error",   "detail": "...", "code": "...", "timestamp": "..."}
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def success(
    data: Any,
    message: str = "OK",
    meta: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Wrap a successful payload in the standard envelope."""
    response = {
        "status": "success",
        "message": message,
        "data": data,
        "timestamp": _now(),
    }
    if meta:
        response["meta"] = meta
    return response


def paginated(
    data: List[Any],
    total: int,
    skip: int = 0,
    limit: int = 100,
    message: str = "OK",
) -> Dict[str, Any]:
    """Wrap a paginated list in the standard envelope."""
    return {
        "status": "success",
        "message": message,
        "data": data,
        "pagination": {
            "total": total,
            "skip": skip,
            "limit": limit,
            "returned": len(data),
            "has_more": (skip + len(data)) < total,
        },
        "timestamp": _now(),
    }


def error(
    detail: str,
    code: Optional[str] = None,
    errors: Optional[List[Any]] = None,
) -> Dict[str, Any]:
    """Wrap an error in the standard envelope."""
    response: Dict[str, Any] = {
        "status": "error",
        "detail": detail,
        "timestamp": _now(),
    }
    if code:
        response["code"] = code
    if errors:
        response["errors"] = errors
    return response


# Pre-built common error responses
UNAUTHORIZED   = error("Authentication required.",       code="UNAUTHORIZED")
FORBIDDEN      = error("Insufficient permissions.",      code="FORBIDDEN")
NOT_FOUND      = error("Resource not found.",            code="NOT_FOUND")
INTERNAL_ERROR = error("An internal error occurred.",   code="INTERNAL_SERVER_ERROR")
