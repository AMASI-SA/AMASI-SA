"""rev43 — SINGLE SOURCE OF TRUTH: "هل الطلب صالح للإرسال إلى قيود؟"

User decree (2026-06): the send decision was duplicated and
contradictory across mada-candidates / send-diagnosis / one-shot /
Selective Send / pipeline / unsent-orders. From now on EVERY consumer
calls `evaluate_order_for_qoyod_send` — one contract, one verdict.

FINAL RULE: a fully GREEN diagnosis is required before ANY Qoyod
write. Unmapped product ⇒ blocker `product_mapping_check`, NO product
creation inside the send — Adopt first, re-diagnose, then ONE send.

READ-ONLY: zero DB writes, zero Qoyod API calls.
"""
from __future__ import annotations

from datetime import datetime, timezone

from integrations.qoyod.dry_rca_report import _fetch_inbox_row
from integrations.qoyod.selective_send_policy import (
    should_allow_selective_live_send,
)
from integrations.qoyod.send_preflight import build_send_preflight
from integrations.qoyod.unsent_orders import _is_real

# Policy evaluation overlay — judges ORDER-INTRINSIC qualities only.
# The two operator gates are opened per-order exactly the way one_shot
# does at send time (production_writes_locked=False per approval) so
# the verdict reflects the order, not the operator switches. NEVER
# written to qoyod_settings.
POLICY_EVAL_OVERLAY = {
    "selective_live_send_enabled": True,
    "production_writes_locked":    False,
}

# Mirrors one_shot_reprocess._reset_row_to_stage support rules.
_ONE_SHOT_DIRECT_STAGES = {
    "DEAD_LETTER", "PARTIAL_FAILURE", "LOCKED_AWAITING_APPROVAL",
    "NORMALIZED", "NEW", "RECEIVED", "VALIDATED", "ELIGIBLE",
}

# Policy codes already represented by a dedicated SSOT check — skip
# them in the policy blocker to avoid double-listing one root cause.
_POLICY_CODES_COVERED = {
    "dry_invoice_id_detected": "dry_check",
    "preview_id_detected":     "dry_check",
    "customer_dry_or_null":    "dry_check",
    "already_sent":            "duplicate_check",
    "before_sync_start_date":  "sync_start_date_check",
    "product_missing_mapping": "product_mapping_check",
}


def _stage_check(row: dict) -> dict:
    stage = row.get("pipeline_stage")
    if stage == "SKIPPED":
        # rev44 — transient skips (status/payment scope) are
        # resumable via the audited one-shot; unclassified/legacy
        # SKIPPED stays absolutely terminal (fail-closed).
        if row.get("skip_class") == "transient":
            return {"passed": True, "pipeline_stage": stage,
                    "skip_class": "transient",
                    "detail": ("SKIPPED مؤقت (rev44 "
                               "pending_until_eligible) — قابل "
                               "للاستئناف عبر one-shot مدقق")}
        return {"passed": False, "pipeline_stage": stage,
                "skip_class": row.get("skip_class"),
                "detail": "SKIPPED نهائية مطلقة (rev33) — لا reprocess"}
    if stage == "INVOICE_CREATED":
        if _is_real(row.get("qoyod_invoice_id")):
            return {"passed": False, "pipeline_stage": stage,
                    "detail": ("INVOICE_CREATED مع فاتورة حقيقية — "
                               "بوابة partial-IC معطلة (منع الازدواج)")}
        return {"passed": True, "pipeline_stage": stage,
                "requires_partial_ic_flag": True,
                "detail": ("INVOICE_CREATED بدون فاتورة حقيقية — "
                           "مدعومة عبر بوابة partial-IC المدقّقة")}
    if (stage in _ONE_SHOT_DIRECT_STAGES
            or str(stage or "").startswith("FAILED")):
        return {"passed": True, "pipeline_stage": stage,
                "detail": f"المرحلة {stage} مدعومة في one-shot"}
    return {"passed": False, "pipeline_stage": stage,
            "detail": (f"المرحلة {stage} غير مدعومة — one-shot يدعم "
                       "terminal/failed/pre-customer فقط")}


