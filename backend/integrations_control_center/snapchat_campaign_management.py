"""Governed Snapchat campaign mutations for Mezan Ads Manager.

The reporting plane remains read-only.  This module owns a separate owner-only
control plane with a strict proposal -> preview -> approval -> execution ->
verification -> audit -> rollback lifecycle.  New delivery entities are always
created PAUSED; activation has an independent runtime kill switch.
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .snapchat_account_selection import _load_selected_accounts
from .snapchat_native_data_common import (
    SNAPCHAT_API_BASE,
    SNAPCHAT_PROVIDER_ID,
    SnapchatSyncContext,
    _collection,
    _safe_provider_error_detail,
)
from .snapchat_native_entities_sync import _safe_provider_value, _upsert_entity


PROPOSAL_COLLECTION = "mezan_snapchat_campaign_proposals_v1"
AUDIT_COLLECTION = "mezan_snapchat_campaign_audit_v1"
SOURCE_MODE = "snapchat_campaign_management_v1"
PROPOSAL_TTL = timedelta(minutes=30)
MUTATIONS_ENABLED_ENV = "MEZAN_SNAPCHAT_CAMPAIGN_MUTATIONS_ENABLED"
ACTIVATION_ENABLED_ENV = "MEZAN_SNAPCHAT_CAMPAIGN_ACTIVATION_ENABLED"
MAX_DAILY_BUDGET_ENV = "MEZAN_SNAPCHAT_MAX_DAILY_BUDGET_MICRO"
DEFAULT_MAX_DAILY_BUDGET_MICRO = 5_000_000_000

Action = Literal[
    "campaign.create",
    "campaign.update",
    "ad_squad.create",
    "ad_squad.update",
    "ad.create",
    "ad.update",
    "creative.create",
]

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
    "name", "start_time", "end_time", "daily_budget_micro",
    "lifetime_spend_cap_micro", "objective", "objective_v2_properties",
    "measurement_spec", "regulations", "buy_model", "pacing_level",
    "shared_properties", "product_properties",
}
CAMPAIGN_UPDATE_FIELDS = CAMPAIGN_CREATE_FIELDS | {"status"}
AD_SQUAD_CREATE_FIELDS = {
    "name", "type", "targeting", "placement_v2", "billing_event",
    "bid_micro", "bid_strategy", "daily_budget_micro",
    "lifetime_budget_micro", "optimization_goal", "conversion_window",
    "pixel_id", "start_time", "end_time", "pacing_type",
    "brand_safety_config", "cap_and_exclusion_config",
    "ad_scheduling_config", "campaign_budget_optimization_properties",
    "child_ad_type", "forced_view_setting", "event_sources",
}
AD_SQUAD_UPDATE_FIELDS = AD_SQUAD_CREATE_FIELDS | {"status"}
AD_CREATE_FIELDS = {"name", "creative_id", "type"}
AD_UPDATE_FIELDS = {
    "name", "status", "third_party_on_swipe_tracking_urls",
    "third_party_paid_impression_tracking_urls",
}
CREATIVE_CREATE_FIELDS = {
    "name", "type", "headline", "call_to_action", "top_snap_media_id",
    "top_snap_crop_position", "web_view_properties", "profile_properties",
    "shareable", "forced_view_eligibility", "ad_product",
    "cta_color_display_mode", "preview_properties", "collection_properties",
    "app_install_properties", "deep_link_properties", "render_type",
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


def _enabled(name: str, default: str = "false") -> bool:
    return str(os.environ.get(name, default)).strip().lower() in {
        "1", "true", "yes", "on", "enabled",
    }


def snapchat_campaign_mutations_enabled() -> bool:
    return _enabled(MUTATIONS_ENABLED_ENV)


def snapchat_campaign_activation_enabled() -> bool:
    return _enabled(ACTIVATION_ENABLED_ENV)


def _max_daily_budget_micro() -> int:
    try:
        return max(5_000_000, int(os.environ.get(
            MAX_DAILY_BUDGET_ENV,
            str(DEFAULT_MAX_DAILY_BUDGET_MICRO),
        )))
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
    if any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for char in normalized):
        raise ValueError(f"{field} is invalid")
    return normalized


def _bounded_payload(payload: dict[str, Any]) -> dict[str, Any]:
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str)
    if len(encoded.encode("utf-8")) > 64_000:
        raise ValueError("payload is too large")
    lowered = encoded.lower()
    if any(fragment in lowered for fragment in (
        "access_token", "refresh_token", "authorization", "client_secret",
        "password", "credential", "ciphertext",
    )):
        raise ValueError("payload contains a forbidden sensitive field")
    safe = _safe_provider_value(payload)
    if not isinstance(safe, dict):
        raise ValueError("payload is invalid")
    return safe


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

    @field_validator("idempotency_key")
    @classmethod
    def normalize_idempotency_key(cls, value: str) -> str:
        return _identifier(value, field="idempotency_key")

    @field_validator("payload")
    @classmethod
    def normalize_payload(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _bounded_payload(value)

    @model_validator(mode="after")
    def validate_relationships(self) -> "SnapchatManagementProposalInput":
        if self.action in UPDATE_ACTIONS and not self.target_id:
            raise ValueError("target_id is required for update actions")
        if self.action in {"ad_squad.create", "ad_squad.update", "ad.create", "ad.update"} and not self.parent_id:
            raise ValueError("parent_id is required for this action")
        if self.action in DELIVERY_CREATE_ACTIONS and str(self.payload.get("status") or "PAUSED").upper() == "ACTIVE":
            raise ValueError("new delivery entities must be created PAUSED")
        if str(self.payload.get("status") or "").upper() == "ACTIVE" and not self.activation_acknowledged:
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


def build_snapchat_operation(payload: SnapchatManagementProposalInput) -> dict[str, Any]:
    """Build one fixed provider request without accepting a caller URL/method."""
    action = payload.action
    entity = dict(payload.payload)
    account_id = payload.account_id
    target_id = payload.target_id
    parent_id = payload.parent_id
    operation: dict[str, Any]

    if action == "campaign.create":
        entity = _allow_only(
            entity, CAMPAIGN_CREATE_FIELDS, allow_forced_status=True
        )
        entity["name"] = _text(entity.get("name"), field="name")
        entity["start_time"] = _text(entity.get("start_time"), field="start_time", maximum=80)
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
            plural="campaigns", singular="campaign", entity_type="campaign",
            changes=entity,
        )
    elif action == "ad_squad.create":
        entity = _allow_only(
            entity, AD_SQUAD_CREATE_FIELDS, allow_forced_status=True
        )
        entity["name"] = _text(entity.get("name"), field="name")
        for required in ("type", "targeting", "placement_v2", "billing_event", "bid_strategy", "optimization_goal"):
            if entity.get(required) in (None, "", {}, []):
                raise ValueError(f"{required} is required")
        if not any(key in entity for key in ("daily_budget_micro", "lifetime_budget_micro")):
            raise ValueError("daily_budget_micro or lifetime_budget_micro is required")
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
            plural="adsquads", singular="adsquad", entity_type="ad_squad",
            changes=entity,
        )
    elif action == "ad.create":
        entity = _allow_only(
            entity, AD_CREATE_FIELDS, allow_forced_status=True
        )
        entity["name"] = _text(entity.get("name"), field="name")
        entity["creative_id"] = _identifier(entity.get("creative_id"), field="creative_id")
        entity["type"] = _text(entity.get("type"), field="type", maximum=80).upper()
        entity["ad_squad_id"] = parent_id
        entity["status"] = "PAUSED"
        operation = {
            "method": "POST",
            "path": f"/adsquads/{parent_id}/ads",
            "plural": "ads", "singular": "ad", "entity_type": "ad",
            "body": {"ads": [entity]},
        }
    elif action == "ad.update":
        entity = _allow_only(entity, AD_UPDATE_FIELDS)
        operation = _patch_operation(
            path=f"/adsquads/{parent_id}/ads/{target_id}",
            plural="ads", singular="ad", entity_type="ad", changes=entity,
        )
    elif action == "creative.create":
        entity = _allow_only(entity, CREATIVE_CREATE_FIELDS)
        for required in ("name", "type", "headline", "top_snap_media_id"):
            if not entity.get(required):
                raise ValueError(f"{required} is required")
        entity["name"] = _text(entity["name"], field="name")
        entity["headline"] = _text(entity["headline"], field="headline", maximum=34)
        entity["type"] = _text(
            entity["type"], field="type", maximum=80
        ).upper()
        entity["top_snap_media_id"] = _identifier(
            entity["top_snap_media_id"], field="top_snap_media_id"
        )
        profile = entity.get("profile_properties")
        if not isinstance(profile, dict) or not profile.get("profile_id"):
            raise ValueError("profile_properties.profile_id is required")
        profile["profile_id"] = _identifier(
            profile["profile_id"], field="profile_id"
        )
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
            "plural": "creatives", "singular": "creative",
            "entity_type": "creative", "body": {"creatives": [entity]},
        }
    else:  # pragma: no cover - Literal and Pydantic reject this first
        raise ValueError("unsupported action")

    _budget_guard(entity)
    operation["account_id"] = account_id
    operation["action"] = action
    operation["target_id"] = target_id
    operation["parent_id"] = parent_id
    operation["activates_delivery"] = str(entity.get("status") or "").upper() == "ACTIVE"
    operation["summary"] = _operation_summary(action, entity, target_id)
    return operation


def _patch_operation(*, path: str, plural: str, singular: str,
                     entity_type: str, changes: dict[str, Any]) -> dict[str, Any]:
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
        "method": "PATCH", "path": path, "plural": plural,
        "singular": singular, "entity_type": entity_type,
        "body": patches, "changes": changes,
    }


def _operation_summary(action: str, entity: dict[str, Any], target_id: str | None) -> dict[str, Any]:
    return {
        "action": action,
        "target_id": target_id,
        "name": entity.get("name"),
        "status": entity.get("status"),
        "daily_budget_micro": entity.get("daily_budget_micro"),
        "lifetime_budget_micro": entity.get("lifetime_budget_micro"),
        "lifetime_spend_cap_micro": entity.get("lifetime_spend_cap_micro"),
        "changed_fields": sorted(entity),
    }


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
        "campaign.create": ("POST", f"/adaccounts/{account_id}/campaigns", "campaign", "campaigns", "campaign"),
        "campaign.update": ("PATCH", f"/adaccounts/{account_id}/campaigns/{target_id}", "campaign", "campaigns", "campaign"),
        "ad_squad.create": ("POST", f"/campaigns/{parent_id}/adsquads", "ad_squad", "adsquads", "adsquad"),
        "ad_squad.update": ("PATCH", f"/campaigns/{parent_id}/adsquads/{target_id}", "ad_squad", "adsquads", "adsquad"),
        "ad.create": ("POST", f"/adsquads/{parent_id}/ads", "ad", "ads", "ad"),
        "ad.update": ("PATCH", f"/adsquads/{parent_id}/ads/{target_id}", "ad", "ads", "ad"),
        "creative.create": ("POST", f"/adaccounts/{account_id}/creatives", "creative", "creatives", "creative"),
    }.get(action)
    if not expected:
        raise ValueError("stored action is invalid")
    actual = (
        operation.get("method"), operation.get("path"),
        operation.get("entity_type"), operation.get("plural"),
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
        if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
            raise ValueError("stored create operation is invalid")
        entity = rows[0]
        if action in DELIVERY_CREATE_ACTIONS and str(
            entity.get("status") or ""
        ).upper() != "PAUSED":
            raise ValueError("stored create operation is not PAUSED")
        if action in {"campaign.create", "creative.create"} and str(
            entity.get("ad_account_id") or ""
        ) != account_id:
            raise ValueError("stored entity account does not match the proposal")
        if action == "ad_squad.create" and str(
            entity.get("campaign_id") or ""
        ) != str(parent_id or ""):
            raise ValueError("stored entity parent does not match the proposal")
        if action == "ad.create" and str(
            entity.get("ad_squad_id") or ""
        ) != str(parent_id or ""):
            raise ValueError("stored entity parent does not match the proposal")
    _budget_guard(entity)
    activates_delivery = str(entity.get("status") or "").upper() == "ACTIVE"
    if operation.get("activates_delivery") is not activates_delivery:
        raise ValueError("stored activation flag does not match the proposal")


class SnapchatManagementProvider:
    def __init__(self, db: Any, user_id: str) -> None:
        self.db = db
        self.user_id = user_id
        self.context = SnapchatSyncContext(db=db, user_id=user_id)
        self._management_roles_cache: dict[str, tuple[set[str], set[str]]] = {}

    async def _request(self, method: str, path: str, *, body: Any = None,
                       content_type: str = "application/json") -> dict[str, Any]:
        token = await self.context.access_token()
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
                )
        except httpx.HTTPError as exc:
            raise HTTPException(
                status_code=502,
                detail={"code": "snapchat_management_network_error", "message": "تعذر الاتصال بـ Snapchat أثناء تنفيذ العملية."},
            ) from exc
        if response.status_code in {401, 403}:
            raise HTTPException(
                status_code=409,
                detail={"code": "snapchat_management_needs_reauth_or_role", "message": "يحتاج اتصال Snapchat إلى إعادة توثيق أو دور إدارة مناسب."},
            )
        if response.status_code >= 400:
            try:
                provider_payload = response.json() or {}
            except (TypeError, ValueError):
                provider_payload = {}
            safe_detail = _safe_provider_error_detail(provider_payload)
            raise HTTPException(
                status_code=502,
                detail={
                    "code": f"snapchat_management_http_{response.status_code}",
                    "message": "رفض Snapchat العملية المقترحة.",
                    **safe_detail,
                },
            )
        try:
            payload = response.json() or {}
        except (TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=502,
                detail={"code": "snapchat_management_invalid_json", "message": "أعاد Snapchat استجابة غير صالحة."},
            ) from exc
        if not isinstance(payload, dict):
            raise HTTPException(status_code=502, detail={"code": "snapchat_management_invalid_payload"})
        request_status = str(payload.get("request_status") or payload.get("status") or "SUCCESS").upper()
        if "FAIL" in request_status or "ERROR" in request_status:
            raise HTTPException(status_code=502, detail={"code": "snapchat_management_request_failed", "message": "أبلغ Snapchat عن فشل العملية."})
        return payload

    async def read_entity(self, entity_type: str, entity_id: str) -> dict[str, Any]:
        path_kind = {"campaign": "campaigns", "ad_squad": "adsquads", "ad": "ads", "creative": "creatives"}[entity_type]
        payload = await self._request("GET", f"/{path_kind}/{entity_id}")
        return _extract_entity(payload, path_kind, "adsquad" if entity_type == "ad_squad" else entity_type)

    async def execute(self, operation: dict[str, Any]) -> dict[str, Any]:
        content_type = "application/json-patch+json" if operation["method"] == "PATCH" else "application/json"
        payload = await self._request(
            operation["method"], operation["path"],
            body=operation["body"], content_type=content_type,
        )
        return _extract_entity(payload, operation["plural"], operation["singular"])

    async def management_role(self, account: dict[str, Any], action: str) -> dict[str, Any]:
        organization_id = str(account.get("organization_id") or "").strip()
        account_id = str(account.get("ad_account_id") or "").strip()
        cached = self._management_roles_cache.get(account_id)
        if cached is None:
            organizations = await self._request(
                "GET", "/me/organizations?with_ad_accounts=true"
            )
            member_id = ""
            for wrapped in organizations.get("organizations") or []:
                org = wrapped.get("organization", wrapped) if isinstance(wrapped, dict) else {}
                if str(org.get("id") or "") == organization_id:
                    member_id = str(org.get("my_member_id") or "").strip()
                    break
            if not member_id:
                return {"allowed": False, "role": None, "reason": "member_identity_missing"}
            roles_payload = await self._request(
                "GET", f"/members/{member_id}/roles?limit=200"
            )
            account_roles: set[str] = set()
            organization_roles: set[str] = set()
            for wrapped in roles_payload.get("roles") or []:
                role = wrapped.get("role", wrapped) if isinstance(wrapped, dict) else {}
                role_type = str(role.get("type") or "").lower()
                if str(role.get("ad_account_id") or "") == account_id:
                    account_roles.add(role_type)
                if str(role.get("organization_id") or "") == organization_id:
                    organization_roles.add(role_type)
            self._management_roles_cache[account_id] = (
                account_roles, organization_roles
            )
        else:
            account_roles, organization_roles = cached
        allowed_roles = {"admin", "general"}
        if action in CREATIVE_ACTIONS:
            allowed_roles.add("creative")
        allowed = bool(account_roles & allowed_roles or "admin" in organization_roles)
        visible_role = sorted(account_roles & allowed_roles or organization_roles & {"admin"})
        return {
            "allowed": allowed,
            "role": visible_role[0] if visible_role else None,
            "reason": None if allowed else "snapchat_management_role_missing",
        }


def _extract_entity(payload: dict[str, Any], plural: str, singular: str) -> dict[str, Any]:
    rows = payload.get(plural) or []
    if not isinstance(rows, list) or not rows:
        raise HTTPException(status_code=502, detail={"code": "snapchat_management_entity_missing", "message": "لم يعد Snapchat الكيان المتوقع."})
    wrapper = rows[0] if isinstance(rows[0], dict) else {}
    sub_status = str(wrapper.get("sub_request_status") or wrapper.get("status") or "SUCCESS").upper()
    if "FAIL" in sub_status or "ERROR" in sub_status:
        raise HTTPException(status_code=502, detail={"code": "snapchat_management_subrequest_failed", "message": "رفض Snapchat الكيان داخل العملية."})
    entity = wrapper.get(singular, wrapper)
    if not isinstance(entity, dict) or not entity.get("id"):
        raise HTTPException(status_code=502, detail={"code": "snapchat_management_entity_invalid"})
    safe = _safe_provider_value(entity)
    return safe if isinstance(safe, dict) else {}


async def ensure_snapchat_management_indexes(db: Any) -> None:
    proposals = _collection(db, PROPOSAL_COLLECTION)
    audit = _collection(db, AUDIT_COLLECTION)
    await proposals.create_index([("user_id", 1), ("proposal_id", 1)], unique=True, name="snap_management_proposal_unique")
    await proposals.create_index([("user_id", 1), ("idempotency_key", 1)], unique=True, name="snap_management_idempotency_unique")
    await proposals.create_index([("user_id", 1), ("status", 1), ("created_at", -1)], name="snap_management_status_latest")
    await audit.create_index([("user_id", 1), ("proposal_id", 1), ("occurred_at", 1)], name="snap_management_audit_timeline")


async def _selected_account(db: Any, user_id: str, account_id: str) -> dict[str, Any]:
    accounts = await _load_selected_accounts(db, user_id)
    for account in accounts:
        if str(account.get("ad_account_id") or "") == account_id:
            return account
    raise HTTPException(status_code=409, detail={"code": "snapchat_management_account_not_selected", "message": "اختر حساب Snapchat داخل ميزان قبل إدارته."})


async def _audit(db: Any, *, user_id: str, proposal_id: str, event: str,
                 actor_id: str, detail: dict[str, Any] | None = None) -> None:
    await _collection(db, AUDIT_COLLECTION).insert_one({
        "audit_id": str(uuid.uuid4()), "user_id": user_id,
        "proposal_id": proposal_id, "event": event, "actor_id": actor_id,
        "detail": _safe_provider_value(detail or {}), "occurred_at": _iso(),
        "source_mode": SOURCE_MODE,
    })


def _public_proposal(row: dict[str, Any], *, confirm_token: str | None = None) -> dict[str, Any]:
    operation = dict(row.get("operation") or {})
    output = {
        "proposal_id": row.get("proposal_id"), "status": row.get("status"),
        "revision": int(row.get("revision") or 1), "action": row.get("action"),
        "account_id": row.get("account_id"), "target_id": row.get("target_id"),
        "parent_id": row.get("parent_id"), "reason": row.get("reason"),
        "preview": operation.get("summary") or {},
        "creates_paused": row.get("action") in DELIVERY_CREATE_ACTIONS,
        "activates_delivery": operation.get("activates_delivery") is True,
        "expires_at": row.get("expires_at"), "created_at": row.get("created_at"),
        "approved_at": row.get("approved_at"), "executed_at": row.get("executed_at"),
        "verification": row.get("verification"), "rollback": row.get("rollback"),
        "confirmation_phrase": f"تراجع {str(row.get('proposal_id') or '')[:8]}",
        "provider_write_reached": row.get("provider_write_reached") is True,
        "provider_write_state": row.get("provider_write_state") or "not_attempted",
        "provider_write_uncertain": row.get("provider_write_uncertain") is True,
        "accounting_write_reached": False, "qoyod_write_reached": False,
    }
    if confirm_token:
        output["confirm_token"] = confirm_token
    return output


async def create_snapchat_management_proposal(db: Any, user_id: str,
                                              actor_id: str,
                                              payload: SnapchatManagementProposalInput,
                                              *, provider: SnapchatManagementProvider | None = None) -> dict[str, Any]:
    await ensure_snapchat_management_indexes(db)
    existing = await _collection(db, PROPOSAL_COLLECTION).find_one(
        {"user_id": user_id, "idempotency_key": payload.idempotency_key},
        {"_id": 0},
    )
    if existing:
        if existing.get("status") == "previewed":
            replacement_token = secrets.token_urlsafe(32)
            await _collection(db, PROPOSAL_COLLECTION).update_one(
                {
                    "user_id": user_id,
                    "proposal_id": existing.get("proposal_id"),
                    "status": "previewed",
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
        raise HTTPException(status_code=409, detail={"code": "snapchat_management_role_missing", "message": "الحساب يحتاج دور general أو admin لإدارة الحملات، ودور creative أو admin للإبداع."})
    try:
        operation = build_snapchat_operation(payload)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail={"code": "snapchat_management_payload_invalid", "message": str(exc)}) from exc

    original: dict[str, Any] | None = None
    if payload.action in UPDATE_ACTIONS:
        original = await client.read_entity(operation["entity_type"], payload.target_id or "")
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
        if str(parent.get("ad_account_id") or "") != payload.account_id:
            raise HTTPException(status_code=409, detail={"code": "snapchat_management_parent_account_mismatch"})
    elif payload.action == "ad.create":
        parent = await client.read_entity("ad_squad", payload.parent_id or "")
        campaign = await client.read_entity("campaign", str(parent.get("campaign_id") or ""))
        if str(campaign.get("ad_account_id") or "") != payload.account_id:
            raise HTTPException(status_code=409, detail={"code": "snapchat_management_parent_account_mismatch"})

    proposal_id = str(uuid.uuid4())
    token = secrets.token_urlsafe(32)
    now = _utcnow()
    row = {
        "proposal_id": proposal_id, "user_id": user_id,
        "actor_id": actor_id, "action": payload.action,
        "account_id": payload.account_id, "target_id": payload.target_id,
        "parent_id": payload.parent_id, "reason": payload.reason,
        "idempotency_key": payload.idempotency_key, "status": "previewed",
        "revision": 1, "operation": operation, "original_snapshot": original,
        "role": role.get("role"), "confirm_token_hash": hashlib.sha256(token.encode()).hexdigest(),
        "created_at": _iso(now), "expires_at": _iso(now + PROPOSAL_TTL),
        "provider_write_reached": False,
        "provider_write_state": "not_attempted",
        "provider_write_uncertain": False,
        "accounting_write_reached": False,
        "qoyod_write_reached": False, "source_mode": SOURCE_MODE,
    }
    await _collection(db, PROPOSAL_COLLECTION).insert_one(row)
    await _audit(db, user_id=user_id, proposal_id=proposal_id, event="previewed", actor_id=actor_id, detail={"action": payload.action, "role": role.get("role")})
    return _public_proposal(row, confirm_token=token)


async def approve_snapchat_management_proposal(db: Any, user_id: str,
                                               actor_id: str, proposal_id: str,
                                               payload: SnapchatManagementApprovalInput) -> dict[str, Any]:
    row = await _collection(db, PROPOSAL_COLLECTION).find_one(
        {"user_id": user_id, "proposal_id": proposal_id}, {"_id": 0}
    )
    if not row:
        raise HTTPException(status_code=404, detail={"code": "snapchat_management_proposal_not_found"})
    if row.get("status") != "previewed" or int(row.get("revision") or 0) != payload.expected_revision:
        raise HTTPException(status_code=409, detail={"code": "snapchat_management_proposal_not_approvable"})
    if _expired(row.get("expires_at")):
        raise HTTPException(status_code=409, detail={"code": "snapchat_management_proposal_expired"})
    token_hash = hashlib.sha256(payload.confirm_token.encode()).hexdigest()
    if not secrets.compare_digest(token_hash, str(row.get("confirm_token_hash") or "")):
        raise HTTPException(status_code=409, detail={"code": "snapchat_management_confirm_token_mismatch"})
    now_iso = _iso()
    result = await _collection(db, PROPOSAL_COLLECTION).update_one(
        {"user_id": user_id, "proposal_id": proposal_id, "status": "previewed", "revision": payload.expected_revision},
        {"$set": {"status": "approved", "approved_at": now_iso, "approved_by": actor_id, "confirm_token_hash": None}, "$inc": {"revision": 1}},
    )
    if not getattr(result, "matched_count", 1):
        raise HTTPException(status_code=409, detail={"code": "snapchat_management_approval_race"})
    await _audit(db, user_id=user_id, proposal_id=proposal_id, event="approved", actor_id=actor_id)
    updated = await _collection(db, PROPOSAL_COLLECTION).find_one({"user_id": user_id, "proposal_id": proposal_id}, {"_id": 0})
    return _public_proposal(updated or row)


def _changed_values_match(expected: dict[str, Any], actual: dict[str, Any]) -> tuple[bool, list[str]]:
    mismatches: list[str] = []
    for key, value in expected.items():
        if actual.get(key) != value:
            mismatches.append(key)
    return not mismatches, mismatches


async def execute_snapchat_management_proposal(db: Any, user_id: str,
                                               actor_id: str, proposal_id: str,
                                               *, provider: SnapchatManagementProvider | None = None) -> dict[str, Any]:
    if not snapchat_campaign_mutations_enabled():
        raise HTTPException(status_code=409, detail={"code": "snapchat_campaign_mutations_disabled", "message": "تشغيل كتابة حملات Snapchat متوقف بمفتاح الأمان."})
    row = await _collection(db, PROPOSAL_COLLECTION).find_one({"user_id": user_id, "proposal_id": proposal_id}, {"_id": 0})
    if not row:
        raise HTTPException(status_code=404, detail={"code": "snapchat_management_proposal_not_found"})
    if row.get("status") == "completed":
        return _public_proposal(row)
    if row.get("status") != "approved":
        raise HTTPException(status_code=409, detail={"code": "snapchat_management_proposal_not_executable"})
    if _expired(row.get("expires_at")):
        raise HTTPException(
            status_code=409,
            detail={"code": "snapchat_management_proposal_expired"},
        )
    operation = dict(row.get("operation") or {})
    try:
        _assert_stored_operation_integrity(row, operation)
    except ValueError as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": "snapchat_management_operation_integrity_failed"},
        ) from exc
    if operation.get("activates_delivery") and not snapchat_campaign_activation_enabled():
        raise HTTPException(status_code=409, detail={"code": "snapchat_campaign_activation_disabled", "message": "تشغيل الحملات متوقف بمفتاح أمان مستقل؛ يمكن الإنشاء والتعديل والإيقاف فقط."})
    account = await _selected_account(db, user_id, str(row.get("account_id") or ""))
    client = provider or SnapchatManagementProvider(db, user_id)
    role = await client.management_role(account, str(row.get("action") or ""))
    if not role.get("allowed"):
        raise HTTPException(status_code=409, detail={"code": "snapchat_management_role_missing"})
    locked = await _collection(db, PROPOSAL_COLLECTION).update_one(
        {"user_id": user_id, "proposal_id": proposal_id, "status": "approved"},
        {"$set": {
            "status": "executing",
            "execution_started_at": _iso(),
            "provider_write_state": "attempting",
        }},
    )
    if not getattr(locked, "matched_count", 1):
        raise HTTPException(status_code=409, detail={"code": "snapchat_management_execution_race"})
    provider_write_confirmed = False
    provider_entity_id = ""
    try:
        provider_entity = await client.execute(operation)
        provider_write_confirmed = True
        provider_entity_id = str(
            provider_entity.get("id") or row.get("target_id") or ""
        )
        await _collection(db, PROPOSAL_COLLECTION).update_one(
            {"user_id": user_id, "proposal_id": proposal_id, "status": "executing"},
            {"$set": {
                "provider_write_reached": True,
                "provider_write_state": "confirmed",
                "provider_write_uncertain": False,
                "provider_entity_id": provider_entity_id,
            }},
        )
        verified = await client.read_entity(
            operation["entity_type"], provider_entity_id
        )
        expected = (
            operation.get("changes") or
            {key: value for key, value in (operation.get("body", {}).get(operation["plural"], [{}])[0]).items() if key in {"name", "status", "daily_budget_micro", "lifetime_budget_micro", "creative_id", "type"}}
        )
        verified_ok, mismatches = _changed_values_match(expected, verified)
        if not verified_ok:
            raise HTTPException(status_code=502, detail={"code": "snapchat_management_verification_failed", "mismatched_fields": mismatches})
        await _upsert_entity(
            SnapchatSyncContext(db=db, user_id=user_id), account=account,
            entity_type=operation["entity_type"], entity=verified,
        )
        verification = {
            "verified": True, "entity_id": provider_entity_id,
            "status": verified.get("status"), "verified_at": _iso(),
            "provider_snapshot": verified,
        }
        now_iso = _iso()
        await _collection(db, PROPOSAL_COLLECTION).update_one(
            {"user_id": user_id, "proposal_id": proposal_id, "status": "executing"},
            {"$set": {"status": "completed", "executed_at": now_iso, "executed_by": actor_id, "provider_write_reached": True, "provider_write_state": "confirmed", "provider_write_uncertain": False, "provider_entity_id": provider_entity_id, "verification": verification}},
        )
        await _audit(db, user_id=user_id, proposal_id=proposal_id, event="executed_and_verified", actor_id=actor_id, detail={"entity_id": provider_entity_id, "status": verified.get("status")})
    except Exception as exc:
        detail = exc.detail if isinstance(exc, HTTPException) else {"code": "snapchat_management_execution_failed"}
        write_state = "confirmed" if provider_write_confirmed else "unknown_after_error"
        await _collection(db, PROPOSAL_COLLECTION).update_one(
            {"user_id": user_id, "proposal_id": proposal_id, "status": "executing"},
            {"$set": {
                "status": "failed",
                "failed_at": _iso(),
                "failure": _safe_provider_value(detail),
                "provider_write_reached": provider_write_confirmed,
                "provider_write_state": write_state,
                "provider_write_uncertain": not provider_write_confirmed,
                **({"provider_entity_id": provider_entity_id} if provider_entity_id else {}),
            }},
        )
        await _audit(
            db, user_id=user_id, proposal_id=proposal_id,
            event="execution_failed", actor_id=actor_id,
            detail={
                **(detail if isinstance(detail, dict) else {}),
                "provider_write_state": write_state,
                "provider_entity_id": provider_entity_id or None,
            },
        )
        raise
    updated = await _collection(db, PROPOSAL_COLLECTION).find_one({"user_id": user_id, "proposal_id": proposal_id}, {"_id": 0})
    return _public_proposal(updated or row)


async def rollback_snapchat_management_proposal(db: Any, user_id: str,
                                                actor_id: str, proposal_id: str,
                                                payload: SnapchatManagementRollbackInput,
                                                *, provider: SnapchatManagementProvider | None = None) -> dict[str, Any]:
    if not snapchat_campaign_mutations_enabled():
        raise HTTPException(status_code=409, detail={"code": "snapchat_campaign_mutations_disabled"})
    row = await _collection(db, PROPOSAL_COLLECTION).find_one({"user_id": user_id, "proposal_id": proposal_id}, {"_id": 0})
    rollback_from_status = str(row.get("status") or "") if row else ""
    recovery_after_failed_verification = (
        rollback_from_status == "failed"
        and row.get("provider_write_reached") is True
        and bool(row.get("provider_entity_id"))
    ) if row else False
    if not row or (
        rollback_from_status != "completed"
        and not recovery_after_failed_verification
    ):
        raise HTTPException(status_code=409, detail={"code": "snapchat_management_rollback_not_available"})
    expected_phrase = f"تراجع {proposal_id[:8]}"
    if payload.confirmation_phrase.strip() != expected_phrase:
        raise HTTPException(status_code=409, detail={"code": "snapchat_management_rollback_phrase_mismatch", "expected_phrase": expected_phrase})
    operation = dict(row.get("operation") or {})
    try:
        _assert_stored_operation_integrity(row, operation)
    except ValueError as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": "snapchat_management_operation_integrity_failed"},
        ) from exc
    client = provider or SnapchatManagementProvider(db, user_id)
    account = await _selected_account(
        db, user_id, str(row.get("account_id") or "")
    )
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
        {"$set": {
            "status": "rolling_back",
            "rollback_started_at": _iso(),
            "rollback_started_by": actor_id,
        }},
    )
    if not getattr(locked, "matched_count", 1):
        raise HTTPException(
            status_code=409,
            detail={"code": "snapchat_management_rollback_race"},
        )
    entity_id = str(row.get("provider_entity_id") or row.get("target_id") or "")
    try:
        current = await client.read_entity(operation["entity_type"], entity_id)
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
        if str(restore.get("status") or "").upper() == "ACTIVE" and not snapchat_campaign_activation_enabled():
            raise HTTPException(
                status_code=409,
                detail={"code": "snapchat_campaign_activation_disabled"},
            )
        if not restore and not removed_fields:
            rollback = {"status": "neutralized", "detail": "Created creative remains unused and cannot spend independently.", "rolled_back_at": _iso()}
        else:
            rollback_operation = _rollback_patch_operation(
                path=_entity_patch_path(operation["entity_type"], row, entity_id),
                plural=operation["plural"], singular=operation["singular"],
                entity_type=operation["entity_type"], restore=restore,
                removed_fields=removed_fields,
            )
            rollback_operation.update({"action": f"{row.get('action')}.rollback", "account_id": row.get("account_id")})
            await client.execute(rollback_operation)
            verified = await client.read_entity(operation["entity_type"], entity_id)
            ok, mismatches = _rollback_values_match(
                restore, removed_fields, verified
            )
            if not ok:
                raise HTTPException(status_code=502, detail={"code": "snapchat_management_rollback_verification_failed", "mismatched_fields": mismatches})
            await _upsert_entity(SnapchatSyncContext(db=db, user_id=user_id), account=account, entity_type=operation["entity_type"], entity=verified)
            rollback = {"status": "verified", "before": current, "after": verified, "rolled_back_at": _iso(), "reason": payload.reason}
    except Exception as exc:
        failure = exc.detail if isinstance(exc, HTTPException) else {
            "code": "snapchat_management_rollback_failed"
        }
        await _collection(db, PROPOSAL_COLLECTION).update_one(
            {"user_id": user_id, "proposal_id": proposal_id, "status": "rolling_back"},
            {"$set": {
                "status": rollback_from_status,
                "rollback_failure": _safe_provider_value(failure),
                "rollback_failed_at": _iso(),
            }},
        )
        await _audit(
            db, user_id=user_id, proposal_id=proposal_id,
            event="rollback_failed", actor_id=actor_id,
            detail=failure if isinstance(failure, dict) else {},
        )
        raise
    await _collection(db, PROPOSAL_COLLECTION).update_one(
        {"user_id": user_id, "proposal_id": proposal_id, "status": "rolling_back"},
        {"$set": {"status": "rolled_back", "rollback": _safe_provider_value(rollback), "rolled_back_by": actor_id}},
    )
    await _audit(db, user_id=user_id, proposal_id=proposal_id, event="rolled_back", actor_id=actor_id, detail={"reason": payload.reason})
    updated = await _collection(db, PROPOSAL_COLLECTION).find_one({"user_id": user_id, "proposal_id": proposal_id}, {"_id": 0})
    return _public_proposal(updated or row)


def _rollback_patch_operation(*, path: str, plural: str, singular: str,
                              entity_type: str, restore: dict[str, Any],
                              removed_fields: list[str]) -> dict[str, Any]:
    _budget_guard(restore)
    patches = [
        {"op": "replace", "path": f"/{key}", "value": value}
        for key, value in sorted(restore.items())
    ]
    patches.extend(
        {"op": "remove", "path": f"/{key}"}
        for key in sorted(removed_fields)
    )
    if not patches:
        raise ValueError("rollback patch is empty")
    return {
        "method": "PATCH", "path": path, "plural": plural,
        "singular": singular, "entity_type": entity_type,
        "body": patches, "changes": restore,
    }


def _rollback_values_match(restore: dict[str, Any], removed_fields: list[str],
                           actual: dict[str, Any]) -> tuple[bool, list[str]]:
    ok, mismatches = _changed_values_match(restore, actual)
    for key in removed_fields:
        if key in actual and actual.get(key) is not None:
            mismatches.append(key)
    return ok and not mismatches, sorted(set(mismatches))


def _entity_patch_path(entity_type: str, row: dict[str, Any], entity_id: str) -> str:
    if entity_type == "campaign":
        return f"/adaccounts/{row.get('account_id')}/campaigns/{entity_id}"
    if entity_type == "ad_squad":
        return f"/campaigns/{row.get('parent_id')}/adsquads/{entity_id}"
    if entity_type == "ad":
        return f"/adsquads/{row.get('parent_id')}/ads/{entity_id}"
    raise HTTPException(status_code=409, detail={"code": "snapchat_management_rollback_neutralized"})


async def snapchat_management_readiness(db: Any, user_id: str,
                                        *, provider: SnapchatManagementProvider | None = None) -> dict[str, Any]:
    accounts = await _load_selected_accounts(db, user_id)
    client = provider or SnapchatManagementProvider(db, user_id)
    output = []
    for account in accounts:
        role = await client.management_role(account, "campaign.update")
        creative_role = await client.management_role(account, "creative.create")
        output.append({
            "account_id": account.get("ad_account_id"),
            "display_name": account.get("display_name"),
            "currency": account.get("currency"), "timezone": account.get("timezone"),
            "role": role.get("role"), "management_allowed": role.get("allowed") is True,
            "reason": role.get("reason"),
            "creative_role": creative_role.get("role"),
            "creative_allowed": creative_role.get("allowed") is True,
            "creative_reason": creative_role.get("reason"),
        })
    return {
        "provider": SNAPCHAT_PROVIDER_ID,
        "proposal_enabled": True,
        "execution_enabled": snapchat_campaign_mutations_enabled(),
        "activation_enabled": snapchat_campaign_activation_enabled(),
        "max_daily_budget_micro": _max_daily_budget_micro(),
        "accounts": output,
        "required_lifecycle": ["proposal", "preview", "approval", "execution", "verification", "audit", "rollback"],
        "new_entities_status": "PAUSED",
        "salla_permission_dependency": False,
    }


def attach_snapchat_campaign_management_routes(router: APIRouter, db: Any,
                                               current_user: Callable,
                                               require_owner: Callable[[Any], dict]) -> None:
    @router.get(f"/{SNAPCHAT_PROVIDER_ID}/management/readiness")
    async def readiness(user: dict = Depends(current_user)) -> dict[str, Any]:
        owner = require_owner(user)
        return await snapchat_management_readiness(db, str(owner["id"]))

    @router.get(f"/{SNAPCHAT_PROVIDER_ID}/management/proposals")
    async def list_proposals(limit: int = Query(default=50, ge=1, le=100), user: dict = Depends(current_user)) -> dict[str, Any]:
        owner = require_owner(user)
        cursor = _collection(db, PROPOSAL_COLLECTION).find({"user_id": str(owner["id"])}, {"_id": 0, "confirm_token_hash": 0, "original_snapshot": 0, "operation.body": 0}).sort("created_at", -1).limit(limit)
        rows = await cursor.to_list(length=limit)
        return {"provider": SNAPCHAT_PROVIDER_ID, "proposals": [_public_proposal(row) for row in rows]}

    @router.post(f"/{SNAPCHAT_PROVIDER_ID}/management/proposals", status_code=status.HTTP_201_CREATED)
    async def create_proposal(payload: SnapchatManagementProposalInput, user: dict = Depends(current_user)) -> dict[str, Any]:
        owner = require_owner(user)
        return await create_snapchat_management_proposal(db, str(owner["id"]), str(owner["id"]), payload)

    @router.post(f"/{SNAPCHAT_PROVIDER_ID}/management/proposals/{{proposal_id}}/approve")
    async def approve_proposal(proposal_id: str, payload: SnapchatManagementApprovalInput, user: dict = Depends(current_user)) -> dict[str, Any]:
        owner = require_owner(user)
        return await approve_snapchat_management_proposal(db, str(owner["id"]), str(owner["id"]), _identifier(proposal_id, field="proposal_id"), payload)

    @router.post(f"/{SNAPCHAT_PROVIDER_ID}/management/proposals/{{proposal_id}}/execute")
    async def execute_proposal(proposal_id: str, user: dict = Depends(current_user)) -> dict[str, Any]:
        owner = require_owner(user)
        return await execute_snapchat_management_proposal(db, str(owner["id"]), str(owner["id"]), _identifier(proposal_id, field="proposal_id"))

    @router.post(f"/{SNAPCHAT_PROVIDER_ID}/management/proposals/{{proposal_id}}/rollback")
    async def rollback_proposal(proposal_id: str, payload: SnapchatManagementRollbackInput, user: dict = Depends(current_user)) -> dict[str, Any]:
        owner = require_owner(user)
        return await rollback_snapchat_management_proposal(db, str(owner["id"]), str(owner["id"]), _identifier(proposal_id, field="proposal_id"), payload)


__all__ = [
    "SnapchatManagementApprovalInput",
    "SnapchatManagementProposalInput",
    "SnapchatManagementRollbackInput",
    "SnapchatManagementProvider",
    "attach_snapchat_campaign_management_routes",
    "build_snapchat_operation",
    "create_snapchat_management_proposal",
    "execute_snapchat_management_proposal",
    "rollback_snapchat_management_proposal",
    "snapchat_campaign_activation_enabled",
    "snapchat_campaign_mutations_enabled",
    "snapchat_management_readiness",
]
