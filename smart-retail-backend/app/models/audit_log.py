"""
Audit Log SQLAlchemy Model
===========================
Stores an immutable record of every significant operation on the platform.

Design principles:
  - Non-repudiation: captures WHO did WHAT to WHICH resource at WHEN
  - Tamper-evident: each record holds a SHA-256 checksum for integrity
  - Low-coupling: written asynchronously via a background queue; never blocks
  - Compliance-ready: filterable by user, resource, date, event type
  - Retention-aware: timestamp index supports efficient range deletes

Event taxonomy:
  AUTH      — login, logout, failed login, token refresh, password change
  CRUD      — create/read/update/delete on any resource
  ML        — dataset generation, pipeline run, model training, forecast
  ANALYTICS — analytics queries, BI reports, dashboard views
  EXPORT    — CSV/PDF data exports
  ALERT     — notification engine runs, deduplication events
  SECURITY  — rate-limited, injection attempt, RBAC denial
  SYSTEM    — startup, shutdown, DB init, cache events
"""

from sqlalchemy import Column, Integer, String, Float, DateTime, Text, Index
from sqlalchemy.sql import func

from app.database.connection import Base


class AuditLog(Base):
    __tablename__ = "audit_logs"

    # Composite indexes for the most common query patterns
    __table_args__ = (
        Index("ix_audit_ts",           "timestamp"),
        Index("ix_audit_user",         "user_id"),
        Index("ix_audit_event_type",   "event_type"),
        Index("ix_audit_resource",     "resource_type", "resource_id"),
        Index("ix_audit_path_method",  "path", "method"),
        Index("ix_audit_severity",     "severity"),
    )

    # ── Identity ─────────────────────────────────────────────────────────────
    id        = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(DateTime(timezone=True), server_default=func.now(),
                       nullable=False)

    # ── Event classification ──────────────────────────────────────────────────
    event_type = Column(String(50),  nullable=False)   # AUTH | CRUD | ML | …
    action     = Column(String(100), nullable=False)   # LOGIN | CREATE | TRAIN | …
    severity   = Column(String(20),  default="INFO")   # INFO | WARNING | ERROR | CRITICAL

    # ── Resource context ──────────────────────────────────────────────────────
    resource_type = Column(String(100))   # product | order | user | supplier | …
    resource_id   = Column(Integer)       # numeric ID of the affected entity

    # ── Actor ─────────────────────────────────────────────────────────────────
    user_id    = Column(Integer)
    username   = Column(String(100))
    ip_address = Column(String(50))
    user_agent = Column(String(500))

    # ── HTTP context ──────────────────────────────────────────────────────────
    request_id  = Column(String(50))
    method      = Column(String(10))
    path        = Column(String(500))
    status_code = Column(Integer)
    duration_ms = Column(Float)

    # ── Integrity ─────────────────────────────────────────────────────────────
    # SHA-256 of (username + action + resource_type + resource_id + timestamp).
    # Allows offline verification that the record was not altered.
    checksum = Column(String(64))

    # ── Extra details (JSON) ──────────────────────────────────────────────────
    # Stores context-specific data:
    #   AUTH:     {"failure_reason": "invalid_password"}
    #   CRUD:     {"changed_fields": ["unit_price", "reorder_level"]}
    #   ML:       {"model": "GradientBoosting", "horizon": 30, "mae": 4.2}
    #   SECURITY: {"threat": "RATE_LIMITED", "limit": 5}
    details = Column(Text)
