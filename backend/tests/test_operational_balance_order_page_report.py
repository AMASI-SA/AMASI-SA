"""The page proof is a breakdown, never another claim or a bank deposit."""
from copy import deepcopy

from operational_financial_position import build_financial_position
from test_operational_balance_financial_position import NOW, opening, obligation, state_for, table


def page(order, paid, remaining, *, currency="SAR"):
    return {"order_id": order, "order_number": "R-" + order, "provider": "tamara", "currency": currency,
            "gross": "100.00", "paid": paid, "remaining": remaining, "observed_at": NOW,
            "source": "canonical_salla_order_page"}


def test_page_breakdown_keeps_remaining_with_customer_and_held_amounts_out_of_debt():
    s = state_for()
    opening(s, "provider-start", "provider", "500.00", party="tamara")
    opening(s, "bank-start", "bank", "90.00")
    obligation(s, "provider:part", "provider", "provider", "40.00", direction="receivable", party="tamara")
    s["engine"]["facts"] = {"provider_order_balance:part": page("part", "40.00", "60.00")}
    candidate = {"code": "provider_historical_capture_review_required", "source_id": "provider:old",
                 "order_page_balance_review": page("old", "100.00", "0.00", currency="QAR")}
    s["engine"]["issues"] = [candidate, deepcopy(candidate)]
    before = deepcopy(s)
    result = build_financial_position(s, generated_at=NOW)
    rows = table(result, "provider_order_balances")["rows"]
    assert [(r["orders"],r["paid"],r["remaining"]) for r in rows] == [(1, "40.00", "60.00")]
    held = table(result, "provider_order_balances", currency="QAR")
    assert [(r["orders"],r["paid"],r["remaining"]) for r in held["rows"]] == [(1,"100.00","0.00")]
    assert not held["show_totals"] and "مراجعة" in held["rows"][0]["status"]
    assert result["currencies"]["SAR"]["debt_totals"]["receivable"] == "540.00"
    assert result["currencies"]["SAR"]["metrics"]["liquidity"] == "90.00"
    assert result["currencies"]["QAR"]["debt_totals"]["receivable"] == "0.00"
    assert s == before
