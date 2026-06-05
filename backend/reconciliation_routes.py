"""Reconciliation API — Phase 2.2.

Shows for each payment platform:
- expected   = `expected_orders_balance` from accounts (the gross amount
               Salla/Tabby/etc still owes the merchant from collected orders).
- transferred = Σ outgoing `internal_transfer` rows from this account to ANY
                bank account (account_type="bank") in `account_transactions`.
- pending     = expected - transferred
- collection_rate = transferred / expected * 100
- last_transfer_at + last_transfer_to_bank_name from the same ledger.

Two endpoints:
- GET /api/reconciliation/summary             → grand totals + per-platform rows
- GET /api/reconciliation/platform/{account_id} → details + outgoing transfers

No alerts, no 14-day window, no statement matching — that's Phase 2.3+.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from auth import get_current_user_from_db


def attach_reconciliation_routes(parent_router: APIRouter, db) -> None:
    router = APIRouter(prefix="/reconciliation", tags=["reconciliation"])

    async def current_user(request: Request) -> dict:
        return await get_current_user_from_db(request, db)

    async def _platform_row(uid: str, acc: dict) -> dict:
        """Compute reconciliation numbers for ONE payment_platform account.

        `transferred` = sum of internal_transfer OUT rows whose peer is a bank.
        We filter peer-side by joining with the accounts collection — keeps
        the query simple without storing peer_account_type on every tx row.
        """
        # All outgoing internal_transfer rows from this account
        out_rows = await db.account_transactions.find(
            {
                "user_id": uid,
                "account_id": acc["id"],
                "transaction_type": "internal_transfer",
                "direction": "out",
            },
            {"_id": 0, "amount": 1, "peer_account_id": 1, "transaction_date": 1,
             "peer_account_name": 1, "created_at": 1},
        ).sort([("transaction_date", -1), ("created_at", -1)]).to_list(10000)

        # Resolve which peers are banks (in one batched lookup).
        peer_ids = list({r.get("peer_account_id") for r in out_rows if r.get("peer_account_id")})
        bank_ids: set[str] = set()
        if peer_ids:
            async for p in db.accounts.find(
                {"user_id": uid, "id": {"$in": peer_ids}, "account_type": "bank"},
                {"_id": 0, "id": 1},
            ):
                bank_ids.add(p["id"])

        transferred = 0.0
        transfers_count = 0
        last_transfer_at: str | None = None
        last_transfer_to_bank: str | None = None
        for r in out_rows:
            if r.get("peer_account_id") in bank_ids:
                transferred += float(r.get("amount") or 0)
                transfers_count += 1
                if last_transfer_at is None:
                    last_transfer_at = r.get("transaction_date")
                    last_transfer_to_bank = r.get("peer_account_name")

        expected = round(float(acc.get("expected_orders_balance") or 0), 2)
        current_balance = round(float(acc.get("current_balance") or 0), 2)
        transferred = round(transferred, 2)
        pending = round(expected - transferred, 2)
        rate = round((transferred / expected * 100), 2) if expected > 0 else 0.0

        return {
            "account_id": acc["id"],
            "name": acc["name"],
            "normalized_payment_method": acc.get("normalized_payment_method"),
            "orders_count": int(acc.get("orders_count") or 0),
            "expected": expected,
            "transferred": transferred,
            "pending": pending,
            "current_balance": current_balance,
            "collection_rate": rate,
            "transfers_count": transfers_count,
            "last_transfer_at": last_transfer_at,
            "last_transfer_to_bank": last_transfer_to_bank,
            "currency": acc.get("currency") or "SAR",
        }

    @router.get("/summary")
    async def reconciliation_summary(user: dict = Depends(current_user)):
        """Powers the main /reconciliation page — grand totals + per-platform rows
        + a `transparency` block that explains any gap between Reports total
        sales and Accounts total assets (waiting, empty payment_method, …).
        """
        uid = user["id"]
        accs = await db.accounts.find(
            {"user_id": uid, "account_type": "payment_platform", "status": {"$ne": "hidden"}},
            {"_id": 0},
        ).sort("expected_orders_balance", -1).to_list(1000)

        rows = [await _platform_row(uid, a) for a in accs]
        total_expected = round(sum(r["expected"] for r in rows), 2)
        total_transferred = round(sum(r["transferred"] for r in rows), 2)
        total_pending = round(total_expected - total_transferred, 2)
        overall_rate = (
            round(total_transferred / total_expected * 100, 2) if total_expected > 0 else 0.0
        )

        # ── Transparency: compute Reports total_sales vs Accounts total
        # using IDENTICAL filters (status whitelist + hide_inferred_date_orders).
        # Then break down the gap into named buckets the user can verify.
        from auth import ensure_user_settings  # local import to avoid cycle
        from payment_methods import resolve_account_key as _resolve
        import re as _re
        settings = await ensure_user_settings(db, uid)
        included = settings.get("report_included_statuses") or []
        match_stage: dict = {"user_id": uid}
        if included:
            match_stage["$or"] = [
                {"order_status": {"$regex": _re.escape(s), "$options": "i"}}
                for s in included if s
            ]
        if settings.get("hide_inferred_date_orders"):
            match_stage["order_date_inferred"] = {"$ne": True}

        pipeline = [
            {"$match": match_stage},
            {"$group": {
                "_id": {"$ifNull": ["$payment_method", ""]},
                "amount": {"$sum": {"$ifNull": ["$total_amount", 0]}},
                "count":  {"$sum": 1},
            }},
        ]
        in_accounts_amount = 0.0
        in_accounts_orders = 0
        unclassified_buckets: dict[str, dict] = {}
        empty_payment_amount = 0.0
        empty_payment_orders = 0
        async for r in db.unified_orders.aggregate(pipeline):
            raw = (r.get("_id") or "").strip()
            amt = float(r.get("amount") or 0)
            cnt = int(r.get("count") or 0)
            if not raw:
                empty_payment_amount += amt
                empty_payment_orders += cnt
                continue
            key, _disp = _resolve(raw)
            if key is None:
                slot = unclassified_buckets.setdefault(raw, {"raw": raw, "amount": 0.0, "count": 0})
                slot["amount"] += amt
                slot["count"] += cnt
            else:
                in_accounts_amount += amt
                in_accounts_orders += cnt

        unclassified_amount = round(sum(b["amount"] for b in unclassified_buckets.values()), 2)
        empty_payment_amount = round(empty_payment_amount, 2)
        in_accounts_amount = round(in_accounts_amount, 2)
        total_sales = round(
            in_accounts_amount + unclassified_amount + empty_payment_amount, 2
        )
        gap = round(total_sales - in_accounts_amount, 2)

        return {
            "totals": {
                "expected": total_expected,
                "transferred": total_transferred,
                "pending": total_pending,
                "collection_rate": overall_rate,
            },
            "platforms": rows,
            # User-facing transparency block — explains every riyal that's
            # in Reports total_sales but NOT in Accounts total_assets.
            "transparency": {
                "total_sales": total_sales,
                "in_accounts": in_accounts_amount,
                "in_accounts_orders": in_accounts_orders,
                "unclassified_amount": unclassified_amount,
                "unclassified_orders": sum(b["count"] for b in unclassified_buckets.values()),
                "unclassified_buckets": sorted(
                    [
                        {"raw": b["raw"], "amount": round(b["amount"], 2), "count": b["count"]}
                        for b in unclassified_buckets.values()
                    ],
                    key=lambda x: -x["amount"],
                ),
                "empty_payment_method_amount": empty_payment_amount,
                "empty_payment_method_orders": empty_payment_orders,
                "gap": gap,
                "filters_applied": {
                    "report_included_statuses": included,
                    "hide_inferred_date_orders": bool(
                        settings.get("hide_inferred_date_orders")
                    ),
                },
            },
        }

    @router.get("/platform/{account_id}")
    async def reconciliation_platform(
        account_id: str, user: dict = Depends(current_user)
    ):
        """Drill-down view: one platform + every outgoing transfer to a bank."""
        uid = user["id"]
        acc = await db.accounts.find_one(
            {"id": account_id, "user_id": uid, "account_type": "payment_platform"},
            {"_id": 0},
        )
        if not acc:
            raise HTTPException(404, "حساب منصة الدفع غير موجود.")
        summary = await _platform_row(uid, acc)

        # Fetch every outgoing internal_transfer + enrich with destination
        # bank name. We hydrate from the `transfers` envelope so the user
        # also sees the reference / notes / attachment.
        transfers = await db.transfers.find(
            {"user_id": uid, "from_account_id": account_id},
            {"_id": 0},
        ).sort([("transfer_date", -1), ("created_at", -1)]).to_list(2000)

        # Filter to only those whose destination is a bank.
        if transfers:
            dest_ids = list({t["to_account_id"] for t in transfers})
            bank_lookup: dict[str, dict] = {}
            async for p in db.accounts.find(
                {"user_id": uid, "id": {"$in": dest_ids}},
                {"_id": 0, "id": 1, "name": 1, "account_type": 1},
            ):
                bank_lookup[p["id"]] = p
            transfers = [
                {
                    **t,
                    "to_account_name": (bank_lookup.get(t["to_account_id"]) or {}).get("name") or "—",
                    "to_account_type": (bank_lookup.get(t["to_account_id"]) or {}).get("account_type"),
                }
                for t in transfers
                if (bank_lookup.get(t["to_account_id"]) or {}).get("account_type") == "bank"
            ]

        return {"summary": summary, "transfers": transfers}

    parent_router.include_router(router)
