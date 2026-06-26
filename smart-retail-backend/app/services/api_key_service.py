"""
API Key Service
================
Business logic for creating, validating, and managing API keys.

Key lifecycle
-------------
1. ``create_api_key()``   — generates a raw key, stores the SHA-256 hash,
                             returns the plaintext **once** (never retrievable again)
2. ``verify_api_key()``   — hashes the presented raw key, looks up the record,
                             checks active/expiry/quota, updates usage counters
3. ``rotate_api_key()``   — atomically invalidates the old hash and issues a new one
4. ``revoke_api_key()``   — sets is_active=False (soft delete, preserves audit trail)
5. ``purge_expired_keys()``— batch hard-delete truly expired + already-revoked keys

Available scopes
----------------
  read:inventory   write:inventory
  read:orders      write:orders
  read:analytics   read:forecast
  write:ml         read:audit
  *                (admin wildcard — all scopes)
"""

import hashlib
import json
import logging
import os
import secrets
from datetime import datetime, date, timezone, timedelta
from typing import Any, Dict, List, Optional

from fastapi import HTTPException, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.api_key import APIKey
from app.models.user import User

logger = logging.getLogger("smart_retail.api_key_service")

# ── Constants ─────────────────────────────────────────────────────────────────

KEY_PREFIX = "srk_"
KEY_RANDOM_BYTES = 24          # 48 hex chars → full key = "srk_" + 48 = 52 chars
MAX_KEYS_PER_USER = 20         # guard against unbounded key proliferation

AVAILABLE_SCOPES: List[str] = [
    "read:inventory",
    "write:inventory",
    "read:orders",
    "write:orders",
    "read:analytics",
    "read:forecast",
    "write:ml",
    "read:audit",
    "*",   # admin wildcard
]

DEFAULT_SCOPES: List[str] = [
    "read:inventory",
    "read:orders",
    "read:analytics",
    "read:forecast",
]


# ── Internal helpers ──────────────────────────────────────────────────────────

def _hash_key(raw_key: str) -> str:
    """Return the SHA-256 hex-digest of a raw API key."""
    return hashlib.sha256(raw_key.encode()).hexdigest()


def _generate_raw_key() -> str:
    """Generate a cryptographically secure API key string."""
    return KEY_PREFIX + secrets.token_hex(KEY_RANDOM_BYTES)


def _validate_scopes(scopes: List[str]) -> None:
    """Raise 422 if any requested scope is not in AVAILABLE_SCOPES."""
    invalid = [s for s in scopes if s not in AVAILABLE_SCOPES]
    if invalid:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid scope(s): {invalid}. Valid: {AVAILABLE_SCOPES}",
        )


def _reset_daily_counter_if_needed(key: APIKey, db: Session) -> None:
    """Zero today_requests when the calendar day has rolled over."""
    today = date.today()
    if key.quota_reset_date != today:
        key.today_requests = 0
        key.quota_reset_date = today
        db.flush()


# ── CRUD operations ───────────────────────────────────────────────────────────

