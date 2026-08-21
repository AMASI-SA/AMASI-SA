"""Governed Snapchat campaign mutations for Mezan Ads Manager.

The reporting plane remains read-only.  This module owns a separate owner-only
control plane with a strict proposal -> preview -> approval -> execution ->
verification -> audit -> rollback lifecycle.  New delivery entities are always
created PAUSED; activation has an independent runtime kill switch.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Literal

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .snapchat_account_selection import _load_selected_accounts
from .snapchat_native_data_common import (
    SNAPCHAT_API_BASE,
    SNAPCHAT_PROVIDER_ID,
    SnapchatNativeSyncError,
    SnapchatSyncContext,
    _collection,
    _safe_next_url,
    _safe_provider_error_detail,
)
from .snapchat_native_entities_sync import _safe_provider_value, _upsert_entity


PROPOSAL_COLLECTION = "mezan_snapchat_campaign_proposals_v1"
AUDIT_COLLECTION = "mezan_snapchat_campaign_audit_v1"
ENTITY_LEASE_COLLECTION = "mezan_snapchat_campaign_entity_leases_v1"
SOURCE_MODE = "snapchat_campaign_management_v1"
PROPOSAL_TTL = timedelta(minutes=30)
ENTITY_LEASE_DURATION = timedelta(minutes=5)
MUTATIONS_ENABLED_ENV = "MEZAN_SNAPCHAT_CAMPAIGN_MUTATIONS_ENABLED"
ACTIVATION_ENABLED_ENV = "MEZAN_SNAPCHAT_CAMPAIGN_ACTIVATION_ENABLED"
MAX_DAILY_BUDGET_ENV = "MEZAN_SNAPCHAT_MAX_DAILY_BUDGET_MICRO"
DEFAULT_MAX_DAILY_BUDGET_MICRO = 5_000_000_000
TRACKING_ASSET_COLLECTION = "mezan_snapchat_tracking_assets_v2"
MANAGEMENT_SAFETY_PROTOCOL_VERSION = 2
MAX_MANAGEMENT_LIST_PAGES = 10
MAX_MANAGEMENT_LIST_ROWS = 10_000
CREATE_RECONCILIATION_GRACE = timedelta(minutes=5)
PIXEL_CONVERSION_WINDOWS = {"SWIPE_28DAY_VIEW_1DAY", "SWIPE_7DAY"}
PIXEL_ELIGIBLE_STATUSES = {"ELIGIBLE", "ELIGIBLE_WARNING"}

Action = Literal[
    "campaign.create",
    "campaign.update",
    "ad_squad.create",
    "ad_squad.update",
    "ad.create",
    "ad.update",
    "creative.create",
]
EvidenceVerification = Literal["verified", "inferred", "user_suggestion"]

DELIVERY_CREATE_ACTIONS = {
    "campaign.create",
    "ad_squad.create",
    "ad.create",
}
UPDATE_ACTIONS = {
    "campaign.update",
    "ad_squad.update",
    "ad.update",
}
CREATIVE_ACTIONS = {"creative.create"}

CAMPAIGN_CREATE_FIELDS = {
    "name",
    "start_time",
    "end_time",
    "daily_budget_micro",
    "lifetime_spend_cap_micro",
    "objective",
    "objective_v2_properties",
    "measurement_spec",
    "regulations",
    "buy_model",
    "pacing_level",
    "shared_properties",
    "product_properties",
}
CAMPAIGN_UPDATE_FIELDS = CAMPAIGN_CREATE_FIELDS | {"status"}
AD_SQUAD_CREATE_FIELDS = {
    "name",
    "type",
    "targeting",
    "placement_v2",
    "billing_event",
    "bid_micro",
    "bid_strategy",
    "daily_budget_micro",
    "lifetime_budget_micro",
    "optimization_goal",
    "conversion_window",
    "pixel_id",
    "start_time",
    "end_time",
    "pacing_type",
    "brand_safety_config",
    "cap_and_exclusion_config",
    "ad_scheduling_config",
    "campaign_budget_optimization_properties",
    "child_ad_type",
    "forced_view_setting",
    "event_sources",
    "delivery_constraint",
}
AD_SQUAD_UPDATE_FIELDS = AD_SQUAD_CREATE_FIELDS | {"status"}
AD_CREATE_FIELDS = {"name", "creative_id", "type"}
AD_UPDATE_FIELDS = {
    "name",
    "status",
    "third_party_on_swipe_tracking_urls",
    "third_party_paid_impression_tracking_urls",
}
CREATIVE_CREATE_FIELDS = {
    "name",
    "type",
    "headline",
    "call_to_action",
    "top_snap_media_id",
    "top_snap_crop_position",
    "web_view_properties",
    "profile_properties",
    "shareable",
    "forced_view_eligibility",
    "ad_product",
    "cta_color_display_mode",
    "preview_properties",
    "collection_properties",
    "app_install_properties",
    "deep_link_properties",
    "render_type",
}

# Snapchat's ad endpoint uses an ad type that can differ from the attached
# creative type. Keep this allow-list deliberately narrow and fail closed for
# unknown mappings before a provider write can be attempted.
CREATIVE_TYPE_TO_AD_TYPES = {
    "WEB_VIEW": ("REMOTE_WEBPAGE",),
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None = None) -> str:
    return (value or _utcnow()).astimezone(timezone.utc).isoformat()


def _expired(value: Any) -> bool:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return True
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc) <= _utcnow()


def _is_pixel_optimization_goal(value: Any) -> bool:
    return str(value or "").strip().upper().startswith("PIXEL_")


def _enabled(name: str, default: str = "false") -> bool:
    return str(os.environ.get(name, default)).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
        "enabled",
    }


def snapchat_campaign_mutations_enabled() -> bool:
    return _enabled(MUTATIONS_ENABLED_ENV)


def snapchat_campaign_activation_enabled() -> bool:
    return _enabled(ACTIVATION_ENABLED_ENV)


def _max_daily_budget_micro() -> int:
    try:
        return max(
            5_000_000,
            int(
                os.environ.get(
                    MAX_DAILY_BUDGET_ENV,
                    str(DEFAULT_MAX_DAILY_BUDGET_MICRO),
                )
            ),
        )
    except (TypeError, ValueError):
        return DEFAULT_MAX_DAILY_BUDGET_MICRO


def _text(value: Any, *, field: str, maximum: int = 375) -> str:
    normalized = " ".join(str(value or "").split()).strip()
    if not normalized:
        raise ValueError(f"{field} is required")
    if len(normalized) > maximum:
        raise ValueError(f"{field} exceeds {maximum} characters")
    return normalized


def _identifier(value: Any, *, field: str) -> str:
    normalized = str(value or "").strip()
    if not normalized or len(normalized) > 160:
        raise ValueError(f"{field} is invalid")
    if any(
        char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
        for char in normalized
    ):
        raise ValueError(f"{field} is invalid")
    return normalized


def _bounded_payload(payload: dict[str, Any]) -> dict[str, Any]:
    encoded = json.dumps(
        payload, ensure_ascii=False, separators=(",", ":"), default=str
    )
    if len(encoded.encode("utf-8")) > 64_000:
        raise ValueError("payload is too large")
    lowered = encoded.lower()
    if any(
        fragment in lowered
        for fragment in (
            "access_token",
            "refresh_token",
            "authorization",
            "client_secret",
            "password",
            "credential",
            "ciphertext",
        )
    ):
        raise ValueError("payload contains a forbidden sensitive field")
    safe = _safe_provider_value(payload)
    if not isinstance(safe, dict):
        raise ValueError("payload is invalid")
    return safe


def snapchat_management_request_fingerprint(
    payload: "SnapchatManagementProposalInput",
) -> str:
    """Return the canonical fingerprint shared by sync and async previews."""
    return hashlib.sha256(
        json.dumps(
            payload.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()


class SnapchatDecisionEvidenceInput(BaseModel):
    """One bounded claim; only verified claims may affect a write decision."""

    model_config = ConfigDict(extra="forbid")

    kind: str = Field(min_length=2, max_length=80)
    value: Any = None
    source: str = Field(min_length=2, max_length=500)
    observed_at: str | None = Field(default=None, max_length=80)
    verification_status: EvidenceVerification = "user_suggestion"
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    used_in_decision: bool = False
    weight: float = Field(default=0.0, ge=0.0, le=1.0)

    @field_validator("kind", "source")
    @classmethod
    def normalize_text(cls, value: str, info: Any) -> str:
        return _text(value, field=info.field_name, maximum=500)

    @field_validator("value")
    @classmethod
    def normalize_value(cls, value: Any) -> Any:
        return _safe_provider_value(value)

    @model_validator(mode="after")
    def prevent_unverified_decision_basis(self) -> "SnapchatDecisionEvidenceInput":
        # Proposal callers (including the UI/user) are not an authority that
        # can promote a claim to verified.  Verified evidence is produced by
        # Mezan's internal collectors and stored in the immutable baseline.
        if self.verification_status == "verified":
            raise ValueError(
                "verified evidence may only be produced by Mezan collectors"
            )
        if self.verification_status != "verified" and self.used_in_decision:
            raise ValueError("only verified evidence may be used in the decision")
        if not self.used_in_decision and self.weight != 0:
            raise ValueError("unused evidence must have weight 0")
        return self


class SnapchatManagedProductInput(BaseModel):
    """A product the advertising entity is intended to promote."""

    model_config = ConfigDict(extra="forbid")

    product_id: str = Field(min_length=1, max_length=160)
    product_variant_id: str | None = Field(default=None, max_length=160)
    product_name: str | None = Field(default=None, max_length=300)

    @field_validator("product_id", "product_variant_id")
    @classmethod
    def normalize_product_identifiers(cls, value: str | None, info: Any) -> str | None:
        if value is None:
            return None
        return _identifier(value, field=info.field_name)

    @field_validator("product_name")
    @classmethod
    def normalize_product_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _text(value, field="product_name", maximum=300)


class SnapchatManagementProposalInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Action
    account_id: str = Field(min_length=1, max_length=160)
    target_id: str | None = Field(default=None, max_length=160)
    parent_id: str | None = Field(default=None, max_length=160)
    payload: dict[str, Any] = Field(default_factory=dict)
    reason: str = Field(min_length=5, max_length=500)
    idempotency_key: str = Field(min_length=8, max_length=128)
    activation_acknowledged: bool = False
    expected_outcome: dict[str, Any] | None = None
    supporting_evidence: list[SnapchatDecisionEvidenceInput] = Field(
        default_factory=list, max_length=30
    )
    products: list[SnapchatManagedProductInput] = Field(
        default_factory=list, max_length=20
    )
    trend_override_reason: str | None = Field(default=None, max_length=500)
    # The UI sends v2 explicitly.  A pre-v2 replica rejects the unknown field,
    # which prevents it from preparing a proposal that it cannot safely
    # execute during a rolling deployment.  Defaulting to v1 preserves
    # read/reconciliation compatibility for durable legacy proposals.
    safety_protocol_version: Literal[1, 2] = 1

    @field_validator("account_id", "target_id", "parent_id")
    @classmethod
    def normalize_identifiers(cls, value: str | None, info: Any) -> str | None:
        if value is None:
            return None
        return _identifier(value, field=info.field_name)

    @field_validator("reason")
    @classmethod
    def normalize_reason(cls, value: str) -> str:
        return _text(value, field="reason", maximum=500)

    @field_validator("trend_override_reason")
    @classmethod
    def normalize_trend_override_reason(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _text(value, field="trend_override_reason", maximum=500)

    @field_validator("idempotency_key")
    @classmethod
    def normalize_idempotency_key(cls, value: str) -> str:
        return _identifier(value, field="idempotency_key")

    @field_validator("payload")
    @classmethod
    def normalize_payload(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _bounded_payload(value)

    @field_validator("expected_outcome")
    @classmethod
    def normalize_expected_outcome(
        cls, value: dict[str, Any] | None
    ) -> dict[str, Any] | None:
        if value is None:
            return None
        return _bounded_payload(value)

    @model_validator(mode="after")
    def validate_relationships(self) -> "SnapchatManagementProposalInput":
        if self.action in UPDATE_ACTIONS and not self.target_id:
            raise ValueError("target_id is required for update actions")
        if (
            self.action
            in {"ad_squad.create", "ad_squad.update", "ad.create", "ad.update"}
            and not self.parent_id
        ):
            raise ValueError("parent_id is required for this action")
        if (
            self.action in DELIVERY_CREATE_ACTIONS
            and str(self.payload.get("status") or "PAUSED").upper() == "ACTIVE"
        ):
            raise ValueError("new delivery entities must be created PAUSED")
        if (
            str(self.payload.get("status") or "").upper() == "ACTIVE"
            and not self.activation_acknowledged
        ):
            raise ValueError("activation_acknowledged is required for ACTIVE status")
        return self


class SnapchatManagementApprovalInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirm_token: str = Field(min_length=16, max_length=240)
    expected_revision: int = Field(ge=1)


class SnapchatManagementRollbackInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirmation_phrase: str = Field(min_length=8, max_length=160)
    reason: str = Field(min_length=5, max_length=500)


def _allow_only(
    payload: dict[str, Any],
    allowed: set[str],
    *,
    allow_forced_status: bool = False,
) -> dict[str, Any]:
    accepted = allowed | ({"status"} if allow_forced_status else set())
    unknown = sorted(set(payload) - accepted)
    if unknown:
        raise ValueError(f"unsupported fields: {', '.join(unknown[:10])}")
    return dict(payload)


def _budget_guard(entity: dict[str, Any]) -> None:
    for key in ("daily_budget_micro",):
        if key not in entity:
            continue
        try:
            value = int(entity[key])
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{key} must be an integer") from exc
        if value < 5_000_000 or value > _max_daily_budget_micro():
            raise ValueError(
                f"{key} must be between 5000000 and {_max_daily_budget_micro()}"
            )
        entity[key] = value
    for key in ("lifetime_budget_micro", "lifetime_spend_cap_micro", "bid_micro"):
        if key not in entity:
            continue
        try:
            value = int(entity[key])
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{key} must be an integer") from exc
        if value < 0 or value > 50_000_000_000_000:
            raise ValueError(f"{key} is outside the safety range")
        entity[key] = value


def build_snapchat_operation(
    payload: SnapchatManagementProposalInput,
) -> dict[str, Any]:
    """Build one fixed provider request without accepting a caller URL/method."""
    action = payload.action
    entity = dict(payload.payload)
    account_id = payload.account_id
    target_id = payload.target_id
    parent_id = payload.parent_id
    operation: dict[str, Any]

    if action == "campaign.create":
        entity = _allow_only(entity, CAMPAIGN_CREATE_FIELDS, allow_forced_status=True)
        entity["name"] = _text(entity.get("name"), field="name")
        entity["start_time"] = _text(
            entity.get("start_time"), field="start_time", maximum=80
        )
        entity["ad_account_id"] = account_id
        entity["status"] = "PAUSED"
        entity.setdefault("buy_model", "AUCTION")
        objective = entity.get("objective_v2_properties")
        if not isinstance(objective, dict) or not objective.get("objective_v2_type"):
            raise ValueError("objective_v2_properties.objective_v2_type is required")
        operation = {
            "method": "POST",
            "path": f"/adaccounts/{account_id}/campaigns",
            "plural": "campaigns",
            "singular": "campaign",
            "entity_type": "campaign",
            "body": {"campaigns": [entity]},
        }
    elif action == "campaign.update":
        entity = _allow_only(entity, CAMPAIGN_UPDATE_FIELDS)
        operation = _patch_operation(
            path=f"/adaccounts/{account_id}/campaigns/{target_id}",
            plural="campaigns",
            singular="campaign",
            entity_type="campaign",
            changes=entity,
        )
    elif action == "ad_squad.create":
        entity = _allow_only(entity, AD_SQUAD_CREATE_FIELDS, allow_forced_status=True)
        entity["name"] = _text(entity.get("name"), field="name")
        for required in (
            "type",
            "targeting",
            "placement_v2",
            "billing_event",
            "bid_strategy",
            "optimization_goal",
        ):
            if entity.get(required) in (None, "", {}, []):
                raise ValueError(f"{required} is required")
        budget_fields = [
            key
            for key in ("daily_budget_micro", "lifetime_budget_micro")
            if key in entity
        ]
        if not budget_fields:
            raise ValueError("daily_budget_micro or lifetime_budget_micro is required")
        if len(budget_fields) != 1:
            raise ValueError(
                "exactly one of daily_budget_micro or lifetime_budget_micro is required"
            )
        optimization_goal = str(entity.get("optimization_goal") or "").upper()
        entity["optimization_goal"] = optimization_goal
        if _is_pixel_optimization_goal(optimization_goal):
            entity["pixel_id"] = _identifier(
                entity.get("pixel_id"), field="pixel_id"
            )
            conversion_window = str(entity.get("conversion_window") or "").upper()
            if conversion_window not in PIXEL_CONVERSION_WINDOWS:
                raise ValueError(
                    "conversion_window is required for a PIXEL optimization goal"
                )
            entity["conversion_window"] = conversion_window
        elif entity.get("pixel_id"):
            entity["pixel_id"] = _identifier(
                entity.get("pixel_id"), field="pixel_id"
            )
        entity["delivery_constraint"] = (
            "DAILY_BUDGET"
            if budget_fields == ["daily_budget_micro"]
            else "LIFETIME_BUDGET"
        )
        entity["campaign_id"] = parent_id
        entity["status"] = "PAUSED"
        operation = {
            "method": "POST",
            "path": f"/campaigns/{parent_id}/adsquads",
            "plural": "adsquads",
            "singular": "adsquad",
            "entity_type": "ad_squad",
            "body": {"adsquads": [entity]},
        }
    elif action == "ad_squad.update":
        entity = _allow_only(entity, AD_SQUAD_UPDATE_FIELDS)
        operation = _patch_operation(
            path=f"/campaigns/{parent_id}/adsquads/{target_id}",
            plural="adsquads",
            singular="adsquad",
            entity_type="ad_squad",
            changes=entity,
        )
    elif action == "ad.create":
        entity = _allow_only(entity, AD_CREATE_FIELDS, allow_forced_status=True)
        entity["name"] = _text(entity.get("name"), field="name")
        entity["creative_id"] = _identifier(
            entity.get("creative_id"), field="creative_id"
        )
        entity["type"] = _text(entity.get("type"), field="type", maximum=80).upper()
        entity["ad_squad_id"] = parent_id
        entity["status"] = "PAUSED"
        operation = {
            "method": "POST",
            "path": f"/adsquads/{parent_id}/ads",
            "plural": "ads",
            "singular": "ad",
            "entity_type": "ad",
            "body": {"ads": [entity]},
        }
    elif action == "ad.update":
        entity = _allow_only(entity, AD_UPDATE_FIELDS)
        operation = _patch_operation(
            path=f"/adsquads/{parent_id}/ads/{target_id}",
            plural="ads",
            singular="ad",
            entity_type="ad",
            changes=entity,
        )
    elif action == "creative.create":
        entity = _allow_only(entity, CREATIVE_CREATE_FIELDS)
        for required in ("name", "type", "headline", "top_snap_media_id"):
            if not entity.get(required):
                raise ValueError(f"{required} is required")
        entity["name"] = _text(entity["name"], field="name")
        entity["headline"] = _text(entity["headline"], field="headline", maximum=34)
        entity["type"] = _text(entity["type"], field="type", maximum=80).upper()
        entity["top_snap_media_id"] = _identifier(
            entity["top_snap_media_id"], field="top_snap_media_id"
        )
        profile = entity.get("profile_properties")
        if not isinstance(profile, dict) or not profile.get("profile_id"):
            raise ValueError("profile_properties.profile_id is required")
        profile["profile_id"] = _identifier(profile["profile_id"], field="profile_id")
        if entity["type"] == "WEB_VIEW":
            if not entity.get("call_to_action"):
                raise ValueError("call_to_action is required for WEB_VIEW")
            web_view = entity.get("web_view_properties")
            if not isinstance(web_view, dict) or not str(
                web_view.get("url") or ""
            ).startswith(("https://", "http://")):
                raise ValueError("web_view_properties.url is required for WEB_VIEW")
        entity["ad_account_id"] = account_id
        operation = {
            "method": "POST",
            "path": f"/adaccounts/{account_id}/creatives",
            "plural": "creatives",
            "singular": "creative",
            "entity_type": "creative",
            "body": {"creatives": [entity]},
        }
    else:  # pragma: no cover - Literal and Pydantic reject this first
        raise ValueError("unsupported action")

    _budget_guard(entity)
    operation["account_id"] = account_id
    operation["action"] = action
    operation["target_id"] = target_id
    operation["parent_id"] = parent_id
    operation["activates_delivery"] = (
        str(entity.get("status") or "").upper() == "ACTIVE"
    )
    operation["summary"] = _operation_summary(action, entity, target_id)
    return operation


def _patch_operation(
    *, path: str, plural: str, singular: str, entity_type: str, changes: dict[str, Any]
) -> dict[str, Any]:
    if not changes:
        raise ValueError("at least one change is required")
    _budget_guard(changes)
    if "status" in changes:
        status_value = str(changes["status"] or "").upper()
        if status_value not in {"ACTIVE", "PAUSED"}:
            raise ValueError("status must be ACTIVE or PAUSED")
        changes["status"] = status_value
    patches = [
        {"op": "replace", "path": f"/{key}", "value": value}
        for key, value in sorted(changes.items())
    ]
    return {
        "method": "PATCH",
        "path": path,
        "plural": plural,
        "singular": singular,
        "entity_type": entity_type,
        "body": patches,
        "changes": changes,
    }


def _operation_summary(
    action: str, entity: dict[str, Any], target_id: str | None
) -> dict[str, Any]:
    return {
        "action": action,
        "target_id": target_id,
        "name": entity.get("name"),
        "status": entity.get("status"),
        "daily_budget_micro": entity.get("daily_budget_micro"),
        "lifetime_budget_micro": entity.get("lifetime_budget_micro"),
        "lifetime_spend_cap_micro": entity.get("lifetime_spend_cap_micro"),
        "pixel_id": entity.get("pixel_id"),
        "optimization_goal": entity.get("optimization_goal"),
        "conversion_window": entity.get("conversion_window"),
        "changed_fields": sorted(entity),
    }


def snapchat_management_intent_fingerprint(operation: dict[str, Any]) -> str:
    """Fence logically identical creates even when their proposal IDs differ."""
    body = operation.get("body")
    rows = body.get(operation.get("plural")) if isinstance(body, dict) else None
    entity = dict(rows[0]) if isinstance(rows, list) and rows and isinstance(rows[0], dict) else {}
    for provider_field in (
        "status",
        "ad_account_id",
        "campaign_id",
        "ad_squad_id",
        "delivery_constraint",
    ):
        entity.pop(provider_field, None)
    canonical = {
        "action": operation.get("action"),
        "account_id": operation.get("account_id"),
        "parent_id": operation.get("parent_id"),
        "entity": _canonical_control_value(entity),
    }
    return hashlib.sha256(
        json.dumps(
            canonical,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()


def _assert_stored_operation_integrity(
    row: dict[str, Any], operation: dict[str, Any]
) -> None:
    action = str(row.get("action") or "")
    account_id = _identifier(row.get("account_id"), field="account_id")
    target_id = row.get("target_id")
    parent_id = row.get("parent_id")
    if action in UPDATE_ACTIONS:
        target_id = _identifier(target_id, field="target_id")
    if action in {"ad_squad.create", "ad_squad.update", "ad.create", "ad.update"}:
        parent_id = _identifier(parent_id, field="parent_id")
    expected = {
        "campaign.create": (
            "POST",
            f"/adaccounts/{account_id}/campaigns",
            "campaign",
            "campaigns",
            "campaign",
        ),
        "campaign.update": (
            "PATCH",
            f"/adaccounts/{account_id}/campaigns/{target_id}",
            "campaign",
            "campaigns",
            "campaign",
        ),
        "ad_squad.create": (
            "POST",
            f"/campaigns/{parent_id}/adsquads",
            "ad_squad",
            "adsquads",
            "adsquad",
        ),
        "ad_squad.update": (
            "PATCH",
            f"/campaigns/{parent_id}/adsquads/{target_id}",
            "ad_squad",
            "adsquads",
            "adsquad",
        ),
        "ad.create": ("POST", f"/adsquads/{parent_id}/ads", "ad", "ads", "ad"),
        "ad.update": (
            "PATCH",
            f"/adsquads/{parent_id}/ads/{target_id}",
            "ad",
            "ads",
            "ad",
        ),
        "creative.create": (
            "POST",
            f"/adaccounts/{account_id}/creatives",
            "creative",
            "creatives",
            "creative",
        ),
    }.get(action)
    if not expected:
        raise ValueError("stored action is invalid")
    actual = (
        operation.get("method"),
        operation.get("path"),
        operation.get("entity_type"),
        operation.get("plural"),
        operation.get("singular"),
    )
    if actual != expected:
        raise ValueError("stored provider operation does not match the proposal")
    if operation.get("account_id") != account_id:
        raise ValueError("stored provider account does not match the proposal")
    body = operation.get("body")
    if expected[0] == "PATCH":
        changes = operation.get("changes")
        if not isinstance(body, list) or not isinstance(changes, dict):
            raise ValueError("stored patch operation is invalid")
        expected_patches = [
            {"op": "replace", "path": f"/{key}", "value": value}
            for key, value in sorted(changes.items())
        ]
        if body != expected_patches:
            raise ValueError("stored patch body does not match its preview")
        entity = changes
    else:
        rows = body.get(expected[3]) if isinstance(body, dict) else None
        if (
            not isinstance(rows, list)
            or len(rows) != 1
            or not isinstance(rows[0], dict)
        ):
            raise ValueError("stored create operation is invalid")
        entity = rows[0]
        if (
            action in DELIVERY_CREATE_ACTIONS
            and str(entity.get("status") or "").upper() != "PAUSED"
        ):
            raise ValueError("stored create operation is not PAUSED")
        if (
            action in {"campaign.create", "creative.create"}
            and str(entity.get("ad_account_id") or "") != account_id
        ):
            raise ValueError("stored entity account does not match the proposal")
        if action == "ad_squad.create" and str(entity.get("campaign_id") or "") != str(
            parent_id or ""
        ):
            raise ValueError("stored entity parent does not match the proposal")
        if action == "ad.create" and str(entity.get("ad_squad_id") or "") != str(
            parent_id or ""
        ):
            raise ValueError("stored entity parent does not match the proposal")
    _budget_guard(entity)
    activates_delivery = str(entity.get("status") or "").upper() == "ACTIVE"
    if operation.get("activates_delivery") is not activates_delivery:
        raise ValueError("stored activation flag does not match the proposal")
    if action in DELIVERY_CREATE_ACTIONS | CREATIVE_ACTIONS:
        stored_intent = str(row.get("intent_fingerprint") or "")
        if int(row.get("safety_protocol_version") or 1) == 2 and (
            not stored_intent
            or stored_intent != snapchat_management_intent_fingerprint(operation)
        ):
            raise ValueError("stored create intent fingerprint is invalid")
    if action == "ad_squad.create" and int(
        row.get("safety_protocol_version") or 1
    ) == 2:
        goal = str(entity.get("optimization_goal") or "").upper()
        if _is_pixel_optimization_goal(goal):
            pixel_id = _identifier(entity.get("pixel_id"), field="pixel_id")
            window = str(entity.get("conversion_window") or "").upper()
            proof = row.get("pixel_eligibility")
            if (
                not isinstance(proof, dict)
                or proof.get("verified") is not True
                or str(proof.get("pixel_id") or "") != pixel_id
                or str(proof.get("account_id") or "") != account_id
                or str(proof.get("optimization_goal") or "").upper() != goal
                or str(proof.get("conversion_window") or "").upper() != window
                or str(proof.get("eligibility_status") or "").upper()
                not in PIXEL_ELIGIBLE_STATUSES
                or proof.get("membership_verified") is not True
                or str(proof.get("pixel_status") or "").upper() != "ACTIVE"
                or str(proof.get("pixel_effective_status") or "").upper()
                != "ACTIVE"
            ):
                raise ValueError("stored Pixel eligibility proof is invalid")


def _safe_nested_provider_error_detail(payload: Any) -> dict[str, str]:
    """Find bounded, allow-listed error details inside bulk API responses."""
    queue: list[tuple[Any, int]] = [(payload, 0)]
    seen: set[int] = set()
    best: dict[str, str] = {}
    best_score = -1
    cursor = 0
    generic_statuses = {
        "SUCCESS",
        "OK",
        "FAILURE",
        "FAILED",
        "FAIL",
        "ERROR",
        "UNKNOWN",
    }
    while cursor < len(queue) and cursor < 32:
        value, depth = queue[cursor]
        cursor += 1
        if not isinstance(value, (dict, list)) or id(value) in seen:
            continue
        seen.add(id(value))
        if isinstance(value, dict):
            candidate = _safe_provider_error_detail(value)
            code = str(candidate.get("provider_error_code") or "")
            message = str(candidate.get("provider_error_message") or "")
            if code.upper() in {"SUCCESS", "OK"} and not message:
                candidate = {}
                code = ""
            score = (
                (4 if message else 0)
                + (2 if code and code.upper() not in generic_statuses else 0)
                + (1 if code else 0)
            )
            if candidate and score > best_score:
                best = candidate
                best_score = score
            children = value.values()
        else:
            children = value[:8]
        if depth < 4:
            queue.extend(
                (nested, depth + 1)
                for nested in children
                if isinstance(nested, (dict, list))
            )
    return best


def _explicit_provider_validation_no_write_proof(
    payload: Any, *, http_status: int, expected_plural: str | None = None
) -> dict[str, Any] | None:
    """Recognize only explicit whole-request validation rejection evidence."""
    if not isinstance(payload, dict):
        return None
    request_status = str(payload.get("request_status") or "").upper()
    if "FAIL" not in request_status and "ERROR" not in request_status:
        return None
    wrappers: list[dict[str, Any]] = []
    present_plural_count = 0
    for plural in ("campaigns", "adsquads", "ads", "creatives"):
        if plural not in payload:
            continue
        present_plural_count += 1
        value = payload.get(plural)
        if (
            not isinstance(value, list)
            or len(value) != 1
            or not isinstance(value[0], dict)
        ):
            return None
        wrappers.append(value[0])
    if (
        present_plural_count != 1
        or len(wrappers) != 1
        or not expected_plural
        or expected_plural not in payload
    ):
        # A bare HTTP/top-level rejection does not prove that the provider did
        # not apply an earlier duplicate or partially processed create.
        return None
    expected_singular = {
        "campaigns": "campaign",
        "adsquads": "adsquad",
        "ads": "ad",
        "creatives": "creative",
    }.get(str(expected_plural or ""))
    if not expected_singular:
        return None
    for wrapper in wrappers:
        sub_status = str(wrapper.get("sub_request_status") or "").upper()
        if "FAIL" not in sub_status and "ERROR" not in sub_status:
            return None
        for singular in ("campaign", "adsquad", "ad", "creative"):
            if singular in wrapper and singular != expected_singular:
                return None
            entity = wrapper.get(singular)
            if singular in wrapper:
                if not isinstance(entity, dict) or entity.get("id"):
                    return None
    return {
        "proof_kind": "explicit_whole_request_validation_rejection",
        "http_status": http_status,
        "request_status": request_status,
        "failed_subrequests": len(wrappers),
        "expected_plural": expected_plural,
    }


class SnapchatManagementProvider:
    def __init__(self, db: Any, user_id: str) -> None:
        self.db = db
        self.user_id = user_id
        self.context = SnapchatSyncContext(db=db, user_id=user_id)
        self._management_roles_cache: dict[str, tuple[set[str], set[str]]] = {}

    async def _request(
        self,
        method: str,
        path: str,
        *,
        body: Any = None,
        content_type: str = "application/json",
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        try:
            token = await self.context.access_token()
        except SnapchatNativeSyncError as exc:
            # The shared credential loader intentionally raises its own domain
            # error.  Convert it at the management boundary so FastAPI always
            # returns a bounded JSON response instead of an unhandled 500.
            detail: dict[str, Any] = {
                "code": exc.code,
                "message": exc.message,
                "retryable": bool(exc.retryable),
            }
            if isinstance(exc.result, dict) and exc.result.get("needs_reauth") is True:
                detail["needs_reauth"] = True
            raise HTTPException(
                status_code=exc.status_code,
                detail=detail,
            ) from exc
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        if body is not None:
            headers["Content-Type"] = content_type
        try:
            async with httpx.AsyncClient(timeout=35.0) as client:
                response = await client.request(
                    method,
                    f"{SNAPCHAT_API_BASE}{path}",
                    headers=headers,
                    json=body,
                    params=params,
                )
        except httpx.HTTPError as exc:
            raise HTTPException(
                status_code=502,
                detail={
                    "code": "snapchat_management_network_error",
                    "message": "تعذر الاتصال بـ Snapchat أثناء تنفيذ العملية.",
                },
            ) from exc
        if response.status_code >= 400:
            try:
                provider_payload = response.json() or {}
            except (TypeError, ValueError):
                provider_payload = {}
            safe_detail = _safe_nested_provider_error_detail(provider_payload)
            expected_plural = (
                next(
                    (
                        plural
                        for plural in ("campaigns", "adsquads", "ads", "creatives")
                        if isinstance(body, dict) and plural in body
                    ),
                    None,
                )
                if method.upper() == "POST"
                else None
            )
            no_write_proof = _explicit_provider_validation_no_write_proof(
                provider_payload,
                http_status=response.status_code,
                expected_plural=expected_plural,
            )
            if response.status_code in {401, 403}:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "snapchat_management_needs_reauth_or_role",
                        "message": "يحتاج اتصال Snapchat إلى إعادة توثيق أو دور إدارة مناسب.",
                        **safe_detail,
                    },
                )
            raise HTTPException(
                status_code=502,
                detail={
                    "code": f"snapchat_management_http_{response.status_code}",
                    "message": "رفض Snapchat العملية المقترحة.",
                    **safe_detail,
                    **(
                        {"provider_no_write_proof": no_write_proof}
                        if no_write_proof
                        else {}
                    ),
                },
            )
        try:
            payload = response.json() or {}
        except (TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=502,
                detail={
                    "code": "snapchat_management_invalid_json",
                    "message": "أعاد Snapchat استجابة غير صالحة.",
                },
            ) from exc
        if not isinstance(payload, dict):
            raise HTTPException(
                status_code=502, detail={"code": "snapchat_management_invalid_payload"}
            )
        request_status = str(payload.get("request_status") or "").upper()
        if request_status not in {"SUCCESS", "OK"}:
            expected_plural = (
                next(
                    (
                        plural
                        for plural in ("campaigns", "adsquads", "ads", "creatives")
                        if isinstance(body, dict) and plural in body
                    ),
                    None,
                )
                if method.upper() == "POST"
                else None
            )
            no_write_proof = _explicit_provider_validation_no_write_proof(
                payload,
                http_status=response.status_code,
                expected_plural=expected_plural,
            )
            raise HTTPException(
                status_code=502,
                detail={
                    "code": "snapchat_management_request_failed",
                    "message": "أبلغ Snapchat عن فشل العملية.",
                    **_safe_nested_provider_error_detail(payload),
                    **(
                        {"provider_no_write_proof": no_write_proof}
                        if no_write_proof
                        else {}
                    ),
                },
            )
        return payload

    async def _list_complete(
        self,
        path: str,
        *,
        plural: str,
        singular: str,
        params: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Read every bounded, trusted page or fail without using partial data."""
        next_path = path
        next_params = {"limit": 1000, **(params or {})}
        rows: list[dict[str, Any]] = []
        seen: set[str] = set()
        for page in range(MAX_MANAGEMENT_LIST_PAGES):
            payload = await self._request("GET", next_path, params=next_params)
            wrappers = payload.get(plural)
            if not isinstance(wrappers, list):
                raise HTTPException(
                    status_code=502,
                    detail={
                        "code": "snapchat_management_catalog_incomplete",
                        "message": "تعذر إكمال قراءة كتالوج Snapchat بأمان.",
                    },
                )
            for wrapper in wrappers:
                if not isinstance(wrapper, dict):
                    raise HTTPException(
                        status_code=502,
                        detail={"code": "snapchat_management_catalog_incomplete"},
                    )
                sub_status = str(wrapper.get("sub_request_status") or "").upper()
                if sub_status not in {"SUCCESS", "OK"}:
                    raise HTTPException(
                        status_code=502,
                        detail={
                            "code": "snapchat_management_catalog_incomplete",
                            **_safe_nested_provider_error_detail(wrapper),
                        },
                    )
                if singular not in wrapper:
                    raise HTTPException(
                        status_code=502,
                        detail={"code": "snapchat_management_catalog_incomplete"},
                    )
                entity = wrapper.get(singular)
                if not isinstance(entity, dict) or (
                    not entity.get("id")
                    and singular != "campaign_eligibility"
                ):
                    raise HTTPException(
                        status_code=502,
                        detail={"code": "snapchat_management_catalog_incomplete"},
                    )
                entity_id = str(
                    entity.get("id")
                    or (
                        f"{entity.get('optimization_goal')}:{entity.get('conversion_window')}"
                        if singular == "campaign_eligibility"
                        else ""
                    )
                ).strip()
                if not entity_id:
                    raise HTTPException(
                        status_code=502,
                        detail={"code": "snapchat_management_catalog_incomplete"},
                    )
                if entity_id in seen:
                    existing = next(
                        (
                            row
                            for row in rows
                            if str(row.get("id") or "").strip() == entity_id
                        ),
                        None,
                    )
                    safe_duplicate = _safe_provider_value(entity)
                    if isinstance(safe_duplicate, dict):
                        safe_duplicate.setdefault("id", entity_id)
                    if not isinstance(safe_duplicate, dict) or (
                        _canonical_control_value(existing)
                        != _canonical_control_value(safe_duplicate)
                    ):
                        raise HTTPException(
                            status_code=502,
                            detail={
                                "code": "snapchat_management_catalog_duplicate_conflict"
                            },
                        )
                    continue
                seen.add(entity_id)
                safe = _safe_provider_value(entity)
                if isinstance(safe, dict):
                    safe.setdefault("id", entity_id)
                    rows.append(safe)
                if len(rows) > MAX_MANAGEMENT_LIST_ROWS:
                    raise HTTPException(
                        status_code=409,
                        detail={"code": "snapchat_management_catalog_limit_reached"},
                    )
            paging = payload.get("paging")
            if paging is None:
                paging = {}
            if not isinstance(paging, dict):
                raise HTTPException(
                    status_code=502,
                    detail={"code": "snapchat_management_catalog_incomplete"},
                )
            has_next = "next_link" in paging
            raw_next = paging.get("next_link")
            # The provider may omit ``next_link`` or return null / the exact
            # empty string on the terminal page.  Every other supplied value
            # must be a non-empty trusted URL.  In particular, falsey values
            # such as 0, False, [], {}, and whitespace are malformed rather
            # than evidence that the catalogue is complete.
            terminal_next = raw_next is None or raw_next == ""
            if has_next and not terminal_next:
                if not isinstance(raw_next, str) or not _safe_next_url(raw_next):
                    raise HTTPException(
                        status_code=502,
                        detail={"code": "snapchat_management_catalog_incomplete"},
                    )
            trusted_next = _safe_next_url(raw_next)
            if not trusted_next:
                return rows
            if not trusted_next.startswith(f"{SNAPCHAT_API_BASE}/"):
                raise HTTPException(
                    status_code=502,
                    detail={"code": "snapchat_management_catalog_incomplete"},
                )
            next_path = trusted_next[len(SNAPCHAT_API_BASE) :]
            next_params = None
        raise HTTPException(
            status_code=409,
            detail={"code": "snapchat_management_catalog_page_limit_reached"},
        )

    async def list_account_pixels(self, account_id: str) -> list[dict[str, Any]]:
        return await self._list_complete(
            f"/adaccounts/{account_id}/pixels",
            plural="pixels",
            singular="pixel",
        )

    async def pixel_eligibility(
        self,
        *,
        account_id: str,
        pixel_id: str,
        optimization_goal: str,
        conversion_window: str,
    ) -> dict[str, Any]:
        eligibility_rows = await self._list_complete(
            f"/pixels/{pixel_id}/campaign_eligibilities",
            plural="campaign_eligibilities",
            singular="campaign_eligibility",
            params={
                "ad_account_id": account_id,
                "optimization_goal": optimization_goal,
                "conversion_window": conversion_window,
            },
        )
        matches: list[dict[str, Any]] = []
        for value in eligibility_rows:
            if (
                str(value.get("optimization_goal") or "").upper()
                == optimization_goal
                and str(value.get("conversion_window") or "").upper()
                == conversion_window
            ):
                matches.append(value)
        if len(matches) != 1:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "snapchat_management_pixel_eligibility_unavailable",
                    "matching_rows": len(matches),
                },
            )
        eligibility_status = str(
            matches[0].get("eligibility_status") or ""
        ).upper()
        proof = {
            "verified": eligibility_status in PIXEL_ELIGIBLE_STATUSES,
            "pixel_id": pixel_id,
            "account_id": account_id,
            "optimization_goal": optimization_goal,
            "conversion_window": conversion_window,
            "eligibility_status": eligibility_status,
            "warning": eligibility_status == "ELIGIBLE_WARNING",
            "verified_at": _iso(),
        }
        if eligibility_status not in PIXEL_ELIGIBLE_STATUSES:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "snapchat_management_pixel_ineligible",
                    **proof,
                    "ineligible_properties": _safe_provider_value(
                        matches[0].get("ineligible_properties") or []
                    ),
                },
            )
        return proof

    async def validate_pixel_ad_squad_intent(
        self,
        *,
        account_id: str,
        parent_id: str,
        operation: dict[str, Any],
        reread_parent: bool = True,
    ) -> dict[str, Any] | None:
        entity = _operation_create_entity(operation)
        goal = str(entity.get("optimization_goal") or "").upper()
        if not _is_pixel_optimization_goal(goal):
            return None
        pixel_id = _identifier(entity.get("pixel_id"), field="pixel_id")
        conversion_window = str(entity.get("conversion_window") or "").upper()
        if conversion_window not in PIXEL_CONVERSION_WINDOWS:
            raise HTTPException(
                status_code=422,
                detail={"code": "snapchat_management_pixel_conversion_window_required"},
            )
        if reread_parent:
            parent = await self.read_entity("campaign", parent_id)
            if str(parent.get("id") or "") != parent_id:
                raise HTTPException(
                    status_code=409,
                    detail={"code": "snapchat_management_target_parent_mismatch"},
                )
            if str(parent.get("ad_account_id") or "") != account_id:
                raise HTTPException(
                    status_code=409,
                    detail={"code": "snapchat_management_parent_account_mismatch"},
                )
        pixels = await self.list_account_pixels(account_id)
        matching = [row for row in pixels if str(row.get("id") or "") == pixel_id]
        if len(matching) != 1:
            raise HTTPException(
                status_code=409,
                detail={"code": "snapchat_management_pixel_account_mismatch"},
            )
        pixel_status = str(matching[0].get("status") or "").upper()
        effective_status = str(matching[0].get("effective_status") or "").upper()
        if pixel_status != "ACTIVE" or effective_status != "ACTIVE":
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "snapchat_management_pixel_not_active",
                    "status": pixel_status or None,
                    "effective_status": effective_status or None,
                },
            )
        proof = await self.pixel_eligibility(
            account_id=account_id,
            pixel_id=pixel_id,
            optimization_goal=goal,
            conversion_window=conversion_window,
        )
        return {
            **proof,
            "membership_verified": True,
            "pixel_status": pixel_status,
            "pixel_effective_status": effective_status,
            "membership_verified_at": _iso(),
        }

    async def validate_ad_create_dependencies(
        self,
        *,
        account_id: str,
        parent_id: str,
        operation: dict[str, Any],
    ) -> dict[str, Any]:
        """Re-read the full ad dependency chain immediately before creation."""
        entity = _operation_create_entity(operation)
        requested_ad_type = str(entity.get("type") or "").strip().upper()
        creative_id = _identifier(entity.get("creative_id"), field="creative_id")
        ad_squad = await self.read_entity("ad_squad", parent_id)
        if str(ad_squad.get("id") or "") != parent_id:
            raise HTTPException(
                status_code=409,
                detail={"code": "snapchat_management_target_parent_mismatch"},
            )
        campaign_id = _identifier(
            ad_squad.get("campaign_id"), field="campaign_id"
        )
        campaign = await self.read_entity("campaign", campaign_id)
        if str(campaign.get("id") or "") != campaign_id:
            raise HTTPException(
                status_code=409,
                detail={"code": "snapchat_management_campaign_identity_mismatch"},
            )
        if str(campaign.get("ad_account_id") or "") != account_id:
            raise HTTPException(
                status_code=409,
                detail={"code": "snapchat_management_parent_account_mismatch"},
            )
        creative = await self.read_entity("creative", creative_id)
        if str(creative.get("id") or "") != creative_id:
            raise HTTPException(
                status_code=409,
                detail={"code": "snapchat_management_creative_identity_mismatch"},
            )
        if str(creative.get("ad_account_id") or "") != account_id:
            raise HTTPException(
                status_code=409,
                detail={"code": "snapchat_management_creative_account_mismatch"},
            )
        creative_type = str(creative.get("type") or "").strip().upper()
        allowed_ad_types = CREATIVE_TYPE_TO_AD_TYPES.get(creative_type) or ()
        if requested_ad_type not in allowed_ad_types:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "snapchat_management_creative_ad_type_mismatch",
                    "creative_type": creative_type,
                    "requested_ad_type": requested_ad_type,
                    "allowed_ad_types": list(allowed_ad_types),
                },
            )
        return {
            "verified": True,
            "ad_squad_id": parent_id,
            "campaign_id": campaign_id,
            "creative_id": creative_id,
            "creative_type": creative_type,
            "requested_ad_type": requested_ad_type,
            "verified_at": _iso(),
        }

    async def list_create_reconciliation_candidates(
        self, operation: dict[str, Any]
    ) -> list[dict[str, Any]]:
        entity_type = str(operation.get("entity_type") or "")
        account_id = str(operation.get("account_id") or "")
        config = {
            "campaign": (
                f"/adaccounts/{account_id}/campaigns",
                "campaigns",
                "campaign",
            ),
            "ad_squad": (
                f"/adaccounts/{account_id}/adsquads",
                "adsquads",
                "adsquad",
            ),
            "ad": (
                f"/adaccounts/{account_id}/ads",
                "ads",
                "ad",
            ),
        }.get(entity_type)
        if not config:
            raise HTTPException(
                status_code=409,
                detail={"code": "snapchat_management_reconciliation_unsupported"},
            )
        path, plural, singular = config
        return await self._list_complete(
            path,
            plural=plural,
            singular=singular,
            # Deleted-aware catalogs are required for safe absence proof. Snap
            # forbids combining this flag with sort, so consume every bounded
            # page in provider order.
            params={"read_deleted_entities": "true"},
        )

    async def read_entity(self, entity_type: str, entity_id: str) -> dict[str, Any]:
        path_kind = {
            "campaign": "campaigns",
            "ad_squad": "adsquads",
            "ad": "ads",
            "creative": "creatives",
        }[entity_type]
        payload = await self._request("GET", f"/{path_kind}/{entity_id}")
        return _extract_entity(
            payload, path_kind, "adsquad" if entity_type == "ad_squad" else entity_type
        )

    async def execute(self, operation: dict[str, Any]) -> dict[str, Any]:
        content_type = (
            "application/json-patch+json"
            if operation["method"] == "PATCH"
            else "application/json"
        )
        payload = await self._request(
            operation["method"],
            operation["path"],
            body=operation["body"],
            content_type=content_type,
        )
        return _extract_entity(payload, operation["plural"], operation["singular"])

    async def management_role(
        self, account: dict[str, Any], action: str
    ) -> dict[str, Any]:
        organization_id = str(account.get("organization_id") or "").strip()
        account_id = str(account.get("ad_account_id") or "").strip()
        cached = self._management_roles_cache.get(account_id)
        if cached is None:
            organizations = await self._list_complete(
                "/me/organizations",
                plural="organizations",
                singular="organization",
                params={"with_ad_accounts": "true"},
            )
            member_id = ""
            for org in organizations:
                if str(org.get("id") or "") == organization_id:
                    member_id = str(org.get("my_member_id") or "").strip()
                    break
            if not member_id:
                return {
                    "allowed": False,
                    "role": None,
                    "reason": "member_identity_missing",
                }
            roles = await self._list_complete(
                f"/members/{member_id}/roles",
                plural="roles",
                singular="role",
                params={"limit": 200},
            )
            account_roles: set[str] = set()
            organization_roles: set[str] = set()
            for role in roles:
                role_type = str(role.get("type") or "").lower()
                if str(role.get("ad_account_id") or "") == account_id:
                    account_roles.add(role_type)
                if str(role.get("organization_id") or "") == organization_id:
                    organization_roles.add(role_type)
            self._management_roles_cache[account_id] = (
                account_roles,
                organization_roles,
            )
        else:
            account_roles, organization_roles = cached
        allowed_roles = {"admin", "general"}
        if action in CREATIVE_ACTIONS:
            allowed_roles.add("creative")
        allowed = bool(account_roles & allowed_roles or "admin" in organization_roles)
        visible_role = sorted(
            account_roles & allowed_roles or organization_roles & {"admin"}
        )
        return {
            "allowed": allowed,
            "role": visible_role[0] if visible_role else None,
            "reason": None if allowed else "snapchat_management_role_missing",
        }


