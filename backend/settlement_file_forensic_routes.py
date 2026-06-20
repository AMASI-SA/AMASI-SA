"""Iter-250b · P1.5.d — Settlement File Forensic (READ-ONLY).

Investigates a SPECIFIC imported settlement file by invoice_number or
file_id. Answers the merchant's exact questions:

  Q1. لماذا حسب النظام صافي 16,134.15 بينما ملف Excel = 15,892.60؟
      → Breakdown by event_type + recompute from settlement_entries

  Q2. لماذا لم ينخفض رصيد سلة بعد الاستيراد؟
      → Check if ANY account_transaction or general_ledger entry was
        created with metadata.settlement_file_id == this file.
      → Compare provider account current_balance vs expected.

  Q3. هل تم ربط الفاتورة بحساب سلة؟
      → Search for the salla account_id in any post-import write.

  Q4. كم unified_orders تم تحديثها بهذه الفاتورة؟
      → Count via metadata.last_settlement_file_id

STRICT READ-ONLY · NO writes · NO recompute · NO migrations.

Endpoint::

    GET /api/diagnostics/settlement-file-forensic
        ?invoice_number=<str, e.g. "6381217">
        OR ?file_id=<uuid>
        &list_limit=200
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query


def _r(n) -> float:
    return round(float(n or 0), 2)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def make_settlement_file_forensic_router(db, current_user):
    router = APIRouter(tags=["diagnostics", "settlement-file-forensic"])

    @router.get("/diagnostics/settlement-file-forensic")
    async def settlement_file_forensic(
        invoice_number: Optional[str] = Query(None),
        file_id: Optional[str] = Query(None),
        list_limit: int = Query(200, ge=1, le=2000),
        user: dict = Depends(current_user),
    ):
        uid = user["id"]
        if not invoice_number and not file_id:
            raise HTTPException(
                400, "Pass invoice_number OR file_id.")

        # ── 1. Locate the settlement_file ──────────────────────────
        query: Dict[str, Any] = {"user_id": uid}
        if file_id:
            query["id"] = file_id
        else:
            query["$or"] = [
                {"header.invoice_number": invoice_number},
                {"invoice_number": invoice_number},
                {"filename": {"$regex": invoice_number,
                              "$options": "i"}},
            ]

        sf = await db.settlement_files.find_one(query, {"_id": 0})
        if not sf:
            raise HTTPException(
                404,
                f"settlement_file not found "
                f"(invoice_number={invoice_number}, file_id={file_id})",
            )

        file_id_resolved = sf.get("id")
        provider = sf.get("provider")
        header = sf.get("header") or {}
        stored_totals = sf.get("totals") or {}
        resolved_invoice = (
            sf.get("invoice_number")
            or header.get("invoice_number")
            or invoice_number
            or "—"
        )

        # ── 2. Pull ALL settlement_entries for this file ───────────
        entries: List[Dict[str, Any]] = []
        async for e in db.settlement_entries.find(
            {"user_id": uid, "file_id": file_id_resolved},
            {"_id": 0},
        ).limit(list_limit):
            entries.append(e)

        # ── 3. Recompute totals from entries (the truth) ───────────
        recomputed = {
            "rows_total": len(entries),
            "gross": 0.0, "net": 0.0,
            "fees": 0.0, "fees_vat": 0.0,
            "refund_full": 0.0, "refund_partial": 0.0,
        }
        by_event_type: Dict[str, Dict[str, Any]] = defaultdict(
            lambda: {"count": 0, "gross": 0.0, "net": 0.0,
                     "fees": 0.0, "vat": 0.0,
                     "refund_full": 0.0, "refund_partial": 0.0})
        by_payment_method: Dict[str, Dict[str, Any]] = defaultdict(
            lambda: {"count": 0, "gross": 0.0, "net": 0.0,
                     "fees": 0.0})

        for e in entries:
            event = e.get("event_type") or "(unknown)"
            method = e.get("actual_payment_method") or "(unknown)"
            g = float(e.get("actual_gross_amount") or 0)
            n = float(e.get("actual_net_amount") or 0)
            f = float(e.get("actual_payment_fee") or 0)
            v = float(e.get("actual_payment_vat") or 0)
            rf = float(e.get("actual_refund_amount") or 0)
            rp = float(e.get("actual_partial_refund_amount") or 0)

            recomputed["gross"] += g
            recomputed["net"] += n
            recomputed["fees"] += f
            recomputed["fees_vat"] += v
            recomputed["refund_full"] += rf
            recomputed["refund_partial"] += rp

            by_event_type[event]["count"] += 1
            by_event_type[event]["gross"] += g
            by_event_type[event]["net"] += n
            by_event_type[event]["fees"] += f
            by_event_type[event]["vat"] += v
            by_event_type[event]["refund_full"] += rf
            by_event_type[event]["refund_partial"] += rp

            by_payment_method[method]["count"] += 1
            by_payment_method[method]["gross"] += g
            by_payment_method[method]["net"] += n
            by_payment_method[method]["fees"] += f

        for d in (recomputed,):
            for k in list(d.keys()):
                if isinstance(d[k], float):
                    d[k] = _r(d[k])

        by_event_type_list = [
            {"event_type": k,
             "count": v["count"],
             "gross": _r(v["gross"]),
             "net": _r(v["net"]),
             "fees": _r(v["fees"]),
             "vat": _r(v["vat"]),
             "refund_full": _r(v["refund_full"]),
             "refund_partial": _r(v["refund_partial"])}
            for k, v in sorted(by_event_type.items(),
                               key=lambda x: -x[1]["count"])
        ]
        by_payment_method_list = [
            {"payment_method": k,
             "count": v["count"],
             "gross": _r(v["gross"]),
             "net": _r(v["net"]),
             "fees": _r(v["fees"])}
            for k, v in sorted(by_payment_method.items(),
                               key=lambda x: -x[1]["count"])
        ]

        # ── 4. Stored vs recomputed comparison ─────────────────────
        stored_net = _r(stored_totals.get("net"))
        stored_gross = _r(stored_totals.get("gross"))
        stored_fees = _r(stored_totals.get("fees"))
        recomputed_net = recomputed["net"]
        recomputed_gross = recomputed["gross"]
        recomputed_fees = recomputed["fees"]

        # Sum nets excluding refunds (sale only)
        sale_only_net = sum(
            v["net"] for k, v in by_event_type.items()
            if k == "sale")
        refund_only_net = sum(
            v["net"] for k, v in by_event_type.items()
            if k == "refund")
        wallet_recharge_net = sum(
            v["net"] for k, v in by_event_type.items()
            if k == "salla_purchase")

        # ── 5. Look for any account_transaction linked to this file ─
        linked_account_transactions: List[Dict[str, Any]] = []
        async for tx in db.account_transactions.find(
            {"user_id": uid,
             "$or": [
                 {"metadata.settlement_file_id": file_id_resolved},
                 {"metadata.invoice_number": resolved_invoice},
                 {"settlement_file_id": file_id_resolved},
                 {"description": {
                     "$regex": str(resolved_invoice),
                     "$options": "i"}},
             ]},
            {"_id": 0, "id": 1, "account_id": 1,
             "transaction_type": 1, "amount": 1, "direction": 1,
             "description": 1, "transaction_date": 1,
             "created_at": 1, "metadata": 1, "txn_group_id": 1},
        ).sort("created_at", -1).limit(50):
            linked_account_transactions.append({
                "id": tx.get("id"),
                "account_id": tx.get("account_id"),
                "transaction_type": tx.get("transaction_type"),
                "amount": _r(tx.get("amount")),
                "direction": tx.get("direction"),
                "description": (tx.get("description") or "")[:120],
                "transaction_date": tx.get("transaction_date"),
                "txn_group_id": tx.get("txn_group_id"),
                "metadata_keys":
                    list((tx.get("metadata") or {}).keys()),
            })

        # ── 6. Look for any general_ledger linked to this file ─────
        linked_ledger_entries: List[Dict[str, Any]] = []
        async for r in db.general_ledger.find(
            {"user_id": uid,
             "$or": [
                 {"metadata.settlement_file_id": file_id_resolved},
                 {"metadata.invoice_number": resolved_invoice},
                 {"notes": {
                     "$regex": str(resolved_invoice),
                     "$options": "i"}},
             ]},
            {"_id": 0, "id": 1, "entity_type": 1, "entity_id": 1,
             "sub_account": 1, "entry_type": 1, "side": 1,
             "amount": 1, "posted_at": 1, "notes": 1,
             "metadata": 1, "txn_group_id": 1},
        ).sort("posted_at", -1).limit(50):
            linked_ledger_entries.append({
                "id": r.get("id"),
                "entity_type": r.get("entity_type"),
                "entity_id": r.get("entity_id"),
                "sub_account": r.get("sub_account"),
                "entry_type": r.get("entry_type"),
                "side": r.get("side"),
                "amount": _r(r.get("amount")),
                "posted_at": r.get("posted_at"),
                "notes": (r.get("notes") or "")[:120],
                "txn_group_id": r.get("txn_group_id"),
            })

        # ── 7. unified_orders updated by this file ────────────────
        unified_updates = await db.unified_orders.count_documents(
            {"user_id": uid,
             "last_settlement_file_id": file_id_resolved})

        # Sum of net actual amounts on those orders (= what hit
        # unified_orders thanks to this file)
        unified_sum: Dict[str, Any] = {
            "count": 0, "net": 0.0, "gross": 0.0}
        async for r in db.unified_orders.aggregate([
            {"$match": {"user_id": uid,
                        "last_settlement_file_id": file_id_resolved}},
            {"$group": {
                "_id": None,
                "n": {"$sum": 1},
                "net": {"$sum": "$actual_net_amount"},
                "gross": {"$sum": "$actual_gross_amount"}}},
        ]):
            unified_sum = {
                "count": int(r.get("n") or 0),
                "net": _r(r.get("net")),
                "gross": _r(r.get("gross")),
            }

        # ── 8. The Salla account state (current) ──────────────────
        salla_acc = await db.accounts.find_one(
            {"user_id": uid,
             "account_type": "payment_platform",
             "normalized_payment_method": "salla"},
            {"_id": 0, "id": 1, "name": 1, "current_balance": 1,
             "expected_orders_balance": 1, "orders_count": 1,
             "updated_at": 1, "last_synced_at": 1},
        )

        # ── 9. THE VERDICT ────────────────────────────────────────
        # Did the import affect the Salla account balance?
        impacted_balance = bool(linked_account_transactions
                                or linked_ledger_entries)

        # Compute expected balance change (if import HAD affected it)
        net_for_account = recomputed_net  # what would have moved
        salla_current = (
            _r(salla_acc.get("current_balance"))
            if salla_acc else None
        )
        expected_after_if_settled = (
            _r(salla_current - net_for_account)
            if salla_current is not None else None
        )

        verdict = {
            "import_recorded": True,
            "import_created_account_transaction":
                len(linked_account_transactions) > 0,
            "import_created_general_ledger_entry":
                len(linked_ledger_entries) > 0,
            "import_affected_salla_balance": impacted_balance,
            "unified_orders_updated": unified_updates,
            "salla_account_id":
                salla_acc.get("id") if salla_acc else None,
            "salla_current_balance": salla_current,
            "expected_after_balance_if_transfer_recorded":
                expected_after_if_settled,
            "missing_transfer_amount": net_for_account,
            "explanation_arabic": (
                "استيراد ملف التسوية يُحدِّث `unified_orders` فقط "
                "(actual_net / actual_fee / settlement_reference). "
                "لا يُنشئ أي account_transaction ولا general_ledger. "
                "بالتالي رصيد حساب سلة لا يتأثر تلقائياً. "
                "إذا أراد التاجر تخفيض رصيد سلة بمقدار صافي الفاتورة، "
                "عليه تسجيل تحويل يدوي من سلة إلى البنك في "
                "صفحة /transfers بنفس قيمة صافي الفاتورة."
            ),
        }

        # ── 10. NET DISCREPANCY ANALYSIS (15,892.60 vs 16,134.15) ──
        net_breakdown = {
            "stored_totals_net": stored_net,
            "stored_totals_gross": stored_gross,
            "stored_totals_fees": stored_fees,
            "recomputed_from_entries_net": recomputed_net,
            "recomputed_from_entries_gross": recomputed_gross,
            "recomputed_from_entries_fees": recomputed_fees,
            "drift_stored_vs_recomputed_net":
                _r(stored_net - recomputed_net),
            "sale_only_net": _r(sale_only_net),
            "refund_only_net": _r(refund_only_net),
            "wallet_recharge_net": _r(wallet_recharge_net),
            "sale_minus_refund": _r(sale_only_net + refund_only_net),
            "salla_panel_invoice_net_hint": (
                "Salla's invoice 'Net Amount' usually EXCLUDES "
                "wallet_recharge rows (مشتريات سلة) and excludes "
                "any 'fees on the merchant side'. Compare "
                "stored_totals_net (parsed by our system) with "
                "salla_panel_displayed_net."
            ),
            "possible_explanations_for_drift": [
                {
                    "label": "Wallet recharge included by parser",
                    "delta": _r(wallet_recharge_net),
                    "explains": (
                        "إذا الـ Excel فيه صفوف 'مشتريات سلة' "
                        "(wallet_recharge) كانت موجبة لكن سلة "
                        "تطرحها من النت."
                    ),
                },
                {
                    "label": "Refund handling",
                    "delta": _r(refund_only_net),
                    "explains": (
                        "إذا الـ Excel يطرح المسترجعات وسلة لا تطرحها "
                        "في الـ Invoice Net، يظهر فرق."
                    ),
                },
                {
                    "label": "Extra commission row not in parser",
                    "delta": _r(stored_net - recomputed_net),
                    "explains": (
                        "إذا stored_totals_net ≠ "
                        "Σ(actual_net_amount) فهناك تعديل بعد parse."
                    ),
                },
            ],
        }

        return {
            "ok": True,
            "iter": "iter250b_p1_5_d",
            "generated_at": _now_iso(),
            "settlement_file": {
                "id": file_id_resolved,
                "provider": provider,
                "filename": sf.get("filename"),
                "file_hash": sf.get("file_hash"),
                "file_size": sf.get("file_size"),
                "uploaded_at": sf.get("uploaded_at"),
                "header": header,
                "resolved_invoice_number": resolved_invoice,
                "rows_in_audit": sf.get("rows"),
                "matched": sf.get("matched"),
                "unmatched": sf.get("unmatched"),
                "stored_totals": stored_totals,
            },
            "entries_recompute": {
                "rows_returned": len(entries),
                "totals_recomputed_from_entries": recomputed,
                "by_event_type": by_event_type_list,
                "by_payment_method": by_payment_method_list,
            },
            "net_discrepancy_analysis": net_breakdown,
            "linkage_to_accounting": {
                "account_transactions_count":
                    len(linked_account_transactions),
                "account_transactions_sample":
                    linked_account_transactions,
                "general_ledger_count":
                    len(linked_ledger_entries),
                "general_ledger_sample":
                    linked_ledger_entries,
                "unified_orders_updated_count": unified_updates,
                "unified_orders_sum": unified_sum,
            },
            "salla_account_state": salla_acc,
            "verdict": verdict,
            "notes": [
                "READ-ONLY — no DB writes performed.",
                "settlement_files imports update unified_orders only.",
                "They do NOT create account_transactions or "
                "general_ledger entries.",
                "Hence the Salla account current_balance does NOT "
                "decrease on import. A separate manual transfer in "
                "/transfers is required.",
                "If stored_totals.net ≠ recomputed net, the parser "
                "consolidated rows differently from the merchant's "
                "expected view of the file.",
            ],
        }

    return router


__all__ = ["make_settlement_file_forensic_router"]
