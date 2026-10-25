"""Durable, owner-approved publication of one paused Snapchat delivery chain.

Management owns provider writes and verification. This module supplies a bounded
parent approval, stable child identities, and server-derived zero-delivery proof.
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import re
import secrets
from typing import Any, Callable
from urllib.parse import parse_qsl, urlsplit

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator

from . import snapchat_campaign_management as management
from . import snapchat_media_upload as media
from .snapchat_native_data_common import _collection, SNAPCHAT_API_BASE

PUBLICATION_COLLECTION = "mezan_snapchat_campaign_publications_v1"
PROOF_CONTRACT = "snapchat_paused_publication_v1"
ACTIONS = ("campaign.create", "ad_squad.create", "creative.create", "ad.create")
APPROVAL_TTL = timedelta(minutes=30)
WORKER_TIMEOUT_SECONDS = 600
WORKER_LEASE_SECONDS = 900
_WORKERS: dict[tuple[int, str, str], asyncio.Task] = {}


def _fail(code: str, status: int = 409) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": "snapchat_publication_" + code})


def _hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
        separators=(",", ":")).encode()).hexdigest()


def _jobs(db: Any) -> Any:
    return _collection(db, PUBLICATION_COLLECTION)


def _server_date(value: Any) -> datetime | None:
    # Motor's default BSON decoder returns trusted UTC datetimes without tzinfo.
    # Owner-entered start_time still uses the strict timezone-aware validator.
    if isinstance(value, datetime):
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
    return media._date(value)


def _active_worker(row: dict[str, Any]) -> bool:
    if not row.get("lease_token"):
        return False
    expiry = _server_date(row.get("lease_expires_at"))
    if expiry is None:
        raise _fail("worker_lease_invalid")
    return expiry > media._now()


class PublicationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    account_id: str
    currency: str
    product_id: str
    product_variant_id: str | None = None
    media_upload_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    profile_id: str
    pixel_id: str
    campaign_name: str = Field(min_length=1, max_length=200)
    group_name: str = Field(min_length=1, max_length=200)
    creative_name: str = Field(min_length=1, max_length=200)
    ad_name: str = Field(min_length=1, max_length=200)
    headline: str = Field(min_length=1, max_length=34)
    destination_url: str = Field(min_length=8, max_length=2048)
    daily_budget_micro: StrictInt
    start_time: str
    country: str = Field(pattern=r"^[a-zA-Z]{2}$")
    conversion_window: str
    reason: str = Field(min_length=5, max_length=500)
    idempotency_key: str = Field(min_length=8, max_length=128)

    @field_validator("account_id", "product_id", "product_variant_id", "profile_id",
        "pixel_id", "idempotency_key")
    @classmethod
    def identifier(cls, value: str | None, info: Any) -> str | None:
        return None if value is None else management._identifier(value, field=info.field_name)

    @field_validator("campaign_name", "group_name", "creative_name", "ad_name", "headline", "reason")
    @classmethod
    def text(cls, value: str) -> str:
        value = value.strip()
        if not value or any(ord(c) < 32 for c in value):
            raise ValueError("invalid text")
        return value

    @field_validator("currency")
    @classmethod
    def currency_value(cls, value: str) -> str:
        value = value.strip().upper()
        if value not in {"USD", "SAR"}:
            raise ValueError("verified USD or SAR account required")
        return value

    @field_validator("destination_url")
    @classmethod
    def secure_url(cls, value: str) -> str:
        parsed = urlsplit(value)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username
                or parsed.password or parsed.fragment or any(ord(c) < 33 for c in value)):
            raise ValueError("an HTTPS destination without credentials or fragment is required")
        return value

    @field_validator("daily_budget_micro")
    @classmethod
    def budget(cls, value: int) -> int:
        management._budget_guard({"daily_budget_micro": value})
        return value

    @field_validator("conversion_window")
    @classmethod
    def window(cls, value: str) -> str:
        if value not in management.PIXEL_CONVERSION_WINDOWS:
            raise ValueError("unsupported conversion window")
        return value

    @field_validator("start_time")
    @classmethod
    def start(cls, value: str) -> str:
        parsed = media._date(value)
        if parsed is None:
            raise ValueError("timezone-aware start time required")
        return parsed.isoformat()

    @field_validator("country")
    @classmethod
    def country_value(cls, value: str) -> str:
        return value.lower()


class PublicationApproval(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirm_token: str = Field(min_length=16, max_length=240)
    expected_revision: StrictInt = Field(ge=1)


async def _load(db: Any, user_id: str, publication_id: str) -> dict[str, Any]:
    if not re.fullmatch(r"[0-9a-f]{64}", str(publication_id)):
        raise _fail("not_found", 404)
    row = await _jobs(db).find_one({"_id": publication_id, "user_id": user_id})
    if not row:
        raise _fail("not_found", 404)
    if _hash(row["plan"]) != row.get("plan_fingerprint"):
        raise _fail("plan_integrity_failed")
    return row


def _public(row: dict[str, Any], token: str | None = None) -> dict[str, Any]:
    result = {key: deepcopy(row.get(key)) for key in (
        "publication_id", "account_id", "status", "revision", "plan", "plan_fingerprint",
        "created_at", "expires_at", "approved_at", "approved_by", "current_step",
        "steps", "failure", "finished_at", "last_event", "review", "events", "approval_revision",
    )}
    result["provider"] = "snapchat_ads"
    result["delivery_status"] = "PAUSED"
    result["activation_performed"] = False
    if token:
        result["confirm_token"] = token
    return result


async def _save(db: Any, row: dict[str, Any], values: dict[str, Any]) -> dict[str, Any]:
    values = {**values, "revision": row["revision"] + 1, "updated_at": media._iso()}
    if values.get("status") == "approved":
        values["approval_revision"] = values["revision"]
    raw_event = values.get("last_event") or {}
    event_name = raw_event.get("event") or values.get("worker_phase") or values.get("status")
    if not event_name:
        event_name = "preview_confirmation_refreshed" if "confirm_token_hash" in values else "state_checkpoint"
    event = {"event": str(event_name)[:100], "actor_id": row["user_id"],
        "at": values["updated_at"], "revision": values["revision"],
        "approval_revision": values.get("approval_revision", row.get("approval_revision"))}
    if isinstance(raw_event.get("step"), int):
        event["step"] = raw_event["step"]
    values["last_event"] = event
    result = await _jobs(db).update_one(
        {"_id": row["_id"], "user_id": row["user_id"], "revision": row["revision"],
         "lease_token": row.get("lease_token")}, {"$set": values,
         "$push": {"events": {"$each": [event], "$slice": -100}}})
    if result.matched_count != 1:
        raise _fail("state_changed")
    return {**row, **values, "events": [*(row.get("events") or []), event][-100:]}


async def _product(db: Any, user_id: str, plan: dict[str, Any]) -> dict[str, Any]:
    from product_v2_routes import PRODUCTS

    cursor = _collection(db, PRODUCTS).find({"user_id": user_id, "$or": [
        {"salla_product_id": plan["product_id"]}, {"mezan_product_id": plan["product_id"]},
    ]}, {"_id": 0}).limit(2)
    rows = await cursor.to_list(length=2)
    if len(rows) != 1:
        raise _fail("product_not_verified")
    product = rows[0]
    if product.get("archived") is True or str(product.get("status") or "").lower() != "active":
        raise _fail("product_inactive")
    variant = plan.get("product_variant_id")
    if variant and sum(str(v.get("id") or v.get("variant_id") or "") == variant
            for v in (product.get("variants") or []) if isinstance(v, dict)) != 1:
        raise _fail("variant_not_verified")
    return product


async def _dependencies(db: Any, user_id: str, plan: dict[str, Any], provider: Any) -> None:
    account = await management._selected_account(db, user_id, plan["account_id"])
    raw = await provider.read_account(plan["account_id"])
    if (raw.get("id") != plan["account_id"] or raw.get("currency") != plan["currency"]
            or str(account.get("currency") or "").upper() != plan["currency"]):
        raise _fail("currency_not_verified")
    await management._verify_native_currency(provider, account, plan["account_id"])
    for action in ("campaign.create", "creative.create"):
        provider._management_roles_cache.pop(plan["account_id"], None)
        if (await provider.management_role(account, action)).get("allowed") is not True:
            raise _fail("role_missing")
    await _product(db, user_id, plan)
    source = await media._load(db, user_id, plan["media_upload_id"])
    if (source["account_id"] != plan["account_id"] or source["status"] != "ready"
            or source.get("provider_media_id") != plan["media_id"]
            or source.get("source_verified") is not True or source.get("provider_readback_verified") is not True):
        raise _fail("media_not_verified")
    await media.verify_creative_media_dependencies(provider, account_id=plan["account_id"],
        operation={"body": {"creatives": [{"top_snap_media_id": plan["media_id"],
            "profile_properties": {"profile_id": plan["profile_id"]}}]}})
    pixels = await provider.list_account_pixels(plan["account_id"])
    if sum(str(p.get("id") or "") == plan["pixel_id"] for p in pixels) != 1:
        raise _fail("pixel_not_verified")
    await provider.pixel_eligibility(account_id=plan["account_id"], pixel_id=plan["pixel_id"],
        optimization_goal="PIXEL_PURCHASE", conversion_window=plan["conversion_window"])


async def preview_publication(db: Any, user_id: str, payload: PublicationInput) -> dict[str, Any]:
    publication_id = _hash([user_id, payload.idempotency_key])
    existing = await _jobs(db).find_one({"_id": publication_id, "user_id": user_id})
    request = payload.model_dump(mode="json")
    if existing:
        if existing.get("request_fingerprint") != _hash(request):
            raise _fail("idempotency_conflict")
        row = await _load(db, user_id, publication_id)
        if row["status"] != "previewed":
            return _public(row)
        if _server_date(row["expires_at"]) is None or _server_date(row["expires_at"]) <= media._now():
            raise _fail("approval_expired")
        token = secrets.token_urlsafe(32)
        row = await _save(db, row, {"confirm_token_hash": _hash(token)})
        return _public(row, token)
    if media._date(payload.start_time) <= media._now():
        raise _fail("start_time_must_be_future")
    source = await media._load(db, user_id, payload.media_upload_id)
    plan = {**request, "media_id": source.get("provider_media_id")}
    product = await _product(db, user_id, plan)
    plan["product_name"] = str(product.get("name") or "")[:300]
    await _dependencies(db, user_id, plan, management.SnapchatManagementProvider(db, user_id))
    token, at = secrets.token_urlsafe(32), media._now()
    document = {"_id": publication_id, "publication_id": publication_id, "user_id": user_id,
        "account_id": payload.account_id, "request_fingerprint": _hash(request),
        "plan": plan, "plan_fingerprint": _hash(plan), "status": "previewed", "revision": 1,
        "current_step": 0, "lease_token": None, "confirm_token_hash": _hash(token),
        "created_at": at.isoformat(), "expires_at": (at + APPROVAL_TTL).isoformat(),
        "approval_revision": None,
        "events": [{"event": "previewed", "actor_id": user_id, "at": at.isoformat(),
            "revision": 1, "approval_revision": None}],
        "steps": [{"action": action, "idempotency_key": "mzpub-" + publication_id[:32] + "-" + str(i),
            "proposal_id": None, "provider_entity_id": None, "status": "pending"} for i, action in enumerate(ACTIONS)],
        "last_event": {"event": "previewed", "actor_id": user_id, "at": at.isoformat()}}
    await _jobs(db).update_one({"_id": publication_id}, {"$setOnInsert": document}, upsert=True)
    row = await _load(db, user_id, publication_id)
    if row["request_fingerprint"] != _hash(request):
        raise _fail("idempotency_conflict")
    # An insertion race returns the winning preview, never a token for another row.
    if row.get("confirm_token_hash") != _hash(token):
        return await preview_publication(db, user_id, payload)
    return _public(row, token)


async def approve_publication(db: Any, user_id: str, publication_id: str,
        payload: PublicationApproval) -> dict[str, Any]:
    row = await _load(db, user_id, publication_id)
    if (row["status"] != "previewed" or row["revision"] != payload.expected_revision
            or _server_date(row["expires_at"]) is None or _server_date(row["expires_at"]) <= media._now()
            or not hmac.compare_digest(str(row.get("confirm_token_hash") or ""), _hash(payload.confirm_token))):
        raise _fail("approval_invalid")
    row = await _save(db, row, {"status": "approved", "approved_by": user_id,
        "approved_at": media._iso(), "confirm_token_hash": None,
        "approved_plan_fingerprint": row["plan_fingerprint"],
        "last_event": {"event": "plan_approved", "actor_id": user_id, "at": media._iso()}})
    return _public(row)


def _assert_grant(row: dict[str, Any], *, leased: bool = True) -> None:
    if (row.get("status") not in {"approved", "interrupted", "running"}
            or row.get("approved_by") != row["user_id"]
            or row.get("approved_plan_fingerprint") != row["plan_fingerprint"]
            or _server_date(row.get("expires_at")) is None
            or _server_date(row["expires_at"]) <= media._now()):
        raise _fail("approval_expired_or_changed")
    if leased and (row["status"] != "running" or not row.get("lease_token")
            or _server_date(row.get("lease_expires_at")) is None
            or _server_date(row["lease_expires_at"]) <= media._now()):
        raise _fail("worker_fence_lost")


async def review_publication(db: Any, user_id: str, publication_id: str) -> dict[str, Any]:
    row = await _load(db, user_id, publication_id)
    if row["current_step"] >= 4 or row["status"] == "completed":
        raise _fail("already_completed")
    if _active_worker(row):
        raise _fail("worker_busy")
    await _dependencies(db, user_id, row["plan"], management.SnapchatManagementProvider(db, user_id))
    token = secrets.token_urlsafe(32)
    row = await _save(db, row, {"status": "previewed", "lease_token": None,
        "approved_by": None, "approved_at": None, "approved_plan_fingerprint": None,
        "approval_revision": None,
        "confirm_token_hash": _hash(token), "expires_at": (media._now() + APPROVAL_TTL).isoformat(),
        "last_event": {"event": "remaining_plan_reviewed", "actor_id": user_id, "at": media._iso()}})
    return _public(row, token)


def _verified_id(row: dict[str, Any], index: int) -> str:
    step = row["steps"][index]
    if step.get("status") != "completed" or not step.get("provider_entity_id"):
        raise _fail("dependency_unverified")
    return management._identifier(step["provider_entity_id"], field="provider_entity_id")


def _request(row: dict[str, Any], index: int) -> dict[str, Any]:
    p, action = row["plan"], ACTIONS[index]
    suffix = " · MZ-" + row["publication_id"][:12] + "-" + str(index)
    name = p[("campaign_name", "group_name", "creative_name", "ad_name")[index]] + suffix
    body, parent = {}, None
    if index == 0:
        body = {"name": name, "start_time": p["start_time"],
            "objective_v2_properties": {"objective_v2_type": "SALES"}}
    elif index == 1:
        parent = _verified_id(row, 0)
        body = {"name": name, "type": "SNAP_ADS", "targeting": {"regulated_content": False,
            "geos": [{"country_code": p["country"]}]}, "placement_v2": {"config": "AUTOMATIC"},
            "billing_event": "IMPRESSION", "bid_strategy": "AUTO_BID", "optimization_goal": "PIXEL_PURCHASE",
            "daily_budget_micro": p["daily_budget_micro"], "pixel_id": p["pixel_id"],
            "conversion_window": p["conversion_window"], "start_time": p["start_time"]}
    elif index == 2:
        body = {"name": name, "type": "WEB_VIEW", "headline": p["headline"], "call_to_action": "SHOP_NOW",
            "top_snap_media_id": p["media_id"], "profile_properties": {"profile_id": p["profile_id"]},
            "web_view_properties": {"url": p["destination_url"]}}
    elif index == 3:
        parent = _verified_id(row, 1)
        body = {"name": name, "creative_id": _verified_id(row, 2), "type": "REMOTE_WEBPAGE"}
    return {"action": action, "account_id": p["account_id"], "parent_id": parent,
        "payload": body, "reason": p["reason"], "idempotency_key": row["steps"][index]["idempotency_key"],
        "products": [{"product_id": p["product_id"], "product_variant_id": p.get("product_variant_id"),
            "product_name": p["product_name"]}], "safety_protocol_version": 2,
        "publication_id": row["publication_id"]}


async def _paused_ancestors(row: dict[str, Any], index: int, provider: Any) -> None:
    if index >= 1:
        campaign = await provider.read_entity("campaign", _verified_id(row, 0))
        if campaign.get("ad_account_id") != row["account_id"] or campaign.get("status") != "PAUSED":
            raise _fail("campaign_no_longer_paused")
        expected = management._operation_expected_values(management.build_snapchat_operation(
            management.SnapchatManagementProposalInput(**_request(row, 0))))
        if not management._changed_values_match(expected, campaign)[0]:
            raise _fail("campaign_controls_changed")
    if index >= 2:
        group = await provider.read_entity("ad_squad", _verified_id(row, 1))
        if group.get("campaign_id") != _verified_id(row, 0) or group.get("status") != "PAUSED":
            raise _fail("group_no_longer_paused")
        expected = management._operation_expected_values(management.build_snapchat_operation(
            management.SnapchatManagementProposalInput(**_request(row, 1))))
        if not management._changed_values_match(expected, group)[0]:
            raise _fail("group_controls_changed")


async def validate_publication_creation_proof(db: Any, user_id: str,
        payload: management.SnapchatManagementProposalInput, operation: dict[str, Any]) -> dict[str, Any]:
    """Called by management before its baseline gate; client references confer no authority."""
    publication_id = getattr(payload, "publication_id", None)
    if not publication_id:
        return {}
    row = await _load(db, user_id, publication_id)
    _assert_grant(row)
    index = row["current_step"]
    if index not in range(4):
        raise _fail("step_invalid")
    expected = management.SnapchatManagementProposalInput(**_request(row, index))
    if payload.model_dump(mode="json") != expected.model_dump(mode="json"):
        raise _fail("child_request_changed")
    if operation != management.build_snapchat_operation(expected):
        raise _fail("child_operation_changed")
    if operation.get("activates_delivery") or (index != 2 and
            management._operation_create_entity(operation).get("status") != "PAUSED"):
        raise _fail("zero_delivery_required")
    provider = management.SnapchatManagementProvider(db, user_id)
    await _dependencies(db, user_id, row["plan"], provider)
    await _paused_ancestors(row, index, provider)
    # Any long provider reads must still be followed by a fresh DB fence.
    fresh = await _load(db, user_id, publication_id)
    _assert_grant(fresh)
    if fresh.get("lease_token") != row.get("lease_token") or fresh["revision"] != row["revision"]:
        raise _fail("worker_fence_lost")
    return {"contract": PROOF_CONTRACT, "publication_id": publication_id,
        "plan_fingerprint": row["plan_fingerprint"], "step": index, "action": ACTIONS[index],
        "approved_by": row["approved_by"], "account_id": row["account_id"],
        "approval_revision": row["approval_revision"],
        "zero_delivery": True, "baseline_available": False}


async def assert_publication_write_fence(db: Any, user_id: str,
        child: dict[str, Any], operation: dict[str, Any]) -> None:
    """Final management callback after its write-token resolution, before POST."""
    publication_id = child.get("publication_id")
    if not publication_id:
        return
    row = await _load(db, user_id, publication_id)
    _assert_grant(row)
    index = row["current_step"]
    if index not in range(4) or row["steps"][index].get("proposal_id") != child.get("proposal_id"):
        raise _fail("child_not_bound")
    expected_input = management.SnapchatManagementProposalInput(**_request(row, index))
    if child.get("request_fingerprint") != management.snapchat_management_request_fingerprint(expected_input):
        raise _fail("child_request_changed")
    proof = child.get("publication_creation_proof") or {}
    if (proof.get("contract") != PROOF_CONTRACT or proof.get("publication_id") != publication_id
            or proof.get("plan_fingerprint") != row["plan_fingerprint"] or proof.get("step") != index):
        raise _fail("creation_proof_missing")
    expected_operation = management.build_snapchat_operation(expected_input)
    if index == 2:
        from first_party_attribution.core import build_tracking_url
        tracked, _ = build_tracking_url(row["plan"]["destination_url"], user_id=user_id,
            provider="snapchat", product_id=row["plan"]["product_id"], account_id=row["account_id"],
            link_id="snap-proposal:" + child["proposal_id"], snapchat_macros=True)
        expected_operation["body"]["creatives"][0]["web_view_properties"]["url"] = tracked
    if operation != expected_operation:
        raise _fail("child_operation_changed")
    if not management.snapchat_campaign_mutations_enabled():
        raise _fail("mutations_disabled")
    provider = management.SnapchatManagementProvider(db, user_id)
    await _dependencies(db, user_id, row["plan"], provider)
    await _paused_ancestors(row, index, provider)
    fresh = await _load(db, user_id, publication_id)
    _assert_grant(fresh)
    if fresh["revision"] != row["revision"] or fresh.get("lease_token") != row.get("lease_token"):
        raise _fail("worker_fence_lost")


def _child_id(child: dict[str, Any]) -> str:
    verification = child.get("verification") or {}
    entity_id = child.get("provider_entity_id")
    if (child.get("status") != "completed" or child.get("provider_write_state") != "confirmed"
            or child.get("provider_write_uncertain") is not False
            or verification.get("verified") is not True or verification.get("entity_id") != entity_id):
        raise _fail("child_not_verified")
    return management._identifier(entity_id, field="provider_entity_id")


async def refresh_publication_child(db: Any, parent: dict[str, Any], child: dict[str, Any],
        index: int) -> dict[str, Any]:
    """Renew only an exactly bound never-sent child; preserve every write marker."""
    expected = management.SnapchatManagementProposalInput(**_request(parent, index))
    await validate_publication_creation_proof(db, parent["user_id"], expected,
        management.build_snapchat_operation(expected))
    if (child.get("publication_id") != parent["publication_id"]
            or child.get("request_fingerprint") != management.snapchat_management_request_fingerprint(expected)
            or child.get("status") not in {"previewed", "previewed_v2", "approved", "approved_v2"}
            or child.get("provider_write_state") != "not_attempted"
            or child.get("provider_write_reached") is not False
            or child.get("provider_write_uncertain") is not False
            or await management._active_proposal_entity_lease(db, user_id=parent["user_id"], proposal_id=child["proposal_id"])):
        raise _fail("child_refresh_unsafe")
    token = secrets.token_urlsafe(32)
    expiry = min(_server_date(parent["expires_at"]), media._now() + APPROVAL_TTL).isoformat()
    result = await _collection(db, management.PROPOSAL_COLLECTION).update_one(
        {"user_id": parent["user_id"], "proposal_id": child["proposal_id"],
         "revision": child["revision"], "status": child["status"],
         "provider_write_state": "not_attempted", "provider_write_reached": False,
         "provider_write_uncertain": False},
        {"$set": {"status": "previewed_v2", "confirm_token_hash": hashlib.sha256(token.encode()).hexdigest(),
            "expires_at": expiry}, "$inc": {"revision": 1}})
    if result.matched_count != 1:
        raise _fail("child_refresh_race")
    await management._audit(db, user_id=parent["user_id"], actor_id=parent["user_id"],
        proposal_id=child["proposal_id"], event="publication_never_sent_child_refreshed",
        detail={"publication_id": parent["publication_id"], "step": index, "previous_revision": child["revision"]})
    return management._public_proposal({**child, "status": "previewed_v2", "expires_at": expiry,
        "revision": child["revision"] + 1}, confirm_token=token)


async def _creative_catalog(provider: Any, account_id: str) -> list[dict[str, Any]]:
    path = "/adaccounts/" + management._identifier(account_id, field="account_id") + "/creatives"
    next_path, params, rows, seen_pages, seen_ids = path, {"limit": 1000}, [], set(), set()
    for _ in range(10):
        key = next_path + json.dumps(params, sort_keys=True)
        if key in seen_pages:
            raise _fail("creative_catalog_incomplete")
        seen_pages.add(key)
        payload = await provider._request("GET", next_path, params=params)
        if (not isinstance(payload, dict) or str(payload.get("request_status") or "").upper() not in {"SUCCESS", "OK"}
                or not isinstance(payload.get("creatives"), list)):
            raise _fail("creative_catalog_incomplete")
        for wrapper in payload["creatives"]:
            entity = wrapper.get("creative") if isinstance(wrapper, dict) else None
            if (not isinstance(entity, dict) or str(wrapper.get("sub_request_status") or "").upper() not in {"SUCCESS", "OK"}
                    or not entity.get("id") or not isinstance(entity.get("name"), str)
                    or entity.get("ad_account_id") != account_id or entity["id"] in seen_ids):
                raise _fail("creative_catalog_incomplete")
            seen_ids.add(entity["id"])
            rows.append(entity)
            if len(rows) > 10_000:
                raise _fail("creative_catalog_incomplete")
        paging = payload.get("paging")
        if paging is None:
            paging = {}
        if not isinstance(paging, dict):
            raise _fail("creative_catalog_incomplete")
        link = paging.get("next_link")
        if link is None or link == "":
            if paging.get("has_more") not in (None, False) or paging.get("next_page_id") not in (None, ""):
                raise _fail("creative_catalog_incomplete")
            return rows
        try:
            parsed, base = urlsplit(link), urlsplit(SNAPCHAT_API_BASE)
            pairs = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
            query = dict(pairs)
            if (parsed.scheme != "https" or parsed.netloc != base.netloc
                    or parsed.path != base.path + path or parsed.fragment or len(pairs) != len(query)
                    or set(query) - {"cursor", "limit"} or not query.get("cursor")
                    or len(query["cursor"]) > 8192 or len(link) > 16_384
                    or ("limit" in query and (not query["limit"].isdigit() or not 50 <= int(query["limit"]) <= 1000))):
                raise ValueError()
        except (ValueError, TypeError, AttributeError) as exc:
            raise _fail("creative_catalog_incomplete") from exc
        next_path, params = path + "?" + parsed.query, None
    raise _fail("creative_catalog_incomplete")


def _matching_creative(child: dict[str, Any], entity: dict[str, Any]) -> bool:
    expected = management._operation_expected_values(child["operation"])
    started, failed = _server_date(child.get("execution_started_at")), _server_date(child.get("failed_at"))
    created = media._date(entity.get("created_at"))
    matches, _ = management._changed_values_match(expected, entity)
    return bool(matches and entity.get("ad_account_id") == child["account_id"]
        and entity.get("type") == "WEB_VIEW" and not management._create_candidate_deleted(entity)
        and entity.get("visibility") not in {"HIDDEN", "DELETED"}
        and started and failed and created and started <= failed
        and started - timedelta(minutes=1) <= created <= failed + timedelta(minutes=1))


async def recover_publication_creative(db: Any, user_id: str,
        parent: dict[str, Any], child: dict[str, Any]) -> dict[str, Any]:
    """Positive adoption only. A zero scan never authorizes another create."""
    if (child.get("publication_id") != parent["publication_id"] or child.get("action") != "creative.create"
            or child.get("status") != "failed" or child.get("provider_write_uncertain") is not True
            or child.get("provider_entity_id")):
        raise _fail("creative_recovery_invalid")
    management._assert_stored_operation_integrity(child, child["operation"])
    child = await management._ensure_create_intent_fence(db, user_id=user_id, row=child, operation=child["operation"])
    intent_lease = await management._active_proposal_entity_lease(db, user_id=user_id,
        proposal_id=child["proposal_id"])
    child, scan_token = await management._claim_create_reconciliation_scan(db, user_id=user_id, row=child)
    provider = management.SnapchatManagementProvider(db, user_id)
    try:
        async with asyncio.timeout(60):
            # Identification is GET-only and remains possible after approval,
            # product inventory or Pixel eligibility expires. New writes must
            # pass the complete current dependency proof separately.
            await media._guard(db, user_id, parent["account_id"], provider, writes=False)
            await _paused_ancestors(parent, 2, provider)
            catalogs = await _creative_catalog(provider, parent["account_id"])
            name = management._operation_create_entity(child["operation"])["name"]
            matches = [item for item in catalogs if item["name"] == name]
            if len(matches) != 1 or not _matching_creative(child, matches[0]):
                raise _fail("creative_create_unresolved")
            entity_id = management._identifier(matches[0]["id"], field="creative_id")
            entity = await provider.read_entity("creative", entity_id)
            if entity.get("id") != entity_id or not _matching_creative(child, entity):
                raise _fail("creative_create_unresolved")
            confirm = [item for item in await _creative_catalog(provider, parent["account_id"]) if item["name"] == name]
            if len(confirm) != 1 or confirm[0]["id"] != entity_id or not _matching_creative(child, confirm[0]):
                raise _fail("creative_create_unresolved")
            await media._guard(db, user_id, parent["account_id"], provider, writes=False)
            fresh_parent = await _load(db, user_id, parent["publication_id"])
            if (fresh_parent["current_step"] != 2 or fresh_parent["steps"][2].get("proposal_id") != child["proposal_id"]
                    or fresh_parent.get("lease_token") != parent.get("lease_token")):
                raise _fail("creative_recovery_race")
            result = await _collection(db, management.PROPOSAL_COLLECTION).update_one(
                {"user_id": user_id, "proposal_id": child["proposal_id"], "status": "failed",
                 "provider_write_uncertain": True, "create_reconciliation_scan_token": scan_token,
                 "create_reconciliation_scan_active": True},
                {"$set": {"status": "executing", "provider_entity_id": entity_id,
                    "create_reconciliation_scan_active": False}})
            if result.matched_count != 1:
                raise _fail("creative_recovery_race")
            result = await management._complete_verified_execution(db, user_id=user_id, actor_id=user_id,
                proposal_id=child["proposal_id"], row=child, operation=child["operation"],
                account=await management._selected_account(db, user_id, parent["account_id"]),
                provider_entity_id=entity_id, verified=entity, event="publication_creative_reconciled_by_exact_catalog")
            await management._release_observed_entity_lease(db, user_id=user_id, lease=intent_lease)
            return result
    finally:
        await management._release_create_reconciliation_scan(db, user_id=user_id,
            proposal_id=child["proposal_id"], scan_token=scan_token)


async def _child(db: Any, row: dict[str, Any], index: int) -> dict[str, Any] | None:
    return await _collection(db, management.PROPOSAL_COLLECTION).find_one(
        {"user_id": row["user_id"], "idempotency_key": row["steps"][index]["idempotency_key"]})


async def _final_readback(db: Any, row: dict[str, Any]) -> dict[str, Any]:
    provider = management.SnapchatManagementProvider(db, row["user_id"])
    await _dependencies(db, row["user_id"], row["plan"], provider)
    await _paused_ancestors(row, 3, provider)
    creative = await provider.read_entity("creative", _verified_id(row, 2))
    child = await _child(db, row, 2)
    if not child or not management._changed_values_match(management._operation_expected_values(child["operation"]), creative)[0]:
        raise _fail("creative_readback_changed")
    ad = await provider.read_entity("ad", _verified_id(row, 3))
    if (ad.get("status") != "PAUSED" or ad.get("ad_squad_id") != _verified_id(row, 1)
            or ad.get("creative_id") != _verified_id(row, 2) or ad.get("type") != "REMOTE_WEBPAGE"):
        raise _fail("ad_readback_changed")
    return {"creative_packaging_status": creative.get("packaging_status"),
        "creative_review_status": creative.get("review_status"), "ad_review_status": ad.get("review_status")}


async def run_publication(db: Any, user_id: str, publication_id: str) -> dict[str, Any]:
    row = await _load(db, user_id, publication_id)
    if row["status"] == "completed":
        return _public(row)
    _assert_grant(row, leased=False)
    if _active_worker(row):
        claimed = _server_date(row.get("claimed_at"))
        if (row.get("worker_phase") == "claimed" and claimed
                and media._now() >= claimed + timedelta(seconds=60)):
            # No child work starts until the next revision is acknowledged.
            # CAS recovery fences a delayed unstarted claimant before takeover.
            row = await _save(db, row, {"lease_token": None, "status": "interrupted",
                "worker_phase": "stale_unstarted"})
        else:
            raise _fail("worker_busy")
    if not management.snapchat_campaign_mutations_enabled():
        raise _fail("mutations_disabled")
    claim_token = secrets.token_urlsafe(24)
    try:
        row = await _save(db, row, {"status": "running", "lease_token": claim_token,
            "claimed_at": media._iso(), "worker_phase": "claimed",
            "lease_expires_at": (media._now() + timedelta(seconds=WORKER_LEASE_SECONDS)).isoformat(), "failure": None})
        row = await _save(db, row, {"worker_phase": "running"})
        async with asyncio.timeout(WORKER_TIMEOUT_SECONDS):
            for index in range(row["current_step"], 4):
                _assert_grant(row)
                child = await _child(db, row, index)
                if child and (child.get("provider_write_uncertain") or child.get("status") in {"executing", "rolling_back"}):
                    raise _fail("reconcile_required")
                if child and child.get("status") == "failed":
                    raise _fail("child_failed_review_required")
                if child and (_server_date(child.get("expires_at")) is None
                        or _server_date(child["expires_at"]) <= media._now()) and child.get("status") != "completed":
                    child = await refresh_publication_child(db, row, child, index)
                if not child:
                    child = await management.create_snapchat_management_proposal(db, user_id, user_id,
                        management.SnapchatManagementProposalInput(**_request(row, index)))
                steps = deepcopy(row["steps"])
                steps[index]["proposal_id"] = child["proposal_id"]
                row = await _save(db, row, {"steps": steps})
                if child["status"] in {"previewed", "previewed_v2"}:
                    # Recover a preview token from the same request, never create a new child key.
                    child = await management.create_snapchat_management_proposal(db, user_id, user_id,
                        management.SnapchatManagementProposalInput(**_request(row, index)))
                    child = await management.approve_snapchat_management_proposal(db, user_id, user_id,
                        child["proposal_id"], management.SnapchatManagementApprovalInput(
                            confirm_token=child["confirm_token"], expected_revision=child["revision"]))
                if child["status"] != "completed":
                    child = await management.execute_snapchat_management_proposal(db, user_id, user_id, child["proposal_id"])
                entity_id = _child_id(child)
                if index != 2 and (child.get("verification") or {}).get("status") != "PAUSED":
                    raise _fail("child_not_paused")
                steps = deepcopy(row["steps"])
                steps[index].update({"status": "completed", "provider_entity_id": entity_id,
                    "verified_at": media._iso()})
                row = await _save(db, row, {"steps": steps, "current_step": index + 1,
                    "last_event": {"event": "child_verified", "step": index, "actor_id": user_id, "at": media._iso()}})
            review = await _final_readback(db, row)
            row = await _save(db, row, {"status": "completed", "lease_token": None,
                "finished_at": media._iso(), "review": review,
                "last_event": {"event": "publication_verified_paused", "actor_id": user_id, "at": media._iso()}})
            return _public(row)
    except BaseException as exc:
        code = (exc.detail.get("code") if isinstance(exc, HTTPException) and isinstance(exc.detail, dict)
            else "snapchat_publication_interrupted")
        try:
            # The initial CAS can commit before its acknowledgement is lost.
            # Stabilize only this server-owned token, never a concurrent winner.
            await asyncio.shield(_jobs(db).update_one(
                {"_id": publication_id, "user_id": user_id, "lease_token": claim_token, "status": "running"},
                {"$set": {"status": "interrupted", "lease_token": None, "worker_phase": "interrupted",
                    "failure": {"code": code}, "last_event": {"event": "interrupted", "actor_id": user_id, "at": media._iso()}},
                 "$inc": {"revision": 1}, "$push": {"events": {"$each": [{
                     "event": "worker_interrupted", "actor_id": user_id, "at": media._iso(),
                     "approval_revision": row.get("approval_revision")}], "$slice": -100}}}))
        except Exception:
            pass
        raise


async def reconcile_publication(db: Any, user_id: str, publication_id: str) -> dict[str, Any]:
    row = await _load(db, user_id, publication_id)
    if _active_worker(row):
        raise _fail("worker_busy")
    row = await _save(db, row, {"last_event": {"event": "reconciliation_requested",
        "step": row["current_step"]}})
    index = row["current_step"]
    if index >= 4:
        review = await _final_readback(db, row)
        row = await _save(db, row, {"status": "completed", "lease_token": None,
            "finished_at": media._iso(), "review": review,
            "last_event": {"event": "publication_reconciled_paused", "actor_id": user_id, "at": media._iso()}})
        return _public(row)
    child = await _child(db, row, index)
    if child:
        if child.get("action") == "creative.create" and child.get("provider_write_uncertain") and not child.get("provider_entity_id"):
            child = await recover_publication_creative(db, user_id, row, child)
        else:
            child = await management.reconcile_snapchat_management_proposal(db, user_id, user_id, child["proposal_id"])
        if child.get("status") == "completed":
            entity_id = _child_id(child)
            if index != 2 and (child.get("verification") or {}).get("status") != "PAUSED":
                raise _fail("child_not_paused")
            steps = deepcopy(row["steps"])
            steps[index].update({"proposal_id": child["proposal_id"], "provider_entity_id": entity_id,
                "status": "completed", "verified_at": media._iso()})
            row = await _save(db, row, {"steps": steps, "current_step": index + 1, "status": "interrupted",
                "lease_token": None, "failure": None,
                "last_event": {"event": "child_reconciled", "step": index}})
    return _public(row)


def schedule_publication(db: Any, user_id: str, publication_id: str) -> None:
    key = (id(db), user_id, publication_id)
    if key in _WORKERS and not _WORKERS[key].done():
        return
    task = asyncio.create_task(run_publication(db, user_id, publication_id))
    _WORKERS[key] = task

    def finished(done: asyncio.Task) -> None:
        if _WORKERS.get(key) is done:
            _WORKERS.pop(key, None)
        if not done.cancelled():
            done.exception()
    task.add_done_callback(finished)


def attach_snapchat_campaign_publication_routes(router: APIRouter, db: Any,
        current_user: Callable, require_owner: Callable) -> None:
    base = "/snapchat_ads/management/publications"

    @router.post(base + "/preview", status_code=201)
    async def preview(payload: PublicationInput, user: dict = Depends(current_user)) -> dict:
        return await preview_publication(db, str(require_owner(user)["id"]), payload)

    @router.get(base)
    async def listing(user: dict = Depends(current_user)) -> dict:
        owner = str(require_owner(user)["id"])
        rows = await _jobs(db).find({"user_id": owner}).sort("created_at", -1).limit(20).to_list(length=20)
        return {"publications": [_public(row) for row in rows]}

    @router.get(base + "/current")
    async def current(idempotency_key: str, user: dict = Depends(current_user)) -> dict:
        owner = str(require_owner(user)["id"])
        key = management._identifier(idempotency_key, field="idempotency_key")
        return _public(await _load(db, owner, _hash([owner, key])))

    @router.get(base + "/{publication_id}")
    async def status(publication_id: str, user: dict = Depends(current_user)) -> dict:
        return _public(await _load(db, str(require_owner(user)["id"]), publication_id))

    @router.post(base + "/{publication_id}/approve")
    async def approve(publication_id: str, payload: PublicationApproval,
            user: dict = Depends(current_user)) -> dict:
        return await approve_publication(db, str(require_owner(user)["id"]), publication_id, payload)

    @router.post(base + "/{publication_id}/review")
    async def review(publication_id: str, user: dict = Depends(current_user)) -> dict:
        return await review_publication(db, str(require_owner(user)["id"]), publication_id)

    @router.post(base + "/{publication_id}/execute", status_code=202)
    async def execute(publication_id: str, user: dict = Depends(current_user)) -> dict:
        owner = str(require_owner(user)["id"])
        row = await _load(db, owner, publication_id)
        if row["status"] == "completed":
            return _public(row)
        _assert_grant(row, leased=False)
        schedule_publication(db, owner, publication_id)
        return _public(row)

    @router.post(base + "/{publication_id}/reconcile")
    async def reconcile(publication_id: str, user: dict = Depends(current_user)) -> dict:
        return await reconcile_publication(db, str(require_owner(user)["id"]), publication_id)