def _extract_entity(
    payload: dict[str, Any], plural: str, singular: str
) -> dict[str, Any]:
    present_operation_plurals = [
        key
        for key in ("campaigns", "adsquads", "ads", "creatives")
        if key in payload
    ]
    if present_operation_plurals != [plural]:
        raise HTTPException(
            status_code=502,
            detail={"code": "snapchat_management_entity_invalid"},
        )
    rows = payload.get(plural) or []
    if not isinstance(rows, list) or len(rows) != 1:
        raise HTTPException(
            status_code=502,
            detail={
                "code": "snapchat_management_entity_missing",
                "message": "لم يعد Snapchat الكيان المتوقع.",
            },
        )
    if not isinstance(rows[0], dict):
        raise HTTPException(
            status_code=502,
            detail={"code": "snapchat_management_entity_invalid"},
        )
    wrapper = rows[0]
    sub_status = str(wrapper.get("sub_request_status") or "").upper()
    if sub_status not in {"SUCCESS", "OK"}:
        raise HTTPException(
            status_code=502,
            detail={
                "code": "snapchat_management_subrequest_failed",
                "message": "رفض Snapchat الكيان داخل العملية.",
                **_safe_nested_provider_error_detail(wrapper),
            },
        )
    entity = wrapper.get(singular)
    if not isinstance(entity, dict) or not entity.get("id"):
        raise HTTPException(
            status_code=502, detail={"code": "snapchat_management_entity_invalid"}
        )
    safe = _safe_provider_value(entity)
    return safe if isinstance(safe, dict) else {}


