"""Strict, time-bounded access contract for the independent Meta reviewer."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException, status

META_REVIEWER_ROLE = "meta_reviewer"
META_REVIEW_SCOPES = frozenset({
    "integrations.meta",
    "customer_intelligence",
    "ads.meta",
})
META_INTEGRATION_PROVIDERS = frozenset({"meta_ads", "instagram"})
META_REVIEWER_CI_PERMISSIONS = frozenset({
    "customer_intelligence.inbox.read",
    "customer_intelligence.suggestions.review",
    "customer_intelligence.escalate",
})


def is_meta_reviewer(user: Any) -> bool:
    return (
        isinstance(user, dict)
        and str(user.get("role") or "").strip().casefold() == META_REVIEWER_ROLE
    )


def _as_utc(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def review_access_expired(user: Any, *, now: datetime | None = None) -> bool:
    if not is_meta_reviewer(user):
        return False
    expires_at = _as_utc(user.get("review_access_expires_at"))
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return expires_at is None or expires_at <= current


def require_review_scope(user: Any, scope: str) -> dict:
    """Return the tenant principal for an Owner or a valid Meta reviewer.

    A reviewer principal deliberately replaces id with review_owner_id so all
    existing tenant-scoped services read the store owner's data without ever
    granting the reviewer Owner status.
    """
    if isinstance(user, dict):
        role = str(user.get("role") or "").strip().casefold()
        if (role == "owner" or user.get("is_owner") is True) and user.get("id"):
            return user

    if not is_meta_reviewer(user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "owner_only"},
        )
    if review_access_expired(user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "meta_review_access_expired"},
        )

    configured_scopes = frozenset(
        str(value).strip() for value in (user.get("review_scopes") or []) if str(value).strip()
    )
    if configured_scopes != META_REVIEW_SCOPES or scope not in configured_scopes:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "meta_review_scope_invalid"},
        )

    owner_id = str(user.get("review_owner_id") or "").strip()
    reviewer_id = str(user.get("id") or "").strip()
    if not owner_id or not reviewer_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "meta_review_tenant_binding_missing"},
        )

    principal = dict(user)
    principal["id"] = owner_id
    principal["acting_reviewer_id"] = reviewer_id
    principal["is_owner"] = False
    return principal
