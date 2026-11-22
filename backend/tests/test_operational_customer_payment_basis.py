"""Order edits do not erase independently proved customer payments."""
from copy import deepcopy

import pytest

from operational_balance_engine import reconcile
from operational_provider_transaction_evidence import normalize_operational_payment
from operational_customer_payment_basis import retained_confirmed_paid_basis


CREATED = "2026-11-01T10:00:00+00:00"
UPDATED = "2026-11-02T10:00:00+00:00"
AS_OF = "2026-11-03T10:00:00+00:00"


def document(total="199.20", paid="200.00", refund="0.80", *, captured=False):
    amount = lambda value: {"amount": value, "currency": "SAR"}
    raw = {
        "id": "synthetic-adjustment", "reference_id": "synthetic-order",
        "currency": "SAR", "total": amount(total), "status": "delivered",
        "created_at": CREATED, "updated_at": UPDATED, "payment_method": "mada",
        "payment_methods": [{"payment_method": "mada", "amount": amount(paid),
                             "transaction_reference": "synthetic-capture"}],
        "payment_actions": {
            "refund_action": {"paid_amount": amount(paid), "refund_amount": amount(refund),
                              "refunded_transactions": []},
            "remaining_action": {"paid_amount": amount(paid), "remaining_amount": amount("0.00"),
                                 "has_remaining_amount": False},
        },
    }
    if captured:
        raw["payment"] = {"method": "mada", "status": "paid", "paid_amount": amount(paid),
                          "reference": "synthetic-capture", "paid_at": CREATED}
    source = {"user_id": "owner", "order_id": raw["id"], "order_number": raw["reference_id"],
              "raw_by_source": {"salla_direct": raw}}
    return raw, source


def normalize(raw, source):
    return normalize_operational_payment(raw, "SAR", raw["total"], CREATED, AS_OF,
        owner="owner", order_number=raw["reference_id"], provider_order_id=raw["id"],
        source=source, rows=[])


@pytest.mark.parametrize("captured", [False, True])
def test_refund_hint_keeps_original_paid_principal_and_is_not_execution(captured):
    raw, source = document(captured=captured)
    before = deepcopy((raw, source))
    payment, issues = normalize(raw, source)
    assert payment is not None, issues
    assert payment["gross"] == "200.00"
    assert payment["refunds"] == []
    assert payment["customer_adjustment"]["suggested_refund_amount"] == "0.80"
    assert payment["customer_adjustment"]["suggested_collection_amount"] == "0.00"
    assert (raw, source) == before
    state = {"owner_id": "owner", "status": "active", "started_at": "2026-10-31T10:00:00+00:00",
             "openings": {}, "movements": []}
    order = {"id": raw["id"], "order_number": raw["reference_id"], "currency": "SAR",
             "created_at": CREATED, "updated_at": UPDATED, "payment": payment}
    result = reconcile(state, {"orders": [order]}, AS_OF)
    assert result["engine"]["obligations"]["provider:" + raw["id"]]["confirmed"] == "200.00"
    assert not any(key.startswith("refund:") for key in result["engine"]["facts"])
    repeated = reconcile(result, {"orders": [order]}, AS_OF)
    assert repeated == result


def test_added_product_keeps_paid_basis_and_exposes_only_remaining_amount():
    raw, source = document(total="300.00", refund="0.00", captured=True)
    payment, issues = normalize(raw, source)
    assert payment is not None, issues
    assert payment["gross"] == "200.00"
    assert payment["customer_adjustment"]["suggested_collection_amount"] == "100.00"
    assert payment["customer_adjustment"]["suggested_refund_amount"] == "0.00"


@pytest.mark.parametrize("mutation", ["currency", "reference", "paid_conflict", "owner", "stale", "unpaid"])
def test_independent_payment_fallback_preserves_identity_and_evidence_guards(mutation):
    raw, source = document(captured=True)
    if mutation == "currency":
        raw["payment_methods"][0]["amount"]["currency"] = "USD"
    elif mutation == "reference":
        raw["payment_methods"][0]["transaction_reference"] = "different-capture"
    elif mutation == "paid_conflict":
        raw["payment_actions"]["remaining_action"]["paid_amount"]["amount"] = "201.00"
    elif mutation == "owner":
        source["user_id"] = "other"
    elif mutation == "stale":
        source["g47_salla_snapshot"] = {"source_updated_at": AS_OF}
    elif mutation == "unpaid":
        raw["payment"]["status"] = "authorized"
    payment, issues = normalize(raw, source)
    assert payment is None and issues