async def ensure_snapchat_management_indexes(db: Any) -> None:
    proposals = _collection(db, PROPOSAL_COLLECTION)
    audit = _collection(db, AUDIT_COLLECTION)
    leases = _collection(db, ENTITY_LEASE_COLLECTION)
    await proposals.create_index(
        [("user_id", 1), ("proposal_id", 1)],
        unique=True,
        name="snap_management_proposal_unique",
    )
    await proposals.create_index(
        [("user_id", 1), ("idempotency_key", 1)],
        unique=True,
        name="snap_management_idempotency_unique",
    )
    await proposals.create_index(
        [("user_id", 1), ("status", 1), ("created_at", -1)],
        name="snap_management_status_latest",
    )
    await audit.create_index(
        [("user_id", 1), ("proposal_id", 1), ("occurred_at", 1)],
        name="snap_management_audit_timeline",
    )
    await leases.create_index(
        [
            ("user_id", 1),
            ("account_id", 1),
            ("entity_type", 1),
            ("entity_id", 1),
        ],
        unique=True,
        partialFilterExpression={"active": True},
        name="snap_management_active_entity_lease_unique",
    )
    await leases.create_index(
        [("user_id", 1), ("proposal_id", 1), ("acquired_at", -1)],
        name="snap_management_entity_lease_owner",
    )


async def _selected_account(db: Any, user_id: str, account_id: str) -> dict[str, Any]:
    accounts = await _load_selected_accounts(db, user_id)
    for account in accounts:
        if str(account.get("ad_account_id") or "") == account_id:
            return account
    raise HTTPException(
        status_code=409,
        detail={
            "code": "snapchat_management_account_not_selected",
            "message": "اختر حساب Snapchat داخل ميزان قبل إدارته.",
        },
    )


async def _audit(
    db: Any,
    *,
    user_id: str,
    proposal_id: str,
    event: str,
    actor_id: str,
    detail: dict[str, Any] | None = None,
) -> None:
    await _collection(db, AUDIT_COLLECTION).insert_one(
        {
            "audit_id": str(uuid.uuid4()),
            "user_id": user_id,
            "proposal_id": proposal_id,
            "event": event,
            "actor_id": actor_id,
            "detail": _safe_provider_value(detail or {}),
            "occurred_at": _iso(),
            "source_mode": SOURCE_MODE,
        }
    )


def _public_proposal(
    row: dict[str, Any], *, confirm_token: str | None = None
) -> dict[str, Any]:
    operation = dict(row.get("operation") or {})
    output = {
        "proposal_id": row.get("proposal_id"),
        "status": row.get("status"),
        "revision": int(row.get("revision") or 1),
        "action": row.get("action"),
        "account_id": row.get("account_id"),
        "target_id": row.get("target_id"),
        "parent_id": row.get("parent_id"),
        "reason": row.get("reason"),
        "preview": operation.get("summary") or {},
        "creates_paused": row.get("action") in DELIVERY_CREATE_ACTIONS,
        "activates_delivery": operation.get("activates_delivery") is True,
        "expires_at": row.get("expires_at"),
        "created_at": row.get("created_at"),
        "approved_at": row.get("approved_at"),
        "executed_at": row.get("executed_at"),
        "failed_at": row.get("failed_at"),
        "verification": row.get("verification"),
        "rollback": row.get("rollback"),
        "failure": _safe_provider_value(row.get("failure") or {}),
        "confirmation_phrase": f"تراجع {str(row.get('proposal_id') or '')[:8]}",
        "provider_write_reached": row.get("provider_write_reached") is True,
        "provider_write_state": row.get("provider_write_state") or "not_attempted",
        "provider_write_uncertain": row.get("provider_write_uncertain") is True,
        "provider_entity_id": row.get("provider_entity_id"),
        "execution_retryable": row.get("execution_retryable") is True,
        "automatic_retry_allowed": row.get("automatic_retry_allowed") is True,
        "recovery_action": row.get("recovery_action"),
        "rollback_write_state": row.get("rollback_write_state") or "not_attempted",
        "rollback_write_uncertain": row.get("rollback_write_uncertain") is True,
        "rollback_recovery_action": row.get("rollback_recovery_action"),
        "expected_outcome": _safe_provider_value(row.get("expected")),
        "baseline": _safe_provider_value(row.get("baseline")),
        "supporting_evidence": _safe_provider_value(row.get("decision_evidence") or []),
        "products": _safe_provider_value(row.get("products") or []),
        "product_link_state": row.get("product_link_state") or "not_supplied",
        "trend_review": _safe_provider_value(row.get("trend_review") or {}),
        "trend_override_reason": row.get("trend_override_reason"),
        "safety_protocol_version": int(
            row.get("safety_protocol_version") or 1
        ),
        "pixel_eligibility": _safe_provider_value(
            row.get("pixel_eligibility") or {}
        ),
        "execution_pixel_eligibility": _safe_provider_value(
            row.get("execution_pixel_eligibility") or {}
        ),
        "execution_ad_dependency_verification": _safe_provider_value(
            row.get("execution_ad_dependency_verification") or {}
        ),
        "intent_fingerprint": row.get("intent_fingerprint"),
        "accounting_write_reached": False,
        "qoyod_write_reached": False,
    }
    if confirm_token:
        output["confirm_token"] = confirm_token
    return output


def _is_delivery_increase(
    operation: dict[str, Any], original: dict[str, Any] | None
) -> bool:
    changes = operation.get("changes")
    if not isinstance(changes, dict):
        return False
    if str(changes.get("status") or "").upper() == "ACTIVE":
        return True
    for field in (
        "daily_budget_micro",
        "lifetime_budget_micro",
        "lifetime_spend_cap_micro",
    ):
        if field not in changes:
            continue
        old_value = (original or {}).get(field)
        try:
            new_value = int(changes[field])
            if (old_value is None and new_value > 0) or (
                old_value is not None and new_value > int(old_value)
            ):
                return True
        except (TypeError, ValueError):
            continue
    return False


def _is_pause_or_budget_decrease(
    operation: dict[str, Any], original: dict[str, Any] | None
) -> bool:
    changes = operation.get("changes")
    if not isinstance(changes, dict):
        return False
    if str(changes.get("status") or "").upper() == "PAUSED":
        return True
    if "daily_budget_micro" not in changes:
        return False
    old_value = (original or {}).get("daily_budget_micro")
    try:
        return old_value is not None and int(changes["daily_budget_micro"]) < int(
            old_value
        )
    except (TypeError, ValueError):
        return False


async def _capture_proposal_baseline(
    db: Any,
    user_id: str,
    *,
    account: dict[str, Any],
    account_id: str,
    campaign_id: str | None,
    ad_squad_id: str | None = None,
    ad_id: str | None = None,
    products: list[dict[str, Any]],
) -> dict[str, Any]:
    from .snapchat_decision_metrics import (
        capture_decision_baseline,
        unavailable_decision_baseline,
    )

    try:
        return await capture_decision_baseline(
            db,
            user_id,
            account_id=account_id,
            campaign_id=campaign_id,
            ad_squad_id=ad_squad_id,
            ad_id=ad_id,
            product_ids=[
                str(item.get("product_id") or "")
                for item in products
                if str(item.get("product_id") or "").strip()
            ],
            product_refs=products,
            account_timezone=str(
                account.get("timezone")
                or account.get("account_timezone")
                or "Asia/Riyadh"
            ),
        )
    except Exception as exc:
        # The exception itself may contain provider/customer data.  Persist only
        # its type while keeping the absence explicit in the immutable record.
        return unavailable_decision_baseline(
            account_id=account_id,
            campaign_id=campaign_id,
            reason=f"capture_failed:{type(exc).__name__}",
        )


async def _record_management_decision_deferred_safe(
    db: Any,
    user_id: str,
    actor_id: str,
    proposal_id: str,
    row: dict[str, Any],
) -> None:
    """Journal the terminal state without changing the provider-write result."""
    try:
        from .snapchat_decision_ledger import record_management_decision

        await record_management_decision(db, user_id, row)
    except Exception as exc:
        # Reconciliation can recover the complete proposal later.  Never turn a
        # verified provider success into a false failed status because the
        # internal journal was temporarily unavailable.
        try:
            await _audit(
                db,
                user_id=user_id,
                proposal_id=proposal_id,
                event="decision_ledger_record_deferred",
                actor_id=actor_id,
                detail={"error_type": type(exc).__name__},
            )
        except Exception:
            # The provider outcome is already durable in the proposal row;
            # both append-only stores can be repaired by reconciliation.
            return


async def _ensure_proposal_product_links(
    db: Any,
    user_id: str,
    actor_id: str,
    row: dict[str, Any],
) -> bool:
    products = [item for item in (row.get("products") or []) if isinstance(item, dict)]
    if not products:
        return True
    proposal_id = str(row.get("proposal_id") or "")
    try:
        from .campaign_product_associations import (
            attach_products_to_management_proposal,
        )

        links = await attach_products_to_management_proposal(
            db,
            user_id,
            proposal_id=proposal_id,
            provider=SNAPCHAT_PROVIDER_ID,
            account_id=str(row.get("account_id") or ""),
            products=products,
            actor_id=actor_id,
            observed_at=row.get("created_at") or _iso(),
            idempotency_prefix=f"proposal-product:{proposal_id}",
        )
        await _collection(db, PROPOSAL_COLLECTION).update_one(
            {"user_id": user_id, "proposal_id": proposal_id},
            {
                "$set": {
                    "product_link_state": "confirmed",
                    "product_link_count": len(links),
                    "product_links_recorded_at": _iso(),
                }
            },
        )
        return True
    except Exception as exc:
        await _collection(db, PROPOSAL_COLLECTION).update_one(
            {"user_id": user_id, "proposal_id": proposal_id},
            {
                "$set": {
                    "product_link_state": "deferred",
                    "product_link_error_type": type(exc).__name__,
                }
            },
        )
        try:
            await _audit(
                db,
                user_id=user_id,
                proposal_id=proposal_id,
                event="campaign_product_link_deferred",
                actor_id=actor_id,
                detail={"error_type": type(exc).__name__},
            )
        except Exception:
            pass
        return False


async def _adopt_proposal_products_deferred_safe(
    db: Any,
    user_id: str,
    actor_id: str,
    row: dict[str, Any],
) -> None:
    products = [item for item in (row.get("products") or []) if isinstance(item, dict)]
    if not products:
        return
    proposal_id = str(row.get("proposal_id") or "")
    action = str(row.get("action") or "")
    entity_type = action.split(".", 1)[0]
    provider_entity_id = str(row.get("provider_entity_id") or "")
    baseline = row.get("baseline") if isinstance(row.get("baseline"), dict) else {}
    campaign_id = str(baseline.get("campaign_id") or "")
    ad_squad_id: str | None = None
    ad_id: str | None = None
    if entity_type == "campaign":
        campaign_id = provider_entity_id
    elif entity_type == "ad_squad":
        campaign_id = str(row.get("parent_id") or campaign_id)
        ad_squad_id = provider_entity_id
    elif entity_type == "ad":
        ad_squad_id = str(row.get("parent_id") or "") or None
        ad_id = provider_entity_id
    if not campaign_id:
        return
    try:
        from .campaign_product_associations import (
            adopt_management_proposal_products,
        )

        links = await adopt_management_proposal_products(
            db,
            user_id,
            proposal_id=proposal_id,
            provider=SNAPCHAT_PROVIDER_ID,
            account_id=str(row.get("account_id") or ""),
            campaign_id=campaign_id,
            ad_squad_id=ad_squad_id,
            ad_id=ad_id,
            actor_id=actor_id,
            provider_verified_at=(
                (row.get("verification") or {}).get("verified_at") or _iso()
            ),
            provider_entity_verified=True,
            idempotency_prefix=f"provider-product:{proposal_id}",
        )
        await _collection(db, PROPOSAL_COLLECTION).update_one(
            {"user_id": user_id, "proposal_id": proposal_id},
            {
                "$set": {
                    "product_link_state": "adopted",
                    "provider_product_link_count": len(links),
                    "provider_products_adopted_at": _iso(),
                }
            },
        )
    except Exception as exc:
        await _collection(db, PROPOSAL_COLLECTION).update_one(
            {"user_id": user_id, "proposal_id": proposal_id},
            {
                "$set": {
                    "product_link_state": "adoption_deferred",
                    "product_link_error_type": type(exc).__name__,
                }
            },
        )


async def _retry_terminal_product_adoption(
    db: Any,
    user_id: str,
    actor_id: str,
    row: dict[str, Any],
) -> dict[str, Any]:
    """Retry only the local product adoption for a verified terminal write."""
    if (
        row.get("status") != "completed"
        or row.get("provider_write_uncertain") is True
        or not row.get("products")
        or row.get("product_link_state") not in {"confirmed", "adoption_deferred"}
    ):
        return row
    await _adopt_proposal_products_deferred_safe(db, user_id, actor_id, row)
    updated = (
        await _collection(db, PROPOSAL_COLLECTION).find_one(
            {
                "user_id": user_id,
                "proposal_id": str(row.get("proposal_id") or ""),
            },
            {"_id": 0},
        )
        or row
    )
    if updated.get("product_link_state") == "adopted":
        await _record_management_decision_deferred_safe(
            db,
            user_id,
            actor_id,
            str(row.get("proposal_id") or ""),
            updated,
        )
    return updated


