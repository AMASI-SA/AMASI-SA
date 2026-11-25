"""Safe evidence gate before any auto-confirmation from a provider statement.

A normalized payout line is not a proof of a *new* payment on its own.
Only a statement row with a native event reference matching an independently
confirmed transaction may pass; all ambiguous cases remain for accountant
review. This module is read-only and does not create financial movements.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Mapping


@dataclass(frozen=True)
class StatementMatch:
    eligible: bool
    reason: str
    reference: str | None = None


def _amount(value):
    try:
        number = Decimal(str(value))
        if not number.is_finite() or number <= 0 or number != number.quantize(Decimal(".01")):
            return None
        return format(number, ".2f")
    except (TypeError, ValueError, InvalidOperation):
        return None


def match_statement_to_native_event(row: Mapping, native: Mapping[str, Mapping]) -> StatementMatch:
    if not isinstance(row, Mapping) or row.get("blockers"):
        return StatementMatch(False, "statement_not_verified")
    kind = row.get("event_type")
    if kind not in {"sale", "refund"}:
        return StatementMatch(False, "not_a_customer_payment_event")
    if row.get("provider") not in {"tabby", "tamara", "salla"}:
        return StatementMatch(False, "unsupported_provider")
    if row.get("native_currency") != "SAR" or not _amount(row.get("principal_amount")):
        return StatementMatch(False, "currency_or_amount_invalid")
    reference = row.get("event_reference")
    if not isinstance(reference, str) or not reference.strip():
        return StatementMatch(False, "statement_event_identity_missing")
    if not isinstance(native, Mapping) or reference not in native:
        return StatementMatch(False, "native_event_not_found")
    event = native[reference]
    if not isinstance(event, Mapping):
        return StatementMatch(False, "native_event_invalid")
    if (str(event.get("order_number")) != str(row.get("order_number"))
            or event.get("provider") != row["provider"]
            or event.get("currency") != row["native_currency"]
            or _amount(event.get("amount")) != _amount(row.get("principal_amount"))
            or event.get("kind") != ("payment" if kind == "sale" else "refund")
            or event.get("status") not in {"captured", "paid", "refunded", "completed"}):
        return StatementMatch(False, "native_event_mismatch")
    # A capture cannot substantiate a refund even if its amount is identical.
    if kind == "refund" and event.get("status") not in {"refunded", "completed"}:
        return StatementMatch(False, "refund_execution_unproven")
    if not event.get("verified_source"):
        return StatementMatch(False, "independent_source_unverified")
    return StatementMatch(True, "exact_independent_match", reference)
