"""
Audit Service
==============
Two responsibilities:
  1. Asynchronous write path — a background thread drains a queue and writes
     audit records to the database, so callers never block.
  2. Read path — paginated query API consumed by the audit log routes.

Thread-safety guarantees:
  - The queue is thread-safe by design (queue.Queue).
  - The writer thread has its own SQLAlchemy session; it never shares a session
    with request-handling threads.
  - If the queue is full (maxsize=20 000) records are dropped with a warning
    rather than blocking the request thread.

Checksum algorithm:
    SHA-256(username + action + resource_type + str(resource_id) + iso_timestamp)
    Allows offline integrity verification: recompute the hash from the stored
    fields and compare with the stored checksum.
"""

import hashlib
import json
import logging
import queue
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import desc, func as sql_func
from sqlalchemy.orm import Session

from app.models.audit_log import AuditLog

logger = logging.getLogger("smart_retail.audit")

# ── Write queue ───────────────────────────────────────────────────────────────

_QUEUE_MAXSIZE = 20_000
_audit_queue: queue.Queue = queue.Queue(maxsize=_QUEUE_MAXSIZE)
_writer_started = False
_writer_lock = threading.Lock()


def _compute_checksum(username: str, action: str, resource_type: str,
                      resource_id: Any, ts: str) -> str:
    raw = f"{username}|{action}|{resource_type}|{resource_id}|{ts}"
    return hashlib.sha256(raw.encode()).hexdigest()


def _writer_loop() -> None:
    """Drain the audit queue and persist records to the database."""
    while True:
        try:
            record: Optional[Dict] = _audit_queue.get(timeout=2)
        except queue.Empty:
            continue
        if record is None:           # poison pill — stop the thread
            break
        try:
            # Import here to avoid circular dependency at module load time
            from app.database.connection import SessionLocal
            db: Session = SessionLocal()
            try:
                db.add(AuditLog(**record))
                db.commit()
            except Exception as exc:
                db.rollback()
                logger.error("Audit write failed: %s", exc)
            finally:
                db.close()
        except Exception as exc:
            logger.error("Audit writer loop error: %s", exc)
        finally:
            _audit_queue.task_done()


def _ensure_writer() -> None:
    """Start the background writer thread exactly once (lazy init)."""
    global _writer_started
    with _writer_lock:
        if not _writer_started:
            t = threading.Thread(
                target=_writer_loop, daemon=True, name="audit-writer"
            )
            t.start()
            _writer_started = True


# ── Public write interface ────────────────────────────────────────────────────

def enqueue_audit(
    *,
    event_type: str,
    action: str,
    severity: str = "INFO",
    resource_type: str = "",
    resource_id: Optional[int] = None,
    user_id: Optional[int] = None,
    username: str = "anonymous",
    ip_address: str = "",
    user_agent: str = "",
    request_id: str = "",
    method: str = "",
    path: str = "",
    status_code: int = 0,
    duration_ms: float = 0.0,
    details: Optional[Dict[str, Any]] = None,
) -> None:
    """
    Non-blocking enqueue of an audit record.

    Never raises — failures are logged and silently discarded so that
    an audit-write failure never affects the response to the user.
    """
    _ensure_writer()

    ts = datetime.now(timezone.utc).isoformat()
    checksum = _compute_checksum(username, action, resource_type, resource_id, ts)

    record = {
        "event_type":    event_type,
        "action":        action,
        "severity":      severity,
        "resource_type": resource_type,
        "resource_id":   resource_id,
        "user_id":       user_id,
        "username":      username,
        "ip_address":    ip_address,
        "user_agent":    user_agent[:500] if user_agent else "",
        "request_id":    request_id,
        "method":        method,
        "path":          path[:500] if path else "",
        "status_code":   status_code,
        "duration_ms":   duration_ms,
        "checksum":      checksum,
        "details":       json.dumps(details) if details else None,
    }

    try:
        _audit_queue.put_nowait(record)
    except queue.Full:
        logger.warning("Audit queue full — record dropped: %s %s", event_type, action)


# ── Convenience helpers ───────────────────────────────────────────────────────