def test_same_reference_total_edit_keeps_existing_claim_and_does_not_create_second_capture():
    raw, source = document(total="200.00", refund="0.00")
    initial_payment, issues = normalize(raw, source)
    assert initial_payment is not None, issues
    state = {"owner_id": "owner", "status": "active", "started_at": "2026-10-31T10:00:00+00:00",
             "openings": {}, "movements": []}
    order = {"id": raw["id"], "order_number": raw["reference_id"], "currency": "SAR",
             "created_at": CREATED, "updated_at": UPDATED, "payment": initial_payment}
    first = reconcile(state, {"orders": [order]}, UPDATED)
    edited, source = document(total="199.20", refund="0.80")
    edited["updated_at"] = AS_OF
    payment, issues = normalize(edited, source)
    assert payment is not None, issues
    second = reconcile(first, {"orders": [{**order, "updated_at": AS_OF, "payment": payment}]}, AS_OF)
    claim = second["engine"]["obligations"]["provider:" + raw["id"]]
    assert claim["confirmed"] == "200.00" and not claim.get("incomplete")
    assert not any(key.startswith("refund:") for key in second["engine"]["facts"])
    assert second["movements"] == []


def retained_fixture():
    raw, source = document(captured=True)
    payment, _ = normalize(raw, source)
    state = {"owner_id": "owner", "engine": {
        "facts": {"capture:synthetic-capture": {"order_id": raw["id"], "provider": "salla", "amount": "200.00"}},
        "obligations": {"provider:" + raw["id"]: {"party_type": "provider", "party_id": "salla",
            "currency": "SAR", "confirmed": "200.00", "direction": "receivable"}},
        "provider_fee_inputs": {"orders": {raw["id"]: {"payment": payment}}}}}
    raw["payment"].update(status="refunded", paid_amount="0.00")
    raw["payment_actions"]["refund_action"]["paid_amount"] = "0.00"
    raw["payment_actions"]["remaining_action"]["paid_amount"] = "0.00"
    raw["status"] = "refunded"
    return raw, source, state


def retained(raw, source, state):
    return retained_confirmed_paid_basis(raw, "SAR", raw["total"], CREATED, AS_OF,
        owner="owner", order_number=raw["reference_id"], provider_order_id=raw["id"], source=source, state=state)


def test_retained_lookup_uses_existing_paid_fact_when_current_source_becomes_zero():
    raw, source, state = retained_fixture()
    before = deepcopy((raw, source, state))
    basis = retained(raw, source, state)
    assert basis["paid_amount"] == "200.00" and basis["suggested_refund_amount"] == "0.80"
    assert basis["payment"]["retained_confirmed_payment"] is True
    assert basis["payment"]["captures"][0]["captured_at"] == CREATED
    assert (raw, source, state) == before
    # This read fallback does not enable a new source payment normalization.
    assert normalize(raw, source)[0] is None


@pytest.mark.parametrize("mutation", ["no_fact", "no_claim", "foreign_owner", "foreign_provider", "wrong_reference", "changed_claim", "stale"])
def test_retained_lookup_cannot_manufacture_or_reassign_original_payment(mutation):
    raw, source, state = retained_fixture()
    if mutation == "no_fact":
        state["engine"]["facts"].clear()
    elif mutation == "no_claim":
        state["engine"]["obligations"].clear()
    elif mutation == "foreign_owner":
        state["owner_id"] = "other"
    elif mutation == "foreign_provider":
        state["engine"]["facts"]["capture:synthetic-capture"]["provider"] = "tabby"
    elif mutation == "wrong_reference":
        raw["payment"]["reference"] = "unrelated-reference"
    elif mutation == "changed_claim":
        state["engine"]["obligations"]["provider:" + raw["id"]]["confirmed"] = "201.00"
    elif mutation == "stale":
        source["g47_salla_snapshot"] = {"source_updated_at": AS_OF}
    assert retained(raw, source, state) is None


def test_retained_page_has_no_invented_capture_time_and_keeps_refunded_principal():
    raw, source, state = retained_fixture()
    payment = state["engine"]["provider_fee_inputs"]["orders"][raw["id"]]["payment"]
    page = deepcopy(payment["order_page_balance"])
    state["engine"]["facts"] = {"provider_order_balance:" + raw["id"]: page,
        "refund:salla:known-refund": {"order_id": raw["id"], "amount": "0.80"}}
    state["engine"]["obligations"]["provider:" + raw["id"]]["confirmed"] = "199.20"
    basis = retained(raw, source, state)
    assert basis["paid_amount"] == "200.00" and not basis["payment"].get("captures")
    assert basis["payment"]["order_page_balance"] == page


def cached_adjustment_fixture():
    from operational_balance_sources import collect_sources
    from test_operational_balance_sources import DB, row, run
    from test_operational_balance_configured_provider_fees import settings

    raw, source = document(total="200.00", refund="0.00", captured=True)
    state = {"owner_id": "owner", "status": "active", "started_at": "2026-10-31T10:00:00+00:00",
             "openings": {}, "movements": []}
    db = DB({"unified_orders": [source], "settings": [row(**settings("مدى", "1", "1", "15"))]})
    initial = run(collect_sources(db, "owner", state["started_at"], AS_OF, operational_state=state))
    state = reconcile(state, initial, AS_OF)
    raw["updated_at"] = AS_OF
    raw["total"]["amount"] = "199.20"
    raw["payment"].update(status="refunded", paid_amount="0.00")
    raw["payment_actions"]["refund_action"].update(paid_amount="0.00", refund_amount="0.80")
    raw["payment_actions"]["remaining_action"]["paid_amount"] = "0.00"
    raw["status"] = "refunded"
    return raw, source, state, db


