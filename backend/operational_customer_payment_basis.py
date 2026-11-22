"""Keep proved paid principal separate from later commercial order changes.

This is a pure evidence reader. Refund hints, return status and current order
totals do not execute a refund, create a collection, or establish a fee credit.
The strict general payment normalizer remains the authority for references,
transaction binding and capture dates.
"""
from copy import deepcopy
from decimal import Decimal
from hashlib import sha256
import json

from operational_payment_evidence import (
    _amount, _identity, _instant, _method_rows, _object, _single, resolve_provider,
)


ADJUSTED_PAYMENT_SOURCE = "canonical_salla_adjusted_payment"
ZERO = Decimal(0)


def _cash(value):
    return format(value, ".2f")


def is_adjusted_payment_balance(page, currency):
    """A cumulative paid proof with a separately disclosed commercial total."""
    if (not isinstance(page, dict) or page.get("source") != ADJUSTED_PAYMENT_SOURCE
            or page.get("currency") != currency or page.get("remaining") is not None):
        return False
    try:
        _identity(page.get("transaction_reference"), "provider_payment_reference_missing")
        return _amount(page["paid"], currency) > ZERO and _amount(page["gross"], currency) >= ZERO
    except (ValueError, KeyError, TypeError):
        return False


def _refund_observation(raw, payment, refund, currency):
    hints = [_amount(value, currency) for value in (
        raw.get("refunded_amount"), payment.get("refunded_amount"),
        refund.get("refund_amount"), refund.get("pending_refund_amount"),
    ) if value is not None]
    transactions = refund.get("refunded_transactions")
    if transactions is not None:
        if not isinstance(transactions, list) or len(transactions) > 100:
            raise ValueError("provider_payment_shape_invalid")
        amounts = []
        for row in transactions:
            if not isinstance(row, dict):
                raise ValueError("provider_payment_shape_invalid")
            amounts.append(_amount(row.get("amount"), currency))
        hints.append(sum(amounts, ZERO))
    # These fields can describe the same refund. Never add overlapping totals.
    return max(hints, default=ZERO)


