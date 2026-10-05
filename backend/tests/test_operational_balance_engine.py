"""Requirement-derived isolated financial invariants for the pure reducer."""
from copy import deepcopy
from decimal import Decimal
import pytest

from operational_balance_engine import reconcile, provider_projection
from datetime import date


START = "2026-10-05T09:00:00+00:00"
NOW = "2026-10-08T12:00:00+00:00"


def initial():
    return {"status": "active", "started_at": START}


def order(**kwargs):
    return {"id": "o1", "created_at": "2026-10-06T09:00:00+00:00", "updated_at": "2026-10-06T10:00:00+00:00",
            "currency": "SAR", "status": "in_progress", "items": [{"id": "i1", "cost": "100", "quantity": "2", "supplier_id": "s1"}],
            "carrier": {"id": "imile", "cost": "17.25"}, "cod_amount": "500", **kwargs}


def policy(**kwargs):
    return {"id": "f1", "provider": "tabby", "status": "active", "currency": "SAR", "percentage": "2",
            "fixed_amount": "1", "vat_treatment": "exempt", "effective_from": "2026-01-01", **kwargs}


def test_new_orders_only_and_input_is_immutable():
    state = initial()
    before = deepcopy(state)
    output = reconcile(state, {"orders": [order(created_at="2026-10-04T10:00:00+00:00")]}, NOW)
    assert output["engine"]["obligations"] == {}
    assert state == before


def test_duplicate_order_state_and_atomic_carrier_replacement_freeze_after_delivery():
    source = {"orders": [order()]}
    first = reconcile(initial(), source, NOW)
    assert reconcile(first, source, NOW) == first
    source["orders"][0].update(carrier={"id": "driver1", "party_type": "store_driver", "cost": "20"}, updated_at="2026-10-07T10:00:00+00:00")
    second = reconcile(first, source, NOW)
    shipping = second["engine"]["obligations"]["shipping:o1"]
    assert (shipping["party_id"], shipping["expected"], shipping["confirmed"]) == ("driver1", "20.00", "0.00")
    source["orders"][0]["status"] = "delivered"
    third = reconcile(second, source, NOW)
    source["orders"][0].update(status="cancelled", carrier={"id": "another", "cost": "99"}, cod_amount="0")
    final = reconcile(third, source, NOW)["engine"]["obligations"]
    assert final["shipping:o1"]["confirmed"] == "20.00"
    assert final["shipping:o1"]["party_id"] == "driver1"
    assert final["cod:o1"]["confirmed"] == "500.00"


def test_partial_supplier_receipt_and_accepted_return_are_not_customer_refunds():
    sources = {"orders": [order()], "supplier_receipts": [{"id": "r1", "order_id": "o1", "item_id": "i1", "supplier_id": "s1", "amount": "80", "accepted_at": NOW}]}
    first = reconcile(initial(), sources, NOW)
    rows = first["engine"]["obligations"]
    assert rows["supplier:o1:i1"]["expected"] == "120.00"
    assert rows["supplier:o1:i1:receipt:r1"]["confirmed"] == "80.00"
    sources["orders"][0]["status"] = "cancelled"
    sources["supplier_returns"] = [{"id": "ret1", "receipt_id": "r1", "amount": "30", "accepted": False, "accepted_at": NOW}]
    second = reconcile(first, sources, NOW)
    assert second["engine"]["obligations"]["supplier:o1:i1:receipt:r1"]["confirmed"] == "80.00"
    sources["supplier_returns"][0]["accepted"] = True
    third = reconcile(second, sources, NOW)
    assert third["engine"]["obligations"]["supplier:o1:i1:receipt:r1"]["confirmed"] == "50.00"
    assert third["engine"]["obligations"]["supplier:o1:i1"]["expected"] == "0.00"
    assert reconcile(third, sources, NOW) == third