async def create_snapchat_management_proposal(
    db: Any,
    user_id: str,
    actor_id: str,
    payload: SnapchatManagementProposalInput,
    *,
    provider: SnapchatManagementProvider | None = None,
) -> dict[str, Any]:
    request_fingerprint = snapchat_management_request_fingerprint(payload)
    existing = await _collection(db, PROPOSAL_COLLECTION).find_one(
        {"user_id": user_id, "idempotency_key": payload.idempotency_key},
        {"_id": 0},
    )
    if existing:
        existing_fingerprint = existing.get("request_fingerprint")
        if existing_fingerprint != request_fingerprint:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "snapchat_management_idempotency_conflict",
                    "message": ("مفتاح التكرار مستخدم لطلب مختلف؛ أنشئ مفتاحًا جديدًا."),
                },
            )
        if existing.get("status") in {"previewed", "previewed_v2"}:
            await _ensure_proposal_product_links(db, user_id, actor_id, existing)
            replacement_token = secrets.token_urlsafe(32)
            await _collection(db, PROPOSAL_COLLECTION).update_one(
                {
                    "user_id": user_id,
                    "proposal_id": existing.get("proposal_id"),
                    "status": existing.get("status"),
                },
                {
                    "$set": {
                        "confirm_token_hash": hashlib.sha256(
                            replacement_token.encode()
                        ).hexdigest()
                    }
                },
            )
            return _public_proposal(existing, confirm_token=replacement_token)
        return _public_proposal(existing)
    account = await _selected_account(db, user_id, payload.account_id)
    client = provider or SnapchatManagementProvider(db, user_id)
    role = await client.management_role(account, payload.action)
    if not role.get("allowed"):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "snapchat_management_role_missing",
                "message": "الحساب يحتاج دور general أو admin لإدارة الحملات، ودور creative أو admin للإبداع.",
            },
        )
    try:
        operation = build_snapchat_operation(payload)
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "snapchat_management_payload_invalid", "message": str(exc)},
        ) from exc

    original: dict[str, Any] | None = None
    pixel_eligibility: dict[str, Any] | None = None
    if payload.action in UPDATE_ACTIONS:
        original = await client.read_entity(
            operation["entity_type"], payload.target_id or ""
        )
        if payload.action == "campaign.update":
            if str(original.get("ad_account_id") or "") != payload.account_id:
                raise HTTPException(
                    status_code=409,
                    detail={"code": "snapchat_management_target_account_mismatch"},
                )
        elif payload.action == "ad_squad.update":
            if str(original.get("campaign_id") or "") != str(payload.parent_id or ""):
                raise HTTPException(
                    status_code=409,
                    detail={"code": "snapchat_management_target_parent_mismatch"},
                )
            campaign = await client.read_entity("campaign", payload.parent_id or "")
            if str(campaign.get("ad_account_id") or "") != payload.account_id:
                raise HTTPException(
                    status_code=409,
                    detail={"code": "snapchat_management_target_account_mismatch"},
                )
        elif payload.action == "ad.update":
            if str(original.get("ad_squad_id") or "") != str(payload.parent_id or ""):
                raise HTTPException(
                    status_code=409,
                    detail={"code": "snapchat_management_target_parent_mismatch"},
                )
            ad_squad = await client.read_entity("ad_squad", payload.parent_id or "")
            campaign = await client.read_entity(
                "campaign", str(ad_squad.get("campaign_id") or "")
            )
            if str(campaign.get("ad_account_id") or "") != payload.account_id:
                raise HTTPException(
                    status_code=409,
                    detail={"code": "snapchat_management_target_account_mismatch"},
                )
    elif payload.action == "ad_squad.create":
        parent = await client.read_entity("campaign", payload.parent_id or "")
        if str(parent.get("id") or "") != str(payload.parent_id or ""):
            raise HTTPException(
                status_code=409,
                detail={"code": "snapchat_management_target_parent_mismatch"},
            )
        if str(parent.get("ad_account_id") or "") != payload.account_id:
            raise HTTPException(
                status_code=409,
                detail={"code": "snapchat_management_parent_account_mismatch"},
            )
        pixel_eligibility = await client.validate_pixel_ad_squad_intent(
            account_id=payload.account_id,
            parent_id=payload.parent_id or "",
            operation=operation,
            reread_parent=False,
        )
    elif payload.action == "ad.create":
        parent = await client.read_entity("ad_squad", payload.parent_id or "")
        if str(parent.get("id") or "") != str(payload.parent_id or ""):
            raise HTTPException(
                status_code=409,
                detail={"code": "snapchat_management_target_parent_mismatch"},
            )
        campaign = await client.read_entity(
            "campaign", str(parent.get("campaign_id") or "")
        )
        if str(campaign.get("ad_account_id") or "") != payload.account_id:
            raise HTTPException(
                status_code=409,
                detail={"code": "snapchat_management_parent_account_mismatch"},
            )
        ad_entity = operation["body"]["ads"][0]
        creative = await client.read_entity(
            "creative", str(ad_entity.get("creative_id") or "")
        )
        if str(creative.get("id") or "") != str(
            ad_entity.get("creative_id") or ""
        ):
            raise HTTPException(
                status_code=409,
                detail={"code": "snapchat_management_creative_identity_mismatch"},
            )
        if str(creative.get("ad_account_id") or "") != payload.account_id:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "snapchat_management_creative_account_mismatch",
                    "message": "الإبداع المحدد لا ينتمي إلى حساب Snapchat المختار.",
                },
            )
        creative_type = str(creative.get("type") or "").strip().upper()
        requested_ad_type = str(ad_entity.get("type") or "").strip().upper()
        allowed_ad_types = CREATIVE_TYPE_TO_AD_TYPES.get(creative_type) or ()
        if requested_ad_type not in allowed_ad_types:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "snapchat_management_creative_ad_type_mismatch",
                    "message": (
                        (
                            f"نوع الإبداع {creative_type} يتطلب نوع إعلان "
                            f"{allowed_ad_types[0]} في Snapchat."
                        )
                        if allowed_ad_types
                        else (
                            f"نوع الإبداع {creative_type} ليس ضمن أنواع "
                            "الإبداع المعتمدة لهذا المسار الآمن."
                        )
                    ),
                    "creative_type": creative_type,
                    "requested_ad_type": requested_ad_type,
                    "allowed_ad_types": list(allowed_ad_types),
                },
            )

    from .snapchat_decision_metrics import resolve_decision_campaign_id

    campaign_id = await resolve_decision_campaign_id(
        db,
        user_id,
        account_id=payload.account_id,
        entity_type=str(operation.get("entity_type") or ""),
        entity_id=payload.target_id,
        parent_id=payload.parent_id,
    )
    baseline_ad_squad_id = (
        payload.target_id
        if operation.get("entity_type") == "ad_squad"
        else payload.parent_id if operation.get("entity_type") == "ad" else None
    )
    baseline_ad_id = payload.target_id if operation.get("entity_type") == "ad" else None
    product_rows = [item.model_dump(mode="json") for item in payload.products]
    baseline = await _capture_proposal_baseline(
        db,
        user_id,
        account=account,
        account_id=payload.account_id,
        campaign_id=campaign_id,
        ad_squad_id=baseline_ad_squad_id,
        ad_id=baseline_ad_id,
        products=product_rows,
    )
    if (
        operation.get("entity_type") in {"campaign", "ad_squad", "ad"}
        and not baseline.get("windows")
        and provider is None
    ):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "snapchat_management_decision_baseline_unavailable",
                "message": (
                    "تعذر أخذ لقطة نتائج سلة وSnapchat وقت القرار؛ لم يُنشأ "
                    "التعديل حتى تعود البيانات مكتملة."
                ),
                "coverage": baseline.get("coverage") or {},
            },
        )
    recent_improving = bool(
        ((baseline.get("recent_trend") or {}).get("recent_improving"))
    )
    trend_review = {
        "recent_improvement_observed": recent_improving,
        "delivery_decrease_or_pause": _is_pause_or_budget_decrease(operation, original),
        "separate_explanation_recorded": bool(payload.trend_override_reason),
        "policy": ("supporting_observation_only; never_a_fixed_rule_or_primary_basis"),
    }
    if (
        _is_delivery_increase(operation, original)
        and baseline.get("inventory_delivery_blocked") is True
    ):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "snapchat_management_inventory_blocks_delivery_increase",
                "message": (
                    "المخزون الموثق للمنتج المرتبط لا يسمح بتشغيل أو زيادة الإنفاق."
                ),
                "inventory": baseline.get("inventory") or [],
            },
        )

    proposal_id = str(uuid.uuid4())
    token = secrets.token_urlsafe(32)
    now = _utcnow()
    protocol_version = int(payload.safety_protocol_version or 1)
    first_party_link_record: dict[str, Any] | None = None
    if payload.action == "creative.create":
        creative_rows = (operation.get("body") or {}).get("creatives") or []
        creative = creative_rows[0] if creative_rows else {}
        web_view = creative.get("web_view_properties") if isinstance(creative, dict) else None
        destination_url = (
            str(web_view.get("url") or "").strip()
            if isinstance(web_view, dict)
            else ""
        )
        if destination_url:
            from first_party_attribution.core import build_tracking_url

            tracked_url, first_party_link_record = build_tracking_url(
                destination_url,
                user_id=user_id,
                provider="snapchat",
                product_id=(product_rows[0].get("product_id") if product_rows else None),
                account_id=payload.account_id,
                link_id=f"snap-proposal:{proposal_id}",
                snapchat_macros=True,
            )
            web_view["url"] = tracked_url
            first_party_link_record.update({
                "proposal_id": proposal_id,
                "actor_id": actor_id,
                "status": "proposal_ready",
            })
    intent_fingerprint = (
        snapchat_management_intent_fingerprint(operation)
        if payload.action in DELIVERY_CREATE_ACTIONS | CREATIVE_ACTIONS
        else None
    )
    row = {
        "proposal_id": proposal_id,
        "user_id": user_id,
        "actor_id": actor_id,
        "action": payload.action,
        "account_id": payload.account_id,
        "target_id": payload.target_id,
        "parent_id": payload.parent_id,
        "reason": payload.reason,
        "idempotency_key": payload.idempotency_key,
        "request_fingerprint": request_fingerprint,
        "status": "previewed_v2" if protocol_version == 2 else "previewed",
        "revision": 1,
        "operation": operation,
        "original_snapshot": original,
        "baseline": baseline,
        "expected": payload.expected_outcome,
        "decision_evidence": [
            item.model_dump(mode="json") for item in payload.supporting_evidence
        ],
        "products": product_rows,
        "product_link_state": "pending" if product_rows else "not_supplied",
        "trend_review": trend_review,
        "trend_override_reason": payload.trend_override_reason,
        "safety_protocol_version": protocol_version,
        "pixel_eligibility": _safe_provider_value(pixel_eligibility or {}),
        "intent_fingerprint": intent_fingerprint,
        "role": role.get("role"),
        "confirm_token_hash": hashlib.sha256(token.encode()).hexdigest(),
        "created_at": _iso(now),
        "expires_at": _iso(now + PROPOSAL_TTL),
        "provider_write_reached": False,
        "provider_write_state": "not_attempted",
        "provider_write_uncertain": False,
        "accounting_write_reached": False,
        "qoyod_write_reached": False,
        "source_mode": SOURCE_MODE,
    }
    await _collection(db, PROPOSAL_COLLECTION).insert_one(row)
    if first_party_link_record:
        from first_party_attribution.core import LINK_COLLECTION

        await _collection(db, LINK_COLLECTION).update_one(
            {
                "user_id": user_id,
                "link_id": first_party_link_record["link_id"],
            },
            {"$setOnInsert": first_party_link_record},
            upsert=True,
        )
    product_links_ok = await _ensure_proposal_product_links(db, user_id, actor_id, row)
    if product_rows:
        row["product_link_state"] = "confirmed" if product_links_ok else "deferred"
    await _audit(
        db,
        user_id=user_id,
        proposal_id=proposal_id,
        event="previewed",
        actor_id=actor_id,
        detail={"action": payload.action, "role": role.get("role")},
    )
    return _public_proposal(row, confirm_token=token)


async def approve_snapchat_management_proposal(
    db: Any,
    user_id: str,
    actor_id: str,
    proposal_id: str,
    payload: SnapchatManagementApprovalInput,
) -> dict[str, Any]:
    row = await _collection(db, PROPOSAL_COLLECTION).find_one(
        {"user_id": user_id, "proposal_id": proposal_id}, {"_id": 0}
    )
    if not row:
        raise HTTPException(
            status_code=404, detail={"code": "snapchat_management_proposal_not_found"}
        )
    if (
        row.get("status") not in {"previewed", "previewed_v2"}
        or int(row.get("revision") or 0) != payload.expected_revision
    ):
        raise HTTPException(
            status_code=409,
            detail={"code": "snapchat_management_proposal_not_approvable"},
        )
    if _expired(row.get("expires_at")):
        raise HTTPException(
            status_code=409, detail={"code": "snapchat_management_proposal_expired"}
        )
    token_hash = hashlib.sha256(payload.confirm_token.encode()).hexdigest()
    if not secrets.compare_digest(token_hash, str(row.get("confirm_token_hash") or "")):
        raise HTTPException(
            status_code=409,
            detail={"code": "snapchat_management_confirm_token_mismatch"},
        )
    now_iso = _iso()
    preview_status = str(row.get("status") or "")
    approved_status = (
        "approved_v2" if preview_status == "previewed_v2" else "approved"
    )
    result = await _collection(db, PROPOSAL_COLLECTION).update_one(
        {
            "user_id": user_id,
            "proposal_id": proposal_id,
            "status": preview_status,
            "revision": payload.expected_revision,
        },
        {
            "$set": {
                "status": approved_status,
                "approved_at": now_iso,
                "approved_by": actor_id,
                "confirm_token_hash": None,
            },
            "$inc": {"revision": 1},
        },
    )
    if not getattr(result, "matched_count", 1):
        raise HTTPException(
            status_code=409, detail={"code": "snapchat_management_approval_race"}
        )
    await _audit(
        db,
        user_id=user_id,
        proposal_id=proposal_id,
        event="approved",
        actor_id=actor_id,
    )
    updated = await _collection(db, PROPOSAL_COLLECTION).find_one(
        {"user_id": user_id, "proposal_id": proposal_id}, {"_id": 0}
    )
    return _public_proposal(updated or row)


def _changed_values_match(
    expected: dict[str, Any], actual: dict[str, Any]
) -> tuple[bool, list[str]]:
    mismatches: list[str] = []
    for key, value in expected.items():
        if not _expected_control_subset_matches(value, actual.get(key)):
            mismatches.append(key)
    return not mismatches, mismatches


def _expected_control_subset_matches(expected: Any, actual: Any) -> bool:
    """Provider dictionaries may add defaults; submitted controls may not drift."""
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            return False
        return all(
            key in actual and _expected_control_subset_matches(value, actual.get(key))
            for key, value in expected.items()
        )
    if isinstance(expected, list):
        if not isinstance(actual, list) or len(actual) != len(expected):
            return False
        return all(
            _expected_control_subset_matches(expected_item, actual_item)
            for expected_item, actual_item in zip(expected, actual)
        )
    return _canonical_control_value(actual) == _canonical_control_value(expected)


def _canonical_control_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _canonical_control_value(nested)
            for key, nested in sorted(value.items(), key=lambda item: str(item[0]))
        }
    if isinstance(value, list):
        return [_canonical_control_value(item) for item in value]
    return value


def _operation_expected_values(operation: dict[str, Any]) -> dict[str, Any]:
    changes = operation.get("changes")
    if isinstance(changes, dict):
        return dict(changes)
    body = operation.get("body")
    rows = body.get(operation.get("plural")) if isinstance(body, dict) else None
    entity = rows[0] if isinstance(rows, list) and rows else {}
    if not isinstance(entity, dict):
        return {}
    # POST bodies are constructed only from the entity's validated create
    # allow-list plus provider relationship/status fields injected by Mezan.
    # Every submitted control value must survive provider canonical readback;
    # otherwise objective, targeting, placement, bidding, conversion-window,
    # or creative-property drift could be mislabeled as a verified success.
    return {
        key: _canonical_control_value(value)
        for key, value in entity.items()
        if key != "id"
    }


def _operation_create_entity(operation: dict[str, Any]) -> dict[str, Any]:
    body = operation.get("body")
    rows = body.get(operation.get("plural")) if isinstance(body, dict) else None
    if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
        return {}
    return dict(rows[0])


async def _refresh_verified_entity_cache_deferred_safe(
    db: Any,
    *,
    user_id: str,
    actor_id: str,
    proposal_id: str,
    account: dict[str, Any],
    entity_type: str,
    entity: dict[str, Any],
    event: str,
) -> None:
    try:
        await _upsert_entity(
            SnapchatSyncContext(db=db, user_id=user_id),
            account=account,
            entity_type=entity_type,
            entity=entity,
        )
    except Exception as exc:
        # Provider readback is authoritative.  Local reporting-cache refresh is
        # repairable and must never strand a verified execute/rollback state.
        try:
            await _audit(
                db,
                user_id=user_id,
                proposal_id=proposal_id,
                event=event,
                actor_id=actor_id,
                detail={"error_type": type(exc).__name__},
            )
        except Exception:
            pass


async def _reset_execution_claim(
    db: Any,
    *,
    user_id: str,
    proposal_id: str,
    failure: dict[str, Any] | None = None,
) -> None:
    current = await _collection(db, PROPOSAL_COLLECTION).find_one(
        {"user_id": user_id, "proposal_id": proposal_id},
        {"_id": 0, "safety_protocol_version": 1},
    )
    approved_status = (
        "approved_v2"
        if int((current or {}).get("safety_protocol_version") or 1) == 2
        else "approved"
    )
    values: dict[str, Any] = {
        "status": approved_status,
        "provider_write_reached": False,
        "provider_write_state": "not_attempted",
        "provider_write_uncertain": False,
        "execution_started_at": None,
    }
    if failure:
        values.update(
            {
                "failure": _safe_provider_value(failure),
                "failed_at": _iso(),
                "execution_retryable": True,
            }
        )
    await _collection(db, PROPOSAL_COLLECTION).update_one(
        {
            "user_id": user_id,
            "proposal_id": proposal_id,
            "status": "executing",
        },
        {"$set": values},
    )


async def _complete_verified_execution(
    db: Any,
    *,
    user_id: str,
    actor_id: str,
    proposal_id: str,
    row: dict[str, Any],
    operation: dict[str, Any],
    account: dict[str, Any],
    provider_entity_id: str,
    verified: dict[str, Any],
    event: str,
) -> dict[str, Any]:
    verification = {
        "verified": True,
        "entity_id": provider_entity_id,
        "status": verified.get("status"),
        "verified_at": _iso(),
        "provider_snapshot": verified,
        "verification_source": (
            "post_error_readback"
            if event == "execution_reconciled_applied"
            else "readback"
        ),
    }
    now_iso = _iso()
    terminal = await _collection(db, PROPOSAL_COLLECTION).update_one(
        {"user_id": user_id, "proposal_id": proposal_id, "status": "executing"},
        {
            "$set": {
                "status": "completed",
                "executed_at": now_iso,
                "executed_by": actor_id,
                "provider_write_reached": True,
                "provider_write_state": "confirmed",
                "provider_write_uncertain": False,
                "provider_entity_id": provider_entity_id,
                "verification": verification,
                "execution_retryable": False,
                "automatic_retry_allowed": False,
                "failure": {},
            }
        },
    )
    if int(getattr(terminal, "matched_count", 0) or 0) != 1:
        raise HTTPException(
            status_code=409,
            detail={"code": "snapchat_management_terminal_state_race"},
        )
    await _refresh_verified_entity_cache_deferred_safe(
        db,
        user_id=user_id,
        actor_id=actor_id,
        proposal_id=proposal_id,
        account=account,
        entity_type=operation["entity_type"],
        entity=verified,
        event="verified_entity_cache_refresh_deferred",
    )
    await _audit(
        db,
        user_id=user_id,
        proposal_id=proposal_id,
        event=event,
        actor_id=actor_id,
        detail={"entity_id": provider_entity_id, "status": verified.get("status")},
    )
    completed_row = await _collection(db, PROPOSAL_COLLECTION).find_one(
        {"user_id": user_id, "proposal_id": proposal_id}, {"_id": 0}
    )
    if completed_row:
        await _adopt_proposal_products_deferred_safe(
            db, user_id, actor_id, completed_row
        )
        completed_row = (
            await _collection(db, PROPOSAL_COLLECTION).find_one(
                {"user_id": user_id, "proposal_id": proposal_id}, {"_id": 0}
            )
            or completed_row
        )
        await _record_management_decision_deferred_safe(
            db, user_id, actor_id, proposal_id, completed_row
        )
    updated = await _collection(db, PROPOSAL_COLLECTION).find_one(
        {"user_id": user_id, "proposal_id": proposal_id}, {"_id": 0}
    )
    return _public_proposal(updated or row)


def _provider_state_conflict_fields(
    operation: dict[str, Any],
    original_snapshot: dict[str, Any],
    current_entity: dict[str, Any],
) -> list[str]:
    safety_fields_by_entity = {
        "campaign": CAMPAIGN_UPDATE_FIELDS,
        "ad_squad": AD_SQUAD_UPDATE_FIELDS,
        "ad": AD_UPDATE_FIELDS,
    }
    controlled_fields = {
        field
        for field in safety_fields_by_entity.get(
            str(operation.get("entity_type") or ""),
            set(operation.get("changes") or {}),
        )
        if field in original_snapshot or field in current_entity
    }
    return sorted(
        field
        for field in controlled_fields
        if current_entity.get(field) != original_snapshot.get(field)
    )


def _provider_state_conflict_detail(stale_fields: list[str]) -> dict[str, Any]:
    return {
        "code": "snapchat_management_provider_state_conflict",
        "message": (
            "تغيرت حالة Snapchat بعد المعاينة؛ أُوقف التنفيذ وأنشئ "
            "معاينة جديدة من الحالة الحالية."
        ),
        "changed_fields": stale_fields,
    }


def _lease_entity_id(row: dict[str, Any], operation: dict[str, Any]) -> str:
    return str(
        row.get("provider_entity_id")
        or row.get("target_id")
        or (
            f"intent:{row.get('intent_fingerprint')}"
            if row.get("intent_fingerprint")
            else None
        )
        or f"pending:{row.get('proposal_id')}"
    )


async def _acquire_entity_lease(
    db: Any,
    *,
    user_id: str,
    row: dict[str, Any],
    operation: dict[str, Any],
    operation_kind: str,
) -> str:
    """Claim one provider entity without unsafe automatic lease takeover.

    ``expires_at`` is operational evidence for manual recovery, not permission
    for another worker to steal the lease.  A timed-out provider call can have
    applied after the caller stopped waiting, so blind TTL takeover would
    defeat the fence and could duplicate a financial/ad-delivery write.
    """
    await ensure_snapchat_management_indexes(db)
    token = secrets.token_urlsafe(24)
    now = _utcnow()
    lease = {
        "lease_id": str(uuid.uuid4()),
        "lease_token": token,
        "active": True,
        "user_id": user_id,
        "account_id": str(row.get("account_id") or ""),
        "entity_type": str(operation.get("entity_type") or ""),
        "entity_id": _lease_entity_id(row, operation),
        "proposal_id": str(row.get("proposal_id") or ""),
        "operation_kind": operation_kind,
        "acquired_at": _iso(now),
        "expires_at": _iso(now + ENTITY_LEASE_DURATION),
        "source_mode": SOURCE_MODE,
    }
    try:
        await _collection(db, ENTITY_LEASE_COLLECTION).insert_one(lease)
    except Exception as exc:
        # Motor raises DuplicateKeyError for the partial unique entity fence.
        # Avoid exposing storage details and never steal an expired lease: its
        # provider outcome must first be reconciled by an operator.
        if type(exc).__name__ != "DuplicateKeyError":
            raise
        raise HTTPException(
            status_code=409,
            detail={
                "code": "snapchat_management_entity_busy",
                "message": (
                    "يوجد تنفيذ أو تراجع قائم على نفس كيان Snapchat؛ "
                    "انتظر اكتماله أو نفّذ مسار الاستعادة قبل المحاولة."
                ),
                "recovery_action": "reconcile_existing_entity_lease",
            },
        ) from exc
    return token


async def _release_entity_lease(
    db: Any,
    *,
    user_id: str,
    row: dict[str, Any],
    operation: dict[str, Any],
    lease_token: str,
) -> None:
    await _collection(db, ENTITY_LEASE_COLLECTION).update_one(
        {
            "user_id": user_id,
            "account_id": str(row.get("account_id") or ""),
            "entity_type": str(operation.get("entity_type") or ""),
            "entity_id": _lease_entity_id(row, operation),
            "lease_token": lease_token,
            "active": True,
        },
        {
            "$set": {
                "active": False,
                "released_at": _iso(),
            }
        },
    )


async def _release_proposal_entity_lease(
    db: Any,
    *,
    user_id: str,
    row: dict[str, Any],
) -> None:
    collection = _collection(db, ENTITY_LEASE_COLLECTION)
    query = {
        "user_id": user_id,
        "proposal_id": str(row.get("proposal_id") or ""),
        "active": True,
    }
    updater = getattr(collection, "update_many", None)
    if callable(updater):
        await updater(
            query,
            {"$set": {"active": False, "released_at": _iso()}},
        )
        return
    # Lightweight test doubles and legacy adapters may not implement
    # update_many. Re-read after every CAS release until no active lease for
    # this proposal remains; never assume there is only one.
    for _ in range(10):
        lease = await collection.find_one(query, {"_id": 0})
        if not lease or not lease.get("lease_token"):
            return
        await collection.update_one(
            {**query, "lease_token": str(lease["lease_token"])},
            {"$set": {"active": False, "released_at": _iso()}},
        )
    raise HTTPException(
        status_code=409,
        detail={"code": "snapchat_management_entity_lease_release_incomplete"},
    )


async def _release_observed_entity_lease(
    db: Any,
    *,
    user_id: str,
    lease: dict[str, Any] | None,
) -> None:
    """Release only the exact fence observed before a terminal transition."""
    if not lease:
        return
    lease_token = str(lease.get("lease_token") or "")
    if not lease_token:
        raise HTTPException(
            status_code=409,
            detail={"code": "snapchat_management_entity_lease_identity_missing"},
        )
    released = await _collection(db, ENTITY_LEASE_COLLECTION).update_one(
        {
            "user_id": user_id,
            "proposal_id": str(lease.get("proposal_id") or ""),
            "lease_token": lease_token,
            "entity_id": str(lease.get("entity_id") or ""),
            "active": True,
        },
        {"$set": {"active": False, "released_at": _iso()}},
    )
    if not getattr(released, "matched_count", 1):
        raise HTTPException(
            status_code=409,
            detail={"code": "snapchat_management_entity_lease_release_race"},
        )


async def _active_proposal_entity_lease(
    db: Any, *, user_id: str, proposal_id: str
) -> dict[str, Any] | None:
    return await _collection(db, ENTITY_LEASE_COLLECTION).find_one(
        {
            "user_id": user_id,
            "proposal_id": proposal_id,
            "active": True,
        },
        {"_id": 0},
    )


