"""Selective Live Send Gate — Iter-293.5.

Purpose
───────
The Global Write Lock (`production_writes_locked=true`) blocks every
POST to قيود by default. Per-order approval (Iter-293.4) lifts the
lock for a SINGLE row when a human operator supplies a matching
`approval_phrase`. This module provides the AUTOMATED counterpart:
an in-code gate that decides — for one specific inbox row — whether
the pipeline is allowed to auto-mint a scoped bypass, or whether the
row must be parked in a HOLD state for human triage.

Contract
────────
The gate NEVER opens the global lock. When it grants a bypass it
issues a **scoped credential** that constrains the bypass to:
    • one order_number
    • one trace_id
    • one payload_hash (SHA-256 of the invoice payload)
    • an explicit allowed_actions list (`["create_invoice"]` for COD,
      `["create_invoice", "create_invoice_payment"]` for pre-paid)
Any write attempt outside this scope MUST be refused.

Feature flag
────────────
`qoyod_settings.selective_live_send_enabled` (default `False`).
When False the gate is DEFENSIVE: it still evaluates decisions so the
`GET /admin/qoyod/pending-orders` endpoint can show categorised rows,
but it will refuse to grant any ALLOWED decision. This lets us ship
the gate + read-only surface to production behind a dark flag and
flip it on later after Preview review.

Payment method allow-list (revised per user directive 2026-07-01
after Iter-293.5 first draft)
────────────────────────────────────────────────────────────────
The gate now separates INVOICE creation (needed for every completed
order → ZATCA) from PAYMENT POSTING (invoice_payment / receipt —
different rules per method).

    Level 1 — invoice creation (always attempted when data is ready):
        mada, apple_pay, stc_pay, credit_card family, cod,
        bank_transfer  ← invoice is created, payment deferred.

    Level 2 — payment posting:
        Prepaid family        → invoice + invoice_payment (both).
        cod                   → invoice ONLY (credit_invoice_only).
        bank_transfer         → invoice now, invoice_payment DEFERRED
                                to Iter-294 when the receiving bank
                                mapping is known. Row lands at
                                BANK_TRANSFER_PAYMENT_ROUTING_PENDING.

Anything not on the allow-list (tamara/tabby/BNPL/unknown) still
lands in HOLD_UNSUPPORTED_PAYMENT_METHOD — refusing to auto-send.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


# ─── Payment method canonicalisation ────────────────────────────────
PREPAID_ALLOWED: frozenset[str] = frozenset({
    "mada", "apple_pay", "applepay",
    "stc_pay", "stcpay",
    "credit_card", "creditcard", "cc",
    "visa", "mastercard", "master_card",
    "american_express", "americanexpress", "amex",
})
COD_ALLOWED: frozenset[str] = frozenset({
    "cod", "cash_on_delivery", "cashondelivery",
})
BANK_TRANSFER_METHODS: frozenset[str] = frozenset({
    "bank_transfer", "banktransfer",
})
# Iter-293.5-rev3 — BNPL providers (Buy Now Pay Later) are prepaid
# from the merchant's perspective: the provider (Tabby / Tamara /
# Emkan) settles the full amount to the merchant, then collects
# instalments from the shopper. Accounting-wise the flow is
# identical to any prepaid gateway: create the invoice + book a
# receipt against the BNPL provider's Qoyod account (which the
# operator maps in Settings, one per BNPL variant if desired).
#
# Alias variants like `tabby_installment`, `tamara_installments`,
# `emkan_installment` collapse to their base family via
# `payment_methods.PAYMENT_METHOD_ALIASES` — we accept both here so
# a row whose canonical_payload still carries the un-aliased variant
# is not misrouted to `unsupported_method`.
BNPL_ALLOWED: frozenset[str] = frozenset({
    "tabby", "tabby_installment", "tabby_installments",
    "tabby_pay", "tabby_payment",
    "tamara", "tamara_installment", "tamara_installments",
    "tamara_pay", "tamara_payment",
    "emkan", "emkan_installment", "emkan_installments",
})


def canonicalise_payment_method(raw: Optional[str]) -> str:
    """Normalise a Salla payment_method label to a lower-case token
    we can compare against the allowlist. Empty / None → "" (which
    fails the allowlist check and lands in HOLD_UNSUPPORTED_PAYMENT_METHOD).
    """
    if not raw:
        return ""
    return str(raw).strip().lower().replace("-", "_")


# ─── Decision types ─────────────────────────────────────────────────
class GateOutcome(str, Enum):
    ALLOWED     = "ALLOWED"
    BLOCKED     = "BLOCKED"       # resolvable — appears in pending-orders
    QUARANTINED = "QUARANTINED"   # terminal — hidden from pending-orders


# Guard identifiers — stable strings so audit / UI can key on them.
class GuardCode(str, Enum):
    FLAG_DISABLED             = "flag_disabled"
    ORDER_STATUS_NOT_ELIGIBLE = "order_status_not_eligible"
    ORDER_CANCELLED           = "order_cancelled"
    ORDER_REFUNDED            = "order_refunded"
    ORDER_DELETED             = "order_deleted"
    ORDER_SUPERSEDED          = "order_superseded_by_newer_event"
    STALE_TRACE               = "stale_trace_not_current_order_state"
    BANK_TRANSFER_HELD        = "bank_transfer_held_pending_routing"
    UNSUPPORTED_PAYMENT_METHOD = "unsupported_payment_method"
    UNRESOLVED_DEPENDENCY     = "unresolved_qoyod_dependency"
    DRY_OR_PREVIEW_LEAK       = "dry_or_preview_id_in_payload"
    NULL_ID_IN_PAYLOAD        = "null_id_in_payload"
    EXISTING_INVOICE          = "existing_qoyod_invoice"
    COD_POSTING_MODE_MISMATCH = "cod_posting_mode_mismatch"
    TOTALS_BLOCKER            = "totals_blocker_over_0_01"
    GATE_FLAG_OFF             = "selective_live_send_flag_disabled"


# Category → determines which pending-orders tab the row lands in.
class PendingCategory(str, Enum):
    READY_TO_SEND          = "ready_to_send"
    NEEDS_MAPPING          = "needs_mapping"
    BANK_TRANSFER_HOLD     = "bank_transfer_hold"
    COD                    = "cod"
    STALE_OR_CANCELLED     = "stale_or_cancelled"
    TOTAL_ROUNDING_REVIEW  = "total_rounding_review"
    UNSUPPORTED_METHOD     = "unsupported_method"


# Terminal QUARANTINE reasons — do not surface in pending list.
QUARANTINE_CODES: frozenset[str] = frozenset({
    GuardCode.ORDER_CANCELLED.value,
    GuardCode.ORDER_REFUNDED.value,
    GuardCode.ORDER_DELETED.value,
    GuardCode.ORDER_SUPERSEDED.value,
    GuardCode.STALE_TRACE.value,
})


@dataclass(frozen=True)
class ScopedBypass:
    """Materialises an auto-approval that DOES NOT open the global
    lock. Constrains the bypass to one specific write."""
    order_number:    str
    trace_id:        str
    payload_hash:    str
    allowed_actions: tuple[str, ...]
    approval_phrase: str                    # AUTO-GATE: Approved to send order N only
    approval_type:   str = "selective_live_gate_auto"
    approval_source: str = "live_send_gate"

    def matches(self, action: str, payload: Any) -> bool:
        """Runtime check used by the pipeline to authorise a specific
        Qoyod write against the bypass. Refuses any action / payload
        that wasn't part of the original bypass grant."""
        if action not in self.allowed_actions:
            return False
        return _payload_hash(payload) == self.payload_hash


