"""Synthetic receipt appearance is NOT bank credit or order payment."""

from datetime import datetime, timedelta, timezone

import pytest

from salla_order_creation.bank_receipt_review import (
    ReceiptAppearanceDenied, ReceiptVisibleFields,
    bank_pending_payload, inspect_visible_bank_receipt,
)
from salla_order_creation.payload import PendingOrder

NOW=datetime(2026,11,27,14,0,tzinfo=timezone.utc)
IBAN="SA0380000000608010167519"


def bank():
    return {"source":"salla_store_payment_banks","bank_id":321,
            "status":"active","account_name":"مؤسسة أماسي الخليج التجارية",
            "iban":IBAN}


def visible(**changes):
    data={
        "readable":True,"bank_transfer_shown":True,
        "appears_edited_or_cropped":False,
        "payer_bank_name_visible":True,
        "recipient_bank_name":"Synthetic Bank",
        "recipient_account_name":"مؤسسة أماسي الخليج التجارية",
        "recipient_iban":IBAN,"amount":"165.00",
        "currency":"SAR","transfer_reference":"REF-000123",
        "timestamp":NOW-timedelta(minutes=12),
    }
    data.update(changes)
    return ReceiptVisibleFields.model_validate(data)


def check(look=None,**kwargs):
    return inspect_visible_bank_receipt(
        look or visible(),salla_bank=bank(),checked_at=NOW,now=NOW,
        **kwargs,
    )


def pending_bank_order():
    return PendingOrder.model_validate({
        "customer":{"id":123},
        "receiver":{"name":"Synthetic Recipient",
                    "phone":"+966500000000","country_code":"SA"},
        "courier_id":"201",
        "ship_to":{
            "country":1,"city":2,"district":3,"block":None,
            "street_number":"11","address_line":"Synthetic street",
            "postal_code":"12345","short_address":"RHMA3184",
            "building_number":"11","additional_number":"22",
            "geo_coordinates":{"lat":24.7,"lng":46.7},
        },
        "accepted_methods":["bank"],
        "products":[{"identifier_type":"id","identifier":400,"quantity":1}],
    })


def test_visible_bank_receipt_only_apparently_plausible_never_paid():
    r=check(actual_order_total_sar="165.00")
    assert r["state"]=="appearance_plausible"
    assert r["receipt_amount_sar"]=="165.00"
    assert r["bank_credit_confirmed"] is False
    assert r["paid"] is False
    assert r["may_mark_paid"] is False
    assert r["requires_staff_review"] is True
    assert r["may_begin_fulfillment"] is False
    assert r["flags"]==[]


def test_pre_order_receipt_precheck_does_not_certify_actual_amount():
    r=check()
    assert r["state"]=="appearance_plausible"
    assert r["requires_staff_review"]


@pytest.mark.parametrize("bad",[
    {"readable":False},
    {"bank_transfer_shown":False},
    {"appears_edited_or_cropped":True},
    {"payer_bank_name_visible":False},
    {"recipient_account_name":"متجر آخر"},
    {"recipient_iban":"SA0380000000608010167520"},
    {"currency":"KWD"},
    {"amount":"0.00"},
    {"transfer_reference":None},
    {"timestamp":NOW+timedelta(minutes=15)},
    {"timestamp":NOW-timedelta(days=45)},
])
def test_missing_or_suspicious_visible_fact_requires_clarification(bad):
    r=check(visible(**bad))
    assert r["state"]=="needs_customer_clarification"
    assert r["flags"]
    assert not r["bank_credit_confirmed"]
    assert r["paid"] is False


def test_amount_mismatch_after_salla_sync_is_flagged_not_paid():
    r=check(actual_order_total_sar="170.00")
    assert r["state"]=="needs_customer_clarification"
    assert "receipt_amount_differs_from_salla_order" in r["flags"]


def test_bank_active_and_approved_source_required():
    for changed in ({"status":"inactive"},
                    {"source":"model_suggested_bank"},
                    {"bank_id":0}):
        b={**bank(),**changed}
        with pytest.raises(ReceiptAppearanceDenied):
            inspect_visible_bank_receipt(
                visible(),salla_bank=b,checked_at=NOW,now=NOW,
            )


def test_source_bank_facts_must_be_fresh():
    with pytest.raises(ReceiptAppearanceDenied):
        inspect_visible_bank_receipt(
            visible(),salla_bank=bank(),
            checked_at=NOW-timedelta(minutes=11),now=NOW,
        )


def test_bank_pending_payload_explicit_method_without_paid_or_receipt_url():
    payload=bank_pending_payload(pending_bank_order())
    assert payload["payment"]=={
        "status":"pending_payment","method":"bank",
        "accepted_methods":["bank"],
    }
    assert "receipt_image_path" not in str(payload)
    assert "store_bank_id" not in str(payload)
    assert "paid" not in payload["payment"]["status"]


def test_bank_formatter_denies_card_and_cod():
    for method in ("mada","credit_card","cod"):
        o=pending_bank_order()
        o.accepted_methods=[method]
        with pytest.raises(ReceiptAppearanceDenied):
            bank_pending_payload(o)
