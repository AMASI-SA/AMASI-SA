"""Universal Ledger Routes — Iter-160

Public API for adjustments, reversals, audit log, and ledger reads.
Replaces the destructive `reset-debt` / `recompute-debt` flows.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from auth import get_current_user_from_db
from fastapi import Request
from ledger_core import (
    REASON_CODES,
    AdjustmentIn,
    LedgerEntryIn,
    ReverseEntryIn,
    compute_balance,
    post_ledger_entry,
    reverse_entry,
    write_audit,
)


def make_ledger_router(db) -> APIRouter:
    router = APIRouter(prefix="/ledger", tags=["ledger"])

    async def current_user(request: Request) -> dict:
        return await get_current_user_from_db(request, db)

    # ── GET /reason-codes ───────────────────────────────────────────
    @router.get("/reason-codes")
    async def list_reason_codes(_user: dict = Depends(current_user)):
        return [{"code": k, "label": v} for k, v in REASON_CODES.items()]

    # ── POST /entries — generic entry creation (rarely used directly) ─
    @router.post("/entries")
    async def create_entry(
        payload: LedgerEntryIn,
        user: dict = Depends(current_user),
    ):
        status = "posted" if payload.auto_post else "draft"
        doc = await post_ledger_entry(
            db,
            user_id=user["id"], actor_id=user["id"],
            actor_name=user.get("name") or user.get("email") or "",
            entity_type=payload.entity_type, entity_id=payload.entity_id,
            entry_type=payload.entry_type, amount=payload.amount,
            side=payload.side,
            reason_code=payload.reason_code, notes=payload.notes,
            metadata=payload.metadata or {},
            status=status,
        )
        doc.pop("_id", None)
        return doc

    # ── POST /entries/{id}/post — promote draft → posted ──────────────
    @router.post("/entries/{entry_id}/post")
    async def post_draft(
        entry_id: str,
        user: dict = Depends(current_user),
    ):
        from ledger_core import _now
        orig = await db.general_ledger.find_one(
            {"id": entry_id, "user_id": user["id"]},
        )
        if not orig:
            raise HTTPException(404, "القيد غير موجود")
        if orig.get("status") != "draft":
            raise HTTPException(
                400, "يمكن اعتماد القيود المسودة فقط (status=draft)",
            )
        now = _now()
        await db.general_ledger.update_one(
            {"id": entry_id, "user_id": user["id"]},
            {"$set": {"status": "posted",
                      "posted_at": now, "posted_by": user["id"],
                      "updated_at": now}},
        )
        await write_audit(
            db,
            user_id=user["id"], actor_id=user["id"],
            actor_name=user.get("name") or user.get("email") or "",
            entity_type=orig["entity_type"], entity_id=orig["entity_id"],
            action="post_entry",
            before_state={"status": "draft"},
            after_state={"status": "posted"},
            ledger_entry_id=entry_id,
        )
        return {"ok": True, "id": entry_id, "status": "posted"}

    # ── POST /entries/{id}/reverse ───────────────────────────────────
    @router.post("/entries/{entry_id}/reverse")
    async def reverse(
        entry_id: str, payload: ReverseEntryIn,
        user: dict = Depends(current_user),
    ):
        rev = await reverse_entry(
            db, user_id=user["id"], actor_id=user["id"],
            actor_name=user.get("name") or user.get("email") or "",
            entry_id=entry_id,
            reason_code=payload.reason_code, notes=payload.notes or "",
        )
        rev.pop("_id", None)
        return {"ok": True, "reversal_entry": rev}

    # ── POST /adjustments — settlement / writeoff / adjustment ───────
    @router.post("/adjustments")
    async def make_adjustment(
        payload: AdjustmentIn,
        user: dict = Depends(current_user),
    ):
        # Map direction → ledger side:
        # reduce_debt   → debit  (acts like a payment / settlement / writeoff)
        # increase_debt → credit (acts like adding more obligation)
        side = "debit" if payload.direction == "reduce_debt" else "credit"
        doc = await post_ledger_entry(
            db, user_id=user["id"], actor_id=user["id"],
            actor_name=user.get("name") or user.get("email") or "",
            entity_type=payload.entity_type, entity_id=payload.entity_id,
            entry_type=payload.kind,
            amount=payload.amount, side=side,
            reason_code=payload.reason_code, notes=payload.notes,
            metadata={**(payload.metadata or {}),
                      "direction": payload.direction},
            status="posted",
        )
        doc.pop("_id", None)
        return {"ok": True, "entry": doc}

    # ── GET /entries — list entries with filters ─────────────────────
    @router.get("/entries")
    async def list_entries(
        entity_type: Optional[str] = None,
        entity_id: Optional[str] = None,
        entry_type: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = Query(100, ge=1, le=500),
        skip: int = Query(0, ge=0),
        user: dict = Depends(current_user),
    ):
        q: dict = {"user_id": user["id"]}
        if entity_type: q["entity_type"] = entity_type
        if entity_id:   q["entity_id"] = entity_id
        if entry_type:  q["entry_type"] = entry_type
        if status:      q["status"] = status
        cur = db.general_ledger.find(q, {"_id": 0}).sort(
            "entry_no", -1).skip(skip).limit(limit)
        items = await cur.to_list(limit)
        total = await db.general_ledger.count_documents(q)
        return {"items": items, "total": total, "skip": skip, "limit": limit}

    # ── GET /balance ─────────────────────────────────────────────────
    @router.get("/balance")
    async def get_balance(
        entity_type: str,
        entity_id: str,
        sub_account: Optional[str] = None,
        user: dict = Depends(current_user),
    ):
        return await compute_balance(
            db, user_id=user["id"],
            entity_type=entity_type, entity_id=entity_id,
            sub_account=sub_account,
        )

    # ── GET /audit-log ───────────────────────────────────────────────
    @router.get("/audit-log")
    async def list_audit(
        entity_type: Optional[str] = None,
        entity_id: Optional[str] = None,
        action: Optional[str] = None,
        limit: int = Query(100, ge=1, le=500),
        skip: int = Query(0, ge=0),
        user: dict = Depends(current_user),
    ):
        q: dict = {"user_id": user["id"]}
        if entity_type: q["entity_type"] = entity_type
        if entity_id:   q["entity_id"] = entity_id
        if action:      q["action"] = action
        cur = db.accounting_audit_log.find(q, {"_id": 0}).sort(
            "timestamp", -1).skip(skip).limit(limit)
        items = await cur.to_list(limit)
        total = await db.accounting_audit_log.count_documents(q)
        return {"items": items, "total": total, "skip": skip, "limit": limit}

    return router
