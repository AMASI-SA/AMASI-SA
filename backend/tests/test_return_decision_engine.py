"""Pure tests for the Mezan item-level return decision engine."""
import pytest
from pydantic import ValidationError

from return_decision_engine import (
    ReturnDecisionInput,
    ReturnItemSelection,
    build_return_decision_report,
    extract_salla_return_shipments,
)


def item(**overrides):
    values = {
        "order_item_id": "899615803",
        "product_id": "11313",
        "sku": "AMS11313",
        "name": "محفظة الجواز",
        "quantity_ordered": 3,
        "quantity_return": 1,
        "unit_sale_amount": 100.0,
        "unit_tax_amount": 15.0,
        "unit_cost": 35.0,
        "expected_recoverable_value": 35.0,
        "sellable_probability": 1.0,
        "refurbishment_cost_per_unit": 0.0,
    }
    values.update(overrides)
    return ReturnItemSelection(**values)


def test_partial_return_keeps_unselected_quantity_immutable():
    selected = item(quantity_ordered=3, quantity_return=1)

    assert selected.quantity_ordered == 3
    assert selected.quantity_return == 1
    assert selected.quantity_ordered - selected.quantity_return == 2


def test_return_quantity_cannot_exceed_original_quantity():
    with pytest.raises(ValidationError):
        item(quantity_ordered=2, quantity_return=3)


def test_same_order_item_cannot_be_selected_twice():
    with pytest.raises(ValidationError, match="duplicate_return_item"):
        ReturnDecisionInput(
            reason_code="customer_changed_mind",
            items=[item(), item(quantity_return=2)],
        )


def test_recommends_customer_keep_item_when_retrieval_is_uneconomic():
    report = build_return_decision_report(
        ReturnDecisionInput(
            reason_code="defective",
            requested_resolution="replacement",
            merchant_fault=True,
            items=[
                item(
                    unit_cost=20,
                    expected_recoverable_value=20,
                )
            ],
            return_shipping_quote=18,
            inspection_handling_cost=5,
            replacement_item_cost=20,
            replacement_shipping_cost=12,
        )
    )

    assert report.retrieval_net_benefit == pytest.approx(-3)
    assert report.recommended_option == "keep_replace"


def test_recommends_return_when_recovery_value_exceeds_return_cost():
    report = build_return_decision_report(
        ReturnDecisionInput(
            reason_code="customer_changed_mind",
            requested_resolution="refund",
            items=[
                item(
                    expected_recoverable_value=80,
                )
            ],
            return_shipping_quote=15,
            customer_return_shipping_charge=5,
            inspection_handling_cost=5,
            refund_amount=115,
        )
    )

    assert report.merchant_return_shipping_cost == pytest.approx(10)
    assert report.retrieval_net_benefit == pytest.approx(65)
    assert report.recommended_option == "return_refund"


def test_merchant_fault_never_uses_customer_shipping_charge_in_report():
    report = build_return_decision_report(
        ReturnDecisionInput(
            reason_code="wrong_item",
            requested_resolution="refund",
            merchant_fault=True,
            items=[item()],
            return_shipping_quote=25,
            customer_return_shipping_charge=25,
            inspection_handling_cost=0,
            refund_amount=115,
        )
    )

    assert report.customer_return_shipping_charge_allowed is False
    assert report.merchant_return_shipping_cost == pytest.approx(25)


def test_salla_return_packages_are_never_authoritative_item_selection():
    order = {
        "raw_by_source": {
            "salla_direct": {
                "shipments": [
                    {
                        "id": 490496714,
                        "type": "return",
                        "tracking_number": "6071926888957",
                        "packages": [
                            {
                                "item_id": "one",
                                "quantity": 1,
                            },
                            {
                                "item_id": "two",
                                "quantity": 2,
                            },
                        ],
                    }
                ]
            }
        }
    }

    shipments = extract_salla_return_shipments(order)

    assert len(shipments) == 1
    assert shipments[0]["package_count_from_salla"] == 2
    assert shipments[0]["packages_are_authoritative_items"] is False
    assert "packages" not in shipments[0]
