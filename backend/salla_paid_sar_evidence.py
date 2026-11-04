"""Bind the SAR actually paid on Salla's order list to its original-currency order."""
import hashlib
import json
from decimal import Decimal

from operational_payment_evidence import _amount, _identity, _instant
from order_currency import SUPPORTED_ORDER_CURRENCIES


FIELD = "salla_paid_sar_evidence"
SCHEMA = "salla_order_list_paid_sar_v1"
_INVALID = (ValueError, TypeError, KeyError, AttributeError, ArithmeticError)


def _key(value):
    return _identity(value, "salla_payment_identity_invalid")


def _money(value, currency):
    if not isinstance(value, dict) or value.get("currency") != currency:
        raise ValueError("salla_payment_currency_missing")
    return _amount(value, currency)


def _signature(raw):
    status = raw.get("status")
    slug = status.get("slug") if isinstance(status, dict) else status
    if (not slug or slug in {"canceled", "cancelled", "restored", "refunded", "awaiting_payment", "pending_payment"}
            or raw.get("is_pending_payment") is True):
        raise ValueError("salla_payment_status_conflict")
    payment = raw.get("payment") or {}
    if any(str(s).strip().lower() in {"failed", "declined", "pending", "unpaid", "authorized", "authorised",
                                     "cancelled", "canceled", "refunded", "reversed", "voided"}
           for s in (raw.get("payment_status"), raw.get("payment_collection_status"),
                     payment.get("status"), payment.get("collection_status")) if s):
        raise ValueError("salla_payment_status_conflict")
    if raw.get("order_number") is not None and str(raw["order_number"]) != str(raw["reference_id"]):
        raise ValueError("salla_order_identity_conflict")
    gross = (raw.get("amounts") or {}).get("total") or raw.get("total")
    currency = gross["currency"]
    if currency == "SAR" or currency not in SUPPORTED_ORDER_CURRENCIES or raw.get("currency") not in (None, currency):
        raise ValueError("salla_payment_not_foreign")
    order_total = _money(gross, currency)
    if raw.get("total") is not None and _amount(raw["total"], currency) != order_total:
        raise ValueError("salla_order_total_conflict")
    native = raw["payment_methods"]
    if not isinstance(native, list) or not 0 < len(native) <= 100:
        raise ValueError("salla_payment_tenders_missing")
    legs = []
    for leg in native:
        value = _amount(leg["amount"], currency)
        if value <= 0 or leg.get("currency") not in (None, currency):
            raise ValueError("salla_payment_tender_invalid")
        legs.append((_key(leg["transaction_reference"]), _key(leg["payment_method"]).lower(), format(value, ".2f")))
    if len({ref for ref, _, _ in legs}) != len(legs):
        raise ValueError("salla_payment_reference_repeated")
    primary = _key(raw["payment_method"]).lower()
    if primary not in {method for _, method, _ in legs}:
        raise ValueError("salla_payment_method_conflict")
    value = sum((Decimal(leg[2]) for leg in legs), Decimal(0))
    if value > order_total:
        raise ValueError("salla_payment_exceeds_order_total")
    return {
        "order_id": _key(raw["id"]), "order_number": _key(raw["reference_id"]),
        "created_at": _instant(raw.get("date") or raw["created_at"]).isoformat(),
        "currency": currency, "native_total": format(order_total, ".2f"), "native_paid": format(value, ".2f"),
        "tender_sha256": hashlib.sha256(json.dumps([primary, sorted(legs)], separators=(",", ":")).encode()).hexdigest(),
    }


def _confirmed_paid(raw, currency):
    actions = raw["payment_actions"]
    rem, rf = actions["remaining_action"], actions["refund_action"]
    paid = _money(rem["paid_amount"], currency)
    if (paid <= 0 or _money(rf["paid_amount"], currency) != paid
            or _money(rf["refund_amount"], currency) != 0 or rf.get("refunded_transactions")
            or rem.get("has_remaining_amount") is not False
            or any(_amount(v, currency) != 0 for v in (rem.get("remaining_amount"), rf.get("pending_refund_amount"))
                   if v is not None)):
        raise ValueError("salla_payment_not_confirmed")
    return paid


def list_payment_evidence(owner, row):
    """Call only on a tenant-authenticated Salla order-list row."""
    try:
        signature = _signature(row)
        exchange = row.get("exchange_rate") or {}
        if exchange and (exchange.get("base_currency"), exchange.get("exchange_currency")) != ("SAR", signature["currency"]):
            return None
        paid = _confirmed_paid(row, "SAR")
        updated = row.get("updated_at") or row.get("date_updated")
        return {"schema": SCHEMA, "owner_id": _key(owner), **signature, "paid_sar": format(paid, ".2f"),
                "source_updated_at": _instant(updated).isoformat() if updated else None}
    except _INVALID:
        return None


def matching_sar_payment(source, raw, *, owner, native_paid=None):
    """Require matching actual payment facts; unrelated status edits alone cannot change the amount."""
    try:
        # A directly stored light-list snapshot can already contain the base-currency evidence.
        embedded = list_payment_evidence(owner, raw)
        proof = embedded or source.get(FIELD)
        if (not proof or proof.get("schema") != SCHEMA or proof.get("owner_id") != _key(owner)
                or source.get("user_id") != owner or _key(source["order_number"]) != proof["order_number"]
                or _key(source.get("order_id") or source.get("salla_order_id")) != proof["order_id"]
                or source.get("salla_order_id") is not None and _key(source["salla_order_id"]) != proof["order_id"]
                or any((source.get("g47_salla_snapshot") or {}).get(k) for k in ("cancelled","requires_authoritative_refresh"))):
            return None
        signature = _signature(raw)
        if any(proof.get(k) != v for k, v in signature.items()):
            return None
        if native_paid is not None and _amount(native_paid, signature["currency"]) != Decimal(signature["native_paid"]):
            return None
        if embedded is None and _confirmed_paid(raw, signature["currency"]) != Decimal(signature["native_paid"]):
            return None
        stamp = raw.get("updated_at") or raw.get("date_updated")
        if proof.get("source_updated_at") and (not stamp or _instant(stamp) != _instant(proof["source_updated_at"])):
            return None
        paid = _amount(proof["paid_sar"], "SAR")
        return paid if paid > 0 else None
    except _INVALID:
        return None
