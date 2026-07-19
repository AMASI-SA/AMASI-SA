"""Receiving-bank routing must never collapse to generic bank_transfer."""

from integrations.qoyod.payment_methods import (
    receiving_bank_key,
    resolve_receiving_bank_account,
)


def test_approved_bank_accounts_are_routed_exactly():
    cases = [
        ("bank_transfer", "مصرف الراجحي", "bank_rajhi", "94"),
        ("bank_transfer", "البنك الأهلي السعودي", "bank_ahli", "95"),
        ("bank_transfer", "مصرف الإنماء", "bank_inma", "8"),
    ]
    for method, bank_name, expected_key, expected_account in cases:
        key, account = resolve_receiving_bank_account(
            {}, method, bank_name,
        )
        assert key == expected_key
        assert account == expected_account


def test_specific_mapping_can_override_approved_default():
    settings = {
        "payment_method_mapping": [
            {"salla_method": "bank_rajhi", "qoyod_account_id": "194"},
            {"salla_method": "bank_transfer", "qoyod_account_id": "999"},
        ],
    }
    assert resolve_receiving_bank_account(
        settings, "bank_transfer", "الراجحي",
    ) == ("bank_rajhi", "194")


def test_unknown_or_missing_bank_never_uses_generic_mapping():
    settings = {
        "payment_method_mapping": [
            {"salla_method": "bank_transfer", "qoyod_account_id": "999"},
        ],
    }
    assert receiving_bank_key("bank_transfer", None) is None
    assert resolve_receiving_bank_account(
        settings, "bank_transfer", "بنك غير معروف",
    ) == (None, None)


def test_bank_specific_payment_method_is_sufficient():
    assert resolve_receiving_bank_account(
        {}, "bank_inma", None,
    ) == ("bank_inma", "8")
