"""Read-only qualification of source events. Import/matching never implies recognition."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json

PROVIDERS = {
    "tamara": {"tamara", "تمارا"},
    "tabby": {"tabby", "تابي"},
    "emkan": {"emkan", "imkan", "emkaninstallment", "إمكان", "امكان"},
}
CAPTURED = {"captured", "fully_captured", "closed", "completed"}
FULFILLED = {"delivered", "completed", "تم التنفيذ", "تم التوصيل"}
REFUNDED = {"succeeded", "completed", "refunded", "fully_refunded", "partially_refunded"}


class EvidenceError(ValueError):
    pass


def require(condition, reason):
    if not condition:
        raise EvidenceError(reason)


def amount(value):
    try:
        number = Decimal(str(value))
        require(number.is_finite() and number >= 0, "invalid_amount")
        require(number == number.quantize(Decimal("0.01")), "sub_cent_amount")
        return number
    except (InvalidOperation, TypeError):
        raise EvidenceError("invalid_amount") from None


def timestamp(value):
    try:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        require(parsed.tzinfo is not None and parsed.utcoffset() is not None, "timezone_required")
        return parsed.astimezone(timezone.utc)
    except (ValueError, TypeError) as exc:
        if isinstance(exc, EvidenceError):
            raise
        raise EvidenceError("invalid_event_date") from None


def canonical_identity(value):
    identity = str(value or "").strip()
    require(bool(identity) and len(identity) <= 200, "canonical_provider_id_required")
    require(not identity.startswith(("synthetic:", "aggregate:", "payout-fee:")), "aggregate_identity_not_postable")
    return identity


def project_source_evidence(order, payment, refund=None):
    """Read documented normalized/raw fields without rewriting source records.

    The normal Tabby/Tamara producers retain raw_payload and capture rows,
    rather than a captured_at field. Full recognition uses the LAST verified
    capture, with every capture amount/date present and their sum reconciled.
    Order creation, provider update and settlement dates are never substitutes.
    """
    order, payment = dict(order), dict(payment)
    raw = payment.get("raw_payload") or {}
    captures = raw.get("captures") or []
    if raw:
        total_evidence = raw.get("total_amount")
        source_currency = raw.get("currency") or (
            total_evidence.get("currency") if isinstance(total_evidence, dict) else None)
        require(source_currency == payment.get("currency"), "source_payment_currency_conflict")
        require(str(raw.get("id") or raw.get("order_id") or "") == payment.get("provider_id"),
                "source_payment_identity_conflict")
    if captures:
        total = amount(0)
        dates = []
        for capture in captures:
            require(isinstance(capture, dict), "capture_evidence_incomplete")
            value = capture.get("amount")
            if isinstance(value, dict):
                require(value.get("currency") == payment.get("currency"), "capture_currency_conflict")
                value = value.get("amount")
            total += amount(value)
            dates.append(timestamp(capture.get("created_at") or capture.get("captured_at")
                                   or capture.get("created_at_provider") or capture.get("date")))
        require(total == amount(payment.get("captured_amount")) == amount(payment.get("amount")),
                "capture_total_conflict")
        last_capture = max(dates)
        if payment.get("captured_at"):
            require(timestamp(payment["captured_at"]) == last_capture, "capture_date_conflict")
        payment["captured_at"] = last_capture.isoformat()
    if not payment.get("source") and raw and payment.get("synced_at"):
        payment["source"] = "payment_transactions.raw_payload"
    source = (order.get("raw_by_source") or {}).get("salla_direct") or {}
    if not order.get("delivered_at"):
        order["delivered_at"] = order.get("completed_at") or source.get("completed_at")
    if not order.get("delivered_at"):
        shipments = source.get("shipments") or []
        if shipments and all(isinstance(s, dict) and s.get("delivered_at") for s in shipments):
            order["delivered_at"] = max(timestamp(s["delivered_at"]) for s in shipments).isoformat()
        elif (source.get("shipping") or {}).get("delivered_at"):
            order["delivered_at"] = source["shipping"]["delivered_at"]
    if refund is not None:
        refund = dict(refund)
        if not refund.get("source") and refund.get("raw") and refund.get("synced_at"):
            refund["source"] = "payment_refunds.raw"
    return order, payment, refund


def qualify(owner, provider, order, payment, *, cutoff, refund=None, now=None):
    """Build a source-preserving event proposal, with no database writes.

    The operational producer must resolve documents within the tenant first.
    IDs come from provider payment/refund evidence, never from an order number,
    a file row number, or a generated substitute. A confirmed delivery time is
    separate from order creation and payment creation.
    """
    order, payment, refund = project_source_evidence(order, payment, refund)
    require(provider in PROVIDERS, "unsupported_provider")
    require(order.get("user_id") == payment.get("user_id") == owner, "owner_mismatch")
    require(payment.get("provider") == provider, "provider_mismatch")
    reference = str(order.get("order_number") or "").strip()
    require(reference and str(payment.get("order_reference_id") or payment.get("order_number") or "").strip() == reference,
            "order_identity_mismatch")
    require(str(order.get("payment_method") or "").strip().lower() in PROVIDERS[provider],
            "order_payment_method_mismatch")
    require(order.get("currency") == payment.get("currency") == "SAR", "sar_evidence_required")
    fulfilment_states = FULFILLED | (REFUNDED if refund is not None else set())
    capture_states = CAPTURED | (REFUNDED if refund is not None else set())
    require(str(order.get("order_status") or "").strip().lower() in fulfilment_states, "fulfilment_not_proven")
    require(str(payment.get("status") or "").lower() in capture_states, "capture_not_confirmed")
    require(not payment.get("synthesised") and not (payment.get("raw") or {}).get("_fetch_error"),
            "provider_evidence_unverified")
    provider_id = canonical_identity(payment.get("provider_id"))
    principal = amount(payment.get("captured_amount", payment.get("amount")))
    require(principal > 0 and principal == amount(payment.get("amount")), "capture_amount_conflict")
    require(principal == amount(order.get("total_amount")), "order_principal_conflict")
    original = (order.get("raw_by_source") or {}).get("excel") or {}
    if original:
        original_total = original.get("original_total_amount", original.get("total_amount"))
        require(original_total is not None and amount(original_total) == principal, "original_order_principal_conflict")
        require(original.get("original_currency", original.get("currency")) == "SAR", "original_currency_conflict")

    cut = timestamp(cutoff)
    clock = now or datetime.now(timezone.utc)
    captured_at = timestamp(payment.get("captured_at"))
    delivered_at = timestamp(order.get("delivered_at"))
    # Creation time cannot stand in for the recognition event.
    recognized_at = max(captured_at, delivered_at)
    require(captured_at <= clock and delivered_at <= clock, "future_source_event")
    require(recognized_at >= cut, "pre_cutover_recognition")
    if order.get("order_created_at"):
        created = timestamp(order["order_created_at"])
        require(created <= delivered_at and created <= captured_at, "event_date_conflict")
    require(not order.get("pre_cutover_qoyod_invoice_id"), "pre_cutover_invoice_requires_reclassification")

    kind = "sale"
    event_id = provider_id
    event_amount = principal
    source = {"order_id": order.get("id") or order.get("order_id"),
              "order_number": reference, "payment_document_id": payment.get("id"),
              "payment_source": payment.get("source") or payment.get("status_source"),
              "captured_at": captured_at.isoformat(), "delivered_at": delivered_at.isoformat(),
              "payment_evidence": payment.get("evidence") or {}}
    require(bool(source["payment_document_id"]) and bool(source["payment_source"]), "source_provenance_required")
    if refund is not None:
        require(refund.get("user_id") == owner and refund.get("provider") == provider, "refund_owner_provider_mismatch")
        require(refund.get("provider_payment_id") == provider_id, "refund_payment_mismatch")
        require(str(refund.get("order_reference_id") or reference) == reference, "refund_order_mismatch")
        require(refund.get("currency") == "SAR", "refund_currency_conflict")
        require(str(refund.get("status") or "").lower() in REFUNDED, "refund_not_confirmed")
        require(not refund.get("synthesised"), "aggregate_identity_not_postable")
        event_id = canonical_identity(refund.get("provider_refund_id"))
        event_amount = amount(refund.get("amount"))
        require(0 < event_amount <= principal, "refund_exceeds_sale")
        refunded_at = timestamp(refund.get("refunded_at"))
        require(recognized_at <= refunded_at <= clock, "refund_date_conflict")
        recognized_at = refunded_at
        kind = "refund"
        require(bool(refund.get("id")) and bool(refund.get("source")), "refund_provenance_required")
        source.update(refund_document_id=refund["id"], refund_source=refund["source"],
                      refund_evidence=refund.get("evidence") or {})
    event = {"kind": kind, "provider": provider, "provider_payment_id": provider_id,
             "canonical_event_id": event_id, "order_number": reference,
             "amount": str(event_amount), "sale_amount": str(principal), "currency": "SAR",
             "recognized_at": recognized_at.isoformat(), "cutover_at": cut.isoformat(),
             "source": source, "idempotency_key": f"bnpl_{kind}:{provider}:{event_id}"}
    event["source_fingerprint"] = hashlib.sha256(json.dumps(event, sort_keys=True, default=str).encode()).hexdigest()
    return event