async def execute_snapchat_management_proposal(
    db: Any,
    user_id: str,
    actor_id: str,
    proposal_id: str,
    *,
    provider: SnapchatManagementProvider | None = None,
) -> dict[str, Any]:
    row = await _collection(db, PROPOSAL_COLLECTION).find_one(
        {"user_id": user_id, "proposal_id": proposal_id}, {"_id": 0}
    )
    if not row:
        raise HTTPException(
            status_code=404, detail={"code": "snapchat_management_proposal_not_found"}
        )
    if row.get("status") == "completed":
        row = await _retry_terminal_product_adoption(db, user_id, actor_id, row)
        return _public_proposal(row)
    if row.get("status") not in {"approved", "approved_v2"}:
        raise HTTPException(
            status_code=409,
            detail={"code": "snapchat_management_proposal_not_executable"},
        )
    operation = dict(row.get("operation") or {})
    lease_token = await _acquire_entity_lease(
        db,
        user_id=user_id,
        row=row,
        operation=operation,
        operation_kind="execute",
    )
    try:
        result = await _execute_snapchat_management_proposal_under_lease(
            db,
            user_id,
            actor_id,
            proposal_id,
            provider=provider,
        )
        return result
    except asyncio.CancelledError:
        # ``CancelledError`` is a BaseException on supported Python versions,
        # so the provider-error handler below the lock cannot persist an
        # uncertain outcome.  Stabilize the durable state before propagating
        # cancellation; a later recovery remains strictly read-only.
        await _stabilize_cancelled_execution(
            db,
            user_id=user_id,
            actor_id=actor_id,
            proposal_id=proposal_id,
        )
        raise
    finally:
        latest = await _collection(db, PROPOSAL_COLLECTION).find_one(
            {"user_id": user_id, "proposal_id": proposal_id}, {"_id": 0}
        )
        # Preserve the entity fence when the provider outcome is unresolved.
        # A later retry must not write blindly; recovery reconciles and then
        # explicitly releases the durable fence.
        if (latest or {}).get("status") != "executing" and not (latest or {}).get(
            "provider_write_uncertain"
        ):
            await _release_entity_lease(
                db,
                user_id=user_id,
                row=row,
                operation=operation,
                lease_token=lease_token,
            )


async def _stabilize_cancelled_execution(
    db: Any,
    *,
    user_id: str,
    actor_id: str,
    proposal_id: str,
) -> None:
    """Fence a cancelled in-flight provider call before cancellation escapes."""
    row = await _collection(db, PROPOSAL_COLLECTION).find_one(
        {"user_id": user_id, "proposal_id": proposal_id}, {"_id": 0}
    )
    if not row or row.get("status") != "executing":
        return
    write_state = str(row.get("provider_write_state") or "not_attempted")
    if write_state not in {"attempting", "confirmed"}:
        await _reset_execution_claim(
            db,
            user_id=user_id,
            proposal_id=proposal_id,
            failure={"code": "snapchat_management_worker_cancelled_before_write"},
        )
        return
    await _collection(db, PROPOSAL_COLLECTION).update_one(
        {
            "user_id": user_id,
            "proposal_id": proposal_id,
            "status": "executing",
        },
        {
            "$set": {
                "status": "failed",
                "failed_at": _iso(),
                "provider_write_state": "unknown_needs_reconciliation",
                "provider_write_reached": (
                    row.get("provider_write_reached") is True
                    or write_state == "confirmed"
                ),
                "provider_write_uncertain": True,
                "execution_retryable": False,
                "automatic_retry_allowed": False,
                "recovery_action": (
                    "manual_reconcile_provider_entity_before_retry_or_rollback"
                ),
                "failure": {
                    "code": "snapchat_management_worker_cancelled_during_execution",
                    "recovery_action": (
                        "manual_reconcile_provider_entity_before_retry_or_rollback"
                    ),
                },
            }
        },
    )
    try:
        await _audit(
            db,
            user_id=user_id,
            proposal_id=proposal_id,
            event="execution_cancelled_write_uncertain",
            actor_id=actor_id,
            detail={"provider_write_state_before_cancellation": write_state},
        )
    except Exception:
        # The proposal row and entity fence are the safety boundary; audit
        # repair must never weaken them or replace task cancellation.
        return


def _provider_datetime(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _create_candidate_matches(
    row: dict[str, Any], operation: dict[str, Any], candidate: dict[str, Any]
) -> bool:
    if str(candidate.get("deleted") or "").strip().lower() in {"true", "1", "yes"}:
        return False
    expected = _operation_expected_values(operation)
    exact, _ = _changed_values_match(expected, candidate)
    if not exact or str(candidate.get("status") or "").upper() != "PAUSED":
        return False
    action = str(row.get("action") or "")
    if action == "campaign.create" and str(candidate.get("ad_account_id") or "") != str(
        row.get("account_id") or ""
    ):
        return False
    if action == "ad_squad.create" and str(candidate.get("campaign_id") or "") != str(
        row.get("parent_id") or ""
    ):
        return False
    if action == "ad.create" and str(candidate.get("ad_squad_id") or "") != str(
        row.get("parent_id") or ""
    ):
        return False
    if action == "creative.create" and str(candidate.get("ad_account_id") or "") != str(
        row.get("account_id") or ""
    ):
        return False
    started = _provider_datetime(row.get("execution_started_at"))
    failed = _provider_datetime(row.get("failed_at"))
    created = _provider_datetime(candidate.get("created_at"))
    if not started or not failed or not created or failed < started:
        return False
    return started - timedelta(minutes=1) <= created <= failed + timedelta(minutes=1)


def _submitted_control_shape_present(expected: Any, actual: Any) -> bool:
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(
            key in actual
            and _submitted_control_shape_present(value, actual.get(key))
            for key, value in expected.items()
        )
    if isinstance(expected, list):
        return (
            isinstance(actual, list)
            and len(actual) == len(expected)
            and all(
                _submitted_control_shape_present(expected_item, actual_item)
                for expected_item, actual_item in zip(expected, actual)
            )
        )
    return True


def _create_candidate_deleted(candidate: dict[str, Any]) -> bool:
    return str(candidate.get("deleted") or "").strip().lower() in {
        "true",
        "1",
        "yes",
    }


def _create_candidate_base_evidence_complete(
    row: dict[str, Any], candidate: Any
) -> bool:
    """Require only evidence needed to place a row outside or inside scope."""
    if not isinstance(candidate, dict) or not str(candidate.get("id") or "").strip():
        return False
    deleted = _create_candidate_deleted(candidate)
    if not deleted and str(candidate.get("status") or "").upper() not in {
        "ACTIVE",
        "PAUSED",
    }:
        return False
    if not _provider_datetime(candidate.get("created_at")):
        return False
    relationship = {
        "campaign.create": "ad_account_id",
        "ad_squad.create": "campaign_id",
        "ad.create": "ad_squad_id",
    }.get(str(row.get("action") or ""))
    if not relationship or not str(candidate.get(relationship) or "").strip():
        return False
    return True


def _create_candidate_same_scope_and_window(
    row: dict[str, Any], candidate: dict[str, Any]
) -> bool:
    relationship = {
        "campaign.create": ("ad_account_id", str(row.get("account_id") or "")),
        "ad_squad.create": ("campaign_id", str(row.get("parent_id") or "")),
        "ad.create": ("ad_squad_id", str(row.get("parent_id") or "")),
    }.get(str(row.get("action") or ""))
    if not relationship or str(candidate.get(relationship[0]) or "") != relationship[1]:
        return False
    started = _provider_datetime(row.get("execution_started_at"))
    failed = _provider_datetime(row.get("failed_at"))
    created = _provider_datetime(candidate.get("created_at"))
    if not started or not failed or not created or failed < started:
        return False
    return started - timedelta(minutes=1) <= created <= failed + timedelta(minutes=1)


def _create_candidate_submitted_shape_complete(
    operation: dict[str, Any], candidate: dict[str, Any]
) -> bool:
    expected = _operation_expected_values(operation)
    return all(
        key in candidate
        and _submitted_control_shape_present(value, candidate.get(key))
        for key, value in expected.items()
    )


def _classify_create_reconciliation_catalog(
    row: dict[str, Any],
    operation: dict[str, Any],
    catalog: Any,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], bool]:
    if not isinstance(catalog, list):
        return [], [], True
    matches: list[dict[str, Any]] = []
    plausible: list[dict[str, Any]] = []
    for candidate in catalog:
        if not _create_candidate_base_evidence_complete(row, candidate):
            return [], [], True
        if not _create_candidate_same_scope_and_window(row, candidate):
            continue
        if (
            _create_candidate_deleted(candidate)
            or not _create_candidate_submitted_shape_complete(operation, candidate)
        ):
            plausible.append(candidate)
        elif _create_candidate_matches(row, operation, candidate):
            matches.append(candidate)
        else:
            plausible.append(candidate)
    return matches, plausible, False


async def _ensure_create_intent_fence(
    db: Any,
    *,
    user_id: str,
    row: dict[str, Any],
    operation: dict[str, Any],
) -> dict[str, Any]:
    try:
        _assert_stored_operation_integrity(row, operation)
    except ValueError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "snapchat_management_reconciliation_legacy_intent_incomplete",
                "recovery_action": "manual_provider_identity_review",
            },
        ) from exc
    fingerprint = str(row.get("intent_fingerprint") or "")
    calculated = snapchat_management_intent_fingerprint(operation)
    if fingerprint and fingerprint != calculated:
        raise HTTPException(
            status_code=409,
            detail={"code": "snapchat_management_reconciliation_intent_mismatch"},
        )
    if not fingerprint:
        fingerprint = calculated
        await _collection(db, PROPOSAL_COLLECTION).update_one(
            {"user_id": user_id, "proposal_id": row.get("proposal_id")},
            {"$set": {"intent_fingerprint": fingerprint}},
        )
        row = {**row, "intent_fingerprint": fingerprint}
    entity_id = f"intent:{fingerprint}"
    leases = _collection(db, ENTITY_LEASE_COLLECTION)
    own_cursor = leases.find(
        {
            "user_id": user_id,
            "proposal_id": str(row.get("proposal_id") or ""),
            "active": True,
        },
        {"_id": 0},
    )
    if hasattr(own_cursor, "limit"):
        own_cursor = own_cursor.limit(3)
    if hasattr(own_cursor, "to_list"):
        own_active = await own_cursor.to_list(length=3)
    elif isinstance(own_cursor, list):
        own_active = own_cursor[:3]
    else:
        own_active = []
    if len(own_active) > 1:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "snapchat_management_create_intent_lease_ambiguous",
                "recovery_action": "manual_lease_review",
            },
        )
    if own_active:
        current = own_active[0]
        current_entity_id = str(current.get("entity_id") or "")
        if current_entity_id == entity_id:
            return row
        if not current_entity_id.startswith("pending:") or not current.get(
            "lease_token"
        ):
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "snapchat_management_create_intent_lease_mismatch",
                    "recovery_action": "manual_lease_review",
                },
            )
        try:
            upgraded = await leases.update_one(
                {
                    "user_id": user_id,
                    "proposal_id": str(row.get("proposal_id") or ""),
                    "lease_token": str(current.get("lease_token") or ""),
                    "entity_id": current_entity_id,
                    "active": True,
                },
                {
                    "$set": {
                        "entity_id": entity_id,
                        "operation_kind": "reconcile_create_intent",
                        "intent_fingerprint": fingerprint,
                        "intent_fence_upgraded_at": _iso(),
                    }
                },
            )
        except Exception as exc:
            if type(exc).__name__ != "DuplicateKeyError":
                raise
            raise HTTPException(
                status_code=409,
                detail={"code": "snapchat_management_create_intent_busy"},
            ) from exc
        if not getattr(upgraded, "matched_count", 1):
            raise HTTPException(
                status_code=409,
                detail={"code": "snapchat_management_create_intent_lease_race"},
            )
        return row

    existing = await leases.find_one(
        {
            "user_id": user_id,
            "account_id": str(row.get("account_id") or ""),
            "entity_type": str(operation.get("entity_type") or ""),
            "entity_id": entity_id,
            "active": True,
        },
        {"_id": 0},
    )
    if existing and str(existing.get("proposal_id") or "") != str(
        row.get("proposal_id") or ""
    ):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "snapchat_management_create_intent_busy",
                "message": "توجد محاولة إنشاء مطابقة غير محسومة؛ أُوقفت المصالحة الموازية.",
            },
        )
    if not existing:
        await _acquire_entity_lease(
            db,
            user_id=user_id,
            row=row,
            operation=operation,
            operation_kind="reconcile_create_intent",
        )
    return row


async def _claim_create_reconciliation_scan(
    db: Any,
    *,
    user_id: str,
    row: dict[str, Any],
) -> tuple[dict[str, Any], str]:
    """Serialize read-only create scans so contradictory results cannot race."""
    proposals = _collection(db, PROPOSAL_COLLECTION)
    current = await proposals.find_one(
        {
            "user_id": user_id,
            "proposal_id": str(row.get("proposal_id") or ""),
            "status": "failed",
            "provider_write_uncertain": True,
        },
        {"_id": 0},
    )
    if not current:
        raise HTTPException(
            status_code=409,
            detail={"code": "snapchat_management_reconciliation_race"},
        )
    active = current.get("create_reconciliation_scan_active") is True
    scan_expiry = current.get("create_reconciliation_scan_expires_at")
    if active and not _expired(scan_expiry):
        raise HTTPException(
            status_code=409,
            detail={"code": "snapchat_management_reconciliation_scan_busy"},
        )
    previous_token = current.get("create_reconciliation_scan_token")
    token = secrets.token_urlsafe(24)
    claimed = await proposals.update_one(
        {
            "user_id": user_id,
            "proposal_id": str(row.get("proposal_id") or ""),
            "status": "failed",
            "provider_write_uncertain": True,
            "create_reconciliation_scan_token": previous_token,
        },
        {
            "$set": {
                "create_reconciliation_scan_active": True,
                "create_reconciliation_scan_token": token,
                "create_reconciliation_scan_started_at": _iso(),
                "create_reconciliation_scan_expires_at": _iso(
                    _utcnow() + ENTITY_LEASE_DURATION
                ),
            }
        },
    )
    if not getattr(claimed, "matched_count", 1):
        raise HTTPException(
            status_code=409,
            detail={"code": "snapchat_management_reconciliation_scan_race"},
        )
    return current, token


async def _release_create_reconciliation_scan(
    db: Any,
    *,
    user_id: str,
    proposal_id: str,
    scan_token: str,
) -> bool:
    released = await _collection(db, PROPOSAL_COLLECTION).update_one(
        {
            "user_id": user_id,
            "proposal_id": proposal_id,
            "status": "failed",
            "provider_write_uncertain": True,
            "create_reconciliation_scan_active": True,
            "create_reconciliation_scan_token": scan_token,
        },
        {
            "$set": {
                "create_reconciliation_scan_active": False,
                "create_reconciliation_scan_finished_at": _iso(),
            }
        },
    )
    return bool(getattr(released, "matched_count", 1))


async def _record_create_reconciliation_inconclusive(
    db: Any,
    *,
    user_id: str,
    proposal_id: str,
    scan_token: str,
    reason: str,
    matching_candidates: int | None = None,
    plausible_candidates: int | None = None,
    catalog_incomplete: bool | None = None,
) -> None:
    """Persist sticky evidence that can never be downgraded into absence."""
    evidence: dict[str, Any] = {
        "complete_scan": catalog_incomplete is not True,
        "inconclusive": True,
        "reason": reason,
        "scanned_at": _iso(),
    }
    if matching_candidates is not None:
        evidence["matching_candidates"] = max(0, int(matching_candidates))
    if plausible_candidates is not None:
        evidence["plausible_candidates"] = max(0, int(plausible_candidates))
    if catalog_incomplete is not None:
        evidence["catalog_incomplete"] = bool(catalog_incomplete)
    recorded = await _collection(db, PROPOSAL_COLLECTION).update_one(
        {
            "user_id": user_id,
            "proposal_id": proposal_id,
            "status": "failed",
            "provider_write_uncertain": True,
            "create_reconciliation_scan_active": True,
            "create_reconciliation_scan_token": scan_token,
        },
        {
            "$set": {
                "create_reconciliation_scan_active": False,
                "create_reconciliation_scan_finished_at": _iso(),
                "recovery_action": "manual_review_plausible_create_candidate",
                "create_reconciliation": evidence,
            }
        },
    )
    if not getattr(recorded, "matched_count", 1):
        raise HTTPException(
            status_code=409,
            detail={"code": "snapchat_management_reconciliation_race"},
        )


async def _reconcile_uncertain_create_without_id(
    db: Any,
    *,
    user_id: str,
    actor_id: str,
    row: dict[str, Any],
    operation: dict[str, Any],
    account: dict[str, Any],
    client: "SnapchatManagementProvider",
) -> dict[str, Any]:
    row, scan_token = await _claim_create_reconciliation_scan(
        db, user_id=user_id, row=row
    )
    proposal_id = str(row.get("proposal_id") or "")
    try:
        row = await _ensure_create_intent_fence(
            db, user_id=user_id, row=row, operation=operation
        )
    except Exception:
        await _release_create_reconciliation_scan(
            db,
            user_id=user_id,
            proposal_id=proposal_id,
            scan_token=scan_token,
        )
        raise
    intent_lease = await _active_proposal_entity_lease(
        db,
        user_id=user_id,
        proposal_id=proposal_id,
    )
    expected_intent_entity = f"intent:{row.get('intent_fingerprint')}"
    if (
        not intent_lease
        or str(intent_lease.get("entity_id") or "") != expected_intent_entity
        or not str(intent_lease.get("lease_token") or "")
    ):
        await _release_create_reconciliation_scan(
            db,
            user_id=user_id,
            proposal_id=proposal_id,
            scan_token=scan_token,
        )
        raise HTTPException(
            status_code=409,
            detail={"code": "snapchat_management_create_intent_lease_mismatch"},
        )
    started_at = _provider_datetime(row.get("execution_started_at"))
    failed_at = _provider_datetime(row.get("failed_at"))
    if not started_at or not failed_at or failed_at < started_at:
        await _release_create_reconciliation_scan(
            db,
            user_id=user_id,
            proposal_id=proposal_id,
            scan_token=scan_token,
        )
        raise HTTPException(
            status_code=409,
            detail={
                "code": "snapchat_management_reconciliation_legacy_window_missing",
                "recovery_action": "manual_provider_identity_review",
            },
        )
    try:
        catalog = await client.list_create_reconciliation_candidates(operation)
    except Exception:
        await _release_create_reconciliation_scan(
            db,
            user_id=user_id,
            proposal_id=proposal_id,
            scan_token=scan_token,
        )
        await _audit(
            db,
            user_id=user_id,
            proposal_id=str(row.get("proposal_id") or ""),
            event="create_reconciliation_catalog_incomplete",
            actor_id=actor_id,
            detail={"adoption_performed": False, "absence_proven": False},
        )
        raise
    matches, plausible_drift, catalog_incomplete = (
        _classify_create_reconciliation_catalog(row, operation, catalog)
    )
    if catalog_incomplete:
        await _release_create_reconciliation_scan(
            db,
            user_id=user_id,
            proposal_id=proposal_id,
            scan_token=scan_token,
        )
        await _audit(
            db,
            user_id=user_id,
            proposal_id=proposal_id,
            event="create_reconciliation_catalog_inconclusive",
            actor_id=actor_id,
            detail={"adoption_performed": False, "absence_proven": False},
        )
        raise HTTPException(
            status_code=502,
            detail={"code": "snapchat_management_reconciliation_catalog_inconclusive"},
        )
    previous_reconciliation = row.get("create_reconciliation")
    previous_reconciliation = (
        previous_reconciliation
        if isinstance(previous_reconciliation, dict)
        else {}
    )
    sticky_manual = bool(
        int(previous_reconciliation.get("matching_candidates") or 0) > 1
        or previous_reconciliation.get("inconclusive") is True
    )
    if plausible_drift or sticky_manual:
        reason = (
            "deleted_candidate_in_attempt_window"
            if any(
                _create_candidate_deleted(candidate)
                for candidate in plausible_drift
            )
            else "same_scope_candidate_drifted_in_attempt_window"
            if plausible_drift
            else "prior_manual_reconciliation_is_sticky"
        )
        recorded = await _collection(db, PROPOSAL_COLLECTION).update_one(
            {
                "user_id": user_id,
                "proposal_id": proposal_id,
                "status": "failed",
                "provider_write_uncertain": True,
                "create_reconciliation_scan_active": True,
                "create_reconciliation_scan_token": scan_token,
            },
            {
                "$set": {
                    "create_reconciliation_scan_active": False,
                    "create_reconciliation_scan_finished_at": _iso(),
                    "recovery_action": "manual_review_plausible_create_candidate",
                    "create_reconciliation": {
                        "complete_scan": True,
                        "inconclusive": True,
                        "reason": reason,
                        "plausible_candidates": len(plausible_drift),
                        "scanned_at": _iso(),
                    },
                }
            },
        )
        if not getattr(recorded, "matched_count", 1):
            raise HTTPException(
                status_code=409,
                detail={"code": "snapchat_management_reconciliation_race"},
            )
        raise HTTPException(
            status_code=409,
            detail={
                "code": "snapchat_management_reconciliation_inconclusive",
                "reason": reason,
            },
        )
    if len(matches) > 1:
        recorded = await _collection(db, PROPOSAL_COLLECTION).update_one(
            {
                "user_id": user_id,
                "proposal_id": proposal_id,
                "status": "failed",
                "provider_write_uncertain": True,
                "create_reconciliation_scan_active": True,
                "create_reconciliation_scan_token": scan_token,
            },
            {
                "$set": {
                    "create_reconciliation_scan_active": False,
                    "create_reconciliation_scan_finished_at": _iso(),
                    "recovery_action": "manual_review_ambiguous_create_candidates",
                    "create_reconciliation": {
                        "complete_scan": True,
                        "matching_candidates": len(matches),
                        "scanned_at": _iso(),
                    },
                }
            },
        )
        if not getattr(recorded, "matched_count", 1):
            raise HTTPException(
                status_code=409,
                detail={"code": "snapchat_management_reconciliation_race"},
            )
        raise HTTPException(
            status_code=409,
            detail={
                "code": "snapchat_management_reconciliation_ambiguous",
                "matching_candidates": len(matches),
            },
        )
    if not matches:
        now = _utcnow()
        previous = previous_reconciliation
        previous_zero_scans = int(previous.get("complete_zero_scans") or 0)
        first_zero = _provider_datetime(previous.get("first_zero_scan_at")) or now
        failed_at = _provider_datetime(row.get("failed_at"))
        zero_scans = previous_zero_scans + 1
        grace_elapsed = bool(
            failed_at
            and now >= failed_at + CREATE_RECONCILIATION_GRACE
            and now >= first_zero + CREATE_RECONCILIATION_GRACE
        )
        scan = {
            "complete_scan": True,
            "matching_candidates": 0,
            "complete_zero_scans": zero_scans,
            "first_zero_scan_at": _iso(first_zero),
            "last_zero_scan_at": _iso(now),
            "grace_elapsed": grace_elapsed,
        }
        if zero_scans >= 2 and grace_elapsed:
            confirmed = await _collection(db, PROPOSAL_COLLECTION).update_one(
                {
                    "user_id": user_id,
                    "proposal_id": proposal_id,
                    "status": "failed",
                    "provider_write_uncertain": True,
                    "create_reconciliation_scan_active": True,
                    "create_reconciliation_scan_token": scan_token,
                },
                {
                    "$set": {
                        "create_reconciliation_scan_active": False,
                        "create_reconciliation_scan_finished_at": _iso(),
                        "provider_write_reached": False,
                        "provider_write_state": "confirmed_not_applied",
                        "provider_write_uncertain": False,
                        "execution_retryable": False,
                        "automatic_retry_allowed": False,
                        "recovery_action": "create_new_preview",
                        "create_reconciliation": scan,
                    }
                },
            )
            if not getattr(confirmed, "matched_count", 1):
                raise HTTPException(
                    status_code=409,
                    detail={"code": "snapchat_management_reconciliation_race"},
                )
            await _release_observed_entity_lease(
                db, user_id=user_id, lease=intent_lease
            )
            updated = await _collection(db, PROPOSAL_COLLECTION).find_one(
                {"user_id": user_id, "proposal_id": row.get("proposal_id")},
                {"_id": 0},
            )
            return _public_proposal(updated or row)
        recorded = await _collection(db, PROPOSAL_COLLECTION).update_one(
            {
                "user_id": user_id,
                "proposal_id": proposal_id,
                "status": "failed",
                "provider_write_uncertain": True,
                "create_reconciliation_scan_active": True,
                "create_reconciliation_scan_token": scan_token,
            },
            {
                "$set": {
                    "create_reconciliation_scan_active": False,
                    "create_reconciliation_scan_finished_at": _iso(),
                    "create_reconciliation": scan,
                    "recovery_action": "repeat_read_only_reconciliation_after_grace",
                }
            },
        )
        if not getattr(recorded, "matched_count", 1):
            raise HTTPException(
                status_code=409,
                detail={"code": "snapchat_management_reconciliation_race"},
            )
        updated = await _collection(db, PROPOSAL_COLLECTION).find_one(
            {"user_id": user_id, "proposal_id": row.get("proposal_id")},
            {"_id": 0},
        )
        return _public_proposal(updated or row)

    candidate_id = str(matches[0].get("id") or "")
    try:
        verified = await client.read_entity(operation["entity_type"], candidate_id)
    except Exception:
        await _record_create_reconciliation_inconclusive(
            db,
            user_id=user_id,
            proposal_id=proposal_id,
            scan_token=scan_token,
            reason="exact_candidate_readback_unavailable",
            matching_candidates=1,
        )
        raise
    if (
        str(verified.get("id") or "") != candidate_id
        or not _create_candidate_base_evidence_complete(row, verified)
        or not _create_candidate_submitted_shape_complete(operation, verified)
        or not _create_candidate_matches(row, operation, verified)
    ):
        await _record_create_reconciliation_inconclusive(
            db,
            user_id=user_id,
            proposal_id=proposal_id,
            scan_token=scan_token,
            reason="exact_candidate_readback_drifted",
            matching_candidates=1,
        )
        raise HTTPException(
            status_code=409,
            detail={"code": "snapchat_management_reconciliation_candidate_drift"},
        )
    # A point GET verifies the entity body but cannot prove uniqueness. Repeat
    # the complete deleted-aware catalogue after readback, and adopt only if
    # the same ID remains the sole exact candidate with no plausible row.
    try:
        confirmation_catalog = await client.list_create_reconciliation_candidates(
            operation
        )
    except Exception:
        await _record_create_reconciliation_inconclusive(
            db,
            user_id=user_id,
            proposal_id=proposal_id,
            scan_token=scan_token,
            reason="exact_candidate_confirmation_catalog_unavailable",
            matching_candidates=1,
            catalog_incomplete=True,
        )
        raise
    confirmation_matches, confirmation_plausible, confirmation_incomplete = (
        _classify_create_reconciliation_catalog(
            row, operation, confirmation_catalog
        )
    )
    if (
        confirmation_incomplete
        or confirmation_plausible
        or len(confirmation_matches) != 1
        or str(confirmation_matches[0].get("id") or "") != candidate_id
    ):
        await _record_create_reconciliation_inconclusive(
            db,
            user_id=user_id,
            proposal_id=proposal_id,
            scan_token=scan_token,
            reason="exact_candidate_confirmation_changed",
            matching_candidates=len(confirmation_matches),
            plausible_candidates=len(confirmation_plausible),
            catalog_incomplete=confirmation_incomplete,
        )
        raise HTTPException(
            status_code=409,
            detail={
                "code": "snapchat_management_reconciliation_confirmation_failed",
                "matching_candidates": len(confirmation_matches),
                "plausible_candidates": len(confirmation_plausible),
                "catalog_incomplete": confirmation_incomplete,
            },
        )
    claimed = await _collection(db, PROPOSAL_COLLECTION).update_one(
        {
            "user_id": user_id,
            "proposal_id": row.get("proposal_id"),
            "status": "failed",
            "provider_write_uncertain": True,
            "create_reconciliation_scan_active": True,
            "create_reconciliation_scan_token": scan_token,
        },
        {
            "$set": {
                "status": "executing",
                "create_reconciliation_scan_active": False,
                "create_reconciliation_scan_finished_at": _iso(),
                "provider_entity_id": candidate_id,
                "create_reconciliation": {
                    "complete_scan": True,
                    "matching_candidates": 1,
                    "scanned_at": _iso(),
                },
            }
        },
    )
    if not getattr(claimed, "matched_count", 1):
        raise HTTPException(
            status_code=409,
            detail={"code": "snapchat_management_reconciliation_race"},
        )
    result = await _complete_verified_execution(
        db,
        user_id=user_id,
        actor_id=actor_id,
        proposal_id=str(row.get("proposal_id") or ""),
        row=row,
        operation=operation,
        account=account,
        provider_entity_id=candidate_id,
        verified=verified,
        event="execution_reconciled_create_by_exact_catalog_match",
    )
    await _release_observed_entity_lease(
        db, user_id=user_id, lease=intent_lease
    )
    return result