def test_assignment_moves_only_unreceived_estimate():
    sources = {"orders": [order(items=[{"id": "i1", "cost": "100", "supplier_id": None}])]}
    first = reconcile(initial(), sources, NOW)
    assert first["engine"]["obligations"]["supplier:o1:i1"]["party_id"] is None
    sources["orders"][0]["items"][0]["supplier_id"] = "supplier2"
    second = reconcile(first, sources, NOW)
    assert second["engine"]["obligations"]["supplier:o1:i1"]["party_id"] == "supplier2"
    assert len([r for r in second["engine"]["obligations"].values() if r["kind"] == "supplier"]) == 1


def test_provider_fees_refunds_and_no_actual_bank_effect():
    payment = {"provider": "tabby", "currency": "SAR", "gross": "1000", "cancelled": "100", "refunds": [
        {"id": "ref1", "amount": "200", "status": "requested"}]}
    terms = policy(cancellation_fee_treatment="recalculate", refund_fee_treatment="recalculate")
    requested = provider_projection(payment, [terms], date(2026, 10, 6))
    assert requested["expected_receivable"] == "881.00"
    payment["refunds"][0]["status"] = "executed"
    payment["refunds"].append({"id": "ref1", "amount": "200", "status": "settled"})
    executed = provider_projection(payment, [terms], date(2026, 10, 6))
    assert executed["refunded"] == "200.00"
    assert executed["expected_receivable"] == "685.00"
    output = reconcile(initial(), {"orders": [order(payment=payment)], "fee_policies": [terms]}, NOW)
    assert output["engine"]["obligations"]["provider:o1"]["confirmed"] == "0.00"
    assert "movements" not in output


@pytest.mark.parametrize("policies,match", [([], "policy_incomplete"), ([policy(), policy(id="f2")], "ambiguous"),
    ([policy(vat_treatment="exclusive")], "amount_missing")])
def test_missing_ambiguous_or_incomplete_policy_fails_closed(policies, match):
    with pytest.raises(ValueError, match=match):
        provider_projection({"provider": "tabby", "currency": "SAR", "gross": "100"}, policies, date(2026, 10, 6))


def test_refund_terms_not_inferred_from_percentage_and_overlap_rejected():
    with pytest.raises(ValueError, match="refund_fee_treatment_incomplete"):
        provider_projection({"provider": "tabby", "currency": "SAR", "gross": "100", "refunds": [{"id": "x", "amount": "20", "status": "executed"}]}, [policy()], date(2026, 10, 6))
    with pytest.raises(ValueError, match="overlap"):
        provider_projection({"provider": "tabby", "currency": "SAR", "gross": "100", "cancelled": "90", "refunds": [{"id": "x", "amount": "20", "status": "executed"}]}, [policy()], date(2026, 10, 6))


def test_salary_next_riyadh_day_effective_salary_and_no_repeat():
    sources = {"employees": [{"id": "e1", "currency": "SAR", "salary_revisions": [
        {"effective_from": "2026-01-01", "salary": "3100", "status": "active"},
        {"effective_from": "2026-10-08", "salary": "6200", "status": "active"}]}]}
    output = reconcile(initial(), sources, NOW)
    days = output["engine"]["salary_days"]
    assert set(days) == {"salary:e1:2026-10-06", "salary:e1:2026-10-07", "salary:e1:2026-10-08"}
    assert [row["amount"] for row in days.values()] == ["100.00", "100.00", "200.00"]
    assert reconcile(output, sources, NOW) == output


def test_full_month_salary_rounding_conserves_amount():
    state = {"status": "active", "started_at": "2026-09-30T09:00:00+00:00"}
    sources = {"employees": [{"id": "e", "currency": "SAR", "salary_revisions": [{"effective_from": "2026-01-01", "salary": "3000", "status": "active"}]}]}
    result = reconcile(state, sources, "2026-10-31T10:00:00+00:00")
    assert sum(Decimal(x["amount"]) for x in result["engine"]["salary_days"].values()) == Decimal("3000")


