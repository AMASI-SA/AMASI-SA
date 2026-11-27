"""Synthetic-only private-vision preliminary bank receipt plausibility tests."""

from datetime import date
import pytest

from salla_order_creation.bank_receipt_preflight import (
    PrivateVisualObservation, ReceiptReviewDenied,
    assess_visible_bank_receipt,
)

HASH = "a" * 64
TODAY = date(2026, 11, 27)


def bank():
    return {
        "source": "salla_active_store_bank_details",
        "active": True,
        "store_bank_id": 123,
        "beneficiary_name": "Synthetic Merchant",
    }


def observation(**values):
    data = {
        "source": "approved_private_receipt_vision_v1",
        "image_sha256": HASH,
        "document_kind": "bank_transfer_receipt",
        "text_legible": True,
        "transfer_marked_completed": True,
        "suspicious_editing_visible": False,
        "currency": "SAR",
        "amount": "200.00",
        "transfer_date": "2026-11-27",
        "receiving_store_bank_id": 123,
        "beneficiary_name_visible": "Synthetic Merchant",
        "transaction_reference_visible": "ABC1234500",
    }
    data.update(values)
    return PrivateVisualObservation.model_validate(data)


def assess(obs=None, receiving=None):
    return assess_visible_bank_receipt(
        obs or observation(),
        expected_image_sha256=HASH,
        bank_details=receiving or bank(),
        today=TODAY,
    )


def test_visual_plausibility_is_never_proof_of_bank_credit():
    result = assess()
    assert result["state"] == "preliminary_pass_for_human_review"
    assert result["visual_only"] is True
    assert result["human_review_required"] is True
    assert result["bank_credit_confirmed"] is False
    assert result["claimed_amount_sar"] == "200.00"
    assert result["store_bank_id"] == 123
    assert result["image_sha256"] == HASH
    assert "ABC1234500" not in str(result)
    assert "Synthetic Merchant" not in str(result)


@pytest.mark.parametrize("edited", [
    {"source": "model_guessed"},
    {"document_kind": "bank_balance"},
    {"text_legible": False},
    {"transfer_marked_completed": False},
    {"suspicious_editing_visible": True},
    {"currency": "USD"},
    {"receiving_store_bank_id": 999},
    {"beneficiary_name_visible": "Unrelated Receiver"},
    {"amount": "NaN"},
    {"amount": "0"},
    {"amount": "-100"},
    {"amount": "100.001"},
    {"amount": "1000001"},
    {"transfer_date": "2026-12-01"},
    {"transfer_date": "2026-10-01"},
    {"transaction_reference_visible": "111111"},
])
def test_unsafe_incomplete_or_unrelated_receipt_not_auto_approved(edited):
    with pytest.raises(ReceiptReviewDenied):
        assess(observation(**edited))


def test_mismatched_immutable_image_digest_denied():
    with pytest.raises(ReceiptReviewDenied):
        assess(observation(image_sha256="b" * 64))


def test_inactive_or_model_supplied_bank_is_not_trusted():
    with pytest.raises(ReceiptReviewDenied):
        assess(receiving={**bank(), "active": False})
    with pytest.raises(ReceiptReviewDenied):
        assess(receiving={**bank(), "source": "assistant_guess"})


def test_amount_is_claimed_only_and_not_matched_to_uncreated_order_yet():
    row = assess(observation(amount="317.50"))
    assert row["claimed_amount_sar"] == "317.50"
    assert "actual_salla_order_total" not in row
    assert "paid" not in row["state"]