def confirmed_paid_basis(raw, currency, total, created_at, as_of, *, owner,
                         order_number, provider_order_id, source, rows=()):
    """Return independent paid evidence, or None if its scope is not proved.

    Amounts are strings in the original currency. Suggestions are observations
    before local confirmed refunds/collections are applied by the caller. This
    function performs no reads or writes and never mutates its arguments.
    """
    from operational_provider_transaction_evidence import normalize_transaction_payment
    from salla_payment_collection import provider_order_page_current

    try:
        raw, source = _object(raw), _object(source)
        provider = resolve_provider(raw, require_known=True)
        if not provider or not provider_order_page_current(source, raw, owner=owner,
                order_number=order_number, provider_order_id=provider_order_id):
            return None
        commercial = _amount(total, currency)
        amounts = _object(raw.get("amounts"))
        payment = _object(raw.get("payment"))
        actions = _object(raw.get("payment_actions"))
        refund = _object(actions.get("refund_action"))
        remaining = _object(actions.get("remaining_action"))
        totals = [value for value in (raw.get("total"), raw.get("total_amount"), amounts.get("total"))
                  if value is not None]
        if (not totals or any(_amount(value, currency) != commercial for value in totals)
                or any(container.get("currency") not in (None, currency)
                       for container in (raw, amounts, payment, refund, remaining))):
            return None
        paid_values = [_amount(container["paid_amount"], currency)
                       for container in (raw, payment, refund, remaining)
                       if container.get("paid_amount") is not None]
        paid = _single(paid_values, "provider_payment_amount_conflict")
        if paid is None or paid <= ZERO:
            return None
        native = _method_rows(raw, provider, currency, page_balance=False)
        if native and native[0][1] != paid:
            return None
        refs = [_identity(payment[key], "provider_payment_reference_missing")
                for key in ("reference", "transaction_reference") if payment.get(key) is not None]
        refs += [native[0][0]] if native else []
        reference = _single(refs, "provider_payment_reference_conflict")
        if not reference:
            return None
        states = [value for value in (payment.get("status"), payment.get("collection_status"),
                  raw.get("payment_status"), raw.get("payment_collection_status")) if value is not None]
        if any(not isinstance(value, str) for value in states):
            return None
        states = {value.strip().lower() for value in states}
        if states & {"unpaid", "authorized", "authorised", "failed", "declined", "voided",
                     "reversed", "refunded", "cancelled", "canceled", "غير مدفوع"}:
            return None
        complete_capture = (payment.get("status") in {"paid", "captured"}
                            and payment.get("paid_amount") is not None and payment.get("paid_at") is not None)
        # A native paid observation must be tied to its original transaction.
        # A method label or order completion by itself proves no customer cash.
        if not complete_capture and not (native and any(
                container.get("paid_amount") is not None for container in (refund, remaining))):
            return None
        if raw.get("is_pending_payment") is True and commercial <= paid:
            return None
        created, observed, now = (_instant(created_at),
            _instant(raw.get("updated_at") or raw.get("date_updated")), _instant(as_of))
        if not created <= observed <= now:
            return None
        hint = _refund_observation(raw, payment, refund, currency)
        if hint > paid:
            return None
        for container in (raw, payment, remaining):
            if container.get("remaining_amount") is not None:
                _amount(container["remaining_amount"], currency)
            if container.get("has_remaining_amount") is not None and type(container["has_remaining_amount"]) is not bool:
                return None
        adjustment = {
            "order_total": _cash(commercial), "paid_amount": _cash(paid),
            "refund_hint_amount": _cash(hint),
            "suggested_refund_amount": _cash(max(paid - commercial, hint, ZERO)),
            "suggested_collection_amount": _cash(max(commercial - paid, ZERO)),
            "evidence_source": ADJUSTED_PAYMENT_SOURCE,
        }
        proof = {"owner": owner, "order_id": provider_order_id, "order_number": order_number,
                 "provider": provider, "currency": currency, "reference": reference,
                 "source_updated_at": observed.isoformat(), **adjustment}
        proof_hash = sha256(json.dumps(proof, ensure_ascii=False, sort_keys=True,
                                      separators=(",", ":")).encode()).hexdigest()
        # Project only the independent tender into the strict capture reader.
        # The actual order/refund payload is preserved unchanged above and the
        # commercial differences remain explicit in customer_adjustment.
        canonical = deepcopy(raw)
        canonical.pop("status", None)
        canonical.pop("refunded_amount", None)
        canonical.pop("is_pending_payment", None)
        canonical["payment"] = deepcopy(payment)
        canonical["payment"].pop("refunded_amount", None)
        canonical["payment"]["paid_amount"] = _cash(paid)
        canonical["payment_actions"] = {
            "refund_action": {"paid_amount": _cash(paid)},
            "remaining_action": {"paid_amount": _cash(paid), "remaining_amount": "0.00"},
        }
        normalized, issues = normalize_transaction_payment(canonical, currency, _cash(paid), created_at, as_of,
            owner=owner, order_number=order_number, provider_order_id=provider_order_id, rows=rows,
            page_balance={"paid": _cash(paid), "remaining": "0.00"})
        if normalized is None or any(issue.get("code") != "provider_capture_evidence_incomplete" for issue in issues):
            return None
        normalized.update(gross=_cash(paid), customer_adjustment={**adjustment, "proof_hash": proof_hash})
        normalized["order_page_balance"] = {
            "source": ADJUSTED_PAYMENT_SOURCE, "owner_id": owner, "order_id": provider_order_id,
            "order_number": order_number, "provider": provider, "currency": currency,
            "gross": _cash(commercial), "paid": _cash(paid), "remaining": None,
            "transaction_reference": reference, "source_updated_at": observed.isoformat(),
        }
        normalized.pop("order_page_reference", None)
        return {"provider": provider, "currency": currency, **adjustment,
                "payment": normalized, "proof_hash": proof_hash}
    except (ValueError, KeyError, TypeError):
        return None
