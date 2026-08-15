from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

from meta_reviewer_access import (
    META_REVIEW_SCOPES,
    require_review_scope,
    review_access_expired,
    reviewer_api_path_allowed,
)


def reviewer(**overrides):
    now = datetime.now(timezone.utc)
    value = {
        "id": "reviewer-1",
        "role": "meta_reviewer",
        "review_owner_id": "owner-1",
        "review_scopes": sorted(META_REVIEW_SCOPES),
        "review_access_expires_at": (now + timedelta(days=366)).isoformat(),
    }
    value.update(overrides)
    return value


def test_reviewer_resolves_to_owner_tenant_without_owner_status():
    principal = require_review_scope(reviewer(), "ads.meta")
    assert principal["id"] == "owner-1"
    assert principal["acting_reviewer_id"] == "reviewer-1"
    assert principal["is_owner"] is False


def test_expired_or_malformed_expiry_fails_closed():
    assert review_access_expired(reviewer(review_access_expires_at="not-a-date"))
    with pytest.raises(HTTPException) as exc:
        require_review_scope(
            reviewer(review_access_expires_at=(datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()),
            "integrations.meta",
        )
    assert exc.value.status_code == 403


def test_scope_configuration_must_be_exact():
    with pytest.raises(HTTPException):
        require_review_scope(
            reviewer(review_scopes=["ads.meta", "integrations.meta"]),
            "ads.meta",
        )
    with pytest.raises(HTTPException):
        require_review_scope(
            reviewer(review_scopes=[*META_REVIEW_SCOPES, "dashboard.read"]),
            "ads.meta",
        )


def test_owner_principal_is_unchanged():
    owner = {"id": "owner-1", "role": "owner", "is_owner": True}
    assert require_review_scope(owner, "ads.meta") is owner


def test_reviewer_api_allowlist_denies_every_unrelated_backend_path():
    assert reviewer_api_path_allowed("/api/integrations-v2/overview")
    assert reviewer_api_path_allowed("/api/customer-intelligence/v1/inbox")
    assert reviewer_api_path_allowed("/api/ads-manager/overview")
    assert reviewer_api_path_allowed("/api/auth/me")
    assert not reviewer_api_path_allowed("/api/dashboard")
    assert not reviewer_api_path_allowed("/api/team")
    assert not reviewer_api_path_allowed("/api/auth/profile/email")
