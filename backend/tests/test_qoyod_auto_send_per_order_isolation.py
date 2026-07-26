"""Regression coverage for Plan-B per-order failure isolation."""
from qoyod_auto_per_order_isolation import ORDER_LOCAL_FAILURE_CODES


def test_payment_and_order_data_failures_are_order_local():
    required = {
        "payment_method_unmapped",
        "receiving_bank_missing",
        "authoritative_payment_method_still_pending",
        "authoritative_order_missing_after_resync",
        "legacy_sender_inbox_row_missing",
        "legacy_sender_inbox_update_missed",
        "authoritative_payment_refresh_failed",
        "not_completed",
        "zero_total_refused",
        "product_id_missing",
        "duplicated_invoice_items_detected",
    }
    assert required <= ORDER_LOCAL_FAILURE_CODES


def test_systemic_failures_are_not_order_local():
    systemic = {
        "qoyod_credentials_missing",
        "qoyod_http_error",
        "salla_status_refresh_failed",
        "unhandled_exception",
    }
    assert ORDER_LOCAL_FAILURE_CODES.isdisjoint(systemic)