async def _execute_snapchat_management_proposal_under_lease(
    db: Any,
    user_id: str,
    actor_id: str,
    proposal_id: str,
    *,
    provider: SnapchatManagementProvider | None = None,
) -> dict[str, Any]:
    if not snapchat_campaign_mutations_enabled():
        raise HTTPException(
            status_code=409,
            detail={
                "code": "snapchat_campaign_mutations_disabled",
                "message": "تشغيل كتابة حملات Snapchat متوقف بمفتاح الأمان.",
            },
        )
    row = await _collection(db, PROPOSAL_COLLECTION).find_one(
        {"user_id": user_id, "proposal_id": proposal_id}, {"_id": 0}
    )
    if not row:
        raise HTTPException(
            status_code=404, detail={"code": "snapchat_management_proposal_not_found"}
        )
    if row.get("status") == "completed":
        return _public_proposal(row)
    if row.get("status") not in {"approved", "approved_v2"}:
        raise HTTPException(
            status_code=409,
            detail={"code": "snapchat_management_proposal_not_executable"},
        )
    if _expired(row.get("expires_at")):
        raise HTTPException(
            status_code=409,
            detail={"code": "snapchat_management_proposal_expired"},
        )
    operation = dict(row.get("operation") or {})
    if (
        str(row.get("action") or "") == "ad_squad.create"
        and _is_pixel_optimization_goal(
            _operation_create_entity(operation).get("optimization_goal")
        )
        and int(row.get("safety_protocol_version") or 1) != 2
    ):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "snapchat_management_pixel_preview_v2_required",
                "message": "أنشئ معاينة v2 جديدة بعد التحقق من Pixel وأهليته.",
            },
        )
    if row.get("products") and row.get("product_link_state") not in {
        "confirmed",
        "adopted",
    }:
        linked = await _ensure_proposal_product_links(db, user_id, actor_id, row)
        if not linked:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "snapchat_management_product_link_unavailable",
                    "message": (
                        "تعذر تثبيت ارتباط المنتج بالحملة؛ لم يصل أي تعديل "
                        "إلى Snapchat ويمكن إعادة المحاولة بأمان."
                    ),
                },
            )
    try:
        _assert_stored_operation_integrity(row, operation)
    except ValueError as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": "snapchat_management_operation_integrity_failed"},
        ) from exc
    if (
        operation.get("activates_delivery")
        and not snapchat_campaign_activation_enabled()
    ):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "snapchat_campaign_activation_disabled",
                "message": "تشغيل الحملات متوقف بمفتاح أمان مستقل؛ يمكن الإنشاء والتعديل والإيقاف فقط.",
            },
        )
    account = await _selected_account(db, user_id, str(row.get("account_id") or ""))
    client = provider or SnapchatManagementProvider(db, user_id)
    role = await client.management_role(account, str(row.get("action") or ""))
    if not role.get("allowed"):
        raise HTTPException(
            status_code=409, detail={"code": "snapchat_management_role_missing"}
        )
    if str(row.get("action") or "") in UPDATE_ACTIONS:
        preflight_entity = await client.read_entity(
            str(operation.get("entity_type") or ""),
            str(row.get("target_id") or ""),
        )
        preflight_stale_fields = _provider_state_conflict_fields(
            operation,
            dict(row.get("original_snapshot") or {}),
            preflight_entity,
        )
        if preflight_stale_fields:
            raise HTTPException(
                status_code=409,
                detail=_provider_state_conflict_detail(preflight_stale_fields),
            )
    if _is_delivery_increase(
        operation, dict(row.get("original_snapshot") or {}) or None
    ):
        current_baseline = await _capture_proposal_baseline(
            db,
            user_id,
            account=account,
            account_id=str(row.get("account_id") or ""),
            campaign_id=(row.get("baseline") or {}).get("campaign_id"),
            ad_squad_id=(
                str(row.get("target_id") or "")
                if operation.get("entity_type") == "ad_squad"
                else (
                    str(row.get("parent_id") or "")
                    if operation.get("entity_type") == "ad"
                    else None
                )
            ),
            ad_id=(
                str(row.get("target_id") or "")
                if operation.get("entity_type") == "ad"
                else None
            ),
            products=list(row.get("products") or []),
        )
        if (
            current_baseline.get("inventory_delivery_blocked") is True
            or current_baseline.get("inventory_verification_status") != "verified"
        ):
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "snapchat_management_inventory_changed_before_execution",
                    "message": (
                        "تعذر التحقق من المخزون الحالي أو لم يعد يسمح بزيادة "
                        "التسليم؛ لم يصل أي تعديل إلى Snapchat."
                    ),
                    "inventory_verification_status": current_baseline.get(
                        "inventory_verification_status"
                    ),
                },
            )
    approved_status = str(row.get("status") or "")
    locked = await _collection(db, PROPOSAL_COLLECTION).update_one(
        {
            "user_id": user_id,
            "proposal_id": proposal_id,
            "status": approved_status,
        },
        {
            "$set": {
                "status": "executing",
                "execution_started_at": _iso(),
                "provider_write_state": "preflighting",
                "provider_write_reached": False,
                "provider_write_uncertain": False,
            }
        },
    )
    if not getattr(locked, "matched_count", 1):
        raise HTTPException(
            status_code=409, detail={"code": "snapchat_management_execution_race"}
        )
    # Configuration can change while preflight checks are running.  Re-read
    # both independent kill switches only after the proposal and entity locks
    # have been acquired, immediately before any provider write.
    if not snapchat_campaign_mutations_enabled():
        await _reset_execution_claim(
            db,
            user_id=user_id,
            proposal_id=proposal_id,
            failure={"code": "snapchat_campaign_mutations_disabled_after_lock"},
        )
        raise HTTPException(
            status_code=409,
            detail={"code": "snapchat_campaign_mutations_disabled"},
        )
    if (
        operation.get("activates_delivery")
        and not snapchat_campaign_activation_enabled()
    ):
        await _reset_execution_claim(
            db,
            user_id=user_id,
            proposal_id=proposal_id,
            failure={"code": "snapchat_campaign_activation_disabled_after_lock"},
        )
        raise HTTPException(
            status_code=409,
            detail={"code": "snapchat_campaign_activation_disabled"},
        )
    expected_outcome = (
        row.get("expected") if isinstance(row.get("expected"), dict) else {}
    )
    if expected_outcome.get("source") == "ai_recommendation_5h":
        # Revalidate analytical quality under the durable lease *before* the
        # final provider read.  The read/state comparison and provider POST can
        # then remain adjacent with no intervening DB await.
        import campaign_ai_execution_quality_gate as execution_quality

        try:
            if (
                expected_outcome.get("execution_quality_contract")
                != execution_quality.CONTRACT_VERSION
            ):
                raise execution_quality.ExecutionQualityBlocked(
                    ["execution_quality_contract_missing"]
                )
            await execution_quality.preflight_approved_execution(
                db,
                recommendation_collection="mezan_campaign_ai_recommendations_v1",
                user_id=user_id,
                snapshot_id=str(expected_outcome.get("snapshot_id") or ""),
                recommendation_id=str(
                    expected_outcome.get("recommendation_id") or ""
                ),
                expected_digest=str(
                    expected_outcome.get("snapshot_digest") or ""
                ),
            )
        except execution_quality.ExecutionQualityBlocked as exc:
            failure = {
                "code": "campaign_execution_data_quality_blocked",
                "contract_version": execution_quality.CONTRACT_VERSION,
                "blockers": exc.blockers,
            }
            await _reset_execution_claim(
                db,
                user_id=user_id,
                proposal_id=proposal_id,
                failure=failure,
            )
            raise HTTPException(status_code=409, detail=failure) from exc
    # Final provider read happens after the proposal lock and immediately
    # before the write.  If any delivery-sensitive field drifted since the
    # immutable preview, fail closed and leave the provider untouched.
    if str(row.get("action") or "") in UPDATE_ACTIONS:
        try:
            current_entity = await client.read_entity(
                str(operation.get("entity_type") or ""),
                str(row.get("target_id") or ""),
            )
        except Exception:
            # No provider write has been attempted yet.  Release the local
            # execution lock so a transient read failure cannot strand an
            # otherwise valid proposal in ``executing`` forever.
            await _reset_execution_claim(
                db,
                user_id=user_id,
                proposal_id=proposal_id,
                failure={"code": "snapchat_management_final_read_failed"},
            )
            raise
        stale_fields = _provider_state_conflict_fields(
            operation,
            dict(row.get("original_snapshot") or {}),
            current_entity,
        )
        if stale_fields:
            await _reset_execution_claim(
                db,
                user_id=user_id,
                proposal_id=proposal_id,
                failure=_provider_state_conflict_detail(stale_fields),
            )
            raise HTTPException(
                status_code=409,
                detail=_provider_state_conflict_detail(stale_fields),
            )
    final_pixel_eligibility: dict[str, Any] | None = None
    final_ad_dependency_verification: dict[str, Any] | None = None
    if str(row.get("action") or "") == "ad_squad.create":
        try:
            # This is the final provider-read sequence after the durable lease
            # and proposal claim: parent -> complete account Pixel catalogue ->
            # exact goal/window eligibility.  No provider write can happen on
            # any failure and no other provider call is interposed before POST.
            final_pixel_eligibility = await client.validate_pixel_ad_squad_intent(
                account_id=str(row.get("account_id") or ""),
                parent_id=str(row.get("parent_id") or ""),
                operation=operation,
                reread_parent=True,
            )
        except Exception:
            await _reset_execution_claim(
                db,
                user_id=user_id,
                proposal_id=proposal_id,
                failure={"code": "snapchat_management_pixel_final_preflight_failed"},
            )
            raise
    if str(row.get("action") or "") == "ad.create":
        try:
            # Re-read the complete ad dependency chain after the durable
            # proposal/entity claims. Snapchat can change any of these
            # relationships after preview; no POST is allowed on drift.
            final_ad_dependency_verification = (
                await client.validate_ad_create_dependencies(
                    account_id=str(row.get("account_id") or ""),
                    parent_id=str(row.get("parent_id") or ""),
                    operation=operation,
                )
            )
        except Exception:
            await _reset_execution_claim(
                db,
                user_id=user_id,
                proposal_id=proposal_id,
                failure={"code": "snapchat_management_ad_final_preflight_failed"},
            )
            raise
    if not snapchat_campaign_mutations_enabled():
        await _reset_execution_claim(
            db,
            user_id=user_id,
            proposal_id=proposal_id,
            failure={"code": "snapchat_campaign_mutations_disabled_after_final_read"},
        )
        raise HTTPException(
            status_code=409,
            detail={"code": "snapchat_campaign_mutations_disabled"},
        )
    if (
        operation.get("activates_delivery")
        and not snapchat_campaign_activation_enabled()
    ):
        await _reset_execution_claim(
            db,
            user_id=user_id,
            proposal_id=proposal_id,
            failure={"code": "snapchat_campaign_activation_disabled_after_final_read"},
        )
        raise HTTPException(
            status_code=409,
            detail={"code": "snapchat_campaign_activation_disabled"},
        )
    write_claim = await _collection(db, PROPOSAL_COLLECTION).update_one(
        {
            "user_id": user_id,
            "proposal_id": proposal_id,
            "status": "executing",
            "provider_write_state": "preflighting",
        },
        {
            "$set": {
                "provider_write_state": "attempting",
                "provider_write_attempted_at": _iso(),
            }
        },
    )
    if not getattr(write_claim, "matched_count", 1):
        await _reset_execution_claim(
            db,
            user_id=user_id,
            proposal_id=proposal_id,
            failure={"code": "snapchat_management_provider_write_claim_failed"},
        )
        raise HTTPException(
            status_code=409,
            detail={"code": "snapchat_management_provider_write_claim_failed"},
        )
    provider_write_confirmed = False
    provider_entity_id = (
        str(row.get("target_id") or "")
        if str(row.get("action") or "") in UPDATE_ACTIONS
        else ""
    )
    expected = _operation_expected_values(operation)
    try:
        provider_entity = await client.execute(operation)
        provider_write_confirmed = True
        provider_entity_id = str(
            provider_entity.get("id") or row.get("target_id") or ""
        )
        await _collection(db, PROPOSAL_COLLECTION).update_one(
            {"user_id": user_id, "proposal_id": proposal_id, "status": "executing"},
            {
                "$set": {
                    "provider_write_reached": True,
                    "provider_write_state": "confirmed",
                    "provider_write_uncertain": False,
                    "provider_entity_id": provider_entity_id,
                    # Preserve the bounded response before starting a separate
                    # verification request.  If that request fails, recovery
                    # still knows exactly what Snapchat acknowledged.
                    "provider_response_snapshot": _safe_provider_value(provider_entity),
                    "execution_pixel_eligibility": _safe_provider_value(
                        final_pixel_eligibility or {}
                    ),
                    "execution_ad_dependency_verification": _safe_provider_value(
                        final_ad_dependency_verification or {}
                    ),
                }
            },
        )
        verified = await client.read_entity(
            operation["entity_type"], provider_entity_id
        )
        verified_ok, mismatches = _changed_values_match(expected, verified)
        if not verified_ok:
            raise HTTPException(
                status_code=502,
                detail={
                    "code": "snapchat_management_verification_failed",
                    "mismatched_fields": mismatches,
                },
            )
        return await _complete_verified_execution(
            db,
            user_id=user_id,
            actor_id=actor_id,
            proposal_id=proposal_id,
            row=row,
            operation=operation,
            account=account,
            provider_entity_id=provider_entity_id,
            verified=verified,
            event="executed_and_verified",
        )
    except Exception as exc:
        detail = (
            exc.detail
            if isinstance(exc, HTTPException)
            else {"code": "snapchat_management_execution_failed"}
        )
        readback: dict[str, Any] | None = None
        if provider_entity_id:
            try:
                readback = await client.read_entity(
                    operation["entity_type"], provider_entity_id
                )
            except Exception:
                readback = None

        if readback is not None:
            planned_ok, _ = _changed_values_match(expected, readback)
            if planned_ok:
                return await _complete_verified_execution(
                    db,
                    user_id=user_id,
                    actor_id=actor_id,
                    proposal_id=proposal_id,
                    row=row,
                    operation=operation,
                    account=account,
                    provider_entity_id=provider_entity_id,
                    verified=readback,
                    event="execution_reconciled_applied",
                )

        original_ok = False
        if readback is not None and str(row.get("action") or "") in UPDATE_ACTIONS:
            original_ok = not _provider_state_conflict_fields(
                operation,
                dict(row.get("original_snapshot") or {}),
                readback,
            )

        explicit_validation_no_write = bool(
            isinstance(detail, dict)
            and isinstance(detail.get("provider_no_write_proof"), dict)
            and not provider_write_confirmed
            and not provider_entity_id
        )
        known_not_applied = original_ok or explicit_validation_no_write
        uncertain = not known_not_applied
        write_state = (
            "confirmed_not_applied"
            if known_not_applied
            else "unknown_needs_reconciliation"
        )
        recovery_action = (
            "create_new_preview"
            if known_not_applied
            else "manual_reconcile_provider_entity_before_retry_or_rollback"
        )
        safe_detail = dict(detail) if isinstance(detail, dict) else {}
        safe_detail.update(
            {
                "reconciliation": (
                    "current_matches_original"
                    if original_ok
                    else "explicit_provider_validation_rejection"
                    if explicit_validation_no_write
                    else "mixed_or_unavailable"
                ),
                "recovery_action": recovery_action,
            }
        )
        await _collection(db, PROPOSAL_COLLECTION).update_one(
            {"user_id": user_id, "proposal_id": proposal_id, "status": "executing"},
            {
                "$set": {
                    "status": "failed",
                    "failed_at": _iso(),
                    "failure": _safe_provider_value(safe_detail),
                    "provider_write_reached": (
                        provider_write_confirmed or readback is not None
                    )
                    and not known_not_applied,
                    "provider_write_state": write_state,
                    "provider_write_uncertain": uncertain,
                    "execution_retryable": False,
                    "automatic_retry_allowed": False,
                    "recovery_action": recovery_action,
                    "reconciliation_snapshot": _safe_provider_value(readback or {}),
                    **(
                        {"provider_entity_id": provider_entity_id}
                        if provider_entity_id
                        else {}
                    ),
                }
            },
        )
        await _audit(
            db,
            user_id=user_id,
            proposal_id=proposal_id,
            event="execution_failed",
            actor_id=actor_id,
            detail={
                **safe_detail,
                "provider_write_state": write_state,
                "provider_entity_id": provider_entity_id or None,
            },
        )
        failed_row = await _collection(db, PROPOSAL_COLLECTION).find_one(
            {"user_id": user_id, "proposal_id": proposal_id}, {"_id": 0}
        )
        if failed_row:
            await _record_management_decision_deferred_safe(
                db, user_id, actor_id, proposal_id, failed_row
            )
        raise


