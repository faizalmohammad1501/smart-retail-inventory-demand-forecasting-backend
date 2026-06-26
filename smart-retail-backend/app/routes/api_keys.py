"""
API Key Management Routes
==========================
REST endpoints for creating, listing, updating, rotating, and revoking
per-client API keys.

Endpoints (user-facing)
-----------------------
  POST   /api/keys/                    — create a new API key (returns raw key once)
  GET    /api/keys/                    — list all keys owned by the current user
  GET    /api/keys/{key_id}            — get key details + usage stats
  PATCH  /api/keys/{key_id}            — update name / scopes / limits
  DELETE /api/keys/{key_id}            — revoke (soft-delete) a key
  POST   /api/keys/{key_id}/rotate     — rotate: issue new secret, preserve ID
  GET    /api/keys/{key_id}/usage      — detailed usage statistics

Endpoints (admin-facing)
------------------------
  GET    /api/keys/admin/all           — list all keys across all users
  DELETE /api/keys/admin/{key_id}      — force-revoke any key
  POST   /api/keys/admin/purge         — hard-delete expired/stale revoked keys

Available scopes
----------------
  read:inventory   write:inventory
  read:orders      write:orders
  read:analytics   read:forecast
  write:ml         read:audit
  *                (admin wildcard)
"""

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_active_user, require_roles
from app.database.connection import get_db
from app.models.user import User
from app.services import api_key_service

logger = logging.getLogger("smart_retail.api_keys_routes")

router = APIRouter(prefix="/api/keys", tags=["API Key Management"])


# ── POST /api/keys/ ───────────────────────────────────────────────────────────

