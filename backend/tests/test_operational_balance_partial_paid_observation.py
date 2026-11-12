"""A proved partial tender stays money even when Salla omits due/capture time."""
from copy import deepcopy
from decimal import Decimal

import pytest

from operational_balance_service import report
from operational_customer_payment_sar import value_customer_payment
from operational_provider_transaction_evidence import normalize_operational_payment
from salla_payment_collection import provider_order_page_balance
from test_operational_balance_order_page_recognition import (
    BORN, FACT, KEY, T1, T2, apply, normalize, raw_document, source_txn, state,
)


def partial_document(**changes):
    return {**raw_document(), "remaining_amount": None, "updated_at": T1, **changes}


def test_partial_paid_without_due_or_capture_time_is_one_claim_and_keeps_due_unknown():
    raw = partial_document()
    assert provider_order_page_balance(raw, "SAR", "100.00") is None
    payment, issues = normalize(raw)
    assert not issues
    assert payment["order_page_balance"]["paid"] == "45.00"
    assert payment["order_page_balance"]["remaining"] is None
    assert payment["order_page_balance"]["source"] == "canonical_salla_partial_tender"
    assert payment["gross"] == "100.00" and not payment.get("captures")
    first = apply(state(), raw, T1)
    second = apply(first, raw, T1)
    assert first == second
    assert first["engine"]["obligations"][KEY]["confirmed"] == "45.00"
    assert first["engine"]["facts"][FACT]["remaining"] is None
    assert not first["movements"] and not first["openings"]
    projection = report(first)
    assert projection["details"]["provider_paid_totals"]["SAR"][0]["paid"] == "45.00"
    assert projection["details"]["provider_order_payments"]["SAR"][0]["remaining"] is None


def test_later_transaction_corroborates_partial_without_double_counting_or_backdating():
    first = apply(state(), partial_document(), T1)
    no_time = apply(first, partial_document(), T2, rows=[source_txn(paid_at=None)])
    assert not any(key.startswith("capture:") for key in no_time["engine"]["facts"])
    captured = apply(no_time, partial_document(), T2, rows=[source_txn()])
    assert captured["engine"]["obligations"][KEY]["confirmed"] == "45.00"
    assert captured["engine"]["facts"][FACT]["observed_at"] == T1
    assert captured["engine"]["facts"]["capture:capture-A"]["amount"] == "45.00"
    assert report(captured)["details"]["provider_paid_totals"]["SAR"][0]["paid"] == "45.00"
    assert not captured["movements"]


def test_partial_newer_cumulative_paid_preserves_previous_claim_without_adding_twice():
    first = apply(state(), partial_document(), T1)
    second = apply(first, partial_document(paid_amount="70.00"), T2)
    assert second["engine"]["obligations"][KEY]["confirmed"] == "70.00"
    assert second["engine"]["facts"][FACT]["remaining"] is None
    assert apply(second, partial_document(paid_amount="70.00"), T2) == second


@pytest.mark.parametrize("changes", [
    {"paid_amount": "100.01"},
    {"has_remaining_amount": False},
    {"payment_status": "unpaid"},
    {"payment_status": "authorized"},
    {"status": "cancelled"},
    {"payment": {"paid_amount": "44.00"}},
    {"paid_amount": {"amount": "45.00", "currency": "KWD"}},
    {"refunded_amount": "1.00"},
    {"payment_actions": {"refund_action": {"pending_refund_amount": "1.00"}}},
    {"payment_methods": [
        {"payment_method": "tamara", "amount": {"amount": "30.00", "currency": "SAR"}, "transaction_reference": "one"},
        {"payment_method": "customer_wallet", "amount": {"amount": "15.00", "currency": "SAR"}, "transaction_reference": "two"},
    ]},
])
def test_partial_does_not_relax_conflicts_or_allocate_unproved_split(changes):
    payment, issues = normalize(partial_document(**changes))
    assert not payment or not payment.get("order_page_balance")


def test_historical_partial_excludes_unallocated_payout_and_leaves_opening_unchanged():
    initial = state()
    initial["engine"] = {"as_of": T1}
    initial["movements"] = [{"party_type": "provider", "party_id": "tamara", "currency": "SAR",
        "kind": "settlement", "direction": "incoming", "amount": "50.00", "allocations": []}]
    before = deepcopy(initial)
    result = apply(initial, partial_document(), T2)
    assert KEY not in result["engine"]["obligations"]
    assert any(i["code"] == "provider_historical_capture_review_required" for i in result["engine"]["issues"])
    assert result["movements"] == before["movements"] and result["openings"] == before["openings"]


def test_partial_foreign_paid_uses_same_verified_salla_fx_as_order_hydration():
    from order_currency import salla_order_currency_fields

    raw = {**partial_document(), "currency": "KWD", "total": {"amount": "100.00", "currency": "KWD"},
           "exchange_rate": {"rate": "12.34", "base_currency": "SAR", "exchange_currency": "KWD"}}
    source = {"user_id": "shop", "order_number": "123"}
    payment, issues = normalize_operational_payment(raw, "KWD", "100.00", BORN, T2, owner="shop",
        order_number="123", provider_order_id="1001", rows=[], source=source)
    assert payment and not issues
    conversion = salla_order_currency_fields(raw)
    assert conversion["currency_conversion_status"] == "verified"
    valued = value_customer_payment(raw, source, "shop", "KWD", {"tamara": Decimal(payment["order_page_balance"]["paid"])})
    assert valued["basis"] == "salla_order_fx"
    assert valued["paid"] == "555.30" and valued["shares"]["tamara"] == "555.30"
    assert Decimal(valued["paid"]) == Decimal("45.00") * Decimal(conversion["exchange_rate_to_sar"])
    assert payment["order_page_balance"]["remaining"] is None and not payment.get("captures")


def test_native_partial_fee_policy_prices_the_paid_share_and_never_relabels_a_sar_fixed_fee():
    from operational_balance_engine import reconcile
    from operational_configured_provider_fees import estimate_fee

    raw = {**partial_document(), "currency": "KWD", "total": {"amount": "100.00", "currency": "KWD"}}
    payment, issues = normalize_operational_payment(raw, "KWD", "100.00", BORN, T2, owner="shop",
        order_number="123", provider_order_id="1001", rows=[], source={"user_id": "shop", "order_number": "123"})
    assert not issues
    policy = {"id": "native-fee", "provider": "tamara", "currency": "KWD", "status": "active",
        "effective_from": "2026-01-01", "percentage": "10", "fixed_amount": "0", "vat_treatment": "exempt",
        "refund_fee_treatment": "retain", "cancellation_fee_treatment": "retain"}
    result = reconcile(state(), {"orders": [{"id": "1001", "order_number": "123", "currency": "KWD",
        "created_at": BORN, "updated_at": T1, "payment": payment}], "fee_policies": [policy],
        "provider_fee_settings": {}}, T2)
    row, = report(result)["details"]["provider_balances"]["KWD"]
    assert (row["known_gross"], row["known_fees"], row["known_net"]) == ("45.00", "4.50", "40.50")
    assert row["complete"] and row["pending_orders"] == 0
    assert estimate_fee({"payment_methods": [{"name": "تمارا", "commission_percent": "10", "fixed_fee": "1", "vat_percent": "0"}]},
        "tamara", "45.00", currency="KWD") is None
