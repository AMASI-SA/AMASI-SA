"""rev39.1 — MADA Candidate Finder (READ-ONLY).

User decree: order 270513107 is vetoed (SKIPPED row). Find the first
mada order that is TRULY eligible, using the SAME preflight checks:
  • Salla creation date >= 2026-07-01
  • payment_method = mada
  • latest row NOT SKIPPED (no rev33 veto)
  • no REAL قيود invoice
  • products mapped (ready_now) or only-missing products
    (ready_with_product_create) — amount diff <= 0.01 when computable

ZERO writes, ZERO Qoyod API calls. No send / run-now / reprocess /
canary arming here.
"""
from __future__ import annotations

from integrations.qoyod.send_preflight import build_send_preflight

REQUIRED_PAYMENT_METHOD = "mada"
_ACCEPTED_STATUSES = frozenset({"completed", "تم التنفيذ"})

# rev41.2 — budget blockers are EXPECTED while the budget is still
# pinned to the old canary order; they are separated so the operator
# can judge candidate eligibility WITHOUT touching the budget.
_BUDGET_BLOCKER_CODES = frozenset({
    "budget_not_armed", "budget_pinned_to_other_order",
    "budget_exhausted",
})


async def _recent_mada_order_numbers(db, user_id: str,
                                     scan_limit: int) -> list[str]:
    pipeline = [
        {"$match": {
            "user_id": user_id,
            "canonical_payload.payment_method": REQUIRED_PAYMENT_METHOD,
            "salla_order_number": {"$nin": [None, ""]},
        }},
        {"$sort": {"received_at": -1}},
        {"$group": {"_id": "$salla_order_number",
                    "latest": {"$first": "$received_at"}}},
        {"$sort": {"latest": -1}},
        {"$limit": int(scan_limit)},
    ]
    return [str(d["_id"]) async for d in
            db.integration_inbox.aggregate(pipeline)]


def _classify(pf: dict) -> tuple[str, str]:
    """→ (verdict, reason). verdict ∈ ready_now /
    ready_with_product_create / rejected."""
    c = pf["checks"]
    if not c["scope_check"]["passed"]:
        return "rejected", "خارج نطاق 2026-07-01"
    if not c["payment_check"]["passed"]:
        return "rejected", "طريقة الدفع ليست mada"
    if not c["duplicate_check"]["passed"]:
        return "rejected", "توجد فاتورة قيود حقيقية"
    if not c["skipped_history_check"]["passed"]:
        return "rejected", "فيتو SKIPPED (rev33)"
    if not c["dead_letter_check"]["passed"]:
        return "rejected", "فيتو DEAD_LETTER/حالة محظورة (rev32.1)"
    status = str(pf.get("salla_status") or "").strip()
    if status not in _ACCEPTED_STATUSES:
        return "rejected", f"حالة سلة غير مؤهلة ({status})"
    amount = c["amount_check"]
    if amount["passed"]:
        return "ready_now", "كل الفحوصات خضراء — منتجات مربوطة وفرق ≤ 0.01"
    unmapped = list(amount.get("unmapped_skus") or [])
    if unmapped:
        return "ready_with_product_create", (
            "مؤهل — يحتاج إنشاء/ربط منتجات: " + ", ".join(unmapped))
    return "rejected", (amount.get("detail")
                        or "فرق المبلغ أكبر من 0.01")


async def find_mada_candidates(
    db, *, user_id: str, limit: int = 5, scan_limit: int = 200,
) -> dict:
    order_numbers = await _recent_mada_order_numbers(
        db, user_id, scan_limit)
    candidates: list[dict] = []
    rejected: dict[str, int] = {}
    for order in order_numbers:
        if len(candidates) >= limit:
            break
        pf = await build_send_preflight(
            db, user_id=user_id, order_number=order,
            expected_payment_method=REQUIRED_PAYMENT_METHOD)
        if not pf.get("found"):
            continue
        verdict, reason = _classify(pf)
        if verdict == "rejected":
            rejected[reason] = rejected.get(reason, 0) + 1
            continue
        amount = pf["checks"]["amount_check"]
        candidates.append({
            "order_number":   pf["order_number"],
            "trace_id":       pf["trace_id"],
            "salla_status":   pf["salla_status"],
            "total_amount":   pf["total_amount"],
            "pipeline_stage": pf["pipeline_stage"],
            "verdict":        verdict,
            "reason":         reason,
            "amount_difference": amount.get("difference"),
            "unmapped_skus":  list(amount.get("unmapped_skus") or []),
        })
    # ready_now first (fully green), then create-needed.
    candidates.sort(key=lambda c: 0 if c["verdict"] == "ready_now" else 1)

    # rev41.2 — enrich each candidate with the FULL unified send
    # diagnosis (rev41), read-only. Budget blockers are listed apart
    # so the pinned/unarmed budget does not hide real blockers.
    from integrations.qoyod.send_diagnosis import build_send_diagnosis
    for cand in candidates:
        diag = await build_send_diagnosis(
            db, user_id=user_id, order_number=cand["order_number"],
            expected_payment_method=REQUIRED_PAYMENT_METHOD)
        all_blockers = diag.get("all_blockers") or []
        non_budget = [b for b in all_blockers
                      if b["code"] not in _BUDGET_BLOCKER_CODES]
        budget_only = [b for b in all_blockers
                       if b["code"] in _BUDGET_BLOCKER_CODES]
        cand["send_diagnosis"] = {
            "verdict": diag.get("verdict"),
            "blocker_code": diag.get("blocker_code"),
            "blocker_reason": diag.get("blocker_reason"),
            "non_budget_blockers": non_budget,
            "budget_blockers": budget_only,
            "ready_excluding_budget": len(non_budget) == 0,
            "duplicate_check": diag.get("duplicate_check"),
            "amount_check": diag.get("amount_check"),
            "pipeline_stage": diag.get("pipeline_stage"),
        }
    # Fully-clean candidates (ignoring budget) first.
    candidates.sort(key=lambda c: (
        0 if c["send_diagnosis"]["ready_excluding_budget"] else 1,
        0 if c["verdict"] == "ready_now" else 1))
    return {
        "ok": True,
        "payment_method": REQUIRED_PAYMENT_METHOD,
        "scanned_orders": len(order_numbers),
        "candidates": candidates,
        "rejected_summary": rejected,
        "read_only": True,
        "no_qoyod_api_calls": True,
        "note": ("قراءة فقط — لا إرسال ولا تسليح. المرشح ready_now "
                 "مع ready_excluding_budget=true جاهز للاعتماد؛ "
                 "حواجز الميزانية معروضة منفصلة ولن تُغيَّر الميزانية "
                 "إلا بعد اعتماد الطلب البديل."),
    }
