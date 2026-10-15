"""Governed Product V2 media drafts and Salla publishing.

The media editor never writes to Salla while a user is editing. It stores one
active draft per product, requires approval, then publishes image metadata and
URL/file additions through Salla's Product Images endpoints. Every publish
stores before/after snapshots for audit and rollback preparation.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Callable
from urllib.parse import unquote, urlparse

import httpx
from fastapi import APIRouter, Body, Depends, HTTPException

from ai_store_access_contract import (
    find_role_assignment,
    role_assignment_owner_user_id,
)
from ai_store_operations_foundation import AI_ACTION_LOG
from product_media_upload_routes import MEDIA_UPLOADS, delete_temp_uploads
from product_v2_routes import PRODUCTS
from salla_integration.service import SALLA_API_BASE, SallaError, ensure_fresh_access_token

MEDIA_DRAFTS = "mezan_product_media_drafts_v2"
MEDIA_VERSIONS = "mezan_product_media_versions_v2"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _now_dt() -> datetime:
    return datetime.now(timezone.utc)


def _text(value: Any) -> str:
    return str(value or "").strip()


def upload_token_from_row(row: dict[str, Any]) -> str | None:
    explicit = _text(row.get("upload_token"))
    if explicit:
        return explicit
    try:
        path = unquote(urlparse(_text(row.get("url"))).path).rstrip("/")
    except Exception:
        return None
    marker = "/media-upload/file/"
    if marker not in path:
        return None
    token = path.rsplit(marker, 1)[-1].strip("/")
    return token or None


def normalize_media_rows(rows: Any) -> list[dict[str, Any]]:
    if not isinstance(rows, list):
        raise ValueError("images_must_be_list")
    if not 1 <= len(rows) <= 10:
        raise ValueError("images_count_must_be_between_1_and_10")
    result: list[dict[str, Any]] = []
    seen_keys: set[str] = set()
    main_count = 0
    for index, raw in enumerate(rows):
        if not isinstance(raw, dict):
            raise ValueError("invalid_image_row")
        image_id = _text(raw.get("id")) or None
        url = _text(raw.get("url"))
        if not url.startswith(("https://", "http://")):
            raise ValueError("image_url_must_be_http")
        inferred_token = upload_token_from_row(raw)
        source = "salla" if image_id else (_text(raw.get("source")) or ("temporary_upload" if inferred_token else "external_url"))
        upload_token = inferred_token if source == "temporary_upload" else None
        if source == "temporary_upload" and not upload_token:
            raise ValueError("temporary_upload_token_required")
        if source not in {"salla", "external_url", "temporary_upload"}:
            raise ValueError("invalid_image_source")
        key = image_id or upload_token or url.split("?", 1)[0]
        if key in seen_keys:
            raise ValueError("duplicate_image")
        seen_keys.add(key)
        is_main = bool(raw.get("is_main"))
        if is_main:
            main_count += 1
        result.append({
            "id": image_id,
            "url": url,
            "alt": _text(raw.get("alt"))[:250],
            "is_main": is_main,
            "sort": index + 1,
            "source": source,
            "upload_token": upload_token,
            "filename": _text(raw.get("filename"))[:180] or None,
        })
    if main_count != 1:
        raise ValueError("exactly_one_main_image_required")
    return result


def media_diff(before: list[dict[str, Any]], after: list[dict[str, Any]]) -> dict[str, Any]:
    before_ids = {str(row.get("id")): row for row in before if row.get("id")}
    after_ids = {str(row.get("id")): row for row in after if row.get("id")}
    added = [row for row in after if not row.get("id")]
    removed = [row for key, row in before_ids.items() if key not in after_ids]
    updated = []
    for key, row in after_ids.items():
        old = before_ids.get(key)
        if old and any(old.get(field) != row.get(field) for field in ("alt", "is_main", "sort", "url")):
            updated.append({"before": old, "after": row})
    return {"added": added, "removed": removed, "updated": updated}


def _has_media_permission(user: dict[str, Any], assignment: dict[str, Any] | None, permission: str) -> bool:
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


async def _assignment(db: Any, user: dict[str, Any]) -> dict[str, Any] | None:
    return await find_role_assignment(
        db,
        owner_user_id=role_assignment_owner_user_id(user),
        user_id=str(user.get("id") or ""),
    )


async def _salla_multipart(
    db: Any,
    user_id: str,
    method: str,
    path: str,
    fields: dict[str, Any] | None = None,
    *,
    photo: tuple[str, bytes, str] | None = None,
) -> dict[str, Any]:
    token = await ensure_fresh_access_token(db, user_id, recover_needs_reauth=True)
    multipart: dict[str, tuple[Any, ...]] = {key: (None, str(value)) for key, value in (fields or {}).items() if value is not None}
    if photo is not None:
        filename, content, content_type = photo
        multipart["photo"] = (filename, content, content_type)
    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0)) as client:
        response = await client.request(
            method,
            f"{SALLA_API_BASE}{path}",
            headers={"Authorization": f"Bearer {token}"},
            files=multipart or None,
        )
    if response.status_code >= 400:
        raise SallaError(f"Salla {method} {path} → {response.status_code}: {response.text[:500]}", status_code=response.status_code)
    if not response.content:
        return {"status": response.status_code, "success": True}
    try:
        return response.json()
    except Exception:
        return {"status": response.status_code, "success": True, "raw": response.text[:500]}


def published_image_from_response(response: dict[str, Any], fallback: dict[str, Any]) -> dict[str, Any]:
    data = response.get("data") if isinstance(response, dict) else {}
    if not isinstance(data, dict):
        data = {}
    image = data.get("image")
    if not isinstance(image, dict):
        image = {}
    original = image.get("original")
    cdn_url = _text(original.get("url")) if isinstance(original, dict) else _text(original)
    cdn_url = cdn_url or _text(data.get("url")) or _text(fallback.get("url"))
    return {
        "id": _text(data.get("id") or fallback.get("id")) or None,
        "url": cdn_url,
        "alt": _text(data.get("alt_seo") or data.get("alt") or fallback.get("alt"))[:250],
        "is_main": bool(data.get("default") if data.get("default") is not None else fallback.get("is_main")),
        "sort": int(data.get("sort") or fallback.get("sort") or 1),
        "source": "salla",
        "upload_token": None,
        "filename": None,
    }


async def _temporary_upload(db: Any, user_id: str, salla_product_id: str, token: str) -> dict[str, Any]:
    row = await db[MEDIA_UPLOADS].find_one({
        "token": token,
        "user_id": user_id,
        "salla_product_id": salla_product_id,
        "expires_at": {"$gt": _now_dt()},
    })
    if not row:
        raise SallaError("Temporary product image is missing or expired. Upload it again before publishing.", status_code=410)
    return row


async def _audit(db: Any, user: dict[str, Any], action: str, target_id: str, before: Any, after: Any, status: str = "completed") -> None:
    await db[AI_ACTION_LOG].insert_one({
        "id": uuid.uuid4().hex,
        "actor_type": "human",
        "actor_id": str(user.get("id") or ""),
        "actor_name": user.get("name"),
        "action": action,
        "target_type": "product_media",
        "target_id": target_id,
        "before": before,
        "after": after,
        "status": status,
        "occurred_at": _now(),
    })


def make_product_media_draft_router(db: Any, current_user: Callable) -> APIRouter:
    router = APIRouter(prefix="/products-v2", tags=["Product V2 Media Drafts"])

    @router.get("/{product_id}/media-control")
    async def media_control(product_id: str, user: dict = Depends(current_user)) -> dict[str, Any]:
        product = await _product(db, str(user["id"]), product_id)
        assignment = await _assignment(db, user)
        draft = await db[MEDIA_DRAFTS].find_one({
            "user_id": str(user["id"]),
            "salla_product_id": str(product["salla_product_id"]),
            "status": {"$in": ["draft", "approved"]},
        }, {"_id": 0}, sort=[("updated_at", -1)])
        return {
            "ok": True,
            "product": {"id": product.get("id"), "salla_product_id": str(product["salla_product_id"]), "name": product.get("name")},
            "current_images": product.get("images") or [],
            "draft": draft,
            "permissions": {
                "edit": _has_media_permission(user, assignment, "products.media.edit"),
                "approve": _has_media_permission(user, assignment, "products.approve"),
                "publish": _has_media_permission(user, assignment, "products.media.publish"),
            },
        }

    @router.put("/{product_id}/media-draft")
    async def save_media_draft(product_id: str, payload: dict = Body(...), user: dict = Depends(current_user)) -> dict[str, Any]:
        user_id = str(user["id"])
        assignment = await _assignment(db, user)
        if not _has_media_permission(user, assignment, "products.media.edit"):
            raise HTTPException(status_code=403, detail={"code": "products_media_edit_required"})
        product = await _product(db, user_id, product_id)
        try:
            images = normalize_media_rows(payload.get("images"))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail={"code": str(exc)}) from exc
        salla_product_id = str(product["salla_product_id"])
        for row in images:
            if row.get("source") != "temporary_upload":
                continue
            upload = await db[MEDIA_UPLOADS].find_one({
                "token": row.get("upload_token"),
                "user_id": user_id,
                "salla_product_id": salla_product_id,
                "expires_at": {"$gt": _now_dt()},
            }, {"_id": 0, "token": 1})
            if not upload:
                raise HTTPException(status_code=422, detail={"code": "temporary_image_missing_or_expired"})
        current = product.get("images") or []
        now = _now()
        previous = await db[MEDIA_DRAFTS].find_one({
            "user_id": user_id,
            "salla_product_id": salla_product_id,
            "status": {"$in": ["draft", "approved"]},
        }, {"_id": 0}, sort=[("updated_at", -1)])
        if previous:
            await db[MEDIA_DRAFTS].update_many({
                "user_id": user_id,
                "salla_product_id": salla_product_id,
                "status": {"$in": ["draft", "approved"]},
            }, {"$set": {"status": "superseded", "superseded_at": now}})
        draft = {
            "id": uuid.uuid4().hex,
            "user_id": user_id,
            "salla_product_id": salla_product_id,
            "mezan_product_id": product.get("mezan_product_id") or product.get("id"),
            "status": "draft",
            "before_images": current,
            "images": images,
            "diff": media_diff(current, images),
            "reason": _text(payload.get("reason"))[:500],
            "created_by": user_id,
            "created_by_name": user.get("name"),
            "created_at": now,
            "updated_at": now,
        }
        await db[MEDIA_DRAFTS].insert_one(dict(draft))
        await _audit(db, user, "product_media_draft_saved", salla_product_id, current, draft)
        return {"ok": True, "draft": draft}

    @router.post("/{product_id}/media-draft/{draft_id}/approve")
    async def approve_media_draft(product_id: str, draft_id: str, user: dict = Depends(current_user)) -> dict[str, Any]:
        user_id = str(user["id"])
        assignment = await _assignment(db, user)
        if not _has_media_permission(user, assignment, "products.approve"):
            raise HTTPException(status_code=403, detail={"code": "products_approve_required"})
        product = await _product(db, user_id, product_id)
        salla_product_id = str(product["salla_product_id"])
        draft = await db[MEDIA_DRAFTS].find_one({
            "id": draft_id,
            "user_id": user_id,
            "salla_product_id": salla_product_id,
            "status": "draft",
        }, {"_id": 0})
        if not draft:
            raise HTTPException(status_code=404, detail={"code": "media_draft_not_found_or_not_draft"})
        now = _now()
        update = {
            "status": "approved",
            "approved_by": user_id,
            "approved_by_name": user.get("name"),
            "approved_at": now,
            "updated_at": now,
        }
        await db[MEDIA_DRAFTS].update_one({"id": draft_id}, {"$set": update})
        draft.update(update)
        await _audit(db, user, "product_media_draft_approved", salla_product_id, None, draft)
        return {"ok": True, "draft": draft}

    @router.post("/{product_id}/media-draft/{draft_id}/publish")
    async def publish_media_draft(product_id: str, draft_id: str, user: dict = Depends(current_user)) -> dict[str, Any]:
        user_id = str(user["id"])
        assignment = await _assignment(db, user)
        if not _has_media_permission(user, assignment, "products.media.publish"):
            raise HTTPException(status_code=403, detail={"code": "products_media_publish_required"})
        product = await _product(db, user_id, product_id)
        salla_product_id = str(product["salla_product_id"])
        draft = await db[MEDIA_DRAFTS].find_one({
            "id": draft_id,
            "user_id": user_id,
            "salla_product_id": salla_product_id,
            "status": "approved",
        }, {"_id": 0})
        if not draft:
            raise HTTPException(status_code=404, detail={"code": "approved_media_draft_not_found"})
        before = draft.get("before_images") or []
        after = draft.get("images") or []
        diff = media_diff(before, after)
        published_images: list[dict[str, Any]] = []
        cleanup_tokens: list[str] = []
        try:
            for removed in diff["removed"]:
                await _salla_multipart(db, user_id, "DELETE", f"/products/images/{removed['id']}")
            for row in after:
                fields = {
                    "default": 1 if row.get("is_main") else 0,
                    "sort": row.get("sort"),
                    "alt": row.get("alt") or "",
                }
                if row.get("id"):
                    response = await _salla_multipart(db, user_id, "POST", f"/products/images/{row['id']}", fields)
                    published_images.append(published_image_from_response(response, row))
                    continue
                temp_token = upload_token_from_row(row)
                if temp_token:
                    upload = await _temporary_upload(db, user_id, salla_product_id, temp_token)
                    response = await _salla_multipart(
                        db,
                        user_id,
                        "POST",
                        f"/products/{salla_product_id}/images",
                        fields,
                        photo=(
                            _text(upload.get("filename")) or "product-image.jpg",
                            bytes(upload["content"]),
                            _text(upload.get("content_type")) or "image/jpeg",
                        ),
                    )
                    cleanup_tokens.append(temp_token)
                else:
                    response = await _salla_multipart(
                        db,
                        user_id,
                        "POST",
                        f"/products/{salla_product_id}/images",
                        {"original": row["url"], **fields},
                    )
                published_images.append(published_image_from_response(response, row))
        except SallaError as exc:
            await db[MEDIA_DRAFTS].update_one({"id": draft_id}, {"$set": {"last_error": str(exc), "last_error_at": _now()}})
            await _audit(db, user, "product_media_publish_failed", salla_product_id, before, after, status="failed")
            raise HTTPException(status_code=exc.status_code or 502, detail={"code": "salla_media_publish_failed", "message": str(exc)}) from exc
        now = _now()
        version = {
            "id": uuid.uuid4().hex,
            "user_id": user_id,
            "salla_product_id": salla_product_id,
            "before_images": before,
            "after_images": published_images,
            "draft_id": draft_id,
            "published_by": user_id,
            "published_by_name": user.get("name"),
            "published_at": now,
        }
        await db[MEDIA_VERSIONS].insert_one(dict(version))
        await db[MEDIA_DRAFTS].update_one({"id": draft_id}, {"$set": {
            "status": "published",
            "published_at": now,
            "published_by": user_id,
            "published_images": published_images,
            "updated_at": now,
        }})
        await db[PRODUCTS].update_one({
            "user_id": user_id,
            "salla_product_id": salla_product_id,
        }, {"$set": {
            "images": published_images,
            "main_image": next((row["url"] for row in published_images if row.get("is_main")), published_images[0]["url"]),
            "details_synced_at": now,
            "updated_at": now,
        }})
        await delete_temp_uploads(db, cleanup_tokens)
        await _audit(db, user, "product_media_published", salla_product_id, before, published_images)
        return {"ok": True, "version": version, "published_images": published_images}

    return router
