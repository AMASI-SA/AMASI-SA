"""Tamara audit report (Iter-116) — forensic verification of synced data.

Answers: are these 558 records genuinely unique orders, or just multiple
transactions per order?  Compares sum-of-amounts between
`payment_transactions` (raw from Tamara) and `unified_orders` (after
merge) to prove no double-counting or loss.
"""
from __future__ import annotations

from typing import Any, Dict, List

from fastapi import APIRouter, Depends, Request

from auth import get_current_user_from_db


def attach_bnpl_audit_routes(parent_router: APIRouter, db) -> None:
    async def current_user(request: Request) -> dict:
        return await get_current_user_from_db(request, db)

    router = APIRouter(prefix="/bnpl", tags=["bnpl-audit"])

    @router.get("/tamara/audit")
    async def tamara_audit(user: dict = Depends(current_user)):
        uid = user["id"]
        ptx_filter = {"user_id": uid, "provider": "tamara"}

        total_transactions = await db.payment_transactions.count_documents(ptx_filter)

        # 1) Unique orders by order_reference_id (drop empty refs)
        unique_refs_pipeline = [
            {"$match": ptx_filter},
            {"$match": {"order_reference_id": {"$nin": [None, ""]}}},
            {"$group": {"_id": "$order_reference_id"}},
            {"$count": "n"},
        ]
        unique_orders = 0
        async for r in db.payment_transactions.aggregate(unique_refs_pipeline):
            unique_orders = r.get("n", 0)

        # transactions without any reference (orphans)
        orphan_ptx = await db.payment_transactions.count_documents({
            **ptx_filter,
            "$or": [
                {"order_reference_id": {"$in": [None, ""]}},
                {"order_reference_id": {"$exists": False}},
            ],
        })

        # 2-3) Status breakdown
        status_pipeline = [
            {"$match": ptx_filter},
            {"$group": {"_id": "$status", "n": {"$sum": 1}}},
        ]
        by_status: Dict[str, int] = {}
        async for r in db.payment_transactions.aggregate(status_pipeline):
            by_status[(r.get("_id") or "").lower()] = r.get("n", 0)

        # 4) Refunds count + total refund amount
        refund_count = await db.payment_refunds.count_documents({
            "user_id": uid, "provider": "tamara",
        })
        refund_total = 0.0
        async for r in db.payment_refunds.aggregate([
            {"$match": {"user_id": uid, "provider": "tamara"}},
            {"$group": {"_id": None, "s": {"$sum": "$amount"}}},
        ]):
            refund_total = float(r.get("s") or 0)

        # 4b) Refunded-status orders vs explicit refund records — proves
        # whether per-refund detail is missing from Tamara payloads.
        refunded_status_count = (
            by_status.get("fully_refunded", 0)
            + by_status.get("partially_refunded", 0)
            + by_status.get("refunded", 0)
        )
        refunded_amount_in_ptx = 0.0
        ptx_with_refund_count = 0
        async for r in db.payment_transactions.aggregate([
            {"$match": {**ptx_filter, "refunded_amount": {"$gt": 0}}},
            {"$group": {
                "_id": None,
                "n": {"$sum": 1},
                "s": {"$sum": "$refunded_amount"},
            }},
        ]):
            ptx_with_refund_count = int(r.get("n") or 0)
            refunded_amount_in_ptx = float(r.get("s") or 0)
        refunds_comparison = {
            "orders_with_refund_status": refunded_status_count,
            "payment_refunds_records": refund_count,
            "payment_transactions_with_refunded_gt_0": ptx_with_refund_count,
            "refunded_amount_in_transactions": round(refunded_amount_in_ptx, 2),
            "refunded_amount_in_refund_records": round(refund_total, 2),
            "delta_records_vs_status": refunded_status_count - refund_count,
            "diagnosis": (
                "✓ matched — every refunded order has a detail record"
                if refunded_status_count == refund_count else
                f"⚠️ {refunded_status_count - refund_count} orders have a "
                "refunded status but no per-refund detail record. "
                "Tamara's /merchants/orders/reference-id/{ref} endpoint "
                "may not include refunds[] for legacy orders. "
                "Re-run Tamara backfill with the fixed extractor — it "
                "now synthesises an aggregate refund row from "
                "total_refunded_amount when detail is missing."
            ),
        }

        # 5) Orders with >1 transactions
        dup_pipeline = [
            {"$match": ptx_filter},
            {"$match": {"order_reference_id": {"$nin": [None, ""]}}},
            {"$group": {"_id": "$order_reference_id", "n": {"$sum": 1}}},
            {"$match": {"n": {"$gt": 1}}},
            {"$count": "n"},
        ]
        orders_with_multi_tx = 0
        async for r in db.payment_transactions.aggregate(dup_pipeline):
            orders_with_multi_tx = r.get("n", 0)

        # 6) Sum from API (payment_transactions)
        api_sum_pipeline = [
            {"$match": ptx_filter},
            {"$group": {
                "_id": None,
                "amount":   {"$sum": "$amount"},
                "captured": {"$sum": "$captured_amount"},
                "refunded": {"$sum": "$refunded_amount"},
            }},
        ]
        api_sums = {"amount": 0.0, "captured": 0.0, "refunded": 0.0}
        async for r in db.payment_transactions.aggregate(api_sum_pipeline):
            api_sums["amount"] = float(r.get("amount") or 0)
            api_sums["captured"] = float(r.get("captured") or 0)
            api_sums["refunded"] = float(r.get("refunded") or 0)

        # 7) Sum in unified_orders for those touched by tamara
        uo_sum_pipeline = [
            {"$match": {"user_id": uid, "sources_seen": "tamara"}},
            {"$group": {
                "_id": None,
                "gross":    {"$sum": "$gross_amount"},
                "paid":     {"$sum": "$paid_amount"},
                "refunded": {"$sum": "$refunded_amount"},
                "count":    {"$sum": 1},
            }},
        ]
        uo_sums = {"gross": 0.0, "paid": 0.0, "refunded": 0.0, "count": 0}
        async for r in db.payment_transactions.aggregate(uo_sum_pipeline):
            pass  # placeholder
        async for r in db.unified_orders.aggregate(uo_sum_pipeline):
            uo_sums["gross"] = float(r.get("gross") or 0)
            uo_sums["paid"] = float(r.get("paid") or 0)
            uo_sums["refunded"] = float(r.get("refunded") or 0)
            uo_sums["count"] = int(r.get("count") or 0)

        # 8) Difference (API vs unified)
        delta = {
            "amount_vs_gross": round(
                api_sums["amount"] - uo_sums["gross"], 2),
            "captured_vs_paid": round(
                api_sums["captured"] - uo_sums["paid"], 2),
            "refunded_vs_refunded": round(
                api_sums["refunded"] - uo_sums["refunded"], 2),
        }

        # Sample of duplicate orders so the user can inspect them
        sample_dups: List[Dict[str, Any]] = []
        async for r in db.payment_transactions.aggregate([
            {"$match": ptx_filter},
            {"$match": {"order_reference_id": {"$nin": [None, ""]}}},
            {"$group": {
                "_id": "$order_reference_id",
                "n": {"$sum": 1},
                "statuses": {"$push": "$status"},
                "amounts":  {"$push": "$amount"},
                "provider_ids": {"$push": "$provider_id"},
                "created_at": {"$push": "$created_at_provider"},
            }},
            {"$match": {"n": {"$gt": 1}}},
            {"$sort": {"n": -1}},
            {"$limit": 10},
        ]):
            sample_dups.append({
                "order_reference_id": r["_id"],
                "transactions": r["n"],
                "statuses": r["statuses"],
                "amounts": r["amounts"],
                "provider_ids": r["provider_ids"],
                "created_at": r["created_at"],
            })

        # Verdict
        verdict = "✓ بياناتك نظيفة" if (
            delta["captured_vs_paid"] == 0 and
            delta["refunded_vs_refunded"] == 0 and
            orders_with_multi_tx == 0
        ) else (
            f"⚠️ يوجد {orders_with_multi_tx} طلب بأكثر من معاملة "
            "(طبيعي إذا كانت العملية authorise ثم capture ثم refund "
            "كصفوف منفصلة في API)" if orders_with_multi_tx > 0 else
            "⚠️ هناك فرق في المبالغ — راجع delta"
        )

        return {
            "ok": True,
            "summary": {
                "total_transactions_synced": total_transactions,
                "unique_orders": unique_orders,
                "orphan_transactions_no_ref": orphan_ptx,
                "orders_with_multi_transactions": orders_with_multi_tx,
                "refund_count": refund_count,
                "refund_total_amount": round(refund_total, 2),
            },
            "by_status": by_status,
            "by_status_key_hints": {
                "authorised":          by_status.get("authorised", 0)
                                       + by_status.get("authorized", 0),
                "fully_captured":      by_status.get("fully_captured", 0)
                                       + by_status.get("captured", 0),
                "partially_captured":  by_status.get("partially_captured", 0),
                "canceled":            by_status.get("canceled", 0)
                                       + by_status.get("cancelled", 0),
                "fully_refunded":      by_status.get("fully_refunded", 0)
                                       + by_status.get("refunded", 0),
                "partially_refunded":  by_status.get("partially_refunded", 0),
            },
            "amounts_from_api": api_sums,
            "amounts_in_unified_orders": uo_sums,
            "delta": delta,
            "refunds_comparison": refunds_comparison,
            "sample_duplicate_orders": sample_dups,
            "verdict": verdict,
        }

    @router.post("/tamara/refund-inspect")
    async def tamara_refund_inspect(user: dict = Depends(current_user)):
        """Pull the RAW JSON of one refunded order from Tamara so we can
        see exactly which fields/paths carry refund information for
        this merchant's account.
        """
        from .clients.tamara import TamaraClient, TamaraError
        from .config_store import DEFAULTS, get_raw_secrets

        uid = user["id"]
        secrets = await get_raw_secrets(db, uid, "tamara")
        if not secrets.get("api_token"):
            return {"ok": False, "error": "Tamara api_token not set"}

        # Find one refunded order in our payment_transactions
        sample = await db.payment_transactions.find_one(
            {"user_id": uid, "provider": "tamara",
             "status": {"$in": ["fully_refunded", "partially_refunded",
                                "refunded"]}},
            {"_id": 0, "order_reference_id": 1, "provider_id": 1,
             "status": 1, "refunded_amount": 1},
        )
        if not sample:
            return {"ok": True, "note": "No refunded order in local data."}

        client = TamaraClient(
            api_token=secrets["api_token"],
            base_url=(secrets.get("api_base_url")
                      or DEFAULTS["tamara"]["api_base_url"]),
        )

        ref = sample.get("order_reference_id") or ""
        order_id = sample.get("provider_id") or ""

        report: Dict[str, Any] = {
            "ok": True,
            "sample_order_used": {
                "order_reference_id": ref,
                "tamara_order_id": order_id,
                "status_in_local_data": sample.get("status"),
                "refunded_amount_in_local": sample.get("refunded_amount"),
            },
        }

        async def _probe(label: str, coro):
            try:
                raw = await coro
                if not isinstance(raw, dict):
                    raw = {"_non_dict_response": str(raw)[:300]}
                # Surface the refund-related keys explicitly
                report[label] = {
                    "status": raw.get("status"),
                    "total_amount":           raw.get("total_amount"),
                    "total_refunded_amount":  raw.get("total_refunded_amount"),
                    "refunded_amount":        raw.get("refunded_amount"),
                    "refunds_array_len":      len(raw.get("refunds") or []),
                    "refund_orders_array_len": len(raw.get("refund_orders") or []),
                    "captures_count":         len(raw.get("captures") or []),
                    "first_capture_refunds_len": len(
                        ((raw.get("captures") or [{}])[0] or {}).get("refunds") or []
                    ) if (raw.get("captures") or []) else 0,
                    "all_top_level_keys": sorted(raw.keys()),
                    "raw_truncated": str(raw)[:1500],
                }
            except TamaraError as exc:
                report[label] = {"error": str(exc)}

        if order_id:
            await _probe("by_order_id", client.get_order_by_id(order_id))
        if ref:
            await _probe("by_reference_id", client.get_order_by_reference(ref))

        return report



    @router.post("/tamara/rebuild-refunds")
    async def tamara_rebuild_refunds(user: dict = Depends(current_user)):
        """One-shot reconstruction of `payment_refunds` from existing
        `payment_transactions` data — for orders whose Tamara payload
        carried `refunded_amount > 0` but no per-refund detail array
        (so the original backfill never created the refund row).

        Idempotent: uses the deterministic `synthetic:{provider_id}`
        refund_id so re-runs of this endpoint don't create duplicates.
        Skips transactions that already have an associated refund row.
        """
        import uuid
        from datetime import datetime, timezone

        uid = user["id"]
        scanned = 0
        created = 0
        already_had = 0
        amount_total = 0.0

        async for ptx in db.payment_transactions.find(
            {"user_id": uid, "provider": "tamara",
             "refunded_amount": {"$gt": 0}},
            {"_id": 0, "provider_id": 1, "order_reference_id": 1,
             "status": 1, "refunded_amount": 1, "currency": 1,
             "updated_at_provider": 1, "created_at_provider": 1},
        ):
            scanned += 1
            pid = (ptx.get("provider_id") or "").strip()
            ref = (ptx.get("order_reference_id") or "").strip()
            amt = float(ptx.get("refunded_amount") or 0)
            if not pid or amt <= 0:
                continue

            synthetic_id = f"synthetic:{pid}"

            # Does ANY refund row already exist for this payment?
            existing = await db.payment_refunds.find_one(
                {"user_id": uid, "provider": "tamara",
                 "provider_payment_id": pid},
                {"_id": 0, "id": 1},
            )
            if existing:
                already_had += 1
                continue

            now_iso = datetime.now(timezone.utc).isoformat()
            doc = {
                "id": str(uuid.uuid4()),
                "user_id": uid,
                "provider": "tamara",
                "provider_payment_id": pid,
                "provider_refund_id": synthetic_id,
                "order_reference_id": ref,
                "amount": round(amt, 2),
                "currency": ptx.get("currency") or "SAR",
                "status": (ptx.get("status") or "").lower(),
                "reason": "rebuilt from payment_transactions.refunded_amount",
                "refunded_at": (
                    ptx.get("updated_at_provider")
                    or ptx.get("created_at_provider") or ""
                ),
                "raw": {"_rebuilt_from": "payment_transactions"},
                "synced_at": now_iso,
                "created_at": now_iso,
                "synthesised": True,
            }
            try:
                await db.payment_refunds.insert_one(doc)
                created += 1
                amount_total += amt
            except Exception as exc:  # noqa: BLE001
                # Unique index collision → another concurrent run already
                # inserted the same synthetic row. Treat as already-had.
                already_had += 1
                _ = exc

        # Also refresh unified_orders.refunded_amount in case it was
        # zero on rows whose payment_transactions DO show a refund.
        uo_synced = 0
        async for ptx in db.payment_transactions.find(
            {"user_id": uid, "provider": "tamara",
             "refunded_amount": {"$gt": 0}},
            {"_id": 0, "order_reference_id": 1, "refunded_amount": 1},
        ):
            ref = (ptx.get("order_reference_id") or "").strip()
            if not ref:
                continue
            res = await db.unified_orders.update_one(
                {"user_id": uid,
                 "$or": [{"order_reference_id": ref},
                         {"order_number": ref}],
                 "refunded_amount": {"$lt": float(ptx["refunded_amount"])}},
                {"$set": {"refunded_amount": float(ptx["refunded_amount"])}},
            )
            if res.modified_count > 0:
                uo_synced += 1

        return {
            "ok": True,
            "scanned_transactions": scanned,
            "refund_records_created": created,
            "already_had_records": already_had,
            "total_amount_reconstructed": round(amount_total, 2),
            "unified_orders_synced": uo_synced,
            "verdict": (
                f"✓ Created {created} new payment_refunds records "
                f"(SAR {round(amount_total, 2)}). "
                + ("Re-run Tamara Audit — delta should now be 0."
                   if created > 0 else "Nothing to rebuild.")
            ),
        }

    parent_router.include_router(router)
