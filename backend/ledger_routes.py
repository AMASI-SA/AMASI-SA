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

    # ── POST /groups/{group_id}/reverse — Iter-214 group reversal ────
    @router.post("/groups/{group_id}/reverse")
    async def reverse_group(
        group_id: str, payload: ReverseEntryIn,
        user: dict = Depends(current_user),
    ):
        """Reverse every leg of a transaction group atomically.

        Each leg is reversed via `reverse_entry`. If any leg has already
        been reversed (or has invalid status) the call fails before any
        change is committed — we pre-validate the whole group first.
        """
        if not payload.reason_code:
            raise HTTPException(400, "reason_code إلزامي")
        legs = await db.general_ledger.find(
            {"txn_group_id": group_id, "user_id": user["id"]},
            {"_id": 0, "id": 1, "status": 1,
             "reversed_by_entry_id": 1, "entry_type": 1},
        ).to_list(length=200)
        if not legs:
            raise HTTPException(404, "المجموعة غير موجودة")
        for leg in legs:
            if leg.get("status") != "posted":
                raise HTTPException(
                    400,
                    "لا يمكن عكس المجموعة: أحد القيود ليس بحالة 'معتمد'",
                )
            if leg.get("reversed_by_entry_id"):
                raise HTTPException(
                    400, "هذه المجموعة معكوسة من قبل",
                )
            if leg.get("entry_type") == "reversal":
                raise HTTPException(
                    400, "لا يمكن عكس مجموعة عكسية",
                )
        # All clear — reverse every leg.
        reversals = []
        for leg in legs:
            rev = await reverse_entry(
                db, user_id=user["id"], actor_id=user["id"],
                actor_name=user.get("name") or user.get("email") or "",
                entry_id=leg["id"],
                reason_code=payload.reason_code,
                notes=payload.notes or "",
            )
            rev.pop("_id", None)
            reversals.append(rev)
        return {"ok": True, "reversed_count": len(reversals),
                "group_id": group_id}

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

        # Iter-214 — Enrich each entry with `posted_by_name` (creator)
        # and, for reversed entries, `reversed_by_name` + `reversed_at`.
        # Names come from the `users` collection. Caches lookups within
        # this request so we hit Mongo at most once per distinct user.
        user_ids = {it.get("posted_by") for it in items if it.get("posted_by")}
        reverser_ids: dict[str, str] = {}  # original_id → reversal_id
        if any(it.get("reversed_by_entry_id") for it in items):
            rev_ids = [it["reversed_by_entry_id"] for it in items
                       if it.get("reversed_by_entry_id")]
            async for r in db.general_ledger.find(
                {"id": {"$in": rev_ids}, "user_id": user["id"]},
                {"_id": 0, "id": 1, "posted_by": 1, "posted_at": 1},
            ):
                reverser_ids[r["id"]] = r
                if r.get("posted_by"):
                    user_ids.add(r["posted_by"])
        name_cache: dict[str, str] = {}
        if user_ids:
            async for u in db.users.find(
                {"id": {"$in": list(user_ids)}},
                {"_id": 0, "id": 1, "name": 1, "email": 1},
            ):
                name_cache[u["id"]] = u.get("name") or u.get("email") or ""
        for it in items:
            it["posted_by_name"] = name_cache.get(
                it.get("posted_by") or "", "")
            rev_id = it.get("reversed_by_entry_id")
            if rev_id and rev_id in reverser_ids:
                rev_doc = reverser_ids[rev_id]
                it["reversed_by_name"] = name_cache.get(
                    rev_doc.get("posted_by") or "", "")
                it["reversed_at"] = rev_doc.get("posted_at")
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
