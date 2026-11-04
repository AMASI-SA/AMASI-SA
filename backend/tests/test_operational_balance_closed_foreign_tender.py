"""A closed foreign tender proves its paid claim independently of the order total."""
from copy import deepcopy

import pytest

from operational_balance_engine import reconcile
from operational_balance_service import report
from operational_customer_tender_coverage import new_coverage, observe_nonprovider
from operational_provider_report_coverage import provider_customer_payments
from test_operational_balance_payment_source_current import STAMP, foreign_payment


CREATED = "2026-11-01T01:00:00Z"
NOW = "2026-11-03T09:00:00Z"
KEY = "provider:71"
FACT = "provider_order_balance:71"


def source(gross="16.41", paid="16.40", sar="198.21"):
    payment, issues = foreign_payment(order=gross, paid=paid)
    assert payment is not None and not issues
    return {"id": "71", "order_number": "825", "currency": "KWD", "created_at": CREATED,
            "updated_at": STAMP, "payment": payment,
            "customer_payment_sar": {"currency": "KWD", "native": {"salla": paid},
                "shares": {"salla": sar}, "paid": sar, "basis": "salla_list_paid"}}


def state():
    return {"owner_id": "owner", "status": "active", "started_at": "2026-11-01T00:00:00Z",
            "openings": {}, "movements": [], "engine": {"as_of": STAMP}}


@pytest.mark.parametrize("gross,paid,sar,fees,net", [
    ("16.41", "16.40", "198.21", "4.57", "193.64"),
    ("43.84", "43.82", "529.36", "10.28", "519.08"),
])
def test_proved_paid_claim_uses_exact_sar_basis_and_saved_fee_once(gross, paid, sar, fees, net):
    order = source(gross, paid, sar)
    payment = order["payment"]
    page = payment["order_page_balance"]
    assert payment["gross"] == paid and not payment.get("captures")
    assert (page["gross"], page["paid"], page["remaining"]) == (gross, paid, None)
    assert page["source"] == "canonical_salla_closed_tender"
    assert page["transaction_reference"] == "native-cc-reference"
    assert "captured_at" not in page
    sources = {"orders": [order], "provider_fee_settings": {"payment_methods": [
        {"name": "البطاقة الإئتمانية", "commission_percent": "1.5", "fixed_fee": "1", "vat_percent": "15"}]}}
    before = deepcopy(sources)
    result = reconcile(state(), sources, NOW)
    assert result["engine"]["obligations"][KEY]["confirmed"] == paid
    assert result["engine"]["obligations"][KEY]["expected"] == "0.00"
    assert result["engine"]["facts"][FACT]["remaining"] is None
    assert not any(key.startswith("capture:") for key in result["engine"]["facts"])
    assert sources == before and not result["movements"] and not result["openings"]
    assert reconcile(result, sources, NOW) == result
    view = report(result, provider_balances_sar=True, provider_balances_net=True)
    platform, = view["details"]["provider_balances"]["SAR"]
    assert (platform["orders"], platform["gross"], platform["fees"], platform["net"]) == (1, sar, fees, net)
    assert platform["complete"] and platform["held_orders"] == 0
    assert view["summary"]["actual_liquidity"] == "0.00"
    pages, totals = provider_customer_payments(view)
    assert totals["KWD"] == [{"provider": "salla", "name": "سلة", "orders": 1, "paid": paid}]
    assert pages["KWD"][0]["remaining"] is None


def test_native_fee_policy_uses_the_proved_tender_instead_of_the_larger_order_total():
    order = source()
    policy = {"id": "native-fee", "provider": "salla", "currency": "KWD", "status": "active",
              "effective_from": "2026-01-01", "percentage": "100", "fixed_amount": "0",
              "vat_treatment": "exempt", "refund_fee_treatment": "retain", "cancellation_fee_treatment": "retain"}
    result = reconcile(state(), {"orders": [order], "fee_policies": [policy]}, NOW)
    projection = result["engine"]["provider_reports"][KEY]
    assert projection["gross"] == "16.40" and projection["estimated_fees"] == "16.40"
    assert result["engine"]["facts"][FACT]["gross"] == "16.41"


@pytest.mark.parametrize("currency,allocations", [
    ("SAR", []), ("KWD", []), ("KWD", [{"obligation_id": KEY, "amount": "10.00"}]),
])
def test_prior_payout_ambiguity_keeps_claim_held_with_original_provenance(currency, allocations):
    original = state()
    original["movements"] = [{"id": "payout", "kind": "settlement", "direction": "incoming",
        "party_type": "provider", "party_id": "salla", "currency": currency, "amount": "10.00",
        "actual_fee_amount": "0.00", "allocations": allocations}]
    before = deepcopy(original)
    result = reconcile(original, {"orders": [source()]}, NOW)
    assert KEY not in result["engine"]["obligations"] and FACT not in result["engine"]["facts"]
    assert result["movements"] == before["movements"] and result["openings"] == before["openings"]
    review = next(i["order_page_balance_review"] for i in result["engine"]["issues"]
                  if i["code"] == "provider_historical_capture_review_required")
    assert (review["gross"], review["paid"], review["remaining"]) == ("16.41", "16.40", None)
    assert review["source"] == "canonical_salla_closed_tender"
    assert review["transaction_reference"] == "native-cc-reference"


