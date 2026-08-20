import pytest

from store_delivery_domain import StoreDeliveryRuleError
from store_delivery_payment_evidence_routes import _detected_type, authoritative_outstanding_amount


def test_authoritative_remaining_amount_is_primary():
    assert authoritative_outstanding_amount({"remaining_amount": 250, "total_amount": 999, "paid_amount": 0}) == 250.0


def test_authoritative_explicit_zero_is_valid():
    assert authoritative_outstanding_amount({"remaining_amount": 0, "has_remaining_amount": False}) == 0.0


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