@pytest.mark.parametrize("status", ["refunded", "cancelled"])
def test_zero_paid_terminal_refresh_updates_only_cached_adjustment_and_keeps_original_cash_and_fees(status):
    from operational_balance_sources import collect_sources
    from test_operational_balance_sources import run
    from test_operational_balance_configured_provider_fees import platform
    from test_operational_customer_provider_projection import refund

    raw, source, state, db = cached_adjustment_fixture()
    raw["status"] = raw["payment"]["status"] = status
    original = deepcopy(state)
    sources = run(collect_sources(db, "owner", state["started_at"], AS_OF, operational_state=state))
    observed, = sources["orders"]
    assert observed.get("payment") is None  # Never replay retained money into recognition.
    assert observed["retained_customer_adjustment"]["customer_adjustment"]["refund_hint_amount"] == "0.80"
    refreshed = reconcile(state, sources, AS_OF)
    assert state == original
    assert refreshed["engine"]["facts"] == original["engine"]["facts"]
    assert refreshed["engine"]["obligations"]["provider:" + raw["id"]] == original["engine"]["obligations"]["provider:" + raw["id"]]
    old_input = original["engine"]["provider_fee_inputs"]["orders"][raw["id"]]
    new_input = refreshed["engine"]["provider_fee_inputs"]["orders"][raw["id"]]
    assert new_input["updated_at"] == old_input["updated_at"]  # Original fee date remains unchanged.
    assert {key: value for key, value in new_input["payment"].items() if key != "customer_adjustment"} == {
        key: value for key, value in old_input["payment"].items() if key != "customer_adjustment"}
    card = platform(refreshed, "salla")
    assert (card["gross"], card["known_fees"], card["known_net"]) == ("200.00", "3.45", "196.55")
    assert card["pending_orders"] == 0
    assert card["customer_adjustments"] == {"currency": "SAR", "refund_pending_count": 1,
        "refund_pending_amount": "0.80", "collection_pending_count": 0, "collection_pending_amount": "0.00"}
    # The observation cannot conceal an independent fee/statement problem.
    fee_review = deepcopy(refreshed)
    fee_review["engine"]["issues"].append({"code": "provider_statement_fee_review_required",
        "source_id": raw["id"], "scope": "provider_payment", "provider": "salla", "currency": "SAR"})
    assert "provider_statement_fee_review_required" in {
        reason["code"] for reason in platform(fee_review, "salla")["pending_reasons"]}
    repeated = reconcile(refreshed, sources, AS_OF)
    assert repeated["engine"]["facts"] == refreshed["engine"]["facts"]
    assert platform(repeated, "salla") == card
    repeated["customer_returns"] = [refund(order_id=raw["id"], order_number=raw["reference_id"])]
    confirmed = platform(repeated, "salla")
    assert confirmed["known_net"] == "195.75" and confirmed["known_fees"] == "3.45"
    assert confirmed["customer_adjustments"]["refund_pending_count"] == 0
    assert confirmed["customer_adjustments"]["collection_pending_count"] == 0


@pytest.mark.parametrize("mutation", ["reference", "stale", "currency", "paid_conflict", "same_version", "saved_newer"])
def test_terminal_report_metadata_keeps_source_identity_currency_and_zero_paid_guards(mutation):
    from operational_balance_sources import collect_sources
    from test_operational_balance_sources import run

    raw, source, state, db = cached_adjustment_fixture()
    if mutation == "reference":
        raw["payment"]["reference"] = "unrelated-reference"
    elif mutation == "stale":
        source["g47_salla_snapshot"] = {"source_updated_at": "2026-11-04T10:00:00+00:00"}
    elif mutation == "currency":
        raw["payment_actions"]["remaining_action"]["currency"] = "USD"
    elif mutation == "same_version":
        raw["updated_at"] = UPDATED
    elif mutation == "saved_newer":
        state["engine"]["order_versions"][raw["id"]] = "2026-11-04T10:00:00+00:00"
    else:
        raw["payment_actions"]["remaining_action"]["paid_amount"] = "1.00"
    sources = run(collect_sources(db, "owner", state["started_at"], AS_OF, operational_state=state))
    assert sources["orders"][0].get("payment") is None
    assert "retained_customer_adjustment" not in sources["orders"][0]
    assert any(issue.get("scope") == "provider_payment" and not issue.get("customer_adjustment_proof")
               for issue in sources["issues"])