def test_salary_hire_date_and_suspension():
    sources = {"employees": [{"id": "e", "currency": "SAR", "hire_date": "2026-10-07", "payroll_suspension_periods": [{"start_date": "2026-10-08"}],
        "salary_revisions": [{"effective_from": "2026-01-01", "salary": "3100", "status": "active"}]}]}
    output = reconcile(initial(), sources, NOW)
    assert list(output["engine"]["salary_days"].values())[0]["amount"] == "100.00"
    assert list(output["engine"]["salary_days"].values())[1]["amount"] == "0.00"


def test_ad_cumulative_close_and_explicit_correction():
    base = {"account_id": "a1", "date": "2026-10-06", "currency": "SAR", "funding_type": "postpaid"}
    snapshots = [dict(base, id=str(i), amount=str(value), observed_at=f"2026-10-06T{10+i}:00:00+00:00") for i, value in enumerate([200, 300, 450])]
    first = reconcile(initial(), {"ad_snapshots": snapshots}, NOW)
    key = "advertising:a1:2026-10-06"
    assert first["engine"]["obligations"][key]["expected"] == "450.00"
    close = dict(base, id="close", amount="450", observed_at="2026-10-07T00:00:00+00:00", complete=True, closed=True, day_ended=True)
    second = reconcile(first, {"ad_snapshots": [close]}, NOW)
    assert second["engine"]["obligations"][key]["confirmed"] == "450.00"
    assert reconcile(second, {"ad_snapshots": [close]}, NOW) == second
    close.update(amount="460", observed_at=NOW)
    third = reconcile(second, {"ad_snapshots": [close]}, NOW)
    assert third["engine"]["obligations"][key]["confirmed"] == "450.00"
    assert third["engine"]["issues"][0]["code"] == "advertising_closed_day_correction_required"
    close.update(correction_approved=True, correction_id="correction1")
    fourth = reconcile(third, {"ad_snapshots": [close]}, NOW)
    assert fourth["engine"]["obligations"][key]["confirmed"] == "460.00"


def test_recurring_does_not_create_bank_movement():
    source = {"recurring": [{"id": "rent-oct", "party_id": "landlord", "currency": "SAR", "amount": "1000", "due_at": NOW}]}
    result = reconcile(initial(), source, NOW)
    assert result["engine"]["obligations"]["recurring:rent-oct"]["expected"] == "1000.00"
    assert "movements" not in result


def test_provider_confirmed_capture_and_refund_survive_missing_later_evidence():
    payment = {"provider": "tabby", "gross": "1000", "captures": [{"id": "cap1", "amount": "1000", "captured_at": NOW}],
               "refunds": [{"id": "ref1", "amount": "100", "status": "executed"}]}
    sources = {"orders": [order(payment=payment)], "fee_policies": [policy(refund_fee_treatment="recalculate")]}
    first = reconcile(initial(), sources, NOW)
    assert first["engine"]["obligations"]["provider:o1"]["confirmed"] == "900.00"
    assert first["engine"]["obligations"]["provider:o1"]["expected"] == "0.00"
    assert first["engine"]["provider_reports"]["provider:o1"]["expected_receivable"] == "881.00"
    payment["captures"] = []
    payment["refunds"] = []
    second = reconcile(first, sources, NOW)
    assert second["engine"]["obligations"]["provider:o1"]["confirmed"] == "900.00"
    assert second["engine"]["provider_reports"]["provider:o1"]["refunded"] == "100.00"


