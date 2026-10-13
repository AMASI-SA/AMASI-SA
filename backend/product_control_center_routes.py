"""Governed Product Control Center for Mezan OS.

This module owns product-content drafts, approvals, publishing, verification and
rollback. Mezan cost collections are deliberately excluded from every payload.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable

import httpx
from fastapi import APIRouter, Body, Depends, HTTPException
from fastapi.responses import JSONResponse
from pymongo import ASCENDING, ReturnDocument
from pymongo.errors import DuplicateKeyError

from product_v2_routes import PRODUCTS, _text
from salla_integration.service import SallaError, call_salla

DRAFTS = "mezan_product_change_drafts_v2"
REVISIONS = "mezan_product_change_revisions_v2"
POLICIES = "mezan_product_ai_policies_v2"
ATTEMPTS = "mezan_product_publish_attempts_v2"
ACTIVE_DRAFT_STATUSES = ["draft", "approved"]
ACTIVE_ATTEMPT_STATUSES = {
    "preparing", "publishing", "verifying", "verification_pending",
    "outcome_unknown", "rolling_back", "rollback_required",
}
RETRYABLE_ATTEMPT_STATUSES = {"failed_before_write", "rolled_back"}


def _positive_timeout(name: str, default: float) -> float:
    try:
        return max(0.1, min(float(os.getenv(name, default)), 60.0))
    except (TypeError, ValueError):
        return default


# These bounds apply only to Product Control Center provider calls.  The shared
# Salla client keeps its existing global timeout for every other integration.
PRODUCT_PROVIDER_READ_TIMEOUT_SECONDS = _positive_timeout(
    "PRODUCT_PROVIDER_READ_TIMEOUT_SECONDS", 12.0,
)
PRODUCT_PROVIDER_WRITE_TIMEOUT_SECONDS = _positive_timeout(
    "PRODUCT_PROVIDER_WRITE_TIMEOUT_SECONDS", 25.0,
)
PUBLISH_EXECUTION_LEASE_SECONDS = 90
VERIFICATION_BACKOFF_SECONDS = (5, 15, 30, 60, 300)

PROTECTED_FIELDS = {
    "base_cost", "variant_costs", "cost_price", "cost_price_from_salla",
    "unit_cost", "initial_unit_cost", "component_costs", "option_costs",
    "profit", "margin", "accounting", "qoyod",
}

PUBLISHABLE_FIELDS = {
    "name", "description", "short_description", "price", "sale_price",
    "salla_cost_price", "status", "sku", "barcode", "categories", "brand", "seo",
    "google_category", "local_category", "images", "options",
    "custom_fields", "variants", "slug",
}

DEFAULT_POLICY = {
    "mode": "proposal_only",
    "require_human_approval": True,
    "allow_content": True,
    "allow_images": False,
    "allow_price": False,
    "allow_categories": True,
    "allow_status": False,
    "min_margin_percent": 35.0,
    "max_price_change_percent": 10.0,
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _clean_patch(payload: dict[str, Any]) -> dict[str, Any]:
    attempted = PROTECTED_FIELDS.intersection(payload)
    if attempted:
        raise HTTPException(status_code=422, detail={
            "code": "protected_mezan_cost_fields",
            "fields": sorted(attempted),
        })
    return {key: value for key, value in payload.items() if key in PUBLISHABLE_FIELDS}


def _serialize(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return row
    result = dict(row)
    result.pop("_id", None)
    for key, value in list(result.items()):
        if hasattr(value, "isoformat"):
            result[key] = value.isoformat()
    return result


def _salla_payload(patch: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for key in (
        "name", "description", "short_description", "price", "sale_price",
        "status", "sku", "barcode", "categories", "brand", "images",
        "options", "custom_fields", "variants", "slug",
    ):
        if key in patch:
            payload[key] = patch[key]
    # `salla_cost_price` is deliberately a Product Control Center alias.
    # The canonical Mezan accounting/cost fields remain protected and are never
    # accepted from this draft path. Only the outbound Salla payload receives
    # the provider field name `cost_price`.
    if "salla_cost_price" in patch:
        payload["cost_price"] = patch["salla_cost_price"]
    seo = patch.get("seo")
    if isinstance(seo, dict):
        if seo.get("title") is not None:
            payload["seo_title"] = seo.get("title")
        if seo.get("description") is not None:
            payload["seo_description"] = seo.get("description")
        if seo.get("keywords") is not None:
            payload["keywords"] = seo.get("keywords")
    if patch.get("google_category") is not None:
        payload["google_product_category"] = patch.get("google_category")
    return payload


def _money_amount(value: Any) -> float | None:
    if value in (None, ""):
        return None
    if isinstance(value, dict):
        for key in ("amount", "value", "price", "total"):
            if key in value:
                return _money_amount(value.get(key))
        return None
    try:
        return float(value)
    except (TypeError, ValueError, OverflowError):
        return None


def _salla_product(response: Any) -> dict[str, Any]:
    if not isinstance(response, dict):
        return {}
    data = response.get("data")
    return data if isinstance(data, dict) else response


def _price_snapshot(product: dict[str, Any]) -> dict[str, float | None]:
    regular = _money_amount(product.get("regular_price"))
    if regular is None:
        regular = _money_amount(product.get("price"))
    return {
        "price": regular,
        "sale_price": _money_amount(product.get("sale_price")),
        "cost_price": _money_amount(product.get("cost_price") or product.get("cost")),
    }


def _salla_payload_with_preserved_prices(
    patch: dict[str, Any], current_product: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, float | None]]:
    """Build a complete price-safe Salla payload from a partial Mezan patch."""
    payload = _salla_payload(patch)
    snapshot = _price_snapshot(current_product)
    if snapshot["price"] is None:
        raise HTTPException(status_code=409, detail={"code": "salla_price_snapshot_missing"})
    if "price" not in payload:
        payload["price"] = snapshot["price"]
    if "sale_price" not in payload and snapshot["sale_price"] is not None:
        payload["sale_price"] = snapshot["sale_price"]
    expected = {
        "price": _money_amount(payload.get("price")),
        "sale_price": _money_amount(payload.get("sale_price")),
    }
    if "cost_price" in payload:
        expected["cost_price"] = _money_amount(payload.get("cost_price"))
    return payload, expected


def _verify_salla_prices(
    product: dict[str, Any], expected: dict[str, float | None]
) -> None:
    actual = _price_snapshot(product)
    mismatches: dict[str, dict[str, float | None]] = {}
    for key, expected_value in expected.items():
        if expected_value is None:
            continue
        actual_value = actual.get(key)
        if actual_value is None or abs(actual_value - expected_value) > 0.0001:
            mismatches[key] = {"expected": expected_value, "actual": actual_value}
    if mismatches:
        raise HTTPException(status_code=409, detail={
            "code": "salla_price_verification_failed",
            "mismatches": mismatches,
        })


def _before_value(product: dict[str, Any], key: str) -> Any:
    if key == "salla_cost_price":
        return product.get("cost_price_from_salla")
    return product.get(key)


class _ProviderCallFailure(Exception):
    def __init__(self, code: str, *, retryable: bool, status_code: int = 503):
        super().__init__(code)
        self.code = code
        self.retryable = retryable
        self.status_code = status_code


async def _bounded_provider_call(
    provider: Callable[..., Awaitable[dict[str, Any]]],
    db: Any,
    user_id: str,
    method: str,
    path: str,
    *,
    timeout_seconds: float,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    try:
        kwargs = {"json": payload} if payload is not None else {}
        return await asyncio.wait_for(
            provider(db, user_id, method, path, **kwargs),
            timeout=timeout_seconds,
        )
    except SallaError as exc:
        status_code = int(exc.status_code or 503)
        if status_code == 429:
            code = "salla_rate_limited"
        elif status_code >= 500:
            code = "salla_unavailable"
        elif status_code in {401, 403}:
            code = "salla_authorization_failed"
        else:
            code = "salla_request_rejected"
        raise _ProviderCallFailure(
            code,
            retryable=status_code == 429 or status_code >= 500,
            status_code=status_code,
        ) from exc
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, dict) else {}
        raise _ProviderCallFailure(
            str(detail.get("code") or "salla_request_rejected"),
            retryable=False,
            status_code=int(exc.status_code or 409),
        ) from exc
    except (asyncio.TimeoutError, TimeoutError, httpx.TimeoutException) as exc:
        raise _ProviderCallFailure("salla_timeout", retryable=True) from exc
    except (httpx.NetworkError, ConnectionError, OSError) as exc:
        raise _ProviderCallFailure("salla_connection_error", retryable=True) from exc


def _canonical_projection_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _canonical_projection_value(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, list):
        return [_canonical_projection_value(item) for item in value]
    return value


def _expected_projection(payload: dict[str, Any]) -> dict[str, Any]:
    projection: dict[str, Any] = {}
    for key, value in payload.items():
        if key == "__mezan_status":
            projection["status"] = {
                "active": "sale",
                "sale": "sale",
                "inactive": "hidden",
                "hidden": "hidden",
                "out_of_stock": "out",
                "out": "out",
            }.get(str(value or "").strip().lower(), value)
            continue
        if key in {"price", "sale_price", "cost_price"}:
            projection[key] = _money_amount(value)
        elif key == "categories":
            projection[key] = sorted(
                str(item.get("id") if isinstance(item, dict) else item)
                for item in (value or [])
            )
        else:
            projection[key] = _canonical_projection_value(value)
    return projection


def _projection_hash(projection: dict[str, Any]) -> str:
    encoded = json.dumps(
        projection,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _remote_projection(
    product: dict[str, Any], expected: dict[str, Any],
) -> dict[str, Any]:
    prices = _price_snapshot(product)
    actual: dict[str, Any] = {}
    for key in expected:
        if key in prices:
            actual[key] = prices[key]
        elif key == "categories":
            actual[key] = sorted(
                str(item.get("id") if isinstance(item, dict) else item)
                for item in (product.get(key) or [])
            )
        else:
            actual[key] = _canonical_projection_value(product.get(key))
    return actual


def _projection_mismatches(
    product: dict[str, Any], expected: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    actual = _remote_projection(product, expected)
    mismatches: dict[str, dict[str, Any]] = {}
    for key, expected_value in expected.items():
        actual_value = actual.get(key)
        if key in {"price", "sale_price", "cost_price"}:
            if expected_value is None:
                continue
            if actual_value is None or abs(float(actual_value) - float(expected_value)) > 0.0001:
                mismatches[key] = {"expected": expected_value, "actual": actual_value}
        elif actual_value != expected_value:
            mismatches[key] = {"expected": expected_value, "actual": actual_value}
    return mismatches


def _attempt_public(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return None
    hidden = {
        "_id", "execution_token", "execution_expires_at", "patch", "before",
        "rollback_projection", "provider_response_summary",
    }
    result = {key: value for key, value in row.items() if key not in hidden}
    for key, value in list(result.items()):
        if hasattr(value, "isoformat"):
            result[key] = value.isoformat()
    return result


def _attempt_code(row: dict[str, Any]) -> str:
    if row.get("last_error_code"):
        return str(row["last_error_code"])
    return {
        "preparing": "product_publish_preparing",
        "publishing": "product_publish_in_progress",
        "verifying": "product_publish_verifying",
        "verification_pending": "salla_publish_verification_pending",
        "outcome_unknown": "salla_publish_outcome_unknown",
        "rollback_required": "salla_publish_rollback_required",
        "rolled_back": "salla_publish_rolled_back",
        "succeeded": "salla_publish_succeeded",
    }.get(str(row.get("status") or ""), "product_publish_state")


def _attempt_response(
    row: dict[str, Any],
    *,
    revision: dict[str, Any] | None = None,
) -> JSONResponse:
    status = str(row.get("status") or "")
    http_status = 200
    if status in {"preparing", "publishing", "verifying", "verification_pending", "outcome_unknown"}:
        http_status = 202
    elif status == "failed_before_write":
        http_status = int(row.get("provider_status_code") or 503)
        if http_status < 400 or http_status > 599:
            http_status = 503
    elif status in {"rollback_required", "rolled_back"}:
        http_status = 409
    content: dict[str, Any] = {
        "ok": status == "succeeded",
        "code": _attempt_code(row),
        "stage": row.get("stage"),
        "attempt_id": row.get("id"),
        "status": status,
        "retryable": bool(row.get("retryability")),
        "outcome": row.get("outcome") or status,
        "attempt": _attempt_public(row),
    }
    if revision:
        content["revision"] = _serialize(revision)
        content["cost_engine_preserved"] = True
    return JSONResponse(status_code=http_status, content=content)


async def _update_attempt(
    db: Any, user_id: str, attempt_id: str, fields: dict[str, Any],
) -> dict[str, Any]:
    now = _now()
    update: dict[str, Any] = {"$set": {**fields, "updated_at": now}}
    if fields.get("status") not in {"preparing", "publishing", "verifying", "rolling_back"}:
        update["$unset"] = {
            "execution_token": "",
            "execution_expires_at": "",
        }
    await db[ATTEMPTS].update_one(
        {"id": attempt_id, "user_id": user_id},
        update,
    )
    return await db[ATTEMPTS].find_one(
        {"id": attempt_id, "user_id": user_id}, {"_id": 0},
    )


async def _recover_stale_attempt(db: Any, row: dict[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return None
    expires_at = row.get("execution_expires_at")
    if not isinstance(expires_at, datetime) or expires_at > _now():
        return row
    status = str(row.get("status") or "")
    recovery = {
        "preparing": ("failed_before_write", "preparing", "publish_worker_stale_before_write", True),
        "publishing": ("outcome_unknown", "publishing", "publish_worker_stale_during_write", False),
        "verifying": ("verification_pending", "verifying", "publish_worker_stale_during_verify", True),
        "rolling_back": ("rollback_required", "rolling_back", "rollback_worker_stale", False),
    }.get(status)
    if not recovery:
        return row
    next_status, stage, code, retryable = recovery
    claimed = await db[ATTEMPTS].find_one_and_update(
        {
            "id": row.get("id"),
            "user_id": row.get("user_id"),
            "status": status,
            "execution_token": row.get("execution_token"),
        },
        {
            "$set": {
                "status": next_status,
                "stage": stage,
                "outcome": next_status,
                "last_error_code": code,
                "retryability": retryable,
                "updated_at": _now(),
            },
            "$unset": {"execution_token": "", "execution_expires_at": ""},
        },
        return_document=ReturnDocument.AFTER,
    )
    return claimed or await db[ATTEMPTS].find_one(
        {"id": row.get("id"), "user_id": row.get("user_id")}, {"_id": 0},
    )


async def _attempt_for_draft(
    db: Any, user_id: str, draft_id: str,
) -> dict[str, Any] | None:
    row = await db[ATTEMPTS].find_one(
        {"user_id": user_id, "draft_id": draft_id}, {"_id": 0},
    )
    return await _recover_stale_attempt(db, row)


async def _acquire_publish_attempt(
    db: Any,
    *,
    user_id: str,
    product: dict[str, Any],
    draft: dict[str, Any],
    patch: dict[str, Any],
) -> tuple[dict[str, Any], bool]:
    token = uuid.uuid4().hex
    now = _now()
    attempt_id = uuid.uuid4().hex
    row = {
        "id": attempt_id,
        "draft_id": str(draft["id"]),
        "user_id": user_id,
        "product_id": str(product.get("mezan_product_id") or product.get("id") or ""),
        "salla_product_id": str(product["salla_product_id"]),
        "stage": "preparing",
        "status": "preparing",
        "outcome": "preparing",
        "created_at": now,
        "updated_at": now,
        "provider_write_started_at": None,
        "provider_write_acknowledged_at": None,
        "verification_at": None,
        "verification_attempts": 0,
        "last_error_code": None,
        "retryability": False,
        "expected_projection": {},
        "expected_projection_hash": None,
        "patch": patch,
        "before": draft.get("before") or {},
        "execution_token": token,
        "execution_expires_at": now + timedelta(seconds=PUBLISH_EXECUTION_LEASE_SECONDS),
    }
    try:
        current = await db[ATTEMPTS].find_one_and_update(
            {"user_id": user_id, "draft_id": str(draft["id"])},
            {"$setOnInsert": row},
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
    except DuplicateKeyError:
        current = await db[ATTEMPTS].find_one(
            {"user_id": user_id, "draft_id": str(draft["id"])}, {"_id": 0},
        )
    current = await _recover_stale_attempt(db, current)
    if current and current.get("execution_token") == token:
        return current, True
    if current and current.get("status") in RETRYABLE_ATTEMPT_STATUSES:
        claimed = await db[ATTEMPTS].find_one_and_update(
            {
                "id": current.get("id"),
                "user_id": user_id,
                "status": current.get("status"),
            },
            {
                "$set": {
                    "stage": "preparing",
                    "status": "preparing",
                    "outcome": "preparing",
                    "last_error_code": None,
                    "retryability": False,
                    "execution_token": token,
                    "execution_expires_at": now + timedelta(seconds=PUBLISH_EXECUTION_LEASE_SECONDS),
                    "updated_at": now,
                },
            },
            return_document=ReturnDocument.AFTER,
        )
        if claimed and claimed.get("execution_token") == token:
            return claimed, True
    return current, False


def _provider_response_summary(response: Any) -> dict[str, Any]:
    if not isinstance(response, dict):
        return {}
    return {
        key: response.get(key)
        for key in ("status", "success", "skipped", "reason")
        if response.get(key) is not None
    }


async def _product(db: Any, user_id: str, product_id: str) -> dict[str, Any]:
    row = await db[PRODUCTS].find_one({
        "user_id": user_id,
        "$or": [
            {"id": product_id}, {"mezan_product_id": product_id},
            {"salla_product_id": product_id},
        ],
    }, {"_id": 0, "raw_salla": 0, "raw_salla_details": 0})
    if not row:
        raise HTTPException(status_code=404, detail={"code": "product_v2_not_found"})
    return row


async def ensure_indexes(db: Any) -> None:
    await db[DRAFTS].create_index(
        [("user_id", ASCENDING), ("salla_product_id", ASCENDING), ("status", ASCENDING)],
        name="ix_product_drafts_v2",
    )
    await db[REVISIONS].create_index(
        [("user_id", ASCENDING), ("salla_product_id", ASCENDING), ("created_at", ASCENDING)],
        name="ix_product_revisions_v2",
    )
    await db[POLICIES].create_index(
        [("user_id", ASCENDING)], unique=True, name="uq_product_ai_policy_v2",
    )
    await db[ATTEMPTS].create_index(
        [("user_id", ASCENDING), ("draft_id", ASCENDING)],
        unique=True,
        name="uq_product_publish_attempt_v2",
    )
    await db[ATTEMPTS].create_index(
        [("user_id", ASCENDING), ("salla_product_id", ASCENDING), ("updated_at", ASCENDING)],
        name="ix_product_publish_attempt_state_v2",
    )
    await db[REVISIONS].create_index(
        [("user_id", ASCENDING), ("id", ASCENDING)],
        unique=True,
        name="uq_product_revision_id_v2",
    )


async def supersede_active_drafts(db: Any, *, user_id: str, salla_id: str, keep_id: str | None = None, now: datetime | None = None) -> None:
    timestamp = now or _now()
    query: dict[str, Any] = {
        "user_id": user_id,
        "salla_product_id": salla_id,
        "status": {"$in": ACTIVE_DRAFT_STATUSES},
    }
    if keep_id:
        query["id"] = {"$ne": keep_id}
    await db[DRAFTS].update_many(query, {"$set": {
        "status": "superseded",
        "superseded_at": timestamp,
        "updated_at": timestamp,
        "superseded_by": keep_id,
    }})


async def _finalize_successful_publish(
    db: Any,
    *,
    attempt: dict[str, Any],
    draft: dict[str, Any],
    product: dict[str, Any],
    verified_remote: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Commit local state exactly once after Salla matches the stored projection."""
    user_id = str(attempt["user_id"])
    attempt_id = str(attempt["id"])
    salla_id = str(attempt["salla_product_id"])
    patch = dict(attempt.get("patch") or draft.get("changes") or {})
    now = _now()
    revision_id = f"salla-publish-{attempt_id}"
    revision = {
        "id": revision_id,
        "user_id": user_id,
        "salla_product_id": salla_id,
        "mezan_product_id": product.get("mezan_product_id"),
        "draft_id": str(draft["id"]),
        "publish_attempt_id": attempt_id,
        "before": attempt.get("before") or draft.get("before") or {},
        "after": patch,
        "source": draft.get("source"),
        "reason": draft.get("reason"),
        "provider_response_summary": attempt.get("provider_response_summary") or {},
        "salla_response": attempt.get("provider_response_summary") or {},
        "created_at": now,
    }
    await db[REVISIONS].update_one(
        {"id": revision_id, "user_id": user_id},
        {"$setOnInsert": revision},
        upsert=True,
    )
    await db[DRAFTS].update_one(
        {"id": str(draft["id"]), "user_id": user_id},
        {"$set": {
            "status": "published",
            "published_at": now,
            "updated_at": now,
            "revision_id": revision_id,
            "publish_attempt_id": attempt_id,
        }},
    )
    await supersede_active_drafts(
        db,
        user_id=user_id,
        salla_id=salla_id,
        keep_id=str(draft["id"]),
        now=now,
    )
    local_patch = {key: value for key, value in patch.items() if key != "salla_cost_price"}
    if "salla_cost_price" in patch:
        local_patch["cost_price_from_salla"] = _money_amount(
            verified_remote.get("cost_price") or verified_remote.get("cost")
        )
    await db[PRODUCTS].update_one(
        {"user_id": user_id, "salla_product_id": salla_id},
        {"$set": {
            **local_patch,
            "updated_at": now,
            "last_control_center_publish_at": now,
        }},
    )
    updated = await _update_attempt(db, user_id, attempt_id, {
        "stage": "completed",
        "status": "succeeded",
        "outcome": "succeeded",
        "verification_at": now,
        "last_error_code": None,
        "retryability": False,
        "revision_id": revision_id,
    })
    saved_revision = await db[REVISIONS].find_one(
        {"id": revision_id, "user_id": user_id}, {"_id": 0},
    )
    return updated, saved_revision or revision


