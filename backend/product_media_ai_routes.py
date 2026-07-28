"""Governed AI media proposal jobs for Mezan Product V2.

This first delivery is deliberately proposal-only. It records bounded image
operations, permissions, provider readiness, and audit history without calling
an AI provider or publishing any media. The execution engine can be connected
later without changing the product-media governance flow.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from typing import Any, Callable

from fastapi import APIRouter, Body, Depends, HTTPException

from ai_store_operations_foundation import AI_ACTION_LOG, ROLE_ASSIGNMENTS
from product_media_draft_routes import MEDIA_DRAFTS
from product_v2_routes import PRODUCTS

AI_MEDIA_JOBS = "mezan_product_media_ai_jobs_v2"
MAX_PROMPT_LENGTH = 1200
PENDING_STATUSES = {"waiting_provider", "ready_for_execution", "proposal_created"}

OPERATION_CATALOG: dict[str, dict[str, Any]] = {
    "improve_quality": {
        "label": "تحسين الجودة والإضاءة",
        "permission": "products.media.ai_edit",
        "requires_source": True,
        "risk": "low",
    },
    "remove_background": {
        "label": "إزالة الخلفية",
        "permission": "products.media.ai_edit",
        "requires_source": True,
        "risk": "low",
    },
    "studio_background": {
        "label": "إنشاء خلفية استوديو",
        "permission": "products.media.ai_edit",
        "requires_source": True,
        "risk": "medium",
    },
    "ad_creative": {
        "label": "إنشاء صورة إعلانية",
        "permission": "products.media.ai_generate",
        "requires_source": True,
        "risk": "medium",
    },
    "generate_from_prompt": {
        "label": "إنشاء صورة من وصف",
        "permission": "products.media.ai_generate",
        "requires_source": False,
        "risk": "medium",
    },
    "suggest_alt": {
        "label": "اقتراح ALT للصورة",
        "permission": "products.media.ai_edit",
        "requires_source": True,
        "risk": "low",
    },
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _text(value: Any) -> str:
    return str(value or "").strip()


def image_provider_status() -> dict[str, Any]:
    key_present = bool(os.environ.get("OPENAI_API_KEY", "").strip())
    image_enabled = os.environ.get("MEZAN_AI_IMAGE_ENABLED", "").strip().lower() in {"1", "true", "yes", "on"}
    model = os.environ.get("MEZAN_OPENAI_IMAGE_MODEL", "").strip() or None
    ready = key_present and image_enabled and bool(model)
    if not key_present:
        state = "unconfigured"
    elif not image_enabled:
        state = "disabled_by_policy"
    elif not model:
        state = "model_not_configured"
    else:
        state = "ready"
    return {
        "provider": "openai",
        "state": state,
        "key_present": key_present,
        "image_execution_enabled": image_enabled,
        "model": model,
        "ready": ready,
        "execution_available": False,
        "mode": "proposal_only",
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


async def _available_source_urls(db: Any, user_id: str, product: dict[str, Any]) -> set[str]:
    urls = {_text(row.get("url")) for row in (product.get("images") or []) if isinstance(row, dict) and _text(row.get("url"))}
    draft = await db[MEDIA_DRAFTS].find_one({
        "user_id": user_id,
        "salla_product_id": str(product.get("salla_product_id")),
        "status": {"$in": ["draft", "approved"]},
    }, {"_id": 0}, sort=[("updated_at", -1)])
    for row in (draft or {}).get("images") or []:
        if isinstance(row, dict) and _text(row.get("url")):
            urls.add(_text(row.get("url")))
    return urls


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
        operations = []
        for key, spec in OPERATION_CATALOG.items():
            allowed = _has_permission(user, assignment, spec["permission"])
            operations.append({"key": key, **spec, "allowed": allowed})
        jobs = await db[AI_MEDIA_JOBS].find({
            "user_id": user_id,
            "salla_product_id": str(product.get("salla_product_id")),
        }, {"_id": 0}).sort("created_at", -1).limit(20).to_list(20)
        return {
            "ok": True,
            "mode": "proposal_only",
            "provider": image_provider_status(),
            "operations": operations,
            "jobs": jobs,
            "publish_from_ai": False,
            "human_approval_required": True,
        }

    @router.post("/{product_id}/media-ai/jobs")
    async def create_media_ai_job(product_id: str, payload: dict = Body(...), user: dict = Depends(current_user)) -> dict[str, Any]:
        user_id = str(user["id"])
        product = await _product(db, user_id, product_id)
        assignment = await _assignment(db, user_id)
        available_urls = await _available_source_urls(db, user_id, product)
        try:
            normalized = validate_ai_media_request(payload, available_urls)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail={"code": str(exc)}) from exc
        if not _has_permission(user, assignment, normalized["permission"]):
            raise HTTPException(status_code=403, detail={"code": "ai_media_permission_required", "permission": normalized["permission"]})

        provider = image_provider_status()
        status = "ready_for_execution" if provider["ready"] else "waiting_provider"
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
        await db[AI_MEDIA_JOBS].update_one({"id": job_id}, {"$set": {
            "status": "cancelled",
            "cancelled_by": user_id,
            "cancelled_at": now,
            "updated_at": now,
        }})
        job.update({"status": "cancelled", "cancelled_by": user_id, "cancelled_at": now, "updated_at": now})
        await _audit(db, user, "product_media_ai_job_cancelled", job_id, job)
        return {"ok": True, "job": job}

    return router
