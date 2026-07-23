import pytest

from integrations.qoyod_manual.send import (
    ManualSendRefused,
    _extract_qoyod_invoice_total,
    _resolve_payment_amount,
    _validate_qoyod_actual_total,
    _within_amount_tolerance,
)


def test_extracts_qoyod_actual_total_from_invoice_response():
    response = {
        "invoice": {
            "id": 525,
            "total": 311.24,
        }
    }

    assert _extract_qoyod_invoice_total(response) == 311.24


def test_extracts_money_object_total():
    response = {
        "data": {
            "id": 525,
            "total_amount": {
                "amount": "311.24",
                "currency": "SAR",
            },
        }
    }

    assert _extract_qoyod_invoice_total(response) == 311.24


def test_positive_one_halalah_difference_is_allowed():
    result = _validate_qoyod_actual_total(
        actual_total=311.24,
        salla_total=311.23,
        invoice_id=525,
    )

    assert result == 311.24


def test_negative_one_halalah_difference_is_allowed():
    result = _validate_qoyod_actual_total(
        actual_total=311.22,
        salla_total=311.23,
        invoice_id=525,
    )

    assert result == 311.22


def test_exact_actual_parity_is_allowed():
    result = _validate_qoyod_actual_total(
        actual_total=311.23,
        salla_total=311.23,
        invoice_id=525,
    )

    assert result == 311.23


@pytest.mark.parametrize(
    ("qoyod_total", "salla_total", "expected_payment"),
    [
        (311.23, 311.23, 311.23),
        (311.24, 311.23, 311.23),
        (311.22, 311.23, 311.22),
    ],
)
def test_payment_never_exceeds_salla_collection_or_qoyod_invoice(
    qoyod_total,
    salla_total,
    expected_payment,
):
    assert _resolve_payment_amount(
        qoyod_total=qoyod_total,
        salla_collected_total=salla_total,
    ) == expected_payment


@pytest.mark.parametrize(
    ("actual_total", "expected_difference"),
    [
        (311.25, 0.02),
        (311.21, -0.02),
    ],
)
def test_difference_above_one_halalah_blocks_payment(
    actual_total,
    expected_difference,
):
    with pytest.raises(ManualSendRefused) as exc:
        _validate_qoyod_actual_total(
            actual_total=actual_total,
            salla_total=311.23,
            invoice_id=525,
        )

    assert exc.value.code == "qoyod_actual_total_mismatch"
    assert exc.value.extra["difference"] == expected_difference
    assert exc.value.extra["allowed_tolerance"] == 0.01


def test_missing_actual_total_blocks_payment():
    with pytest.raises(ManualSendRefused) as exc:
        _validate_qoyod_actual_total(
            actual_total=None,
            salla_total=311.23,
            invoice_id=525,
        )

    assert exc.value.code == "qoyod_actual_total_missing"


@pytest.mark.parametrize("difference", [0, 0.01, -0.01, "0.010"])
def test_public_amount_tolerance_includes_one_halalah(difference):
    assert _within_amount_tolerance(difference) is True


@pytest.mark.parametrize("difference", [0.02, -0.02, None, "invalid"])
def test_public_amount_tolerance_blocks_larger_or_invalid_values(difference):
    assert _within_amount_tolerance(difference) is False