async def _mark_verification_pending(
    db: Any,
    attempt: dict[str, Any],
    failure: _ProviderCallFailure,
    *,
    preserve_unknown: bool = False,
) -> dict[str, Any]:
    count = int(attempt.get("verification_attempts") or 0) + 1
    delay = VERIFICATION_BACKOFF_SECONDS[min(count - 1, len(VERIFICATION_BACKOFF_SECONDS) - 1)]
    status = "outcome_unknown" if preserve_unknown else "verification_pending"
    return await _update_attempt(db, str(attempt["user_id"]), str(attempt["id"]), {
        "stage": "verifying",
        "status": status,
        "outcome": status,
        "last_error_code": (
            "salla_publish_outcome_unknown"
            if preserve_unknown
            else "salla_publish_verification_pending"
        ),
        "last_provider_error_code": failure.code,
        "provider_status_code": failure.status_code,
        "retryability": not preserve_unknown,
        "verification_attempts": count,
        "next_verification_at": _now() + timedelta(seconds=delay),
    })


async def _rollback_after_explicit_mismatch(
    db: Any,
    *,
    attempt: dict[str, Any],
    provider: Callable[..., Awaitable[dict[str, Any]]],
    mismatches: dict[str, Any],
) -> dict[str, Any]:
    user_id = str(attempt["user_id"])
    attempt_id = str(attempt["id"])
    salla_id = str(attempt["salla_product_id"])
    rollback = dict(attempt.get("rollback_projection") or {})
    rolling = await _update_attempt(db, user_id, attempt_id, {
        "stage": "rolling_back",
        "status": "rolling_back",
        "outcome": "mismatch_confirmed",
        "mismatches": mismatches,
        "rollback_started_at": _now(),
        "retryability": False,
        "execution_token": attempt.get("execution_token"),
        "execution_expires_at": _now() + timedelta(seconds=PUBLISH_EXECUTION_LEASE_SECONDS),
    })
    try:
        await _bounded_provider_call(
            provider,
            db,
            user_id,
            "PUT",
            f"/products/{salla_id}",
            timeout_seconds=PRODUCT_PROVIDER_WRITE_TIMEOUT_SECONDS,
            payload=rollback,
        )
    except _ProviderCallFailure as failure:
        return await _update_attempt(db, user_id, attempt_id, {
            "stage": "rolling_back",
            "status": "rollback_required",
            "outcome": "outcome_unknown",
            "last_error_code": "salla_rollback_outcome_unknown",
            "last_provider_error_code": failure.code,
            "provider_status_code": failure.status_code,
            "retryability": False,
        })
    try:
        response = await _bounded_provider_call(
            provider,
            db,
            user_id,
            "GET",
            f"/products/{salla_id}",
            timeout_seconds=PRODUCT_PROVIDER_READ_TIMEOUT_SECONDS,
        )
    except _ProviderCallFailure as failure:
        return await _update_attempt(db, user_id, attempt_id, {
            "stage": "rolling_back",
            "status": "rollback_required",
            "outcome": "verification_pending",
            "last_error_code": "salla_rollback_verification_pending",
            "last_provider_error_code": failure.code,
            "provider_status_code": failure.status_code,
            "retryability": False,
        })
    rollback_mismatches = _projection_mismatches(_salla_product(response), rollback)
    if rollback_mismatches:
        return await _update_attempt(db, user_id, attempt_id, {
            "stage": "rolling_back",
            "status": "rollback_required",
            "outcome": "rollback_mismatch",
            "last_error_code": "salla_rollback_verification_failed",
            "rollback_mismatches": rollback_mismatches,
            "retryability": False,
        })
    return await _update_attempt(db, user_id, attempt_id, {
        "stage": "rolled_back",
        "status": "rolled_back",
        "outcome": "rolled_back",
        "rollback_verified_at": _now(),
        "last_error_code": "salla_publish_rolled_back",
        "retryability": True,
    })


