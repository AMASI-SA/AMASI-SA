"""Confirmed money, customer suggestions and fee evidence remain separate."""
from copy import deepcopy
from decimal import Decimal

import pytest

from operational_balance_service import report
from operational_customer_collection_effects import collection_match, collection_projection
from operational_workspace_provider_orders import provider_position
from test_operational_balance_configured_provider_fees import paid_state, platform, settings


def source_state(total="99.20", refund="0.80"):
    state, _ = paid_state("salla", method="mada", terms=settings("مدى", "1", "1", "15"))
    order = state["engine"]["provider_fee_inputs"]["orders"]["one"]
    order["order_number"] = "synthetic-order"
    order["payment"]["customer_adjustment"] = {
        "order_total": total, "paid_amount": "100.00", "refund_hint_amount": refund,
        "suggested_refund_amount": refund, "suggested_collection_amount": "0.00",
        "evidence_source": "canonical_salla_adjusted_payment",
    }
    return state


def collection(**changes):
    return {"id": "manual-collection", "status": "confirmed", "order_id": "one",
            "order_number": "synthetic-order", "amount": "50.00", "currency": "SAR",
            "destination_type": "provider", "destination_id": "salla", "destination_name": "سلة",
            "reference": "additional-capture", "fee_mode": "new_transaction",
            "fee_amount": "1.73", "fee_status": "estimated", "paid_basis": "100.00",
            "destination_paid_basis": "100.00", "source_paid_basis": "100.00", **changes}


def refund(**changes):
    return {"id": "manual-refund", "case_type": "financial_refund", "method": "original",
            "status": "refunded", "order_id": "one", "order_number": "synthetic-order",
            "amount": "0.80", "currency": "SAR", "refund_source_type": "provider",
            "refund_source_id": "salla", "refund_source_name": "سلة", "refund_reference": "refund-one",
            "financial_effect": "confirmed_provider_refund_debit", **changes}


def canonical_extra(state, *, claim=True, reference=True):
    payment = state["engine"]["provider_fee_inputs"]["orders"]["one"]["payment"]
    payment["gross"] = "150.00"
    if reference:
        state["engine"]["facts"]["capture:additional-capture"] = {
            "order_id": "one", "provider": "salla", "amount": "50.00"}
        payment["captures"].append({**payment["captures"][0], "id": "additional-capture", "amount": "50.00"})
    else:
        state["engine"]["facts"]["provider_order_balance:one"] = {
            "order_id": "one", "provider": "salla", "currency": "SAR", "paid": "150.00",
            "transaction_reference": "receipt"}
        payment["order_page_balance"] = state["engine"]["facts"]["provider_order_balance:one"].copy()
    if claim:
        state["engine"]["obligations"]["provider:one"]["confirmed"] = "150.00"


def test_unconfirmed_refund_keeps_paid_gross_fee_and_net_outside_ordinary_warnings():
    state = source_state()
    before = deepcopy(state)
    row = platform(state, "salla")
    assert (row["gross"], row["known_fees"], row["known_net"]) == ("100.00", "2.30", "97.70")
    assert row["pending_orders"] == 0 and row["customer_adjustments"] == {
        "currency": "SAR", "refund_pending_count": 1, "refund_pending_amount": "0.80",
        "collection_pending_count": 0, "collection_pending_amount": "0.00"}
    assert provider_position(report(state), "salla", show_orders=True)["customer_adjustments"] == row["customer_adjustments"]
    assert "customer_adjustments" not in provider_position(report(state), "salla", show_orders=False)
    assert state == before


@pytest.mark.parametrize("kind,identity,expected_net", [("provider", "salla", "96.90"), ("bank", "bank", "97.70")])
def test_refund_reduces_only_actual_source_and_clears_suggestion_without_new_debt(kind, identity, expected_net):
    state = source_state()
    state["customer_returns"] = [refund(refund_source_type=kind, refund_source_id=identity)]
    row = platform(state, "salla")
    assert row["known_net"] == expected_net
    assert row["customer_adjustments"]["refund_pending_count"] == 0
    assert row["customer_adjustments"]["collection_pending_count"] == 0
    assert row["known_fees"] == "2.30"


def test_new_platform_collection_adds_money_and_its_own_fee_once():
    state = source_state(total="150.00", refund="0.00")
    state["customer_collections"] = [collection()]
    before = deepcopy(state)
    row = platform(state, "salla")
    assert (row["known_gross"], row["known_fees"], row["known_net"]) == ("150.00", "4.03", "145.97")
    assert row["customer_adjustments"]["collection_pending_count"] == 0
    assert row["pending_orders"] == 0 and state == before
    assert platform(state, "salla") == row


def test_unavailable_incremental_fee_preserves_original_net_and_confirmed_extra_gross():
    state = source_state(total="150.00", refund="0.00")
    state["customer_collections"] = [collection(fee_amount=None, fee_status="unavailable")]
    row = platform(state, "salla")
    assert row["known_gross"] == "150.00" and row["known_net"] == "97.70"
    assert row["known_fees"] == "2.30" and row["net"] is None
    assert row["uncomputed_principal"] == "50.00"
    assert row["customer_adjustments"]["collection_pending_count"] == 0
    assert {reason["code"] for reason in row["pending_reasons"]} == {"customer_collection_fee_unavailable"}


