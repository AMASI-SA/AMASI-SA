"""Cash capture parity with the existing report, without monetary writes."""
from copy import deepcopy
from decimal import Decimal
import random

import operational_financial_activity as activity
from operational_balance_service import report
from operational_balance_store import initial

STAMP = "2026-11-08T00:00:00+00:00"


def state_fixture():
    state = initial("synthetic-owner")
    state.update(status="active", started_at="2026-11-01T00:00:00+00:00")
    for kind, identity, currency in (("bank", "bank", "SAR"), ("cash", "cash", "SAR"),
                                      ("bank", "usd", "USD")):
        state["openings"][identity] = {"id": identity, "party_type": kind, "party_id": identity,
            "currency": currency, "name": identity, "amount": "10000.00", "direction": "for_us"}
    state["engine"] = {"obligations": {}, "issues": [], "as_of": STAMP}
    return state


def receipt(identity, **extra):
    return {"id": str(identity), "party_type": "bank", "party_id": "bank", "name": "bank",
        "bank_kind": "bank", "bank_id": "bank", "bank_name": "bank", "amount": "1.00",
        "currency": "SAR", "direction": "incoming", "kind": "receipt", "actor_id": "operational_order_worker",
        "occurred_at": STAMP, "order_id": str(identity), "allocations": [], **extra}


def capture(state, movement):
    state["movements"].append(movement)
    event = {"id": movement["id"], "action": "order_bank_credited", "after": movement}
    activity.capture_after_balances(state, event)
    return event["financial_activity_snapshots"]


def test_cash_capture_matches_report_after_every_mixed_effect():
    state = state_fixture()
    rng = random.Random(20261108)
    for index in range(120):
        amount = format(Decimal(rng.randrange(1, 5000)) / 100, ".2f")
        movement = receipt(index, amount=amount)
        mode = index % 6
        if mode == 1:
            movement.update(kind="transfer", direction="outgoing", party_type="cash", party_id="cash", name="cash")
        elif mode == 2:
            movement.update(kind="transfer", direction="outgoing", party_id="usd", name="usd", currency="USD",
                            bank_currency="SAR", bank_amount=format(Decimal(amount) * Decimal("3.75"), ".2f"))
        elif mode == 3:
            movement.update(kind="correction", direction="outgoing", bank_kind=None, bank_id=None)
        elif mode == 4:
            state.setdefault("customer_returns", []).append({"id": str(index), "status": "refunded",
                "refund_source_type": "bank", "refund_source_id": "bank", "refund_source_name": "bank",
                "currency": "SAR", "amount": amount})
        elif mode == 5:
            state.setdefault("customer_returns", []).append({"id": str(index), "status": "salla_refund_confirmed",
                "refund_source_type": "bank", "refund_source_id": "bank", "refund_source_name": "bank",
                "currency": "SAR", "amount": amount})
        snapshots = capture(state, movement)
        expected = {(p["party_type"], p["party_id"], p["currency"]): p for p in report(state)["parties"]}
        rows = activity._movement_rows(movement, {})
        for row, snap in zip(rows, snapshots):
            key = row["account_type"], row["account_id"], row["currency"]
            assert snap["account_balance_after"] == expected[key]["actual"]
            assert snap["account_name"] == expected[key]["name"]
            assert snap["counterparty_balance_after"] is None


def test_cash_capture_reads_current_state_and_does_not_mutate_records():
    state = state_fixture()
    first = capture(state, receipt("one"))
    state["openings"]["bank"]["amount"] = "500.00"
    state["movements"][0]["amount"] = "20.00"
    before = deepcopy(state)
    event = {"id": "changed", "action": "order_bank_credited", "after": state["movements"][0]}
    activity.capture_after_balances(state, event)
    assert state == before
    assert first[0]["account_balance_after"] == "10001.00"
    assert event["financial_activity_snapshots"][0]["account_balance_after"] == "520.00"


def test_unconfirmed_refunds_do_not_reduce_cash_and_currency_is_not_relabelled():
    state = state_fixture()
    state["customer_returns"] = [
        {"status": status, "refund_source_type": "bank", "refund_source_id": "bank",
         "refund_source_name": "bank", "amount": "50.00", "currency": currency}
        for status, currency in (("pending", "SAR"), ("rejected", "SAR"), ("refunded", "USD"))]
    assert capture(state, receipt("one"))[0]["account_balance_after"] == "10001.00"


def test_debt_counterparty_retains_report_and_confirmed_obligation_basis():
    state = state_fixture()
    state["openings"]["supplier"] = {"id": "supplier", "party_type": "supplier", "party_id": "supplier",
        "name": "supplier", "currency": "SAR", "amount": "100.00", "direction": "for_party"}
    movement = receipt("payment", party_type="supplier", party_id="supplier", name="supplier", direction="outgoing",
                       amount="40.00", kind="payment", allocations=[{"obligation_id": "supplier", "amount": "40.00"}])
    assert activity._cash_only_projection(state, activity._movement_rows(movement, {})) is None
    snap = capture(state, movement)[0]
    assert snap["account_balance_after"] == "9960.00"
    assert snap["counterparty_balance_after"] == "-60.00"
    assert snap["counterparty_balance_basis"] == "confirmed_obligations"


def test_hundred_credit_batch_preserves_every_intermediate_balance_without_full_reports(monkeypatch):
    state = state_fixture()
    state["movements"] = [receipt(i) for i in range(5000)]
    def full_report_forbidden(*args, **kwargs):
        raise AssertionError("Cash-only automatic credit must not compute unrelated report projections")
    monkeypatch.setattr("operational_balance_service.report", full_report_forbidden)
    for index in range(5000, 5100):
        snap = capture(state, receipt(index))[0]
        assert snap["account_balance_after"] == format(Decimal(10001 + index), ".2f")
        assert snap["counterparty_balance_after"] is None
