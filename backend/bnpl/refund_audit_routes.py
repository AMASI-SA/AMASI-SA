"""Unified Refund Audit (Tabby + Tamara) — Iter-117.

Returns one consolidated view so merchants can see at a glance:
  • How many transactions have a refund (full vs partial vs none).
  • How many refund records exist in `payment_refunds`.
  • Total refunded amount per provider, summed across:
      - payment_transactions.refunded_amount  (provider truth)
      - payment_refunds.amount               (our reconstructed rows)
      - unified_orders.refunded_amount       (what dashboards display)
  • Delta = transactions_refund_amount − refunds_amount.  Should be 0.
  • Profit/report check: total refund amount actually deducted in
    `unified_orders` (the source-of-truth used by Reports / Profits /
    Settlements).  If unified ≠ provider truth, the dashboards are
    out of sync and refunds aren't reflected in profit.
"""
from __future__ import annotations

from typing import Any, Dict, List

from fastapi import APIRouter, Depends


PROVIDERS = ("tabby", "tamara")


async def _diagnose_provider_delta(
    db, user_id: str, provider: str,
) -> Dict[str, Any]:
    """Return per-refund classification so the merchant can see WHY
    `records_vs_status` is non-zero.  For every payment_refunds row,
    we look up its matching payment_transaction and produce one of:

      • expected_full      — ptx.refunded_amount >= ptx.amount
      • expected_partial   — 0 < ptx.refunded_amount < ptx.amount
      • multiple_partials  — multiple refund rows roll up into one ptx
                             (this is the most common reason for
                             records > status: e.g. 2 refund records
                             for the same payment, but the ptx counts
                             once on the status side)
      • orphan_no_ptx      — refund row references a payment we don't
                             have in payment_transactions
      • orphan_zero_ptx    — ptx exists but ptx.refunded_amount == 0
      • duplicate          — same provider_refund_id appears 2+ times
    """
    # Step 1 — pull all refunds and group by payment_id for roll-up
    refunds: List[Dict[str, Any]] = []
    async for r in db.payment_refunds.find(
        {"user_id": user_id, "provider": provider},
        {"_id": 0},
    ):
        refunds.append(r)

    # Index payment_transactions by provider_id for O(1) joins
    ptx_index: Dict[str, Dict[str, Any]] = {}
    async for t in db.payment_transactions.find(
        {"user_id": user_id, "provider": provider},
        {"_id": 0, "provider_id": 1, "order_reference_id": 1,
         "amount": 1, "refunded_amount": 1, "status": 1,
         "created_at_provider": 1},
    ):
        pid = (t.get("provider_id") or "").strip()
        if pid:
            ptx_index[pid] = t

    # Count refund rows per payment for the multiple_partials case
    refunds_per_payment: Dict[str, int] = {}
    refund_ids_seen: Dict[str, int] = {}
    for r in refunds:
        pid = (r.get("provider_payment_id") or "").strip()
        rid = (r.get("provider_refund_id") or "").strip()
        refunds_per_payment[pid] = refunds_per_payment.get(pid, 0) + 1
        if rid:
            refund_ids_seen[rid] = refund_ids_seen.get(rid, 0) + 1

    rows: List[Dict[str, Any]] = []
    counts = {
        "expected_full": 0, "expected_partial": 0,
        "multiple_partials": 0, "orphan_no_ptx": 0,
        "orphan_zero_ptx": 0, "duplicate": 0,
    }

    # Track which payment we've already counted as "first refund" so
    # subsequent refunds on same payment get labeled multiple_partials.
    seen_payments_first_refund: set = set()

    # Sort refunds by refunded_at so the FIRST one wins the "expected"
    # label and later ones get "multiple_partials".
    for r in sorted(refunds, key=lambda x: x.get("refunded_at") or ""):
        pid = (r.get("provider_payment_id") or "").strip()
        rid = (r.get("provider_refund_id") or "").strip()
        ptx = ptx_index.get(pid)
        ptx_amount = float(ptx.get("amount") or 0) if ptx else 0
        ptx_refunded = float(ptx.get("refunded_amount") or 0) if ptx else 0

        if rid and refund_ids_seen.get(rid, 0) > 1:
            classification = "duplicate"
        elif not ptx:
            classification = "orphan_no_ptx"
        elif ptx_refunded <= 0:
            classification = "orphan_zero_ptx"
        elif pid in seen_payments_first_refund:
            classification = "multiple_partials"
        elif ptx_refunded >= ptx_amount and ptx_amount > 0:
            classification = "expected_full"
            seen_payments_first_refund.add(pid)
        else:
            classification = "expected_partial"
            seen_payments_first_refund.add(pid)

        counts[classification] = counts.get(classification, 0) + 1

        rows.append({
            "classification": classification,
            "payment_id": pid,
            "refund_id": rid,
            "order_reference_id": (
                r.get("order_reference_id")
                or (ptx.get("order_reference_id") if ptx else "")
                or ""
            ),
            "refund_amount": round(float(r.get("amount") or 0), 2),
            "refund_status": r.get("status") or "",
            "refund_reason": r.get("reason") or "",
            "refunded_at": r.get("refunded_at") or "",
            "transaction_amount": round(ptx_amount, 2) if ptx else None,
            "transaction_refunded_amount": round(ptx_refunded, 2) if ptx else None,
            "transaction_status": (ptx.get("status") if ptx else None),
            "transaction_created_at": (
                ptx.get("created_at_provider") if ptx else None
            ),
            "refunds_on_same_payment": refunds_per_payment.get(pid, 1),
            "synthesised": bool(r.get("synthesised")),
        })

    # Sort: anomalies first (so the user sees the problem rows on top)
    anomaly_order = {
        "duplicate": 0, "orphan_no_ptx": 1, "orphan_zero_ptx": 2,
        "multiple_partials": 3, "expected_partial": 4, "expected_full": 5,
    }
    rows.sort(key=lambda r: (anomaly_order.get(r["classification"], 99), r["payment_id"]))

    return {
        "provider": provider,
        "total_refund_records": len(refunds),
        "total_payments_with_refund": len({
            r["payment_id"] for r in rows
            if r["classification"] not in ("duplicate", "orphan_no_ptx")
        }),
        "counts": counts,
        "rows": rows,
    }


