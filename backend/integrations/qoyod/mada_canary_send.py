"""rev39 — MADA Canary Send (single-order, one-shot, fail-closed).

User decree (2026-07):
    order_number   = 270513107 ONLY
    payment_method = mada ONLY
    SKU AMS11981 is created ONCE inside this approved send
    max_orders     = 1 (canary budget, pinned to this order)
    No duplicate invoice. Totals diff <= 0.01. Then invoice + payment.

Design mirrors the (closed) tabby canary_live_send: hard guards run
BEFORE any Qoyod call; refusals are structured; the actual send is
delegated to the audited `reprocess_one_order`. The settings overlay
is SCOPED to this single call — `qoyod_settings` in the DB is NEVER
mutated.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from integrations.qoyod.canary_budget import (
    CANARY_SCOPE_ALLOWLIST, get_canary_budget,
)
from integrations.qoyod.eligible_orders import (
    QOYOD_SYNC_START_DATE, _parse_iso_date,
)
from integrations.qoyod.send_preflight import build_send_preflight
from integrations.qoyod.unsent_orders import _is_real, _order_created_date

# ── Immutable contract constants (user decree) ──────────────────────
# rev46 — re-pinned to 270939808 (credit_card, first order to pass
# the FULL SSOT green diagnosis under rev43/44/45). Previous picks
# 269875747 (SKIPPED+DL+DRY — permanently excluded), 270513107 and
# 269997994 remain untouched.
MADA_CANARY_ORDER_NUMBER: str = "270939808"
MADA_CANARY_APPROVAL_PHRASE: str = (
    "Approved live Qoyod credit_card canary send for order "
    "270939808 only")
REQUIRED_PAYMENT_METHOD: str = "credit_card"
REQUIRED_SKU: str = "AMS11981"
_ACCEPTED_STATUSES = frozenset({"completed", "تم التنفيذ"})
AUDIT_COLLECTION = "mada_canary_audit_log"


class MadaCanaryGuardFailed(Exception):
    def __init__(self, guard_no: int, code: str, detail: str):
        super().__init__(f"guard#{guard_no} {code}: {detail}")
        self.guard_no = guard_no
        self.code = code
        self.detail = detail


# ── Scoped settings overlay — NEVER writes to qoyod_settings ────────
_OVERLAY = {
    "dry_run_mode":                False,
    "production_writes_locked":    False,
    "selective_live_send_enabled": True,
    "selective_auto_send_enabled": True,
    "selective_auto_send_allowed_payment_methods":
        list(CANARY_SCOPE_ALLOWLIST),
}


class _ScopedSettingsColl:
    __slots__ = ("_coll",)

    def __init__(self, real_coll):
        self._coll = real_coll

    async def find_one(self, *a, **kw):
        doc = await self._coll.find_one(*a, **kw)
        if isinstance(doc, dict):
            doc = dict(doc)
            doc.update(_OVERLAY)
            doc.setdefault("selective_auto_send_cutover_at",
                           f"{QOYOD_SYNC_START_DATE}T00:00:00+00:00")
        return doc

    def __getattr__(self, name):
        return getattr(self._coll, name)


class _ScopedDB:
    """DB proxy: overlays reads of qoyod_settings only. All other
    collections (budget, inbox, audits, mappings) pass through."""

    def __init__(self, real_db):
        object.__setattr__(self, "_db", real_db)

    def __getattr__(self, name):
        if name == "qoyod_settings":
            return _ScopedSettingsColl(self._db.qoyod_settings)
        return getattr(self._db, name)

    def __getitem__(self, name):
        if name == "qoyod_settings":
            return _ScopedSettingsColl(self._db["qoyod_settings"])
        return self._db[name]


async def _audit(db, attempt_id: str, phase: str, status: str,
                 **extra) -> None:
    await db[AUDIT_COLLECTION].insert_one({
        "attempt_id": attempt_id, "phase": phase, "status": status,
        "at": datetime.now(timezone.utc), **extra})


async def _run_guards(db, *, user_id: str, order_number: str,
                      approval_phrase: str) -> dict:
    # 1 — approval phrase, exact.
    if approval_phrase != MADA_CANARY_APPROVAL_PHRASE:
        raise MadaCanaryGuardFailed(
            1, "approval_phrase_mismatch",
            f"العبارة يجب أن تكون حرفياً: '{MADA_CANARY_APPROVAL_PHRASE}'")
    # 2 — order scope, exact.
    if str(order_number).strip() != MADA_CANARY_ORDER_NUMBER:
        raise MadaCanaryGuardFailed(
            2, "order_out_of_scope",
            f"هذه الأداة تعمل حصرياً للطلب {MADA_CANARY_ORDER_NUMBER}")
    # 3 — preflight (same read-only checks the operator reviewed).
    pf = await build_send_preflight(
        db, user_id=user_id, order_number=MADA_CANARY_ORDER_NUMBER,
        expected_payment_method=REQUIRED_PAYMENT_METHOD)
    if not pf.get("found"):
        raise MadaCanaryGuardFailed(3, "order_not_found",
                                    "لا يوجد سجل للطلب في ميزان")
    checks = pf["checks"]
    if not checks["scope_check"]["passed"]:
        raise MadaCanaryGuardFailed(
            4, "scope_check_failed", checks["scope_check"]["detail"])
    if not checks["payment_check"]["passed"]:
        raise MadaCanaryGuardFailed(
            5, "payment_method_mismatch",
            checks["payment_check"]["detail"])
    if not checks["duplicate_check"]["passed"]:
        raise MadaCanaryGuardFailed(
            6, "duplicate_real_invoice",
            checks["duplicate_check"]["detail"])
    # 7 — Salla status must be executed/completed.
    status = str(pf.get("salla_status") or "").strip()
    if status not in _ACCEPTED_STATUSES:
        raise MadaCanaryGuardFailed(
            7, "status_not_completed",
            f"حالة الطلب في سلة '{status}' — المقبول: تم التنفيذ/completed")
    # 8 — required SKU present; amount check: mapped→must pass; the
    # ONLY tolerated amount_check failure is the unmapped REQUIRED_SKU
    # (it is created once inside this send; the pipeline's own totals
    # guard still blocks >0.01 AFTER creation, before any invoice).
    amount = checks["amount_check"]
    unmapped = list(amount.get("unmapped_skus") or [])
    if not amount["passed"]:
        if unmapped != [REQUIRED_SKU]:
            raise MadaCanaryGuardFailed(
                8, "amount_check_failed",
                amount.get("detail") or "فشل فحص المبلغ")
    # 9 — SKIPPED history veto (rev33 X) — refuse cleanly here instead
    # of tripping the kill switch mid-send. rev44: a SKIPPED stamped
    # skip_class='transient' is resumable via the audited one-shot;
    # fatal/unclassified SKIPPED stays refused (fail-closed).
    row = await db.integration_inbox.find_one(
        {"user_id": user_id,
         "salla_order_number": MADA_CANARY_ORDER_NUMBER,
         "trace_id": pf.get("trace_id")},
        {"_id": 0, "stage_history": 1, "pipeline_stage": 1,
         "skip_class": 1})
    history = [str(h.get("stage") or h) for h in
               (row or {}).get("stage_history") or []]
    _has_skipped = ("SKIPPED" in history
                    or (row or {}).get("pipeline_stage") == "SKIPPED")
    if _has_skipped and (row or {}).get("skip_class") != "transient":
        raise MadaCanaryGuardFailed(
            9, "skipped_history_veto",
            "الصف المختار سبق تخطيه (SKIPPED) بتصنيف قاتل/غير مصنّف — "
            "فيتو rev33 يمنع إرساله. نحتاج قراراً منفصلاً/صفاً جديداً "
            "من الويبهوك.")
    # 9.5 — DEAD_LETTER / blocked-stage veto (rev32.1) — attempting a
    # write would trip the kill switch; refuse cleanly here.
    if not pf["checks"].get("dead_letter_check", {}).get("passed", False):
        raise MadaCanaryGuardFailed(
            9, "dead_letter_veto",
            pf["checks"].get("dead_letter_check", {}).get("detail")
            or "الصف بحالة محظورة للكتابة (rev32.1)")
    # 10 — canary budget: armed + pinned to THIS order + slot free.
    budget = await get_canary_budget(db, user_id=user_id)
    if not budget.get("armed"):
        raise MadaCanaryGuardFailed(
            10, "budget_not_armed",
            "ميزانية الكناري غير مسلّحة — سلّحها أولاً "
            "(POST /admin/live-canary/budget/arm مع "
            "pinned_order_number=270513107)")
    if budget.get("pinned_order_number") != MADA_CANARY_ORDER_NUMBER:
        raise MadaCanaryGuardFailed(
            10, "budget_not_pinned_to_order",
            f"الميزانية غير مثبّتة على {MADA_CANARY_ORDER_NUMBER} "
            f"(pinned={budget.get('pinned_order_number')!r}) — أعد "
            "التسليح مع pinned_order_number")
    used = list(budget.get("order_numbers") or [])
    if used and used != [MADA_CANARY_ORDER_NUMBER]:
        raise MadaCanaryGuardFailed(
            10, "budget_consumed_by_other_order",
            f"الميزانية مستهلكة بواسطة {used!r}")
    return pf


async def execute_mada_canary_send(
    db, *, user_id: str, order_number: str, approval_phrase: str,
    actor: str = "operator",
) -> dict:
    attempt_id = str(uuid.uuid4())
    await _audit(db, attempt_id, "attempt_received", "pending",
                 actor=actor, order_number=str(order_number))
    try:
        pf = await _run_guards(
            db, user_id=user_id, order_number=str(order_number),
            approval_phrase=approval_phrase)
    except MadaCanaryGuardFailed as g:
        await _audit(db, attempt_id, "guard_check", "refused",
                     guard_no=g.guard_no, code=g.code, detail=g.detail)
        return {"attempt_id": attempt_id, "outcome": "REFUSED",
                "guard_no": g.guard_no, "code": g.code,
                "detail": g.detail, "no_qoyod_api_calls": True,
                "settings_untouched": True}

    await _audit(db, attempt_id, "guards_passed", "dispatching",
                 trace_id=pf.get("trace_id"),
                 total_amount=pf.get("total_amount"))

    # rev48 — reserve the rev35 budget slot BEFORE dispatch. The
    # one-shot mints its Qoyod client directly and NEVER passes
    # through pipeline._get_api_client (the only place that called
    # reserve_canary_budget), so the write-time guard
    # (is_order_reserved) correctly refused and DEAD_LETTERed the
    # prod send (canary_budget_violation, 2026-07-07). Idempotent:
    # re-reserving the SAME order consumes one slot.
    from integrations.qoyod.canary_budget import reserve_canary_budget
    _rsv_ok, _rsv_reason = await reserve_canary_budget(
        db, user_id=user_id, order_number=MADA_CANARY_ORDER_NUMBER)
    if not _rsv_ok:
        await _audit(db, attempt_id, "budget_reserve", "refused",
                     reason=_rsv_reason)
        return {"attempt_id": attempt_id, "outcome": "REFUSED",
                "code": "canary_budget_reserve_refused",
                "detail": _rsv_reason, "no_qoyod_api_calls": True,
                "settings_untouched": True}
    await _audit(db, attempt_id, "budget_reserve", "reserved",
                 order_number=MADA_CANARY_ORDER_NUMBER)

    from integrations.qoyod.one_shot_reprocess import (
        APPROVAL_PHRASE_TEMPLATE, CONFIRM_TOKEN_TEMPLATE,
        reprocess_one_order,
    )
    scoped_db = _ScopedDB(db)
    # rev39.5 — the pinned order's row sits at INVOICE_CREATED (a DRY
    # era leftover). one_shot has a purpose-built, audited escape
    # hatch for exactly this: it self-verifies NO real invoice exists
    # on the row and quarantines DRY mappings before the two-hop
    # reset. We opt in ONLY when the row is actually at
    # INVOICE_CREATED — all our guards (dup / amount / pinned budget /
    # single order) already passed above.
    _stage = pf.get("pipeline_stage")
    try:
        result = await reprocess_one_order(
            scoped_db, user_id=user_id,
            order_number=MADA_CANARY_ORDER_NUMBER,
            trace_id=pf.get("trace_id"),
            confirm=CONFIRM_TOKEN_TEMPLATE.format(
                order_number=MADA_CANARY_ORDER_NUMBER),
            approval_phrase=APPROVAL_PHRASE_TEMPLATE.format(
                order_number=MADA_CANARY_ORDER_NUMBER),
            allow_reset_from_partial_invoice_created=(
                _stage == "INVOICE_CREATED"),
            actor=f"mada_canary:{actor}")
    except Exception as exc:
        # rev39.6 — surface one_shot's STRUCTURED refusal fields
        # (selective_send_blocker_code etc.) instead of swallowing
        # them, plus the full pre-dispatch guard snapshot. Display
        # only — no bypass.
        extra = {}
        to_dict = getattr(exc, "to_dict", None)
        if callable(to_dict):
            try:
                extra = dict(to_dict())
            except Exception:
                extra = {}
        budget_snap = await get_canary_budget(db, user_id=user_id)
        await _audit(db, attempt_id, "dispatch", "error",
                     error=f"{type(exc).__name__}: {exc}"[:500],
                     **{k: v for k, v in extra.items()
                        if isinstance(v, (str, int, float, bool))
                        or v is None})
        return {"attempt_id": attempt_id, "outcome": "ERROR",
                "code": type(exc).__name__, "detail": str(exc)[:500],
                "one_shot_refusal": extra or None,
                "guards_snapshot": {
                    "order_number": MADA_CANARY_ORDER_NUMBER,
                    "trace_id": pf.get("trace_id"),
                    "pipeline_stage": pf.get("pipeline_stage"),
                    "total_amount": pf.get("total_amount"),
                    "ready_to_send": pf.get("ready_to_send"),
                    "checks": pf.get("checks"),
                    "allow_reset_from_partial_invoice_created":
                        pf.get("pipeline_stage") == "INVOICE_CREATED",
                    "budget": {
                        "pinned_order_number":
                            budget_snap.get("pinned_order_number"),
                        "used": budget_snap.get("used"),
                        "remaining": budget_snap.get("remaining"),
                        "armed": budget_snap.get("armed"),
                    },
                },
                "settings_untouched": True}

    await _audit(db, attempt_id, "dispatch", "returned",
                 outcome=result.get("outcome"),
                 qoyod_invoice_id=result.get("qoyod_invoice_id"),
                 qoyod_invoice_payment_id=result.get(
                     "qoyod_invoice_payment_id"))
    return {"attempt_id": attempt_id,
            "outcome": result.get("outcome"),
            "order_number": MADA_CANARY_ORDER_NUMBER,
            "trace_id": pf.get("trace_id"),
            "total_amount": pf.get("total_amount"),
            "result": result,
            "settings_untouched": True}
