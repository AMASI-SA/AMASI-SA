"""Synthetic-only buyer matching/guest email requirements for Salla orders."""

import pytest

from salla_order_creation.customer_identity import (
    CustomerIdentityUnavailable,KnownSallaCustomer,normalized_mobile,
    resolve_salla_buyer,
)


def row(id=123,phone="+966500000000",owner="owner",source="salla_order_details"):
    return KnownSallaCustomer(
        user_id=owner,salla_customer_id=id,buyer_mobile=phone,source=source,
    )


@pytest.mark.parametrize("variant",[
    "+966500000000", "00966500000000","966500000000","0500000000","500000000",
])
def test_saudi_number_normalization(variant):
    assert normalized_mobile(variant)=="+966500000000"


def test_returning_buyer_uses_one_verified_salla_id():
    r=resolve_salla_buyer(
        user_id="owner",verified_whatsapp_buyer_mobile="0500000000",
        previous_customers=(row(),row()),
    )
    assert r.customer.model_dump(exclude_none=True)=={"id":123}
    assert r.source=="existing_salla_customer_id"


def test_customer_does_not_need_email_if_verified_salla_id_exists():
    r=resolve_salla_buyer(
        user_id="owner",verified_whatsapp_buyer_mobile="0500000000",
        previous_customers=(row(),),
    )
    assert r.customer.id==123
    assert r.customer.email is None


def test_new_customer_cannot_get_fake_email():
    with pytest.raises(CustomerIdentityUnavailable,match="email_required"):
        resolve_salla_buyer(
            user_id="owner",verified_whatsapp_buyer_mobile="0500000000",
            previous_customers=(),buyer_name="Synthetic Buyer",
        )


def test_new_buyer_uses_only_customer_supplied_email():
    r=resolve_salla_buyer(
        user_id="owner",verified_whatsapp_buyer_mobile="0500000000",
        previous_customers=(),buyer_name="Synthetic Buyer",
        buyer_email="BUYER@example.invalid",
    )
    assert r.customer.id is None
    assert r.customer.mobile=="+966500000000"
    assert r.customer.email=="buyer@example.invalid"


def test_cross_merchant_customer_evidence_denied():
    with pytest.raises(CustomerIdentityUnavailable,match="scope_mismatch"):
        resolve_salla_buyer(
            user_id="owner",verified_whatsapp_buyer_mobile="0500000000",
            previous_customers=(row(owner="someone-else"),),
        )


def test_ambiguous_matching_provider_ids_are_denied():
    with pytest.raises(CustomerIdentityUnavailable,match="ambiguous"):
        resolve_salla_buyer(
            user_id="owner",verified_whatsapp_buyer_mobile="0500000000",
            previous_customers=(row(id=123),row(id=999)),
        )


def test_wrong_phone_does_not_reuse_gift_recipient_salla_customer_id():
    with pytest.raises(CustomerIdentityUnavailable,match="email_required"):
        resolve_salla_buyer(
            user_id="owner",verified_whatsapp_buyer_mobile="0559999999",
            previous_customers=(row(phone="0500000000"),),
            buyer_name="Another buyer",
        )


@pytest.mark.parametrize("bad",["not@valid", "foo@example", "foo @example.com",
                                "foo@example.com?redirect=evil"])
def test_invalid_guest_email_denied(bad):
    with pytest.raises(CustomerIdentityUnavailable,match="invalid_new_buyer_email"):
        resolve_salla_buyer(
            user_id="owner",verified_whatsapp_buyer_mobile="0500000000",
            previous_customers=(),buyer_name="Synthetic Buyer",
            buyer_email=bad,
        )


def test_unverified_history_source_denied():
    with pytest.raises(CustomerIdentityUnavailable,match="unverified"):
        resolve_salla_buyer(
            user_id="owner",verified_whatsapp_buyer_mobile="0500000000",
            previous_customers=(row(source="model_memory"),),
        )


def test_invalid_buyer_mobile_denied():
    with pytest.raises(CustomerIdentityUnavailable,match="invalid_customer_mobile"):
        resolve_salla_buyer(
            user_id="owner",verified_whatsapp_buyer_mobile="123",
            previous_customers=(),buyer_name="A B",
            buyer_email="synthetic@example.invalid",
        )