async def _audit_provider(db, user_id: str, provider: str) -> Dict[str, Any]:
    ptx_filter = {"user_id": user_id, "provider": provider}

    # --- payment_transactions side ---------------------------------
    # Classify by REFUND AMOUNT (not status string) because Tabby
    # tracks refunds via `refunded_amount` on a payment whose status
    # is still "closed".  Tamara also exposes the same field.  Using
    # amount keeps the audit provider-agnostic and accurate.
    full_count = 0
    partial_count = 0
    none_count = 0
    refunded_amount_in_ptx = 0.0

    async for r in db.payment_transactions.aggregate([
        {"$match": ptx_filter},
        {"$project": {
            "amount": {"$ifNull": ["$amount", 0]},
            "refunded_amount": {"$ifNull": ["$refunded_amount", 0]},
        }},
        {"$group": {
            "_id": {
                "$switch": {
                    "branches": [
                        {"case": {"$lte": ["$refunded_amount", 0]}, "then": "none"},
                        {"case": {"$gte": ["$refunded_amount", "$amount"]}, "then": "full"},
                    ],
                    "default": "partial",
                },
            },
            "n": {"$sum": 1},
            "refund_sum": {"$sum": "$refunded_amount"},
        }},
    ]):
        classification = r["_id"]
        n = int(r.get("n") or 0)
        s = float(r.get("refund_sum") or 0)
        if classification == "full":
            full_count = n
            refunded_amount_in_ptx += s
        elif classification == "partial":
            partial_count = n
            refunded_amount_in_ptx += s
        else:
            none_count = n
    refunded_amount_in_ptx = round(refunded_amount_in_ptx, 2)

    # --- payment_refunds side --------------------------------------
    # Two counts:
    #   • refund_records_count = number of refund ROWS (each partial
    #     refund is its own row in payment_refunds)
    #   • refund_payments_count = number of UNIQUE provider_payment_ids
    #     having ≥1 refund row (this is what should match the count
    #     of payments with refund status on the ptx side)
    refund_records_count = await db.payment_refunds.count_documents({
        "user_id": user_id, "provider": provider,
    })
    refund_payments_count = 0
    refund_amount = 0.0
    async for r in db.payment_refunds.aggregate([
        {"$match": {"user_id": user_id, "provider": provider}},
        {"$group": {
            "_id": "$provider_payment_id",
            "total": {"$sum": "$amount"},
        }},
        {"$group": {
            "_id": None,
            "n_payments": {"$sum": 1},
            "amount_sum": {"$sum": "$total"},
        }},
    ]):
        refund_payments_count = int(r.get("n_payments") or 0)
        refund_amount = float(r.get("amount_sum") or 0)
    refund_amount = round(refund_amount, 2)

    # --- unified_orders side (the dashboard source of truth) -------
    unified_with_refund = 0
    unified_refund_amount = 0.0
    async for r in db.unified_orders.aggregate([
        {"$match": {"user_id": user_id, "sources_seen": provider,
                    "refunded_amount": {"$gt": 0}}},
        {"$group": {"_id": None, "n": {"$sum": 1},
                    "s": {"$sum": "$refunded_amount"}}},
    ]):
        unified_with_refund = int(r.get("n") or 0)
        unified_refund_amount = float(r.get("s") or 0)
    unified_refund_amount = round(unified_refund_amount, 2)

    # --- Deltas -----------------------------------------------------
    # Status-side counts UNIQUE payments; refunds-side now also
    # counts unique payments (refund_payments_count) so partial-
    # refund rows don't inflate the delta artificially.
    delta_records_vs_status = (full_count + partial_count) - refund_payments_count
    delta_amount_ptx_vs_refunds = round(refunded_amount_in_ptx - refund_amount, 2)
    delta_amount_unified_vs_ptx = round(unified_refund_amount - refunded_amount_in_ptx, 2)

    # --- Verdict ---------------------------------------------------
    if (delta_records_vs_status == 0
            and abs(delta_amount_ptx_vs_refunds) < 0.01
            and abs(delta_amount_unified_vs_ptx) < 0.01):
        verdict = "ok"
        message = "✓ كل المسترجعات متطابقة بين API و قاعدة البيانات والتقارير."
    elif delta_records_vs_status > 0:
        verdict = "missing_records"
        message = (
            f"⚠️ {delta_records_vs_status} طلب بحالة استرجاع لكن "
            "بدون سجل مفصّل في payment_refunds — شغّل 'إعادة بناء "
            "Refunds' من وضع المطوّر."
        )
    elif abs(delta_amount_unified_vs_ptx) > 0.01:
        verdict = "dashboard_drift"
        message = (
            "⚠️ مبلغ المسترجعات في unified_orders لا يطابق "
            "payment_transactions — الأرباح/التقارير لن تعكس "
            "المسترجعات بدقّة. شغّل 'مزامنة الآن'."
        )
    else:
        verdict = "amount_mismatch"
        message = (
            f"⚠️ فرق في المبلغ بين payment_transactions و "
            f"payment_refunds = {delta_amount_ptx_vs_refunds:.2f} ر.س."
        )

    return {
        "provider": provider,
        "transactions": {
            "full_refund": full_count,
            "partial_refund": partial_count,
            "no_refund": none_count,
            "total_with_refund": full_count + partial_count,
            "refunded_amount_sum": refunded_amount_in_ptx,
        },
        "refund_records": {
            "count": refund_records_count,
            "payments_count": refund_payments_count,
            "amount_sum": refund_amount,
        },
        "unified_orders": {
            "rows_with_refund": unified_with_refund,
            "refund_amount_sum": unified_refund_amount,
        },
        "deltas": {
            "records_vs_status": delta_records_vs_status,
            "amount_ptx_vs_refunds": delta_amount_ptx_vs_refunds,
            "amount_unified_vs_ptx": delta_amount_unified_vs_ptx,
        },
        "verdict": verdict,
        "message": message,
    }