@dataclass
class GateDecision:
    outcome:            GateOutcome
    category:           Optional[PendingCategory]      # None for QUARANTINE
    hold_stage:         Optional[str] = None           # pipeline stage token
    guards_passed:      list[str] = field(default_factory=list)
    guards_failed:      list[str] = field(default_factory=list)
    reason_code:        Optional[str] = None            # top-level failure token
    reason_message:     Optional[str] = None
    canonical_pm:       Optional[str] = None
    payment_method_raw: Optional[str] = None
    posting_mode:       Optional[str] = None
    bypass:             Optional[ScopedBypass] = None   # only when ALLOWED
    # Iter-293.5-rev2 — Bank transfer flow:
    # invoice IS created (for ZATCA), but the payment posting is
    # deferred until Iter-294 provides the receiving-bank mapping.
    # When True the pipeline MUST:
    #   • run create_invoice through the ScopedBypass, then
    #   • transition the row to BANK_TRANSFER_PAYMENT_ROUTING_PENDING
    #     (never call create_invoice_payment / create_receipt).
    defers_payment_posting: bool = False
    # Post-invoice hold to land at when defers_payment_posting=True.
    # Kept explicit so the pipeline doesn't have to reverse-engineer
    # the reason from `canonical_pm`.
    post_invoice_hold_stage: Optional[str] = None

    def to_json(self) -> dict:
        return {
            "outcome":            self.outcome.value,
            "category":           (self.category.value
                                   if self.category else None),
            "hold_stage":         self.hold_stage,
            "reason_code":        self.reason_code,
            "reason_message":     self.reason_message,
            "guards_passed":      list(self.guards_passed),
            "guards_failed":      list(self.guards_failed),
            "canonical_pm":       self.canonical_pm,
            "payment_method_raw": self.payment_method_raw,
            "posting_mode":       self.posting_mode,
            "bypass_granted":     self.bypass is not None,
            "defers_payment_posting":  self.defers_payment_posting,
            "post_invoice_hold_stage": self.post_invoice_hold_stage,
            # Never leak the full bypass over the wire — audit only.
            "bypass_hash":        (self.bypass.payload_hash[:12]
                                   if self.bypass else None),
        }


