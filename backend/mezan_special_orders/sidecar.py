"""Non-posting balance-overlay contract for an existing Salla sale.

Never mutate Salla total/paid, never count the sale twice. The future MZ2
adapter must bind the approved agreement and matching source revision before
using this projection or creating a receivable. This module does not write one.
"""
from __future__ import annotations

from .contracts import SettlementAdjustment
from .domain import DomainError


def collection_overlay(adjustment: SettlementAdjustment, *, current_source_revision: str,
                       source_total_minor: int, source_paid_minor: int,
                       local_confirmed_minor: int = 0,
                       already_reflected_in_salla_minor: int = 0) -> dict:
    amounts = (source_total_minor, source_paid_minor, local_confirmed_minor, already_reflected_in_salla_minor)
    if any(type(n) is not int or n < 0 or n > 10**12 for n in amounts):
        raise DomainError("invalid_balance_amount", 422)
    if current_source_revision != adjustment.source_revision:
        raise DomainError("salla_balance_snapshot_stale")
    if (source_total_minor, source_paid_minor) != (adjustment.source_total_minor, adjustment.source_paid_minor):
        raise DomainError("salla_balance_facts_changed")
    if already_reflected_in_salla_minor > min(local_confirmed_minor, source_paid_minor):
        raise DomainError("invalid_salla_payment_reconciliation")
    unreflected = local_confirmed_minor - already_reflected_in_salla_minor
    original_balance = source_total_minor - source_paid_minor
    extra = adjustment.amount_minor if adjustment.basis == "additional_agreed_charge" else 0
    gross_due = original_balance + extra
    if unreflected > gross_due:
        raise DomainError("overpayment_requires_reconciliation")
    outstanding = gross_due - unreflected
    return {"original_order_id": adjustment.original_order_id,
            "original_order_number": adjustment.original_order_number,
            "source_total_minor": source_total_minor, "source_paid_minor": source_paid_minor,
            "existing_source_balance_minor": original_balance,
            "additional_agreed_charge_minor": extra,
            "unreflected_local_collection_minor": unreflected,
            "cod_to_collect_minor": outstanding,
            "cod_to_collect_sar_minor": adjustment.fx.to_sar_minor(outstanding),
            "new_sales_order_count": 0, "salla_mutation_required": False,
            "new_receivable_required_minor": extra,
            "accounting_owner": "mezan_v2",
            "requires_verified_agreement": True}
