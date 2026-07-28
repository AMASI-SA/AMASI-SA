"""Secure temporary device uploads for governed Product V2 media drafts."""
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from bson.binary import Binary
from fastapi import APIRouter, Depends, File, HTTPException, Request, Response, UploadFile

from product_v2_routes import PRODUCTS

MEDIA_UPLOADS = "mezan_product_media_uploads_v2"
MAX_IMAGE_BYTES = 5 * 1024 * 1024
UPLOAD_TTL_DAYS = 7
ALLOWED_TYPES = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _detected_type(data: bytes) -> str | None:
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


async def delete_temp_uploads(db: Any, tokens: list[str]) -> int:
    clean = sorted({str(token) for token in tokens if token})
    if not clean:
        return 0
    result = await db[MEDIA_UPLOADS].delete_many({"token": {"$in": clean}})
    return int(result.deleted_count or 0)


def make_product_media_upload_router(db: Any, current_user: Callable) -> APIRouter:
    router = APIRouter(prefix="/products-v2", tags=["Product V2 Media Uploads"])

    @router.post("/{product_id}/media-upload")
    async def upload_media(
        product_id: str,
        request: Request,
        file: UploadFile = File(...),
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        user_id = str(user["id"])
        product = await db[PRODUCTS].find_one({
            "user_id": user_id,
            "$or": [{"id": product_id}, {"mezan_product_id": product_id}, {"salla_product_id": product_id}],
        }, {"_id": 0, "id": 1, "mezan_product_id": 1, "salla_product_id": 1})
        if not product:
            raise HTTPException(status_code=404, detail={"code": "product_v2_not_found"})

        declared = str(file.content_type or "").lower()
        if declared not in ALLOWED_TYPES:
            raise HTTPException(status_code=415, detail={"code": "unsupported_image_type"})
        data = await file.read(MAX_IMAGE_BYTES + 1)
        if not data:
            raise HTTPException(status_code=422, detail={"code": "empty_image"})
        if len(data) > MAX_IMAGE_BYTES:
            raise HTTPException(status_code=413, detail={"code": "image_too_large", "max_bytes": MAX_IMAGE_BYTES})
        detected = _detected_type(data)
        if detected not in ALLOWED_TYPES or detected != declared:
            raise HTTPException(status_code=415, detail={"code": "image_signature_mismatch"})

        token = secrets.token_urlsafe(32)
        now = _now()
        expires_at = now + timedelta(days=UPLOAD_TTL_DAYS)
        await db[MEDIA_UPLOADS].create_index("expires_at", expireAfterSeconds=0)
        await db[MEDIA_UPLOADS].insert_one({
            "token": token,
            "user_id": user_id,
            "salla_product_id": str(product.get("salla_product_id")),
            "filename": str(file.filename or f"upload{ALLOWED_TYPES[detected]}")[:180],
            "content_type": detected,
            "size": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            "content": Binary(data),
            "created_at": now,
            "expires_at": expires_at,
        })
        url = str(request.url_for("product_media_temp_file", token=token))
        return {
            "ok": True,
            "image": {
                "id": None,
                "url": url,
                "alt": "",
                "is_main": False,
                "source": "temporary_upload",
                "upload_token": token,
                "filename": file.filename,
                "size": len(data),
                "expires_at": expires_at.isoformat(),
            },
        }

    @router.delete("/{product_id}/media-upload/{token}")
    async def delete_media_upload(product_id: str, token: str, user: dict = Depends(current_user)) -> dict[str, Any]:
        result = await db[MEDIA_UPLOADS].delete_one({"token": token, "user_id": str(user["id"])})
        return {"ok": True, "deleted": int(result.deleted_count or 0)}

    @router.get("/media-upload/file/{token}", name="product_media_temp_file")
    async def product_media_temp_file(token: str) -> Response:
        row = await db[MEDIA_UPLOADS].find_one({"token": token})
        if not row or row.get("expires_at") <= _now():
            raise HTTPException(status_code=404, detail="temporary_image_not_found")
        return Response(
            content=bytes(row["content"]),
            media_type=row["content_type"],
            headers={"Cache-Control": "private, no-store, max-age=0", "X-Content-Type-Options": "nosniff"},
        )

    return router
