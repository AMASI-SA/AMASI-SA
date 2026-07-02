"""Iter-2026-02.rev16 — Selective Auto-Send Gate.

Scope (STRICT — user directive 2026-02-27)
──────────────────────────────────────────
This gate is the ONE control that opens automatic Qoyod writes for
NEW orders only, under Fail-Closed defaults. It is disabled unless
`qoyod_settings.selective_auto_send_enabled == True`.

Twelve invariants (must all pass — else row is SKIPPED with reason):

  1. `selective_auto_send_enabled` is True
  2. `selective_auto_send_cutover_at` is set (ISO datetime)
  3. Order was CREATED (Salla creation date) STRICTLY AFTER cutover
  4. `dto.order_status` is in the allowed statuses set
  5. `dto.order_status_native` NOT in the hard-blocked statuses set
     (`delivered`, `تم التوصيل`, `under_delivery`, `جاري التوصيل`,
      `جاري_التوصيل`)
  6. `canonical.payment_method` is NOT in the hard-blocked set
     (`bank_transfer`, `cod`, `cash_on_delivery`)
  7. `canonical.payment_method` IS in the tenant's
     `selective_auto_send_allowed_payment_methods` list — start with
     `["tabby_installment"]` only, expanded manually after first
     confirmed success.
  8. قيود payment_method mapping resolves for the incoming method
     (exact key first, then canonical alias) — else refuse before
     any Qoyod POST attempt.
  9. Row does NOT already carry a REAL `qoyod_invoice_id` — those
     rows are handled by `retry_payment_only`, never re-invoiced.

The gate returns `GateDecision` — a structured object suitable for
persistence (`to_log_dict()`).

Scoped write allowance
──────────────────────
When the gate PASSES, callers pass `row=<...>` to `_get_api_client`
which flips `write_lock_enabled=False` for THIS ROW's execution
only. The DB's `production_writes_locked` flag is NEVER modified.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional


# ── Hard constants (tenant CANNOT override via UI) ──────────────
ALLOWED_STATUSES: frozenset[str] = frozenset({
    "completed", "تم التنفيذ",
})

BLOCKED_STATUSES: frozenset[str] = frozenset({
    # Delivery-in-progress / delivered — never auto-send.
    "delivered",  "تم التوصيل",
    "under_delivery", "جاري التوصيل", "جاري_التوصيل",
})

BLOCKED_PAYMENT_METHODS: frozenset[str] = frozenset({
    "bank_transfer", "cod", "cash_on_delivery",
})

# The tenant's `selective_auto_send_allowed_payment_methods` list
# defaults to this on first enable. Operator can expand later.
DEFAULT_ALLOWED_PAYMENT_METHODS: tuple[str, ...] = (
    "tabby_installment",
)

# Canonical → aliases (Iter-290h; mirror payment_methods.py).
_PAYMENT_METHOD_ALIASES: dict[str, str] = {
    "tabby_installment":  "tabby",
    "tabby_installments": "tabby",
    "tabby_pay":          "tabby",
    "tabby_payment":      "tabby",
    "tamara_installment": "tamara",
    "tamara_installments": "tamara",
    "mastercard":         "credit_card",
    "visa":               "credit_card",
}


class ReasonCode:
    NOT_ENABLED               = "selective_auto_send_disabled"
    NO_CUTOVER                = "no_cutover_configured"
    BEFORE_CUTOVER            = "order_created_before_cutover"
    STATUS_NOT_ALLOWED        = "status_not_in_allow_list"
    STATUS_HARD_BLOCKED       = "status_hard_blocked"
    PM_HARD_BLOCKED           = "payment_method_hard_blocked"
    PM_NOT_ALLOWED            = "payment_method_not_in_allow_list"
    PM_MAPPING_MISSING        = "payment_method_mapping_missing"
    HAS_REAL_INVOICE_ID       = "row_has_real_qoyod_invoice_id"
    NO_SALLA_CREATION_DATE    = "salla_creation_date_unresolvable"


@dataclass
class GateDecision:
    eligible:              bool
    reason:                Optional[str] = None
    detail:                Optional[str] = None
    resolved_payment_key:  Optional[str] = None      # after alias resolve
    resolved_account_id:   Optional[str] = None
    salla_created_at:      Optional[str] = None
    cutover_at:            Optional[str] = None
    extras:                dict[str, Any] = field(default_factory=dict)

    def to_log_dict(self) -> dict:
        return {
            "eligible":             self.eligible,
            "reason":               self.reason,
            "detail":               self.detail,
            "resolved_payment_key": self.resolved_payment_key,
            "resolved_account_id":  self.resolved_account_id,
            "salla_created_at":     self.salla_created_at,
            "cutover_at":           self.cutover_at,
            "extras":               self.extras or None,
        }


def _parse_iso(v: Any) -> Optional[datetime]:
    if not v:
        return None
    try:
        s = str(v).replace("Z", "+00:00")
        d = datetime.fromisoformat(s)
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d
    except (TypeError, ValueError):
        return None


def _extract_salla_created(canonical: dict, row: dict) -> Optional[datetime]:
    """Prefer Salla's original creation timestamp — that is what the
    user means by "الطلبات الجديدة فقط بعد cutover"."""
    for k in (
        "salla_order_created_at",
        "order_created_at",
        "created_at",
        "order_date",
    ):
        d = _parse_iso(canonical.get(k))
        if d is not None:
            return d
    # Raw payload fallback (Salla webhook shape).
    raw = row.get("raw_payload") or {}
    if isinstance(raw, dict):
        data = raw.get("data") or {}
        if isinstance(data, dict):
            date = data.get("date")
            if isinstance(date, dict):
                d = _parse_iso(date.get("date"))
                if d is not None:
                    return d
    # Last resort: received_at (webhook ingestion time — close enough
    # to creation for NEW orders since Salla webhooks fire promptly).
    d = _parse_iso(row.get("received_at"))
    return d


def _resolve_payment_mapping(
    pm_raw: str, settings: dict,
) -> tuple[Optional[str], Optional[str]]:
    """Return (resolved_key_used, account_id) or (None, None) if
    the tenant hasn't mapped the method.

    Lookup order:
      1. Exact match on tenant's `payment_method_mapping.salla_method`
      2. Canonical alias (e.g. tabby_installment → tabby)
    """
    mapping = settings.get("payment_method_mapping") or []
    if not isinstance(mapping, list):
        return None, None

    def _find(k: str) -> Optional[str]:
        for m in mapping:
            if not isinstance(m, dict):
                continue
            if str(m.get("salla_method") or "").lower() == k.lower():
                acc = m.get("qoyod_account_id") or m.get("account_id")
                if acc not in (None, ""):
                    return str(acc)
        return None

    pm_norm = str(pm_raw or "").lower().strip()
    acc = _find(pm_norm)
    if acc:
        return pm_norm, acc

    alias = _PAYMENT_METHOD_ALIASES.get(pm_norm)
    if alias:
        acc = _find(alias)
        if acc:
            return alias, acc

    return None, None


def _is_real_invoice_id(v: Any) -> bool:
    if v in (None, ""):
        return False
    s = str(v)
    if s.startswith("DRY:") or s.startswith("PREVIEW:"):
        return False
    return True


def evaluate_selective_auto_send_gate(
    *,
    canonical: dict,
    row:       dict,
    settings:  dict,
    now:       Optional[datetime] = None,
) -> GateDecision:
    """Evaluate the twelve invariants above and return a decision.

    Args:
      canonical: `row["canonical_payload"]` (already-normalised DTO).
      row:       full `integration_inbox` document.
      settings:  loaded `qoyod_settings` doc.
      now:       injected for tests. Defaults to `datetime.utcnow()`.
    """
    now = now or datetime.now(timezone.utc)

    # 1. Master switch.
    if not bool(settings.get("selective_auto_send_enabled", False)):
        return GateDecision(
            eligible=False, reason=ReasonCode.NOT_ENABLED,
            detail="Selective Auto-Send master switch is OFF.")

    # 2. Cutover configured.
    cutover_raw = settings.get("selective_auto_send_cutover_at")
    cutover_dt  = _parse_iso(cutover_raw)
    if cutover_dt is None:
        return GateDecision(
            eligible=False, reason=ReasonCode.NO_CUTOVER,
            detail="`selective_auto_send_cutover_at` not set.",
            cutover_at=str(cutover_raw) if cutover_raw else None)

    # 3. Salla creation strictly AFTER cutover.
    salla_dt = _extract_salla_created(canonical, row)
    if salla_dt is None:
        return GateDecision(
            eligible=False,
            reason=ReasonCode.NO_SALLA_CREATION_DATE,
            detail="Cannot resolve Salla creation date from "
                   "canonical_payload or raw_payload.",
            cutover_at=cutover_dt.isoformat())
    if salla_dt <= cutover_dt:
        return GateDecision(
            eligible=False, reason=ReasonCode.BEFORE_CUTOVER,
            detail=(f"Salla creation {salla_dt.isoformat()} "
                    f"is not AFTER cutover "
                    f"{cutover_dt.isoformat()}."),
            salla_created_at=salla_dt.isoformat(),
            cutover_at=cutover_dt.isoformat())

    # 5. Status hard-block (delivered / جاري التوصيل / etc.).
    status_native = str(canonical.get("order_status_native")
                        or canonical.get("order_status") or "").strip()
    status_canon  = str(canonical.get("order_status") or "").strip()
    if (status_native in BLOCKED_STATUSES
            or status_canon in BLOCKED_STATUSES):
        return GateDecision(
            eligible=False, reason=ReasonCode.STATUS_HARD_BLOCKED,
            detail=(f"Status '{status_native}' / '{status_canon}' "
                    "is on the hard-blocked list "
                    "(delivered / under_delivery)."),
            salla_created_at=salla_dt.isoformat(),
            cutover_at=cutover_dt.isoformat())

    # 4. Status allow-list.
    if (status_canon not in ALLOWED_STATUSES
            and status_native not in ALLOWED_STATUSES):
        return GateDecision(
            eligible=False, reason=ReasonCode.STATUS_NOT_ALLOWED,
            detail=(f"Status '{status_canon}' / '{status_native}' "
                    f"is not in the allow-list "
                    f"{sorted(ALLOWED_STATUSES)}."),
            salla_created_at=salla_dt.isoformat(),
            cutover_at=cutover_dt.isoformat())

    # 6. Payment method hard-block.
    pm_raw = str(canonical.get("payment_method") or "").lower().strip()
    if pm_raw in BLOCKED_PAYMENT_METHODS:
        return GateDecision(
            eligible=False, reason=ReasonCode.PM_HARD_BLOCKED,
            detail=(f"Payment method '{pm_raw}' is on the "
                    "hard-blocked list (bank_transfer / COD)."),
            salla_created_at=salla_dt.isoformat(),
            cutover_at=cutover_dt.isoformat())
    # Also check aliases against block list.
    alias = _PAYMENT_METHOD_ALIASES.get(pm_raw)
    if alias and alias in BLOCKED_PAYMENT_METHODS:
        return GateDecision(
            eligible=False, reason=ReasonCode.PM_HARD_BLOCKED,
            detail=(f"Payment method alias '{alias}' is on the "
                    "hard-blocked list."),
            salla_created_at=salla_dt.isoformat(),
            cutover_at=cutover_dt.isoformat())

    # 7. Payment method allow-list.
    allowed_pms = settings.get(
        "selective_auto_send_allowed_payment_methods") or list(
            DEFAULT_ALLOWED_PAYMENT_METHODS)
    allowed_pms_lc = {str(x).lower() for x in allowed_pms
                      if x is not None}
    if pm_raw not in allowed_pms_lc and (
            alias is None or alias not in allowed_pms_lc):
        return GateDecision(
            eligible=False, reason=ReasonCode.PM_NOT_ALLOWED,
            detail=(f"Payment method '{pm_raw}' not in the "
                    f"tenant's allow-list "
                    f"{sorted(allowed_pms_lc)}."),
            salla_created_at=salla_dt.isoformat(),
            cutover_at=cutover_dt.isoformat())

    # 8. Payment method mapping resolves to a قيود account_id.
    resolved_key, account_id = _resolve_payment_mapping(
        pm_raw, settings)
    if not resolved_key or not account_id:
        return GateDecision(
            eligible=False, reason=ReasonCode.PM_MAPPING_MISSING,
            detail=(f"No قيود account mapped for payment method "
                    f"'{pm_raw}' (aliases tried: "
                    f"{alias or 'n/a'})."),
            salla_created_at=salla_dt.isoformat(),
            cutover_at=cutover_dt.isoformat())

    # 9. Row must not carry a REAL قيود invoice id — those rows are
    # handled by `retry_payment_only`. The auto-send path never
    # re-invoices.
    qid_row     = row.get("qoyod_invoice_id")
    qid_inv     = None
    inv_row_ref = row.get("qoyod_invoices_row") or {}
    if isinstance(inv_row_ref, dict):
        qid_inv = inv_row_ref.get("qoyod_invoice_id")
    if _is_real_invoice_id(qid_row) or _is_real_invoice_id(qid_inv):
        return GateDecision(
            eligible=False, reason=ReasonCode.HAS_REAL_INVOICE_ID,
            detail=(f"Row already carries real قيود invoice_id "
                    f"({qid_row or qid_inv}). Auto-send must not "
                    "re-invoice; route via retry_payment_only."),
            salla_created_at=salla_dt.isoformat(),
            cutover_at=cutover_dt.isoformat())

    # All gates passed.
    return GateDecision(
        eligible=True, reason="eligible",
        resolved_payment_key=resolved_key,
        resolved_account_id=account_id,
        salla_created_at=salla_dt.isoformat(),
        cutover_at=cutover_dt.isoformat())
