from fastapi import Request, status
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
import time
import uuid
import logging
import json
from datetime import datetime, timezone

# ── Structured JSON logger ────────────────────────────────────────────────────

class _JSONFormatter(logging.Formatter):
    """Emit log records as single-line JSON for easy parsing by log aggregators."""

    def format(self, record: logging.LogRecord) -> str:
        log: dict = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            log["exc"] = self.formatException(record.exc_info)
        for key in ("request_id", "method", "path", "status_code", "duration_ms"):
            if hasattr(record, key):
                log[key] = getattr(record, key)
        return json.dumps(log)


def _configure_logging(level: str = "INFO"):
    handler = logging.StreamHandler()
    handler.setFormatter(_JSONFormatter())
    root = logging.getLogger()
    if not root.handlers:
        root.addHandler(handler)
    root.setLevel(getattr(logging, level.upper(), logging.INFO))


_configure_logging()
logger = logging.getLogger("smart_retail")


# ── Request/Response logging middleware ───────────────────────────────────────

async def logging_middleware(request: Request, call_next):
    """Attach a request ID, log every request and response with duration."""
    request_id = str(uuid.uuid4())[:8]
    request.state.request_id = request_id
    start = time.perf_counter()

    extra = {"request_id": request_id, "method": request.method, "path": request.url.path}
    logger.info("Incoming request", extra=extra)

    response = await call_next(request)

    duration_ms = round((time.perf_counter() - start) * 1000, 2)
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Process-Time-Ms"] = str(duration_ms)

    logger.info(
        "Request completed",
        extra={**extra, "status_code": response.status_code, "duration_ms": duration_ms},
    )
    return response


# ── Global exception middleware ───────────────────────────────────────────────

async def error_handling_middleware(request: Request, call_next):
    """Catch any unhandled exception and return a sanitized 500 response."""
    try:
        return await call_next(request)
    except Exception as exc:
        request_id = getattr(request.state, "request_id", "unknown")
        logger.error(
            f"Unhandled exception: {type(exc).__name__}: {exc}",
            exc_info=True,
            extra={"request_id": request_id, "path": request.url.path},
        )
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "status": "error",
                "detail": "An internal server error occurred.",
                "code": "INTERNAL_SERVER_ERROR",
                "request_id": request_id,
            },
        )


# ── Validation error handler ──────────────────────────────────────────────────

async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """Return structured 422 with per-field error details."""
    request_id = getattr(request.state, "request_id", "unknown")
    errors = []
    for e in exc.errors():
        field = " → ".join(str(loc) for loc in e.get("loc", []))
        errors.append({"field": field, "message": e.get("msg", ""), "type": e.get("type", "")})

    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={
            "status": "error",
            "detail": "Request validation failed.",
            "code": "VALIDATION_ERROR",
            "errors": errors,
            "request_id": request_id,
        },
    )


# ── HTTP exception handler ────────────────────────────────────────────────────

async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    """Normalise all HTTPExceptions into the standard error envelope."""
    request_id = getattr(request.state, "request_id", "unknown")
    code_map = {
        400: "BAD_REQUEST",
        401: "UNAUTHORIZED",
        403: "FORBIDDEN",
        404: "NOT_FOUND",
        405: "METHOD_NOT_ALLOWED",
        409: "CONFLICT",
        429: "RATE_LIMITED",
        500: "INTERNAL_SERVER_ERROR",
        501: "NOT_IMPLEMENTED",
    }
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "status": "error",
            "detail": exc.detail,
            "code": code_map.get(exc.status_code, "HTTP_ERROR"),
            "request_id": request_id,
        },
    )

