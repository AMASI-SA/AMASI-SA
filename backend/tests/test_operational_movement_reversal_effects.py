"""A reversal is an append-only correction of an existing cash movement, never a second sale or settlement."""
from copy import deepcopy
from decimal import Decimal

import pytest

from operational_balance_service import account_statement, report
from operational_balance_store import initial
from operational_financial_activity import cash_positions
from operational_movement_reversal import build_reverse, inspect, reversal_id, reversal_index
from operational_profit_projection import OPERATING, SOURCE, VERSION, project_profit

STAMP = "2026-11-14T21:45:00+00:00"


def movement(**fields):
    return dict({
        "id": "entry", "request_id": "manual-request", "actor_id": "recorder", "source": "mezan2",
        "occurred_at": "2026-11-07T09:00:00+00:00", "business_date": "2026-11-07",
        "name": "Office supplies", "bank_name": "Bank", "currency": "SAR", "amount": "25.50",
        "party_type": "operating_expense", "party_id": "category", "bank_kind": "bank",
        "bank_id": "bank", "kind": "payment", "direction": "outgoing", "allocations": [],
        "reference": "original reference", "workspace_operation": "expense.pay",
    }, **fields)


def ledger(*rows):
    state = initial("owner")
    state.update(status="active", started_at="2026-11-01T00:00:00+00:00", movements=list(rows))
    state["openings"]["bank-opening"] = {"id": "bank-opening", "party_id": "bank", "party_type": "bank",
        "currency": "SAR", "amount": "100.00", "name": "Bank", "direction": "for_us"}
    return state


def undo(state, original):
    assert inspect(state, original) is None
    snapshot = deepcopy(original)
    inverse = build_reverse(original, reversal_id(original["id"]), {"id": "reviewer", "name": "مراجع"},
                            "Duplicate expense entered in error", STAMP)
    state["movements"].append(inverse)
    assert original == snapshot and inverse["reverse_of"] == original["id"]
    assert inverse["business_date"] == "2026-11-15" and inverse["recorded_by"]["id"] == "reviewer"
    assert inverse["reference"] != original["reference"]
    assert inspect(state, original) and inspect(state, inverse)
    return inverse


def balance(state, kind, identity):
    return next(row for row in report(state)["parties"] if (row["party_type"], row["party_id"]) == (kind, identity))


def test_expense_keeps_original_and_opposes_both_cash_and_expense_on_reversal_date():
    original = movement()
    state = ledger(original)
    state["engine"].update(as_of=STAMP, profit_snapshot={
        "version": VERSION, "source": SOURCE, "owner_id": "owner", "currency": "SAR", "as_of": STAMP,
        "period": {"from": state["started_at"], "to": STAMP, "timezone": "Asia/Riyadh"},
        "components": {key: {"complete": True, "amount": "100" if key == "net_sales" else "2"}
                       for key in (*OPERATING, "financing", "tax")},
    })
    baseline = deepcopy(state)
    baseline["movements"] = []
    assert balance(state, "bank", "bank")["actual"] == "74.50"
    assert report(state)["summary"]["operating_expenses_paid"] == "25.50"
    undo(state, original)
    expense = balance(state, "operating_expense", "category")
    assert expense["actual"] == expense["expense_paid"] == "0.00"
    assert report(state)["summary"] == report(baseline)["summary"]
    assert project_profit(state, "SAR")["net_profit"] == "84.00"
    assert project_profit(state, "SAR") == project_profit(baseline, "SAR")
    key = ("bank", "bank", "SAR")
    assert cash_positions(state, {key})[key]["actual"] == Decimal("100.00")
    rows = account_statement(state, {"kind":"bank", "id":"bank", "currency":"SAR", "name":"Bank"})["items"]
    assert [(r["business_date"],r["amount"],r["direction"]) for r in rows] == [
        ("2026-11-15","25.50","incoming"),("2026-11-07","25.50","outgoing")]

    from operational_balance_workspace import _movement_rows
    from operational_workspace_projection import statement
    from operational_balance_access import AccessPolicy
    policy = AccessPolicy({"id": "owner"}, "owner", True, True, 1, (), [])
    resource = {"kind": "operating_expense", "id": "category", "currency": "SAR", "name": "Office supplies",
                "access": policy.resource("operating_expense", "category"), "position_balance": expense["expense_paid"]}
    rows = _movement_rows(resource, policy, state["movements"])
    def period(day):
        return statement(resource, rows, date_from=day, date_to=day, actor=policy.actor, generated_at=STAMP,
                         purpose="view", started_at=state["started_at"])
    original_day, reversal_day = period("2026-11-07"), period("2026-11-15")
    assert (original_day["opening_balance"], original_day["closing_balance"]) == ("0.00", "-25.50")
    assert (reversal_day["opening_balance"], reversal_day["closing_balance"]) == ("-25.50", "0.00")


