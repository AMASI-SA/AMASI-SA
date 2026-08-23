"""HTTP routes for BNPL Automatic Settlements — Phase 4."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from .settlement_bridge import post_bnpl_settlement_to_ledger
from .settlements_service import (
    compute_all_settlements,
    compute_settlement_for_provider,
    compute_weekly_settlements,
    _compute_period_items,
    PROVIDERS,
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class BNPLSettlementRegisterIn(BaseModel):
    provider: str               # "tabby" | "tamara"
    bank_account_id: str
    transferred_amount: float = Field(..., ge=0)
    commission: float = Field(0.0, ge=0)
    commission_vat: float = Field(0.0, ge=0)
    settlement_fee: float = Field(0.0, ge=0)
    settlement_reference: str = Field(..., min_length=1, max_length=200)
    settlement_date: Optional[str] = Field(None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    notes: Optional[str] = ""
    # Iter-246x — explicit settlement period for SSOT duplicate detection.
    period_from: Optional[str] = Field(None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    period_to: Optional[str] = Field(None, pattern=r"^\d{4}-\d{2}-\d{2}$")


def attach_bnpl_settlements_routes(parent_router, *, db, get_current_user):
    router = APIRouter(prefix="/bnpl/settlements", tags=["BNPL Settlements"])

    @router.get("/summary")
    async def settlements_summary(
        user: dict = Depends(get_current_user),
        from_date: Optional[str] = Query(None, alias="from",
                                         pattern=r"^\d{4}-\d{2}-\d{2}$"),
        to_date: Optional[str] = Query(None, alias="to",
                                       pattern=r"^\d{4}-\d{2}-\d{2}$"),
    ):
        """One call → full BNPL settlement breakdown for both providers
        plus global totals.  Optional date filter (YYYY-MM-DD).
        Wrapped in try/except so Cloudflare can't 524 us."""
        try:
            return {
                "success": True,
                **(await compute_all_settlements(db, user["id"], from_date, to_date)),
            }
        except Exception as e:  # noqa: BLE001
            return {"success": False, "error": f"{type(e).__name__}: {e}"}

    @router.get("/items/{provider}")
    async def settlement_items(
        provider: str,
        user: dict = Depends(get_current_user),
        from_date: str = Query(..., alias="from",
                               pattern=r"^\d{4}-\d{2}-\d{2}$"),
        to_date: str = Query(..., alias="to",
                             pattern=r"^\d{4}-\d{2}-\d{2}$"),
    ):
        """Iter-120 — return the raw sales + refund items inside a
        single settlement period.  Powers the two detail tables shown
        when the merchant expands a weekly settlement row.

        IMPORTANT ACCOUNTING RULE:
          • Sales:  orders whose ORDER DATE  ∈ [from, to].
          • Refunds: refunds whose REFUND DATE ∈ [from, to] — regardless
            of when the original order was placed.  Each refund row is
            enriched with its original order's date and amount so the
            merchant can see when the refund crosses period boundaries.
        """
        if provider not in PROVIDERS:
            return {"success": False, "error": f"unknown provider {provider}"}
        try:
            items = await _compute_period_items(
                db, user["id"], provider, from_date, to_date,
            )
            return {
                "success":     True,
                "provider":    provider,
                "period":      {"from": from_date, "to": to_date},
                "sales":       items["sales"],
                "refunds":     items["refunds"],
                "sales_total": round(
                    sum(s["amount"] for s in items["sales"]), 2,
                ),
                "refunds_total": round(
                    sum(r["refund_amount"] for r in items["refunds"]), 2,
                ),
                "cross_period_refunds_count": sum(
                    1 for r in items["refunds"]
                    if (r.get("order_date") or "")[:10] < from_date
                ),
            }
        except Exception as e:  # noqa: BLE001
            return {"success": False, "error": f"{type(e).__name__}: {e}"}

    @router.post("/register")
    async def register_settlement(
        payload: BNPLSettlementRegisterIn,
        user: dict = Depends(get_current_user),
    ):
        """Iter-220 — register a BNPL settlement (bank transfer + fees)
        and post the balanced SSOT entry that closes the receivable.

        Side-effects:
          • general_ledger: 1 balanced txn_group (`bnpl_settlement`).
          • account_transactions: 1 `settlement` row on the destination
            bank account (so the existing bank UI sees the inbound
            transfer).

        Idempotency: same (provider, settlement_reference) → no
        duplicate ledger group, no duplicate account_transactions row.
        """
        uid = user["id"]
        if payload.provider.lower() not in PROVIDERS:
            raise HTTPException(400, f"unknown provider {payload.provider}")

        try:
            res = await post_bnpl_settlement_to_ledger(
                db, user_id=uid,
                actor_id=uid, actor_name=user.get("name") or "user",
                provider=payload.provider,
                bank_account_id=payload.bank_account_id,
                transferred_amount=payload.transferred_amount,
                commission=payload.commission,
                commission_vat=payload.commission_vat,
                settlement_fee=payload.settlement_fee,
                settlement_reference=payload.settlement_reference,
                settlement_date=payload.settlement_date,
                notes=payload.notes or "",
                # Iter-246x — period dedup + fresh-sync metadata.
                period_from=payload.period_from,
                period_to=payload.period_to,
            )
        except HTTPException:
            raise
        except Exception as e:  # noqa: BLE001
            raise HTTPException(
                500, f"settlement bridge failed: {type(e).__name__}: {e}",
            )

        # Iter-233 — TRUE idempotency across BOTH `general_ledger`
        # AND `account_transactions`.  Previously when the bridge said
        # `skipped=True` we returned immediately, assuming the first
        # call had already written the bank row.  That assumption broke
        # whenever the first call partially failed (network drop, retry,
        # crash between ledger insert and account_transactions insert),
        # leaving the settlement invisible on the bank page forever.
        #
        # New behaviour: regardless of `skipped`, ensure the
        # `account_transactions` row exists for this idempotency key.
        # If it's missing, create it now.  Safe to re-run.
        idem_key = (
            f"bnpl_settlement:{payload.provider}:"
            f"{payload.settlement_reference}"
        )
        if payload.transferred_amount > 0:
            existing_at = await db.account_transactions.find_one(
                {"user_id": uid,
                 "metadata.idempotency_key": idem_key},
                {"_id": 0, "id": 1},
            )
            if not existing_at:
                now = _now_iso()
                await db.account_transactions.insert_one({
                    "id": str(uuid.uuid4()),
                    "user_id": uid,
                    "account_id": payload.bank_account_id,
                    "transaction_type": "settlement",
                    "amount": round(float(payload.transferred_amount), 2),
                    "direction": "in",
                    "description": (
                        payload.notes
                        or f"تسوية {payload.provider} — مرجع "
                           f"{payload.settlement_reference}"
                    ),
                    "transaction_date": (
                        payload.settlement_date or now[:10]
                    ),
                    "balance_after": 0.0,    # recomputed below
                    "status": "posted",
                    "attachment_url": None,
                    "created_at": now,
                    "updated_at": now,
                    "metadata": {
                        "bnpl_settlement_group_id": res.get("txn_group_id"),
                        "provider": payload.provider,
                        "settlement_reference": payload.settlement_reference,
                        "idempotency_key": idem_key,
                    },
                })
                # Recompute bank balance.
                try:
                    from accounts_routes import _recompute_balance
                    await _recompute_balance(
                        db, uid, payload.bank_account_id,
                    )
                except Exception:  # noqa: BLE001
                    pass

        return {"success": True, **res}

    # ── Iter-233 — Backfill missing bank account_transactions ─────
    @router.post("/backfill-bank-transactions")
    async def backfill_bank_transactions(
        user: dict = Depends(get_current_user),
        dry_run: bool = Query(False),
    ):
        """Iter-233 — Find BNPL settlements that exist in the SSOT
        `general_ledger` but are MISSING the corresponding row in
        `account_transactions` (which is what the bank-detail page and
        the BNPL register page read from).

        For every missing settlement we re-create the row using the
        same idempotency_key the original register-call would have
        used, then recompute the bank account balance.

        Safe to re-run — idempotent.  Set `?dry_run=true` to preview
        without writing.
        """
        uid = user["id"]
        # Find every DEBIT leg on a bank entity inside a bnpl_settlement
        # group. That's the leg that should mirror to account_transactions.
        cursor = db.general_ledger.find(
            {"user_id": uid, "entry_type": "bnpl_settlement",
             "status": "posted", "side": "debit",
             "entity_type": "bank"},
            {"_id": 0, "txn_group_id": 1, "amount": 1, "entity_id": 1,
             "metadata": 1, "posted_at": 1, "notes": 1},
        ).sort("posted_at", -1)

        checked = 0
        already_ok = 0
        created = 0
        missing: list[dict] = []
        recomputed_accounts: set[str] = set()
        async for e in cursor:
            checked += 1
            meta = e.get("metadata") or {}
            ref = meta.get("settlement_reference")
            provider = (meta.get("provider") or "").lower()
            if not ref or provider not in PROVIDERS:
                continue
            idem_key = f"bnpl_settlement:{provider}:{ref}"
            existing = await db.account_transactions.find_one(
                {"user_id": uid,
                 "metadata.idempotency_key": idem_key},
                {"_id": 0, "id": 1},
            )
            if existing:
                already_ok += 1
                continue
            entry_record = {
                "settlement_reference": ref,
                "provider": provider,
                "bank_account_id": e.get("entity_id"),
                "amount": round(float(e.get("amount") or 0), 2),
                "settlement_date": (
                    meta.get("settlement_date")
                    or (e.get("posted_at") or "")[:10]
                ),
                "txn_group_id": e.get("txn_group_id"),
            }
            missing.append(entry_record)
            if dry_run:
                continue
            now = _now_iso()
            await db.account_transactions.insert_one({
                "id": str(uuid.uuid4()),
                "user_id": uid,
                "account_id": e.get("entity_id"),
                "transaction_type": "settlement",
                "amount": entry_record["amount"],
                "direction": "in",
                "description": (
                    e.get("notes")
                    or f"تسوية {provider} — مرجع {ref}"
                ),
                "transaction_date": entry_record["settlement_date"],
                "balance_after": 0.0,    # recomputed at the end
                "status": "posted",
                "attachment_url": None,
                "created_at": now,
                "updated_at": now,
                "metadata": {
                    "bnpl_settlement_group_id": e.get("txn_group_id"),
                    "provider": provider,
                    "settlement_reference": ref,
                    "idempotency_key": idem_key,
                    "backfilled_at": now,
                    "iter": "iter233",
                },
            })
            created += 1
            recomputed_accounts.add(e.get("entity_id"))

        # Recompute affected bank balances ONCE per account.
        if not dry_run and recomputed_accounts:
            try:
                from accounts_routes import _recompute_balance
                for acc_id in recomputed_accounts:
                    try:
                        await _recompute_balance(db, uid, acc_id)
                    except Exception:  # noqa: BLE001
                        pass
            except Exception:  # noqa: BLE001
                pass

        return {
            "success": True,
            "dry_run": dry_run,
            "checked": checked,
            "already_ok": already_ok,
            "created": created,
            "missing_details": missing[:50],   # cap response size
        }

    # ── Iter-234 — Per-order diagnostic for Tamara/Tabby ──────────────
    @router.get("/order-diagnostic/{provider}")
    async def order_diagnostic(
        provider: str,
        order_id: str = Query(..., description="Tamara/Tabby provider order_id or order_reference_id"),
        date_from: str = Query(...),
        date_to: str = Query(...),
        user: dict = Depends(get_current_user),
    ):
        """Iter-234 — Diagnose why a specific order is/isn't counted in
        a settlement window.  READ-ONLY.  Useful for verifying that the
        Iter-234 orphan-refund recovery is active in production.

        Returns:
          - txn: the matching payment_transactions row (if any)
          - refunds: matching payment_refunds rows in the window
          - in_window_by_attribution: bool (gross would include it?)
          - in_window_after_recovery: bool (after Iter-234 rescue?)
          - engine_version: marker confirming deployed code includes Iter-234
        """
        from .settlements_service import (
            _local_date_window_utc, _r,
        )
        if provider not in PROVIDERS:
            raise HTTPException(400, f"unknown provider {provider}")
        uid = user["id"]
        utc_gte, utc_lte = _local_date_window_utc(date_from, date_to)

        # Locate the payment transaction by provider_id OR order_reference_id
        # OR order_number (so the merchant can paste any identifier).
        txn = await db.payment_transactions.find_one(
            {"user_id": uid, "provider": provider,
             "$or": [{"provider_id": order_id},
                     {"order_reference_id": order_id},
                     {"order_number": order_id}]},
            {"_id": 0},
        )

        in_window_attr = False
        attribution_field_value = None
        if txn:
            sales_date_field = (
                "effective_settlement_date" if provider == "tamara"
                else "created_at_provider"
            )
            attribution_field_value = txn.get(sales_date_field)
            if attribution_field_value:
                in_window_attr = (
                    (not utc_gte or str(attribution_field_value) >= utc_gte)
                    and (not utc_lte or str(attribution_field_value) <= utc_lte)
                )

        # Find refunds for this order inside the window.
        refund_match: dict = {"user_id": uid, "provider": provider}
        if utc_gte or utc_lte:
            refund_match["refunded_at"] = {}
            if utc_gte:
                refund_match["refunded_at"]["$gte"] = utc_gte
            if utc_lte:
                refund_match["refunded_at"]["$lte"] = utc_lte
        refund_match["$or"] = [
            {"provider_payment_id": (txn or {}).get("provider_id") or order_id},
            {"order_reference_id": (txn or {}).get("order_reference_id") or order_id},
        ]
        refunds = []
        async for r in db.payment_refunds.find(refund_match, {"_id": 0}):
            refunds.append({
                "provider_refund_id": r.get("provider_refund_id"),
                "amount": _r(float(r.get("amount") or 0)),
                "refunded_at": r.get("refunded_at"),
                "provider_payment_id": r.get("provider_payment_id"),
                "order_reference_id": r.get("order_reference_id"),
            })

        # Would Iter-234 recovery include it?
        in_window_after_recovery = in_window_attr
        if (
            provider == "tamara" and not in_window_attr
            and txn and len(refunds) > 0
        ):
            in_window_after_recovery = True

        # Iter-234c — Replicate the EXACT sales_match used by the
        # settlement engine (`_compute_provider_totals`) so we can see
        # WHY a txn that looks in-window by attribution isn't included
        # in `gross_sales`.  This catches:
        #   - accounting_cutoff filter (Iter-149) excluding it
        #   - is_pre_accounting flag
        #   - any future filter additions
        sales_date_field = (
            "effective_settlement_date" if provider == "tamara"
            else "created_at_provider"
        )
        engine_sales_match: dict = {
            "user_id": uid, "provider": provider,
        }
        if utc_gte or utc_lte:
            rng: dict = {}
            if utc_gte:
                rng["$gte"] = utc_gte
            if utc_lte:
                rng["$lte"] = utc_lte
            engine_sales_match[sales_date_field] = rng
        accounting_cutoff = None
        try:
            from accounting_cutoffs import get_cutoff
            accounting_cutoff = await get_cutoff(db, uid, provider)
        except Exception:
            pass
        if accounting_cutoff:
            rng = engine_sales_match.setdefault(sales_date_field, {})
            if isinstance(rng, dict):
                prev = rng.get("$gte")
                if not prev or str(prev) < accounting_cutoff:
                    rng["$gte"] = accounting_cutoff
            engine_sales_match["is_pre_accounting"] = {"$ne": True}

        # Does THIS exact txn match the engine filter?
        engine_matches = False
        # Iter-234c — progressive filter breakdown so we can pinpoint
        # which filter (if any) is excluding this txn.
        filter_breakdown = {}
        if txn and txn.get("provider_id"):
            tx_pid = txn["provider_id"]
            stages = [
                ("base_user_provider_pid", {
                    "user_id": uid, "provider": provider,
                    "provider_id": tx_pid,
                }),
                ("+_sales_date_field_range", {
                    "user_id": uid, "provider": provider,
                    "provider_id": tx_pid,
                    sales_date_field: (
                        engine_sales_match.get(sales_date_field) or {}
                    ),
                }),
                ("+_is_pre_accounting", {
                    "user_id": uid, "provider": provider,
                    "provider_id": tx_pid,
                    sales_date_field: (
                        engine_sales_match.get(sales_date_field) or {}
                    ),
                    "is_pre_accounting": (
                        engine_sales_match.get(
                            "is_pre_accounting",
                            {"$ne": True},
                        )
                    ),
                }),
            ]
            for name, q in stages:
                # Drop empty range to avoid mongo error.
                if (sales_date_field in q
                        and not q.get(sales_date_field)):
                    q.pop(sales_date_field, None)
                cnt = await db.payment_transactions.count_documents(q)
                filter_breakdown[name] = cnt
            engine_matches = filter_breakdown.get(
                "+_is_pre_accounting", 0,
            ) > 0
        engine_filters = {
            "sales_date_field": sales_date_field,
            "accounting_cutoff": accounting_cutoff,
            "filter_used": {
                k: (v if not isinstance(v, dict) else dict(v))
                for k, v in engine_sales_match.items()
            },
            "filter_breakdown": filter_breakdown,
        }
        # Aggregate gross/count using the engine's exact filter to show
        # what the engine ACTUALLY computes for this window.
        engine_gross = 0.0
        engine_count = 0
        async for r in db.payment_transactions.aggregate([
            {"$match": engine_sales_match},
            {"$group": {"_id": None, "n": {"$sum": 1},
                        "gross": {"$sum": {"$ifNull": ["$amount", 0]}}}},
        ]):
            engine_count = int(r.get("n") or 0)
            engine_gross = _r(float(r.get("gross") or 0))

        # Iter-234c — Also surface the FULL settlement engine output
        # for this window so the merchant can see exactly what numbers
        # the deployed engine produces (no guessing from the UI).
        from .settlements_service import compute_settlement_for_provider
        engine_full = None
        try:
            engine_full = await compute_settlement_for_provider(
                db, uid, provider, date_from, date_to,
            )
        except Exception as exc:  # noqa: BLE001
            engine_full = {"error": str(exc)}
        engine_totals = (engine_full or {}).get("totals") or {}
        engine_summary = {
            "engine_version": (engine_full or {}).get("engine_version"),
            "data_source": (engine_full or {}).get("data_source"),
            "transactions_count": engine_totals.get("transactions_count"),
            "refunds_count": engine_totals.get("refunds_count"),
            "gross_sales": engine_totals.get("gross_sales"),
            "total_refunds": engine_totals.get("total_refunds"),
            "net_sales": engine_totals.get("net_sales"),
            "commission": engine_totals.get("commission"),
            "commission_vat": engine_totals.get("commission_vat"),
            "settlement_fee": engine_totals.get("settlement_fee"),
            "settlement_fee_vat": engine_totals.get("settlement_fee_vat"),
            "net_payable": engine_totals.get("net_payable"),
        }
        return {
            "success": True,
            "engine_version": "iter234",
            "provider": provider,
            "order_id_input": order_id,
            "period": {"from": date_from, "to": date_to,
                       "utc_gte": utc_gte, "utc_lte": utc_lte},
            "txn": {
                "found": txn is not None,
                "provider_id": (txn or {}).get("provider_id"),
                "order_reference_id": (txn or {}).get("order_reference_id"),
                "order_number": (txn or {}).get("order_number"),
                "amount": _r(float((txn or {}).get("amount") or 0)),
                "status": (txn or {}).get("status"),
                "created_at_provider": (txn or {}).get("created_at_provider"),
                "captured_at_provider": (txn or {}).get("captured_at_provider"),
                "billing_eligible_at": (txn or {}).get("billing_eligible_at"),
                "effective_settlement_date": (txn or {}).get("effective_settlement_date"),
                "settlement_source": (txn or {}).get("settlement_source"),
                "is_pre_accounting": bool((txn or {}).get("is_pre_accounting")),
            },
            "attribution_field_value": attribution_field_value,
            "in_window_by_attribution": in_window_attr,
            "in_window_after_iter234_recovery": in_window_after_recovery,
            "refunds_in_window": refunds,
            "refunds_count_in_window": len(refunds),
            "engine_filters": engine_filters,
            "engine_matches_this_txn": engine_matches,
            "engine_gross_in_window": engine_gross,
            "engine_count_in_window": engine_count,
            "engine_full_settlement_totals": engine_summary,
            "official_file_overrides": (
                engine_summary.get("data_source") == "provider_official_file"
            ),
        }

    # ── Iter-234d — Clear official Tamara/Tabby file entries ─────────
    @router.delete("/clear-official-entries/{provider}")
    async def clear_official_entries(
        provider: str,
        date_from: str = Query(...),
        date_to: str = Query(...),
        dry_run: bool = Query(False),
        user: dict = Depends(get_current_user),
    ):
        """Iter-234d — Delete imported provider-settlement-file entries
        for a period, so the engine falls back to its dynamic
        computation (the new Iter-234 logic).

        Use this when an old, partial, or stale settlement file was
        uploaded and now overrides the engine's computed totals with
        wrong numbers.

        Read-only safe via `?dry_run=true`.
        """
        if provider not in PROVIDERS:
            raise HTTPException(400, f"unknown provider {provider}")
        uid = user["id"]
        q: dict = {
            "user_id": uid,
            "provider": provider,
            "settlement_date": {"$gte": date_from, "$lte": date_to},
        }
        n = await db.settlement_entries.count_documents(q)
        # Show a tiny sample for verification.
        sample = []
        async for e in db.settlement_entries.find(
            q, {"_id": 0, "order_number": 1, "event_type": 1,
                "settlement_date": 1, "actual_gross_amount": 1,
                "actual_net_amount": 1, "actual_payment_fee": 1,
                "source_file": 1, "file_hash": 1, "created_at": 1},
        ).sort("created_at", -1).limit(5):
            sample.append(e)
        deleted = 0
        if not dry_run and n > 0:
            res = await db.settlement_entries.delete_many(q)
            deleted = res.deleted_count
        return {
            "success": True,
            "dry_run": dry_run,
            "provider": provider,
            "period": {"from": date_from, "to": date_to},
            "matched_entries": n,
            "deleted_entries": deleted,
            "sample_first_5": sample,
            "next_step": (
                "Re-fetch the settlement preview; engine should now show "
                "data_source='computed' with the Iter-234 numbers."
            ),
        }

    # ── Iter-221 — Registration page support endpoints ────────────────
    @router.get("/registered")
    async def list_registered_settlements(
        user: dict = Depends(get_current_user),
        provider: Optional[str] = Query(None),
        limit: int = Query(50, ge=1, le=200),
    ):
        """List BNPL settlements recorded via `/register` (SSOT ledger
        groups with `txn_type=bnpl_settlement`). Newest first."""
        uid = user["id"]
        match: dict = {
            "user_id": uid,
            "entry_type": "bnpl_settlement",
            "status": "posted",
            "side": "credit",                # the close-out leg only
            "entity_type": "payment_gateway",
        }
        if provider and provider in PROVIDERS:
            match["entity_id"] = provider
        cursor = db.general_ledger.find(
            match,
            {"_id": 0},
        ).sort("posted_at", -1).limit(int(limit))
        out = []
        async for e in cursor:
            meta = e.get("metadata") or {}
            out.append({
                "txn_group_id": e.get("txn_group_id"),
                "entry_no": e.get("entry_no"),
                "provider": meta.get("provider") or e.get("entity_id"),
                "settlement_reference": meta.get("settlement_reference"),
                "settlement_date": meta.get("settlement_date"),
                "posted_at": e.get("posted_at"),
                "transferred_amount": meta.get("transferred_amount") or 0,
                "commission": meta.get("commission") or 0,
                "commission_vat": meta.get("commission_vat") or 0,
                "settlement_fee": meta.get("settlement_fee") or 0,
                "total_closed": e.get("amount"),
                "bank_account_id": meta.get("bank_account_id"),
                "bank_account_name": meta.get("bank_account_name"),
                "notes": e.get("notes"),
            })
        return {"success": True, "items": out}

    @router.get("/registered/{txn_group_id}")
    async def get_registered_settlement(
        txn_group_id: str,
        user: dict = Depends(get_current_user),
    ):
        """Return all legs of a single registered settlement so the UI
        can show the resulting double-entry after the user saves."""
        uid = user["id"]
        entries = await db.general_ledger.find(
            {"user_id": uid, "txn_group_id": txn_group_id},
            {"_id": 0},
        ).sort("entry_no", 1).to_list(20)
        if not entries:
            raise HTTPException(404, "القيد غير موجود")
        meta = (entries[0] or {}).get("metadata") or {}
        debit_total = round(
            sum(e["amount"] for e in entries if e["side"] == "debit"), 2,
        )
        credit_total = round(
            sum(e["amount"] for e in entries if e["side"] == "credit"), 2,
        )
        return {
            "success": True,
            "txn_group_id": txn_group_id,
            "meta": {
                "provider": meta.get("provider"),
                "settlement_reference": meta.get("settlement_reference"),
                "settlement_date": meta.get("settlement_date"),
                "bank_account_id": meta.get("bank_account_id"),
                "bank_account_name": meta.get("bank_account_name"),
                "transferred_amount": meta.get("transferred_amount") or 0,
                "commission": meta.get("commission") or 0,
                "commission_vat": meta.get("commission_vat") or 0,
                "settlement_fee": meta.get("settlement_fee") or 0,
            },
            "entries": entries,
            "debit_total": debit_total,
            "credit_total": credit_total,
            "balanced": abs(debit_total - credit_total) < 0.01,
        }

    @router.get("/registration-overview")
    async def registration_overview(
        user: dict = Depends(get_current_user),
    ):
        """Aggregate per-provider numbers needed by the registration page:

          • current_receivable: live general_ledger receivable (sales − refunds − closed settlements)
          • expected_total:    expected net_payable from compute_all_settlements
          • received_total:    sum of bnpl_settlement total_closed legs
          • difference:        expected − received
          • last_settlement:   most recent registered settlement
          • match_status:      green | yellow | red
        """
        uid = user["id"]
        from ledger_core import compute_balance
        # 1) Expected — reuse the existing summary engine.
        try:
            summary = await compute_all_settlements(db, uid, None, None)
        except Exception:  # noqa: BLE001
            summary = {"providers": []}
        expected_by_provider: dict = {}
        for p in (summary.get("providers") or []):
            prov = (p.get("provider") or "").lower()
            tots = p.get("totals") or {}
            expected_by_provider[prov] = round(
                float(tots.get("net_payable") or 0), 2,
            )

        out_providers = []
        for provider in PROVIDERS:
            recv = await compute_balance(
                db, user_id=uid, entity_type="payment_gateway",
                entity_id=provider, sub_account="receivable",
            )
            current_receivable = round(recv.get("net_balance") or 0, 2)

            # received_total = sum of credit legs (close-outs) for this provider
            recv_pipeline = [
                {"$match": {
                    "user_id": uid,
                    "entry_type": "bnpl_settlement",
                    "status": "posted",
                    "side": "credit",
                    "entity_type": "payment_gateway",
                    "entity_id": provider,
                }},
                {"$group": {
                    "_id": None,
                    "total": {"$sum": "$amount"},
                    "count": {"$sum": 1},
                }},
            ]
            received_total = 0.0
            received_count = 0
            async for row in db.general_ledger.aggregate(recv_pipeline):
                received_total = round(float(row.get("total") or 0), 2)
                received_count = int(row.get("count") or 0)

            # Last settlement (most recent close-out leg)
            last = await db.general_ledger.find_one(
                {"user_id": uid,
                 "entry_type": "bnpl_settlement",
                 "status": "posted",
                 "side": "credit",
                 "entity_type": "payment_gateway",
                 "entity_id": provider},
                {"_id": 0, "posted_at": 1, "amount": 1,
                 "metadata": 1, "txn_group_id": 1},
                sort=[("posted_at", -1)],
            )
            last_block = None
            if last:
                m = last.get("metadata") or {}
                last_block = {
                    "txn_group_id": last.get("txn_group_id"),
                    "posted_at": last.get("posted_at"),
                    "amount": last.get("amount"),
                    "reference": m.get("settlement_reference"),
                    "settlement_date": m.get("settlement_date"),
                    "bank_account_name": m.get("bank_account_name"),
                }

            expected_total = expected_by_provider.get(provider, 0.0)
            # Clamp small negative pre-cutoff drift to 0 — these come
            # from legacy refunds the engine still sees but which the
            # bridge will never book. Avoids a misleading red status
            # for merchants who have nothing yet to settle.
            if -1.0 < expected_total < 0:
                expected_total = 0.0
            difference = round(expected_total - received_total, 2)

            # Match status — tolerance: 0.5 SAR exact, then 5% bucket
            abs_diff = abs(difference)
            if abs_diff < 0.5:
                match_status = "green"
            elif expected_total > 0 and abs_diff / max(expected_total, 1) <= 0.05:
                match_status = "yellow"
            else:
                match_status = "red"
            if expected_total == 0 and received_total == 0 and current_receivable == 0:
                match_status = "green"   # nothing to settle yet

            out_providers.append({
                "provider": provider,
                "current_receivable": current_receivable,
                "expected_total": expected_total,
                "received_total": received_total,
                "received_count": received_count,
                "difference": difference,
                "last_settlement": last_block,
                "match_status": match_status,
            })

        return {"success": True, "providers": out_providers}

    @router.get("/import-preview/{provider}")
    async def import_preview(
        provider: str,
        user: dict = Depends(get_current_user),
        date_from: Optional[str] = Query(
            None, pattern=r"^\d{4}-\d{2}-\d{2}$",
        ),
        date_to: Optional[str] = Query(
            None, pattern=r"^\d{4}-\d{2}-\d{2}$",
        ),
        period: Optional[str] = Query(
            None, pattern=r"^(this_week|last_week|last_7d|last_14d|this_month|last_month)$",
        ),
    ):
        """Iter-223 — Auto Settlement Import preview.

        Returns the **pre-filled** values for the settlement
        registration form, computed from `compute_settlement_for_provider`
        (which already reconciles against an official Tamara file if
        the merchant uploaded one — surfaced via `data_source`).

        The frontend takes this payload and drops it straight into
        the AddSettlementModal so the user only reviews and approves.
        """
        from datetime import date, timedelta
        from datetime import datetime as _dt
        uid = user["id"]
        provider = (provider or "").lower()
        if provider not in PROVIDERS:
            raise HTTPException(400, f"unknown provider {provider}")

        # Period shorthand → date_from/date_to. The user-provided
        # explicit dates always win.
        # Iter-231 — provider-aware weekly cycles driven by the
        # MERCHANT's `bnpl_settings.invoice_weekdays` (set on
        # /integrations/bnpl). Falls back to provider defaults if
        # not customised.
        WEEKDAY_MAP = {"monday": 0, "tuesday": 1, "wednesday": 2,
                       "thursday": 3, "friday": 4, "saturday": 5,
                       "sunday": 6}
        _PROVIDER_DEFAULTS = {
            "tabby":  {"invoice": "monday", "transfer": "monday"},
            "tamara": {"invoice": "saturday", "transfer": "tuesday"},
        }
        settings_doc = await db.bnpl_settings.find_one(
            {"user_id": uid, "provider": provider},
            {"_id": 0, "invoice_weekdays": 1, "transfer_weekdays": 1,
             "statement_cycle_defaults_version": 1},
        ) or {}
        defaults_for_p = _PROVIDER_DEFAULTS.get(provider) or {}
        inv_wds = (settings_doc.get("invoice_weekdays")
                   or [defaults_for_p.get("invoice", "monday")])
        if provider == "tamara":
            from bnpl.config_store import TAMARA_STATEMENT_CYCLE_VERSION
            if (
                settings_doc.get("statement_cycle_defaults_version")
                != TAMARA_STATEMENT_CYCLE_VERSION
                and inv_wds == ["sunday"]
            ):
                inv_wds = ["saturday"]
        tr_wds  = (settings_doc.get("transfer_weekdays")
                   or [defaults_for_p.get("transfer", "tuesday")])
        if provider == "tabby":
            from bnpl.config_store import TABBY_STATEMENT_DEFAULTS_VERSION
            if (
                settings_doc.get("statement_cycle_defaults_version")
                != TABBY_STATEMENT_DEFAULTS_VERSION
                and tr_wds == ["tuesday", "wednesday"]
            ):
                tr_wds = ["monday"]
        invoice_wd = WEEKDAY_MAP.get(
            (inv_wds[0] or "").lower(),
            WEEKDAY_MAP[defaults_for_p.get("invoice", "monday")],
        )
        transfer_wd = WEEKDAY_MAP.get(
            (tr_wds[0] or "").lower(),
            WEEKDAY_MAP[defaults_for_p.get("transfer", "tuesday")],
        )
        # period_end = day BEFORE invoice_weekday
        # period_start = period_end − 6 days (7-day cycle)
        period_end_wd = (invoice_wd - 1) % 7
        period_start_wd = (period_end_wd - 6) % 7  # equivalent to invoice_wd

        if not (date_from and date_to) and period:
            today = date.today()
            wd = today.weekday()
            if period == "this_week":
                days_since_start = (wd - period_start_wd) % 7
                start = today - timedelta(days=days_since_start)
                end = start + timedelta(days=6)
            elif period == "last_week":
                days_since_end = (wd - period_end_wd) % 7
                # If today IS period_end (e.g. Sunday for Tabby),
                # "last_week" means the FULL previous cycle.
                if days_since_end == 0:
                    end = today - timedelta(days=7)
                else:
                    end = today - timedelta(days=days_since_end)
                start = end - timedelta(days=6)
            elif period == "last_7d":
                end = today
                start = today - timedelta(days=6)
            elif period == "last_14d":
                end = today
                start = today - timedelta(days=13)
            elif period == "this_month":
                start = today.replace(day=1)
                end = today
            elif period == "last_month":
                first_this = today.replace(day=1)
                end = first_this - timedelta(days=1)
                start = end.replace(day=1)
            else:
                start = today - timedelta(days=6)
                end = today
            date_from = start.isoformat()
            date_to = end.isoformat()

        s = await compute_settlement_for_provider(
            db, uid, provider, date_from, date_to,
        )
        if "error" in s:
            raise HTTPException(400, s["error"])

        tots = s.get("totals") or {}
        bank = s.get("bank") or {}

        # Pre-filled form values — clamp negatives to 0 because the
        # settlement bridge rejects negative amounts. A negative
        # net_payable means refunds exceeded sales in the window — the
        # user shouldn't be registering a "settlement" in that case.
        def _clamp(v: float) -> float:
            return round(max(float(v or 0), 0), 2)
        transferred_amount = _clamp(tots.get("net_payable") or 0)
        commission = _clamp(tots.get("commission") or 0)
        commission_vat = _clamp(tots.get("commission_vat") or 0)
        settlement_fee = _clamp(tots.get("settlement_fee") or 0)
        settlement_fee_vat = _clamp(tots.get("settlement_fee_vat") or 0)
        # Roll the settlement-fee VAT into the existing "settlement_fee"
        # line because the registration bridge has only one fee bucket.
        if settlement_fee_vat > 0:
            settlement_fee = round(settlement_fee + settlement_fee_vat, 2)

        # Auto-generated reference if none provided by the user.
        # Iter-246z — use Asia/Riyadh today, not UTC.
        from bnpl.timezone import today_riyadh
        _ry_today = today_riyadh()
        ref_default = (
            f"{provider.upper()}-{date_from or _ry_today.isoformat()}-AUTO"
            if date_from
            else f"{provider.upper()}-AUTO-{_ry_today.strftime('%Y%m%d')}"
        )

        # Iter-231 — settlement_date_value is now driven by the
        # merchant's `transfer_weekdays` config (saved at
        # /integrations/bnpl). We pick the FIRST date strictly AFTER
        # `date_to` whose weekday matches one of the configured
        # `transfer_weekdays`. Fallback: provider-default behaviour
        # (Tabby: +1 day, Tamara: +4 days) if config is empty/unusable.
        settlement_date_value = date_to
        if date_to:
            try:
                _dt_to = _dt.strptime(date_to, "%Y-%m-%d").date()
                from datetime import timedelta as _td
                target_weekdays = {
                    WEEKDAY_MAP[(w or "").lower()]
                    for w in (tr_wds or [])
                    if (w or "").lower() in WEEKDAY_MAP
                }
                picked = None
                if target_weekdays:
                    # Search up to 14 days forward (covers any weekly cycle).
                    for delta in range(1, 15):
                        day = _dt_to + _td(days=delta)
                        if day.weekday() in target_weekdays:
                            picked = day
                            break
                if picked is not None:
                    settlement_date_value = picked.isoformat()
                else:
                    # Legacy fallback (only if no transfer_weekdays config).
                    if provider == "tamara":
                        settlement_date_value = (
                            _dt_to + _td(days=4)
                        ).isoformat()
                    else:
                        settlement_date_value = (
                            _dt_to + _td(days=1)
                        ).isoformat()
            except Exception:  # noqa: BLE001
                pass

        return {
            "success": True,
            "provider": provider,
            "period": s.get("period") or {
                "from": date_from, "to": date_to,
            },
            "data_source": s.get("data_source") or "computed",
            "prefill": {
                "settlement_reference": ref_default,
                "settlement_date": settlement_date_value,
                "bank_account_id": bank.get("linked_account_id"),
                "bank_account_name": bank.get("linked_account_name"),
                "transferred_amount": transferred_amount,
                "commission": commission,
                "commission_vat": commission_vat,
                "settlement_fee": settlement_fee,
                "notes": (
                    f"تسوية مستوردة تلقائياً ({s.get('data_source') or 'computed'}) "
                    f"للفترة {date_from or '?'} → {date_to or '?'} "
                    f"(تاريخ إصدار الفاتورة: {settlement_date_value or '?'})"
                ),
            },
            "breakdown": {
                "gross_sales": tots.get("gross_sales") or 0,
                "total_refunds": tots.get("total_refunds") or 0,
                "net_sales": tots.get("net_sales") or 0,
                "commission": commission,
                "commission_vat": commission_vat,
                "settlement_fee_raw": tots.get("settlement_fee") or 0,
                "settlement_fee_vat": settlement_fee_vat,
                "settlement_fee_total": settlement_fee,
                "transactions_count": tots.get("transactions_count") or 0,
                "refunds_count": tots.get("refunds_count") or 0,
                "net_payable": transferred_amount,
                # Iter-246s — SSOT proof for the registration modal.
                # The frontend MUST verify `engine_version == "iter246r"`
                # before allowing the merchant to save. Older deployments
                # without the Tamara historical-pin + Net-Zero filters
                # will produce inflated Gross numbers — the modal will
                # surface a red warning and block save in that case.
                "engine_version": s.get("engine_version", "unknown"),
            },
            "bank_reconciliation": bank,
        }

    @router.get("/reconciliation")
    async def settlement_reconciliation(
        user: dict = Depends(get_current_user),
        date_from: Optional[str] = Query(
            None, pattern=r"^\d{4}-\d{2}-\d{2}$",
        ),
        date_to: Optional[str] = Query(
            None, pattern=r"^\d{4}-\d{2}-\d{2}$",
        ),
    ):
        """Iter-223 — Expected vs Actual reconciliation per provider.

        Expected = compute_settlement_for_provider().net_payable
        Actual   = sum of bnpl_settlement total_closed legs in window
        """
        uid = user["id"]
        rows = []
        # Build a date window for the registered settlements query
        # using `settlement_date` metadata.
        for provider in PROVIDERS:
            s = await compute_settlement_for_provider(
                db, uid, provider, date_from, date_to,
            )
            expected = round(
                float((s.get("totals") or {}).get("net_payable") or 0), 2,
            )

            match: dict = {
                "user_id": uid,
                "entry_type": "bnpl_settlement",
                "status": "posted",
                "side": "credit",
                "entity_type": "payment_gateway",
                "entity_id": provider,
            }
            if date_from or date_to:
                cond: dict = {}
                if date_from:
                    cond["$gte"] = date_from
                if date_to:
                    cond["$lte"] = date_to
                match["metadata.settlement_date"] = cond

            agg = [
                {"$match": match},
                {"$group": {
                    "_id": None,
                    "total": {"$sum": "$amount"},
                    "count": {"$sum": 1},
                }},
            ]
            actual = 0.0
            count = 0
            async for row in db.general_ledger.aggregate(agg):
                actual = round(float(row.get("total") or 0), 2)
                count = int(row.get("count") or 0)

            diff = round(expected - actual, 2)
            abs_diff = abs(diff)
            if abs_diff < 0.5:
                status = "green"
            elif expected > 0 and abs_diff / max(expected, 1) <= 0.05:
                status = "yellow"
            elif expected == 0 and actual == 0:
                status = "green"
            else:
                status = "red"

            rows.append({
                "provider": provider,
                "expected": expected,
                "actual": actual,
                "difference": diff,
                "count": count,
                "match_status": status,
                "data_source": s.get("data_source") or "computed",
            })

        return {
            "success": True,
            "period": {"from": date_from, "to": date_to},
            "rows": rows,
        }

    @router.get("/{provider}")
    async def provider_settlement(
        provider: str,
        user: dict = Depends(get_current_user),
        from_date: Optional[str] = Query(None, alias="from",
                                         pattern=r"^\d{4}-\d{2}-\d{2}$"),
        to_date: Optional[str] = Query(None, alias="to",
                                       pattern=r"^\d{4}-\d{2}-\d{2}$"),
    ):
        """Settlement for a single provider — used by the per-provider
        detail drawer on the frontend."""
        if provider not in PROVIDERS:
            return {"success": False, "error": f"unknown provider {provider}"}
        try:
            return {
                "success": True,
                **(await compute_settlement_for_provider(
                    db, user["id"], provider, from_date, to_date,
                )),
            }
        except Exception as e:  # noqa: BLE001
            return {"success": False, "error": f"{type(e).__name__}: {e}"}

    @router.get("/weekly/{provider}")
    async def weekly_settlements(
        provider: str,
        user: dict = Depends(get_current_user),
        from_date: Optional[str] = Query(None, alias="from",
                                         pattern=r"^\d{4}-\d{2}-\d{2}$"),
        to_date: Optional[str] = Query(None, alias="to",
                                       pattern=r"^\d{4}-\d{2}-\d{2}$"),
    ):
        """List of weekly settlements for ONE provider (one row per
        weekly invoice).  Default range = activation_date → today."""
        if provider not in PROVIDERS:
            return {"success": False, "error": f"unknown provider {provider}"}
        try:
            rows = await compute_weekly_settlements(
                db, user["id"], provider, from_date, to_date,
            )
            totals = {
                k: round(sum(r.get(k, 0) for r in rows), 2)
                for k in ("gross_sales", "total_refunds", "net_sales",
                         "commission", "commission_vat", "settlement_fee",
                         "settlement_fee_vat",   # Iter-134
                         "net_payable", "transferred_amount",
                         "remaining_with_provider")
            }
            totals["invoices_count"] = len(rows)
            return {
                "success": True,
                "provider": provider,
                "rows": rows,
                "totals": totals,
                "range": {
                    "from": (rows[0]["from"] if rows else from_date),
                    "to":   (rows[-1]["to"] if rows else to_date),
                },
            }
        except Exception as e:  # noqa: BLE001
            return {"success": False, "error": f"{type(e).__name__}: {e}"}

    @router.get("/balances/canonical")
    async def canonical_balances(user: dict = Depends(get_current_user)):
        """Single Source of Truth for Tabby + Tamara balances.  Every
        page that shows a BNPL balance should call this endpoint so
        all pages agree on the number."""
        try:
            from .balance_service import get_all_bnpl_balances
            balances = await get_all_bnpl_balances(db, user["id"])
            return {
                "success": True,
                "balances": balances,
                "total": round(sum(float(b["balance"] or 0) for b in balances), 2),
                "formula_doc": (
                    "balance = gross_sales − refunds − commission − VAT "
                    "− settlement_fee − transferred_to_bank"
                ),
            }
        except Exception as e:  # noqa: BLE001
            return {"success": False, "error": f"{type(e).__name__}: {e}"}

    @router.get("/matching/{provider}")
    async def matching_for_provider(
        provider: str,
        user: dict = Depends(get_current_user),
        from_date: Optional[str] = Query(None, alias="from",
                                         pattern=r"^\d{4}-\d{2}-\d{2}$"),
        to_date: Optional[str] = Query(None, alias="to",
                                       pattern=r"^\d{4}-\d{2}-\d{2}$"),
    ):
        """Phase 4-B — auto-match weekly invoices with bank transfers.

        Returns each invoice's match status (`matched` / `unmatched`
        / `over` / `under`) plus the list of leftover transfers that
        the system could not assign to any invoice.  Read-only — no
        DB writes."""
        if provider not in PROVIDERS:
            return {"success": False, "error": f"unknown provider {provider}"}
        try:
            from .matching_service import compute_matches_for_provider
            return {
                "success": True,
                **(await compute_matches_for_provider(
                    db, user["id"], provider, from_date, to_date,
                )),
            }
        except Exception as e:  # noqa: BLE001
            return {"success": False, "error": f"{type(e).__name__}: {e}"}

    parent_router.include_router(router)