async def _execute_publish_attempt(
    db: Any,
    *,
    attempt: dict[str, Any],
    draft: dict[str, Any],
    product: dict[str, Any],
    provider: Callable[..., Awaitable[dict[str, Any]]],
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    user_id = str(attempt["user_id"])
    attempt_id = str(attempt["id"])
    salla_id = str(attempt["salla_product_id"])
    patch = dict(attempt.get("patch") or {})
    try:
        before_response = await _bounded_provider_call(
            provider,
            db,
            user_id,
            "GET",
            f"/products/{salla_id}",
            timeout_seconds=PRODUCT_PROVIDER_READ_TIMEOUT_SECONDS,
        )
        current_remote = _salla_product(before_response)
        requested_payload = _salla_payload(patch)
        if set(requested_payload) == {"google_product_category"}:
            # The installed taxonomy boundary records this Mezan-managed value
            # without a public Salla PUT. Adding the price safety envelope
            # would incorrectly turn it into a forbidden mixed-field write.
            remote_payload = requested_payload
        else:
            remote_payload, _expected_prices = _salla_payload_with_preserved_prices(
                patch, current_remote,
            )
    except _ProviderCallFailure as failure:
        failed = await _update_attempt(db, user_id, attempt_id, {
            "stage": "preparing",
            "status": "failed_before_write",
            "outcome": "failed_before_write",
            "last_error_code": "salla_publish_failed_before_write",
            "last_provider_error_code": failure.code,
            "provider_status_code": failure.status_code,
            "retryability": failure.retryable,
        })
        return failed, None
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, dict) else {}
        failed = await _update_attempt(db, user_id, attempt_id, {
            "stage": "preparing",
            "status": "failed_before_write",
            "outcome": "failed_before_write",
            "last_error_code": str(detail.get("code") or "salla_publish_failed_before_write"),
            "provider_status_code": int(exc.status_code or 409),
            "retryability": False,
        })
        return failed, None

    expected = _expected_projection(remote_payload)
    # The complete projection is required to distinguish an unknown write from
    # a successful one. Automatic rollback remains price-scoped below because
    # those are the only fields captured from the pre-write provider snapshot.
    verification_projection = expected
    rollback = {
        key: value
        for key, value in _price_snapshot(current_remote).items()
        if value is not None
    }
    publishing = await _update_attempt(db, user_id, attempt_id, {
        "stage": "publishing",
        "status": "publishing",
        "outcome": "publishing",
        "provider_write_started_at": _now(),
        "expected_projection": expected,
        "expected_projection_hash": _projection_hash(expected),
        "verification_projection": verification_projection,
        "rollback_projection": rollback,
        "retryability": False,
        "execution_token": attempt.get("execution_token"),
        "execution_expires_at": _now() + timedelta(seconds=PUBLISH_EXECUTION_LEASE_SECONDS),
    })
    try:
        write_response = await _bounded_provider_call(
            provider,
            db,
            user_id,
            "PUT",
            f"/products/{salla_id}",
            timeout_seconds=PRODUCT_PROVIDER_WRITE_TIMEOUT_SECONDS,
            payload=remote_payload,
        )
    except _ProviderCallFailure as failure:
        unknown = await _update_attempt(db, user_id, attempt_id, {
            "stage": "publishing",
            "status": "outcome_unknown",
            "outcome": "outcome_unknown",
            "last_error_code": "salla_publish_outcome_unknown",
            "last_provider_error_code": failure.code,
            "provider_status_code": failure.status_code,
            "retryability": False,
        })
        return unknown, None

    verifying = await _update_attempt(db, user_id, attempt_id, {
        "stage": "verifying",
        "status": "verifying",
        "outcome": "verifying",
        "provider_write_acknowledged_at": _now(),
        "provider_response_summary": _provider_response_summary(write_response),
        "retryability": False,
        "execution_token": publishing.get("execution_token"),
        "execution_expires_at": _now() + timedelta(seconds=PUBLISH_EXECUTION_LEASE_SECONDS),
    })
    if write_response.get("skipped") and write_response.get("reason") in {
        "google_taxonomy_mezan_managed",
        "google_taxonomy_already_matches",
    }:
        return await _finalize_successful_publish(
            db,
            attempt=verifying,
            draft=draft,
            product=product,
            verified_remote=current_remote,
        )
    try:
        verified_response = await _bounded_provider_call(
            provider,
            db,
            user_id,
            "GET",
            f"/products/{salla_id}",
            timeout_seconds=PRODUCT_PROVIDER_READ_TIMEOUT_SECONDS,
        )
    except _ProviderCallFailure as failure:
        return await _mark_verification_pending(db, verifying, failure), None

    verified_remote = _salla_product(verified_response)
    mismatches = _projection_mismatches(verified_remote, verification_projection)
    if mismatches:
        if not set(mismatches).issubset({"price", "sale_price", "cost_price"}):
            mismatch = await _update_attempt(db, user_id, attempt_id, {
                "stage": "verifying",
                "status": "rollback_required",
                "outcome": "mismatch_confirmed",
                "last_error_code": "salla_publish_verification_mismatch",
                "mismatches": mismatches,
                "verification_at": _now(),
                "retryability": False,
            })
            return mismatch, None
        rolled_back = await _rollback_after_explicit_mismatch(
            db,
            attempt=verifying,
            provider=provider,
            mismatches=mismatches,
        )
        return rolled_back, None
    return await _finalize_successful_publish(
        db,
        attempt=verifying,
        draft=draft,
        product=product,
        verified_remote=verified_remote,
    )


