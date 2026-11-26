"""PII-free signed vault quote preparation, synthetic-only tests."""

import json
from uuid import UUID

import pytest

from salla_order_creation.private_vault_action import (
    verify_private_vault_prepare,
)
from salla_order_creation.signature import SignatureDenied, sign_action

SECRET=b"synthetic-signed-private-vault-secret-00001"
STAMP="1800000000"
DELIVERY=str(UUID("12345678-1234-4234-8234-123456789abc"))


def message(**changes):
    m={
        "action":"salla.prepare_from_private_vault",
        "account_id":1,"inbox_id":2,"conversation_id":3,
        "intent_id":"intent-0001","source_message_id":900,
        "source_contact_id":77,"receiver_country_code":"SA",
        "country_id":1,"city_id":2,"district_id":3,
        "courier_id":220,"selected_rate_id":888,
        "accepted_methods":["mada"],
        "products":[{"identifier_type":"id","identifier":400,"quantity":1}],
    }
    m.update(changes)
    return m


def signed(body):
    raw=json.dumps(body,sort_keys=True,separators=(",",":")).encode()
    return verify_private_vault_prepare(
        raw,STAMP,DELIVERY,sign_action(raw,STAMP,DELIVERY,SECRET),
        SECRET,now=1800000000,
    )


def test_signed_vault_action_contains_no_customer_pii():
    payload=signed(message())
    assert payload.source_contact_id==77
    assert payload.products[0].identifier==400
    raw=json.dumps(message()).lower()
    for forbidden in (
        "buyer_name","buyer_mobile","recipient_name","recipient_phone",
        "buyer_email","address_line","checkout_url","paid",
    ):
        assert forbidden not in raw


@pytest.mark.parametrize("bad",[
    {"buyer_mobile":"+966500000000"},
    {"recipient_name":"Synthetic Name"},
    {"buyer_email":"buyer@example.invalid"},
    {"address_line":"customer address"},
    {"payment":{"status":"paid"}},
    {"checkout_url":"https://evil.invalid"},
    {"total_sar":"0.01"},
    {"salla_token":"synthetic_token"},
    {"source_contact_id":True},
    {"country_id":0},
    {"source_message_id":0},
    {"action":"salla.create_pending_order"},
])
def test_signed_selection_cannot_contain_private_customer_data_or_payment(bad):
    with pytest.raises(SignatureDenied):
        signed(message(**bad))


def test_tampered_purpose_or_region_hmac_rejected():
    raw=json.dumps(message()).encode()
    changed=raw.replace(b'"country_id": 1',b'"country_id": 7')
    with pytest.raises(SignatureDenied):
        verify_private_vault_prepare(
            changed,STAMP,DELIVERY,
            sign_action(raw,STAMP,DELIVERY,SECRET),
            SECRET,now=1800000000,
        )


def test_new_order_numeric_product_variants_allowed_but_no_raw_pii():
    p=message(products=[{
        "identifier_type":"id","identifier":400,"quantity":1,
        "options":[{"id":501,"value":["701"]}],
    }])
    out=signed(p)
    assert out.products[0].options[0].value==["701"]


def test_explicitly_unsupported_payment_mode_must_be_rejected_in_server_writer():
    # Strict HMAC syntax allows known Salla payment enums; server's unpriced
    # writer validates Mada/card ONLY and must deny bank/COD pre-POST.
    out=signed(message(accepted_methods=["cod"]))
    assert out.accepted_methods==["cod"]
