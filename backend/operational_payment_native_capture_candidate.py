"""Read-only recovery of a DATED, owner-bound Salla native transaction.

A Salla order detail may show an exact paid amount and transaction reference
without payment.paid_at. The separate authenticated /transactions observations
can sometimes establish the original capture timestamp. This preview never
posts money, changes a saved order, or guesses the date from a page update.

Any incomplete source batch, stale order identity, duplicate transaction,
refunded/partial amount or missing original date fails closed. A separate CAS
posting path may consume this only after owner/signed-event, prior settlement,
opening and cross-order reference checks at commit.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from operational_payment_evidence import _instant
from operational_provider_transaction_evidence import normalize_transaction_payment
from salla_payment_collection import provider_order_page_current

PROVIDERS = frozenset({"salla", "tabby", "tamara", "emkan", "paypal"})


def recovered_dated_native_capture(*, source, raw, rows, owner, order_number, as_of):
    """Return one verified candidate or fail closed with an issue code.

    Input rows come from the exact owner/order's native transaction collection;
    any enclosing source read must be bounded and ownership checked again.
    This function is purely a validation preview, NOT an admission to money.
    """
    rejected = lambda reason: {
        "status":"not_verified", "reason":reason, "posting_permitted":False,
    }
    if not all(type(v) is str and v.strip() for v in (owner, order_number, as_of)):
        return rejected("transaction_owner_or_date_missing")
    if not isinstance(source, dict) or not isinstance(raw, dict) or not isinstance(rows, list):
        return rejected("transaction_source_shape_invalid")
    if not 0 < len(rows) <= 32:
        return rejected("transaction_rows_incomplete")
    order_id = str(raw.get("id") or "")
    if not order_id or str(raw.get("reference_id") or raw.get("order_number") or "") != order_number:
        return rejected("transaction_order_identity_conflict")
    if not provider_order_page_current(
        source, raw, owner=owner, order_number=order_number,
        provider_order_id=order_id,
    ):
        return rejected("transaction_salla_source_stale")
    for row in rows:
        if (not isinstance(row, dict)
                or row.get("user_id") != owner
                or row.get("source") != "salla_transactions"
                or row.get("order_number") != order_number
                or str(row.get("provider_order_id")) != order_id):
            return rejected("transaction_owner_order_scope_conflict")
    currency = (raw.get("amounts") or {}).get("total")
    if not isinstance(currency, dict) or currency.get("currency") != "SAR":
        return rejected("transaction_currency_unsupported")
    created = raw.get("created_at") or raw.get("date")
    if created is None:
        return rejected("transaction_original_order_clock_missing")
    try:
        at = _instant(as_of)
        original = _instant(created)
        if original > at:
            return rejected("transaction_original_order_clock_invalid")
        from operational_provider_capture_batches import expand_batches
        # Incomplete, malformed or mismatched envelopes must never allow the
        # first valid row to silently dominate the full historical evidence.
        proof_rows = expand_batches(rows, max_rows=32)
        normalized, issues = normalize_transaction_payment(
            raw, "SAR", currency, created, as_of, owner=owner,
            order_number=order_number, provider_order_id=order_id,
            rows=proof_rows,
        )
        if issues or not isinstance(normalized, dict):
            return rejected("transaction_native_evidence_incomplete")
        captures = normalized.get("captures")
        if (normalized.get("provider") not in PROVIDERS
                or not isinstance(captures, list) or len(captures) != 1
                or normalized.get("refunds")):
            return rejected("transaction_not_single_full_capture")
        capture = captures[0]
        if (not isinstance(capture, dict)
                or not isinstance(capture.get("id"), str)
                or not capture["id"].strip()):
            return rejected("transaction_capture_reference_missing")
        amount = Decimal(str(capture["amount"]))
        gross = Decimal(str(normalized["gross"]))
        captured = _instant(capture["captured_at"])
        if (not amount.is_finite() or amount <= 0 or amount != gross
                or not original <= captured <= at):
            return rejected("transaction_capture_amount_or_date_conflict")
        return {
            "status":"verified_native_capture",
            "posting_permitted":False,
            "order_id":order_id,"provider":normalized["provider"],
            "transaction_reference":capture["id"],
            "original_captured_at":captured.isoformat(),
            "amount":format(amount,".2f"),
            "currency":"SAR",
            "evidence_basis":"owner_bound_native_salla_transactions",
        }
    except (ValueError, TypeError, KeyError, OverflowError, InvalidOperation):
        return rejected("transaction_native_evidence_incomplete")