def audit_auth_event(
    action: str,
    username: str,
    ip: str,
    success: bool,
    details: Optional[Dict] = None,
) -> None:
    """Record an authentication event (login, logout, token refresh)."""
    enqueue_audit(
        event_type="AUTH",
        action=action,
        severity="INFO" if success else "WARNING",
        resource_type="user",
        username=username,
        ip_address=ip,
        details={"success": success, **(details or {})},
    )


def audit_security_event(
    action: str,
    ip: str,
    path: str,
    details: Optional[Dict] = None,
) -> None:
    """Record a security-relevant event (rate-limit, injection attempt, RBAC denial)."""
    enqueue_audit(
        event_type="SECURITY",
        action=action,
        severity="WARNING",
        ip_address=ip,
        path=path,
        details=details or {},
    )


# ── Read API ──────────────────────────────────────────────────────────────────

_DEFAULT_PAGE_SIZE = 50
_MAX_PAGE_SIZE = 500


def get_audit_logs(
    db: Session,
    *,
    event_type: Optional[str] = None,
    action: Optional[str] = None,
    username: Optional[str] = None,
    user_id: Optional[int] = None,
    resource_type: Optional[str] = None,
    resource_id: Optional[int] = None,
    severity: Optional[str] = None,
    path_contains: Optional[str] = None,
    start_dt: Optional[datetime] = None,
    end_dt: Optional[datetime] = None,
    skip: int = 0,
    limit: int = _DEFAULT_PAGE_SIZE,
) -> Dict[str, Any]:
    """Paginated audit log query with optional filters."""
    limit = min(limit, _MAX_PAGE_SIZE)
    q = db.query(AuditLog)

    if event_type:
        q = q.filter(AuditLog.event_type == event_type.upper())
    if action:
        q = q.filter(AuditLog.action == action.upper())
    if username:
        q = q.filter(AuditLog.username.ilike(f"%{username}%"))
    if user_id is not None:
        q = q.filter(AuditLog.user_id == user_id)
    if resource_type:
        q = q.filter(AuditLog.resource_type == resource_type.lower())
    if resource_id is not None:
        q = q.filter(AuditLog.resource_id == resource_id)
    if severity:
        q = q.filter(AuditLog.severity == severity.upper())
    if path_contains:
        q = q.filter(AuditLog.path.contains(path_contains))
    if start_dt:
        q = q.filter(AuditLog.timestamp >= start_dt)
    if end_dt:
        q = q.filter(AuditLog.timestamp <= end_dt)

    total = q.count()
    records = (
        q.order_by(desc(AuditLog.timestamp))
         .offset(skip)
         .limit(limit)
         .all()
    )

    return {
        "total": total,
        "skip": skip,
        "limit": limit,
        "logs": [_serialize(r) for r in records],
    }


def get_audit_summary(db: Session, days: int = 30) -> Dict[str, Any]:
    """Aggregate audit counts for the monitoring dashboard."""
    from datetime import timedelta
    since = datetime.now(timezone.utc) - timedelta(days=days)

    # Total events in period
    total = (
        db.query(sql_func.count(AuditLog.id))
        .filter(AuditLog.timestamp >= since)
        .scalar() or 0
    )

    # By event type
    by_type = (
        db.query(AuditLog.event_type, sql_func.count(AuditLog.id))
        .filter(AuditLog.timestamp >= since)
        .group_by(AuditLog.event_type)
        .all()
    )

    # By severity
    by_severity = (
        db.query(AuditLog.severity, sql_func.count(AuditLog.id))
        .filter(AuditLog.timestamp >= since)
        .group_by(AuditLog.severity)
        .all()
    )

    # Top 10 most active users
    top_users = (
        db.query(AuditLog.username, sql_func.count(AuditLog.id).label("count"))
        .filter(AuditLog.timestamp >= since, AuditLog.username != "anonymous")
        .group_by(AuditLog.username)
        .order_by(desc("count"))
        .limit(10)
        .all()
    )

    # Top 10 most called paths
    top_paths = (
        db.query(AuditLog.path, sql_func.count(AuditLog.id).label("count"))
        .filter(AuditLog.timestamp >= since)
        .group_by(AuditLog.path)
        .order_by(desc("count"))
        .limit(10)
        .all()
    )

    # Auth failures in period
    auth_failures = (
        db.query(sql_func.count(AuditLog.id))
        .filter(
            AuditLog.timestamp >= since,
            AuditLog.event_type == "AUTH",
            AuditLog.severity == "WARNING",
        )
        .scalar() or 0
    )

    # Security events (rate limits, injection, RBAC denials)
    security_events = (
        db.query(sql_func.count(AuditLog.id))
        .filter(
            AuditLog.timestamp >= since,
            AuditLog.event_type == "SECURITY",
        )
        .scalar() or 0
    )

    # Average response time for the period
    avg_duration = (
        db.query(sql_func.avg(AuditLog.duration_ms))
        .filter(AuditLog.timestamp >= since, AuditLog.duration_ms > 0)
        .scalar()
    )

    return {
        "period_days": days,
        "total_events": total,
        "auth_failures": auth_failures,
        "security_events": security_events,
        "avg_response_ms": round(float(avg_duration), 2) if avg_duration else 0.0,
        "by_event_type": {t: c for t, c in by_type},
        "by_severity": {s: c for s, c in by_severity},
        "top_users": [{"username": u, "events": c} for u, c in top_users],
        "top_paths": [{"path": p, "requests": c} for p, c in top_paths],
    }


