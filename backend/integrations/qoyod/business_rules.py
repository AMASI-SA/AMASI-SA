"""Qoyod Business Rules — `NORMALIZED → RULES_APPLIED` / `SKIPPED`.

Day 4 scope:
    Decide whether a NORMALIZED order is **eligible** to be sent to
    Qoyod, and if so, compute the canonical *invoice date* that goes
    on the Qoyod invoice. NOTHING ELSE happens here — no API calls,
    no DB writes beyond the transition itself.

The single source of truth for "should this order go to Qoyod?" is the
**Invoice Trigger Policy** stored on `qoyod_settings`:

    invoice_trigger_statuses   list[str]   default ["completed"]
    invoice_date_source        str         default "send_date"  (rev9)
    trigger_once_only          bool        default True

Iter-293.4-rev9 — Invoice issue-date policy defaults to `send_date`:
    Historically the default was `trigger_status_date` (which resolves
    to `completed_at` for COD/completed orders). That meant a manual
    resend the following day would still stamp the قيود invoice with
    yesterday's date. Per user directive (2026-07-01), for ZATCA
    correctness the قيود `issue_date` MUST reflect when the invoice
    was CREATED in قيود — the actual send date in Asia/Riyadh — not
    the Salla-side completion timestamp. `completed_at` is preserved
    as diagnostic metadata but no longer drives `issue_date`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

try:
    from zoneinfo import ZoneInfo    # Python ≥ 3.9
except Exception:    # pragma: no cover
    ZoneInfo = None    # type: ignore[assignment]

from integrations.qoyod.dto import SalesOrderDTO
from integrations.qoyod.eligible_statuses import resolve_trigger_statuses


# Asia/Riyadh — the SAR-denominated ZATCA jurisdiction. Every
# قيود-facing issue_date computed with `invoice_date_source=send_date`
# is snapped to LOCAL midnight in this zone so a 23:31 UTC+3 send
# stamps the correct local date (never a "day-earlier" UTC leak).
QOYOD_ISSUE_DATE_TIMEZONE = "Asia/Riyadh"


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
    # Iter-293.4-rev9 — extra diagnostics for the operator UI.
    completed_at:                Optional[datetime] = None
    invoice_issue_date_timezone: Optional[str] = None

    def to_log_dict(self) -> dict:
        """Compact form for stage_history / Qoyod invoice metadata."""
        return {
            "eligible":            self.eligible,
            "reason":              self.reason,
            "invoice_date":        (self.invoice_date.isoformat()
                                    if self.invoice_date else None),
            "invoice_date_source": self.invoice_date_source,
            "triggered_by_status": self.triggered_by_status,
            # Iter-293.4-rev9 diagnostics.
            "completed_at":        (self.completed_at.isoformat()
                                    if self.completed_at else None),
            "invoice_issue_date_source":   self.invoice_date_source,
            "invoice_issue_date_timezone": self.invoice_issue_date_timezone,
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


def _resolve_send_date_riyadh() -> tuple[datetime, str]:
    """Iter-293.4-rev9 — قيود issue_date = current send-time in
    Asia/Riyadh. Returns (datetime, source-label).

    We return the LOCAL Riyadh datetime (with `tzinfo=Asia/Riyadh` so
    downstream `.date()` calls snap to the correct local day). Falls
    back to fixed UTC+3 offset if `zoneinfo` isn't available (rare —
    Python ≥ 3.9 ships it by default).
    """
    now_utc = datetime.now(timezone.utc)
    if ZoneInfo is not None:
        try:
            return now_utc.astimezone(ZoneInfo(QOYOD_ISSUE_DATE_TIMEZONE)), \
                   "send_date"
        except Exception:    # pragma: no cover — bad tzdata
            pass
    # Fallback: fixed UTC+3 (no DST in Saudi Arabia — safe constant).
    from datetime import timedelta
    return now_utc.astimezone(
        timezone(timedelta(hours=3), name="Asia/Riyadh-fallback")), \
        "send_date_utc_offset_fallback"


def _resolve_invoice_date(
    dto: SalesOrderDTO, source: str, triggered_by: Optional[str]
) -> tuple[Optional[datetime], str]:
    """Apply the merchant's `invoice_date_source` setting.

    Returns (date_or_None, actual_source_used). The "actual source"
    can differ from the requested one when a fallback kicks in — the
    pipeline logs both so the operator can audit the choice.

    Iter-293.4-rev9 — `send_date` is the recommended production
    default. It stamps قيود's `issue_date` with the current Asia/Riyadh
    date at the moment the invoice is being sent, matching the ZATCA
    requirement that issue_date reflects the true creation moment
    (not the underlying Salla-side event).
    """
    s = (source or "").strip()
    # Iter-293.4-rev9 — new preferred source.
    if s == "send_date":
        return _resolve_send_date_riyadh()
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
    # Unknown setting → defensive fallback to the new send_date default
    # so a mistyped config doesn't accidentally revive the old
    # completed_at behaviour.
    return _resolve_send_date_riyadh()


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
    # Iter-293.5-rev3 — widen the default trigger list to the unified
    # ELIGIBLE_ORDER_STATUSES set so preflight, pending queue, and
    # business_rules never disagree. Tenants can still explicitly
    # narrow the list via `qoyod_settings.invoice_trigger_statuses`.
    triggers = resolve_trigger_statuses(settings)
    once = bool(settings.get("trigger_once_only", True))
    # Iter-293.4-rev9 — Default flipped from "trigger_status_date"
    # (which resolved to completed_at for COD orders) to "send_date"
    # so قيود's issue_date matches the actual send moment in
    # Asia/Riyadh. Legacy setting is honoured when explicit.
    source = settings.get("invoice_date_source") or "send_date"

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
        # Iter-293.4-rev9 — Diagnostics for the UI. `completed_at`
        # stays visible as a REFERENCE timestamp regardless of which
        # source drives `invoice_date`. Timezone label only when the
        # send_date path resolved (Asia/Riyadh) so operators can
        # distinguish local-day-snapping from a raw DTO timestamp.
        completed_at=dto.completed_at,
        invoice_issue_date_timezone=(
            QOYOD_ISSUE_DATE_TIMEZONE
            if used_source in ("send_date",
                               "send_date_utc_offset_fallback")
            else None),
    )