# ─── Helpers ────────────────────────────────────────────────────────
def _payload_hash(payload: Any) -> str:
    """Deterministic SHA-256 hash of a JSON-serialisable payload.
    Used to scope the bypass to ONE specific invoice payload."""
    try:
        canonical = json.dumps(
            payload, sort_keys=True, separators=(",", ":"),
            default=str, ensure_ascii=False)
    except Exception:
        canonical = repr(payload)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _extract_order_status(row: dict) -> str:
    """Robustly pull the order's Salla status regardless of adapter
    version. Returns the lower-case native token."""
    canonical = row.get("canonical_payload") or {}
    val = (canonical.get("order_status_native")
           or canonical.get("order_status")
           or row.get("order_status_native"))
    return (str(val).strip().lower() if val else "")


def _is_cancelled_family(status: str) -> Optional[GuardCode]:
    """Map a Salla status to a specific quarantine reason. Returns
    None when the status is fine (or unknown)."""
    if status in {"canceled", "cancelled"}:
        return GuardCode.ORDER_CANCELLED
    if status in {"refunded", "partial_refund", "partially_refunded"}:
        return GuardCode.ORDER_REFUNDED
    if status in {"deleted", "trash", "archived"}:
        return GuardCode.ORDER_DELETED
    return None


# ─── The Gate ───────────────────────────────────────────────────────
def evaluate(
    *,
    row: dict,
    settings: dict,
    dependency_status: Optional[dict] = None,
    invoice_payload: Optional[dict] = None,
    posting_mode: Optional[str] = None,
    is_current_trace: bool = True,
) -> GateDecision:
    """Decide whether a single inbox row is eligible for automated
    live send.

    Inputs
    ──────
      row               — inbox document (must include canonical_payload,
                          trace_id, pipeline_stage, and any qoyod_* ids).
      settings          — merchant qoyod_settings dict.
      dependency_status — output of `preview_reprocess_one_order` (or
                          equivalent). Must carry `sendable` + optional
                          `request_body_unresolved` + will_create_* fields.
      invoice_payload   — the built `POST /invoices` body. Only used to
                          compute the payload_hash for the bypass scope.
      posting_mode      — pre-resolved posting mode (see payment_methods.py).
      is_current_trace  — True when the caller has confirmed this
                          trace_id represents the LATEST event for the
                          order. When False → GuardCode.STALE_TRACE.

    Behaviour
    ─────────
      • Returns GateOutcome.QUARANTINED for terminal reasons
        (cancelled / refunded / deleted / superseded). These never
        show in pending-orders and never get auto-retried.
      • Returns GateOutcome.BLOCKED for RESOLVABLE reasons
        (needs mapping / bank transfer / unsupported method / totals
        mismatch). These appear in pending-orders under the matching
        category.
      • Returns GateOutcome.ALLOWED with a ScopedBypass only when
        the feature flag is TRUE and every guard passes.
    """
    canonical = row.get("canonical_payload") or {}
    order_number = str(
        row.get("salla_order_number")
        or canonical.get("order_number")
        or row.get("salla_order_id")
        or "")
    trace_id = str(row.get("trace_id") or "")
    guards_passed: list[str] = []
    guards_failed: list[str] = []

    def _fail(code: GuardCode, msg: str,
              hold: Optional[str],
              category: Optional[PendingCategory],
              quarantine: bool = False) -> GateDecision:
        guards_failed.append(code.value)
        return GateDecision(
            outcome=(GateOutcome.QUARANTINED if quarantine
                     else GateOutcome.BLOCKED),
            category=(None if quarantine else category),
            hold_stage=hold,
            guards_passed=guards_passed,
            guards_failed=guards_failed,
            reason_code=code.value,
            reason_message=msg,
            canonical_pm=canonicalise_payment_method(
                canonical.get("payment_method")),
            payment_method_raw=canonical.get("payment_method"),
            posting_mode=posting_mode,
        )

    # G0 — Feature flag
    flag_enabled = bool(settings.get("selective_live_send_enabled", False))

    # G1 — Order status (Iter-293.5-rev3: consult unified set)
    from integrations.qoyod.eligible_statuses import (
        ELIGIBLE_ORDER_STATUSES,
    )
    status = _extract_order_status(row)
    quarantine_code = _is_cancelled_family(status)
    if quarantine_code is not None:
        return _fail(
            quarantine_code,
            f"order status '{status}' is terminal — never auto-send",
            hold="QUARANTINED", category=None, quarantine=True)
    # Compare against the unified eligible set (both lowered English
    # canonicals and Arabic natives are members).
    if status not in ELIGIBLE_ORDER_STATUSES:
        return _fail(
            GuardCode.ORDER_STATUS_NOT_ELIGIBLE,
            (f"order_status='{status}' is not in the unified eligible "
             f"set (completed/delivered/shipped/shipping/processing/"
             f"in_progress + Arabic natives)"),
            hold="STALE_TRACE_NOT_CURRENT_ORDER_STATE",
            category=PendingCategory.STALE_OR_CANCELLED)
    guards_passed.append("order_status_eligible")

    # G2 — Trace freshness (stale supersede check)
    if not is_current_trace:
        return _fail(
            GuardCode.ORDER_SUPERSEDED,
            "this trace_id is not the most recent event for the order",
            hold="ORDER_SUPERSEDED_BY_NEWER_EVENT",
            category=None, quarantine=True)
    guards_passed.append("current_trace")

    # G3 — Payment method allowlist
    #
    # Iter-293.5-rev2: `bank_transfer` is now ALLOWED for invoice
    # creation (ZATCA requires the invoice regardless of receipt
    # method). Only the *payment posting* is deferred until Iter-294
    # so we never book to a legacy generic-bank account.
    raw_pm = canonical.get("payment_method")
    canonical_pm = canonicalise_payment_method(raw_pm)
    is_bank_transfer = canonical_pm in BANK_TRANSFER_METHODS
    is_cod = canonical_pm in COD_ALLOWED
    is_prepaid = canonical_pm in PREPAID_ALLOWED
    # Iter-293.5-rev3 — BNPL family behaves like prepaid: provider
    # settles the full amount, we book invoice + receipt against the
    # BNPL provider's Qoyod account.
    is_bnpl = canonical_pm in BNPL_ALLOWED
    if is_bnpl:
        is_prepaid = True
    if not (is_cod or is_prepaid or is_bank_transfer):
        return _fail(
            GuardCode.UNSUPPORTED_PAYMENT_METHOD,
            (f"payment_method '{raw_pm}' (canonical='{canonical_pm}') "
             "is not on the Iter-293.5 allowlist"),
            hold="HOLD_UNSUPPORTED_PAYMENT_METHOD",
            category=PendingCategory.UNSUPPORTED_METHOD)
    guards_passed.append("payment_method_allowed")

    # G4 — Idempotency
    existing_qid = row.get("qoyod_invoice_id") or ""
    if existing_qid and not str(existing_qid).startswith(
            ("DRY:", "PREVIEW:")):
        return _fail(
            GuardCode.EXISTING_INVOICE,
            (f"qoyod_invoice_id={existing_qid} already exists for this "
             "order — auto-send blocked. Use finalize / diagnostic "
             "endpoints for recovery, not the gate."),
            hold=row.get("pipeline_stage") or "INVOICE_CREATED",
            category=PendingCategory.TOTAL_ROUNDING_REVIEW)
    guards_passed.append("no_existing_invoice")

    # G5 — Dependency sendability + DRY/PREVIEW/null leaks
    dep = dependency_status or {}
    if not dep.get("sendable", False):
        return _fail(
            GuardCode.UNRESOLVED_DEPENDENCY,
            ("preview-reprocess reported sendable=false — the invoice "
             "payload is not ready to POST"),
            hold="UNRESOLVED_QOYOD_DEPENDENCY",
            category=PendingCategory.NEEDS_MAPPING)
    unresolved = dep.get("request_body_unresolved") or []
    if unresolved:
        return _fail(
            GuardCode.NULL_ID_IN_PAYLOAD,
            (f"invoice payload has {len(unresolved)} unresolved fields "
             f"({', '.join(str(u) for u in unresolved[:5])}…)"),
            hold="UNRESOLVED_QOYOD_DEPENDENCY",
            category=PendingCategory.NEEDS_MAPPING)
    if invoice_payload is not None and _has_dry_or_preview_leak(
            invoice_payload):
        return _fail(
            GuardCode.DRY_OR_PREVIEW_LEAK,
            ("invoice payload contains a DRY: or PREVIEW: id — never "
             "auto-send"),
            hold="UNRESOLVED_QOYOD_DEPENDENCY",
            category=PendingCategory.NEEDS_MAPPING)
    guards_passed.append("dependency_sendable")
    guards_passed.append("no_dry_or_preview_leak")

    # G6 — COD posting_mode invariant
    if is_cod and posting_mode != "credit_invoice_only":
        return _fail(
            GuardCode.COD_POSTING_MODE_MISMATCH,
            (f"COD order but posting_mode='{posting_mode}' — expected "
             "'credit_invoice_only'. Refusing auto-send."),
            hold="HOLD_COD_PENDING_FIX",
            category=PendingCategory.COD)
    guards_passed.append(
        "cod_invariant_ok" if is_cod
        else ("bank_transfer_invoice_only" if is_bank_transfer
              else "prepaid_no_cod_invariant_needed"))

    # G7 — Feature flag (final check — logged AFTER all guards so
    # `guards_passed` reflects "would have been allowed if the flag
    # were on"; useful for the read-only pending-orders surface).
    if not flag_enabled:
        return GateDecision(
            outcome=GateOutcome.BLOCKED,
            category=PendingCategory.READY_TO_SEND,
            hold_stage=row.get("pipeline_stage"),
            guards_passed=guards_passed,
            guards_failed=[GuardCode.FLAG_DISABLED.value],
            reason_code=GuardCode.FLAG_DISABLED.value,
            reason_message=("selective_live_send_enabled=false — "
                            "gate defensive mode: rows are surfaced "
                            "in pending-orders but no auto-send."),
            canonical_pm=canonical_pm,
            payment_method_raw=raw_pm,
            posting_mode=posting_mode,
            defers_payment_posting=is_bank_transfer,
            post_invoice_hold_stage=(
                "BANK_TRANSFER_PAYMENT_ROUTING_PENDING"
                if is_bank_transfer else None),
        )

    # ── All guards passed → mint the scoped bypass ──
    # Iter-293.5-rev2 — three shapes of `allowed_actions`:
    #   • Prepaid family     → ("create_invoice", "create_invoice_payment")
    #   • COD                → ("create_invoice",)      (no payment ever)
    #   • bank_transfer      → ("create_invoice",)      (payment deferred)
    if is_prepaid:
        allowed_actions: tuple[str, ...] = (
            "create_invoice", "create_invoice_payment")
    else:
        # COD + bank_transfer both create an invoice ONLY at this stage.
        allowed_actions = ("create_invoice",)
    payload_hash = _payload_hash(invoice_payload or {})
    bypass = ScopedBypass(
        order_number=order_number,
        trace_id=trace_id,
        payload_hash=payload_hash,
        allowed_actions=allowed_actions,
        approval_phrase=f"AUTO-GATE: Approved to send order {order_number} only",
    )
    return GateDecision(
        outcome=GateOutcome.ALLOWED,
        category=PendingCategory.READY_TO_SEND,
        hold_stage=None,
        guards_passed=guards_passed,
        guards_failed=[],
        reason_code=None,
        reason_message=None,
        canonical_pm=canonical_pm,
        payment_method_raw=raw_pm,
        posting_mode=posting_mode,
        bypass=bypass,
        # Bank-transfer flow — invoice yes, payment posting deferred.
        defers_payment_posting=is_bank_transfer,
        post_invoice_hold_stage=(
            "BANK_TRANSFER_PAYMENT_ROUTING_PENDING"
            if is_bank_transfer else None),
    )


def _has_dry_or_preview_leak(obj: Any) -> bool:
    """Deep scan for DRY: / PREVIEW: string prefixes. Also flags None
    values in obviously-identifier fields (contact_id, product_id)."""
    if isinstance(obj, str):
        return obj.startswith("DRY:") or obj.startswith("PREVIEW:")
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in ("contact_id", "product_id") and v is None:
                return True
            if _has_dry_or_preview_leak(v):
                return True
        return False
    if isinstance(obj, list):
        return any(_has_dry_or_preview_leak(x) for x in obj)
    return False