def get_user_activity(
    db: Session,
    user_id: int,
    limit: int = 100,
) -> Dict[str, Any]:
    """Return the most recent audit events for a specific user."""
    records = (
        db.query(AuditLog)
        .filter(AuditLog.user_id == user_id)
        .order_by(desc(AuditLog.timestamp))
        .limit(min(limit, _MAX_PAGE_SIZE))
        .all()
    )
    return {
        "user_id": user_id,
        "total_records": len(records),
        "activity": [_serialize(r) for r in records],
    }


def purge_old_logs(db: Session, older_than_days: int = 90) -> int:
    """
    Delete audit records older than *older_than_days* days.
    Returns the number of deleted rows.
    Admin-only operation — not exposed via middleware, only the API route.
    """
    from datetime import timedelta
    cutoff = datetime.now(timezone.utc) - timedelta(days=older_than_days)
    deleted = (
        db.query(AuditLog)
        .filter(AuditLog.timestamp < cutoff)
        .delete(synchronize_session=False)
    )
    db.commit()
    logger.info("Audit purge: deleted %d records older than %d days", deleted, older_than_days)
    return deleted


def export_audit_csv(
    db: Session,
    start_dt: Optional[datetime] = None,
    end_dt: Optional[datetime] = None,
    event_type: Optional[str] = None,
) -> str:
    """Export audit records as CSV string (for download endpoints)."""
    import csv
    import io

    q = db.query(AuditLog)
    if start_dt:
        q = q.filter(AuditLog.timestamp >= start_dt)
    if end_dt:
        q = q.filter(AuditLog.timestamp <= end_dt)
    if event_type:
        q = q.filter(AuditLog.event_type == event_type.upper())
    records = q.order_by(desc(AuditLog.timestamp)).limit(10_000).all()

    output = io.StringIO()
    columns = [
        "id", "timestamp", "event_type", "action", "severity",
        "resource_type", "resource_id", "username", "user_id",
        "ip_address", "method", "path", "status_code", "duration_ms",
        "request_id", "checksum",
    ]
    writer = csv.writer(output)
    writer.writerow(columns)
    for r in records:
        writer.writerow([getattr(r, col, "") for col in columns])

    return output.getvalue()


# ── Internal helpers ──────────────────────────────────────────────────────────

def _serialize(r: AuditLog) -> Dict[str, Any]:
    return {
        "id":            r.id,
        "timestamp":     r.timestamp.isoformat() if r.timestamp else None,
        "event_type":    r.event_type,
        "action":        r.action,
        "severity":      r.severity,
        "resource_type": r.resource_type,
        "resource_id":   r.resource_id,
        "user_id":       r.user_id,
        "username":      r.username,
        "ip_address":    r.ip_address,
        "method":        r.method,
        "path":          r.path,
        "status_code":   r.status_code,
        "duration_ms":   r.duration_ms,
        "request_id":    r.request_id,
        "checksum":      r.checksum,
        "details":       json.loads(r.details) if r.details else None,
    }
