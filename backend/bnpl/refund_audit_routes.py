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
    refund_records = await db.payment_refunds.count_documents({
        "user_id": user_id, "provider": provider,
    })
    refund_amount = 0.0
    async for r in db.payment_refunds.aggregate([
        {"$match": {"user_id": user_id, "provider": provider}},
        {"$group": {"_id": None, "s": {"$sum": "$amount"}}},
    ]):
        refund_amount = float(r.get("s") or 0)
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
    delta_records_vs_status = (full_count + partial_count) - refund_records
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
            "count": refund_records,
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

    parent_router.include_router(router)