async def _claim_attempt_verification(
    db: Any, attempt: dict[str, Any],
) -> tuple[dict[str, Any], bool, str]:
    status = str(attempt.get("status") or "")
    if status not in {"verification_pending", "outcome_unknown"}:
        return attempt, False, status
    next_at = attempt.get("next_verification_at")
    if isinstance(next_at, datetime) and next_at > _now():
        return attempt, False, status
    token = uuid.uuid4().hex
    claimed = await db[ATTEMPTS].find_one_and_update(
        {
            "id": attempt.get("id"),
            "user_id": attempt.get("user_id"),
            "status": status,
        },
        {"$set": {
            "stage": "verifying",
            "status": "verifying",
            "execution_token": token,
            "execution_expires_at": _now() + timedelta(seconds=PUBLISH_EXECUTION_LEASE_SECONDS),
            "updated_at": _now(),
        }},
        return_document=ReturnDocument.AFTER,
    )
    return (claimed or attempt), bool(claimed and claimed.get("execution_token") == token), status


async def _verify_publish_attempt(
    db: Any,
    *,
    attempt: dict[str, Any],
    prior_status: str,
    draft: dict[str, Any],
    product: dict[str, Any],
    provider: Callable[..., Awaitable[dict[str, Any]]],
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    user_id = str(attempt["user_id"])
    attempt_id = str(attempt["id"])
    try:
        response = await _bounded_provider_call(
            provider,
            db,
            user_id,
            "GET",
            f"/products/{attempt['salla_product_id']}",
            timeout_seconds=PRODUCT_PROVIDER_READ_TIMEOUT_SECONDS,
        )
    except _ProviderCallFailure as failure:
        return await _mark_verification_pending(
            db,
            attempt,
            failure,
            preserve_unknown=prior_status == "outcome_unknown",
        ), None
    verified_remote = _salla_product(response)
    mismatches = _projection_mismatches(
        verified_remote,
        dict(
            attempt.get("verification_projection")
            or attempt.get("expected_projection")
            or {}
        ),
    )
    if mismatches:
        if prior_status == "outcome_unknown":
            pending = await _update_attempt(db, user_id, attempt_id, {
                "stage": "verifying",
                "status": "outcome_unknown",
                "outcome": "outcome_unknown",
                "last_error_code": "salla_publish_outcome_unknown",
                "mismatches": mismatches,
                "retryability": False,
                "verification_attempts": int(attempt.get("verification_attempts") or 0) + 1,
            })
            return pending, None
        mismatch = await _update_attempt(db, user_id, attempt_id, {
            "stage": "verifying",
            "status": "rollback_required",
            "outcome": "mismatch_confirmed",
            "last_error_code": "salla_publish_verification_mismatch",
            "mismatches": mismatches,
            "retryability": False,
            "verification_at": _now(),
            "verification_attempts": int(attempt.get("verification_attempts") or 0) + 1,
        })
        return mismatch, None
    return await _finalize_successful_publish(
        db,
        attempt=attempt,
        draft=draft,
        product=product,
        verified_remote=verified_remote,
    )


def make_product_control_center_router(db: Any, current_user: Callable[..., Any]) -> APIRouter:
    router = APIRouter(prefix="/products-v2", tags=["Product Control Center"])

    @router.get("/{product_id}/control-center")
    async def get_control_center(product_id: str, user: dict = Depends(current_user)) -> dict[str, Any]:
        await ensure_indexes(db)
        user_id = str(user["id"])
        product = await _product(db, user_id, product_id)
        salla_id = str(product["salla_product_id"])
        draft = await db[DRAFTS].find_one(
            {"user_id": user_id, "salla_product_id": salla_id, "status": {"$in": ACTIVE_DRAFT_STATUSES}},
            {"_id": 0}, sort=[("updated_at", -1)],
        )
        policy = await db[POLICIES].find_one({"user_id": user_id}, {"_id": 0}) or {
            "user_id": user_id, **DEFAULT_POLICY,
        }
        attempt = await _attempt_for_draft(db, user_id, str(draft["id"])) if draft else None
        return {
            "product": _serialize(product),
            "draft": _serialize(draft),
            "publish_attempt": _attempt_public(attempt),
            "policy": _serialize(policy),
            "protected_fields": sorted(PROTECTED_FIELDS),
            "capabilities": {
                "content": True, "seo": True, "categories": True,
                "images": True, "pricing": True, "options": True,
                "salla_cost_price": True,
                "rollback": True, "cost_engine_preserved": True,
            },
        }

    @router.put("/{product_id}/control-center/draft")
    async def save_draft(product_id: str, payload: dict = Body(...), user: dict = Depends(current_user)) -> dict[str, Any]:
        await ensure_indexes(db)
        user_id = str(user["id"])
        product = await _product(db, user_id, product_id)
        patch = _clean_patch(payload.get("changes") if isinstance(payload.get("changes"), dict) else payload)
        if not patch:
            raise HTTPException(status_code=422, detail={"code": "empty_product_change"})
        now = _now()
        salla_id = str(product["salla_product_id"])
        draft_id = uuid.uuid4().hex
        await supersede_active_drafts(db, user_id=user_id, salla_id=salla_id, keep_id=draft_id, now=now)
        row = {
            "id": draft_id,
            "user_id": user_id,
            "salla_product_id": salla_id,
            "mezan_product_id": product.get("mezan_product_id"),
            "status": "draft",
            "source": _text(payload.get("source")) or "human",
            "reason": _text(payload.get("reason")),
            "changes": patch,
            "before": {key: _before_value(product, key) for key in patch},
            "created_at": now,
            "updated_at": now,
        }
        await db[DRAFTS].insert_one(row)
        return {"ok": True, "draft": _serialize(row)}

    @router.post("/{product_id}/control-center/draft/{draft_id}/approve")
    async def approve_draft(product_id: str, draft_id: str, user: dict = Depends(current_user)) -> dict[str, Any]:
        user_id = str(user["id"])
        product = await _product(db, user_id, product_id)
        result = await db[DRAFTS].find_one_and_update(
            {"id": draft_id, "user_id": user_id, "salla_product_id": str(product["salla_product_id"]), "status": "draft"},
            {"$set": {"status": "approved", "approved_at": _now(), "updated_at": _now()}},
            return_document=True,
        )
        if not result:
            raise HTTPException(status_code=404, detail={"code": "draft_not_found"})
        await supersede_active_drafts(db, user_id=user_id, salla_id=str(product["salla_product_id"]), keep_id=draft_id)
        return {"ok": True, "draft": _serialize(result)}

    @router.post("/{product_id}/control-center/draft/{draft_id}/publish")
    async def publish_draft(product_id: str, draft_id: str, payload: dict = Body(default={}), user: dict = Depends(current_user)) -> Any:
        await ensure_indexes(db)
        user_id = str(user["id"])
        product = await _product(db, user_id, product_id)
        salla_id = str(product["salla_product_id"])
        draft = await db[DRAFTS].find_one({"id": draft_id, "user_id": user_id, "salla_product_id": salla_id}, {"_id": 0})
        if not draft:
            raise HTTPException(status_code=404, detail={"code": "draft_not_found"})
        existing_attempt = await _attempt_for_draft(db, user_id, draft_id)
        if draft.get("status") != "approved" and not existing_attempt:
            raise HTTPException(status_code=409, detail={"code": "draft_not_approved"})
        if existing_attempt and existing_attempt.get("status") == "succeeded":
            revision = await db[REVISIONS].find_one(
                {"id": existing_attempt.get("revision_id"), "user_id": user_id}, {"_id": 0},
            )
            return _attempt_response(existing_attempt, revision=revision)
        if not existing_attempt:
            newest = await db[DRAFTS].find_one(
                {"user_id": user_id, "salla_product_id": salla_id, "status": {"$in": ACTIVE_DRAFT_STATUSES}},
                {"_id": 0}, sort=[("updated_at", -1)],
            )
            if newest and newest.get("id") != draft_id:
                raise HTTPException(status_code=409, detail={"code": "draft_superseded", "newest_draft_id": newest.get("id")})
        if not existing_attempt and payload.get("confirmation") != "نشر التعديل إلى سلة":
            raise HTTPException(status_code=409, detail={"code": "publish_confirmation_required"})
        patch = _clean_patch(draft.get("changes") or {})
        if not _salla_payload(patch):
            raise HTTPException(status_code=422, detail={"code": "no_salla_publishable_fields"})
        attempt, owns_execution = await _acquire_publish_attempt(
            db,
            user_id=user_id,
            product=product,
            draft=draft,
            patch=patch,
        )
        if not owns_execution:
            revision = None
            if attempt and attempt.get("revision_id"):
                revision = await db[REVISIONS].find_one(
                    {"id": attempt.get("revision_id"), "user_id": user_id}, {"_id": 0},
                )
            return _attempt_response(attempt, revision=revision)
        completed, revision = await _execute_publish_attempt(
            db,
            attempt=attempt,
            draft=draft,
            product=product,
            provider=call_salla,
        )
        return _attempt_response(completed, revision=revision)

    @router.post("/{product_id}/control-center/publish-attempt/{attempt_id}/verify")
    async def verify_publish_attempt(product_id: str, attempt_id: str, user: dict = Depends(current_user)) -> Any:
        """Reconcile an ambiguous publish with GET only; never resend a PUT."""
        await ensure_indexes(db)
        user_id = str(user["id"])
        product = await _product(db, user_id, product_id)
        attempt = await db[ATTEMPTS].find_one(
            {
                "id": attempt_id,
                "user_id": user_id,
                "salla_product_id": str(product["salla_product_id"]),
            },
            {"_id": 0},
        )
        attempt = await _recover_stale_attempt(db, attempt)
        if not attempt:
            raise HTTPException(status_code=404, detail={"code": "publish_attempt_not_found"})
        if attempt.get("status") == "succeeded":
            revision = await db[REVISIONS].find_one(
                {"id": attempt.get("revision_id"), "user_id": user_id}, {"_id": 0},
            )
            return _attempt_response(attempt, revision=revision)
        claimed, owns_execution, prior_status = await _claim_attempt_verification(db, attempt)
        if not owns_execution:
            return _attempt_response(claimed)
        draft = await db[DRAFTS].find_one(
            {"id": str(attempt["draft_id"]), "user_id": user_id}, {"_id": 0},
        )
        if not draft:
            failed = await _update_attempt(db, user_id, attempt_id, {
                "stage": "verifying",
                "status": "rollback_required",
                "outcome": "local_state_missing",
                "last_error_code": "publish_draft_missing_during_verification",
                "retryability": False,
            })
            return _attempt_response(failed)
        verified, revision = await _verify_publish_attempt(
            db,
            attempt=claimed,
            prior_status=prior_status,
            draft=draft,
            product=product,
            provider=call_salla,
        )
        return _attempt_response(verified, revision=revision)

    @router.get("/{product_id}/control-center/history")
    async def history(product_id: str, user: dict = Depends(current_user)) -> dict[str, Any]:
        user_id = str(user["id"])
        product = await _product(db, user_id, product_id)
        rows = await db[REVISIONS].find({"user_id": user_id, "salla_product_id": str(product["salla_product_id"])}, {"_id": 0}).sort("created_at", -1).to_list(length=100)
        return {"items": [_serialize(row) for row in rows], "total": len(rows)}

    @router.put("/control-center/policy")
    async def save_policy(payload: dict = Body(...), user: dict = Depends(current_user)) -> dict[str, Any]:
        await ensure_indexes(db)
        user_id = str(user["id"])
        allowed = set(DEFAULT_POLICY)
        policy = {key: payload[key] for key in allowed if key in payload}
        policy["user_id"] = user_id
        policy["updated_at"] = _now()
        await db[POLICIES].update_one({"user_id": user_id}, {"$set": policy, "$setOnInsert": {"created_at": _now()}}, upsert=True)
        saved = await db[POLICIES].find_one({"user_id": user_id}, {"_id": 0})
        return {"ok": True, "policy": _serialize(saved)}

    return router