def test_receipt_identity_conflict_and_excess_return_roll_back_order():
    receipt = {"id": "r1", "order_id": "o1", "item_id": "i1", "supplier_id": "s1", "amount": "80", "accepted_at": NOW}
    sources = {"orders": [order()], "supplier_receipts": [receipt]}
    first = reconcile(initial(), sources, NOW)
    sources["supplier_returns"] = [{"id": "ret1", "receipt_id": "r1", "amount": "90", "accepted": True, "accepted_at": NOW}]
    second = reconcile(first, sources, NOW)
    assert second["engine"]["facts"] == first["engine"]["facts"]
    assert second["engine"]["obligations"] == first["engine"]["obligations"]
    assert second["engine"]["issues"][0]["code"] == "supplier_return_exceeds_receipt"
    sources.pop("supplier_returns")
    receipt["amount"] = "100"
    third = reconcile(first, sources, NOW)
    assert third["engine"]["facts"] == first["engine"]["facts"]
    assert third["engine"]["issues"][0]["code"] == "confirmed_evidence_changed"


def test_missing_cost_is_incomplete_never_zero_and_stale_order_is_ignored():
    sources = {"orders": [order(items=[{"id": "i1", "cost": None}])]}
    output = reconcile(initial(), sources, NOW)
    assert output["engine"]["obligations"] == {}
    assert output["engine"]["issues"][0]["code"] == "amount_missing"
    first = reconcile(initial(), {"orders": [order()]}, NOW)
    stale = order(updated_at="2026-10-06T09:00:00+00:00", carrier={"id": "wrong", "cost": "999"})
    assert reconcile(first, {"orders": [stale]}, NOW)["engine"]["obligations"] == first["engine"]["obligations"]


def test_provider_exclusive_vat_minimum_and_currency_policy():
    projection = provider_projection({"provider": "tabby", "currency": "SAR", "gross": "100"},
        [policy(percentage="1", fixed_amount="0", minimum="5", maximum="10", vat_treatment="exclusive", vat_rate="15")], date(2026, 10, 6))
    assert projection["estimated_fees"] == "5.75"
    assert projection["expected_receivable"] == "94.25"


def test_february_leap_year_daily_salary_and_contract_end():
    state = {"status": "active", "started_at": "2028-01-31T09:00:00+00:00"}
    source = {"employees": [{"id": "e", "currency": "SAR", "effective_to": "2028-02-02",
        "salary_revisions": [{"effective_from": "2028-01-01", "salary": "2900", "status": "active"}]}]}
    out = reconcile(state, source, "2028-02-29T09:00:00+00:00")
    assert [x["amount"] for x in out["engine"]["salary_days"].values()] == ["100.00", "100.00"]


def test_source_item_split_removes_old_estimate_without_double_counting():
    first = reconcile(initial(), {"orders": [order()]}, NOW)
    second = reconcile(first, {"orders": [order(items=[{"id": "piece1", "cost": "100"}, {"id": "piece2", "cost": "100"}])]}, NOW)
    supplier = [r for r in second["engine"]["obligations"].values() if r["kind"] == "supplier"]
    assert sum(Decimal(r["expected"]) for r in supplier) == Decimal("200")
    assert second["engine"]["obligations"]["supplier:o1:i1"]["expected"] == "0.00"


def test_native_salary_suspension_return_date_is_eligible():
    source = {"employees": [{"id": "e", "currency": "SAR", "payroll_suspension_periods": [{"started_on": "2026-10-06", "returned_on": "2026-10-08"}],
        "salary_revisions": [{"effective_from": "2026-01-01", "salary": "3100", "status": "active"}]}]}
    output = reconcile(initial(), source, NOW)
    assert [r["amount"] for r in output["engine"]["salary_days"].values()] == ["0.00", "0.00", "100.00"]


def test_no_forbidden_imports_or_io_in_engine():
    import ast
    from pathlib import Path
    tree = ast.parse(Path(__file__).parents[1].joinpath("operational_balance_engine.py").read_text(encoding="utf-8"))
    imports = [node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
    assert not any("accounting" in name or "legacy" in name for name in imports)
    assert not any(isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                   and node.func.attr in {"insert_one", "update_one", "delete_one", "find", "find_one"} for node in ast.walk(tree))