def create_api_key(
    db: Session,
    owner_id: int,
    name: str,
    description: Optional[str] = None,
    scopes: Optional[List[str]] = None,
    rate_limit_per_minute: int = 60,
    daily_quota: int = 0,
    expires_in_days: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Create a new API key for *owner_id*.

    Returns a dict including ``raw_key`` — this is the **only** time the
    plaintext key is available.  The caller must surface it to the user
    immediately.
    """
    # --- guard: max keys per user ---
    existing = db.query(func.count(APIKey.id)).filter(
        APIKey.owner_id == owner_id,
        APIKey.is_active == True,                # noqa: E712
    ).scalar()
    if existing >= MAX_KEYS_PER_USER:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Maximum of {MAX_KEYS_PER_USER} active API keys per user reached.",
        )

    scopes = scopes or DEFAULT_SCOPES[:]
    _validate_scopes(scopes)

    # Admin-only wildcard guard: only admins may request "*"
    owner = db.query(User).filter(User.id == owner_id).first()
    if "*" in scopes and (owner is None or owner.role != "admin"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail='Scope "*" (wildcard) is reserved for admin accounts.',
        )

    raw_key = _generate_raw_key()
    key_hash = _hash_key(raw_key)
    key_prefix = raw_key[:16]   # "srk_" + first 12 chars of random part

    expires_at = None
    if expires_in_days is not None and expires_in_days > 0:
        expires_at = datetime.now(timezone.utc) + timedelta(days=expires_in_days)

    api_key = APIKey(
        key_hash=key_hash,
        key_prefix=key_prefix,
        name=name,
        description=description,
        owner_id=owner_id,
        scopes=json.dumps(scopes),
        rate_limit_per_minute=max(0, rate_limit_per_minute),
        daily_quota=max(0, daily_quota),
        expires_at=expires_at,
        quota_reset_date=date.today(),
    )
    db.add(api_key)
    db.commit()
    db.refresh(api_key)

    logger.info("API key created: id=%d owner=%d name=%r prefix=%s",
                api_key.id, owner_id, name, key_prefix)

    return {
        "id":                   api_key.id,
        "raw_key":              raw_key,   # shown ONCE
        "key_prefix":           key_prefix,
        "name":                 name,
        "description":          description,
        "scopes":               scopes,
        "rate_limit_per_minute": rate_limit_per_minute,
        "daily_quota":          daily_quota,
        "expires_at":           expires_at.isoformat() if expires_at else None,
        "created_at":           api_key.created_at.isoformat(),
        "message": (
            "Store this key securely — it will NOT be shown again."
        ),
    }


def list_api_keys(db: Session, owner_id: int) -> List[Dict[str, Any]]:
    """Return all keys owned by *owner_id* (raw keys never exposed)."""
    keys = (
        db.query(APIKey)
        .filter(APIKey.owner_id == owner_id)
        .order_by(APIKey.created_at.desc())
        .all()
    )
    return [_serialize_key(k) for k in keys]


def get_api_key(db: Session, key_id: int, owner_id: int) -> APIKey:
    """Fetch a single key record; raises 404 if not found or not owned."""
    key = db.query(APIKey).filter(
        APIKey.id == key_id,
        APIKey.owner_id == owner_id,
    ).first()
    if not key:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="API key not found.",
        )
    return key


def update_api_key(
    db: Session,
    key_id: int,
    owner_id: int,
    *,
    name: Optional[str] = None,
    description: Optional[str] = None,
    scopes: Optional[List[str]] = None,
    rate_limit_per_minute: Optional[int] = None,
    daily_quota: Optional[int] = None,
    is_active: Optional[bool] = None,
) -> Dict[str, Any]:
    """Partial update of mutable key fields."""
    key = get_api_key(db, key_id, owner_id)

    if name is not None:
        key.name = name
    if description is not None:
        key.description = description
    if scopes is not None:
        _validate_scopes(scopes)
        owner = db.query(User).filter(User.id == owner_id).first()
        if "*" in scopes and (owner is None or owner.role != "admin"):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail='Scope "*" is reserved for admin accounts.',
            )
        key.scopes = json.dumps(scopes)
    if rate_limit_per_minute is not None:
        key.rate_limit_per_minute = max(0, rate_limit_per_minute)
    if daily_quota is not None:
        key.daily_quota = max(0, daily_quota)
    if is_active is not None:
        key.is_active = is_active

    db.commit()
    db.refresh(key)
    return _serialize_key(key)


def revoke_api_key(db: Session, key_id: int, owner_id: int) -> Dict[str, Any]:
    """Soft-delete: set is_active=False.  Preserves usage history."""
    key = get_api_key(db, key_id, owner_id)
    key.is_active = False
    db.commit()
    logger.info("API key revoked: id=%d owner=%d", key_id, owner_id)
    return {"status": "revoked", "key_id": key_id}


def rotate_api_key(db: Session, key_id: int, owner_id: int) -> Dict[str, Any]:
    """
    Atomically invalidate the current key and issue a fresh secret.

    The old hash is replaced; usage counters are preserved.
    Returns the new raw key (shown ONCE).
    """
    key = get_api_key(db, key_id, owner_id)

    raw_key = _generate_raw_key()
    key.key_hash   = _hash_key(raw_key)
    key.key_prefix = raw_key[:16]
    key.is_active  = True          # re-activate if it was suspended

    db.commit()
    db.refresh(key)
    logger.info("API key rotated: id=%d owner=%d new_prefix=%s",
                key_id, owner_id, key.key_prefix)

    return {
        "id":         key.id,
        "raw_key":    raw_key,
        "key_prefix": key.key_prefix,
        "name":       key.name,
        "message":    "Key rotated. Store the new key securely — it will NOT be shown again.",
    }


def get_key_usage(db: Session, key_id: int, owner_id: int) -> Dict[str, Any]:
    """Return usage statistics for a key."""
    key = get_api_key(db, key_id, owner_id)
    scopes = json.loads(key.scopes)
    return {
        "id":               key.id,
        "key_prefix":       key.key_prefix,
        "name":             key.name,
        "is_active":        key.is_active,
        "scopes":           scopes,
        "total_requests":   key.total_requests,
        "today_requests":   key.today_requests,
        "daily_quota":      key.daily_quota,
        "quota_remaining":  (
            max(0, key.daily_quota - key.today_requests)
            if key.daily_quota > 0 else None
        ),
        "rate_limit_per_minute": key.rate_limit_per_minute,
        "last_used_at":     key.last_used_at.isoformat() if key.last_used_at else None,
        "created_at":       key.created_at.isoformat(),
        "expires_at":       key.expires_at.isoformat() if key.expires_at else None,
        "is_expired":       key.is_expired(),
    }


# ── Verification (called on every API-key-authenticated request) ──────────────

def verify_api_key(db: Session, raw_key: str) -> APIKey:
    """
    Verify a raw API key presented in the ``X-API-Key`` header.

    Raises appropriate HTTP exceptions so the middleware can return structured
    error responses.  Also increments usage counters on success.
    """
    if not raw_key or not raw_key.startswith(KEY_PREFIX):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key format.",
            headers={"WWW-Authenticate": "APIKey"},
        )

    key_hash = _hash_key(raw_key)
    key = db.query(APIKey).filter(APIKey.key_hash == key_hash).first()

    if key is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key.",
            headers={"WWW-Authenticate": "APIKey"},
        )

    if not key.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="API key has been revoked.",
            headers={"WWW-Authenticate": "APIKey"},
        )

    if key.is_expired():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="API key has expired.",
            headers={"WWW-Authenticate": "APIKey"},
        )

    # Daily quota check — reset counter if calendar day has rolled over
    _reset_daily_counter_if_needed(key, db)

    if key.is_quota_exceeded():
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Daily quota of {key.daily_quota} requests exceeded. "
                   f"Resets at midnight UTC.",
            headers={"Retry-After": "86400"},
        )

    # --- update usage ---
    now = datetime.now(timezone.utc)
    key.total_requests  += 1
    key.today_requests  += 1
    key.last_used_at     = now
    db.commit()

    return key


# ── Admin helpers ─────────────────────────────────────────────────────────────

def admin_list_all_keys(
    db: Session,
    *,
    skip: int = 0,
    limit: int = 50,
    owner_id: Optional[int] = None,
    is_active: Optional[bool] = None,
) -> Dict[str, Any]:
    """Admin view: list all keys with optional filters."""
    q = db.query(APIKey)
    if owner_id is not None:
        q = q.filter(APIKey.owner_id == owner_id)
    if is_active is not None:
        q = q.filter(APIKey.is_active == is_active)

    total = q.count()
    keys  = q.order_by(APIKey.created_at.desc()).offset(skip).limit(limit).all()
    return {
        "total": total,
        "skip":  skip,
        "limit": limit,
        "items": [_serialize_key(k) for k in keys],
    }


def admin_revoke_key(db: Session, key_id: int) -> Dict[str, Any]:
    """Admin: revoke any key regardless of owner."""
    key = db.query(APIKey).filter(APIKey.id == key_id).first()
    if not key:
        raise HTTPException(status_code=404, detail="API key not found.")
    key.is_active = False
    db.commit()
    logger.info("API key force-revoked by admin: id=%d", key_id)
    return {"status": "revoked", "key_id": key_id}


def purge_expired_keys(db: Session) -> Dict[str, Any]:
    """
    Hard-delete all keys that are both revoked AND (expired OR last-used > 90 days ago).

    Safe to call from a scheduled job.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=90)
    keys = db.query(APIKey).filter(
        APIKey.is_active == False,  # noqa: E712
    ).all()

    deleted = 0
    for k in keys:
        if k.is_expired() or (k.last_used_at and k.last_used_at < cutoff):
            db.delete(k)
            deleted += 1

    db.commit()
    logger.info("Purged %d expired/stale API keys", deleted)
    return {"purged": deleted}


# ── Serializer ────────────────────────────────────────────────────────────────

def _serialize_key(key: APIKey) -> Dict[str, Any]:
    """Convert an APIKey row to a safe dict (no key_hash)."""
    return {
        "id":                    key.id,
        "key_prefix":            key.key_prefix,
        "name":                  key.name,
        "description":           key.description,
        "owner_id":              key.owner_id,
        "scopes":                json.loads(key.scopes),
        "is_active":             key.is_active,
        "rate_limit_per_minute": key.rate_limit_per_minute,
        "daily_quota":           key.daily_quota,
        "total_requests":        key.total_requests,
        "today_requests":        key.today_requests,
        "last_used_at":          key.last_used_at.isoformat() if key.last_used_at else None,
        "created_at":            key.created_at.isoformat(),
        "expires_at":            key.expires_at.isoformat() if key.expires_at else None,
        "is_expired":            key.is_expired(),
    }
