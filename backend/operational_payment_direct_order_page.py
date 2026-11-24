"""Reject a new provider receivable without original dated gateway capture.

Salla's native order detail can prove a cumulative paid amount and transaction
reference but frequently omits the *original* capture date. The authoritative
operational_balance_engine.reconcile calls provider_dates.recognition_day,
which rejects any NEW confirmed provider balance without dated capture facts.
Writing a provisional provider_order_balance receivable would cause the
following full refresh to reject/re-date it. Keep this source as review evidence,
never a financial posting.

Future posting on a dated capture uses operational_payment_direct_provider,
with its separate exact transaction, cutover, fee, source and CAS guards.
"""
from __future__ import annotations

from operational_payment_salla_order_page import native_paid_order_page


class FirstPageHold(ValueError):
    """Permanent no-money hold requiring original capture-date evidence."""


def apply_first_paid_order_page(state, *, raw, source, owner, order_number, as_of):
    """Fail closed before any CAS candidate can change financial totals."""
    if (not isinstance(state, dict) or state.get("owner_id") != owner
            or state.get("status") != "active" or not state.get("started_at")):
        raise FirstPageHold("operational_owner_or_start_incomplete")
    page = native_paid_order_page(
        raw, owner=owner, order_number=order_number, source=source)
    if page is None:
        raise FirstPageHold("salla_paid_order_page_unverified")
    # A successful Salla order page is NOT gateway captured_at. Even if the
    # amount, reference and saved source watermark are valid, recognition_day
    # would refuse a confirmed obligation until an original dated native
    # transaction proves the booking date. Do not post a surrogate date.
    raise FirstPageHold("paid_page_original_capture_date_required")