def test_reference_owned_by_a_different_order_cannot_create_a_second_claim():
    original = state()
    original["engine"]["facts"] = {"capture:native-cc-reference": {
        "order_id": "different", "provider": "salla", "amount": "16.40"}}
    result = reconcile(original, {"orders": [source()]}, NOW)
    assert KEY not in result["engine"]["obligations"]
    assert result["engine"]["facts"] == original["engine"]["facts"]
    assert any(i["code"] == "provider_capture_identity_conflict" for i in result["engine"]["issues"])


def test_closed_tender_does_not_invent_customer_remaining_money():
    order = source()
    coverage = new_coverage(state()["started_at"], NOW)
    coverage["uninspected_source_orders"] = 1
    raw = {"id": "71", "reference_id": "825", "date": CREATED, "updated_at": STAMP,
           "currency": "KWD", "payment_method": "credit_card"}
    observe_nonprovider(coverage, raw, {}, order, "16.41", order["payment"], "owner")
    row, = coverage["sar"]["by_method"]
    assert row["unknown_remaining_orders"] == 1 and row["remaining"] is None


@pytest.mark.parametrize("changes", [
    {"user_id": "other"}, {"order_number": "wrong"}, {"order_id": "wrong"},
    {"g47_salla_snapshot": {"cancelled": True}},
    {"g47_salla_snapshot": {"requires_authoritative_refresh": True}},
    {"g47_salla_snapshot": {"source_updated_at": "2026-11-02T08:00:01Z"}},
])
def test_closed_tender_keeps_source_owner_identity_and_watermark_gates(changes):
    payment, _ = foreign_payment(order="16.41", paid="16.40", source_changes=changes)
    assert not payment or "order_page_balance" not in payment


def test_a_fully_allocated_opening_payout_cannot_hide_a_proved_post_start_order():
    original = state()
    original["openings"] = {"opening:salla": {"id": "opening:salla", "party_type": "provider",
        "party_id": "salla", "currency": "SAR", "direction": "for_us", "amount": "1000.00"}}
    original["movements"] = [{"id": "opening-payout", "kind": "settlement", "direction": "incoming",
        "party_type": "provider", "party_id": "salla", "currency": "SAR", "amount": "900.00",
        "actual_fee_amount": "0.00", "allocations": [{"obligation_id": "opening:salla", "amount": "900.00"}]}]
    sources = {"orders": [source()]}
    result = reconcile(original, sources, NOW)
    assert result["engine"]["obligations"][KEY]["confirmed"] == "16.40"
    assert result["openings"] == original["openings"] and result["movements"] == original["movements"]
    assert reconcile(result, sources, NOW) == result


@pytest.mark.parametrize("field,value", [("owner_id", "other"), ("order_id", "other"),
    ("currency", "SAR"), ("source", "unverified"), ("transaction_reference", None)])
def test_invalid_closed_tender_envelope_cannot_enter_the_engine(field, value):
    order = source()
    order["payment"]["order_page_balance"][field] = value
    result = reconcile(state(), {"orders": [order]}, NOW)
    assert KEY not in result["engine"]["obligations"] and FACT not in result["engine"]["facts"]


def test_later_real_capture_corroborates_the_same_tender_without_rebooking_it():
    from operational_provider_transaction_evidence import VERSION

    order = source()
    first = reconcile(state(), {"orders": [order]}, NOW)
    native = {"user_id": "owner", "source": "salla_transactions", "order_number": "825",
        "provider_order_id": "71", "transaction_id": "native-payment", "native_transaction_evidence": [{
            "schema": VERSION, "provider_order_id": "71", "provider": "salla", "transaction_id": "native-payment",
            "reference": "native-cc-reference", "status": "paid", "paid_at": STAMP, "currency": "KWD", "amount": "16.40"}]}
    payment, issues = foreign_payment(order="16.41", paid="16.40", rows=[native])
    assert payment is not None and not issues and payment["captures"][0]["captured_at"] == STAMP
    order["payment"] = payment
    second = reconcile(first, {"orders": [order]}, NOW)
    assert second["engine"]["facts"][FACT] == first["engine"]["facts"][FACT]
    assert second["engine"]["facts"]["capture:native-cc-reference"]["amount"] == "16.40"
    assert second["engine"]["obligations"][KEY] == first["engine"]["obligations"][KEY]
    assert not second["movements"]
    assert reconcile(second, {"orders": [order]}, NOW) == second
