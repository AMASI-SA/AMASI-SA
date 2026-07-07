"""rev41→rev43 — Unified Send Diagnosis (READ-ONLY, all methods).

Now a THIN WRAPPER around the single source of truth
`evaluate_order_for_qoyod_send` (send_eligibility_ssot.py) plus the
canary-budget layer (budget is an OPERATOR constraint, not an
order-eligibility fact, so it lives here — not in the SSOT).

ZERO writes. ZERO Qoyod API calls. `qoyod_write_reached` always false.
"""
from __future__ import annotations

from integrations.qoyod.canary_budget import (
    CANARY_SCOPE_ALLOWLIST, get_canary_budget,
)
from integrations.qoyod.eligible_orders import QOYOD_TAX_PERIOD
from integrations.qoyod.mada_canary_send import _OVERLAY
from integrations.qoyod.selective_send_policy import (
    QOYOD_ENABLED_TRIGGER_STATUSES_DEFAULT,
    QOYOD_INVOICE_DATE_SOURCE_DEFAULT,
    _floored_sync_start,
)
from integrations.qoyod.send_eligibility_ssot import (
    POLICY_EVAL_OVERLAY, evaluate_order_for_qoyod_send,
)

_BUDGET_BLOCKER_CODES = ("budget_not_armed",
                         "budget_pinned_to_other_order",
                         "budget_exhausted")


def _gates_of(settings: dict) -> dict:
    return {
        "selective_live_send_enabled": bool(
            settings.get("selective_live_send_enabled", False)),
        "production_writes_locked": bool(
            settings.get("production_writes_locked", True)),
        "qoyod_sync_start_date": _floored_sync_start(
            settings.get("qoyod_sync_start_date")).isoformat(),
        "qoyod_tax_period": settings.get(
            "qoyod_tax_period", QOYOD_TAX_PERIOD),
        "bank_transfer_routing_enabled": bool(
            settings.get("bank_transfer_routing_enabled", False)),
        "qoyod_invoice_date_source": settings.get(
            "qoyod_invoice_date_source",
            QOYOD_INVOICE_DATE_SOURCE_DEFAULT),
        "qoyod_enabled_invoice_trigger_statuses": list(
            settings.get("qoyod_enabled_invoice_trigger_statuses")
            or QOYOD_ENABLED_TRIGGER_STATUSES_DEFAULT),
    }


async def build_send_diagnosis(
    db, *, user_id: str, order_number: str,
    expected_payment_method: str | None = None,
) -> dict:
    base = {"order_number": str(order_number),
            "read_only": True, "qoyod_write_reached": False,
            "no_qoyod_api_calls": True,
            "source": "evaluate_order_for_qoyod_send"}

    ssot = await evaluate_order_for_qoyod_send(
        db, user_id=user_id, order_number=str(order_number),
        expected_payment_method=expected_payment_method)

    budget = await get_canary_budget(db, user_id=user_id)
    budget_block = {
        "armed": budget.get("armed", False),
        "pinned_order_number": budget.get("pinned_order_number"),
        "used": budget.get("used", 0),
        "remaining": budget.get("remaining", 0),
        "canary_payment_method": CANARY_SCOPE_ALLOWLIST[0],
    }

    if not ssot.get("found"):
        return {**base, "verdict": "REFUSED",
                "blocker_code": ssot["primary_blocker_code"],
                "blocker_reason": ssot["primary_blocker_reason"],
                "all_blockers": ssot["blockers"],
                "ssot": ssot, "budget": budget_block}

    # ── Budget layer (operator constraint, on top of the SSOT) ──────
    budget_blockers: list[dict] = []
    if not budget_block["armed"]:
        budget_blockers.append({"code": "budget_not_armed",
                                "reason": "الميزانية غير مسلّحة"})
    elif budget_block["pinned_order_number"] not in (
            None, str(order_number)):
        budget_blockers.append({
            "code": "budget_pinned_to_other_order",
            "reason": (f"الميزانية مثبّتة على "
                       f"{budget_block['pinned_order_number']}")})
    elif budget_block["remaining"] < 1 and str(order_number) not in (
            budget.get("order_numbers") or []):
        budget_blockers.append({"code": "budget_exhausted",
                                "reason": "الميزانية مستهلكة"})

    all_blockers = list(ssot["blockers"]) + budget_blockers
    verdict = "READY_TO_SEND_ONCE" if not all_blockers else "REFUSED"
    first = all_blockers[0] if all_blockers else None

    # ── Guards snapshot (stored vs overlays) ─────────────────────────
    settings = await db.qoyod_settings.find_one(
        {"user_id": user_id}, {"_id": 0}) or {}
    eff = dict(settings)
    eff.update(_OVERLAY)
    guards_snapshot = {
        "stored_settings_gates": _gates_of(settings),
        "effective_during_canary_send": _gates_of(eff),
        "canary_overlay_applied": dict(_OVERLAY),
        "ssot_policy_overlay": dict(POLICY_EVAL_OVERLAY),
        "note": ("stored = المحفوظ فعلياً في qoyod_settings "
                 "(fail-closed). effective = ما يراه محرك السياسة "
                 "أثناء إرسال الكناري — الطبقة لا تُكتب في قاعدة "
                 "البيانات أبداً."),
    }

    # Attempt history for THIS order (canary audit, read-only).
    attempts = []
    cur = db.mada_canary_audit_log.find(
        {"order_number": str(order_number)},
        {"_id": 0}).sort("at", -1).limit(10)
    async for a in cur:
        a["at"] = str(a.get("at"))
        attempts.append(a)

    pol = ssot["policy_check"]
    return {
        **base,
        "verdict": verdict,
        "blocker_code": first["code"] if first else None,
        "blocker_reason": first["reason"] if first else None,
        "all_blockers": all_blockers,
        "eligible": ssot["eligible"],
        "ready_to_send": ssot["ready_to_send"],
        "payment_method": ssot.get("payment_method"),
        "pipeline_stage": ssot.get("pipeline_stage"),
        "trace_id": ssot.get("trace_id"),
        "total_amount": ssot.get("total_amount"),
        "duplicate_check": ssot["duplicate_check"],
        "amount_check": ssot["amount_check"],
        "product_mapping_check": ssot["product_mapping_check"],
        "stage_check": ssot["stage_check"],
        "dry_check": ssot["dry_check"],
        "skipped_dead_letter_check": ssot["skipped_dead_letter_check"],
        "sync_start_date_check": ssot["sync_start_date_check"],
        "selective_send_policy": {
            "decision": "allow" if pol["passed"] else "block",
            "blocker_code": pol["blocker_code"],
            "blocker_reason": pol["blocker_reason"],
            "enabled_trigger_statuses": pol["enabled_trigger_statuses"],
            "normalized_status": pol["normalized_status"],
        },
        "one_shot_stage_support": {
            "supported": ssot["stage_check"]["passed"],
            "reason": ssot["stage_check"]["detail"],
            **({"requires_partial_ic_flag": True}
               if ssot["stage_check"].get("requires_partial_ic_flag")
               else {}),
        },
        "budget": budget_block,
        "budget_used": budget_block["used"],
        "budget_remaining": budget_block["remaining"],
        "budget_blockers": budget_blockers,
        "guards_snapshot": guards_snapshot,
        "ssot": ssot,
        "recent_send_attempts": attempts,
    }
