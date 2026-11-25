"""Fail-closed adapter for already-normalized native Salla capture evidence.

Only consume the output of operational_payment_evidence.normalize_payment /
operational_provider_transaction_evidence.normalize_transaction_payment,
not the raw order's fulfillment status or observed paid_amount.

No writes or financial effects are performed here. A fresh authoritative
operational state and origin scope must be checked at a CAS posting boundary.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from operational_payment_event_policy import Evidence, Kind, PaymentEvent


class CaptureEvidenceError(ValueError):
    pass


def _money(value):
    if isinstance(value, bool):
        raise CaptureEvidenceError("capture_amount_invalid")
    try:
        v = Decimal(str(value))
        if not v.is_finite() or v <= 0 or v != v.quantize(Decimal(".01")):
            raise CaptureEvidenceError("capture_amount_invalid")
        return format(v, ".2f")
    except (ValueError, InvalidOperation, TypeError):
        raise CaptureEvidenceError("capture_amount_invalid") from None


def _timestamp(value):
    if not isinstance(value, str):
        raise CaptureEvidenceError("capture_timestamp_missing")
    try:
        t = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if t.tzinfo is None or t.utcoffset() is None:
            raise ValueError()
        return t
    except ValueError:
        raise CaptureEvidenceError("capture_timestamp_invalid") from None


def payment_events_from_normalized(order_id, normalized, diagnostics=()):
    """Require a complete canonical capture; return empty for no paid capture.

    Caller assigns initial-vs-additional *after* comparing canonical posted
    provider transaction IDs; no inferred initial payment from current total.
    """
    if diagnostics:
        return []
    if not isinstance(order_id, str) or not order_id.strip():
        raise CaptureEvidenceError("order_identity_missing")
    if not isinstance(normalized, dict):
        return []
    provider, currency = normalized.get("provider"), normalized.get("currency")
    if provider not in {"salla", "tabby", "tamara", "emkan", "paypal"}:
        return []
    if currency != "SAR":
        # Retain native currency evidence for review; do not imply SAR receipt.
        return []
    captures = normalized.get("captures") or []
    if not isinstance(captures, list) or len(captures) > 32:
        raise CaptureEvidenceError("capture_history_invalid")
    out, refs = [], set()
    gross = _money(normalized["gross"])
    aggregate = Decimal("0")
    for capture in captures:
        if not isinstance(capture, dict):
            raise CaptureEvidenceError("capture_shape_invalid")
        ref = capture.get("id")
        if not isinstance(ref, str) or not ref.strip() or len(ref) > 256:
            raise CaptureEvidenceError("capture_reference_missing")
        if ref in refs:
            raise CaptureEvidenceError("capture_reference_repeated")
        refs.add(ref)
        value = _money(capture.get("amount"))
        captured_at = _timestamp(capture.get("captured_at")).astimezone(timezone.utc).isoformat()
        aggregate += Decimal(value)
        if aggregate > Decimal(gross):
            raise CaptureEvidenceError("capture_exceeds_original_total")
        out.append(PaymentEvent(order_id=order_id, provider=provider, amount=value,
                                kind=Kind.PAYMENT, evidence=Evidence.PROVIDER_SUCCESS,
                                transaction_id=ref, currency=currency,
                                captured_at=captured_at))
    return out
