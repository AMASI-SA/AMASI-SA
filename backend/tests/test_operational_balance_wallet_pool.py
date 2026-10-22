"""Account-level results from daily evidence, without fabricated event times."""
from copy import deepcopy
from decimal import Decimal
from itertools import permutations

import pytest

from operational_ad_wallet_events import project_wallet_events
from operational_ad_wallet_pool import project_pool
from operational_balance_engine import reconcile
from operational_balance_service import report
from operational_balance_store import initial


ACCOUNT = "operational-ad:example"
START = "2026-10-20T00:00:00+00:00"
NOW = "2026-10-22T12:00:00+00:00"


def state(wallet="50", debt="0"):
    result = initial("owner")
    result.update(status="active", started_at=START,
                  ad_funding={ACCOUNT: {"mode": "wallet_first", "currency": "SAR"}})
    for key, value, direction, kind, identity in (
        ("wallet", wallet, "for_us", "ad_account", ACCOUNT),
        ("debt", debt, "for_party", "ad_account", ACCOUNT),
        ("bank", "1000", "for_us", "bank", "bank"),
    ):
        result["openings"][key] = dict(id=key, amount=value, direction=direction,
            party_type=kind, party_id=identity, currency="SAR", name=identity,
            funding_mode="hybrid" if kind == "ad_account" else None)
    return result


def snapshot(amount="80", **changes):
    return dict(account_id=ACCOUNT, date="2026-10-21", amount=amount, currency="SAR",
        observed_at="2026-10-22T03:00:00+00:00", timezone="UTC", funding_type="hybrid",
        funding_policy="wallet_first", closed=True, complete=True, day_ended=True, **changes)


def paid(result, amount="100"):
    result["movements"].append(dict(id="p1", party_type="ad_account", party_id=ACCOUNT,
        currency="SAR", kind="payment", direction="outgoing", amount=amount,
        bank_kind="bank", bank_id="bank", bank_name="bank", name=ACCOUNT,
        allocations=[], occurred_at=NOW, funding_policy="wallet_first",
        wallet_first_allocation={"debt_settled": "0.00", "wallet_funded": amount,
                                 "basis": "confirmed_balance_at_recording"}))
    return result


def balances(result):
    view = report(result)
    row = next(r for r in view["parties"] if r["party_id"] == ACCOUNT)
    return row, view


@pytest.mark.parametrize("wallet,debt,spend,payment,expected_wallet,expected_debt", [
    ("50", "0", "80", "0", "0.00", "30.00"),
    ("50", "0", "50", "0", "0.00", "0.00"),
    ("100", "0", "80", "0", "20.00", "0.00"),
    ("0", "100", "0", "200", "100.00", "0.00"),
    ("50", "100", "40", "80", "10.00", "20.00"),
    ("50", "100", "80", "200", "70.00", "0.00"),
    ("0.01", "0", "0.03", "0.01", "0.00", "0.01"),
])
def test_real_reducer_and_report_expose_wallet_depletion_as_debt(wallet, debt, spend, payment, expected_wallet, expected_debt):
    result = reconcile(paid(state(wallet, debt), payment), {"ad_snapshots": [snapshot(spend)]}, NOW)
    before = deepcopy(result)
    row, view = balances(result)
    assert (row["ad_wallet_balance"], row["ad_payable"]) == (expected_wallet, expected_debt)
    assert view["summary"]["actual_liquidity"] == f"{Decimal('1000')-Decimal(payment):.2f}"
    assert row["outstanding_payable"] == expected_debt
    synthetic = [r for r in view["obligations"] if r.get("balance_role") == "wallet_pool_balance"]
    assert len(synthetic) == 1 and synthetic[0]["available_to_pay"] == expected_debt
    assert all(r["available_to_pay"] == "0.00" for r in view["obligations"] if r.get("balance_role") == "wallet_pool_evidence")
    assert result == before
    assert reconcile(result, {"ad_snapshots": [snapshot(spend)]}, NOW) == result


