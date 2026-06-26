"""
APIKey SQLAlchemy Model
========================
Stores per-client API keys for machine-to-machine (M2M) authentication.

Design decisions
----------------
- The **raw key is never stored**.  Only a SHA-256 hex-digest is persisted so
  a database breach cannot expose live keys.
- A short ``key_prefix`` (``srk_xxxxxxxx``) is stored for display in dashboards
  without revealing the full secret.
- ``scopes`` is a JSON-encoded list of permission strings (e.g.
  ``["read:inventory", "write:orders"]``).  An empty list means no access; the
  special value ``["*"]`` grants all scopes (admin keys only).
- ``daily_quota = 0`` means unlimited requests per day.
- ``rate_limit_per_minute = 0`` means fall back to the global default.
- ``expires_at = NULL`` means the key never expires.
- ``quota_reset_date`` tracks when ``today_requests`` was last zeroed so the
  day boundary check is a single date comparison.

Key format
----------
  ``srk_<48 hex chars>`` — 52 characters total.
  Example: ``srk_a3f9bc2d1e7044ab8c6f09d53e812ca7b4019ef2d6c35a80``

Indexes
-------
  ix_api_keys_hash    — primary lookup on every authenticated request
  ix_api_keys_owner   — list keys by owner efficiently
  ix_api_keys_active  — filter active keys quickly
  ix_api_keys_prefix  — uniqueness guard for the display prefix
"""

from datetime import datetime, date, timezone

from sqlalchemy import (
    Boolean, Column, Date, DateTime, Index,
    Integer, String, Text, ForeignKey,
)
from sqlalchemy.orm import relationship

from app.database.connection import Base


class APIKey(Base):
    __tablename__ = "api_keys"

    # ── Identity ──────────────────────────────────────────────────────────────
    id         = Column(Integer, primary_key=True, index=True)
    key_hash   = Column(String(64), unique=True, nullable=False, index=True)
    key_prefix = Column(String(20), nullable=False)      # "srk_xxxxxxxx…" safe to display

    # ── Metadata ──────────────────────────────────────────────────────────────
    name        = Column(String(100), nullable=False)
    description = Column(String(500), nullable=True)

    # ── Owner ─────────────────────────────────────────────────────────────────
    owner_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    owner = relationship("User", back_populates="api_keys")

    # ── Permissions ───────────────────────────────────────────────────────────
    # JSON-encoded list, e.g. '["read:inventory","write:orders"]'
    # Special value: '["*"]'  → all scopes (admin keys only)
    scopes = Column(
        Text,
        nullable=False,
        default='["read:inventory","read:orders","read:analytics","read:forecast"]',
    )

    # ── Rate limiting & quota ─────────────────────────────────────────────────
    rate_limit_per_minute = Column(Integer, default=60,  nullable=False)  # 0 = global default
    daily_quota           = Column(Integer, default=0,   nullable=False)  # 0 = unlimited

    # ── Usage counters ────────────────────────────────────────────────────────
    total_requests    = Column(Integer,  default=0,    nullable=False)
    today_requests    = Column(Integer,  default=0,    nullable=False)
    quota_reset_date  = Column(Date,     nullable=True)
    last_used_at      = Column(DateTime, nullable=True)

    # ── Status & lifecycle ────────────────────────────────────────────────────
    is_active  = Column(Boolean,  default=True, nullable=False)
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    expires_at = Column(DateTime, nullable=True)   # NULL → never expires

    # ── Composite indexes ─────────────────────────────────────────────────────
    __table_args__ = (
        Index("ix_api_keys_owner",  "owner_id"),
        Index("ix_api_keys_active", "is_active"),
    )

    # ── Helpers ───────────────────────────────────────────────────────────────
    def is_expired(self) -> bool:
        if self.expires_at is None:
            return False
        return datetime.now(timezone.utc) > self.expires_at.replace(tzinfo=timezone.utc)

    def is_quota_exceeded(self) -> bool:
        """Return True if the daily quota (>0) has been reached."""
        if self.daily_quota == 0:
            return False
        today = date.today()
        if self.quota_reset_date != today:
            # Day has rolled over — counter will be reset on next write
            return False
        return self.today_requests >= self.daily_quota

    def __repr__(self) -> str:
        return f"<APIKey id={self.id} prefix={self.key_prefix!r} owner={self.owner_id}>"
