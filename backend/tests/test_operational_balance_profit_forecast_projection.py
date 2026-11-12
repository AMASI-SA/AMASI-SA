"""Profit and cash forecasts keep period authority, unknowns and repeated fees separate."""
from copy import deepcopy

import pytest

from operational_forecast_projection import COMPONENTS, cod_forecast_coverage, project_forecast
from operational_profit_projection import SOURCE, VERSION, project_profit

START, END = "2026-11-01T00:00:00+03:00", "2026-11-03T00:00:00+03:00"


def snapshot():
    values = {"net_sales": "1000.00", "cost_of_goods": "200.00", "shipping": "50.00",
              "payment_fees": "20.00", "advertising": "100.00", "salaries": "100.00", "operating_expenses": "30.00"}
    source = {"version": VERSION, "source": SOURCE, "owner_id": "owner", "currency": "SAR", "as_of": END,
        "period": {"from": START, "to": END, "timezone": "Asia/Riyadh"}, "quality": {},
        "components": {key: {"amount": value, "known_subtotal": value, "complete": True, "source": "existing_source"}
                       for key, value in values.items()}}
    return {"owner_id": "owner", "started_at": START, "engine": {"as_of": END, "profit_snapshot": source}}


def test_operating_profit_is_available_without_inventing_financing_or_income_tax():
    state = snapshot(); before = deepcopy(state)
    result = project_profit(state, "SAR")
    assert result["operating_profit"] == "500.00" and result["net_profit"] is None
    assert result["operating_complete"] and not result["complete"]
    assert "دون ضريبة" not in result["rows"][0]["label"]
    assert state == before and result == project_profit(state, "SAR")
    for name in ("financing", "tax"):
        state["engine"]["profit_snapshot"]["components"][name] = {"complete": True, "amount": "0.00"}
    assert project_profit(state, "SAR")["net_profit"] == "500.00"


def test_partial_cost_keeps_subtotal_and_never_uses_unknown_as_zero_in_profit():
    state = snapshot()
    state["engine"]["profit_snapshot"]["components"]["cost_of_goods"].update(complete=False, amount=None, known_subtotal="120.00")
    result = project_profit(state, "SAR")
    assert result["operating_profit"] is None and result["net_profit"] is None
    assert next(row for row in result["rows"] if row["id"] == "cost_of_goods")["known_subtotal"] == "120.00"


@pytest.mark.parametrize("damage", ["owner", "start", "end", "timezone", "currency"])
def test_stale_or_foreign_snapshot_never_becomes_profit_for_current_balances(damage):
    state = snapshot(); saved = state["engine"]["profit_snapshot"]
    if damage == "owner": saved["owner_id"] = "other"
    elif damage == "start": saved["period"]["from"] = "2026-10-01T00:00:00+03:00"
    elif damage == "end": state["engine"]["as_of"] = "2026-11-04T00:00:00+03:00"
    elif damage == "timezone": saved["period"]["timezone"] = "UTC"
    else: saved["currency"] = "USD"
    assert project_profit(state, "SAR")["source_available"] is False


def tables():
    values = {"cod_expected": "200.00", "provider_collections_expected": "100.00", "products_unreceived": "50.00",
              "products_received_uninvoiced": "20.00", "shipping_expected": "10.00"}
    return [{"id": key, "title": key, "rows": [{"amount": value}], "totals": {"amount": value}, "complete": True}
            for key, value in values.items()]


def test_forecast_uses_each_existing_component_once_and_can_complete():
    rows = tables()
    rows.append({"id": "provider_fees_expected", "rows": [{"amount": "999.00"}], "complete": True, "totals": {"amount": "999.00"}})
    result = project_forecast(rows, current_position="500.00", actual_complete=True)
    assert result["forecast_change"] == "220.00" and result["forecast_monetary_position"] == "720.00"
    assert result["complete"] and not result["pending_components"]
    assert result["scope"] == "listed_order_work_only"


@pytest.mark.parametrize("damage", ["missing", "partial", "duplicate"])
def test_incomplete_forecast_keeps_known_rows_without_certifying_final_position(damage):
    rows = tables()
    if damage == "missing": rows.pop()
    elif damage == "partial": rows[-1].update(complete=False, totals={"amount": None})
    else: rows.append(deepcopy(rows[-1]))
    result = project_forecast(rows, current_position="500.00", actual_complete=True)
    assert result["forecast_change"] is None and result["forecast_monetary_position"] is None
    assert result["known_forecast_change"] is not None
    assert result["pending_components"] == ["shipping_expected"]
    assert set(row["id"] for row in result["rows"][:-2]) == set(COMPONENTS)


@pytest.mark.parametrize("damage,complete", [(None, True), ("missing", False), ("withheld", False), ("time", False), ("cod_issue", False), ("fee_issue", True)])
def test_cod_total_requires_both_cohorts_and_exact_snapshot_coverage(damage, complete):
    exposure = {"status": "preview", "started_at": START, "as_of": END, "engine_as_of": END,
        "prestart_open_cod": {"by_currency": {}, "withheld_candidates": 0},
        "open_customer_cod": {"by_currency": {}, "withheld_candidates": 0}}
    issues = []
    if damage == "missing": exposure["open_customer_cod"].pop("withheld_candidates")
    elif damage == "withheld": exposure["prestart_open_cod"]["withheld_candidates"] = 1
    elif damage == "time": exposure["engine_as_of"] = START
    elif damage == "cod_issue": issues = [{"code": "cod_collection_contract_incomplete", "source_id": "one"}]
    elif damage == "fee_issue": issues = [{"code": "provider_fee_estimate_unavailable", "source_id": "provider:one"}]
    result = cod_forecast_coverage(exposure, START, END, issues)
    assert result["complete"] is complete
    assert bool(result["reason_codes"]) is not complete
    assert not cod_forecast_coverage(None, START, END)["complete"]
