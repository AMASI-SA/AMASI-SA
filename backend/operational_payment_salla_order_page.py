"""Proof-only candidate from Salla's native *paid order page*, no captured_at.

Some verified paid native orders have payment_methods.transaction_reference,
an exact Salla paid/remaining balance and status under_review, but no
payment.paid_at. This adapter preserves a cumulative source observation
separate from actual gateway settlement and from dated individual captures.

It never writes money, sets captured_at, or uses status as proof alone.
Bank transfer and COD must remain outside provider auto-posting.
"""
from __future__ import annotations

from decimal import Decimal
from operational_payment_evidence import resolve_provider, _instant
from salla_payment_collection import provider_order_page_balance, provider_order_page_current

ONLINE = frozenset({"salla", "tamara", "tabby", "emkan", "paypal"})
BANK_AND_COD = frozenset({"bank", "bank_transfer", "cash_on_delivery", "cod"})
REVIEW_NAME = "بإنتظار المراجعة"


def native_paid_order_page(raw, *, owner, order_number, source):
    """Return one current, fully paid, identified order-page observation or None.

    Caller must still apply operational_order_page_payment.observed_balance
    with the *real* current CAS state and historical/source-overlap guards.
    """
    if not isinstance(raw, dict) or not isinstance(source, dict) or not owner:
        return None
    if str(raw.get("reference_id") or raw.get("order_number") or "") != str(order_number):
        return None
    oid = str(raw.get("id") or "")
    if not oid:
        return None
    status = raw.get("status") or {}
    if not isinstance(status, dict) or status.get("slug") != "under_review" or status.get("name") != REVIEW_NAME:
        return None
    if raw.get("is_pending_payment") is True:
        return None
    method = raw.get("payment_method")
    if not isinstance(method, str) or method.lower() in BANK_AND_COD:
        return None
    provider = resolve_provider(raw, require_known=False)
    if provider not in ONLINE:
        return None
    methods = raw.get("payment_methods")
    if not isinstance(methods, list) or len(methods) != 1 or not isinstance(methods[0], dict):
        return None
    native = methods[0]
    reference = native.get("transaction_reference")
    if not isinstance(reference, str) or not reference.strip() or len(reference) > 256:
        return None
    actions = raw.get("payment_actions") or {}
    remaining = actions.get("remaining_action") if isinstance(actions, dict) else None
    if not isinstance(remaining, dict) or remaining.get("has_remaining_amount") is not False:
        return None
    amounts = raw.get("amounts") or {}
    if not isinstance(amounts, dict) or not isinstance(amounts.get("total"), dict):
        return None
    total = amounts["total"]
    if total.get("currency") != "SAR":
        return None
    try:
        gross = Decimal(str(total["amount"]))
        original_paid = Decimal(str(native["amount"]))
        if not gross.is_finite() or gross <= 0 or original_paid != gross:
            return None
        balance = provider_order_page_balance(raw, "SAR", total)
        if balance is None or Decimal(balance["paid"]) != gross or balance["remaining"] != "0.00":
            return None
    except (ValueError, TypeError, KeyError, ArithmeticError):
        return None
    if not provider_order_page_current(
        source, raw, owner=owner, order_number=str(order_number), provider_order_id=oid):
        return None
    try:
        observed_at = _instant(raw.get("updated_at") or raw.get("date_updated")).isoformat()
    except (ValueError, TypeError):
        return None
    return {
        "source": "canonical_salla_order_page",
        "owner_id": owner, "order_id": oid, "order_number": str(order_number),
        "provider": provider, "currency": "SAR", "gross": format(gross, ".2f"),
        "paid": format(gross, ".2f"), "remaining": "0.00",
        "transaction_reference": reference,
        "source_updated_at": observed_at,
        # Date represents a native order-page observation, NOT a settlement
        # or original gateway capture instant.
        "date_basis": "source_observation_only",
    }
