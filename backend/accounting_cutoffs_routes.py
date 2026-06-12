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
        return res

    @router.post("/cutoffs/recompute")
    async def recompute_pre_accounting(
        provider: Optional[str] = Query(None),
        user: dict = Depends(current_user),
    ):
        """Flag every entity tied to `provider` (or every supported
        provider when None) as `is_pre_accounting` based on its date
        and the current cutoff.

        Touches:
          • payment_transactions  (created_at_provider < cutoff)
          • payment_refunds       (refunded_at        < cutoff)
          • settlement_entries    (settlement_date    < cutoff)
          • unified_orders        (received_at        < cutoff)  — Salla
          • account_transactions  (transaction_date   < cutoff)  — BANK
        """
        uid = user["id"]
        targets = (
            [provider] if provider in SUPPORTED_PROVIDERS
            else sorted(SUPPORTED_PROVIDERS)
        )

        results: Dict[str, Dict[str, Any]] = {}
        for p in targets:
            cutoff = await get_cutoff(db, uid, p)
            if not cutoff:
                continue
            stats = {"cutoff": cutoff,
                     "payment_transactions": 0,
                     "payment_refunds":      0,
                     "settlement_entries":   0,
                     "unified_orders":       0,
                     "account_transactions": 0}

            if p in ("tabby", "tamara"):
                r1 = await db.payment_transactions.update_many(
                    {"user_id": uid, "provider": p,
                     "created_at_provider": {"$lt": cutoff}},
                    {"$set": {"is_pre_accounting": True}},
                )
                stats["payment_transactions"] = int(
                    getattr(r1, "modified_count", 0) or 0
                )
                # Reset rows that have moved AFTER the cutoff.
                await db.payment_transactions.update_many(
                    {"user_id": uid, "provider": p,
                     "created_at_provider": {"$gte": cutoff},
                     "is_pre_accounting": True},
                    {"$set": {"is_pre_accounting": False}},
                )
                r2 = await db.payment_refunds.update_many(
                    {"user_id": uid, "provider": p,
                     "refunded_at": {"$lt": cutoff}},
                    {"$set": {"is_pre_accounting": True}},
                )
                stats["payment_refunds"] = int(
                    getattr(r2, "modified_count", 0) or 0
                )
                await db.payment_refunds.update_many(
                    {"user_id": uid, "provider": p,
                     "refunded_at": {"$gte": cutoff},
                     "is_pre_accounting": True},
                    {"$set": {"is_pre_accounting": False}},
                )
                r3 = await db.settlement_entries.update_many(
                    {"user_id": uid, "provider": p,
                     "settlement_date": {"$lt": cutoff}},
                    {"$set": {"is_pre_accounting": True}},
                )
                stats["settlement_entries"] = int(
                    getattr(r3, "modified_count", 0) or 0
                )
                await db.settlement_entries.update_many(
                    {"user_id": uid, "provider": p,
                     "settlement_date": {"$gte": cutoff},
                     "is_pre_accounting": True},
                    {"$set": {"is_pre_accounting": False}},
                )

            if p == "salla":
                r4 = await db.unified_orders.update_many(
                    {"user_id": uid,
                     "received_at": {"$lt": cutoff + "T00:00:00"}},
                    {"$set": {"is_pre_accounting": True}},
                )
                stats["unified_orders"] = int(
                    getattr(r4, "modified_count", 0) or 0
                )
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
                stats["account_transactions"] = int(
                    getattr(r5, "modified_count", 0) or 0
                )
                await db.account_transactions.update_many(
                    {"user_id": uid,
                     "transaction_date": {"$gte": cutoff},
                     "is_pre_accounting": True},
                    {"$set": {"is_pre_accounting": False}},
                )

            results[p] = stats

        return {"ok": True, "results": results}

    parent_router.include_router(router)