@router.post("/", status_code=status.HTTP_201_CREATED)
def create_key(
    body: Dict[str, Any],
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """
    Create a new API key for the authenticated user.

    **Request body:**
    ```json
    {
      "name":                  "My Integration Key",
      "description":           "Used by the warehouse WMS",
      "scopes":                ["read:inventory", "write:orders"],
      "rate_limit_per_minute": 120,
      "daily_quota":           5000,
      "expires_in_days":       90
    }
    ```

    **Response** includes the ``raw_key`` field — this is shown **exactly once**.
    Store it immediately; it cannot be retrieved later.

    Defaults: scopes = read:inventory + read:orders + read:analytics + read:forecast,
              rate_limit_per_minute = 60, daily_quota = 0 (unlimited),
              expires_in_days = null (never).
    """
    return api_key_service.create_api_key(
        db=db,
        owner_id=current_user.id,
        name=body.get("name", "Unnamed Key"),
        description=body.get("description"),
        scopes=body.get("scopes"),
        rate_limit_per_minute=int(body.get("rate_limit_per_minute", 60)),
        daily_quota=int(body.get("daily_quota", 0)),
        expires_in_days=body.get("expires_in_days"),
    )


# ── GET /api/keys/ ────────────────────────────────────────────────────────────

@router.get("/", status_code=status.HTTP_200_OK)
def list_keys(
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
) -> List[Dict[str, Any]]:
    """
    List all API keys owned by the current user.

    Raw key values are never included in this response.
    """
    return api_key_service.list_api_keys(db=db, owner_id=current_user.id)


# ── GET /api/keys/scopes ──────────────────────────────────────────────────────

@router.get("/scopes", status_code=status.HTTP_200_OK)
def list_available_scopes(
    _: User = Depends(get_current_active_user),
) -> Dict[str, Any]:
    """
    Return the full list of available API key scopes and their descriptions.
    """
    return {
        "scopes": [
            {"scope": "read:inventory",  "description": "Read inventory levels and history"},
            {"scope": "write:inventory", "description": "Adjust inventory quantities"},
            {"scope": "read:orders",     "description": "Read orders and order history"},
            {"scope": "write:orders",    "description": "Create and update orders"},
            {"scope": "read:analytics",  "description": "Access analytics, reports, and dashboards"},
            {"scope": "read:forecast",   "description": "Access demand forecasting results"},
            {"scope": "write:ml",        "description": "Trigger ML model training"},
            {"scope": "read:audit",      "description": "Read audit logs (admin / analyst)"},
            {"scope": "*",               "description": "All scopes — admin accounts only"},
        ]
    }


# ── GET /api/keys/{key_id} ────────────────────────────────────────────────────

@router.get("/{key_id}", status_code=status.HTTP_200_OK)
def get_key(
    key_id: int,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """
    Retrieve metadata and usage statistics for a specific API key.
    """
    return api_key_service.get_key_usage(db=db, key_id=key_id, owner_id=current_user.id)


# ── PATCH /api/keys/{key_id} ──────────────────────────────────────────────────

@router.patch("/{key_id}", status_code=status.HTTP_200_OK)
def update_key(
    key_id: int,
    body: Dict[str, Any],
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """
    Update mutable properties of an API key.

    All fields are optional — only provided fields are updated.

    **Patchable fields:** ``name``, ``description``, ``scopes``,
    ``rate_limit_per_minute``, ``daily_quota``, ``is_active``.
    """
    return api_key_service.update_api_key(
        db=db,
        key_id=key_id,
        owner_id=current_user.id,
        name=body.get("name"),
        description=body.get("description"),
        scopes=body.get("scopes"),
        rate_limit_per_minute=body.get("rate_limit_per_minute"),
        daily_quota=body.get("daily_quota"),
        is_active=body.get("is_active"),
    )


# ── DELETE /api/keys/{key_id} ─────────────────────────────────────────────────

@router.delete("/{key_id}", status_code=status.HTTP_200_OK)
def revoke_key(
    key_id: int,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """
    Revoke an API key.

    Revoked keys are immediately rejected on any subsequent request.
    The record is soft-deleted (``is_active=False``) to preserve audit history.
    """
    return api_key_service.revoke_api_key(db=db, key_id=key_id, owner_id=current_user.id)


# ── POST /api/keys/{key_id}/rotate ────────────────────────────────────────────

@router.post("/{key_id}/rotate", status_code=status.HTTP_200_OK)
def rotate_key(
    key_id: int,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """
    Rotate an API key — issue a new secret while keeping the same ID, scopes,
    and usage counters.

    Use this for scheduled key rotation without changing integrations that
    reference the key by ID.

    **The new raw key is returned exactly once** — store it immediately.
    """
    return api_key_service.rotate_api_key(db=db, key_id=key_id, owner_id=current_user.id)


# ── GET /api/keys/{key_id}/usage ──────────────────────────────────────────────

@router.get("/{key_id}/usage", status_code=status.HTTP_200_OK)
def key_usage(
    key_id: int,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """
    Detailed usage statistics: total requests, today's requests, quota
    remaining, last-used timestamp, and expiry status.
    """
    return api_key_service.get_key_usage(db=db, key_id=key_id, owner_id=current_user.id)


# ── Admin endpoints ───────────────────────────────────────────────────────────

@router.get("/admin/all", status_code=status.HTTP_200_OK)
def admin_list_all(
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    owner_id: Optional[int] = Query(default=None),
    is_active: Optional[bool] = Query(default=None),
    _: User = Depends(require_roles("admin")),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """
    **Admin only** — list all API keys across all users.

    Supports filtering by ``owner_id`` and ``is_active``.
    """
    return api_key_service.admin_list_all_keys(
        db=db,
        skip=skip,
        limit=limit,
        owner_id=owner_id,
        is_active=is_active,
    )


@router.delete("/admin/{key_id}", status_code=status.HTTP_200_OK)
def admin_revoke(
    key_id: int,
    _: User = Depends(require_roles("admin")),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """
    **Admin only** — force-revoke any API key regardless of owner.

    Use this for incident response when a key may have been compromised.
    """
    return api_key_service.admin_revoke_key(db=db, key_id=key_id)


@router.post("/admin/purge", status_code=status.HTTP_200_OK)
def admin_purge(
    _: User = Depends(require_roles("admin")),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """
    **Admin only** — hard-delete revoked keys that are also expired or have not
    been used in the last 90 days.

    Safe to call from a scheduled maintenance job.
    """
    return api_key_service.purge_expired_keys(db=db)
