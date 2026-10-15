"""Move completed AI media results into the ordinary governed media draft."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException, Request

from ai_store_access_contract import (
    find_role_assignment,
    role_assignment_owner_user_id,
)
from ai_store_operations_foundation import AI_ACTION_LOG
from product_media_ai_routes import AI_MEDIA_JOBS
from product_media_draft_routes import MEDIA_DRAFTS, media_diff, normalize_media_rows
from product_media_upload_routes import MEDIA_UPLOADS
from product_v2_routes import PRODUCTS


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _text(value: Any) -> str:
    return str(value or "").strip()


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


def _absolute_result_url(request: Request, value: str) -> str:
    url = _text(value)
    if url.startswith(("https://", "http://")):
        return url
    return f"{str(request.base_url).rstrip('/')}/{url.lstrip('/')}"


async def _audit(db: Any, user: dict[str, Any], job_id: str, draft: dict[str, Any]) -> None:
    await db[AI_ACTION_LOG].insert_one({
        "id": uuid.uuid4().hex,
        "actor_type": "human",
        "actor_id": str(user.get("id") or ""),
        "actor_name": user.get("name"),
        "action": "product_media_ai_result_added_to_draft",
        "target_type": "product_media_ai_job",
        "target_id": job_id,
        "before": None,
        "after": {"draft_id": draft["id"], "salla_product_id": draft["salla_product_id"]},
        "status": "completed",
        "occurred_at": _now(),
    })


def make_product_media_ai_draft_router(db: Any, current_user: Callable) -> APIRouter:
    router = APIRouter(prefix="/products-v2", tags=["Product V2 AI Media Drafts"])

    @router.post("/{product_id}/media-ai/jobs/{job_id}/add-to-draft")
    async def add_ai_result_to_draft(
        product_id: str,
        job_id: str,
        request: Request,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        user_id = str(user["id"])
        assignment = await find_role_assignment(
            db,
            owner_user_id=role_assignment_owner_user_id(user),
            user_id=user_id,
        )
        if not _has_permission(user, assignment, "products.media.edit"):
            raise HTTPException(status_code=403, detail={"code": "products_media_edit_required"})

        product = await _product(db, user_id, product_id)
        salla_product_id = str(product.get("salla_product_id"))
        job = await db[AI_MEDIA_JOBS].find_one({
            "id": job_id,
            "user_id": user_id,
            "salla_product_id": salla_product_id,
        }, {"_id": 0})
        if not job:
            raise HTTPException(status_code=404, detail={"code": "ai_media_job_not_found"})
        if job.get("status") != "completed" or job.get("added_to_draft_at"):
            raise HTTPException(status_code=409, detail={"code": "ai_media_result_not_available", "status": job.get("status")})

        active = await db[MEDIA_DRAFTS].find_one({
            "user_id": user_id,
            "salla_product_id": salla_product_id,
            "status": {"$in": ["draft", "approved"]},
        }, {"_id": 0}, sort=[("updated_at", -1)])
        if active and active.get("status") == "approved":
            raise HTTPException(status_code=409, detail={
                "code": "approved_media_draft_exists",
                "message": "انشر مسودة الصور المعتمدة أو راجعها قبل إضافة نتيجة جديدة.",
            })

        base_images = list((active or {}).get("images") or product.get("images") or [])
        result_image = job.get("result_image") if isinstance(job.get("result_image"), dict) else None
        result_alt = _text(job.get("result_alt"))
        if result_image:
            token = _text(result_image.get("upload_token"))
            exists = await db[MEDIA_UPLOADS].find_one({
                "token": token,
                "user_id": user_id,
                "salla_product_id": salla_product_id,
            }, {"_id": 0, "token": 1})
            if not token or not exists:
                raise HTTPException(status_code=410, detail={"code": "ai_result_image_expired"})
            if len(base_images) >= 10:
                raise HTTPException(status_code=409, detail={"code": "images_limit_reached"})
            if any(_text(row.get("upload_token")) == token for row in base_images if isinstance(row, dict)):
                raise HTTPException(status_code=409, detail={"code": "ai_result_already_in_draft"})
            appended = {
                **result_image,
                "url": _absolute_result_url(request, result_image.get("url")),
                "is_main": not bool(base_images),
                "alt": _text(result_image.get("alt")),
            }
            next_images = [*base_images, appended]
        elif result_alt:
            source_url = _text(job.get("source_image_url"))
            found = False
            next_images = []
            for row in base_images:
                item = dict(row)
                if _text(item.get("url")) == source_url:
                    item["alt"] = result_alt
                    found = True
                next_images.append(item)
            if not found:
                raise HTTPException(status_code=409, detail={"code": "source_image_no_longer_in_draft"})
        else:
            raise HTTPException(status_code=409, detail={"code": "ai_media_result_missing"})

        try:
            normalized = normalize_media_rows(next_images)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail={"code": str(exc)}) from exc

        claimed_at = _now()
        claim = await db[AI_MEDIA_JOBS].update_one({
            "id": job_id,
            "user_id": user_id,
            "status": "completed",
            "added_to_draft_at": {"$exists": False},
        }, {"$set": {"status": "adding_to_draft", "updated_at": claimed_at}})
        if int(getattr(claim, "matched_count", 0) or 0) != 1:
            raise HTTPException(status_code=409, detail={"code": "ai_media_result_already_claimed"})

        now = _now()
        if active:
            await db[MEDIA_DRAFTS].update_one({"id": active["id"]}, {"$set": {"status": "superseded", "superseded_at": now}})
        draft = {
            "id": uuid.uuid4().hex,
            "user_id": user_id,
            "salla_product_id": salla_product_id,
            "mezan_product_id": product.get("mezan_product_id") or product.get("id"),
            "status": "draft",
            "before_images": product.get("images") or [],
            "images": normalized,
            "diff": media_diff(product.get("images") or [], normalized),
            "reason": f"نتيجة ذكاء الصور: {_text(job.get('operation_label') or job.get('operation'))}"[:500],
            "ai_job_id": job_id,
            "created_by": user_id,
            "created_by_name": user.get("name"),
            "created_at": now,
            "updated_at": now,
        }
        try:
            await db[MEDIA_DRAFTS].insert_one(dict(draft))
            job_update = {
                "status": "added_to_draft",
                "added_to_draft_at": now,
                "media_draft_id": draft["id"],
                "updated_at": now,
            }
            await db[AI_MEDIA_JOBS].update_one({"id": job_id, "user_id": user_id}, {"$set": job_update})
        except Exception:
            await db[AI_MEDIA_JOBS].update_one({"id": job_id, "user_id": user_id}, {
                "$set": {"status": "completed", "updated_at": _now()},
                "$unset": {"added_to_draft_at": "", "media_draft_id": ""},
            })
            raise
        job.update(job_update)
        await _audit(db, user, job_id, draft)
        return {
            "ok": True,
            "job": job,
            "draft": draft,
            "direct_publish": False,
            "next_step": "review_and_approve_media_draft",
        }

    return router
