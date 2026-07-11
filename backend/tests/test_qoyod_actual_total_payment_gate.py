import pytest

from integrations.qoyod_manual.send import (
    ManualSendRefused,
    _extract_qoyod_invoice_total,
    _validate_qoyod_actual_total,
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


def test_one_halalah_actual_difference_blocks_payment():
    with pytest.raises(ManualSendRefused) as exc:
        _validate_qoyod_actual_total(
            actual_total=311.24,
            salla_total=311.23,
            invoice_id=525,
        )

    assert exc.value.code == "qoyod_actual_total_mismatch"
    assert exc.value.extra["qoyod_actual_total"] == 311.24
    assert exc.value.extra["salla_total"] == 311.23
    assert exc.value.extra["difference"] == 0.01


def test_exact_actual_parity_is_allowed():
    result = _validate_qoyod_actual_total(
        actual_total=311.23,
        salla_total=311.23,
        invoice_id=525,
    )

    assert result == 311.23


def test_missing_actual_total_blocks_payment():
    with pytest.raises(ManualSendRefused) as exc:
        _validate_qoyod_actual_total(
            actual_total=None,
            salla_total=311.23,
            invoice_id=525,
        )

    assert exc.value.code == "qoyod_actual_total_missing"
