"""Verified Mezan -> Salla Google taxonomy publishing through the linked app.

Salla's public Update Product documentation does not advertise a writable Google
taxonomy field.  This route therefore fails closed: it isolates the provider
write, immediately re-reads Product Details, and records ``synced`` only when
Salla returns the exact taxonomy selected in Mezan.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from fastapi import APIRouter, Body, Depends, HTTPException

from product_google_taxonomy_support import (
    extract_google_taxonomy,
    google_taxonomy_matches,
)
from product_v2_routes import PRODUCTS
from salla_integration.service import SallaError, call_salla


PUBLISH_CONFIRMATION = "نشر تصنيفات Google المعتمدة إلى سلة"
PROBE_CONFIRMATION = "فحص نشر تصنيف Google إلى سلة"
MAX_BATCH = 200
PROVIDER_FIELD = "google_product_category"
AI_ACTION_LOG = "mezan_ai_action_log_v2"
APPROVED_SOURCES = (
    "openai_pilot_human_approved",
    "human_review_approved",
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _text(value: Any) -> str:
    return " ".join(str(value or "").strip().split())


def _eligible_filter(user_id: str) -> dict[str, Any]:
    return {
        "user_id": user_id,
        "salla_product_id": {"$nin": [None, ""]},
        "google_category": {"$nin": [None, ""]},
        "classification_source": {"$in": list(APPROVED_SOURCES)},
        "salla_sync_status": {"$ne": "synced"},
    }


async def _record_result(
    db: Any,
    *,
    user_id: str,
    product: dict[str, Any],
    expected: str,
    before_remote: Any,
    after_remote: Any,
    attempted_write: bool,
    verified: bool,
    error: str | None,
) -> None:
    now = _now()
    salla_id = str(product.get("salla_product_id") or "")
    actual = extract_google_taxonomy(after_remote if after_remote is not None else before_remote)
    status = "synced" if verified else "failed"
    await db[PRODUCTS].update_one(
        {"user_id": user_id, "salla_product_id": salla_id},
        {"$set": {
            "salla_sync_status": status,
            "salla_synced_at": now if verified else None,
            "last_verified_at": now,
            "salla_google_taxonomy": actual,
            "salla_sync_error": error,
            "salla_sync_reason": None if verified else "google_taxonomy_readback_mismatch",
            "google_taxonomy_authority": "salla" if verified else "mezan",
            "updated_at": now,
        }},
    )
    await db[AI_ACTION_LOG].insert_one({
        "id": uuid.uuid4().hex,
        "user_id": user_id,
        "actor_user_id": user_id,
        "action": "google_taxonomy_publish_to_salla",
        "risk": "low",
        "source": "ai_product_manager_google_taxonomy_publish",
        "mezan_product_id": product.get("mezan_product_id") or product.get("id"),
        "salla_product_id": salla_id,
        "expected_google_taxonomy": expected,
        "actual_google_taxonomy": actual,
        "provider_field": PROVIDER_FIELD,
        "provider_write_reached": attempted_write,
        "verified": verified,
        "error": error,
        "occurred_at": now,
    })


async def publish_google_taxonomy_batch(
    db: Any,
    *,
    user_id: str,
    limit: int,
    stop_on_failure: bool,
    provider_call: Callable[..., Awaitable[dict[str, Any]]] = call_salla,
) -> dict[str, Any]:
    bounded_limit = max(1, min(int(limit), MAX_BATCH))
    rows = await db[PRODUCTS].find(
        _eligible_filter(user_id),
        {
            "_id": 0,
            "id": 1,
            "mezan_product_id": 1,
            "salla_product_id": 1,
            "name": 1,
            "google_category": 1,
            "google_category_id": 1,
            "google_category_path": 1,
        },
    ).sort("classified_at", 1).to_list(length=bounded_limit)

    result: dict[str, Any] = {
        "selected": len(rows),
        "attempted": 0,
        "synced": 0,
        "already_matched": 0,
        "failed": 0,
        "stopped_early": False,
        "provider_field": PROVIDER_FIELD,
        "items": [],
    }
    for product in rows:
        salla_id = _text(product.get("salla_product_id"))
        expected = _text(product.get("google_category_id") or product.get("google_category"))
        item = {
            "mezan_product_id": product.get("mezan_product_id") or product.get("id"),
            "salla_product_id": salla_id,
            "name": product.get("name"),
            "expected": expected,
        }
        before_remote = after_remote = None
        attempted = verified = False
        error: str | None = None
        try:
            before_remote = await provider_call(db, user_id, "GET", f"/products/{salla_id}")
            if google_taxonomy_matches(expected, before_remote):
                verified = True
                result["already_matched"] += 1
            else:
                attempted = True
                result["attempted"] += 1
                await provider_call(
                    db,
                    user_id,
                    "PUT",
                    f"/products/{salla_id}",
                    json={PROVIDER_FIELD: expected},
                )
                after_remote = await provider_call(db, user_id, "GET", f"/products/{salla_id}")
                verified = google_taxonomy_matches(expected, after_remote)
                if not verified:
                    error = "google_taxonomy_readback_mismatch"
        except SallaError as exc:
            error = f"salla_{int(exc.status_code or 500)}: {_text(exc)}"
        except Exception as exc:  # keep the batch auditable without leaking credentials
            error = f"provider_error: {_text(exc)}"

        await _record_result(
            db,
            user_id=user_id,
            product=product,
            expected=expected,
            before_remote=before_remote,
            after_remote=after_remote,
            attempted_write=attempted,
            verified=verified,
            error=error,
        )
        item.update({
            "attempted": attempted,
            "verified": verified,
            "actual": extract_google_taxonomy(after_remote if after_remote is not None else before_remote),
            "error": error,
        })
        result["items"].append(item)
        if verified:
            result["synced"] += 1
        else:
            result["failed"] += 1
            if stop_on_failure:
                result["stopped_early"] = True
                break
    return result


def make_product_google_taxonomy_salla_publish_router(db: Any, current_user: Callable[..., Any]) -> APIRouter:
    router = APIRouter(
        prefix="/ai-store-operations/product-intelligence/google-taxonomy/salla-publish",
        tags=["AI Store Operations"],
    )

    @router.get("/preview")
    async def preview(user: dict = Depends(current_user)) -> dict[str, Any]:
        user_id = str(user["id"])
        eligible = await db[PRODUCTS].count_documents(_eligible_filter(user_id))
        return {
            "ok": True,
            "eligible": eligible,
            "provider_field": PROVIDER_FIELD,
            "probe_confirmation": PROBE_CONFIRMATION,
            "publish_confirmation": PUBLISH_CONFIRMATION,
        }

    @router.post("/probe")
    async def probe(payload: dict = Body(...), user: dict = Depends(current_user)) -> dict[str, Any]:
        if payload.get("confirmation") != PROBE_CONFIRMATION:
            raise HTTPException(status_code=409, detail={"code": "taxonomy_salla_probe_confirmation_required"})
        result = await publish_google_taxonomy_batch(
            db,
            user_id=str(user["id"]),
            limit=1,
            stop_on_failure=True,
        )
        return {"ok": result["failed"] == 0, "result": result}

    @router.post("")
    async def publish(payload: dict = Body(...), user: dict = Depends(current_user)) -> dict[str, Any]:
        if payload.get("confirmation") != PUBLISH_CONFIRMATION:
            raise HTTPException(status_code=409, detail={"code": "taxonomy_salla_publish_confirmation_required"})
        result = await publish_google_taxonomy_batch(
            db,
            user_id=str(user["id"]),
            limit=int(payload.get("limit") or MAX_BATCH),
            stop_on_failure=bool(payload.get("stop_on_failure", False)),
        )
        return {"ok": result["failed"] == 0, "result": result}

    return router