def _dry_check(row: dict) -> dict:
    hits = []
    for field in ("qoyod_invoice_id", "qoyod_customer_id"):
        v = row.get(field)
        if v and str(v).startswith(("DRY:", "PREVIEW:")):
            hits.append(f"{field}={v}")
    if hits:
        return {"passed": False, "dry_ids": hits,
                "detail": ("معرّفات DRY/PREVIEW على الصف: "
                           + "؛ ".join(hits)
                           + " — أي إرسال مرفوض حتى تُنظَّف")}
    return {"passed": True, "dry_ids": [],
            "detail": "لا معرّفات DRY/PREVIEW على الصف"}


async def evaluate_order_for_qoyod_send(
    db, *, user_id: str, order_number: str,
    expected_payment_method: str | None = None,
) -> dict:
    """ONE verdict for every payment method. READ-ONLY."""
    base = {
        "order_number": str(order_number),
        "read_only": True,
        "no_qoyod_api_calls": True,
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
        "source": "evaluate_order_for_qoyod_send",
    }
    pf = await build_send_preflight(
        db, user_id=user_id, order_number=str(order_number),
        expected_payment_method=expected_payment_method)
    if not pf.get("found"):
        blocker = {"code": "order_not_found",
                   "reason": "لا يوجد سجل لهذا الطلب في ميزان"}
        return {**base, "found": False,
                "eligible": False, "ready_to_send": False,
                "blockers": [blocker],
                "primary_blocker_code": blocker["code"],
                "primary_blocker_reason": blocker["reason"]}

    row = await _fetch_inbox_row(db, user_id, str(order_number))
    canonical = row.get("canonical_payload") or {}
    c = pf["checks"]

    # ── Contract checks ──────────────────────────────────────────────
    sync_start_date_check = c["scope_check"]
    payment_check = c["payment_check"]
    duplicate_check = c["duplicate_check"]

    sk, dl = c["skipped_history_check"], c["dead_letter_check"]
    skipped_dead_letter_check = {
        "passed": sk["passed"] and dl["passed"],
        "skipped": {"passed": sk["passed"], "detail": sk["detail"]},
        "dead_letter": {"passed": dl["passed"], "detail": dl["detail"],
                        "dead_lettered_at": dl.get("dead_lettered_at")},
        "detail": ("؛ ".join(d for d in (
            None if sk["passed"] else sk["detail"],
            None if dl["passed"] else dl["detail"]) if d)
            or "لا SKIPPED ولا DEAD_LETTER على الصف"),
    }

    stage_check = _stage_check(row)
    dry_check = _dry_check(row)

    unmapped = list(c["amount_check"].get("unmapped_skus") or [])
    product_mapping_check = {
        "passed": not unmapped,
        "unmapped_skus": unmapped,
        "detail": (("منتجات بدون ربط حقيقي في قيود: "
                    + ", ".join(unmapped)
                    + " — اربطها عبر Adopt أولاً؛ لا إنشاء منتج أثناء "
                    "الإرسال (قرار المستخدم rev43)")
                   if unmapped else
                   "كل منتجات الطلب مربوطة بمعرفات قيود حقيقية"),
    }
    amount_check = c["amount_check"]

    # ── Selective Send policy — order-intrinsic (gates overlaid) ────
    settings = await db.qoyod_settings.find_one(
        {"user_id": user_id}, {"_id": 0}) or {}
    eff = dict(settings)
    eff.update(POLICY_EVAL_OVERLAY)
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
            "reason": None,
            # rev45 — mirror one_shot: unresolved customer is
            # resolved during the audited send (DRY stays fatal).
            "pending_resolution_during_send":
                row.get("qoyod_customer_id") is None,
        },
        "products_status": {
            "resolved": not unmapped, "resolved_count": 1,
            "dry_run_only": 0, "missing": list(unmapped)},
        "totals_status": {"valid": True, "total": 0.0,
                          "expected": 0.0, "diff": 0.0},
    }
    decision = should_allow_selective_live_send(
        order=policy_order, settings=eff)
    policy_check = {
        "passed": decision.decision == "allow",
        "blocker_code": decision.blocker_code,
        "blocker_reason": decision.blocker_reason,
        "normalized_status": decision.normalized_status,
        "enabled_trigger_statuses": decision.enabled_trigger_statuses,
    }

    # ── Blockers: ONE deterministic assembly for ALL consumers ──────
    blockers: list[dict] = []

    def _add(code: str, check: dict, reason_key: str = "detail"):
        if not check["passed"]:
            blockers.append({"code": code,
                             "reason": check.get(reason_key)})

    _add("sync_start_date_check", sync_start_date_check)
    _add("payment_check", payment_check)
    _add("duplicate_check", duplicate_check)
    _add("dry_check", dry_check)
    _add("skipped_dead_letter_check", skipped_dead_letter_check)
    _add("stage_check", stage_check)
    _add("product_mapping_check", product_mapping_check)
    if not unmapped:                    # computable only when mapped
        _add("amount_check", amount_check)
    if not policy_check["passed"]:
        covering = _POLICY_CODES_COVERED.get(policy_check["blocker_code"])
        already = covering and any(b["code"] == covering
                                   for b in blockers)
        if not already:
            blockers.append({
                "code": policy_check["blocker_code"] or "policy_blocked",
                "reason": (policy_check["blocker_reason"]
                           or "policy refused")})

    eligible = bool(sync_start_date_check["passed"])
    ready_to_send = not blockers
    primary = blockers[0] if blockers else None

    return {
        **base,
        "found": True,
        "trace_id": row.get("trace_id"),
        "pipeline_stage": row.get("pipeline_stage"),
        "payment_method": payment_check.get("payment_method"),
        "salla_status": pf.get("salla_status"),
        "total_amount": pf.get("total_amount"),
        "eligible": eligible,
        "ready_to_send": ready_to_send,
        "blockers": blockers,
        "primary_blocker_code": primary["code"] if primary else None,
        "primary_blocker_reason": primary["reason"] if primary else None,
        "duplicate_check": duplicate_check,
        "amount_check": amount_check,
        "product_mapping_check": product_mapping_check,
        "stage_check": stage_check,
        "dry_check": dry_check,
        "skipped_dead_letter_check": skipped_dead_letter_check,
        "sync_start_date_check": sync_start_date_check,
        "payment_check": payment_check,
        "policy_check": policy_check,
    }


