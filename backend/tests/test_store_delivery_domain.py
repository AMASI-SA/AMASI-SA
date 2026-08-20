import pytest

from store_delivery_domain import (
    PAYMENT_METHOD_BANK_TRANSFER,
    PAYMENT_METHOD_CARD_TERMINAL,
    PAYMENT_METHOD_CASH,
    StoreDeliveryRuleError,
    assert_driver_can_take_shipment,
    assignment_snapshot,
    collection_requirements,
    driver_earning,
)


def _driver(**overrides):
    row = {
        "id": "driver-sami",
        "name": "سامي",
        "status": "active",
        "city": "الرياض",
        "region": "شمال الرياض",
        "district": "الياسمين",
        "street": "أنس بن مالك",
        "coverage_mode": "city",
        "delivery_fee": 20,
    }
    row.update(overrides)
    return row


def test_v1_city_match_allows_assignment_and_ignores_optional_future_scope():
    assert_driver_can_take_shipment(driver=_driver(), shipping_city=" الرياض ")


def test_v1_city_mismatch_is_rejected_even_if_driver_is_active():
    with pytest.raises(StoreDeliveryRuleError, match="driver_city_mismatch"):
        assert_driver_can_take_shipment(driver=_driver(), shipping_city="جدة")


def test_inactive_driver_is_rejected():
    with pytest.raises(StoreDeliveryRuleError, match="driver_inactive"):
        assert_driver_can_take_shipment(
            driver=_driver(status="inactive"), shipping_city="الرياض"
        )


def test_assignment_captures_fee_snapshot():
    snapshot = assignment_snapshot(driver=_driver(delivery_fee=20), shipping_city="الرياض")
    assert snapshot["delivery_fee_snapshot"] == 20.0

    # A later price edit must not mutate the already-created assignment snapshot.
    _driver_after_edit = _driver(delivery_fee=25)
    assert _driver_after_edit["delivery_fee"] == 25
    assert snapshot["delivery_fee_snapshot"] == 20.0


def test_driver_earning_is_zero_until_delivered_then_uses_snapshot():
    assignment = {"delivery_fee_snapshot": 20}
    assert driver_earning(assignment=assignment, delivered=False) == 0.0
    assert driver_earning(assignment=assignment, delivered=True) == 20.0


def test_cash_collection_becomes_driver_cod_custody():
    result = collection_requirements(
        outstanding_amount=250,
        payment_method=PAYMENT_METHOD_CASH,
    )
    assert result["cod_custody_amount"] == 250.0
    assert result["receipt_required"] is False
    assert result["review_status"] == "not_required"


@pytest.mark.parametrize(
    "method,bank_required",
    [
        (PAYMENT_METHOD_CARD_TERMINAL, False),
        (PAYMENT_METHOD_BANK_TRANSFER, True),
    ],
)
def test_non_cash_collection_requires_receipt_and_accountant_review(method, bank_required):
    result = collection_requirements(outstanding_amount=250, payment_method=method)
    assert result["receipt_required"] is True
    assert result["bank_account_required"] is bank_required
    assert result["review_status"] == "pending_accountant_review"
    assert result["cod_custody_amount"] == 0.0


def test_paid_order_needs_no_collection_method():
    result = collection_requirements(outstanding_amount=0, payment_method=None)
    assert result["amount"] == 0.0
    assert result["payment_method"] is None


def test_unpaid_order_cannot_be_delivered_without_collection_method():
    with pytest.raises(StoreDeliveryRuleError, match="collection_method_required"):
        collection_requirements(outstanding_amount=250, payment_method=None)