async def reconcile_snapchat_management_proposal(
    db: Any,
    user_id: str,
    actor_id: str,
    proposal_id: str,
    *,
    provider: SnapchatManagementProvider | None = None,
) -> dict[str, Any]:
    """Read Snapchat and resolve an uncertain write without issuing a write."""
    row = await _collection(db, PROPOSAL_COLLECTION).find_one(
        {"user_id": user_id, "proposal_id": proposal_id}, {"_id": 0}
    )
    if not row:
        raise HTTPException(
            status_code=404,
            detail={"code": "snapchat_management_proposal_not_found"},
        )
    row = await _retry_terminal_product_adoption(db, user_id, actor_id, row)
    lease = await _active_proposal_entity_lease(
        db, user_id=user_id, proposal_id=proposal_id
    )
    lease_expired = bool(lease and _expired(lease.get("expires_at")))
    lease_kind = str((lease or {}).get("operation_kind") or "")
    execution_state = str(row.get("provider_write_state") or "not_attempted")
    rollback_state = str(row.get("rollback_write_state") or "not_attempted")

    verified_execution_terminal = bool(
        row.get("status") == "completed"
        and isinstance(row.get("verification"), dict)
        and (row.get("verification") or {}).get("verified") is True
        and row.get("provider_write_uncertain") is not True
    )
    verified_rollback_terminal = bool(
        row.get("status") == "rolled_back"
        and isinstance(row.get("rollback"), dict)
        and (row.get("rollback") or {}).get("status") in {"verified", "neutralized"}
        and row.get("rollback_write_uncertain") is not True
    )
    known_execution_no_write = bool(
        row.get("status") == "failed"
        and execution_state == "confirmed_not_applied"
        and row.get("provider_write_uncertain") is not True
    )
    known_rollback_no_write = bool(
        rollback_state == "confirmed_not_applied"
        and row.get("rollback_write_uncertain") is not True
        and row.get("status") != "rolling_back"
    )
    if lease_expired and (
        verified_execution_terminal
        or verified_rollback_terminal
        or known_execution_no_write
        or known_rollback_no_write
    ):
        await _release_observed_entity_lease(db, user_id=user_id, lease=lease)
        await _audit(
            db,
            user_id=user_id,
            proposal_id=proposal_id,
            event="expired_terminal_entity_lease_released",
            actor_id=actor_id,
            detail={"status": row.get("status")},
        )
        updated = await _collection(db, PROPOSAL_COLLECTION).find_one(
            {"user_id": user_id, "proposal_id": proposal_id}, {"_id": 0}
        )
        return _public_proposal(updated or row)

    # A worker can stop after inserting the entity fence but before claiming
    # the proposal.  Only this exact, expired, provably no-write state is safe
    # to release without reading or writing Snapchat.
    if (
        lease_expired
        and lease_kind == "execute"
        and row.get("status") in {"approved", "approved_v2"}
        and execution_state == "not_attempted"
        and row.get("provider_write_reached") is not True
        and row.get("provider_write_uncertain") is not True
    ):
        await _release_observed_entity_lease(db, user_id=user_id, lease=lease)
        await _audit(
            db,
            user_id=user_id,
            proposal_id=proposal_id,
            event="expired_no_write_entity_lease_released",
            actor_id=actor_id,
            detail={"recovery_action": "retry_approved_proposal"},
        )
        updated = await _collection(db, PROPOSAL_COLLECTION).find_one(
            {"user_id": user_id, "proposal_id": proposal_id}, {"_id": 0}
        )
        return _public_proposal(updated or row)

    # ``preflighting`` is persisted before any provider POST can occur. An
    # expired lease in this exact state therefore proves no write was sent.
    # Restore through a CAS, then release only the exact expired lease token;
    # any ownership race leaves the provider-entity fence in place.
    if (
        lease_expired
        and lease_kind == "execute"
        and row.get("status") == "executing"
        and execution_state == "preflighting"
        and row.get("provider_write_reached") is not True
        and row.get("provider_write_uncertain") is not True
        and str((lease or {}).get("lease_token") or "")
    ):
        restored_status = (
            "approved_v2"
            if int(row.get("safety_protocol_version") or 1) == 2
            else "approved"
        )
        restored = await _collection(db, PROPOSAL_COLLECTION).update_one(
            {
                "user_id": user_id,
                "proposal_id": proposal_id,
                "status": "executing",
                "provider_write_state": "preflighting",
                "provider_write_reached": {"$ne": True},
                "provider_write_uncertain": {"$ne": True},
            },
            {
                "$set": {
                    "status": restored_status,
                    "provider_write_state": "not_attempted",
                    "provider_write_reached": False,
                    "provider_write_uncertain": False,
                    "execution_started_at": None,
                    "execution_retryable": True,
                    "automatic_retry_allowed": False,
                    "recovery_action": "retry_approved_proposal",
                    "failure": {
                        "code": "snapchat_management_worker_lost_during_preflight"
                    },
                }
            },
        )
        if not getattr(restored, "matched_count", 1):
            raise HTTPException(
                status_code=409,
                detail={"code": "snapchat_management_preflight_recovery_race"},
            )
        released = await _collection(db, ENTITY_LEASE_COLLECTION).update_one(
            {
                "user_id": user_id,
                "proposal_id": proposal_id,
                "lease_token": str((lease or {}).get("lease_token") or ""),
                "expires_at": (lease or {}).get("expires_at"),
                "active": True,
            },
            {"$set": {"active": False, "released_at": _iso()}},
        )
        if not getattr(released, "matched_count", 1):
            raise HTTPException(
                status_code=409,
                detail={"code": "snapchat_management_entity_lease_release_race"},
            )
        await _audit(
            db,
            user_id=user_id,
            proposal_id=proposal_id,
            event="expired_preflight_entity_lease_recovered",
            actor_id=actor_id,
            detail={"restored_status": restored_status},
        )
        updated = await _collection(db, PROPOSAL_COLLECTION).find_one(
            {"user_id": user_id, "proposal_id": proposal_id}, {"_id": 0}
        )
        return _public_proposal(updated or row)

    # Crash after the proposal claim may have happened during the provider
    # call.  After the durable fence expires, promote it to explicit
    # uncertainty and use the same read-only reconciliation as caught errors.
    if (
        lease_expired
        and lease_kind == "execute"
        and row.get("status") == "executing"
        and execution_state in {"attempting", "confirmed"}
        and row.get("provider_write_uncertain") is not True
    ):
        await _collection(db, PROPOSAL_COLLECTION).update_one(
            {
                "user_id": user_id,
                "proposal_id": proposal_id,
                "status": "executing",
                "provider_write_state": execution_state,
            },
            {
                "$set": {
                    "status": "failed",
                    "failed_at": _iso(),
                    "provider_write_state": "unknown_needs_reconciliation",
                    "provider_write_reached": (
                        row.get("provider_write_reached") is True
                        or execution_state == "confirmed"
                    ),
                    "provider_write_uncertain": True,
                    "automatic_retry_allowed": False,
                    "recovery_action": (
                        "manual_reconcile_provider_entity_before_retry_or_rollback"
                    ),
                    "failure": {
                        "code": "snapchat_management_worker_lost_during_execution"
                    },
                }
            },
        )
        row = (
            await _collection(db, PROPOSAL_COLLECTION).find_one(
                {"user_id": user_id, "proposal_id": proposal_id}, {"_id": 0}
            )
            or row
        )

    rollback_from_status = str(row.get("rollback_from_status") or "completed")
    if (
        lease_expired
        and lease_kind == "rollback"
        and row.get("status") == "rolling_back"
        and rollback_state == "not_attempted"
        and row.get("rollback_write_uncertain") is not True
    ):
        restored = await _collection(db, PROPOSAL_COLLECTION).update_one(
            {
                "user_id": user_id,
                "proposal_id": proposal_id,
                "status": "rolling_back",
                "rollback_write_state": "not_attempted",
                "rollback_write_uncertain": False,
            },
            {
                "$set": {
                    "status": rollback_from_status,
                    "rollback_write_state": "not_attempted",
                    "rollback_write_uncertain": False,
                    "rollback_recovery_action": "retry_rollback_with_confirmation",
                }
            },
        )
        if not getattr(restored, "matched_count", 1):
            raise HTTPException(
                status_code=409,
                detail={"code": "snapchat_management_reconciliation_race"},
            )
        await _release_observed_entity_lease(db, user_id=user_id, lease=lease)
        await _audit(
            db,
            user_id=user_id,
            proposal_id=proposal_id,
            event="expired_no_write_rollback_lease_released",
            actor_id=actor_id,
            detail={"restored_status": rollback_from_status},
        )
        updated = await _collection(db, PROPOSAL_COLLECTION).find_one(
            {"user_id": user_id, "proposal_id": proposal_id}, {"_id": 0}
        )
        return _public_proposal(updated or row)

    if (
        lease_expired
        and lease_kind == "rollback"
        and row.get("status") == "rolling_back"
        and rollback_state == "attempting"
        and row.get("rollback_write_uncertain") is not True
    ):
        await _collection(db, PROPOSAL_COLLECTION).update_one(
            {
                "user_id": user_id,
                "proposal_id": proposal_id,
                "status": "rolling_back",
                "rollback_write_state": "attempting",
            },
            {
                "$set": {
                    "rollback_write_state": "unknown_needs_reconciliation",
                    "rollback_write_uncertain": True,
                    "rollback_automatic_retry_allowed": False,
                    "rollback_recovery_action": "reconcile_uncertain_proposal",
                    "rollback_failure": {
                        "code": "snapchat_management_worker_lost_during_rollback"
                    },
                }
            },
        )
        row = (
            await _collection(db, PROPOSAL_COLLECTION).find_one(
                {"user_id": user_id, "proposal_id": proposal_id}, {"_id": 0}
            )
            or row
        )

    execution_uncertain = row.get("provider_write_uncertain") is True
    rollback_uncertain = row.get("rollback_write_uncertain") is True
    if not execution_uncertain and not rollback_uncertain:
        return _public_proposal(row)
    operation = dict(row.get("operation") or {})
    entity_id = str(row.get("provider_entity_id") or row.get("target_id") or "")
    if (
        not entity_id
        and execution_uncertain
        and str(row.get("action") or "") in DELIVERY_CREATE_ACTIONS
        and (
            not _provider_datetime(row.get("execution_started_at"))
            or not _provider_datetime(row.get("failed_at"))
            or _provider_datetime(row.get("failed_at"))
            < _provider_datetime(row.get("execution_started_at"))
        )
    ):
        await _audit(
            db,
            user_id=user_id,
            proposal_id=proposal_id,
            event="reconciliation_requires_manual_provider_identity",
            actor_id=actor_id,
            detail={
                "recovery_action": "manual_provider_identity_review",
                "reason": "legacy_attempt_window_missing",
            },
        )
        raise HTTPException(
            status_code=409,
            detail={
                "code": "snapchat_management_reconciliation_entity_unknown",
                "recovery_action": "manual_provider_identity_review",
            },
        )
    account = await _selected_account(db, user_id, str(row.get("account_id") or ""))
    client = provider or SnapchatManagementProvider(db, user_id)
    role = await client.management_role(account, str(row.get("action") or ""))
    if not role.get("allowed"):
        raise HTTPException(
            status_code=409,
            detail={"code": "snapchat_management_role_missing"},
        )
    if not entity_id:
        if execution_uncertain and str(row.get("action") or "") in DELIVERY_CREATE_ACTIONS:
            return await _reconcile_uncertain_create_without_id(
                db,
                user_id=user_id,
                actor_id=actor_id,
                row=row,
                operation=operation,
                account=account,
                client=client,
            )
        # Creative creates and incomplete legacy rows remain manual: they lack
        # the PAUSED delivery invariant required for exact safe adoption.
        await _audit(
            db,
            user_id=user_id,
            proposal_id=proposal_id,
            event="reconciliation_requires_manual_provider_identity",
            actor_id=actor_id,
            detail={"recovery_action": "locate_provider_entity_id_manually"},
        )
        raise HTTPException(
            status_code=409,
            detail={
                "code": "snapchat_management_reconciliation_entity_unknown",
                "recovery_action": "locate_provider_entity_id_manually",
            },
        )
    try:
        current = await client.read_entity(operation["entity_type"], entity_id)
    except Exception:
        await _audit(
            db,
            user_id=user_id,
            proposal_id=proposal_id,
            event="reconciliation_read_failed",
            actor_id=actor_id,
            detail={"recovery_action": "retry_read_only_reconciliation"},
        )
        raise
    if str(current.get("id") or "") != entity_id:
        raise HTTPException(
            status_code=502,
            detail={"code": "snapchat_management_reconciliation_identity_mismatch"},
        )

    if execution_uncertain:
        expected = _operation_expected_values(operation)
        planned_ok, _ = _changed_values_match(expected, current)
        if planned_ok:
            claimed = await _collection(db, PROPOSAL_COLLECTION).update_one(
                {
                    "user_id": user_id,
                    "proposal_id": proposal_id,
                    "status": "failed",
                    "provider_write_uncertain": True,
                },
                {"$set": {"status": "executing"}},
            )
            if not getattr(claimed, "matched_count", 1):
                raise HTTPException(
                    status_code=409,
                    detail={"code": "snapchat_management_reconciliation_race"},
                )
            result = await _complete_verified_execution(
                db,
                user_id=user_id,
                actor_id=actor_id,
                proposal_id=proposal_id,
                row=row,
                operation=operation,
                account=account,
                provider_entity_id=entity_id,
                verified=current,
                event="execution_reconciled_applied",
            )
            await _release_observed_entity_lease(db, user_id=user_id, lease=lease)
            return result
        original_ok = str(
            row.get("action") or ""
        ) in UPDATE_ACTIONS and not _provider_state_conflict_fields(
            operation,
            dict(row.get("original_snapshot") or {}),
            current,
        )
        if original_ok:
            confirmed = await _collection(db, PROPOSAL_COLLECTION).update_one(
                {
                    "user_id": user_id,
                    "proposal_id": proposal_id,
                    "status": "failed",
                    "provider_write_uncertain": True,
                },
                {
                    "$set": {
                        "provider_write_reached": False,
                        "provider_write_state": "confirmed_not_applied",
                        "provider_write_uncertain": False,
                        "execution_retryable": False,
                        "automatic_retry_allowed": False,
                        "recovery_action": "create_new_preview",
                        "reconciliation_snapshot": _safe_provider_value(current),
                    }
                },
            )
            if not getattr(confirmed, "matched_count", 1):
                raise HTTPException(
                    status_code=409,
                    detail={"code": "snapchat_management_reconciliation_race"},
                )
            await _release_observed_entity_lease(db, user_id=user_id, lease=lease)
            return _public_proposal(
                await _collection(db, PROPOSAL_COLLECTION).find_one(
                    {"user_id": user_id, "proposal_id": proposal_id}, {"_id": 0}
                )
                or row
            )

    if rollback_uncertain:
        changes = dict(operation.get("changes") or {})
        original = dict(row.get("original_snapshot") or {})
        if row.get("action") in UPDATE_ACTIONS:
            restore = {key: original[key] for key in changes if key in original}
            removed_fields = [key for key in changes if key not in original]
        elif row.get("action") in DELIVERY_CREATE_ACTIONS:
            restore = {"status": "PAUSED"}
            removed_fields = []
        else:
            restore = {}
            removed_fields = []
        rolled_back, _ = _rollback_values_match(restore, removed_fields, current)
        verified_after = dict(
            (row.get("verification") or {}).get("provider_snapshot")
            or row.get("provider_response_snapshot")
            or {}
        )
        if rolled_back:
            await _refresh_verified_entity_cache_deferred_safe(
                db,
                user_id=user_id,
                actor_id=actor_id,
                proposal_id=proposal_id,
                account=account,
                entity_type=operation["entity_type"],
                entity=current,
                event="rollback_entity_cache_refresh_deferred",
            )
            confirmed = await _collection(db, PROPOSAL_COLLECTION).update_one(
                {
                    "user_id": user_id,
                    "proposal_id": proposal_id,
                    "rollback_write_uncertain": True,
                },
                {
                    "$set": {
                        "status": "rolled_back",
                        "rollback_write_state": "confirmed",
                        "rollback_write_uncertain": False,
                        "rollback": {
                            "status": "verified",
                            "before": _safe_provider_value(
                                row.get("rollback_before_snapshot") or verified_after
                            ),
                            "after": _safe_provider_value(current),
                            "rolled_back_at": _iso(),
                            "reason": row.get("rollback_requested_reason"),
                            "verification_source": "manual_read_only_reconciliation",
                        },
                    }
                },
            )
            if not getattr(confirmed, "matched_count", 1):
                raise HTTPException(
                    status_code=409,
                    detail={"code": "snapchat_management_reconciliation_race"},
                )
            await _release_observed_entity_lease(db, user_id=user_id, lease=lease)
            updated = await _collection(db, PROPOSAL_COLLECTION).find_one(
                {"user_id": user_id, "proposal_id": proposal_id}, {"_id": 0}
            )
            if updated:
                await _record_management_decision_deferred_safe(
                    db, user_id, actor_id, proposal_id, updated
                )
            return _public_proposal(updated or row)
        if verified_after and not _rollback_drift_fields(
            operation, verified_after, current
        ):
            confirmed = await _collection(db, PROPOSAL_COLLECTION).update_one(
                {
                    "user_id": user_id,
                    "proposal_id": proposal_id,
                    "rollback_write_uncertain": True,
                },
                {
                    "$set": {
                        "status": str(row.get("rollback_from_status") or "completed"),
                        "rollback_write_state": "confirmed_not_applied",
                        "rollback_write_uncertain": False,
                        "rollback_automatic_retry_allowed": False,
                        "rollback_recovery_action": "create_new_rollback_request",
                        "rollback_reconciliation_snapshot": _safe_provider_value(
                            current
                        ),
                    }
                },
            )
            if not getattr(confirmed, "matched_count", 1):
                raise HTTPException(
                    status_code=409,
                    detail={"code": "snapchat_management_reconciliation_race"},
                )
            await _release_observed_entity_lease(db, user_id=user_id, lease=lease)
            updated = await _collection(db, PROPOSAL_COLLECTION).find_one(
                {"user_id": user_id, "proposal_id": proposal_id}, {"_id": 0}
            )
            return _public_proposal(updated or row)

    await _collection(db, PROPOSAL_COLLECTION).update_one(
        {"user_id": user_id, "proposal_id": proposal_id},
        {
            "$set": {
                "reconciliation_snapshot": _safe_provider_value(current),
                "recovery_action": "manual_review_provider_drift",
            }
        },
    )
    await _audit(
        db,
        user_id=user_id,
        proposal_id=proposal_id,
        event="reconciliation_unresolved",
        actor_id=actor_id,
        detail={"recovery_action": "manual_review_provider_drift"},
    )
    raise HTTPException(
        status_code=409,
        detail={
            "code": "snapchat_management_reconciliation_unresolved",
            "recovery_action": "manual_review_provider_drift",
        },
    )


async def rollback_snapchat_management_proposal(
    db: Any,
    user_id: str,
    actor_id: str,
    proposal_id: str,
    payload: SnapchatManagementRollbackInput,
    *,
    provider: SnapchatManagementProvider | None = None,
) -> dict[str, Any]:
    row = await _collection(db, PROPOSAL_COLLECTION).find_one(
        {"user_id": user_id, "proposal_id": proposal_id}, {"_id": 0}
    )
    if not row:
        raise HTTPException(
            status_code=409,
            detail={"code": "snapchat_management_rollback_not_available"},
        )
    operation = dict(row.get("operation") or {})
    lease_token = await _acquire_entity_lease(
        db,
        user_id=user_id,
        row=row,
        operation=operation,
        operation_kind="rollback",
    )
    try:
        return await _rollback_snapchat_management_proposal_under_lease(
            db,
            user_id,
            actor_id,
            proposal_id,
            payload,
            provider=provider,
        )
    except asyncio.CancelledError:
        await _stabilize_cancelled_rollback(
            db,
            user_id=user_id,
            actor_id=actor_id,
            proposal_id=proposal_id,
        )
        raise
    finally:
        latest = await _collection(db, PROPOSAL_COLLECTION).find_one(
            {"user_id": user_id, "proposal_id": proposal_id}, {"_id": 0}
        )
        if (latest or {}).get("status") != "rolling_back" and not (latest or {}).get(
            "rollback_write_uncertain"
        ):
            await _release_entity_lease(
                db,
                user_id=user_id,
                row=row,
                operation=operation,
                lease_token=lease_token,
            )


async def _stabilize_cancelled_rollback(
    db: Any,
    *,
    user_id: str,
    actor_id: str,
    proposal_id: str,
) -> None:
    """Restore a known no-write rollback or fence a possibly written one."""
    row = await _collection(db, PROPOSAL_COLLECTION).find_one(
        {"user_id": user_id, "proposal_id": proposal_id}, {"_id": 0}
    )
    if not row or row.get("status") != "rolling_back":
        return
    write_state = str(row.get("rollback_write_state") or "not_attempted")
    rollback_from_status = str(row.get("rollback_from_status") or "completed")
    if write_state == "not_attempted":
        await _collection(db, PROPOSAL_COLLECTION).update_one(
            {
                "user_id": user_id,
                "proposal_id": proposal_id,
                "status": "rolling_back",
            },
            {
                "$set": {
                    "status": rollback_from_status,
                    "rollback_write_state": "not_attempted",
                    "rollback_write_uncertain": False,
                    "rollback_failure": {
                        "code": "snapchat_management_worker_cancelled_before_rollback_write"
                    },
                    "rollback_automatic_retry_allowed": False,
                    "rollback_recovery_action": "retry_rollback_with_confirmation",
                }
            },
        )
        event = "rollback_cancelled_before_write"
    else:
        await _collection(db, PROPOSAL_COLLECTION).update_one(
            {
                "user_id": user_id,
                "proposal_id": proposal_id,
                "status": "rolling_back",
            },
            {
                "$set": {
                    "rollback_write_state": "unknown_needs_reconciliation",
                    "rollback_write_uncertain": True,
                    "rollback_automatic_retry_allowed": False,
                    "rollback_recovery_action": "reconcile_uncertain_proposal",
                    "rollback_failure": {
                        "code": "snapchat_management_worker_cancelled_during_rollback",
                        "recovery_action": "reconcile_uncertain_proposal",
                    },
                }
            },
        )
        event = "rollback_cancelled_write_uncertain"
    try:
        await _audit(
            db,
            user_id=user_id,
            proposal_id=proposal_id,
            event=event,
            actor_id=actor_id,
            detail={"rollback_write_state_before_cancellation": write_state},
        )
    except Exception:
        return