def attach_bnpl_refund_audit_routes(parent_router, *, db, get_current_user):
    router = APIRouter(prefix="/bnpl/refund-audit", tags=["BNPL Refund Audit"])

    @router.get("")
    async def refund_audit(user: dict = Depends(get_current_user)):
        try:
            uid = user["id"]
            per_provider: List[Dict[str, Any]] = []
            totals = {
                "full_refund": 0, "partial_refund": 0,
                "refund_records": 0, "refund_amount_sum": 0.0,
                "refunded_amount_in_ptx": 0.0,
                "unified_refund_amount_sum": 0.0,
            }
            all_ok = True
            for prov in PROVIDERS:
                d = await _audit_provider(db, uid, prov)
                per_provider.append(d)
                totals["full_refund"] += d["transactions"]["full_refund"]
                totals["partial_refund"] += d["transactions"]["partial_refund"]
                totals["refund_records"] += d["refund_records"]["count"]
                totals["refund_amount_sum"] += d["refund_records"]["amount_sum"]
                totals["refunded_amount_in_ptx"] += d["transactions"]["refunded_amount_sum"]
                totals["unified_refund_amount_sum"] += d["unified_orders"]["refund_amount_sum"]
                if d["verdict"] != "ok":
                    all_ok = False
            for k in ("refund_amount_sum", "refunded_amount_in_ptx",
                      "unified_refund_amount_sum"):
                totals[k] = round(totals[k], 2)

            return {
                "success": True,
                "all_ok": all_ok,
                "totals": totals,
                "providers": per_provider,
                "global_verdict": (
                    "✓ كل المسترجعات لـ Tabby و Tamara متطابقة "
                    "وتنعكس في الأرباح والتقارير."
                    if all_ok else
                    "⚠️ توجد فروقات تحتاج معالجة — راجع كل مزوّد أدناه."
                ),
            }
        except Exception as e:  # noqa: BLE001
            return {"success": False, "error": f"{type(e).__name__}: {e}"}

    @router.get("/diagnose/{provider}")
    async def diagnose_provider(
        provider: str,
        user: dict = Depends(get_current_user),
    ):
        """Detailed per-refund classification used by the UI's
        'Diagnose Delta' tool to find the exact records causing
        `records_vs_status != 0`."""
        if provider not in PROVIDERS:
            return {"success": False, "error": f"unknown provider {provider}"}
        try:
            uid = user["id"]
            return {
                "success": True,
                **(await _diagnose_provider_delta(db, uid, provider)),
            }
        except Exception as e:  # noqa: BLE001
            return {"success": False, "error": f"{type(e).__name__}: {e}"}

    parent_router.include_router(router)
