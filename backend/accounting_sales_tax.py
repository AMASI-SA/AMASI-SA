"""Mezan 2 manual sales tax arithmetic. Provider fee tax is not an input."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

CENT = Decimal("0.01")


class TaxError(ValueError):
    pass


def decimal_value(value, *, scale=CENT):
    if value is None or isinstance(value, bool) or str(value).strip() == "":
        raise TaxError("explicit_value_required")
    try:
        result = Decimal(str(value))
        if not result.is_finite() or result < 0 or result != result.quantize(scale):
            raise TaxError("invalid_decimal")
        return result
    except (InvalidOperation, TypeError, ValueError) as exc:
        if isinstance(exc, TaxError):
            raise
        raise TaxError("invalid_decimal") from None


def rate_value(value):
    result = decimal_value(value, scale=Decimal("0.0001"))
    if result > 100:
        raise TaxError("rate_out_of_range")
    return result


def instant(value):
    try:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if result.tzinfo is None or result.utcoffset() is None:
            raise TaxError("timezone_required")
        return result.astimezone(timezone.utc)
    except (ValueError, TypeError) as exc:
        if isinstance(exc, TaxError):
            raise
        raise TaxError("invalid_date") from None


def select_version(versions, recognized_at):
    when = instant(recognized_at)
    eligible = [v for v in versions if instant(v["effective_at"]) <= when]
    if not eligible:
        raise TaxError("sales_tax_not_configured_for_date")
    return max(eligible, key=lambda v: (instant(v["effective_at"]), v["revision"]))


def split_gross(gross, rate):
    total, percent = decimal_value(gross), rate_value(rate)
    if total <= 0:
        raise TaxError("positive_gross_required")
    net = (total / (1 + percent / 100)).quantize(CENT, rounding=ROUND_HALF_UP)
    return {"gross": format(total, ".2f"), "net": format(net, ".2f"),
            "tax": format(total - net, ".2f"), "rate": str(percent),
            "rounding": "HALF_UP_CENT", "method": "gross_inclusive_manual_rate_v1"}


def refund_split(original, previous, gross):
    """Allocate cumulative refunds against the immutable original split.

    Cumulative allocation prevents cent-sized refunds from accumulating tax
    drift. The final refund consumes the exact remaining net and tax.
    """
    total = decimal_value(original["gross"])
    original_net, original_tax = decimal_value(original["net"]), decimal_value(original["tax"])
    if total <= 0 or original_net + original_tax != total:
        raise TaxError("original_split_conflict")
    used_gross = sum((decimal_value(r["gross"]) for r in previous), Decimal(0))
    used_net = sum((decimal_value(r["net"]) for r in previous), Decimal(0))
    used_tax = sum((decimal_value(r["tax"]) for r in previous), Decimal(0))
    if used_net + used_tax != used_gross:
        raise TaxError("refund_history_conflict")
    amount = decimal_value(gross)
    cumulative = used_gross + amount
    if amount <= 0 or cumulative > total:
        raise TaxError("refund_exceeds_original")
    target_net = (cumulative * original_net / total).quantize(CENT, rounding=ROUND_HALF_UP)
    net = target_net - used_net
    tax = amount - net
    if min(net, tax) < 0 or used_net + net > original_net or used_tax + tax > original_tax:
        raise TaxError("refund_history_conflict")
    return {**original, "gross": format(amount, ".2f"),
            "net": format(net, ".2f"), "tax": format(tax, ".2f")}