@pytest.mark.parametrize("party", ["employee", "supplier", "external_person"])
@pytest.mark.parametrize("direction,kind,opening,outstanding", [
    ("outgoing", "payment", "against_us", "outstanding_payable"),
    ("incoming", "collection", "for_us", "outstanding_receivable"),
])
def test_account_payment_and_competing_subsequent_settlement_restore_only_original(party, direction, kind, opening, outstanding):
    operation = party + "." + kind
    if party == "external_person":
        operation = "external.pay" if direction == "outgoing" else "external.receive"
    original = movement(party_type=party, settlement_scope="account", workspace_operation=operation,
                        direction=direction, kind=kind)
    other = dict(original, id="later", amount="20.00", request_id="other")
    state = ledger(original, other)
    state["openings"]["due"] = dict(state["openings"]["bank-opening"], id="due", party_type=party,
                                     party_id="category", amount="30.00", direction=opening)
    baseline = deepcopy(state)
    baseline["movements"] = [other]
    assert balance(state,party,"category")["settled"] == "30.00"
    undo(state, original)
    result = balance(state,party,"category")
    assert result["settled"] == "20.00" and result[outstanding] == "10.00"
    assert report(state)["summary"] == report(baseline)["summary"]
    assert result == balance(baseline,party,"category")


def test_transfer_both_sides_undo_and_running_balance_projections_agree():
    original = movement(party_type="cash", party_id="safe", kind="transfer", workspace_operation="accounts.transfer")
    state = ledger(original)
    assert balance(state,"cash","safe")["actual"] == "25.50"
    undo(state,original)
    for kind, identity, expected in [("cash","safe","0.00"), ("bank","bank","100.00")]:
        key = (kind,identity,"SAR")
        assert cash_positions(state,{key})[key]["actual"] == Decimal(expected)
        result = account_statement(state,dict(kind=kind,id=identity,name=identity,currency="SAR"))
        assert result["account"]["actual"] == expected
        assert len(result["items"]) == 2
        assert {r["direction"] for r in result["items"]} == {"incoming","outgoing"}


@pytest.mark.parametrize("kind,party,operation,bank", [
    ("correction","cash",None,None), ("correction","employee","employee.accrual",None),
    ("payment","owner_withdrawal","owner.withdraw","bank"), ("collection","owner_withdrawal","owner.return","bank"),
])
def test_manual_corrections_and_owner_drawings_are_neutralized(kind,party,operation,bank):
    original = movement(kind=kind, party_type=party, workspace_operation=operation, bank_kind=bank, bank_id=bank,
                        direction="incoming" if kind == "collection" else "outgoing")
    state = ledger(original)
    undo(state,original)
    assert report(state)["summary"] == report(ledger())["summary"]


@pytest.mark.parametrize("change", [
    {"automatic_order_bank":True}, {"automatic_prepaid_shipping":True}, {"driver_review_event_id":"a"},
    {"source":"operational_driver_review"}, {"actor_id":"operational_worker"}, {"order_number":"n"},
    {"kind":"opening"}, {"invoice_id":"v"}, {"actual_fee_amount":"0.01"}, {"funding_mode":"hybrid"},
    {"bank_currency":"USD"}, {"allocations":[{"amount":"1.00","obligation_id":"invoice"}]},
    {"workspace_group":"group"}, {"party_type":"employee_custody"}, {"bank_kind":"employee_custody"},
    {"external_operation":"lend"}, {"reverse_of":"a"}, {"amount":"NaN"}, {"amount":"25.567"},
    {"workspace_resources":[{"kind":"supplier","id":"a third party"}]},
])
def test_non_reversible_source_fails_closed(change):
    original = movement(**change)
    assert inspect(ledger(original),original)


@pytest.mark.parametrize("links", [
    {"customer_exchanges":[{"contributions":[{"movement_id":"entry"}]}]},
    {"native_purchase_links":{"inv":{"payments":{"payment":{"movement_id":"entry"}}}}},
    {"driver_payment_review_facts":{"event":{"movement_id":"entry"}}},
])
def test_reversibility_reads_link_records_even_when_original_was_not_changed(links):
    original = movement()
    state=ledger(original)
    state.update(links)
    assert inspect(state,original,index=reversal_index(state))
