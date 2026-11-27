"""Offline Salla bank-transfer payload and merchant active-bank verification."""

import asyncio

import pytest

from salla_order_creation.bank_transfer import (
    BankSelectionDenied,
    bank_order_digest,
    build_pending_bank_order_payload,
    get_verified_receiving_bank,
)
from salla_order_creation.payload import PendingOrder
from salla_order_creation.readers import SourceUnavailable


def bank_order(methods=None):
    return PendingOrder.model_validate({
        "customer": {"id": 501},
        "receiver": {
            "name": "Synthetic Recipient", "phone": "+966500000000",
            "country_code": "SA",
        },
        "courier_id": "220",
        "ship_to": {
            "country": 1, "city": 2, "district": 3, "block": None,
            "street_number": "11", "address_line": "Synthetic street",
            "postal_code": "12345", "short_address": "RHMA3184",
            "building_number": "11", "additional_number": "22",
            "geo_coordinates": {"lat": 24.7, "lng": 46.7},
        },
        "accepted_methods": methods or ["bank"],
        "products": [{
            "identifier_type": "id", "identifier": 400, "quantity": 1,
        }],
    })


def test_bank_order_never_claims_paid_or_invents_receipt_url():
    payload = build_pending_bank_order_payload(bank_order(), store_bank_id=123)
    assert payload["payment"] == {
        "status": "pending_payment", "method": "bank",
        "store_bank_id": 123, "accepted_methods": ["bank"],
    }
    assert "receipt_image_path" not in payload["payment"]
    assert "checkout_url" not in str(payload)
    assert "paid" not in str(payload["payment"]["status"])


@pytest.mark.parametrize("method", [["mada"], ["bank", "credit_card"], ["cod"]])
def test_wrong_payment_method_is_never_bank_order(method):
    with pytest.raises(BankSelectionDenied, match="bank_payment_method_only"):
        build_pending_bank_order_payload(
            bank_order(method), store_bank_id=123,
        )


@pytest.mark.parametrize("id_", [True, None, 0, -5, "123"])
def test_bank_id_must_be_salla_numeric_positive(id_):
    with pytest.raises(BankSelectionDenied):
        build_pending_bank_order_payload(
            bank_order(), store_bank_id=id_,
        )


def test_bank_selection_is_part_of_immutable_order_digest():
    first = bank_order_digest(bank_order(), store_bank_id=123)
    assert len(first) == 64
    assert first != bank_order_digest(bank_order(), store_bank_id=124)


async def connected(db, uid):
    return {"status": "connected", "scope": "payments.read orders.read_write"}


def test_active_bank_get_uses_actual_salla_details_not_model_guesses():
    calls = []

    async def get(db, uid, method, path):
        calls.append((uid, method, path))
        return {"success": True, "data": {
            "id": 123, "bank_name": "Synthetic Bank",
            "account_name": "Synthetic Merchant", "status": "active",
            "iban_number": "SA0000000000000000000000",
        }}

    record = asyncio.run(get_verified_receiving_bank(
        None, "owner", 123,
        request=get, integration_reader=connected,
    ))
    assert calls == [("owner", "GET", "/payment/banks/123")]
    assert record["active"] is True
    assert record["store_bank_id"] == 123
    assert record["beneficiary_name"] == "Synthetic Merchant"
    assert record["payment_funds_verified"] is False
    assert "iban_number" not in record


@pytest.mark.parametrize("changed", [
    {"id": 124},
    {"status": "inactive"},
    {"bank_name": ""},
    {"account_name": None},
])
def test_inactive_or_mismatched_banks_cannot_be_used(changed):
    async def get(*a, **k):
        return {"success": True, "data": {
            "id": 123, "bank_name": "Synthetic Bank",
            "account_name": "Synthetic Merchant", "status": "active",
            **changed,
        }}
    with pytest.raises(BankSelectionDenied):
        asyncio.run(get_verified_receiving_bank(
            None, "owner", 123,
            request=get, integration_reader=connected,
        ))


def test_missing_payment_read_scope_blocks_bank_lookup_before_network():
    async def insufficient(db, uid):
        return {"status": "connected", "scope": "orders.read_write"}

    async def unexpected(*a, **k):
        raise AssertionError("network must not run")

    with pytest.raises(SourceUnavailable, match="payments.read_not_granted"):
        asyncio.run(get_verified_receiving_bank(
            None, "owner", 123,
            request=unexpected, integration_reader=insufficient,
        ))
