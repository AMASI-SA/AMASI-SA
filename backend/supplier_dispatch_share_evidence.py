"""Conversation evidence for employee-to-supplier product-file dispatches.

After the PDF is shared with the supplier, the mobile app must attach a screenshot
of that conversation. Evidence is Mezan-only and is bound to the immutable
supplier dispatch id; it never mutates Salla or Qoyod.
"""
from __future__ import annotations

import hashlib
import uuid
from typing import Any, Callable

from bson.binary import Binary
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pymongo import ASCENDING, DESCENDING, ReturnDocument

from order_review_routes import _merchant_user_id, _text
from preparation_supplier_dispatch import (
    DISPATCHES,
    DISPATCH_EVENTS,
    _actor_name,
    _now,
    _require_preparation_worker,
)

EVIDENCE = "mezan_supplier_dispatch_share_evidence_v1"
MAX_IMAGE_BYTES = 5 * 1024 * 1024
ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp"}


def _detected_image_type(data: bytes) -> str | None:
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


async def ensure_supplier_dispatch_evidence_indexes(db: Any) -> None:
    await db[EVIDENCE].create_index(
        [("user_id", ASCENDING), ("id", ASCENDING)], unique=True,
        name="uq_supplier_dispatch_share_evidence_v1",
    )
    await db[EVIDENCE].create_index(
        [("user_id", ASCENDING), ("dispatch_id", ASCENDING), ("created_at", DESCENDING)],
        name="ix_supplier_dispatch_share_evidence_dispatch_v1",
    )


async def _owned_dispatch(db: Any, *, user_id: str, employee_id: str, dispatch_id: str) -> dict[str, Any]:
    dispatch = await db[DISPATCHES].find_one(
        {"user_id": user_id, "id": _text(dispatch_id), "sent_by_id": employee_id},
        {"_id": 0},
    )
    if not dispatch:
        raise HTTPException(status_code=404, detail={"code": "supplier_dispatch_not_found"})
    return dispatch


def _public_status(dispatch: dict[str, Any]) -> dict[str, Any]:
    return {
        "dispatch_id": _text(dispatch.get("id")),
        "share_status": _text(dispatch.get("share_status")) or "pending_evidence",
        "share_evidence_id": _text(dispatch.get("share_evidence_id")) or None,
        "share_evidence_uploaded_at": dispatch.get("share_evidence_uploaded_at"),
        "share_confirmed": bool(dispatch.get("share_confirmed") is True),
        "share_confirmed_at": dispatch.get("share_confirmed_at"),
    }


def make_supplier_dispatch_share_evidence_router(db: Any, current_user: Callable) -> APIRouter:
    router = APIRouter(prefix="/supplier-dispatch-share-v1", tags=["Supplier Dispatch Share Evidence"])

    @router.get("/{dispatch_id}")
    async def status(dispatch_id: str, user: dict = Depends(current_user)) -> dict[str, Any]:
        worker = await _require_preparation_worker(db, user, permission="preparation.assigned.read")
        user_id = _merchant_user_id(worker)
        dispatch = await _owned_dispatch(db, user_id=user_id, employee_id=_text(worker.get("id")), dispatch_id=dispatch_id)
        return {"ok": True, **_public_status(dispatch)}

    @router.post("/{dispatch_id}/evidence")
    async def upload_evidence(
        dispatch_id: str,
        file: UploadFile = File(...),
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        worker = await _require_preparation_worker(db, user, permission="preparation.assigned.work")
        user_id = _merchant_user_id(worker)
        employee_id = _text(worker.get("id"))
        dispatch = await _owned_dispatch(db, user_id=user_id, employee_id=employee_id, dispatch_id=dispatch_id)
        if dispatch.get("share_confirmed") is True:
            return {"ok": True, **_public_status(dispatch)}

        declared = _text(file.content_type).casefold()
        if declared not in ALLOWED_IMAGE_TYPES:
            raise HTTPException(status_code=415, detail={"code": "supplier_dispatch_share_evidence_image_required"})
        data = await file.read(MAX_IMAGE_BYTES + 1)
        if not data or len(data) > MAX_IMAGE_BYTES:
            raise HTTPException(status_code=413, detail={"code": "supplier_dispatch_share_evidence_size_invalid"})
        detected = _detected_image_type(data)
        if detected != declared:
            raise HTTPException(status_code=415, detail={"code": "supplier_dispatch_share_evidence_image_invalid"})

        await ensure_supplier_dispatch_evidence_indexes(db)
        now = _now()
        evidence_id = f"sdse_{uuid.uuid4().hex}"
        await db[EVIDENCE].insert_one({
            "id": evidence_id,
            "user_id": user_id,
            "dispatch_id": dispatch_id,
            "supplier_id": dispatch.get("supplier_id"),
            "supplier_name": dispatch.get("supplier_name"),
            "content_type": detected,
            "filename": _text(file.filename)[:180],
            "size": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            "content": Binary(data),
            "created_at": now,
            "created_by": employee_id,
            "created_by_name": _actor_name(worker),
        })
        await db[DISPATCHES].update_one(
            {"user_id": user_id, "id": dispatch_id, "sent_by_id": employee_id},
            {"$set": {
                "share_status": "evidence_uploaded",
                "share_evidence_id": evidence_id,
                "share_evidence_uploaded_at": now,
                "updated_at": now,
            }},
        )
        updated = {**dispatch, "share_status": "evidence_uploaded", "share_evidence_id": evidence_id, "share_evidence_uploaded_at": now}
        return {"ok": True, **_public_status(updated)}

    @router.post("/{dispatch_id}/confirm")
    async def confirm(dispatch_id: str, user: dict = Depends(current_user)) -> dict[str, Any]:
        worker = await _require_preparation_worker(db, user, permission="preparation.assigned.work")
        user_id = _merchant_user_id(worker)
        employee_id = _text(worker.get("id"))
        dispatch = await _owned_dispatch(db, user_id=user_id, employee_id=employee_id, dispatch_id=dispatch_id)
        if dispatch.get("share_confirmed") is True:
            return {"ok": True, **_public_status(dispatch)}
        evidence_id = _text(dispatch.get("share_evidence_id"))
        if not evidence_id:
            raise HTTPException(status_code=409, detail={"code": "supplier_dispatch_share_evidence_required"})
        now = _now()
        result = await db[DISPATCHES].find_one_and_update(
            {"user_id": user_id, "id": dispatch_id, "sent_by_id": employee_id, "share_evidence_id": evidence_id},
            {"$set": {
                "share_status": "confirmed",
                "share_confirmed": True,
                "share_confirmed_at": now,
                "share_confirmed_by": employee_id,
                "updated_at": now,
            }},
            return_document=ReturnDocument.AFTER,
            projection={"_id": 0},
        )
        if not result:
            raise HTTPException(status_code=409, detail={"code": "supplier_dispatch_share_confirm_conflict"})
        await db[DISPATCH_EVENTS].insert_one({
            "id": uuid.uuid4().hex,
            "user_id": user_id,
            "event_type": "supplier_dispatch_share_confirmed",
            "dispatch_id": dispatch_id,
            "evidence_id": evidence_id,
            "actor_id": employee_id,
            "actor_name": _actor_name(worker),
            "occurred_at": now,
            "mezan_only": True,
            "salla_updated": False,
            "qoyod_updated": False,
        })
        return {"ok": True, **_public_status(result)}

    return router


__all__ = ["make_supplier_dispatch_share_evidence_router", "ensure_supplier_dispatch_evidence_indexes", "EVIDENCE"]
