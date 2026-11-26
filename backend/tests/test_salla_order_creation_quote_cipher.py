"""Offline encrypted Mezan Salla quote evidence; all synthetic PII."""

import pytest
from cryptography.fernet import Fernet

from salla_order_creation.quote_cipher import (
    EncryptedOrderUnavailable,
    decrypt_order_payload,
    encrypt_order_payload,
)
from salla_order_creation.payload import PendingOrder


def order():
    return PendingOrder.model_validate({
        "customer":{
            "name":"Synthetic Buyer", "mobile":"+966500000000",
            "email":"buyer@example.invalid",
        },
        "receiver":{
            "name":"Synthetic Gift Recipient",
            "phone":"+966500000001", "country_code":"SA",
        },
        "courier_id":"123",
        "ship_to":{
            "country":1, "city":2, "district":None, "block":None,
            "street_number":"11","address_line":"Synthetic street",
            "postal_code":"12345", "short_address":"RHMA3184",
            "building_number":"11","additional_number":"22",
            "geo_coordinates":{"lat":24.7,"lng":46.7},
        },
        "accepted_methods":["mada"],
        "products":[{"identifier_type":"id","identifier":400,"quantity":1}],
    })


def test_ciphertext_contains_no_buyer_recipient_or_address_plaintext():
    cipher=Fernet(Fernet.generate_key())
    enc=encrypt_order_payload(order(),owner_id="owner",
                              intent_id="intent-0001",cipher=cipher)
    assert isinstance(enc,bytes)
    for private in ("Synthetic Buyer","Synthetic Gift Recipient",
                    "+966500000000","buyer@example.invalid",
                    "Synthetic street"):
        assert private.encode() not in enc
    restored=decrypt_order_payload(
        enc,owner_id="owner",intent_id="intent-0001",cipher=cipher,
    )
    assert restored.model_dump()==order().model_dump()
    assert restored.ship_to.district is None
    assert restored.ship_to.block is None


def test_buyer_scope_or_intent_swap_cannot_decrypt_other_cart():
    cipher=Fernet(Fernet.generate_key())
    enc=encrypt_order_payload(order(),owner_id="owner",
                              intent_id="intent-0001",cipher=cipher)
    with pytest.raises(EncryptedOrderUnavailable):
        decrypt_order_payload(
            enc,owner_id="different-merchant",intent_id="intent-0001",
            cipher=cipher,
        )
    with pytest.raises(EncryptedOrderUnavailable):
        decrypt_order_payload(
            enc,owner_id="owner",intent_id="intent-0002",
            cipher=cipher,
        )


def test_wrong_encryption_key_and_tampering_fail_without_customer_data():
    cipher=Fernet(Fernet.generate_key())
    enc=encrypt_order_payload(order(),owner_id="owner",
                              intent_id="intent-0001",cipher=cipher)
    with pytest.raises(EncryptedOrderUnavailable):
        decrypt_order_payload(
            enc,owner_id="owner",intent_id="intent-0001",
            cipher=Fernet(Fernet.generate_key()),
        )
    edited=bytearray(enc)
    edited[-5] ^= 1
    with pytest.raises(EncryptedOrderUnavailable):
        decrypt_order_payload(
            bytes(edited),owner_id="owner",intent_id="intent-0001",
            cipher=cipher,
        )


def test_empty_unencrypted_payload_is_never_accepted():
    with pytest.raises(EncryptedOrderUnavailable):
        decrypt_order_payload({},owner_id="owner",intent_id="intent-0001",
                              cipher=Fernet(Fernet.generate_key()))


def test_oversized_full_order_cannot_be_stored():
    cipher=Fernet(Fernet.generate_key())
    impossible=order()
    impossible.products=[impossible.products[0]]*1000
    with pytest.raises(EncryptedOrderUnavailable,match="too_large"):
        encrypt_order_payload(
            impossible,owner_id="owner",intent_id="intent-0001",
            cipher=cipher,
        )
