"""
Audit Log API Routes
=====================
Provides read-only access to the platform's immutable audit trail.

All endpoints require authentication.
  - GET endpoints require role: admin or analyst
  - DELETE /purge requires role: admin

Endpoints:
  GET  /api/audit/logs                 — paginated log with filters
  GET  /api/audit/logs/export          — CSV download
  GET  /api/audit/summary              — counts by event/user/path/severity
  GET  /api/audit/user/{user_id}       — activity history for a user
  GET  /api/audit/integrity/{log_id}   — verify checksum of a single record
  DELETE /api/audit/purge              — delete records older than N days (admin)
"""

import logging
from datetime import datetime
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_active_user, require_roles
from app.database.connection import get_db
from app.models.user import User
from app.services import audit_service

logger = logging.getLogger("smart_retail.audit_routes")

router = APIRouter(prefix="/api/audit", tags=["Audit Logs & Observability"])


# ── GET /api/audit/logs ───────────────────────────────────────────────────────

@router.get("/logs", status_code=status.HTTP_200_OK)
def list_audit_logs(
    event_type:    Optional[str] = Query(None, description="AUTH | CRUD | ML | ANALYTICS | SECURITY | SYSTEM | ALERT | EXPORT"),
    action:        Optional[str] = Query(None, description="LOGIN | CREATE | UPDATE | DELETE | TRAIN | FORECAST …"),
    username:      Optional[str] = Query(None),
    user_id:       Optional[int] = Query(None),
    resource_type: Optional[str] = Query(None, description="product | order | supplier | user | ml_model …"),
    resource_id:   Optional[int] = Query(None),
    severity:      Optional[str] = Query(None, description="INFO | WARNING | ERROR | CRITICAL"),
    path_contains: Optional[str] = Query(None, description="Substring match on the request path"),
    start_date:    Optional[datetime] = Query(None),
    end_date:      Optional[datetime] = Query(None),
    skip:          int = Query(0, ge=0),
    limit:         int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
    _: User = Depends(require_roles("admin", "analyst")),
) -> Dict[str, Any]:
    """
    Paginated audit log with comprehensive filtering.

    Returns records newest-first.  Use `skip` + `limit` for pagination.
    All filter parameters are optional and combinable.
    """
    return audit_service.get_audit_logs(
        db,
        event_type    = event_type,
        action        = action,
        username      = username,
        user_id       = user_id,
        resource_type = resource_type,
        resource_id   = resource_id,
        severity      = severity,
        path_contains = path_contains,
        start_dt      = start_date,
        end_dt        = end_date,
        skip          = skip,
        limit         = limit,
    )


# ── GET /api/audit/logs/export ────────────────────────────────────────────────

@router.get("/logs/export", status_code=status.HTTP_200_OK)
def export_audit_logs(
    event_type: Optional[str] = Query(None),
    start_date: Optional[datetime] = Query(None),
    end_date:   Optional[datetime] = Query(None),
    db: Session = Depends(get_db),
    _: User = Depends(require_roles("admin")),
) -> Response:
    """
    Download the audit log as a CSV file (max 10 000 records).

    Requires role: admin
    """
    csv_data = audit_service.export_audit_csv(
        db,
        start_dt   = start_date,
        end_dt     = end_date,
        event_type = event_type,
    )
    return Response(
        content     = csv_data,
        media_type  = "text/csv",
        headers     = {
            "Content-Disposition": "attachment; filename=audit_log.csv",
        },
    )


# ── GET /api/audit/summary ────────────────────────────────────────────────────

@router.get("/summary", status_code=status.HTTP_200_OK)
def audit_summary(
    days: int = Query(30, ge=1, le=365, description="Look-back window in days"),
    db: Session = Depends(get_db),
    _: User = Depends(require_roles("admin", "analyst")),
) -> Dict[str, Any]:
    """
    Aggregated audit statistics for the monitoring dashboard.

    Returns:
      - total_events, auth_failures, security_events
      - avg_response_ms
      - by_event_type, by_severity (count maps)
      - top_users (10 most active)
      - top_paths (10 most requested endpoints)
    """
    return audit_service.get_audit_summary(db, days=days)


# ── GET /api/audit/user/{user_id} ─────────────────────────────────────────────

@router.get("/user/{user_id}", status_code=status.HTTP_200_OK)
def user_activity(
    user_id: int,
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles("admin", "analyst")),
) -> Dict[str, Any]:
    """
    Retrieve the audit trail for a specific user.

    Useful for investigating anomalous activity or generating per-user
    compliance reports.
    """
    return audit_service.get_user_activity(db, user_id=user_id, limit=limit)


# ── GET /api/audit/integrity/{log_id} ────────────────────────────────────────

@router.get("/integrity/{log_id}", status_code=status.HTTP_200_OK)
def verify_integrity(
    log_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(require_roles("admin")),
) -> Dict[str, Any]:
    """
    Verify the SHA-256 checksum of a single audit record.

    The checksum was computed at write time as:
        SHA-256(username | action | resource_type | resource_id | timestamp)

    Returns whether the stored checksum matches the recomputed value,
    providing tamper detection for individual records.
    """
    import hashlib
    from app.models.audit_log import AuditLog

    record = db.query(AuditLog).filter(AuditLog.id == log_id).first()
    if not record:
        raise HTTPException(status_code=404, detail=f"Audit record {log_id} not found")

    ts = record.timestamp.isoformat() if record.timestamp else ""
    raw = (
        f"{record.username or ''}|{record.action or ''}|"
        f"{record.resource_type or ''}|{record.resource_id}|{ts}"
    )
    expected = hashlib.sha256(raw.encode()).hexdigest()
    intact = expected == record.checksum

    return {
        "log_id":         log_id,
        "intact":         intact,
        "stored_checksum":   record.checksum,
        "expected_checksum": expected,
        "verified_at":    datetime.utcnow().isoformat(),
        "record_summary": {
            "timestamp":     ts,
            "event_type":    record.event_type,
            "action":        record.action,
            "username":      record.username,
            "resource_type": record.resource_type,
            "resource_id":   record.resource_id,
        },
    }


# ── DELETE /api/audit/purge ───────────────────────────────────────────────────

@router.delete("/purge", status_code=status.HTTP_200_OK)
def purge_old_logs(
    older_than_days: int = Query(
        90, ge=7, le=3650,
        description="Delete audit records older than this many days"
    ),
    db: Session = Depends(get_db),
    _: User = Depends(require_roles("admin")),
) -> Dict[str, Any]:
    """
    Delete audit records older than *older_than_days* days.

    This is a retention management operation for compliance.
    Minimum retention: 7 days.  Maximum: 10 years.

    Requires role: admin
    """
    deleted = audit_service.purge_old_logs(db, older_than_days=older_than_days)
    return {
        "deleted_records": deleted,
        "older_than_days": older_than_days,
        "message": f"Purged {deleted} audit records older than {older_than_days} days.",
    }
