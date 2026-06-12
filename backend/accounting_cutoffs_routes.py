"""Iter-149 — REST routes for the per-provider accounting cutoff.

Endpoints:

  GET  /api/accounting/cutoffs
       → {"cutoffs": {"tabby": "2026-04-27", ...}, "defaults": {...}}

  PUT  /api/accounting/cutoffs/{provider}
       Body: {"accounting_start_date": "2026-04-27"}
       → {"provider": "...", "old": "...", "new": "...", "changed": true}

  POST /api/accounting/cutoffs/recompute
       Sets `is_pre_accounting=true|false` on every entity tied to a
       provider whose cutoff changed.  Pass `?provider=tabby` to scope
       to one provider, omit to do all five.

The Tamara aggregator + BNPL settlements engine consult the cutoff
INLINE on every read (Iter-149) so cutoffs take effect immediately
without a recompute pass.  The recompute endpoint exists only to flag
historical rows so other modules (profits, operational reports) can
filter by `is_pre_accounting` cheaply.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from accounting_cutoffs import (
    DEFAULT_CUTOFFS,
    SUPPORTED_PROVIDERS,
    get_all_cutoffs,
    get_cutoff,
    set_cutoff,
)


class CutoffUpdateIn(BaseModel):
    accounting_start_date: str = Field(..., min_length=10, max_length=10)


async def _recompute_one(db, uid: str, p: str) -> Dict[str, Any]:
    """Iter-149 v2 — Flag every entity tied to `p` as `is_pre_accounting`
    based on the current cutoff.  Idempotent.  Also UNFLAGS rows that
    have moved past the cutoff (e.g., the merchant pulled the cutoff
    BACK to an earlier date)."""
    cutoff = await get_cutoff(db, uid, p)
    if not cutoff:
        return None
    stats: Dict[str, Any] = {"cutoff": cutoff}

    if p in ("tabby", "tamara"):
        for col, date_field in (
            ("payment_transactions", "created_at_provider"),
            ("payment_refunds",      "refunded_at"),
            ("settlement_entries",   "settlement_date"),
        ):
            r = await db[col].update_many(
                {"user_id": uid, "provider": p,
                 date_field: {"$lt": cutoff}},
                {"$set": {"is_pre_accounting": True}},
            )
            stats[col] = int(getattr(r, "modified_count", 0) or 0)
            await db[col].update_many(
                {"user_id": uid, "provider": p,
                 date_field: {"$gte": cutoff},
                 "is_pre_accounting": True},
                {"$set": {"is_pre_accounting": False}},
            )
        # Also flag liabilities whose `due_date` (or `created_at`)
        # falls before the cutoff so the financial-position screen
        # excludes them.
        r2 = await db.liabilities.update_many(
            {"user_id": uid,
             "kind": {"$in": ["ad_account", "supplier"]},
             "$or": [
                {"due_date":   {"$lt": cutoff}},
                {"created_at": {"$lt": cutoff + "T00:00:00"}},
             ]},
            {"$set": {"is_pre_accounting": True}},
        )
        stats["liabilities"] = int(getattr(r2, "modified_count", 0) or 0)

    if p == "salla":
        r4 = await db.unified_orders.update_many(
            {"user_id": uid,
             "received_at": {"$lt": cutoff + "T00:00:00"}},
            {"$set": {"is_pre_accounting": True}},
        )
        stats["unified_orders"] = int(getattr(r4, "modified_count", 0) or 0)
        await db.unified_orders.update_many(
            {"user_id": uid,
             "received_at": {"$gte": cutoff + "T00:00:00"},
             "is_pre_accounting": True},
            {"$set": {"is_pre_accounting": False}},
        )

    if p == "bank_transfer":
        r5 = await db.account_transactions.update_many(
            {"user_id": uid,
             "transaction_date": {"$lt": cutoff}},
            {"$set": {"is_pre_accounting": True}},
        )
        stats["account_transactions"] = int(getattr(r5, "modified_count", 0) or 0)
        await db.account_transactions.update_many(
            {"user_id": uid,
             "transaction_date": {"$gte": cutoff},
             "is_pre_accounting": True},
            {"$set": {"is_pre_accounting": False}},
        )

    if p == "cod":
        # Flag unified_orders that are COD and pre-cutoff (payment method
        # contains COD synonyms).
        r6 = await db.unified_orders.update_many(
            {"user_id": uid,
             "received_at": {"$lt": cutoff + "T00:00:00"},
             "$or": [
                {"payment_method": {"$regex": "(?i)cod|cash|الدفع.*استلام"}},
                {"payment_method_normalized": {"$in": ["cod", "cash_on_delivery"]}},
             ]},
            {"$set": {"is_pre_accounting": True}},
        )
        stats["unified_orders_cod"] = int(getattr(r6, "modified_count", 0) or 0)

    return stats


def attach_accounting_cutoffs_routes(parent_router: APIRouter, db, current_user):
    router = APIRouter(prefix="/accounting", tags=["accounting-cutoffs"])

    @router.get("/cutoffs")
    async def get_cutoffs(user: dict = Depends(current_user)):
        uid = user["id"]
        cutoffs = await get_all_cutoffs(db, uid)
        return {
            "cutoffs":            cutoffs,
            "defaults":           dict(DEFAULT_CUTOFFS),
            "supported_providers": sorted(SUPPORTED_PROVIDERS),
        }

    @router.put("/cutoffs/{provider}")
    async def update_cutoff(
        provider: str,
        body: CutoffUpdateIn,
        user: dict = Depends(current_user),
    ):
        if provider not in SUPPORTED_PROVIDERS:
            raise HTTPException(404, f"Unknown provider: {provider}")
        try:
            res = await set_cutoff(
                db, user["id"], provider, body.accounting_start_date,
            )
        except ValueError as e:
            raise HTTPException(400, str(e)) from e

        # Iter-149 v2 — auto-recompute when the cutoff changes so the
        # merchant sees `is_pre_accounting` propagated immediately
        # without manually clicking the recompute button.
        recompute_stats = None
        if res.get("changed"):
            recompute_stats = await _recompute_one(
                db, user["id"], provider,
            )
        return {**res, "recompute": recompute_stats}

    @router.post("/cutoffs/recompute")
    async def recompute_pre_accounting(
        provider: Optional[str] = Query(None),
        user: dict = Depends(current_user),
    ):
        """Flag every entity tied to `provider` (or every supported
        provider when None) as `is_pre_accounting` based on its date
        and the current cutoff."""
        uid = user["id"]
        targets = (
            [provider] if provider in SUPPORTED_PROVIDERS
            else sorted(SUPPORTED_PROVIDERS)
        )
        results: Dict[str, Dict[str, Any]] = {}
        for p in targets:
            r = await _recompute_one(db, uid, p)
            if r is not None:
                results[p] = r
        return {"ok": True, "results": results}

    parent_router.include_router(router)
