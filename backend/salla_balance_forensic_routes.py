"""Iter-250b · P1.5.c — Salla Balance Forensic (READ-ONLY).

Investigates the discrepancy between:
  • Salla panel's REAL balance (provided by merchant)
  • System's computed balance (current_balance / SSOT / ledger / etc.)

For one specific Salla payment_platform account, this endpoint:

1. Reads the stored fields on `accounts`:
     - current_balance         (stored cache)
     - expected_orders_balance (central metrics SSOT)
     - opening_balance / opening_balance_date
2. Computes the SSOT live balance via `account_balance_ssot()`
3. Computes account_transactions walk (legacy)
4. Computes general_ledger nets for sub_account ∈ {main, balance}
5. Lists the LAST N settlement_files for provider="salla"
6. Lists the LAST N settlement_entries for provider="salla"
7. Lists the LAST N payment_adjustments for provider="salla"
8. (Optional) Searches for a missing amount across all settlement
   collections to determine if the merchant's expected settlement
   reached our system.

STRICT READ-ONLY · NO writes · NO imports · NO recomputes.

Endpoint::

    GET /api/diagnostics/salla-balance-forensic
        ?account_id=<optional>
        &search_amount=<optional float>
        &search_tolerance=<float, default 1.0>
        &lookback_days=<int, default 14>
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query


def _r(n) -> float:
    return round(float(n or 0), 2)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _within(amt: float, target: float, tol: float) -> bool:
    return abs(float(amt or 0) - target) <= tol


def make_salla_balance_forensic_router(db, current_user):
    router = APIRouter(tags=["diagnostics", "salla-balance-forensic"])

    @router.get("/diagnostics/salla-balance-forensic")
    async def salla_forensic(
        account_id: Optional[str] = Query(None),
        search_amount: Optional[float] = Query(None),
        search_tolerance: float = Query(1.0, ge=0.0, le=100.0),
        lookback_days: int = Query(14, ge=1, le=365),
        list_limit: int = Query(20, ge=1, le=100),
        real_balance_at_provider: Optional[float] = Query(
            None,
            description=(
                "Optional: merchant-reported REAL balance on the "
                "Salla panel right now (in SAR). Used to compute "
                "the exact gap reconciliation.")),
        user: dict = Depends(current_user),
    ):
        uid = user["id"]

        # ── 1. Resolve the Salla account ────────────────────────────
        if account_id:
            acc = await db.accounts.find_one(
                {"user_id": uid, "id": account_id}, {"_id": 0})
            if not acc:
                raise HTTPException(404, "Account not found")
        else:
            # Pick the Salla payment_platform account (by canonical key)
            acc = await db.accounts.find_one(
                {"user_id": uid,
                 "account_type": "payment_platform",
                 "normalized_payment_method": "salla"},
                {"_id": 0},
            )
            if not acc:
                raise HTTPException(
                    404,
                    "Salla payment_platform account not found. "
                    "Pass account_id explicitly.",
                )

        # ── 2. Stored / cached fields ───────────────────────────────
        stored_current_balance = _r(acc.get("current_balance"))
        stored_opening_balance = _r(acc.get("opening_balance"))
        stored_opening_at = acc.get("opening_balance_date")
        stored_expected = _r(acc.get("expected_orders_balance"))
        stored_orders_count = int(acc.get("orders_count") or 0)
        last_synced = acc.get("last_synced_at") or acc.get("updated_at")

        # ── 3. SSOT live balance ───────────────────────────────────
        ssot_value: Optional[float] = None
        ssot_error: Optional[str] = None
        try:
            from financial_position_ssot import (
                account_balance_ssot as _ssot,
            )
            ssot_value = _r(
                await _ssot(db, user_id=uid, account=acc))
        except Exception as e:  # noqa: BLE001
            ssot_error = repr(e)

        # ── 4. Ledger nets (main + balance) ────────────────────────
        async def _gl_net(sub: Optional[str]) -> Dict[str, Any]:
            match = {
                "user_id": uid,
                "entity_type": "bank",
                "entity_id": acc["id"],
                "status": "posted",
                "entry_type": {"$ne": "reversal"},
                "metadata.legacy_orphan": {"$ne": True},
            }
            if sub is not None:
                match["sub_account"] = sub
            d, c, dn, cn = 0.0, 0.0, 0, 0
            async for r in db.general_ledger.aggregate([
                {"$match": match},
                {"$group": {
                    "_id": "$side",
                    "total": {"$sum": "$amount"},
                    "n": {"$sum": 1}}},
            ]):
                if r["_id"] == "debit":
                    d = float(r["total"])
                    dn = int(r["n"])
                elif r["_id"] == "credit":
                    c = float(r["total"])
                    cn = int(r["n"])
            return {"debits": _r(d), "credits": _r(c),
                    "net": _r(d - c),
                    "debit_count": dn, "credit_count": cn,
                    "row_count": dn + cn}

        gl_main = await _gl_net("main")
        gl_balance = await _gl_net("balance")
        gl_all = await _gl_net(None)

        # ── 5. account_transactions walk ───────────────────────────
        at_in, at_out, n_in, n_out = 0.0, 0.0, 0, 0
        async for r in db.account_transactions.aggregate([
            {"$match": {"user_id": uid, "account_id": acc["id"]}},
            {"$group": {
                "_id": "$direction",
                "total": {"$sum": "$amount"},
                "n": {"$sum": 1}}},
        ]):
            if r["_id"] == "in":
                at_in = float(r["total"])
                n_in = int(r["n"])
            elif r["_id"] == "out":
                at_out = float(r["total"])
                n_out = int(r["n"])
        at_walk = {
            "in_total": _r(at_in), "out_total": _r(at_out),
            "net": _r(at_in - at_out),
            "in_count": n_in, "out_count": n_out,
            "row_count": n_in + n_out,
        }

        # ── 6. Settlement files (recent) ───────────────────────────
        since_dt = datetime.now(timezone.utc) - timedelta(
            days=lookback_days)
        settlement_files_list: List[Dict[str, Any]] = []
        sf_total = 0
        sf_total_net = 0.0
        sf_total_gross = 0.0
        async for f in db.settlement_files.find(
            {"user_id": uid, "provider": "salla"},
            {"_id": 0, "id": 1, "file_name": 1, "file_hash": 1,
             "uploaded_at": 1, "uploaded_by": 1,
             "invoice_number": 1, "header": 1, "totals": 1,
             "settlement_date": 1, "status": 1},
        ).sort("uploaded_at", -1).limit(list_limit):
            sf_total += 1
            totals = f.get("totals") or {}
            sf_total_net += float(totals.get("net") or 0)
            sf_total_gross += float(totals.get("gross") or 0)
            settlement_files_list.append({
                "id": f.get("id"),
                "file_name": f.get("file_name"),
                "uploaded_at": f.get("uploaded_at"),
                "uploaded_by": f.get("uploaded_by"),
                "invoice_number": (
                    f.get("invoice_number")
                    or (f.get("header") or {}).get("invoice_number")),
                "settlement_date": f.get("settlement_date"),
                "rows": int(totals.get("rows") or 0),
                "gross": _r(totals.get("gross")),
                "fees": _r(totals.get("fees")),
                "net": _r(totals.get("net")),
                "status": f.get("status"),
            })

        # Count + sum the FULL history (not just recent) to compare
        sf_full_count = await db.settlement_files.count_documents(
            {"user_id": uid, "provider": "salla"})
        sf_full_sum = 0.0
        async for r in db.settlement_files.aggregate([
            {"$match": {"user_id": uid, "provider": "salla"}},
            {"$group": {"_id": None,
                        "net": {"$sum": "$totals.net"},
                        "gross": {"$sum": "$totals.gross"}}},
        ]):
            sf_full_sum = float(r.get("net") or 0)

        # ── 7. Settlement entries (recent) ─────────────────────────
        settlement_entries_recent: List[Dict[str, Any]] = []
        se_full_count = await db.settlement_entries.count_documents(
            {"user_id": uid, "provider": "salla"})
        se_full_sum_net = 0.0
        se_full_sum_gross = 0.0
        async for r in db.settlement_entries.aggregate([
            {"$match": {"user_id": uid, "provider": "salla"}},
            {"$group": {"_id": None,
                        "net": {"$sum": "$actual_net_amount"},
                        "gross": {"$sum": "$actual_gross_amount"}}},
        ]):
            se_full_sum_net = float(r.get("net") or 0)
            se_full_sum_gross = float(r.get("gross") or 0)

        async for e in db.settlement_entries.find(
            {"user_id": uid, "provider": "salla"},
            {"_id": 0, "id": 1, "settlement_reference": 1,
             "order_number": 1, "settlement_date": 1,
             "actual_payment_method": 1, "event_type": 1,
             "actual_gross_amount": 1, "actual_net_amount": 1,
             "actual_payment_fee": 1,
             "imported_at": 1, "settlement_file_id": 1},
        ).sort("imported_at", -1).limit(list_limit):
            settlement_entries_recent.append({
                "id": e.get("id"),
                "settlement_reference": e.get("settlement_reference"),
                "order_number": e.get("order_number"),
                "settlement_date": e.get("settlement_date"),
                "imported_at": e.get("imported_at"),
                "settlement_file_id": e.get("settlement_file_id"),
                "actual_payment_method": e.get("actual_payment_method"),
                "event_type": e.get("event_type"),
                "actual_gross_amount":
                    _r(e.get("actual_gross_amount")),
                "actual_net_amount":
                    _r(e.get("actual_net_amount")),
                "actual_payment_fee":
                    _r(e.get("actual_payment_fee")),
            })

        # ── 8. Payment adjustments (provider=salla, canonical) ─────
        pa_full_count = await db.payment_adjustments.count_documents(
            {"user_id": uid})
        pa_recent: List[Dict[str, Any]] = []
        async for p in db.payment_adjustments.find(
            {"user_id": uid,
             "$or": [
                 {"payment_method": {"$regex": "salla", "$options": "i"}},
                 {"payment_method": {"$in": [
                     "mada", "applepay", "googlepay", "stcpay", "visa",
                     "mastercard", "credit_card", "debit_card", "salla_wallet"]}},
             ]},
            {"_id": 0, "id": 1, "order_number": 1, "payment_method": 1,
             "original_amount": 1, "new_amount": 1,
             "settlement_at": 1, "created_at": 1,
             "detection_source": 1, "trigger": 1,
             "actual_net_amount": 1, "actual_payment_fee": 1},
        ).sort("created_at", -1).limit(list_limit):
            pa_recent.append({
                "id": p.get("id"),
                "order_number": p.get("order_number"),
                "payment_method": p.get("payment_method"),
                "original_amount": _r(p.get("original_amount")),
                "new_amount": _r(p.get("new_amount")),
                "actual_net_amount":
                    _r(p.get("actual_net_amount")),
                "actual_payment_fee":
                    _r(p.get("actual_payment_fee")),
                "settlement_at": p.get("settlement_at"),
                "created_at": p.get("created_at"),
                "detection_source": p.get("detection_source"),
                "trigger": p.get("trigger"),
            })

        # ── 9. Recent general_ledger rows for the Salla account ────
        gl_recent: List[Dict[str, Any]] = []
        async for r in db.general_ledger.find(
            {"user_id": uid,
             "entity_type": "bank",
             "entity_id": acc["id"],
             "status": "posted"},
            {"_id": 0, "id": 1, "entry_type": 1, "sub_account": 1,
             "side": 1, "amount": 1, "posted_at": 1, "notes": 1,
             "txn_group_id": 1, "metadata": 1},
        ).sort("posted_at", -1).limit(list_limit):
            md = r.get("metadata") or {}
            gl_recent.append({
                "id": r.get("id"),
                "entry_type": r.get("entry_type"),
                "sub_account": r.get("sub_account") or "main",
                "side": r.get("side"),
                "amount": _r(r.get("amount")),
                "posted_at": r.get("posted_at"),
                "notes": (r.get("notes") or "")[:80],
                "txn_group_id": r.get("txn_group_id"),
                "source": md.get("source"),
            })

        # ── 10. Recent account_transactions for the Salla account ──
        at_recent: List[Dict[str, Any]] = []
        async for tx in db.account_transactions.find(
            {"user_id": uid, "account_id": acc["id"]},
            {"_id": 0, "id": 1, "transaction_type": 1, "amount": 1,
             "direction": 1, "description": 1, "transaction_date": 1,
             "created_at": 1, "txn_group_id": 1, "metadata": 1},
        ).sort("transaction_date", -1).limit(list_limit):
            at_recent.append({
                "id": tx.get("id"),
                "transaction_type": tx.get("transaction_type"),
                "amount": _r(tx.get("amount")),
                "direction": tx.get("direction"),
                "description": (tx.get("description") or "")[:80],
                "transaction_date": tx.get("transaction_date"),
                "txn_group_id": tx.get("txn_group_id"),
            })

        # ── 11. Search for the missing amount ──────────────────────
        search_results: Dict[str, Any] = {
            "search_amount": search_amount,
            "tolerance": search_tolerance,
        }
        if search_amount is not None:
            target = float(search_amount)
            tol = float(search_tolerance)
            hits: Dict[str, List[Dict[str, Any]]] = {
                "settlement_files_by_net": [],
                "settlement_files_by_gross": [],
                "settlement_entries_by_net": [],
                "settlement_entries_by_gross": [],
                "payment_adjustments": [],
                "account_transactions": [],
                "general_ledger": [],
            }

            async for f in db.settlement_files.find(
                {"user_id": uid, "provider": "salla"},
                {"_id": 0, "id": 1, "file_name": 1, "uploaded_at": 1,
                 "settlement_date": 1, "totals": 1,
                 "invoice_number": 1, "header": 1},
            ):
                t = f.get("totals") or {}
                if _within(t.get("net"), target, tol):
                    hits["settlement_files_by_net"].append({
                        "id": f.get("id"),
                        "file_name": f.get("file_name"),
                        "uploaded_at": f.get("uploaded_at"),
                        "settlement_date": f.get("settlement_date"),
                        "invoice_number": (
                            f.get("invoice_number")
                            or (f.get("header") or {}).get(
                                "invoice_number")),
                        "net": _r(t.get("net")),
                        "gross": _r(t.get("gross")),
                    })
                if _within(t.get("gross"), target, tol):
                    hits["settlement_files_by_gross"].append({
                        "id": f.get("id"),
                        "file_name": f.get("file_name"),
                        "net": _r(t.get("net")),
                        "gross": _r(t.get("gross")),
                        "uploaded_at": f.get("uploaded_at"),
                    })

            async for e in db.settlement_entries.find(
                {"user_id": uid, "provider": "salla",
                 "$or": [
                     {"actual_net_amount":
                      {"$gte": target - tol, "$lte": target + tol}},
                     {"actual_gross_amount":
                      {"$gte": target - tol, "$lte": target + tol}},
                 ]},
                {"_id": 0, "id": 1, "order_number": 1,
                 "settlement_reference": 1, "settlement_date": 1,
                 "actual_net_amount": 1, "actual_gross_amount": 1,
                 "imported_at": 1, "actual_payment_method": 1},
            ).limit(50):
                row = {
                    "id": e.get("id"),
                    "order_number": e.get("order_number"),
                    "settlement_reference":
                        e.get("settlement_reference"),
                    "settlement_date": e.get("settlement_date"),
                    "imported_at": e.get("imported_at"),
                    "actual_payment_method":
                        e.get("actual_payment_method"),
                    "actual_net_amount":
                        _r(e.get("actual_net_amount")),
                    "actual_gross_amount":
                        _r(e.get("actual_gross_amount")),
                }
                if _within(e.get("actual_net_amount"), target, tol):
                    hits["settlement_entries_by_net"].append(row)
                if _within(
                        e.get("actual_gross_amount"), target, tol):
                    hits["settlement_entries_by_gross"].append(row)

            async for p in db.payment_adjustments.find(
                {"user_id": uid,
                 "$or": [
                     {"original_amount":
                      {"$gte": target - tol, "$lte": target + tol}},
                     {"new_amount":
                      {"$gte": target - tol, "$lte": target + tol}},
                     {"actual_net_amount":
                      {"$gte": target - tol, "$lte": target + tol}},
                 ]},
                {"_id": 0, "id": 1, "order_number": 1,
                 "payment_method": 1, "original_amount": 1,
                 "new_amount": 1, "actual_net_amount": 1,
                 "settlement_at": 1, "created_at": 1},
            ).limit(50):
                hits["payment_adjustments"].append({
                    "id": p.get("id"),
                    "order_number": p.get("order_number"),
                    "payment_method": p.get("payment_method"),
                    "original_amount": _r(p.get("original_amount")),
                    "new_amount": _r(p.get("new_amount")),
                    "actual_net_amount":
                        _r(p.get("actual_net_amount")),
                    "settlement_at": p.get("settlement_at"),
                    "created_at": p.get("created_at"),
                })

            async for tx in db.account_transactions.find(
                {"user_id": uid, "account_id": acc["id"],
                 "amount":
                 {"$gte": target - tol, "$lte": target + tol}},
                {"_id": 0, "id": 1, "transaction_type": 1, "amount": 1,
                 "direction": 1, "description": 1,
                 "transaction_date": 1},
            ).limit(20):
                hits["account_transactions"].append({
                    "id": tx.get("id"),
                    "transaction_type": tx.get("transaction_type"),
                    "amount": _r(tx.get("amount")),
                    "direction": tx.get("direction"),
                    "description": (tx.get("description") or "")[:80],
                    "transaction_date": tx.get("transaction_date"),
                })

            async for r in db.general_ledger.find(
                {"user_id": uid,
                 "entity_type": "bank",
                 "entity_id": acc["id"],
                 "amount":
                 {"$gte": target - tol, "$lte": target + tol}},
                {"_id": 0, "id": 1, "entry_type": 1, "sub_account": 1,
                 "side": 1, "amount": 1, "posted_at": 1, "notes": 1},
            ).limit(20):
                hits["general_ledger"].append({
                    "id": r.get("id"),
                    "entry_type": r.get("entry_type"),
                    "sub_account": r.get("sub_account"),
                    "side": r.get("side"),
                    "amount": _r(r.get("amount")),
                    "posted_at": r.get("posted_at"),
                    "notes": (r.get("notes") or "")[:80],
                })

            search_results["hits"] = hits
            search_results["found_anywhere"] = any(
                len(v) > 0 for v in hits.values())
            search_results["total_hits"] = sum(
                len(v) for v in hits.values())

        # ── 12. How is the Salla balance computed? ────────────────
        # Tell the user explicitly which fields drive what.
        has_ledger_activity = gl_all["row_count"] > 0
        balance_formula = (
            "ledger_main_net + (current_balance − dw_net) "
            "[Iter-240 hybrid]"
            if has_ledger_activity
            else "accounts.current_balance (legacy fallback)"
        )

        # ── 12.b — Live central metrics (READ-ONLY, no sync) ──────
        # Recompute expected_orders RIGHT NOW from unified_orders so
        # we can see if the stored expected is stale.
        central_live: Dict[str, Any] = {"available": False}
        try:
            from payment_gateway_metrics import compute_metrics
            metrics = await compute_metrics(db, uid)
            rows = metrics.get("rows") or []
            # Map account's canonical key → central keys
            account_to_central = {
                "salla": [
                    "salla", "mada", "applepay", "googlepay", "stcpay",
                    "visa", "mastercard", "credit_card", "debit_card",
                    "salla_wallet",
                ],
                "tamara": ["tamara"],
                "tabby": ["tabby"],
                "emkan": ["emkan"],
                "bank_transfer": ["bank_transfer"],
                "cash_on_delivery": ["cod"],
            }
            keys = account_to_central.get(
                acc.get("normalized_payment_method"), [])
            live_net = 0.0
            live_orders = 0
            per_key_breakdown: List[Dict[str, Any]] = []
            for r in rows:
                if r.get("key") in keys:
                    live_net += float(r.get("net") or 0)
                    live_orders += int(r.get("orders_count") or 0)
                    per_key_breakdown.append({
                        "key": r.get("key"),
                        "net": _r(r.get("net")),
                        "gross": _r(r.get("gross")),
                        "fees": _r(r.get("fees")),
                        "refunds": _r(r.get("refunds")),
                        "orders_count": int(r.get("orders_count") or 0),
                    })
            central_live = {
                "available": True,
                "live_expected_net": _r(live_net),
                "live_orders_count": live_orders,
                "stored_expected": stored_expected,
                "drift_live_vs_stored": _r(live_net - stored_expected),
                "per_key_breakdown": per_key_breakdown,
            }
        except Exception as e:  # noqa: BLE001
            central_live = {"available": False, "error": repr(e)}

        # ── 12.c — Transfer summary by month ──────────────────────
        # Show every recorded transfer OUT of Salla so the merchant
        # can match against bank statements.
        transfers_by_month: Dict[str, Dict[str, Any]] = {}
        transfers_all: List[Dict[str, Any]] = []
        async for tx in db.account_transactions.find(
            {"user_id": uid, "account_id": acc["id"],
             "direction": "out"},
            {"_id": 0, "id": 1, "transaction_type": 1, "amount": 1,
             "description": 1, "transaction_date": 1,
             "peer_account_id": 1, "peer_account_name": 1,
             "created_at": 1},
        ).sort("transaction_date", -1):
            amt = float(tx.get("amount") or 0)
            month = (
                tx.get("transaction_date") or tx.get("created_at")
                or "—"
            )
            if isinstance(month, datetime):
                month = month.strftime("%Y-%m")
            elif isinstance(month, str):
                month = month[:7] if len(month) >= 7 else month
            else:
                month = "—"
            agg = transfers_by_month.setdefault(
                month, {"count": 0, "total": 0.0})
            agg["count"] += 1
            agg["total"] += amt
            transfers_all.append({
                "id": tx.get("id"),
                "transaction_type": tx.get("transaction_type"),
                "amount": _r(amt),
                "description": (tx.get("description") or "")[:80],
                "transaction_date": tx.get("transaction_date"),
                "peer_account_name": tx.get("peer_account_name"),
                "peer_account_id": tx.get("peer_account_id"),
            })
        transfers_by_month_list = [
            {"month": k, "count": v["count"], "total": _r(v["total"])}
            for k, v in sorted(transfers_by_month.items())
        ]
        transfers_total = _r(sum(t["amount"] for t in transfers_all))

        # ── 12.d — Refunds tracking ───────────────────────────────
        # Sum of refund event_types in settlement_entries.
        refunds_summary = {
            "full_refunds_count": 0,
            "full_refunds_amount": 0.0,
            "partial_refunds_count": 0,
            "partial_refunds_amount": 0.0,
        }
        async for r in db.settlement_entries.aggregate([
            {"$match": {
                "user_id": uid, "provider": "salla",
                "event_type": "refund"}},
            {"$group": {
                "_id": None,
                "full_count": {"$sum": {"$cond": [
                    {"$gt": ["$actual_refund_amount", 0]}, 1, 0]}},
                "full_amount": {
                    "$sum": "$actual_refund_amount"},
                "partial_count": {"$sum": {"$cond": [
                    {"$gt":
                     ["$actual_partial_refund_amount", 0]}, 1, 0]}},
                "partial_amount": {
                    "$sum": "$actual_partial_refund_amount"},
            }},
        ]):
            refunds_summary["full_refunds_count"] = int(
                r.get("full_count") or 0)
            refunds_summary["full_refunds_amount"] = _r(
                r.get("full_amount"))
            refunds_summary["partial_refunds_count"] = int(
                r.get("partial_count") or 0)
            refunds_summary["partial_refunds_amount"] = _r(
                r.get("partial_amount"))

        # ── 12.e — Commissions / fees tracking ────────────────────
        commissions_summary = {"total_fees": 0.0, "total_vat": 0.0,
                               "entries_count": 0}
        async for r in db.settlement_entries.aggregate([
            {"$match": {"user_id": uid, "provider": "salla"}},
            {"$group": {
                "_id": None,
                "fees": {"$sum": "$actual_payment_fee"},
                "vat": {"$sum": "$actual_payment_vat"},
                "n": {"$sum": 1}}},
        ]):
            commissions_summary["total_fees"] = _r(r.get("fees"))
            commissions_summary["total_vat"] = _r(r.get("vat"))
            commissions_summary["entries_count"] = int(
                r.get("n") or 0)

        # ── 12.f — GAP RECONCILIATION ─────────────────────────────
        # The crown jewel — exactly what part of the gap belongs to
        # which suspect. Always computed; if no real_balance was
        # passed, returns hypothetical breakdown.
        system_displayed = ssot_value or stored_current_balance
        gap_reconciliation = {
            "system_displayed_balance": system_displayed,
            "stored_expected": stored_expected,
            "transferred_total_out": transfers_total,
            "stored_formula_check": _r(
                stored_expected - transfers_total
                - system_displayed),  # should be ~0 if formula holds
        }
        if real_balance_at_provider is not None:
            real = float(real_balance_at_provider)
            gap = _r(real - system_displayed)
            # Scenario A: expected is stale (too low)
            # If transferred is correct: real_expected = real + transferred
            expected_should_be_A = _r(real + transfers_total)
            expected_shortfall_A = _r(
                expected_should_be_A - stored_expected)
            # Scenario B: transferred is too high
            # If expected is correct: real_transferred = expected - real
            transferred_should_be_B = _r(stored_expected - real)
            transferred_excess_B = _r(
                transfers_total - transferred_should_be_B)
            gap_reconciliation.update({
                "real_balance_at_provider": real,
                "gap_real_minus_system": gap,
                "scenario_A_expected_stale": {
                    "description": (
                        "expected_orders_balance is too low because "
                        "Salla received MORE sales than we've synced."),
                    "expected_should_be": expected_should_be_A,
                    "expected_shortfall": expected_shortfall_A,
                    "likelihood_check": (
                        "live central metrics vs stored expected — "
                        "see central_live.drift_live_vs_stored"),
                },
                "scenario_B_transferred_excess": {
                    "description": (
                        "account_transactions OUT is too high "
                        "(duplicate/over-recorded transfers)."),
                    "transferred_should_be": transferred_should_be_B,
                    "transferred_excess": transferred_excess_B,
                    "likelihood_check": (
                        "review transfers_all and match each row "
                        "to a real bank deposit."),
                },
                "likely_cause_hint": (
                    "If central_live.drift_live_vs_stored > 0, "
                    "Scenario A is more likely (sync stale). "
                    "If = 0, Scenario B is more likely "
                    "(over-recorded transfers)."),
            })

        return {
            "ok": True,
            "iter": "iter250b_p1_5_c",
            "generated_at": _now_iso(),
            "account": {
                "id": acc["id"],
                "name": acc.get("name"),
                "account_type": acc.get("account_type"),
                "normalized_payment_method":
                    acc.get("normalized_payment_method"),
                "currency": acc.get("currency"),
                "status": acc.get("status"),
                "last_synced_at": last_synced,
            },
            "stored_fields": {
                "current_balance": stored_current_balance,
                "expected_orders_balance": stored_expected,
                "opening_balance": stored_opening_balance,
                "opening_balance_date": stored_opening_at,
                "orders_count": stored_orders_count,
            },
            "computed_balances": {
                "ssot_value": ssot_value,
                "ssot_error": ssot_error,
                "ledger_main_net": gl_main["net"],
                "ledger_main_row_count": gl_main["row_count"],
                "ledger_balance_net": gl_balance["net"],
                "ledger_balance_row_count": gl_balance["row_count"],
                "ledger_all_net": gl_all["net"],
                "ledger_all_row_count": gl_all["row_count"],
                "account_transactions_walk": at_walk,
                "displayed_balance": ssot_value or stored_current_balance,
            },
            "balance_formula": {
                "has_ledger_activity": has_ledger_activity,
                "formula_used": balance_formula,
                "drives_displayed_balance": (
                    "ssot_value (account_balance_ssot)"),
                "drives_top_card_in_ui": (
                    "current_balance overwritten by SSOT via "
                    "_account_with_meta"),
                "drives_reconciliation_page": (
                    "expected = central /payment-gateway-metrics; "
                    "current_balance from `accounts` collection"),
                "drives_dashboard_total_owed": (
                    "expected_orders_balance (the gross pending claim "
                    "on Salla side, NOT the cash already settled)"),
            },
            "drifts": {
                "ssot_vs_stored":
                    _r((ssot_value or 0) - stored_current_balance)
                    if ssot_value is not None else None,
                "ssot_vs_walk":
                    _r((ssot_value or 0) - at_walk["net"])
                    if ssot_value is not None else None,
                "displayed_vs_expected_orders":
                    _r((ssot_value or stored_current_balance)
                       - stored_expected),
            },
            "settlements_history": {
                "settlement_files_full_count": sf_full_count,
                "settlement_files_full_sum_net": _r(sf_full_sum),
                "settlement_entries_full_count": se_full_count,
                "settlement_entries_full_sum_net":
                    _r(se_full_sum_net),
                "settlement_entries_full_sum_gross":
                    _r(se_full_sum_gross),
                "payment_adjustments_full_count": pa_full_count,
                "lookback_days": lookback_days,
                "since": since_dt.isoformat(),
            },
            # ── Iter-250b · P1.5.c+ — extended sections ──
            "central_metrics_live": central_live,
            "transfers_out_summary": {
                "total_count": len(transfers_all),
                "total_amount": transfers_total,
                "by_month": transfers_by_month_list,
                "all_rows": transfers_all,
            },
            "refunds_summary": refunds_summary,
            "commissions_summary": commissions_summary,
            "gap_reconciliation": gap_reconciliation,
            "recent_settlement_files": settlement_files_list,
            "recent_settlement_entries": settlement_entries_recent,
            "recent_payment_adjustments": pa_recent,
            "recent_general_ledger": gl_recent,
            "recent_account_transactions": at_recent,
            "search": search_results,
            "notes": [
                "READ-ONLY — no DB writes performed.",
                "Salla balance in the system = ssot_value, NOT the "
                "expected_orders_balance. ssot_value reflects cash "
                "already moved (transferred to banks etc.).",
                "expected_orders_balance = the gross pending claim "
                "on Salla side (Reconciliation page uses it).",
                "If a settlement file was NOT yet imported, neither "
                "settlement_files nor settlement_entries will contain "
                "it — explaining why the system balance lags the "
                "Salla panel real balance.",
                "Use ?search_amount=<value> to look for a specific "
                "settlement amount across ALL collections.",
            ],
        }

    return router


__all__ = ["make_salla_balance_forensic_router"]