async def build_send_eligibility_preview(
    db, *, user_id: str, limit: int = 20, scan_limit: int = 200,
) -> dict:
    """READ-ONLY preview: latest N distinct orders (ANY payment
    method) evaluated by the ONE source of truth."""
    limit = max(1, min(int(limit), 50))
    pipeline = [
        {"$match": {"user_id": user_id,
                    "salla_order_number": {"$nin": [None, ""]}}},
        {"$sort": {"received_at": -1}},
        {"$group": {"_id": "$salla_order_number",
                    "latest": {"$first": "$received_at"}}},
        {"$sort": {"latest": -1}},
        {"$limit": int(max(limit, min(scan_limit, 1000)))},
    ]
    order_numbers = [str(d["_id"]) async for d in
                     db.integration_inbox.aggregate(pipeline)]

    items: list[dict] = []
    blocker_code_counts: dict[str, int] = {}
    ready = 0
    for order in order_numbers[:limit]:
        ev = await evaluate_order_for_qoyod_send(
            db, user_id=user_id, order_number=order)
        if ev["ready_to_send"]:
            ready += 1
        for b in ev["blockers"]:
            blocker_code_counts[b["code"]] = \
                blocker_code_counts.get(b["code"], 0) + 1
        items.append({
            "order_number": ev["order_number"],
            "payment_method": ev.get("payment_method"),
            "salla_status": ev.get("salla_status"),
            "pipeline_stage": ev.get("pipeline_stage"),
            "total_amount": ev.get("total_amount"),
            "eligible": ev["eligible"],
            "ready_to_send": ev["ready_to_send"],
            "blocker_codes": [b["code"] for b in ev["blockers"]],
            "primary_blocker_code": ev["primary_blocker_code"],
            "primary_blocker_reason": ev["primary_blocker_reason"],
        })

    return {
        "ok": True,
        "read_only": True,
        "no_qoyod_api_calls": True,
        "source": "evaluate_order_for_qoyod_send",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_evaluated": len(items),
        "ready_to_send_count": ready,
        "blocked_count": len(items) - ready,
        "blocker_code_counts": blocker_code_counts,
        "items": items,
        "note": ("قراءة فقط — نفس الدالة الموحدة التي تحكم "
                 "mada-candidates وsend-diagnosis وبوابة one-shot. "
                 "لا يمكن أن يظهر طلب ready وفي نفس الوقت عنده blocker."),
    }
