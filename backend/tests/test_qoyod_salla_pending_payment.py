import pytest

from integrations.qoyod.candidate_orders import (
    PAYMENT_ELIGIBLE,
    PAYMENT_INELIGIBLE,
    PAYMENT_NEEDS_LIVE_VERIFICATION,
    payment_eligibility,
)
from salla_integration.sync import _salla_order_to_doc


@pytest.mark.parametrize(
    "payment_method",
    ["mada", "credit_card", "tamara_installment", "tabby_installment"],
)
def test_salla_non_pending_flag_proves_electronic_payment(payment_method):
    row = {
        "payment_method": payment_method,
        "is_pending_payment": False,
    }

    assert payment_eligibility(row) == PAYMENT_ELIGIBLE


def test_salla_pending_flag_blocks_electronic_payment():
    assert payment_eligibility(
        {
            "payment_method": "mada",
            "is_pending_payment": True,
            "payment_status": "paid",
        }
    ) == PAYMENT_INELIGIBLE


def test_non_pending_flag_does_not_relax_bank_transfer_guard():
    assert payment_eligibility(
        {
            "payment_method": "bank_transfer",
            "is_pending_payment": False,
        }
    ) == PAYMENT_NEEDS_LIVE_VERIFICATION


@pytest.mark.parametrize(
    "contradiction",
    [
        {"payment_collection_status": "partial"},
        {"remaining_amount": 10},
        {"paid_amount": 10, "total_amount": 100},
    ],
)
def test_non_pending_flag_cannot_override_contradictory_evidence(contradiction):
    row = {
        "payment_method": "mada",
        "is_pending_payment": False,
        **contradiction,
    }

    assert payment_eligibility(row) == PAYMENT_INELIGIBLE


@pytest.mark.parametrize("flag", [True, False])
def test_salla_mapper_preserves_pending_payment_boolean(flag):
    doc = _salla_order_to_doc(
        {
            "id": 123,
            "reference_id": "273000002",
            "is_pending_payment": flag,
        }
    )

    assert doc["is_pending_payment"] is flag


def test_salla_mapper_rejects_non_boolean_pending_payment_value():
    doc = _salla_order_to_doc(
        {
            "id": 123,
            "reference_id": "273000002",
            "is_pending_payment": "false",
        }
    )

    assert doc["is_pending_payment"] is None
