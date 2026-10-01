"""Read-only V2 obligation discovery and exact opening prepaid allocation.

Coverage uses [start, end); recurring source cycle ends are inclusive and are
converted explicitly. Obligation period amount is never evidence of payment.
"""
import re
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP

from recurring_obligations_routes import cycle_bounds


def _day(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError("prepaid_date_invalid")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("prepaid_date_invalid") from exc


def calculate_prepaid(row, cutover_day):
    """Validate a complete setup row; return computed amounts, never trust UI totals."""
    start, end, cutover, paid = map(_day, (
        row.get("coverage_start"), row.get("coverage_end"), cutover_day, row.get("payment_date")))
    if end <= start:
        raise ValueError("prepaid_coverage_invalid")
    if paid >= cutover:
        raise ValueError("prepaid_payment_not_before_cutover")
    if end <= cutover:
        raise ValueError("prepaid_no_remaining_coverage")
    if row.get("currency") != "SAR":
        raise ValueError("prepaid_currency_requires_fx_evidence")
    raw = row.get("amount_paid")
    if not isinstance(raw, str) or not re.fullmatch(r"\d{1,15}(?:\.\d{1,2})?", raw) or Decimal(raw) <= 0:
        raise ValueError("prepaid_positive_paid_amount_required")
    if row.get("payment_status") == "unpaid":
        raise ValueError("prepaid_unpaid_obligation")
    if not str(row.get("evidence_ref") or "").strip():
        raise ValueError("prepaid_payment_evidence_required")
    if not str(row.get("title") or "").strip():
        raise ValueError("prepaid_title_required")
    mode = row.get("source_mode")
    if mode == "obligation":
        if not row.get("obligation_id"):
            raise ValueError("prepaid_obligation_identity_required")
    elif mode == "manual_exception":
        if not str(row.get("entity") or "").strip():
            raise ValueError("prepaid_entity_required")
    else:
        raise ValueError("prepaid_source_mode_required")
    days = (end - start).days
    consumed_days = max(0, min(days, (cutover - start).days))
    amount = Decimal(raw)
    consumed = (amount * consumed_days / days).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return {"coverage_days": days, "consumed_days": consumed_days,
            "remaining_days": days - consumed_days,
            "consumed_before_cutover": format(consumed, ".2f"),
            "prepaid_remaining_at_cutover": format(amount - consumed, ".2f")}


async def list_prepaid_obligations(db, tenant_id, cutover_day):
    target = _day(cutover_day)
    rows = await db["operating_recurring_obligations_v2"].find({"user_id": tenant_id}, {"_id": 0}).to_list(5000)
    invoices = await db["operating_recurring_invoices_v2"].find({"user_id": tenant_id}, {"_id": 0}).to_list(10000)
    result = []
    for row in rows:
        if not row.get("id"):
            continue
        try:
            start = _day(row.get("start_date"))
            bounds = cycle_bounds(start, row.get("cycle"), max(target, start),
                                  custom_end=_day(row["custom_end_date"]) if row.get("custom_end_date") else None)
            # A nonrenewing obligation cannot be projected into a future cycle.
            if row.get("auto_renew") is False:
                bounds = cycle_bounds(start, row.get("cycle"), start,
                                      custom_end=_day(row["custom_end_date"]) if row.get("custom_end_date") else None)
        except ValueError:
            bounds = None
        related = [i for i in invoices if i.get("obligation_id") == row["id"] and bounds
                   and i.get("period_start") == bounds[0].isoformat() and i.get("period_end") == bounds[1].isoformat()]
        payment_status = related[0].get("payment_status") if len(related) == 1 else None
        result.append({"id": row["id"], "source_collection": "operating_recurring_obligations_v2",
                       **{k: row.get(k) for k in ("title", "expense_type", "cycle", "period_amount", "status", "auto_renew")},
                       "entity": row.get("entity_name"), "entity_id": row.get("entity_id"),
                       "coverage_start": bounds[0].isoformat() if bounds else None,
                       "coverage_end": (bounds[1] + timedelta(days=1)).isoformat() if bounds else None,
                       "payment_status": payment_status,
                       "payment_date": related[0].get("paid_date") if len(related) == 1 and payment_status == "paid" else None,
                       "currency": row.get("currency"),
                       "payment_evidence_required": True})
    return result
