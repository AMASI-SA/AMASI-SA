"""Iter-99 — Counterparties registry.

A unified directory of third parties the merchant transacts with
(suppliers, ad-account profiles like 'Snapchat Account 1', generic
counterparties). NOT employees — those stay in operating_salaries.

Collection: counterparties
  { id, user_id, kind, name, name_lower, ad_provider, notes,
    created_at, updated_at }

Endpoints (/api/counterparties):
  GET    /                     list with filter (kind)
  POST   /                     create (with fuzzy duplicate check)
  POST   /check-duplicate      preview duplicate without saving
  PUT    /{id}                 edit name/notes
  DELETE /{id}                 only when not referenced in liabilities
"""
import difflib
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from auth import get_current_user_from_db


COUNTERPARTY_KINDS = ("supplier", "ad_account", "general")
AD_PROVIDERS = ("snapchat", "tiktok", "meta")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _norm(s: str) -> str:
    return " ".join((s or "").strip().lower().split())


async def ensure_counterparties_indexes(db) -> None:
    try:
        await db.counterparties.create_index(
            [("user_id", 1), ("kind", 1), ("name_lower", 1)],
            unique=True, name="cp_unique_name",
        )
    except Exception:
        pass


def _fuzzy_match(name: str, candidates: list[dict], cutoff: float = 0.82):
    """Return the closest match above the cutoff, or None."""
    if not candidates:
        return None
    target = _norm(name)
    names = [_norm(c["name"]) for c in candidates]
    matches = difflib.get_close_matches(target, names, n=1, cutoff=cutoff)
    if not matches:
        return None
    idx = names.index(matches[0])
    return candidates[idx]


class CounterpartyIn(BaseModel):
    kind: str
    name: str = Field(..., min_length=1, max_length=160)
    ad_provider: Optional[str] = None
    notes: Optional[str] = Field("", max_length=500)
    force: bool = False  # bypass fuzzy duplicate warning


class CounterpartyUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=160)
    notes: Optional[str] = Field(None, max_length=500)


def attach_counterparties_routes(parent_router: APIRouter, db) -> None:
    async def current_user(request: Request) -> dict:
        return await get_current_user_from_db(request, db)

    router = APIRouter(prefix="/counterparties", tags=["counterparties"])

    async def _list_by(uid, kind=None):
        q = {"user_id": uid}
        if kind:
            q["kind"] = kind
        return await db.counterparties.find(q, {"_id": 0}).sort([("name", 1)]).to_list(2000)

    @router.get("")
    async def list_all(kind: Optional[str] = Query(None),
                       user: dict = Depends(current_user)):
        if kind and kind not in COUNTERPARTY_KINDS:
            raise HTTPException(400, f"kind must be one of {COUNTERPARTY_KINDS}")
        items = await _list_by(user["id"], kind)
        return {"items": items, "total": len(items)}

    @router.post("/check-duplicate")
    async def check_duplicate(payload: CounterpartyIn,
                              user: dict = Depends(current_user)):
        """Returns suggested match if a similar name exists (fuzzy)."""
        if payload.kind not in COUNTERPARTY_KINDS:
            raise HTTPException(400, "invalid kind")
        existing = await _list_by(user["id"], payload.kind)
        match = _fuzzy_match(payload.name, existing)
        return {"suggestion": match}

    @router.post("")
    async def create(payload: CounterpartyIn, user: dict = Depends(current_user)):
        if payload.kind not in COUNTERPARTY_KINDS:
            raise HTTPException(400, f"kind must be one of {COUNTERPARTY_KINDS}")
        if payload.kind == "ad_account":
            if not payload.ad_provider or payload.ad_provider not in AD_PROVIDERS:
                raise HTTPException(400, f"ad_provider must be one of {AD_PROVIDERS}")
        name_lower = _norm(payload.name)
        # Exact dup
        exact = await db.counterparties.find_one(
            {"user_id": user["id"], "kind": payload.kind, "name_lower": name_lower},
            {"_id": 0},
        )
        if exact:
            raise HTTPException(409, {"message": "duplicate", "existing": exact})
        # Fuzzy dup
        if not payload.force:
            existing = await _list_by(user["id"], payload.kind)
            match = _fuzzy_match(payload.name, existing)
            if match:
                raise HTTPException(409, {
                    "message": "similar_name_exists",
                    "suggestion": match,
                    "hint": "أعد الإرسال مع force=true لإنشاء الاسم الجديد رغم التشابه",
                })

        row = {
            "id": str(uuid.uuid4()),
            "user_id": user["id"],
            "kind": payload.kind,
            "name": payload.name.strip(),
            "name_lower": name_lower,
            "ad_provider": payload.ad_provider if payload.kind == "ad_account" else None,
            "notes": (payload.notes or "").strip(),
            "created_at": _now(),
            "updated_at": _now(),
        }
        await db.counterparties.insert_one(row)
        row.pop("_id", None)
        return row

    @router.put("/{cid}")
    async def update(cid: str, payload: CounterpartyUpdate,
                     user: dict = Depends(current_user)):
        existing = await db.counterparties.find_one(
            {"id": cid, "user_id": user["id"]}, {"_id": 0},
        )
        if not existing:
            raise HTTPException(404, "not found")
        upd = {"updated_at": _now()}
        if payload.name is not None:
            upd["name"] = payload.name.strip()
            upd["name_lower"] = _norm(payload.name)
        if payload.notes is not None:
            upd["notes"] = payload.notes.strip()
        await db.counterparties.update_one(
            {"id": cid, "user_id": user["id"]}, {"$set": upd}
        )
        return await db.counterparties.find_one(
            {"id": cid, "user_id": user["id"]}, {"_id": 0}
        )

    @router.delete("/{cid}")
    async def delete(cid: str, user: dict = Depends(current_user)):
        # Refuse if referenced in any open liability
        ref = await db.liabilities.find_one(
            {"user_id": user["id"], "counterparty_id": cid,
             "status": {"$ne": "paid"}},
            {"_id": 0, "id": 1},
        )
        if ref:
            raise HTTPException(400, "هذا الطرف مرتبط بالتزام مفتوح. أغلق الالتزام أولاً.")
        res = await db.counterparties.delete_one(
            {"id": cid, "user_id": user["id"]}
        )
        if res.deleted_count == 0:
            raise HTTPException(404, "not found")
        return {"ok": True}

    parent_router.include_router(router)
