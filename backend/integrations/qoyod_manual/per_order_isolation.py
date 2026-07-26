"""Per-order failure isolation policy for Plan-B automatic Qoyod sending.

A bad order must never disable automatic sending for healthy orders.  Only
systemic failures (credentials, Qoyod/Salla outage, networking, corrupt worker
state, or an unknown exception) may trip the global circuit breaker.
"""
from __future__ import annotations


# Business/data failures whose scope is exactly one order.  The worker persists
# them in qoyod_manual_auto_quarantines, skips that order, and continues with the
# rest of the batch.
ORDER_LOCAL_FAILURE_CODES = frozenset({
    # Payment facts / mapping for this order.
    "payment_method_unmapped",
    "receiving_bank_missing",
    "authoritative_payment_method_still_pending",
    "authoritative_order_missing_after_resync",
    "legacy_sender_inbox_row_missing",
    "legacy_sender_inbox_update_missed",
    "authoritative_payment_refresh_failed",

    # Order state / source-data issues.
    "not_completed",
    "no_salla_order_date",
    "before_floor_date",
    "zero_total_refused",
    "order_not_found",
    "product_id_missing",
    "duplicated_invoice_items_detected",

    # Product/order payload validation.
    "qoyod_actual_total_mismatch",
    "qoyod_actual_total_missing",
    "qoyod_payload_precision_unsupported",
    "qoyod_preflight_total_mismatch",
    "qoyod_preflight_payload_invalid",
    "totals_mismatch",
    "rounding_adjustment_product_missing",
    "invoice_created_payment_failed",
})


def install_per_order_isolation() -> None:
    """Extend the worker's order-local allow-list without replacing its logic."""
    from integrations.qoyod_manual import auto_send

    current = set(auto_send._PER_ORDER_MANUAL_REVIEW_CODES)
    current.update(ORDER_LOCAL_FAILURE_CODES)
    auto_send._PER_ORDER_MANUAL_REVIEW_CODES = frozenset(current)