def test_late_daily_spend_uses_cash_already_paid_without_rewriting_payment_snapshot():
    before = paid(state(), "100")
    assert balances(before)[0]["ad_wallet_balance"] == "150.00"
    after = reconcile(before, {"ad_snapshots": [snapshot("180")]}, NOW)
    row, view = balances(after)
    assert (row["ad_wallet_balance"], row["ad_payable"]) == ("0.00", "30.00")
    assert after["movements"] == before["movements"]
    assert view["summary"]["actual_liquidity"] == "900.00"
    # Discovering spend before recording the same payment has the same balance.
    reverse = paid(reconcile(state(), {"ad_snapshots": [snapshot("180")]}, NOW), "100")
    assert balances(reverse)[0] == row


def test_pending_daily_spend_is_visible_but_does_not_create_confirmed_debt():
    pending = snapshot("180")
    pending.update(closed=False, complete=False)
    row, view = balances(reconcile(state(), {"ad_snapshots": [pending]}, NOW))
    assert (row["ad_wallet_balance"], row["ad_payable"], row["expected_payable"]) == ("50.00", "0.00", "180.00")
    assert view["summary"]["payable"] == "0.00"


def test_currency_and_account_boundaries_are_exact():
    result = paid(state(), "100")
    result["movements"].extend([
        {**result["movements"][0], "id": "foreign-currency", "currency": "USD", "amount": "999"},
        {**result["movements"][0], "id": "other-account", "party_id": "other", "amount": "999"},
    ])
    assert project_pool(result, ACCOUNT, "SAR")["wallet_balance"] == Decimal("150")
    with pytest.raises(ValueError, match="wallet_policy_required"):
        project_pool(result, ACCOUNT, "USD")


@pytest.mark.parametrize("kind,direction,allocations", [
    ("correction", "incoming", []), ("refund", "incoming", []),
    ("collection", "incoming", []), ("settlement", "outgoing", []),
    ("payment", "outgoing", [{"obligation_id": "old-day", "amount": "1"}]),
])
def test_unsupported_historical_movements_fail_instead_of_inventing_balances(kind, direction, allocations):
    result = paid(state())
    result["movements"][0].update(kind=kind, direction=direction, allocations=allocations)
    with pytest.raises(ValueError, match="wallet_movement_unsupported"):
        report(result)


def test_old_fixed_split_history_cannot_be_reinterpreted_as_wallet_first():
    result = reconcile(state(), {"ad_snapshots": [snapshot()]}, NOW)
    next(iter(result["engine"]["obligations"].values()))["funding_policy"] = "explicit_split"
    with pytest.raises(ValueError, match="wallet_policy_history_conflict"):
        report(result)


@pytest.mark.parametrize("wallet,debt", [("0", "0"), ("50", "0"), ("50", "100")])
def test_aggregate_balances_match_every_order_of_independently_evidenced_events(wallet, debt):
    values = [("spend", "40"), ("payment", "80"), ("spend", "80"), ("payment", "30")]
    aggregate = project_pool(reconcile(paid(state(wallet, debt), "110"),
                            {"ad_snapshots": [snapshot("120")]}, NOW), ACCOUNT, "SAR")
    for order in permutations(values):
        events = [dict(id=str(i), evidence_id=str(i), owner_id="owner", account_id=ACCOUNT,
                       currency="SAR", kind=kind, amount=value, occurred_at=f"2026-10-21T{i:02d}:00:00Z")
                  for i, (kind, value) in enumerate(order)]
        sequential = project_wallet_events(owner_id="owner", account_id=ACCOUNT, currency="SAR",
            opening_at=START, opening_wallet=wallet, opening_debt=debt, events=events)
        assert Decimal(sequential["wallet_balance"]) == aggregate["wallet_balance"]
        assert Decimal(sequential["payable"]) == aggregate["payable"]
