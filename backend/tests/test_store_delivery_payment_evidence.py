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



def _partial_cod_order():
    return {
        "payment_method": "cod", "payment_status": "partial", "payment_collection_status": "partial",
        "remaining_amount": 0, "has_remaining_amount": False, "total_amount": 300, "paid_amount": 0,
        "raw_by_source": {"salla_direct": {
            "payment_method": "cod", "amounts": {"total": {"amount": 300, "currency": "SAR"}},
            "payment": {"status": "partial"},
            "payment_actions": {"remaining_action": {"remaining_amount": None, "has_remaining_amount": False,
                "paid_amount": {"amount": 50, "currency": "SAR"}}},
        }},
    }


def test_partial_cod_uses_label_balance_instead_of_normalized_zero():
    from store_courier_balance import cod_balance_when_salla_omits_remaining
    order = _partial_cod_order()
    raw = order["raw_by_source"]["salla_direct"]
    label = cod_balance_when_salla_omits_remaining(raw, raw["payment_actions"]["remaining_action"])
    assert label["amount"] == 250
    assert authoritative_outstanding_amount(order) == label["amount"]


def test_raw_explicit_cod_balance_recovers_normalized_zero():
    order = _partial_cod_order()
    order["raw_by_source"]["salla_direct"]["payment_actions"]["remaining_action"]["remaining_amount"] = {"amount": 250, "currency": "SAR"}
    assert authoritative_outstanding_amount(order) == 250


def test_uncertain_partial_cod_does_not_appear_paid():
    order = _partial_cod_order()
    del order["raw_by_source"]["salla_direct"]["payment_actions"]["remaining_action"]["paid_amount"]
    with pytest.raises(StoreDeliveryRuleError, match="authoritative_outstanding_amount_unavailable"):
        authoritative_outstanding_amount(order)


def test_cod_currency_conflict_does_not_become_zero():
    order = _partial_cod_order()
    order["raw_by_source"]["salla_direct"]["payment_actions"]["remaining_action"]["paid_amount"]["currency"] = "QAR"
    with pytest.raises(StoreDeliveryRuleError, match="authoritative_outstanding_amount_unavailable"):
        authoritative_outstanding_amount(order)


def test_paid_canonical_order_cannot_recollect_stale_provider_cod():
    order = _partial_cod_order()
    order["payment_status"] = "paid"
    assert authoritative_outstanding_amount(order) == 0


def test_explicit_zero_survives_a_raw_shipping_only_cod_snapshot():
    order = {"remaining_amount": "0.00", "raw_by_source": {"salla_direct": {
        "payment_method": "cod", "amounts": {"total": {"amount": "0.00", "currency": "SAR"}},
        "shipping": {"status": "delivered"},
    }}}
    assert authoritative_outstanding_amount(order) == 0
