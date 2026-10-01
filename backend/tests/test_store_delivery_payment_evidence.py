import pytest

from store_delivery_domain import StoreDeliveryRuleError
from store_delivery_payment_evidence_routes import (
    CUSTOMER_CONVERSATION_EVIDENCE,
    DELIVERY_PROOFS,
    RECEIPTS,
    _detected_type,
    authoritative_outstanding_amount,
)


def test_authoritative_remaining_amount_is_primary():
    assert authoritative_outstanding_amount({"remaining_amount": 250, "total_amount": 999, "paid_amount": 0}) == 250.0


def test_authoritative_explicit_zero_is_valid():
    assert authoritative_outstanding_amount({"remaining_amount": 0, "has_remaining_amount": False}) == 0.0


def test_authoritative_uncollected_cod_uses_same_total_fallback_as_order_review():
    order = {
        "payment_method": "الدفع عند الاستلام",
        "payment_status": "",
        "payment_collection_status": "unknown",
        "paid_amount": 0,
        "remaining_amount": 0,
        "has_remaining_amount": False,
        "total_amount": 683.24,
        "raw_by_source": {
            "salla_direct": {
                "payment_method": {"code": "cod", "name": "الدفع عند الاستلام"},
                "payment": {"status": ""},
                "payment_actions": {
                    "remaining_action": {
                        "paid_amount": {"amount": 0, "currency": "SAR"},
                        "remaining_amount": None,
                        "has_remaining_amount": False,
                    }
                },
            }
        },
    }
    assert authoritative_outstanding_amount(order) == 683.24


def test_authoritative_cod_fallback_does_not_override_paid_order():
    order = {
        "payment_method": "cod",
        "payment_status": "paid",
        "payment_collection_status": "paid",
        "paid_amount": 683.24,
        "remaining_amount": 0,
        "has_remaining_amount": False,
        "total_amount": 683.24,
        "raw_by_source": {
            "salla_direct": {
                "payment_method": {"code": "cod"},
                "payment": {"status": "paid"},
                "payment_actions": {
                    "remaining_action": {
                        "remaining_amount": None,
                        "has_remaining_amount": False,
                    }
                },
            }
        },
    }
    assert authoritative_outstanding_amount(order) == 0.0


def test_authoritative_total_minus_paid_fallback():
    assert authoritative_outstanding_amount({"total_amount": 300, "paid_amount": 50}) == 250.0


def test_paid_status_is_zero_when_remaining_not_present():
    assert authoritative_outstanding_amount({"payment_status": "paid", "total_amount": 250}) == 0.0


def test_missing_authoritative_amount_fails_closed():
    with pytest.raises(StoreDeliveryRuleError, match="authoritative_outstanding_amount_unavailable"):
        authoritative_outstanding_amount({"payment_status": "pending"})


def test_receipt_signature_detection():
    assert _detected_type(b"\xff\xd8\xffabc") == "image/jpeg"
    assert _detected_type(b"\x89PNG\r\n\x1a\nabc") == "image/png"
    assert _detected_type(b"RIFFxxxxWEBPabc") == "image/webp"
    assert _detected_type(b"not-an-image") is None


def test_delivery_evidence_channels_are_distinct():
    assert len({RECEIPTS, DELIVERY_PROOFS, CUSTOMER_CONVERSATION_EVIDENCE}) == 3
