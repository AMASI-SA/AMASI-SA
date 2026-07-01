"""Selective Live Send Gate — Policy Layer (Phase C.0, 2026-07-01).

Purpose
────────
Provides a **Read-Only diagnostic surface** that answers, for every
order the tenant has, this single question:

    "If Selective Live Send were flipped ON right now, would قيود
     accept this order safely?"

Contract (STRICT, non-negotiable):
    • Fail-Closed by default. Default is BLOCK, not ALLOW.
    • NO Qoyod API calls.
    • NO DB writes.
    • NO buttons / endpoints to trigger send.
    • The global write lock (`production_writes_locked`) is honored
      as a hard blocker. This module NEVER toggles it.
    • The gate flag (`selective_live_send_enabled`) is honored as a
      hard blocker. This module NEVER toggles it.
    • Sync start date (`qoyod_sync_start_date`, default 2026-07-01)
      is honored as a hard blocker — Q2 orders can NEVER be allowed.

The policy function `should_allow_selective_live_send(...)` is a
**pure** decider — safe to call from anywhere. It returns a rich
`SelectiveSendDecision` describing why a row was allowed or blocked.

Every decision is dispatched to `emit_selective_send_decision_log(...)`
so audit trails can be assembled without writing to the DB. The
caller is responsible for persisting the decision if required.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field, asdict
from datetime import date, datetime, timezone
from typing import Any, Optional
from zoneinfo import ZoneInfo

from integrations.qoyod.eligible_orders import (
    QOYOD_SYNC_START_DATE,
    QOYOD_TAX_PERIOD,
    ELIGIBLE_STATUSES,
    _is_eligible_status,
    _is_real_invoice_id,
    _normalize_status,
    _parse_iso_date,
    build_eligible_orders_report,
)


logger = logging.getLogger("selective_send_policy")


# ── Iter-001g/h — Invoice date policy ───────────────────────────────
# Per user directive (2026-07-01): invoice_date / issue_date in the
# Qoyod payload MUST be the SEND date in Asia/Riyadh — NOT
# order.created_at, completed_at, delivered_at, paid_at, or webhook
# received_at.
QOYOD_INVOICE_DATE_SOURCE_DEFAULT: str = "send_date"
QOYOD_SEND_TIMEZONE: str = "Asia/Riyadh"
_RIYADH_TZ = ZoneInfo(QOYOD_SEND_TIMEZONE)


# ── Iter-001h — Default enabled trigger statuses (STRICT) ───────────
# Per user directive (2026-07-01): only `completed` / `تم التنفيذ`
# trigger send by default. `delivered` / `shipping` / `جاري التوصيل`
# / `تم التوصيل` remain visible in Eligible Orders for diagnostics
# but are BLOCKED by the Selective Live Send policy unless the tenant
# opts them in explicitly via `qoyod_enabled_invoice_trigger_statuses`.
QOYOD_ENABLED_TRIGGER_STATUSES_DEFAULT: tuple[str, ...] = (
    "completed", "تم التنفيذ",
)


# ── Iter-001i — Manual Send path ────────────────────────────────────
# Per user directive: when the auto trigger-status check would BLOCK
# a delivered / shipping / Arabic in-transit order, the operator may
# still choose to send it MANUALLY. This does NOT enable auto-send
# for those statuses — it only unlocks a per-order manual path that
# still requires a matching approval phrase.
#
# ONLY these statuses are eligible for the manual bypass; every other
# blocker (Q2 cutoff, DRY IDs, totals mismatch, bank_transfer, etc.)
# still holds even when `manual_send_requested=True`.
_MANUAL_SEND_ELIGIBLE_STATUSES: frozenset[str] = frozenset({
    "delivered", "shipping",
    "تم التوصيل", "جاري التوصيل",
})


def manual_approval_phrase_for(order_number: Any) -> str:
    """Canonical phrase the operator must type to authorise ONE
    manual send. Mirrors Iter-293.4 per-order phrase pattern."""
    return f"Approved manual Qoyod send for order {order_number} only"


# ── Payment method allow-list (verbatim from user directive) ────────
# NOTE: These are the methods that WILL BE allowed once the gate is
# flipped. Today (Phase C.0) every decision is still blocked because
# `selective_live_send_enabled=false` is the master switch above.
_PREPAID = frozenset({
    "mada", "apple_pay", "applepay", "stc_pay", "stcpay",
    "credit_card", "creditcard", "visa", "mastercard",
    "master_card", "amex", "american_express",
})
_BNPL = frozenset({
    "tabby", "tabby_installment", "tabby_installments",
    "tamara", "tamara_installment", "tamara_installments",
    "emkan", "emkan_installment",
})
_COD  = frozenset({"cod", "cash_on_delivery", "cashondelivery"})
_BANK = frozenset({"bank_transfer", "banktransfer"})

_ALLOWED_PAYMENT_METHODS: frozenset[str] = _PREPAID | _BNPL | _COD

# Iter-001g tolerance policy (per user directive):
#   diff == 0.00                   → allow
#   0.00 < |diff| <= 0.01          → allow with warning
#   |diff|          >  0.01        → block
_TOTALS_WARN_TOLERANCE  = 0.001   # <= this → treated as exactly zero
_TOTALS_ALLOW_TOLERANCE = 0.01    # <= this (>0) → allow with warning


# ── Machine-readable blocker codes ──────────────────────────────────
class BlockerCode:
    GATE_DISABLED               = "gate_disabled"
    WRITE_LOCK_ACTIVE           = "write_lock_active"
    BEFORE_SYNC_START_DATE      = "before_sync_start_date"
    MISSING_ORDER_CREATED_AT    = "missing_order_created_at"
    STATUS_NOT_ELIGIBLE         = "status_not_eligible"
    INVOICE_TRIGGER_STATUS_NOT_ENABLED = "invoice_trigger_status_not_enabled"
    MANUAL_APPROVAL_PHRASE_REQUIRED = "manual_approval_phrase_required"
    MANUAL_APPROVAL_PHRASE_MISMATCH = "manual_approval_phrase_mismatch"
    ALREADY_SENT                = "already_sent"
    BANK_TRANSFER_ON_HOLD       = "bank_transfer_on_hold_iter_294"
    PAYMENT_METHOD_NOT_ALLOWED  = "payment_method_not_allowed"
    CUSTOMER_NOT_RESOLVED       = "customer_not_resolved"
    CUSTOMER_DRY_OR_NULL        = "customer_dry_or_null"
    PRODUCT_NOT_RESOLVED        = "product_not_resolved"
    PRODUCT_DRY_OR_NULL         = "product_dry_or_null"
    PRODUCT_MISSING_MAPPING     = "product_missing_mapping"
    DRY_INVOICE_ID_DETECTED     = "dry_invoice_id_detected"
    PREVIEW_ID_DETECTED         = "preview_id_detected"
    TOTALS_MISMATCH_HARD        = "totals_mismatch_hard_diff_gt_0.01"


@dataclass
class SelectiveSendDecision:
    """Immutable outcome of the policy evaluation."""
    order_number:               Optional[str]
    salla_order_id:             Optional[str]
    salla_order_created_at:     Optional[str]
    status:                     Optional[str]
    normalized_status:          Optional[str]
    enabled_trigger_statuses:   list[str]
    payment_method:             Optional[str]
    decision:                   str                 # "allow" | "block"
    blocker_reason:             Optional[str]
    blocker_code:               Optional[str]
    would_send_to_qoyod:        bool
    posting_mode:               Optional[str]
    diff:                       float
    totals_warning:             bool
    dry_ids_detected:           list[str] = field(default_factory=list)
    existing_qoyod_invoice_id:  Optional[Any] = None
    warnings:                   list[str] = field(default_factory=list)
    # Iter-001h — Invoice date contract (always send_date, Asia/Riyadh)
    invoice_date_source:        str = QOYOD_INVOICE_DATE_SOURCE_DEFAULT
    would_use_invoice_date:     Optional[str] = None    # YYYY-MM-DD
    send_timezone:              str = QOYOD_SEND_TIMEZONE
    send_timestamp_riyadh:      Optional[str] = None    # full ISO
    send_date_riyadh:           Optional[str] = None    # YYYY-MM-DD
    # Iter-001i — Manual Send audit trail (echoes inputs; phrase text
    # is deliberately NOT stored — only a boolean).
    manual_send_requested:      bool = False
    manual_approval_phrase_provided: bool = False
    # Snapshot of the gate values active at decision time.
    gates_snapshot:             dict = field(default_factory=dict)


def _looks_like_preview_id(v: Any) -> bool:
    if v is None:
        return False
    return str(v).startswith("PREVIEW:")


def _looks_like_dry_id(v: Any) -> bool:
    if v is None:
        return False
    return str(v).startswith("DRY:")


def _posting_mode_for(pm: str) -> Optional[str]:
    """Iter-001g — canonical mapping consumed by the report."""
    pm = (pm or "").strip().lower()
    if pm in _COD:
        return "credit_invoice_only"
    if pm in _PREPAID or pm in _BNPL:
        return "paid_receipt"
    if pm in _BANK:
        # invoice-only path; payment is deferred to Iter-294.
        return "credit_invoice_only"
    return None


def should_allow_selective_live_send(
    *,
    order: dict,
    settings: dict,
    sync_start_date: Optional[date] = None,
    now_utc: Optional[datetime] = None,
    manual_send_requested: bool = False,
    manual_approval_phrase: Optional[str] = None,
) -> SelectiveSendDecision:
    """PURE policy — no DB, no I/O, no side-effects.

    Args
    ────
    order, settings, sync_start_date, now_utc — see Iter-001g/h docs.
    manual_send_requested : bool
        When True the operator is invoking the Manual Send path. This
        unlocks a NARROW bypass of `invoice_trigger_status_not_enabled`
        ONLY for `delivered / shipping / تم التوصيل / جاري التوصيل`
        AND ONLY when `manual_approval_phrase` matches the canonical
        `Approved manual Qoyod send for order <order_number> only`.
        No other blocker (Q2 cutoff, bank_transfer, DRY/PREVIEW ids,
        totals mismatch, missing customer/product, already sent,
        write lock, master gate) may be bypassed by the manual path.
    manual_approval_phrase : str, optional
        The verbatim phrase entered by the operator. Compared
        strictly to `manual_approval_phrase_for(order_number)`.

    Returns a `SelectiveSendDecision`.
    """
    order_number  = order.get("order_number")
    salla_order_id = order.get("salla_order_id")
    created_at_raw = order.get("salla_order_created_at")
    status        = (order.get("status")
                     or order.get("order_status") or "")
    payment_method = (order.get("payment_method") or "").strip().lower()
    existing_inv  = order.get("existing_qoyod_invoice_id")

    customer_status = order.get("customer_status") or {}
    products_status = order.get("products_status") or {}
    totals_status   = order.get("totals_status") or {}
    diff = float(totals_status.get("diff") or 0.0)

    # Manual-send audit inputs (never store the phrase itself).
    manual_send_requested = bool(manual_send_requested)
    manual_approval_phrase_provided = bool(
        manual_approval_phrase and manual_approval_phrase.strip())

    # ── Send date computation (Asia/Riyadh) ────────────────────
    _now = now_utc or datetime.now(timezone.utc)
    send_ts_riyadh = _now.astimezone(_RIYADH_TZ)
    send_ts_iso    = send_ts_riyadh.isoformat()
    send_date_str  = send_ts_riyadh.date().isoformat()
    invoice_date_source = settings.get(
        "qoyod_invoice_date_source",
        QOYOD_INVOICE_DATE_SOURCE_DEFAULT)

    # ── Enabled trigger statuses (STRICT) ──────────────────────
    enabled_trigger_statuses_raw = settings.get(
        "qoyod_enabled_invoice_trigger_statuses")
    if enabled_trigger_statuses_raw is None:
        enabled_trigger_statuses = list(
            QOYOD_ENABLED_TRIGGER_STATUSES_DEFAULT)
    else:
        # Accept list / tuple / frozenset — always normalise contents.
        enabled_trigger_statuses = [
            str(s) for s in enabled_trigger_statuses_raw if s]
    _enabled_normalized = {
        _normalize_status(s) for s in enabled_trigger_statuses}
    normalized_status = _normalize_status(status)

    # Gate snapshot (Fail-Closed defaults).
    gate_enabled    = bool(settings.get("selective_live_send_enabled",
                                        False))
    write_locked    = bool(settings.get("production_writes_locked",
                                        True))
    cutoff_str      = settings.get("qoyod_sync_start_date",
                                   QOYOD_SYNC_START_DATE)
    cutoff = sync_start_date or _parse_iso_date(cutoff_str) or \
        _parse_iso_date(QOYOD_SYNC_START_DATE)

    posting_mode = _posting_mode_for(payment_method)

    dry_ids: list[str] = []
    if _looks_like_dry_id(existing_inv):
        dry_ids.append(f"invoice_id={existing_inv}")
    if _looks_like_dry_id(customer_status.get("qoyod_id")):
        dry_ids.append(f"customer_id={customer_status.get('qoyod_id')}")
    if _looks_like_preview_id(existing_inv):
        dry_ids.append(f"invoice_id={existing_inv}")

    _gates_snap = {
        "selective_live_send_enabled":   gate_enabled,
        "production_writes_locked":      write_locked,
        "qoyod_sync_start_date":         cutoff.isoformat()
                                          if cutoff else None,
        "qoyod_tax_period":              settings.get(
            "qoyod_tax_period", QOYOD_TAX_PERIOD),
        "bank_transfer_routing_enabled": bool(
            settings.get("bank_transfer_routing_enabled", False)),
        "qoyod_invoice_date_source":     invoice_date_source,
        "qoyod_enabled_invoice_trigger_statuses":
            list(enabled_trigger_statuses),
    }

    def _block(code: str, reason: str) -> SelectiveSendDecision:
        return SelectiveSendDecision(
            order_number=order_number,
            salla_order_id=salla_order_id,
            salla_order_created_at=(
                created_at_raw.isoformat()
                if isinstance(created_at_raw, date) else created_at_raw),
            status=status or None,
            normalized_status=normalized_status or None,
            enabled_trigger_statuses=list(enabled_trigger_statuses),
            payment_method=payment_method or None,
            decision="block",
            blocker_reason=reason,
            blocker_code=code,
            would_send_to_qoyod=False,
            posting_mode=posting_mode,
            diff=round(diff, 4),
            totals_warning=False,
            dry_ids_detected=dry_ids,
            existing_qoyod_invoice_id=existing_inv,
            warnings=[],
            invoice_date_source=invoice_date_source,
            would_use_invoice_date=send_date_str,
            send_timezone=QOYOD_SEND_TIMEZONE,
            send_timestamp_riyadh=send_ts_iso,
            send_date_riyadh=send_date_str,
            manual_send_requested=manual_send_requested,
            manual_approval_phrase_provided=
                manual_approval_phrase_provided,
            gates_snapshot=_gates_snap,
        )

    # ── Check 1: Master gate (Fail-Closed) ──────────────────────
    if not gate_enabled:
        return _block(
            BlockerCode.GATE_DISABLED,
            "selective_live_send_enabled=false — master gate closed. "
            "لا إرسال حتى يفعّلها المشغّل صراحةً.",
        )

    # ── Check 2: Write lock (Fail-Closed, belt & suspenders) ────
    if write_locked:
        return _block(
            BlockerCode.WRITE_LOCK_ACTIVE,
            "production_writes_locked=true — القفل العام مفعّل.",
        )

    # ── Check 3: PREVIEW / DRY sentinel invoice IDs ─────────────
    if _looks_like_preview_id(existing_inv):
        return _block(
            BlockerCode.PREVIEW_ID_DETECTED,
            f"existing_qoyod_invoice_id يحمل بادئة PREVIEW: "
            f"({existing_inv})",
        )
    if _looks_like_dry_id(existing_inv):
        return _block(
            BlockerCode.DRY_INVOICE_ID_DETECTED,
            f"existing_qoyod_invoice_id يحمل بادئة DRY: "
            f"({existing_inv})",
        )

    # ── Check 4: Sync start date (tax period cutoff) ────────────
    created_at = (created_at_raw if isinstance(created_at_raw, date)
                  else _parse_iso_date(created_at_raw))
    if created_at is None:
        return _block(
            BlockerCode.MISSING_ORDER_CREATED_AT,
            "لا يوجد تاريخ إنشاء للطلب — لا يمكن تحديد الربع الضريبي.",
        )
    if cutoff is not None and created_at < cutoff:
        return _block(
            BlockerCode.BEFORE_SYNC_START_DATE,
            f"created_at={created_at.isoformat()} قبل "
            f"{cutoff.isoformat()} — الطلب من ربع ضريبي سابق.",
        )

    # ── Check 5: Broad status eligibility (defensive) ───────────
    # Guards against completely unrelated statuses (waiting, pending,
    # cancelled…). ELIGIBLE_STATUSES = broad "conceptually invoiceable"
    # superset; the trigger-status check below is stricter.
    if not _is_eligible_status(status):
        return _block(
            BlockerCode.STATUS_NOT_ELIGIBLE,
            f"status='{status}' ليست ضمن قائمة الحالات المؤهلة "
            f"({sorted(ELIGIBLE_STATUSES)}).",
        )

    # ── Check 6: Iter-001h — Enabled trigger status (STRICT) ────
    # Even if status is broadly eligible (e.g. delivered), the tenant
    # must have opted it in via `qoyod_enabled_invoice_trigger_statuses`
    # before Selective Send will allow it. Default enables only
    # `completed` / `تم التنفيذ` — everything else is blocked UNLESS
    # the operator triggers the Manual Send path (Iter-001i).
    if normalized_status not in _enabled_normalized:
        # Iter-001i — Manual Send narrow bypass.
        _manual_eligible_normalized = frozenset(
            _normalize_status(s) for s in _MANUAL_SEND_ELIGIBLE_STATUSES)
        can_use_manual_path = (
            manual_send_requested
            and normalized_status in _manual_eligible_normalized
        )
        if not can_use_manual_path:
            return _block(
                BlockerCode.INVOICE_TRIGGER_STATUS_NOT_ENABLED,
                f"حالة الطلب '{status}' ليست ضمن الحالات المفعّلة "
                f"للإرسال ({enabled_trigger_statuses}). "
                "الإرسال الافتراضي مقتصر على completed / تم التنفيذ.",
            )
        # Manual path activated — verify the operator's phrase.
        expected_phrase = manual_approval_phrase_for(order_number)
        if not manual_approval_phrase:
            return _block(
                BlockerCode.MANUAL_APPROVAL_PHRASE_REQUIRED,
                "الإرسال اليدوي يتطلب كتابة عبارة التأكيد الصريحة: "
                f"'{expected_phrase}'.",
            )
        if manual_approval_phrase.strip() != expected_phrase:
            return _block(
                BlockerCode.MANUAL_APPROVAL_PHRASE_MISMATCH,
                f"عبارة التأكيد لا تطابق المطلوب. "
                f"المتوقع بالضبط: '{expected_phrase}'.",
            )
        # Phrase matches — proceed to all remaining checks. Manual
        # send does NOT bypass anything below this point.

    # ── Check 7: Already sent (real قيود invoice exists) ────────
    if _is_real_invoice_id(existing_inv):
        return _block(
            BlockerCode.ALREADY_SENT,
            f"الطلب مُرسَل مسبقاً — existing_qoyod_invoice_id="
            f"{existing_inv}",
        )

    # ── Check 8: Bank transfer — HOLD until Iter-294 ────────────
    if payment_method in _BANK:
        return _block(
            BlockerCode.BANK_TRANSFER_ON_HOLD,
            "التحويل البنكي مؤجَّل حتى إكمال Iter-294 "
            "(bank_transfer_routing_enabled=false).",
        )

    # ── Check 9: Payment method allow-list ──────────────────────
    if not payment_method:
        return _block(
            BlockerCode.PAYMENT_METHOD_NOT_ALLOWED,
            "طريقة الدفع فارغة على الطلب.",
        )
    if payment_method not in _ALLOWED_PAYMENT_METHODS:
        return _block(
            BlockerCode.PAYMENT_METHOD_NOT_ALLOWED,
            f"طريقة دفع غير مسموحة: '{payment_method}'. "
            f"المسموح فقط: mada / apple_pay / credit_card / visa / "
            f"mastercard / stc_pay / tabby / tamara / emkan / cod.",
        )

    # ── Check 10: Customer resolved & non-DRY ───────────────────
    if not customer_status.get("resolved"):
        return _block(
            BlockerCode.CUSTOMER_NOT_RESOLVED,
            f"العميل غير مربوط في قيود. "
            f"({customer_status.get('reason') or 'لم يُذكر السبب'})",
        )
    qcid = customer_status.get("qoyod_id")
    if qcid is None or _looks_like_dry_id(qcid) or \
            _looks_like_preview_id(qcid):
        return _block(
            BlockerCode.CUSTOMER_DRY_OR_NULL,
            f"customer.qoyod_id يحمل قيمة DRY/PREVIEW أو null: {qcid}",
        )

    # ── Check 11: Products resolved & non-DRY & no missing ──────
    missing_skus = products_status.get("missing") or []
    if missing_skus:
        return _block(
            BlockerCode.PRODUCT_MISSING_MAPPING,
            f"منتجات بلا ربط: {missing_skus[:5]}"
            + ("…" if len(missing_skus) > 5 else ""),
        )
    if products_status.get("dry_run_only", 0) > 0:
        return _block(
            BlockerCode.PRODUCT_DRY_OR_NULL,
            f"منتج/منتجات مربوطة بمعرفات DRY/PREVIEW أو null "
            f"(count={products_status.get('dry_run_only')}).",
        )
    if not products_status.get("resolved"):
        return _block(
            BlockerCode.PRODUCT_NOT_RESOLVED,
            "المنتجات غير مربوطة بالكامل في قيود.",
        )

    # ── Check 12: Totals — hard block if |diff| > 0.01 ──────────
    abs_diff = abs(diff)
    totals_warning = False
    warnings: list[str] = []
    if abs_diff > _TOTALS_ALLOW_TOLERANCE:
        return _block(
            BlockerCode.TOTALS_MISMATCH_HARD,
            f"إجمالي غير متطابق: diff={diff:.4f} > "
            f"{_TOTALS_ALLOW_TOLERANCE}.",
        )
    if abs_diff > _TOTALS_WARN_TOLERANCE:
        totals_warning = True
        warnings.append(
            f"totals_rounding_warning: diff={diff:.4f} "
            f"(≤ {_TOTALS_ALLOW_TOLERANCE} — مقبول كتقريب)")

    # ── All checks passed → ALLOW ───────────────────────────────
    return SelectiveSendDecision(
        order_number=order_number,
        salla_order_id=salla_order_id,
        salla_order_created_at=(
            created_at.isoformat() if isinstance(created_at, date)
            else created_at_raw),
        status=status,
        normalized_status=normalized_status,
        enabled_trigger_statuses=list(enabled_trigger_statuses),
        payment_method=payment_method,
        decision="allow",
        blocker_reason=None,
        blocker_code=None,
        would_send_to_qoyod=True,
        posting_mode=posting_mode,
        diff=round(diff, 4),
        totals_warning=totals_warning,
        dry_ids_detected=dry_ids,
        existing_qoyod_invoice_id=existing_inv,
        warnings=warnings,
        invoice_date_source=invoice_date_source,
        would_use_invoice_date=send_date_str,
        send_timezone=QOYOD_SEND_TIMEZONE,
        send_timestamp_riyadh=send_ts_iso,
        send_date_riyadh=send_date_str,
        manual_send_requested=manual_send_requested,
        manual_approval_phrase_provided=manual_approval_phrase_provided,
        gates_snapshot=_gates_snap,
    )


def emit_selective_send_decision_log(
        decision: SelectiveSendDecision) -> None:
    """Stdout audit line. Never writes to DB."""
    logger.info(
        "SELECTIVE_SEND_DECISION order=%s decision=%s code=%s "
        "posting_mode=%s pm=%s created_at=%s gates=%s",
        decision.order_number, decision.decision,
        decision.blocker_code or "-", decision.posting_mode or "-",
        decision.payment_method or "-",
        decision.salla_order_created_at or "-",
        decision.gates_snapshot,
    )


def _compute_readiness_blockers(
    *,
    order: dict,
    settings: dict,
    sync_start_date: Optional[date] = None,
) -> list[str]:
    """Iter-001k+ — Read-only diagnostic inspector.

    Returns the COMPLETE list of substantive blockers that would
    still refuse this order EVEN IF the master gate and write lock
    were flipped open. Unlike `should_allow_selective_live_send`
    which short-circuits at the first blocker, this walks EVERY
    substantive check independently so the operator can see the
    full remediation list.

    Deliberately excludes `gate_disabled` and `write_lock_active` —
    those are the two blockers the operator toggles explicitly.

    Pure. No DB. No I/O. No side effects.
    """
    blockers: list[str] = []

    order_number       = order.get("order_number")
    created_at_raw     = order.get("salla_order_created_at")
    status             = (order.get("status")
                          or order.get("order_status") or "")
    payment_method     = (order.get("payment_method") or "").strip().lower()
    existing_inv       = order.get("existing_qoyod_invoice_id")
    customer_status    = order.get("customer_status") or {}
    products_status    = order.get("products_status") or {}
    totals_status      = order.get("totals_status") or {}
    diff               = float(totals_status.get("diff") or 0.0)

    cutoff_str = settings.get("qoyod_sync_start_date",
                              QOYOD_SYNC_START_DATE)
    cutoff = sync_start_date or _parse_iso_date(cutoff_str) or \
        _parse_iso_date(QOYOD_SYNC_START_DATE)

    enabled_trigger_statuses_raw = settings.get(
        "qoyod_enabled_invoice_trigger_statuses")
    if enabled_trigger_statuses_raw is None:
        enabled_trigger_statuses = list(
            QOYOD_ENABLED_TRIGGER_STATUSES_DEFAULT)
    else:
        enabled_trigger_statuses = [
            str(s) for s in enabled_trigger_statuses_raw if s]
    _enabled_normalized = {
        _normalize_status(s) for s in enabled_trigger_statuses}

    # ── Missing / Q2 cutoff on order_created_at ─────────────────
    created_at = (created_at_raw if isinstance(created_at_raw, date)
                  else _parse_iso_date(created_at_raw))
    if created_at is None:
        blockers.append("missing_order_created_at")
    elif cutoff is not None and created_at < cutoff:
        blockers.append("q2_cutoff")

    # ── Existing invoice sentinel + already-sent ────────────────
    if _looks_like_preview_id(existing_inv):
        blockers.append("preview_id_detected")
    if _looks_like_dry_id(existing_inv):
        blockers.append("dry_invoice_id")
    if _is_real_invoice_id(existing_inv):
        blockers.append("existing_real_qoyod_invoice_id")

    # ── Trigger status enablement ───────────────────────────────
    normalized_status = _normalize_status(status)
    if normalized_status not in _enabled_normalized:
        blockers.append("invoice_trigger_status_not_enabled")

    # ── Bank transfer hold (Iter-294) ───────────────────────────
    if payment_method in _BANK:
        blockers.append("bank_transfer_on_hold_iter_294")

    # ── Customer resolution / DRY / null ────────────────────────
    qcid = customer_status.get("qoyod_id")
    if qcid is None:
        blockers.append("null_contact_id")
    elif _looks_like_dry_id(qcid):
        blockers.append("dry_customer_id")
    elif _looks_like_preview_id(qcid):
        blockers.append("preview_id_detected")

    # ── Product resolution / DRY / null ─────────────────────────
    missing_skus = products_status.get("missing") or []
    if missing_skus:
        blockers.append("null_product_id")
    if int(products_status.get("dry_run_only") or 0) > 0:
        blockers.append("dry_product_id")

    # ── Totals mismatch > 0.01 ──────────────────────────────────
    if abs(diff) > _TOTALS_ALLOW_TOLERANCE:
        blockers.append("totals_mismatch_gt_0_01")

    # Preserve insertion order and dedupe (preview_id may appear
    # via existing_inv AND customer_status.qoyod_id).
    seen: set[str] = set()
    unique_blockers: list[str] = []
    for b in blockers:
        if b not in seen:
            seen.add(b)
            unique_blockers.append(b)
    return unique_blockers


def _aggregate_readiness_blockers(
        decisions: list[dict]) -> dict[str, int]:
    """Count readiness_blocker occurrences across all decision rows.
    Read-only. Pure."""
    counts: dict[str, int] = {}
    for row in decisions:
        for code in row.get("readiness_blockers") or []:
            counts[code] = counts.get(code, 0) + 1
    return counts


async def build_selective_send_policy_report(
    db,
    *,
    user_id: str,
    since_days: int = 90,
    limit: int = 200,
) -> dict:
    """Read-Only diagnostic report.

    Assembles one `SelectiveSendDecision` per order the tenant has and
    returns a JSON-serialisable dict. Wraps `build_eligible_orders_report`
    to reuse the enrichment (customer/products/totals) then applies the
    Selective Send policy.

    Contract:
        • Zero writes.
        • Zero Qoyod API calls.
        • Report can be safely run in production while gates are
          closed — every decision will be `block:gate_disabled`.
    """
    limit = max(1, min(int(limit), 500))
    since_days = max(1, min(int(since_days), 365))

    # 1. Read the underlying eligible-orders enrichment (already reads
    #    customer/product mapping + totals). We ask for BOTH sent and
    #    unsent rows so the policy can classify `already_sent` too.
    eo = await build_eligible_orders_report(
        db,
        user_id=user_id,
        since_days=since_days,
        limit=limit,
        show_already_sent=True,
    )

    # 2. Load settings snapshot with Fail-Closed defaults.
    raw_settings = await db.qoyod_settings.find_one(
        {"user_id": user_id}, {"_id": 0}) or {}
    settings = {
        "selective_live_send_enabled": bool(
            raw_settings.get("selective_live_send_enabled", False)),
        "production_writes_locked":    bool(
            raw_settings.get("production_writes_locked", True)),
        "qoyod_sync_start_date":       raw_settings.get(
            "qoyod_sync_start_date", QOYOD_SYNC_START_DATE),
        "qoyod_tax_period":            raw_settings.get(
            "qoyod_tax_period", QOYOD_TAX_PERIOD),
        "bank_transfer_routing_enabled": bool(
            raw_settings.get("bank_transfer_routing_enabled", False)),
        "qoyod_invoice_date_source":   raw_settings.get(
            "qoyod_invoice_date_source",
            QOYOD_INVOICE_DATE_SOURCE_DEFAULT),
        "qoyod_enabled_invoice_trigger_statuses":
            list(raw_settings.get(
                "qoyod_enabled_invoice_trigger_statuses",
                QOYOD_ENABLED_TRIGGER_STATUSES_DEFAULT)),
    }

    # 3. Apply the policy to every item — compute BOTH the auto path
    #    AND the manual-send availability so the UI can decide whether
    #    to show the "Manual Send" button.
    decisions: list[dict] = []
    counts: dict[str, int] = {"allow": 0, "block": 0}
    blocker_code_counts: dict[str, int] = {}
    payment_method_breakdown: dict[str, int] = {}
    manual_send_available_count = 0

    for item in (eo.get("items") or []):
        # Auto-send decision (no manual bypass).
        auto = should_allow_selective_live_send(
            order=item, settings=settings)
        emit_selective_send_decision_log(auto)
        counts[auto.decision] = counts.get(auto.decision, 0) + 1
        if auto.blocker_code:
            blocker_code_counts[auto.blocker_code] = \
                blocker_code_counts.get(auto.blocker_code, 0) + 1
        pm = auto.payment_method or "(none)"
        payment_method_breakdown[pm] = \
            payment_method_breakdown.get(pm, 0) + 1

        # Manual-send availability probe — feed the canonical phrase
        # to see if THE ONLY remaining blocker was the trigger status.
        # If this returns `allow`, the operator can perform manual
        # send after typing the phrase.
        expected_phrase = manual_approval_phrase_for(
            item.get("order_number"))
        manual_probe = should_allow_selective_live_send(
            order=item, settings=settings,
            manual_send_requested=True,
            manual_approval_phrase=expected_phrase)
        manual_send_available = (manual_probe.decision == "allow")
        if manual_send_available:
            manual_send_available_count += 1

        row = asdict(auto)
        row["auto_send_available"] = (auto.decision == "allow")
        row["manual_send_available"] = manual_send_available
        row["manual_send_confirmation_phrase"] = expected_phrase
        row["manual_send_blocker_code"] = manual_probe.blocker_code
        row["manual_send_blocker_reason"] = manual_probe.blocker_reason
        # ── Iter-001k+ — Read-only readiness diagnostic ─────────
        # What blockers would still refuse this order even if the
        # master gate + write lock were flipped open? Independent
        # of the effective blocker_code above (which is nearly
        # always `gate_disabled` while the gate is closed).
        readiness_blockers = _compute_readiness_blockers(
            order=item, settings=settings)
        row["readiness_blockers"] = readiness_blockers
        row["readiness_ready_if_gate_opened"] = (
            len(readiness_blockers) == 0)
        decisions.append(row)

    # 4. Assemble the report.
    return {
        "generated_at":              datetime.now(timezone.utc)
                                              .isoformat(),
        "since_days":                since_days,
        "source_mode":               eo.get("source_mode"),
        "gates_snapshot":            {
            "selective_live_send_enabled":
                settings["selective_live_send_enabled"],
            "production_writes_locked":
                settings["production_writes_locked"],
            "qoyod_sync_start_date":
                settings["qoyod_sync_start_date"],
            "qoyod_tax_period":
                settings["qoyod_tax_period"],
            "bank_transfer_routing_enabled":
                settings["bank_transfer_routing_enabled"],
            "qoyod_invoice_date_source":
                settings["qoyod_invoice_date_source"],
            "qoyod_enabled_invoice_trigger_statuses":
                settings["qoyod_enabled_invoice_trigger_statuses"],
        },
        "eligible_orders_snapshot":  {
            "total_scanned":       eo.get("total_scanned"),
            "total_classified":    eo.get("total_classified"),
            "excluded_status_count": eo.get("excluded_status_count"),
            "excluded_before_sync_start_date_count":
                eo.get("excluded_before_sync_start_date_count"),
            "excluded_missing_order_created_at_count":
                eo.get("excluded_missing_order_created_at_count"),
        },
        "counts":                    counts,
        "blocker_code_counts":       blocker_code_counts,
        "payment_method_breakdown":  payment_method_breakdown,
        "total_decisions":           len(decisions),
        "would_send_to_qoyod_count": sum(1 for d in decisions
                                         if d["would_send_to_qoyod"]),
        "manual_send_available_count": manual_send_available_count,
        # ── Iter-001k+ — Read-only readiness rollup ─────────────
        "readiness_ready_if_gate_opened_count": sum(
            1 for d in decisions if d["readiness_ready_if_gate_opened"]),
        "readiness_blocker_code_counts": _aggregate_readiness_blockers(
            decisions),
        "decisions":                 decisions,
        "notes": [
            "READ-ONLY POLICY REPORT — لا استدعاء لـ Qoyod، "
            "لا كتابة على DB، لا إرسال، لا approve.",
            f"Master gate `selective_live_send_enabled` = "
            f"{settings['selective_live_send_enabled']} "
            "(default fail-closed = false).",
            f"Global write lock `production_writes_locked` = "
            f"{settings['production_writes_locked']} "
            "(default fail-closed = true).",
            f"Sync cutoff = {settings['qoyod_sync_start_date']} "
            f"({settings['qoyod_tax_period']}) — أي طلب قبل هذا "
            "التاريخ سيُرفض حتى لو فُتحت البوابة.",
            f"invoice_date_source = "
            f"{settings['qoyod_invoice_date_source']} — تاريخ "
            "الفاتورة في قيود = تاريخ الإرسال الفعلي (Asia/Riyadh)، "
            "وليس تاريخ إنشاء الطلب أو completed_at أو delivered_at.",
            f"Enabled trigger statuses (default STRICT) = "
            f"{settings['qoyod_enabled_invoice_trigger_statuses']}. "
            "delivered / shipping / تم التوصيل / جاري التوصيل تُحظر "
            "افتراضياً بـ blocker_code=invoice_trigger_status_not_enabled.",
            "Manual Send (Iter-001i): يمكن إرسال delivered / shipping "
            "يدوياً فقط بعد كتابة عبارة التأكيد بالضبط: "
            "'Approved manual Qoyod send for order <N> only'. "
            "المسار اليدوي لا يتجاوز أي blocker آخر.",
            "bank_transfer معلَّق حتى Iter-294 "
            "(bank_transfer_routing_enabled=false).",
            "المسموح لاحقاً: mada / apple_pay / credit_card / visa / "
            "mastercard / stc_pay / tabby / tamara / emkan / cod فقط.",
            "readiness_blockers (Iter-001k+): قائمة تشخيصية للقيود "
            "الجوهرية التي ستمنع كل طلب حتى لو فُتحت البوابة. لا تؤثر "
            "على blocker_code الحالي. مفيدة لتحديد ما يحتاج إصلاح قبل "
            "الفتح المحدود لاحقاً.",
        ],
    }
