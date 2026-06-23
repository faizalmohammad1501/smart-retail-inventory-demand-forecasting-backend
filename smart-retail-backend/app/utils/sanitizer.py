"""
Input Sanitization Utilities
==============================
Defence-in-depth helpers used alongside Pydantic validation.

The SQLAlchemy ORM already prevents SQL injection via parameterised queries.
These utilities add a second layer for:
  - Stripping null bytes and control characters from strings
  - Removing HTML/script tags before storage
  - Detecting and rejecting obvious injection payloads in free-text fields
  - Masking sensitive values in log records

Usage:
    from app.utils.sanitizer import sanitize_string, mask_sensitive, is_injection_attempt

    clean = sanitize_string(user_input, max_length=255)
    if is_injection_attempt(clean):
        raise HTTPException(400, "Invalid input")
"""

import re
import html
import logging
from typing import Any

logger = logging.getLogger("smart_retail.sanitizer")

# ── Compiled patterns (built once at import time) ─────────────────────────────

_CONTROL_CHARS    = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_NULL_BYTES       = re.compile(r"\x00")
_SCRIPT_FULL      = re.compile(r"<\s*script[^>]*>.*?<\s*/\s*script\s*>", re.IGNORECASE | re.DOTALL)
_HTML_TAGS        = re.compile(r"<[^>]+>")
_MULTI_WHITESPACE = re.compile(r"\s{2,}")

# Patterns indicative of SQL injection in free-text (not query params — ORM handles those)
_SQL_INJECTION = re.compile(
    r"(\b(UNION|SELECT|INSERT|UPDATE|DELETE|DROP|TRUNCATE|ALTER|EXEC|EXECUTE|"
    r"CAST|CONVERT|DECLARE|xp_|sp_|--\s*$|;\s*(DROP|SELECT|INSERT|UPDATE|DELETE))\b"
    r"|(\d+\s*=\s*\d+))",
    re.IGNORECASE,
)

# Patterns indicative of XSS
_XSS_PATTERNS = re.compile(
    r"(javascript\s*:|vbscript\s*:|on\w+\s*=|<\s*iframe|<\s*object|<\s*embed)",
    re.IGNORECASE,
)

# Sensitive field names — values are masked in logs
_SENSITIVE_KEYS = frozenset(
    {"password", "hashed_password", "token", "access_token", "refresh_token",
     "secret", "key", "credential", "api_key", "auth", "authorization"}
)


# ── Core function ─────────────────────────────────────────────────────────────

def sanitize_string(
    value: str,
    max_length: int = 0,
    strip_html: bool = True,
) -> str:
    """
    Return a cleaned version of *value*:
      1. Strip null bytes and ASCII control characters
      2. Optionally strip HTML/script tags
      3. Trim leading/trailing whitespace
      4. Collapse internal duplicate whitespace
      5. Truncate to *max_length* if specified

    Does NOT raise — returns the sanitized string. Callers decide what to do
    with the result (e.g. compare against original to detect tampering).
    """
    if not isinstance(value, str):
        return value

    value = _NULL_BYTES.sub("", value)
    value = _CONTROL_CHARS.sub("", value)

    if strip_html:
        value = _SCRIPT_FULL.sub("", value)
        value = _HTML_TAGS.sub("", value)

    # Decode HTML entities like &lt; → < then re-strip (second-pass defence)
    value = html.unescape(value)
    if strip_html:
        value = _HTML_TAGS.sub("", value)

    value = value.strip()
    value = _MULTI_WHITESPACE.sub(" ", value)

    if max_length and len(value) > max_length:
        value = value[:max_length]

    return value


def is_injection_attempt(value: str) -> bool:
    """
    Returns True if *value* matches known SQL injection or XSS patterns.

    Only use this on free-text string inputs that will be stored or rendered.
    Structured fields (IDs, enums, numbers) are validated by Pydantic before
    reaching this layer.
    """
    if not isinstance(value, str):
        return False
    if _SQL_INJECTION.search(value):
        logger.warning("Possible SQL injection pattern detected in input")
        return True
    if _XSS_PATTERNS.search(value):
        logger.warning("Possible XSS pattern detected in input")
        return True
    return False


def mask_sensitive(data: dict, extra_keys: list[str] | None = None) -> dict:
    """
    Return a copy of *data* with sensitive values replaced by '***'.

    Useful for safe logging of request payloads:
        logger.info("Payload: %s", mask_sensitive(payload))
    """
    keys_to_mask = _SENSITIVE_KEYS | set(k.lower() for k in (extra_keys or []))
    return {
        k: "***" if any(s in k.lower() for s in keys_to_mask) else v
        for k, v in data.items()
    }


def sanitize_query_param(value: str, param_name: str = "param") -> str:
    """
    Sanitize a single query-string parameter value.
    Raises ValueError if injection is detected (so callers can return 400).
    """
    cleaned = sanitize_string(value, max_length=500)
    if is_injection_attempt(cleaned):
        raise ValueError(f"Invalid value for query parameter '{param_name}'")
    return cleaned