async def _rollback_snapchat_management_proposal_under_lease(
    db: Any,
    user_id: str,
    actor_id: str,
    proposal_id: str,
    payload: SnapchatManagementRollbackInput,
    *,
    provider: SnapchatManagementProvider | None = None,
) -> dict[str, Any]:
    if not snapchat_campaign_mutations_enabled():
        raise HTTPException(
            status_code=409, detail={"code": "snapchat_campaign_mutations_disabled"}
        )
    row = await _collection(db, PROPOSAL_COLLECTION).find_one(
        {"user_id": user_id, "proposal_id": proposal_id}, {"_id": 0}
    )
    rollback_from_status = str(row.get("status") or "") if row else ""
    recovery_after_failed_verification = (
        (
            rollback_from_status == "failed"
            and row.get("provider_write_reached") is True
            and bool(row.get("provider_entity_id"))
        )
        if row
        else False
    )
    if not row or (
        rollback_from_status != "completed" and not recovery_after_failed_verification
    ):
        raise HTTPException(
            status_code=409,
            detail={"code": "snapchat_management_rollback_not_available"},
        )
    expected_phrase = f"تراجع {proposal_id[:8]}"
    if payload.confirmation_phrase.strip() != expected_phrase:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "snapchat_management_rollback_phrase_mismatch",
                "expected_phrase": expected_phrase,
            },
        )
    operation = dict(row.get("operation") or {})
    try:
        _assert_stored_operation_integrity(row, operation)
    except ValueError as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": "snapchat_management_operation_integrity_failed"},
        ) from exc
    client = provider or SnapchatManagementProvider(db, user_id)
    account = await _selected_account(db, user_id, str(row.get("account_id") or ""))
    role = await client.management_role(account, str(row.get("action") or ""))
    if not role.get("allowed"):
        raise HTTPException(
            status_code=409,
            detail={"code": "snapchat_management_role_missing"},
        )
    locked = await _collection(db, PROPOSAL_COLLECTION).update_one(
        {
            "user_id": user_id,
            "proposal_id": proposal_id,
            "status": rollback_from_status,
        },
        {
            "$set": {
                "status": "rolling_back",
                "rollback_started_at": _iso(),
                "rollback_started_by": actor_id,
                "rollback_from_status": rollback_from_status,
                "rollback_write_state": "not_attempted",
                "rollback_write_uncertain": False,
                "rollback_requested_reason": payload.reason,
            }
        },
    )
    if not getattr(locked, "matched_count", 1):
        raise HTTPException(
            status_code=409,
            detail={"code": "snapchat_management_rollback_race"},
        )
    rollback_baseline = await _capture_proposal_baseline(
        db,
        user_id,
        account=account,
        account_id=str(row.get("account_id") or ""),
        campaign_id=(row.get("baseline") or {}).get("campaign_id"),
        ad_squad_id=(
            str(row.get("target_id") or "")
            if operation.get("entity_type") == "ad_squad"
            else (
                str(row.get("parent_id") or "")
                if operation.get("entity_type") == "ad"
                else None
            )
        ),
        ad_id=(
            str(row.get("target_id") or "")
            if operation.get("entity_type") == "ad"
            else None
        ),
        products=list(row.get("products") or []),
    )
    await _collection(db, PROPOSAL_COLLECTION).update_one(
        {
            "user_id": user_id,
            "proposal_id": proposal_id,
            "status": "rolling_back",
        },
        {"$set": {"rollback_baseline": _safe_provider_value(rollback_baseline)}},
    )
    entity_id = str(row.get("provider_entity_id") or row.get("target_id") or "")
    removed_fields: list[str] = []
    if row.get("action") in UPDATE_ACTIONS:
        original = dict(row.get("original_snapshot") or {})
        changes = dict(operation.get("changes") or {})
        restore = {key: original[key] for key in changes if key in original}
        removed_fields = [key for key in changes if key not in original]
    elif row.get("action") in DELIVERY_CREATE_ACTIONS:
        restore = {"status": "PAUSED"}
    else:
        restore = {}
    verified_after = dict(
        (row.get("verification") or {}).get("provider_snapshot")
        or row.get("provider_response_snapshot")
        or {}
    )
    rollback_write_attempted = False
    rollback: dict[str, Any] | None = None
    try:
        current = await client.read_entity(operation["entity_type"], entity_id)
        await _collection(db, PROPOSAL_COLLECTION).update_one(
            {
                "user_id": user_id,
                "proposal_id": proposal_id,
                "status": "rolling_back",
            },
            {"$set": {"rollback_before_snapshot": _safe_provider_value(current)}},
        )
        if (restore or removed_fields) and not verified_after:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "snapchat_management_rollback_verified_snapshot_missing",
                    "message": (
                        "لا توجد لقطة موثقة لحالة ما بعد التنفيذ؛ أُوقف التراجع "
                        "حتى لا يمحو تعديلًا مباشرًا أحدث."
                    ),
                },
            )
        drift_fields = (
            _rollback_drift_fields(operation, verified_after, current)
            if verified_after
            else []
        )
        if drift_fields:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "snapchat_management_rollback_provider_drift",
                    "message": (
                        "تغير الكيان مباشرة في Snapchat بعد تنفيذ ميزان؛ "
                        "أُوقف التراجع لحماية التعديل الأحدث."
                    ),
                    "changed_fields": drift_fields,
                },
            )
        if not snapchat_campaign_mutations_enabled():
            raise HTTPException(
                status_code=409,
                detail={"code": "snapchat_campaign_mutations_disabled"},
            )
        if (
            str(restore.get("status") or "").upper() == "ACTIVE"
            and not snapchat_campaign_activation_enabled()
        ):
            raise HTTPException(
                status_code=409,
                detail={"code": "snapchat_campaign_activation_disabled"},
            )
        if not restore and not removed_fields:
            rollback = {
                "status": "neutralized",
                "detail": "Created creative remains unused and cannot spend independently.",
                "rolled_back_at": _iso(),
            }
        else:
            rollback_operation = _rollback_patch_operation(
                path=_entity_patch_path(operation["entity_type"], row, entity_id),
                plural=operation["plural"],
                singular=operation["singular"],
                entity_type=operation["entity_type"],
                restore=restore,
                removed_fields=removed_fields,
            )
            rollback_operation.update(
                {
                    "action": f"{row.get('action')}.rollback",
                    "account_id": row.get("account_id"),
                }
            )
            await _collection(db, PROPOSAL_COLLECTION).update_one(
                {
                    "user_id": user_id,
                    "proposal_id": proposal_id,
                    "status": "rolling_back",
                },
                {
                    "$set": {
                        "rollback_write_state": "attempting",
                        "rollback_write_uncertain": False,
                    }
                },
            )
            if not snapchat_campaign_mutations_enabled():
                raise HTTPException(
                    status_code=409,
                    detail={"code": "snapchat_campaign_mutations_disabled"},
                )
            if (
                str(restore.get("status") or "").upper() == "ACTIVE"
                and not snapchat_campaign_activation_enabled()
            ):
                raise HTTPException(
                    status_code=409,
                    detail={"code": "snapchat_campaign_activation_disabled"},
                )
            rollback_write_attempted = True
            provider_response = await client.execute(rollback_operation)
            await _collection(db, PROPOSAL_COLLECTION).update_one(
                {
                    "user_id": user_id,
                    "proposal_id": proposal_id,
                    "status": "rolling_back",
                },
                {
                    "$set": {
                        "rollback_write_state": "confirmed",
                        "rollback_provider_response_snapshot": _safe_provider_value(
                            provider_response
                        ),
                    }
                },
            )
            verified = await client.read_entity(operation["entity_type"], entity_id)
            ok, mismatches = _rollback_values_match(restore, removed_fields, verified)
            if not ok:
                raise HTTPException(
                    status_code=502,
                    detail={
                        "code": "snapchat_management_rollback_verification_failed",
                        "mismatched_fields": mismatches,
                    },
                )
            await _refresh_verified_entity_cache_deferred_safe(
                db,
                user_id=user_id,
                actor_id=actor_id,
                proposal_id=proposal_id,
                account=account,
                entity_type=operation["entity_type"],
                entity=verified,
                event="rollback_entity_cache_refresh_deferred",
            )
            rollback = {
                "status": "verified",
                "before": current,
                "after": verified,
                "rolled_back_at": _iso(),
                "reason": payload.reason,
            }
    except Exception as exc:
        failure = (
            exc.detail
            if isinstance(exc, HTTPException)
            else {"code": "snapchat_management_rollback_failed"}
        )
        readback: dict[str, Any] | None = None
        if rollback_write_attempted:
            try:
                readback = await client.read_entity(operation["entity_type"], entity_id)
            except Exception:
                readback = None
        if readback is not None:
            applied, _ = _rollback_values_match(restore, removed_fields, readback)
            if applied:
                await _refresh_verified_entity_cache_deferred_safe(
                    db,
                    user_id=user_id,
                    actor_id=actor_id,
                    proposal_id=proposal_id,
                    account=account,
                    entity_type=operation["entity_type"],
                    entity=readback,
                    event="rollback_entity_cache_refresh_deferred",
                )
                rollback = {
                    "status": "verified",
                    "before": current,
                    "after": readback,
                    "rolled_back_at": _iso(),
                    "reason": payload.reason,
                    "verification_source": "post_error_readback",
                }

        if rollback is not None:
            await _audit(
                db,
                user_id=user_id,
                proposal_id=proposal_id,
                event="rollback_reconciled_applied",
                actor_id=actor_id,
                detail={"entity_id": entity_id},
            )
        else:
            known_not_applied = bool(
                rollback_write_attempted
                and readback is not None
                and verified_after
                and not _rollback_drift_fields(operation, verified_after, readback)
            )
            uncertain = rollback_write_attempted and not known_not_applied
            recovery_action = (
                "create_new_rollback_request"
                if known_not_applied
                else (
                    "reconcile_uncertain_proposal"
                    if uncertain
                    else "review_and_retry_rollback"
                )
            )
            safe_failure = dict(failure) if isinstance(failure, dict) else {}
            safe_failure.update(
                {
                    "recovery_action": recovery_action,
                    "reconciliation": (
                        "current_matches_verified_after"
                        if known_not_applied
                        else "mixed_or_unavailable" if uncertain else "not_attempted"
                    ),
                }
            )
            await _collection(db, PROPOSAL_COLLECTION).update_one(
                {
                    "user_id": user_id,
                    "proposal_id": proposal_id,
                    "status": "rolling_back",
                },
                {
                    "$set": {
                        "status": rollback_from_status,
                        "rollback_failure": _safe_provider_value(safe_failure),
                        "rollback_failed_at": _iso(),
                        "rollback_write_state": (
                            "confirmed_not_applied"
                            if known_not_applied
                            else (
                                "unknown_needs_reconciliation"
                                if uncertain
                                else "not_attempted"
                            )
                        ),
                        "rollback_write_uncertain": uncertain,
                        "rollback_automatic_retry_allowed": False,
                        "rollback_recovery_action": recovery_action,
                        "rollback_reconciliation_snapshot": _safe_provider_value(
                            readback or {}
                        ),
                    }
                },
            )
            await _audit(
                db,
                user_id=user_id,
                proposal_id=proposal_id,
                event="rollback_failed",
                actor_id=actor_id,
                detail=safe_failure,
            )
            raise
    await _collection(db, PROPOSAL_COLLECTION).update_one(
        {"user_id": user_id, "proposal_id": proposal_id, "status": "rolling_back"},
        {
            "$set": {
                "status": "rolled_back",
                "rollback": _safe_provider_value(rollback),
                "rolled_back_by": actor_id,
                "rollback_write_state": (
                    "not_needed" if not restore and not removed_fields else "confirmed"
                ),
                "rollback_write_uncertain": False,
            }
        },
    )
    await _audit(
        db,
        user_id=user_id,
        proposal_id=proposal_id,
        event="rolled_back",
        actor_id=actor_id,
        detail={"reason": payload.reason},
    )
    rolled_back_row = await _collection(db, PROPOSAL_COLLECTION).find_one(
        {"user_id": user_id, "proposal_id": proposal_id}, {"_id": 0}
    )
    if rolled_back_row:
        await _record_management_decision_deferred_safe(
            db, user_id, actor_id, proposal_id, rolled_back_row
        )
    updated = await _collection(db, PROPOSAL_COLLECTION).find_one(
        {"user_id": user_id, "proposal_id": proposal_id}, {"_id": 0}
    )
    return _public_proposal(updated or row)


def _rollback_patch_operation(
    *,
    path: str,
    plural: str,
    singular: str,
    entity_type: str,
    restore: dict[str, Any],
    removed_fields: list[str],
) -> dict[str, Any]:
    _budget_guard(restore)
    patches = [
        {"op": "replace", "path": f"/{key}", "value": value}
        for key, value in sorted(restore.items())
    ]
    patches.extend(
        {"op": "remove", "path": f"/{key}"} for key in sorted(removed_fields)
    )
    if not patches:
        raise ValueError("rollback patch is empty")
    return {
        "method": "PATCH",
        "path": path,
        "plural": plural,
        "singular": singular,
        "entity_type": entity_type,
        "body": patches,
        "changes": restore,
    }


def _rollback_values_match(
    restore: dict[str, Any], removed_fields: list[str], actual: dict[str, Any]
) -> tuple[bool, list[str]]:
    ok, mismatches = _changed_values_match(restore, actual)
    for key in removed_fields:
        if key in actual and actual.get(key) is not None:
            mismatches.append(key)
    return ok and not mismatches, sorted(set(mismatches))


def _rollback_drift_fields(
    operation: dict[str, Any],
    verified_after: dict[str, Any],
    current: dict[str, Any],
) -> list[str]:
    entity_fields = {
        "campaign": CAMPAIGN_UPDATE_FIELDS,
        "ad_squad": AD_SQUAD_UPDATE_FIELDS,
        "ad": AD_UPDATE_FIELDS,
    }.get(str(operation.get("entity_type") or ""), set())
    controlled = {
        field for field in entity_fields if field in verified_after or field in current
    }
    controlled.update(_operation_expected_values(operation))
    controlled.add("status")
    return sorted(
        field for field in controlled if verified_after.get(field) != current.get(field)
    )


def _entity_patch_path(entity_type: str, row: dict[str, Any], entity_id: str) -> str:
    if entity_type == "campaign":
        return f"/adaccounts/{row.get('account_id')}/campaigns/{entity_id}"
    if entity_type == "ad_squad":
        return f"/campaigns/{row.get('parent_id')}/adsquads/{entity_id}"
    if entity_type == "ad":
        return f"/adsquads/{row.get('parent_id')}/ads/{entity_id}"
    raise HTTPException(
        status_code=409, detail={"code": "snapchat_management_rollback_neutralized"}
    )


async def snapchat_management_readiness(
    db: Any, user_id: str, *, provider: SnapchatManagementProvider | None = None
) -> dict[str, Any]:
    accounts = await _load_selected_accounts(db, user_id)
    client = provider or SnapchatManagementProvider(db, user_id)
    output = []
    for account in accounts:
        role = await client.management_role(account, "campaign.update")
        creative_role = await client.management_role(account, "creative.create")
        pixel_cursor = _collection(db, TRACKING_ASSET_COLLECTION).find(
            {
                "user_id": user_id,
                "$or": [
                    {"ad_account_id": str(account.get("ad_account_id") or "")},
                    {"ad_account_ids": str(account.get("ad_account_id") or "")},
                ],
                "pixel_id": {"$nin": [None, ""]},
            },
            {
                "_id": 0,
                "pixel_id": 1,
                "display_name": 1,
                "status": 1,
                "effective_status": 1,
                "diagnostics_status": 1,
                "has_event_data": 1,
                "last_observed_at": 1,
            },
        )
        if hasattr(pixel_cursor, "sort"):
            pixel_cursor = pixel_cursor.sort("last_observed_at", -1)
        if hasattr(pixel_cursor, "limit"):
            pixel_cursor = pixel_cursor.limit(101)
        if hasattr(pixel_cursor, "to_list"):
            cached_pixels = await pixel_cursor.to_list(length=101)
        elif isinstance(pixel_cursor, list):
            cached_pixels = pixel_cursor[:101]
        else:
            cached_pixels = []
        pixel_catalog_truncated = len(cached_pixels) > 100
        cached_pixels = cached_pixels[:100]
        visible_pixels = [
            {
                "pixel_id": str(row.get("pixel_id") or ""),
                "display_name": row.get("display_name") or row.get("pixel_id"),
                "status": row.get("status"),
                "effective_status": row.get("effective_status"),
                "diagnostics_status": row.get("diagnostics_status"),
                "has_event_data": row.get("has_event_data") is True,
                "last_observed_at": row.get("last_observed_at"),
            }
            for row in cached_pixels
            if row.get("pixel_id")
        ]
        output.append(
            {
                "account_id": account.get("ad_account_id"),
                "display_name": account.get("display_name"),
                "currency": account.get("currency"),
                "timezone": account.get("timezone"),
                "role": role.get("role"),
                "management_allowed": role.get("allowed") is True,
                "reason": role.get("reason"),
                "creative_role": creative_role.get("role"),
                "creative_allowed": creative_role.get("allowed") is True,
                "creative_reason": creative_role.get("reason"),
                "pixels": visible_pixels,
                "pixel_selection_required": len(visible_pixels) > 1,
                "pixel_catalog_truncated": pixel_catalog_truncated,
            }
        )
    return {
        "provider": SNAPCHAT_PROVIDER_ID,
        "proposal_enabled": True,
        "execution_enabled": snapchat_campaign_mutations_enabled(),
        "activation_enabled": snapchat_campaign_activation_enabled(),
        "max_daily_budget_micro": _max_daily_budget_micro(),
        "accounts": output,
        "required_lifecycle": [
            "proposal",
            "preview",
            "approval",
            "execution",
            "verification",
            "audit",
            "rollback",
        ],
        "new_entities_status": "PAUSED",
        "salla_permission_dependency": False,
        "safety_protocol_version": MANAGEMENT_SAFETY_PROTOCOL_VERSION,
        "mutation_rollout_requires_homogeneous_replicas": True,
    }


def attach_snapchat_campaign_management_routes(
    router: APIRouter,
    db: Any,
    current_user: Callable,
    require_owner: Callable[[Any], dict],
) -> None:
    @router.get(f"/{SNAPCHAT_PROVIDER_ID}/management/readiness")
    async def readiness(user: dict = Depends(current_user)) -> dict[str, Any]:
        owner = require_owner(user)
        return await snapchat_management_readiness(db, str(owner["id"]))

    @router.get(f"/{SNAPCHAT_PROVIDER_ID}/management/proposals")
    async def list_proposals(
        limit: int = Query(default=50, ge=1, le=100), user: dict = Depends(current_user)
    ) -> dict[str, Any]:
        owner = require_owner(user)
        cursor = (
            _collection(db, PROPOSAL_COLLECTION)
            .find(
                {"user_id": str(owner["id"])},
                {
                    "_id": 0,
                    "confirm_token_hash": 0,
                    "original_snapshot": 0,
                    "operation.body": 0,
                },
            )
            .sort("created_at", -1)
            .limit(limit)
        )
        rows = await cursor.to_list(length=limit)
        return {
            "provider": SNAPCHAT_PROVIDER_ID,
            "proposals": [_public_proposal(row) for row in rows],
        }

    @router.post(
        f"/{SNAPCHAT_PROVIDER_ID}/management/proposals",
        status_code=status.HTTP_201_CREATED,
    )
    async def create_proposal(
        payload: SnapchatManagementProposalInput, user: dict = Depends(current_user)
    ) -> dict[str, Any]:
        owner = require_owner(user)
        return await create_snapchat_management_proposal(
            db, str(owner["id"]), str(owner["id"]), payload
        )

    @router.post(
        f"/{SNAPCHAT_PROVIDER_ID}/management/proposals/{{proposal_id}}/approve"
    )
    async def approve_proposal(
        proposal_id: str,
        payload: SnapchatManagementApprovalInput,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        owner = require_owner(user)
        return await approve_snapchat_management_proposal(
            db,
            str(owner["id"]),
            str(owner["id"]),
            _identifier(proposal_id, field="proposal_id"),
            payload,
        )

    @router.post(
        f"/{SNAPCHAT_PROVIDER_ID}/management/proposals/{{proposal_id}}/execute",
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def execute_proposal(
        proposal_id: str,
        background_tasks: BackgroundTasks,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        owner = require_owner(user)
        normalized_id = _identifier(proposal_id, field="proposal_id")
        queued = await _collection(db, PROPOSAL_COLLECTION).find_one(
            {"user_id": str(owner["id"]), "proposal_id": normalized_id}, {"_id": 0}
        )
        if not queued:
            raise HTTPException(
                status_code=404,
                detail={"code": "snapchat_management_proposal_not_found"},
            )
        if queued.get("status") == "completed":
            return _public_proposal(queued)
        if queued.get("status") not in {"approved", "approved_v2"}:
            raise HTTPException(
                status_code=409,
                detail={"code": "snapchat_management_proposal_not_executable"},
            )
        if _expired(queued.get("expires_at")):
            await _collection(db, PROPOSAL_COLLECTION).update_one(
                {
                    "user_id": str(owner["id"]),
                    "proposal_id": normalized_id,
                    "status": queued.get("status"),
                },
                {
                    "$set": {
                        "status": "failed",
                        "failure": {"code": "snapchat_management_proposal_expired"},
                        "failed_at": _iso(),
                        "execution_retryable": False,
                        "automatic_retry_allowed": False,
                        "recovery_action": "create_new_preview",
                    }
                },
            )
            raise HTTPException(
                status_code=409,
                detail={"code": "snapchat_management_proposal_expired"},
            )

        async def execute_in_background() -> None:
            try:
                await execute_snapchat_management_proposal(
                    db,
                    str(owner["id"]),
                    str(owner["id"]),
                    normalized_id,
                )
            except Exception as exc:
                # Provider-attempt failures are persisted by the execution
                # function.  Persist pre-lock/pre-attempt failures too so a 202
                # response can never strand the UI at an unexplained state.
                detail = (
                    exc.detail
                    if isinstance(exc, HTTPException)
                    else {"code": "snapchat_management_background_execution_failed"}
                )
                safe_detail = (
                    dict(detail)
                    if isinstance(detail, dict)
                    else {"code": "snapchat_management_background_execution_failed"}
                )
                code = str(safe_detail.get("code") or "")
                requires_new_preview = code in {
                    "snapchat_management_proposal_expired",
                    "snapchat_management_operation_integrity_failed",
                    "snapchat_management_provider_state_conflict",
                    "snapchat_management_inventory_changed_before_execution",
                }
                await _collection(db, PROPOSAL_COLLECTION).update_one(
                    {
                        "user_id": str(owner["id"]),
                        "proposal_id": normalized_id,
                        "status": queued.get("status"),
                    },
                    {
                        "$set": {
                            "status": (
                                "failed"
                                if requires_new_preview
                                else queued.get("status")
                            ),
                            "failure": _safe_provider_value(safe_detail),
                            "background_failure_at": _iso(),
                            "execution_retryable": not requires_new_preview,
                            "automatic_retry_allowed": False,
                            "recovery_action": (
                                "create_new_preview"
                                if requires_new_preview
                                else "retry_execution_after_resolving_preflight"
                            ),
                            "provider_write_reached": False,
                            "provider_write_state": "not_attempted",
                            "provider_write_uncertain": False,
                        }
                    },
                )

        background_tasks.add_task(execute_in_background)
        return {
            "provider": SNAPCHAT_PROVIDER_ID,
            "proposal_id": normalized_id,
            "status": "executing",
        }

    @router.post(
        f"/{SNAPCHAT_PROVIDER_ID}/management/proposals/{{proposal_id}}/reconcile"
    )
    async def reconcile_proposal(
        proposal_id: str,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        owner = require_owner(user)
        return await reconcile_snapchat_management_proposal(
            db,
            str(owner["id"]),
            str(owner["id"]),
            _identifier(proposal_id, field="proposal_id"),
        )

    @router.post(
        f"/{SNAPCHAT_PROVIDER_ID}/management/proposals/{{proposal_id}}/rollback"
    )
    async def rollback_proposal(
        proposal_id: str,
        payload: SnapchatManagementRollbackInput,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        owner = require_owner(user)
        return await rollback_snapchat_management_proposal(
            db,
            str(owner["id"]),
            str(owner["id"]),
            _identifier(proposal_id, field="proposal_id"),
            payload,
        )


__all__ = [
    "SnapchatManagementApprovalInput",
    "SnapchatManagementProposalInput",
    "SnapchatManagementRollbackInput",
    "SnapchatManagementProvider",
    "attach_snapchat_campaign_management_routes",
    "build_snapchat_operation",
    "create_snapchat_management_proposal",
    "execute_snapchat_management_proposal",
    "reconcile_snapchat_management_proposal",
    "rollback_snapchat_management_proposal",
    "snapchat_campaign_activation_enabled",
    "snapchat_campaign_mutations_enabled",
    "snapchat_management_readiness",
    "snapchat_management_request_fingerprint",
]
