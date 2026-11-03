"""Synthetic saved accruals from the real salary reducer, independent of bank or opening balances."""
from copy import deepcopy

import pytest

from operational_balance_engine import reconcile
from operational_financial_position import build_financial_position
from test_operational_balance_salary_start import employee, policy, state


START = "2026-11-01T03:00:00+03:00"
AS_OF = "2026-11-01T21:05:00Z"  # Nov 2 in Riyadh


def recorded(base, sources):
    result = reconcile(base, sources, AS_OF)
    result["engine"]["as_of"] = AS_OF  # Same saved boundary as operational_balance_service.
    return result


def snapshot():
    base = policy(state(START), approved=START)
    return recorded(base, {"employees": [employee("3000"), dict(employee("600"), id="other", currency="USD")]})


def salary(data, currency="SAR"):
    return next(row for row in data["currencies"][currency]["profit_rows"] if row["id"] == "salaries")


def test_recorded_days_have_period_and_currency_and_never_establish_full_profit():
    snap = snapshot()
    before = deepcopy(snap)
    report = build_financial_position(snap)
    for currency, value in [("SAR", "200.00"), ("USD", "40.00")]:
        row = salary(report, currency)
        assert row["amount"] is None and row["recorded_subtotal"] == {
            "amount": value, "currency": currency, "employee_days": 2,
            "first_day": "2026-11-01", "last_day": "2026-11-02",
        }
        assert value in row["reason"] and currency in row["reason"] and "جزئي" in row["reason"]
        assert report["currencies"][currency]["metrics"]["net_profit"] is None
        assert report["currencies"][currency]["completeness"]["profit"] is False
    assert build_financial_position(snap) == report and snap == before


@pytest.mark.parametrize("damage", ["conflict", "missing_day", "missing_currency", "wrong_obligation",
    "missing_revision", "wrong_date", "foreign_revision", "bad_start", "inflated_pair"])
def test_uncertain_source_does_not_supply_a_salary_amount(damage):
    snap = snapshot(); key = "salary:employee:2026-11-01"
    engine = snap["engine"]
    if damage == "conflict":
        engine["salary_conflicts"] = {key: {"requires_review": True}}
    elif damage == "missing_day":
        del engine["salary_days"][key]
    elif damage == "missing_currency":
        del engine["salary_days"][key]["currency"]
    elif damage == "wrong_obligation":
        engine["obligations"][key]["confirmed"] = "99.00"
    elif damage == "missing_revision":
        del engine["salary_days"][key]["revision"]
    elif damage == "wrong_date":
        engine["obligations"][key]["business_date"] = "2026-11-03"
    elif damage == "inflated_pair":
        engine["salary_days"][key]["amount"] = engine["obligations"][key]["confirmed"] = "9999.00"
    elif damage == "foreign_revision":
        engine["salary_days"][key]["revision"]["effective_from"] = "2026-11-03"
    else:
        snap["salary_accrual_policy"]["fingerprint"] = "changed"
    row = salary(build_financial_position(snap))
    assert row["amount"] is None and "recorded_subtotal" not in row


def test_ordinary_first_day_policy_and_opening_never_create_a_day_of_expense():
    snap = recorded(state(START), {"employees": [employee("3000")]})
    row = salary(build_financial_position(snap))
    assert row["recorded_subtotal"]["first_day"] == "2026-11-02"
    assert row["recorded_subtotal"]["employee_days"] == 1
    assert row["recorded_subtotal"]["amount"] == "100.00"
    empty = recorded(state(START), {"employees": []})
    assert "recorded_subtotal" not in salary(build_financial_position(empty))


def test_payment_and_prior_or_later_days_do_not_change_subtotal():
    snap = snapshot()
    ref = "salary:employee:2026-11-01"
    snap["movements"] = [{"id": "historical-pay", "kind": "payment", "amount": "35.00", "currency": "SAR",
        "bank_kind": "bank", "bank_id": "bank", "party_type": "employee", "party_id": "employee",
        "direction": "outgoing", "allocations": [{"obligation_id": ref, "amount": "35.00"}]}]
    for business_day in ("2026-10-31", "2026-11-03"):
        key = "salary:employee:" + business_day
        snap["engine"]["salary_days"][key] = deepcopy(snap["engine"]["salary_days"][ref])
        snap["engine"]["obligations"][key] = dict(snap["engine"]["obligations"][ref], id=key, business_date=business_day)
    assert salary(build_financial_position(snap))["recorded_subtotal"]["amount"] == "200.00"
