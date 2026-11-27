"""Mezan canonical bank order readback is never a customer-entered amount."""

import asyncio
from types import SimpleNamespace as Obj

import pytest

from salla_order_creation.bank_order_readback import (
    BankOrderNotVerified, read_synced_bank_order_amount,
)


def order(**changes):
    result = Obj(
        order_id="9876", order_number="302076044",
        payment=Obj(
            method="bank_transfer", method_native="تحويل بنكي",
            status="pending_payment", collection_status="unpaid", paid_amount=0,
        ),
        totals=Obj(currency="SAR", total=165.50),
    )
    for k,v in changes.items():
        setattr(result,k,v)
    return result


def check(result=None):
    async def getter(owner,ref):
        assert owner=="owner" and ref=="302076044"
        if result is False:
            raise LookupError("synthetic sync pending")
        return order() if result is None else result
    return asyncio.run(read_synced_bank_order_amount(
        owner_id="owner",salla_order_id=9876,reference_id=302076044,
        load_order=getter,
    ))


def test_synced_bank_amount_is_mezan_salla_amount_only():
    assert check()=="165.50"


def test_unavailable_mezan_sync_is_unknown_not_customer_estimate():
    assert check(result=False) is None


def test_missing_reference_never_guesses_total():
    async def unavailable(*args):
        raise AssertionError("no read")
    assert asyncio.run(read_synced_bank_order_amount(
        owner_id="owner",salla_order_id=9876,reference_id=None,
        load_order=unavailable,
    )) is None


def test_mismatched_order_identity_fails_closed():
    with pytest.raises(BankOrderNotVerified,match="mismatched"):
        check(result=order(order_id="99999"))


def test_unpaid_card_order_cannot_enter_bank_review():
    o=order()
    o.payment.method="mada"
    o.payment.method_native="مدى"
    with pytest.raises(BankOrderNotVerified,match="not_bank"):
        check(result=o)


def test_paid_or_partial_bank_order_not_reviewed_as_pending():
    for status in ("paid","partial"):
        o=order()
        o.payment.collection_status=status
        with pytest.raises(BankOrderNotVerified):
            check(result=o)


def test_wrong_currency_and_nonpositive_total_denied_or_waiting():
    o=order()
    o.totals.currency="KWD"
    with pytest.raises(BankOrderNotVerified,match="currency"):
        check(result=o)
    o.totals.currency="SAR"
    o.totals.total=None
    assert check(result=o) is None


def test_receipt_cannot_override_mezan_actual_total():
    o=order()
    o.totals.total=225.00
    assert check(result=o)=="225.00"
