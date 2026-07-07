"""rev39.1→rev43 — MADA Candidate Finder (READ-ONLY).

rev43 (user decree): the verdict comes from the SINGLE SOURCE OF
TRUTH `evaluate_order_for_qoyod_send` ONLY. It is now IMPOSSIBLE for
an order to show `ready_now` while carrying any blocker (DRY /
SKIPPED / DEAD_LETTER / duplicate / amount / stage / policy).

Verdicts:
  • ready_now           — SSOT ready_to_send=true (zero blockers)
  • needs_product_adopt — the ONLY blocker is product_mapping_check
                          (Adopt first, re-diagnose, then send)
  • (rejected)          — anything else, counted in rejected_summary

ZERO writes, ZERO Qoyod API calls. No send / arming here.
"""
from __future__ import annotations

from integrations.qoyod.send_eligibility_ssot import (
    evaluate_order_for_qoyod_send,
)

REQUIRED_PAYMENT_METHOD = "mada"


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


def _verdict_for(ev: dict) -> tuple[str, str]:
    if ev["ready_to_send"]:
        return "ready_now", "كل فحوصات مصدر الحقيقة الواحد خضراء"
    codes = [b["code"] for b in ev["blockers"]]
    if codes == ["product_mapping_check"]:
        return "needs_product_adopt", (
            "الحاجز الوحيد هو ربط المنتجات — Adopt أولاً ثم أعد "
            "التشخيص (لا إنشاء منتج أثناء الإرسال)")
    return "rejected", (ev["primary_blocker_code"] or "blocked")


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
        ev = await evaluate_order_for_qoyod_send(
            db, user_id=user_id, order_number=order,
            expected_payment_method=REQUIRED_PAYMENT_METHOD)
        if not ev.get("found"):
            continue
        verdict, reason = _verdict_for(ev)
        if verdict == "rejected":
            rejected[reason] = rejected.get(reason, 0) + 1
            continue
        candidates.append({
            "order_number":   ev["order_number"],
            "trace_id":       ev.get("trace_id"),
            "salla_status":   ev.get("salla_status"),
            "total_amount":   ev.get("total_amount"),
            "pipeline_stage": ev.get("pipeline_stage"),
            "verdict":        verdict,
            "reason":         reason,
            "amount_difference": ev["amount_check"].get("difference"),
            "unmapped_skus":
                list(ev["product_mapping_check"]["unmapped_skus"]),
            "send_eligibility": {
                "eligible": ev["eligible"],
                "ready_to_send": ev["ready_to_send"],
                "blockers": ev["blockers"],
                "primary_blocker_code": ev["primary_blocker_code"],
                "primary_blocker_reason": ev["primary_blocker_reason"],
            },
        })
    candidates.sort(
        key=lambda c: 0 if c["verdict"] == "ready_now" else 1)
    return {
        "ok": True,
        "payment_method": REQUIRED_PAYMENT_METHOD,
        "scanned_orders": len(order_numbers),
        "candidates": candidates,
        "rejected_summary": rejected,
        "read_only": True,
        "no_qoyod_api_calls": True,
        "source": "evaluate_order_for_qoyod_send",
        "note": ("قراءة فقط — الحكم من مصدر الحقيقة الواحد فقط. "
                 "ready_now يستحيل مع أي blocker. "
                 "needs_product_adopt = اربط المنتج أولاً ثم أعد "
                 "التشخيص. الميزانية ليست جزءاً من أهلية الطلب."),
    }
