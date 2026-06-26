"""Qoyod Business Rules — `NORMALIZED → RULES_APPLIED` / `SKIPPED`.

Day 4 scope:
    Decide whether a NORMALIZED order is **eligible** to be sent to
    Qoyod, and if so, compute the canonical *invoice date* that goes
    on the Qoyod invoice. NOTHING ELSE happens here — no API calls,
    no DB writes beyond the transition itself.

The single source of truth for "should this order go to Qoyod?" is the
**Invoice Trigger Policy** stored on `qoyod_settings`:

    invoice_trigger_statuses   list[str]   default ["completed"]
    invoice_date_source        str         default "trigger_status_date"
    trigger_once_only          bool        default True

Why this design (per user directive 2026-06-26):
    1. NEVER hard-code "paid" as the trigger. The merchant is legally
       responsible (Zakat + VAT) for the invoice date, so the date
       MUST come from a configurable status transition.
    2. The trigger is a LIST so a merchant can configure multiple
       statuses (e.g. both "completed" and "delivered") without code
       changes.
    3. Once an order produces a Qoyod invoice, subsequent status
       changes do NOT regenerate it (idempotency at the business level).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from integrations.qoyod.dto import SalesOrderDTO


# Decision tokens — closed set. The pipeline maps these directly to
# the state-machine transitions.
ELIGIBLE       = "eligible"
SKIP_NOT_IN_TRIGGER = "not_in_trigger_statuses"
SKIP_ALREADY_SENT   = "already_sent"
SKIP_DUPLICATE_PIPELINE = "duplicate_inbox"


@dataclass
class RulesDecision:
    """Pure output of the rules layer. The orchestrator decides how to
    persist it (transition to RULES_APPLIED or SKIPPED)."""
    eligible:    bool
    reason:      str                  # one of the SKIP_* tokens (or ELIGIBLE)
    invoice_date: Optional[datetime]  # None when not eligible
    invoice_date_source: str          # which timestamp produced invoice_date
    triggered_by_status: Optional[str] = None
    notes:       list[str] = field(default_factory=list)

    def to_log_dict(self) -> dict:
        """Compact form for stage_history / Qoyod invoice metadata."""
        return {
            "eligible":            self.eligible,
            "reason":              self.reason,
            "invoice_date":        (self.invoice_date.isoformat()
                                    if self.invoice_date else None),
            "invoice_date_source": self.invoice_date_source,
            "triggered_by_status": self.triggered_by_status,
        }


# ─────────────────────────────────────────────────────────────────────
# Invoice-date resolution
# ─────────────────────────────────────────────────────────────────────
# Maps a CANONICAL order status to the DTO field that records when the
# order entered that status. Conservative — falls back to `order_date`
# for unknown statuses so the policy still produces a date.
_STATUS_TO_DATE_FIELD = {
    "completed":  "completed_at",
    "delivered":  "completed_at",   # shipped+delivered is "transition end"
    "paid":       "paid_at",
    "shipped":    "order_date",     # Salla doesn't expose shipped_at
    "processing": "order_date",
}


def _resolve_trigger_status_date(
    dto: SalesOrderDTO, triggered_by: Optional[str]
) -> tuple[Optional[datetime], str]:
    """Pick the DTO timestamp that represents *when* the order entered
    the trigger status. Returns (datetime, "<field-name>")."""
    if triggered_by:
        field_name = _STATUS_TO_DATE_FIELD.get(triggered_by)
        if field_name:
            val = getattr(dto, field_name, None)
            if val:
                return val, field_name
    # No matching field → completed_at if present, otherwise order_date.
    if dto.completed_at:
        return dto.completed_at, "completed_at"
    if dto.order_date:
        return dto.order_date, "order_date"
    return None, "none"


def _resolve_invoice_date(
    dto: SalesOrderDTO, source: str, triggered_by: Optional[str]
) -> tuple[Optional[datetime], str]:
    """Apply the merchant's `invoice_date_source` setting.

    Returns (date_or_None, actual_source_used). The "actual source"
    can differ from the requested one when a fallback kicks in — the
    pipeline logs both so the operator can audit the choice.
    """
    s = (source or "").strip()
    if s == "trigger_status_date":
        dt, used = _resolve_trigger_status_date(dto, triggered_by)
        return dt, used
    # Explicit field requests — return directly, no fallback so the
    # merchant sees the absence rather than a silent surrogate.
    if s == "completed_at":
        return dto.completed_at, "completed_at"
    if s == "paid_at":
        return dto.paid_at, "paid_at"
    if s == "created_at":
        return dto.order_date, "order_date"
    # Unknown setting → defensive fallback.
    return _resolve_trigger_status_date(dto, triggered_by)


# ─────────────────────────────────────────────────────────────────────
# Public entry point
# ─────────────────────────────────────────────────────────────────────
def evaluate(
    dto: SalesOrderDTO,
    settings: dict,
    *,
    existing_invoice_row: Optional[dict] = None,
) -> RulesDecision:
    """Decide whether the DTO is eligible to proceed.

    Parameters:
        dto:                   the canonical SalesOrderDTO.
        settings:              the merchant's qoyod_settings doc.
        existing_invoice_row:  matching `qoyod_invoices` row (if any).

    Pure — no DB access, no IO. The caller is responsible for resolving
    `existing_invoice_row` upstream (one Mongo lookup keyed by
    `salla_order_id`).
    """
    triggers = settings.get("invoice_trigger_statuses") or ["completed"]
    once = bool(settings.get("trigger_once_only", True))
    source = settings.get("invoice_date_source") or "trigger_status_date"

    canonical_status = (dto.order_status or "").strip().lower()
    triggered_by = canonical_status if canonical_status in triggers else None

    # ── Rule 1: status must be in trigger list ──────────────────────
    if not triggered_by:
        return RulesDecision(
            eligible=False,
            reason=SKIP_NOT_IN_TRIGGER,
            invoice_date=None,
            invoice_date_source="none",
            triggered_by_status=None,
            notes=[
                f"order_status={dto.order_status_native!r} "
                f"(canonical={dto.order_status!r}) not in triggers={triggers}",
            ],
        )

    # ── Rule 2: trigger_once_only — never re-create ─────────────────
    if once and existing_invoice_row:
        prev_status = existing_invoice_row.get("status")
        if prev_status in ("sent", "invoice_sent_receipt_failed",
                           "pending", "retrying"):
            return RulesDecision(
                eligible=False,
                reason=SKIP_ALREADY_SENT,
                invoice_date=None,
                invoice_date_source="none",
                triggered_by_status=triggered_by,
                notes=[
                    f"trigger_once_only=True and an invoice row already "
                    f"exists with status={prev_status!r}.",
                ],
            )

    # ── Resolve invoice date ────────────────────────────────────────
    invoice_dt, used_source = _resolve_invoice_date(dto, source, triggered_by)
    return RulesDecision(
        eligible=True,
        reason=ELIGIBLE,
        invoice_date=invoice_dt,
        invoice_date_source=used_source,
        triggered_by_status=triggered_by,
    )
