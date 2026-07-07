"""rev41 — Unified Send Diagnosis (READ-ONLY, all payment methods).

User decree: ONE rule for every payment method — either
"READY_TO_SEND_ONCE" or "REFUSED" with the REAL blocker, no hidden
differences between mada/tabby/others and no guessing.

This runs the SAME engines the send path runs, read-only:
  • build_send_preflight        (scope/payment/dup/skipped/dead/amount)
  • get_canary_budget           (pinned/used/remaining)
  • should_allow_selective_live_send — the ACTUAL policy engine, fed
    the EXACT policy_order shape one_shot builds (lines ~852) and the
    EXACT settings overlay the canary send uses.
  • one_shot stage-support matrix (incl. the partial-IC hatch rule).

ZERO writes. ZERO Qoyod API calls. `qoyod_write_reached` is always
false here by construction.
"""
from __future__ import annotations

from integrations.qoyod.canary_budget import (
    CANARY_SCOPE_ALLOWLIST, get_canary_budget,
)
from integrations.qoyod.dry_rca_report import _fetch_inbox_row
from integrations.qoyod.mada_canary_send import _OVERLAY
from integrations.qoyod.selective_send_policy import (
    should_allow_selective_live_send,
)
from integrations.qoyod.send_preflight import build_send_preflight
from integrations.qoyod.unsent_orders import _is_real

# Mirrors one_shot_reprocess._reset_row_to_stage support rules.
_ONE_SHOT_DIRECT_STAGES = {
    "DEAD_LETTER", "PARTIAL_FAILURE", "LOCKED_AWAITING_APPROVAL",
    "NORMALIZED", "NEW", "RECEIVED", "VALIDATED", "ELIGIBLE",
}


def _one_shot_stage_support(row: dict) -> dict:
    stage = row.get("pipeline_stage")
    if stage == "SKIPPED":
        return {"supported": False,
                "reason": "SKIPPED نهائية مطلقة (rev33) — لا reprocess"}
    if stage == "INVOICE_CREATED":
        if _is_real(row.get("qoyod_invoice_id")):
            return {"supported": False,
                    "reason": ("INVOICE_CREATED مع فاتورة حقيقية — "
                               "بوابة partial-IC معطلة (منع الازدواج)")}
        return {"supported": True,
                "reason": ("INVOICE_CREATED بدون فاتورة حقيقية — "
                           "مدعومة عبر بوابة partial-IC المدقّقة"),
                "requires_partial_ic_flag": True}
    if (stage in _ONE_SHOT_DIRECT_STAGES
            or str(stage or "").startswith("FAILED")):
        return {"supported": True,
                "reason": f"المرحلة {stage} مدعومة في one-shot"}
    return {"supported": False,
            "reason": (f"المرحلة {stage} غير مدعومة — one-shot يدعم "
                       "terminal/failed/pre-customer فقط")}


async def build_send_diagnosis(
    db, *, user_id: str, order_number: str,
    expected_payment_method: str | None = None,
) -> dict:
    base = {"order_number": str(order_number),
            "read_only": True, "qoyod_write_reached": False,
            "no_qoyod_api_calls": True}
    pf = await build_send_preflight(
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
    if not pf.get("found"):
        return {**base, "verdict": "REFUSED",
                "blocker_code": "order_not_found",
                "blocker_reason": "لا يوجد سجل لهذا الطلب في ميزان",
                "budget": budget_block}

    row = await _fetch_inbox_row(db, user_id, str(order_number))
    canonical = row.get("canonical_payload") or {}

    # ── REAL policy engine, EXACT one_shot inputs ────────────────────
    settings = await db.qoyod_settings.find_one(
        {"user_id": user_id}, {"_id": 0}) or {}
    eff = dict(settings)
    eff.update(_OVERLAY)                       # same canary overlay
    eff["production_writes_locked"] = False    # one_shot per-order unlock
    policy_order = {
        "order_number": (row.get("salla_order_number")
                         or canonical.get("order_number")
                         or order_number),
        "salla_order_id": (row.get("salla_order_id")
                           or canonical.get("order_id")),
        "salla_order_created_at": canonical.get("order_date"),
        "status": canonical.get("order_status"),
        "payment_method": canonical.get("payment_method"),
        "existing_qoyod_invoice_id": row.get("qoyod_invoice_id"),
        "customer_status": {
            "resolved": row.get("qoyod_customer_id") is not None,
            "qoyod_id": row.get("qoyod_customer_id"),
            "reason": None},
        "products_status": {"resolved": True, "resolved_count": 1,
                            "dry_run_only": 0, "missing": []},
        "totals_status": {"valid": True, "total": 0.0,
                          "expected": 0.0, "diff": 0.0},
    }
    decision = should_allow_selective_live_send(
        order=policy_order, settings=eff)
    policy_block = {
        "decision": decision.decision,
        "blocker_code": decision.blocker_code,
        "blocker_reason": decision.blocker_reason,
        "enabled_trigger_statuses": decision.enabled_trigger_statuses,
        "normalized_status": decision.normalized_status,
    }

    stage_support = _one_shot_stage_support(row)
    checks = pf["checks"]

    # ── ONE verdict rule for ALL payment methods ─────────────────────
    blockers: list[tuple[str, str]] = []
    for name in ("scope_check", "payment_check", "duplicate_check",
                 "skipped_history_check", "dead_letter_check",
                 "amount_check"):
        if not checks[name]["passed"]:
            blockers.append((name, checks[name]["detail"]))
    if not stage_support["supported"]:
        blockers.append(("one_shot_stage", stage_support["reason"]))
    if decision.decision != "allow":
        blockers.append((decision.blocker_code or "policy_blocked",
                         decision.blocker_reason or "policy refused"))
    if not budget_block["armed"]:
        blockers.append(("budget_not_armed", "الميزانية غير مسلّحة"))
    elif budget_block["pinned_order_number"] not in (
            None, str(order_number)):
        blockers.append(("budget_pinned_to_other_order",
                         f"الميزانية مثبّتة على "
                         f"{budget_block['pinned_order_number']}"))
    elif budget_block["remaining"] < 1 and str(order_number) not in (
            budget.get("order_numbers") or []):
        blockers.append(("budget_exhausted", "الميزانية مستهلكة"))

    verdict = "READY_TO_SEND_ONCE" if not blockers else "REFUSED"
    first = blockers[0] if blockers else (None, None)

    # Attempt history for THIS order (mada canary audit).
    attempts = []
    cur = db.mada_canary_audit_log.find(
        {"order_number": str(order_number)},
        {"_id": 0}).sort("at", -1).limit(10)
    async for a in cur:
        a["at"] = str(a.get("at"))
        attempts.append(a)

    return {
        **base,
        "verdict": verdict,
        "blocker_code": first[0],
        "blocker_reason": first[1],
        "all_blockers": [{"code": c, "reason": r} for c, r in blockers],
        "payment_method": pf["checks"]["payment_check"]["payment_method"],
        "pipeline_stage": pf.get("pipeline_stage"),
        "trace_id": pf.get("trace_id"),
        "total_amount": pf.get("total_amount"),
        "ready_to_send": pf.get("ready_to_send"),
        "duplicate_check": checks["duplicate_check"],
        "amount_check": checks["amount_check"],
        "budget": budget_block,
        "budget_used": budget_block["used"],
        "budget_remaining": budget_block["remaining"],
        "selective_send_policy": policy_block,
        "one_shot_stage_support": stage_support,
        "recent_send_attempts": attempts,
    }
