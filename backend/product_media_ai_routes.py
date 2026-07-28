"""Governed AI media proposal jobs for Mezan Product V2.

This first delivery is deliberately proposal-only. It records bounded image
operations, permissions, provider readiness, and audit history without calling
an AI provider or publishing any media. The execution engine can be connected
later without changing the product-media governance flow.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Callable

from fastapi import APIRouter, Body, Depends, HTTPException

from ai_provider_status import openai_runtime_status
from ai_store_operations_foundation import AI_ACTION_LOG, ROLE_ASSIGNMENTS
from product_media_draft_routes import MEDIA_DRAFTS
from product_v2_routes import PRODUCTS

AI_MEDIA_JOBS = "mezan_product_media_ai_jobs_v2"
MAX_PROMPT_LENGTH = 1200
PENDING_STATUSES = {
    "waiting_provider",
    "waiting_image_engine",
    "ready_for_execution",
    "proposal_created",
}

OPERATION_CATALOG: dict[str, dict[str, Any]] = {
    "improve_quality": {"label": "تحسين الجودة والإضاءة", "permission": "products.media.ai_edit", "requires_source": True, "risk": "low"},
    "remove_background": {"label": "إزالة الخلفية", "permission": "products.media.ai_edit", "requires_source": True, "risk": "low"},
    "studio_background": {"label": "إنشاء خلفية استوديو", "permission": "products.media.ai_edit", "requires_source": True, "risk": "medium"},
    "ad_creative": {"label": "إنشاء صورة إعلانية", "permission": "products.media.ai_generate", "requires_source": True, "risk": "medium"},
    "generate_from_prompt": {"label": "إنشاء صورة من وصف", "permission": "products.media.ai_generate", "requires_source": False, "risk": "medium"},
    "suggest_alt": {"label": "اقتراح ALT للصورة", "permission": "products.media.ai_edit", "requires_source": True, "risk": "low"},
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _text(value: Any) -> str:
    return str(value or "").strip()


def image_provider_status() -> dict[str, Any]:
    runtime = openai_runtime_status()
    images = runtime["images"]
    return {
        "provider": "openai",
        "connected": runtime["connected"],
        "connection_status": runtime["connection_status"],
        "state": runtime["state"],
        "label_ar": runtime["label_ar"],
        "analysis_ready": runtime["analysis"]["ready"],
        "analysis_model": runtime["analysis"]["model"],
        "image_policy_enabled": images["policy_enabled"],
        "image_model": images["model"],
        "ready": images["ready"],
        "execution_available": False,
        "mode": "proposal_only",
        "human_approval_required": True,
        "direct_publish_allowed": False,
    }


def _has_permission(user: dict[str, Any], assignment: dict[str, Any] | None, permission: str) -> bool:
    if str(user.get("role") or "").lower() == "owner" or user.get("is_owner"):
        return True
    if not assignment or assignment.get("enabled") is False:
        return False
    return permission in set(assignment.get("effective_permissions") or [])


async def _product(db: Any, user_id: str, product_id: str) -> dict[str, Any]:
    row = await db[PRODUCTS].find_one({
        "user_id": user_id,
        "$or": [{"id": product_id}, {"mezan_product_id": product_id}, {"salla_product_id": product_id}],
    }, {"_id": 0})
    if not row:
        raise HTTPException(status_code=404, detail={"code": "product_v2_not_found"})
    return row


async def _assignment(db: Any, user_id: str) -> dict[str, Any] | None:
    return await db[ROLE_ASSIGNMENTS].find_one({"user_id": user_id}, {"_id": 0})


def _safe_source_row(raw: Any, index: int) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    url = _text(raw.get("url"))
    if not url:
        return None
    return {
        "id": _text(raw.get("id")) or None,
        "url": url,
        "alt": _text(raw.get("alt"))[:250],
        "is_main": bool(raw.get("is_main") or raw.get("main") or index == 0),
        "source": _text(raw.get("source")) or ("salla" if raw.get("id") else "external_url"),
    }


async def _available_source_rows(db: Any, user_id: str, product: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add_many(values: Any) -> None:
        for index, raw in enumerate(values if isinstance(values, list) else []):
            row = _safe_source_row(raw, index)
            if not row or row["url"] in seen:
                continue
            seen.add(row["url"])
            rows.append(row)

    add_many(product.get("images") or [])
    draft = await db[MEDIA_DRAFTS].find_one({
        "user_id": user_id,
        "salla_product_id": str(product.get("salla_product_id")),
        "status": {"$in": ["draft", "approved"]},
    }, {"_id": 0}, sort=[("updated_at", -1)])
    add_many((draft or {}).get("images") or [])
    return rows[:10]


def validate_ai_media_request(payload: dict[str, Any], available_urls: set[str]) -> dict[str, Any]:
    operation = _text(payload.get("operation"))
    spec = OPERATION_CATALOG.get(operation)
    if not spec:
        raise ValueError("invalid_ai_media_operation")
    prompt = _text(payload.get("prompt"))
    if len(prompt) > MAX_PROMPT_LENGTH:
        raise ValueError("ai_media_prompt_too_long")
    source_url = _text(payload.get("source_image_url")) or None
    if spec["requires_source"] and not source_url:
        raise ValueError("source_image_required")
    if source_url and source_url not in available_urls:
        raise ValueError("source_image_not_owned_by_product")
    aspect_ratio = _text(payload.get("aspect_ratio")) or "original"
    if aspect_ratio not in {"original", "1:1", "4:5", "9:16", "16:9"}:
        raise ValueError("invalid_aspect_ratio")
    return {
        "operation": operation,
        "prompt": prompt,
        "source_image_url": source_url,
        "aspect_ratio": aspect_ratio,
        "permission": spec["permission"],
        "risk": spec["risk"],
        "operation_label": spec["label"],
    }


async def _audit(db: Any, user: dict[str, Any], action: str, target_id: str, after: Any) -> None:
    await db[AI_ACTION_LOG].insert_one({
        "id": uuid.uuid4().hex,
        "actor_type": "human",
        "actor_id": str(user.get("id") or ""),
        "actor_name": user.get("name"),
        "action": action,
        "target_type": "product_media_ai_job",
        "target_id": target_id,
        "before": None,
        "after": after,
        "status": "completed",
        "occurred_at": _now(),
    })


def make_product_media_ai_router(db: Any, current_user: Callable) -> APIRouter:
    router = APIRouter(prefix="/products-v2", tags=["Product V2 AI Media"])

    @router.get("/{product_id}/media-ai")
    async def media_ai_state(product_id: str, user: dict = Depends(current_user)) -> dict[str, Any]:
        user_id = str(user["id"])
        product = await _product(db, user_id, product_id)
        assignment = await _assignment(db, user_id)
        operations = [{"key": key, **spec, "allowed": _has_permission(user, assignment, spec["permission"])} for key, spec in OPERATION_CATALOG.items()]
        jobs = await db[AI_MEDIA_JOBS].find({
            "user_id": user_id,
            "salla_product_id": str(product.get("salla_product_id")),
        }, {"_id": 0}).sort("created_at", -1).limit(20).to_list(20)
        source_images = await _available_source_rows(db, user_id, product)
        return {
            "ok": True,
            "mode": "proposal_only",
            "provider": image_provider_status(),
            "operations": operations,
            "source_images": source_images,
            "jobs": jobs,
            "publish_from_ai": False,
            "human_approval_required": True,
        }

    @router.post("/{product_id}/media-ai/jobs")
    async def create_media_ai_job(product_id: str, payload: dict = Body(...), user: dict = Depends(current_user)) -> dict[str, Any]:
        user_id = str(user["id"])
        product = await _product(db, user_id, product_id)
        assignment = await _assignment(db, user_id)
        source_rows = await _available_source_rows(db, user_id, product)
        try:
            normalized = validate_ai_media_request(payload, {row["url"] for row in source_rows})
        except ValueError as exc:
            raise HTTPException(status_code=422, detail={"code": str(exc)}) from exc
        if not _has_permission(user, assignment, normalized["permission"]):
            raise HTTPException(status_code=403, detail={"code": "ai_media_permission_required", "permission": normalized["permission"]})

        provider = image_provider_status()
        status = "waiting_provider" if not provider["connected"] else "waiting_image_engine" if not provider["ready"] else "ready_for_execution"
        now = _now()
        job = {
            "id": uuid.uuid4().hex,
            "user_id": user_id,
            "salla_product_id": str(product.get("salla_product_id")),
            "mezan_product_id": product.get("mezan_product_id") or product.get("id"),
            "product_name": product.get("name"),
            **normalized,
            "status": status,
            "provider_snapshot": provider,
            "execution_mode": "proposal_only",
            "execution_attempted": False,
            "result_image": None,
            "publish_allowed": False,
            "human_approval_required": True,
            "created_by": user_id,
            "created_by_name": user.get("name"),
            "created_at": now,
            "updated_at": now,
        }
        await db[AI_MEDIA_JOBS].insert_one(dict(job))
        await _audit(db, user, "product_media_ai_job_created", job["id"], job)
        return {"ok": True, "job": job, "provider": provider}

    @router.post("/{product_id}/media-ai/jobs/{job_id}/cancel")
    async def cancel_media_ai_job(product_id: str, job_id: str, user: dict = Depends(current_user)) -> dict[str, Any]:
        user_id = str(user["id"])
        product = await _product(db, user_id, product_id)
        job = await db[AI_MEDIA_JOBS].find_one({
            "id": job_id,
            "user_id": user_id,
            "salla_product_id": str(product.get("salla_product_id")),
        }, {"_id": 0})
        if not job:
            raise HTTPException(status_code=404, detail={"code": "ai_media_job_not_found"})
        if job.get("status") not in PENDING_STATUSES:
            raise HTTPException(status_code=409, detail={"code": "ai_media_job_not_cancellable"})
        now = _now()
        update = {"status": "cancelled", "cancelled_by": user_id, "cancelled_at": now, "updated_at": now}
        await db[AI_MEDIA_JOBS].update_one({"id": job_id}, {"$set": update})
        job.update(update)
        await _audit(db, user, "product_media_ai_job_cancelled", job_id, job)
        return {"ok": True, "job": job}

    return router
