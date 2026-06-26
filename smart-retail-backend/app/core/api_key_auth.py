"""
API Key FastAPI Dependencies
=============================
Drop-in counterparts to the JWT-based dependencies in ``app/core/dependencies.py``.

Usage patterns
--------------
(A) Accept EITHER a JWT Bearer token OR an API key — most flexible:

    from app.core.api_key_auth import get_authenticated_principal

    @router.get("/items")
    def list_items(principal=Depends(get_authenticated_principal)):
        user_id = principal["user_id"]
        scopes  = principal["scopes"]   # None → full JWT user (all access)

(B) Require a specific scope from an API-key caller:

    from app.core.api_key_auth import require_scope

    @router.get("/inventory")
    def get_inventory(_=Depends(require_scope("read:inventory"))):
        ...

(C) API-key-only endpoint (rejects Bearer tokens):

    from app.core.api_key_auth import get_api_key_principal

    @router.get("/webhook-target")
    def webhook(principal=Depends(get_api_key_principal)):
        ...

Precedence
----------
When both ``Authorization: Bearer …`` and ``X-API-Key: …`` headers are present
``get_authenticated_principal`` prefers the Bearer token.
"""

import json
import logging
from typing import Any, Dict, List, Optional

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.database.connection import get_db
from app.models.api_key import APIKey

logger = logging.getLogger("smart_retail.api_key_auth")


# ── Raw extraction helpers ────────────────────────────────────────────────────

async def _extract_api_key(
    x_api_key: Optional[str] = Header(default=None, alias="X-API-Key"),
) -> Optional[str]:
    """Extract the raw API key from the ``X-API-Key`` header (may be None)."""
    return x_api_key


# ── Core dependency: API-key principal ───────────────────────────────────────

def get_api_key_principal(
    raw_key: Optional[str] = Depends(_extract_api_key),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """
    Authenticate the request using the ``X-API-Key`` header.

    Returns a *principal dict*:
    {
        "auth_method": "api_key",
        "key_id":      <int>,
        "user_id":     <int>,
        "username":    <str>,
        "role":        <str>,
        "scopes":      <list[str]>,
    }

    Raises 401 if no key is provided or the key is invalid/revoked/expired.
    Raises 429 if the daily quota is exceeded.
    """
    if not raw_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="X-API-Key header required.",
            headers={"WWW-Authenticate": "APIKey"},
        )

    # Import here to avoid circular imports at module load time
    from app.services.api_key_service import verify_api_key
    api_key: APIKey = verify_api_key(db, raw_key)

    scopes = json.loads(api_key.scopes)

    return {
        "auth_method": "api_key",
        "key_id":      api_key.id,
        "user_id":     api_key.owner_id,
        "username":    api_key.owner.username if api_key.owner else None,
        "role":        api_key.owner.role     if api_key.owner else "user",
        "scopes":      scopes,
    }


# ── Dual-auth dependency: JWT or API key ──────────────────────────────────────

def get_authenticated_principal(
    request: Request,
    raw_key: Optional[str] = Depends(_extract_api_key),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """
    Accept EITHER a ``Authorization: Bearer <jwt>`` OR an ``X-API-Key`` header.

    - If the Bearer header is present and valid → returns a JWT principal.
    - Else if X-API-Key is present and valid → returns an API key principal.
    - Otherwise raises 401.

    JWT principal shape:
    {
        "auth_method": "jwt",
        "user_id":     <int>,
        "username":    <str>,
        "role":        <str>,
        "scopes":      None,    # JWT users have unrestricted access by role
    }

    API key principal shape: see ``get_api_key_principal``.
    """
    # --- try JWT first ---
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[len("Bearer "):]
        from app.core.security import decode_token
        payload = decode_token(token)
        if payload and payload.get("type") == "access":
            from app.models.user import User
            user = db.query(User).filter(
                User.username == payload.get("sub"),
                User.is_active == True,  # noqa: E712
            ).first()
            if user:
                return {
                    "auth_method": "jwt",
                    "user_id":     user.id,
                    "username":    user.username,
                    "role":        user.role,
                    "scopes":      None,   # full access governed by role
                }

    # --- fall back to API key ---
    if raw_key:
        return get_api_key_principal(raw_key=raw_key, db=db)

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Authentication required: provide Bearer token or X-API-Key.",
        headers={"WWW-Authenticate": 'Bearer, APIKey realm="smart-retail"'},
    )


# ── Scope guard ───────────────────────────────────────────────────────────────

def require_scope(*required_scopes: str):
    """
    Dependency factory — enforce that the API key principal has **all** of the
    listed scopes.

    JWT principals (``scopes=None``) always pass — their access is governed by
    the existing role-based system.

    Usage::

        @router.get("/inventory")
        def get_inventory(_=Depends(require_scope("read:inventory"))):
            ...
    """
    def _check(
        principal: Dict[str, Any] = Depends(get_api_key_principal),
    ) -> Dict[str, Any]:
        scopes: Optional[List[str]] = principal.get("scopes")
        if scopes is None:
            # JWT user — skip scope check
            return principal

        # wildcard "* " grants all scopes
        if "*" in scopes:
            return principal

        missing = [s for s in required_scopes if s not in scopes]
        if missing:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"API key missing required scope(s): {missing}",
            )
        return principal

    return _check


def require_scope_dual(*required_scopes: str):
    """
    Same as ``require_scope`` but accepts JWT Bearer tokens too.

    Use this when an endpoint should be reachable by both regular (JWT) users
    and external (API key) integrations.
    """
    def _check(
        principal: Dict[str, Any] = Depends(get_authenticated_principal),
    ) -> Dict[str, Any]:
        scopes: Optional[List[str]] = principal.get("scopes")
        if scopes is None:
            # JWT user — governed by role, skip scope check
            return principal

        if "*" in scopes:
            return principal

        missing = [s for s in required_scopes if s not in scopes]
        if missing:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"API key missing required scope(s): {missing}",
            )
        return principal

    return _check