def test_late_exact_capture_before_claim_refresh_cannot_remove_or_duplicate_manual_cash():
    state = source_state(total="150.00", refund="0.00")
    state["customer_collections"] = [collection()]
    canonical_extra(state, claim=False)
    assert collection_match(state, state["customer_collections"][0]) is None
    row = platform(state, "salla")
    assert (row["known_gross"], row["known_fees"], row["known_net"]) == ("150.00", "4.03", "145.97")


def test_exact_new_transaction_match_never_adds_principal_or_guesses_second_fixed_fee():
    state = source_state(total="150.00", refund="0.00")
    state["customer_collections"] = [collection()]
    canonical_extra(state)
    assert collection_match(state, state["customer_collections"][0]) == "capture:additional-capture"
    row = platform(state, "salla")
    assert row["known_gross"] == "150.00" and row["manual_collected_amount"] == "0.00"
    assert row["known_net"] == "97.70" and row["known_fees"] == "2.30"
    assert row["uncomputed_principal"] == "50.00"
    assert {reason["code"] for reason in row["pending_reasons"]} == {"customer_collection_fee_match_required"}


def test_cumulative_page_overlap_keeps_larger_proved_amount_without_false_matching():
    state = source_state(total="150.00", refund="0.00")
    state["customer_collections"] = [collection()]
    canonical_extra(state, reference=False)
    effect = collection_projection(state, state["customer_collections"][0])
    assert effect["matched_evidence_id"] is None and effect["principal_delta"] == Decimal(0)
    assert effect["review_code"] == "customer_collection_source_overlap"
    row = platform(state, "salla")
    assert row["known_gross"] == "150.00" and row["known_net"] == "145.97"
    assert row["customer_adjustments"]["refund_pending_count"] == 0
    assert row["customer_adjustments"]["collection_pending_count"] == 0
    assert row["pending_orders"] == 1


def test_late_refund_fact_without_claim_reduction_keeps_one_manual_debit_and_original_fees():
    state = source_state()
    state["customer_returns"] = [refund()]
    state["engine"]["facts"]["refund:salla:refund-one"] = {"order_id": "one", "amount": "0.80"}
    row = platform(state, "salla")
    assert row["known_net"] == "96.90" and row["known_fees"] == "2.30"
    assert row["refunded"] == "0.80"


@pytest.mark.parametrize("page_paid,expected_match", [("130.00", False), ("150.00", True)])
def test_same_transaction_cumulative_source_does_not_repeat_fixed_fee(page_paid, expected_match):
    state = source_state(total="150.00", refund="0.00")
    state["customer_collections"] = [collection(fee_mode="same_transaction", fee_amount="0.58", original_reference="receipt")]
    canonical_extra(state, reference=False)
    state["engine"]["facts"]["provider_order_balance:one"]["paid"] = page_paid
    state["engine"]["obligations"]["provider:one"]["confirmed"] = page_paid
    state["engine"]["provider_fee_inputs"]["orders"]["one"]["payment"]["order_page_balance"]["paid"] = page_paid
    assert bool(collection_match(state, state["customer_collections"][0])) is expected_match
    row = platform(state, "salla")
    assert (row["known_gross"], row["known_fees"], row["known_net"]) == ("150.00", "2.88", "147.12")
    assert row["customer_adjustments"]["collection_pending_count"] == 0


def test_accepted_fees_for_both_exact_captures_release_incremental_net_once():
    from test_operational_balance_statement_fees import accepted_event, attach

    state = source_state(total="150.00", refund="0.00")
    state["customer_collections"] = [collection()]
    canonical_extra(state)
    attach(state,
        accepted_event(state, key="first", provider="salla", fee="2.00", vat="0.30", total="2.30"),
        accepted_event(state, key="second", provider="salla", cover="capture:additional-capture",
                       principal="50.00", fee="1.50", vat="0.23", total="1.73"))
    row = platform(state, "salla")
    assert (row["known_gross"], row["known_fees"], row["known_net"]) == ("150.00", "4.03", "145.97")
    assert row["pending_orders"] == 0 and row["fee_status"] == "confirmed"


def test_bank_statement_match_does_not_reopen_customer_collection_suggestion():
    state = source_state(total="150.00", refund="0.00")
    state["customer_collections"] = [collection(destination_type="bank", destination_id="bank", fee_amount="0.00")]
    state["movements"].append({"id": "bank-native-receipt", "kind": "collection", "party_type": "bank",
        "party_id": "bank", "bank_id": "bank", "bank_kind": "bank", "currency": "SAR", "direction": "incoming", "amount": "50.00",
        "order_id": "one", "reference": "additional-capture", "allocations": []})
    assert collection_match(state, state["customer_collections"][0]) == "bank-movement:bank-native-receipt"
    row = platform(state, "salla")
    assert row["customer_adjustments"]["collection_pending_count"] == 0
    assert row["known_gross"] == "100.00" and row["known_net"] == "97.70"


def test_documented_and_reserved_refunds_share_one_pending_amount_without_debit():
    state = source_state()
    state["engine"]["provider_fee_inputs"]["orders"]["one"]["payment"].pop("customer_adjustment")
    state["customer_returns"] = [refund(id="reservation", status="pending", amount="20.00"),
                                  refund(id="documentary", status="salla_refund_recorded", amount="30.00")]
    row = platform(state, "salla")
    assert row["customer_adjustments"]["refund_pending_count"] == 1
    assert row["customer_adjustments"]["refund_pending_amount"] == "30.00"
    assert row["pending_orders"] == 0 and row["known_net"] == "97.70"
