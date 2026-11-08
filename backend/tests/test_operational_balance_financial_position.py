"""The financial report projects one operational snapshot without creating money."""
from copy import deepcopy
import re

import pytest

from operational_balance_engine import reconcile
from operational_balance_store import initial
from operational_financial_position import build_financial_position


START = "2026-11-01T00:00:00+03:00"
NOW = "2026-11-01T12:00:00+03:00"


def state_for():
    state = initial("synthetic-owner")
    state.update(status="active", started_at=START)
    state["engine"] = {"as_of": NOW, "obligations": {}, "facts": {}, "issues": []}
    return state


def opening(state, identity, kind, amount, direction="for_us", *, currency="SAR", party=None):
    state["openings"][identity] = {
        "id": identity, "party_type": kind, "party_id": party or identity,
        "name": identity, "currency": currency, "amount": amount, "direction": direction,
    }


def obligation(state, identity, kind, party_type, amount, *, party="party-one", expected="0.00",
               direction="payable", currency="SAR", **extra):
    row = {"id": identity, "kind": kind, "party_type": party_type, "party_id": party,
           "name": party, "currency": currency, "confirmed": amount, "expected": expected,
           "direction": direction, "business_date": "2026-11-01", **extra}
    state["engine"]["obligations"][identity] = row
    return row


def table(result, identity, currency="SAR"):
    return next(row for row in result["currencies"][currency]["tables"] if row["id"] == identity)


def test_actual_money_debts_prepaid_credit_and_custody_are_disjoint_and_pure():
    state = state_for()
    opening(state, "bank", "bank", "1000.00")
    opening(state, "cash", "cash", "100.00")
    opening(state, "terminal", "pos_receivable", "50.00")
    opening(state, "supplier-debt", "supplier", "200.00", "for_party")
    opening(state, "supplier-advance", "supplier", "50.00")
    opening(state, "employee-debt", "employee", "80.00", "for_party", party="employee")
    opening(state, "employee-advance", "employee", "20.00", party="employee")
    opening(state, "external-receivable", "external_person", "70.00")
    opening(state, "external-payable", "external_person", "30.00", "for_party")
    opening(state, "ad-credit", "ad_account", "40.00", party="ad")
    opening(state, "ad-debt", "ad_account", "10.00", "for_party", party="ad")
    opening(state, "custody", "employee_custody", "60.00")
    opening(state, "carrier-collection", "courier", "80.00")
    opening(state, "carrier-fees", "courier", "20.00", "for_party")
    opening(state, "provider-capture", "provider", "100.00")
    before = deepcopy(state)

    result = build_financial_position(state, generated_at=NOW)
    currency = result["currencies"]["SAR"]
    assert result["authority"] == "operational_balance_report"
    assert currency["metrics"]["liquidity"] == "1100.00"
    assert currency["debt_totals"] == {"receivable": "350.00", "payable": "320.00"}
    assert currency["metrics"]["monetary_position"] == "1130.00"
    assert currency["metrics"]["prepaid_ad_credit"] == "40.00"
    assert currency["metrics"]["employee_custody"] == "60.00"
    assert currency["metrics"]["displayed_position"] == "1230.00"
    assert table(result, "banks_network")["totals"]["available"] == "1000.00"
    assert table(result, "banks_network")["totals"]["unsettled"] == "50.00"
    assert table(result, "employees")["totals"]["payable"] == "60.00"
    assert currency["completeness"] == {"actual": True, "forecast": False, "profit": False}
    assert currency["metrics"]["net_profit"] is None
    assert currency["metrics"]["forecast_monetary_position"] is None
    assert state == before
    assert result == build_financial_position(state, generated_at=NOW)


@pytest.mark.parametrize("status", ["draft", "frozen", "accounting_active"])
def test_inactive_snapshot_does_not_present_current_balances(status):
    state = state_for()
    opening(state, "bank", "bank", "100.00")
    state["status"] = status
    result = build_financial_position(state, generated_at=NOW)
    assert result["status"] == status
    assert result["currencies"] == {} and result["currency_order"] == []


def test_exact_decimals_and_currencies_never_mix():
    state = state_for()
    opening(state, "large", "bank", "9007199254740992.01")
    opening(state, "cents", "cash", "0.02")
    opening(state, "foreign", "bank", "12.34", currency="USD")
    result = build_financial_position(state, generated_at=NOW)
    assert result["currency_order"] == ["SAR", "USD"]
    assert result["currencies"]["SAR"]["metrics"]["liquidity"] == "9007199254740992.03"
    assert result["currencies"]["USD"]["metrics"]["liquidity"] == "12.34"


def test_every_authoritative_debt_survives_category_projection():
    state = state_for()
    opening(state, "bank", "bank", "1000.00")
    opening(state, "expense-opening", "operating_expense", "250.00", "for_party")
    opening(state, "owner-correction", "owner_withdrawal", "25.00")
    result = build_financial_position(state)
    assert result["currencies"]["SAR"]["debt_totals"] == {"receivable": "25.00", "payable": "250.00"}
    assert result["currencies"]["SAR"]["metrics"]["monetary_position"] == "775.00"
    assert table(result, "other_balances")["totals"]["payable"] == "250.00"
    assert result["currencies"]["SAR"]["metrics"]["net_profit"] is None


def test_missing_cost_and_profit_evidence_is_unavailable_never_zero():
    state = state_for()
    opening(state, "bank", "bank", "100.00")
    result = build_financial_position(state, generated_at=NOW)
    for key in ("products_unreceived", "products_received_uninvoiced", "shipping_expected", "cod_expected"):
        assert table(result, key)["complete"] is False
        assert table(result, key)["totals"]["amount"] is None
    profit = result["currencies"]["SAR"]["profit_rows"]
    assert profit and all(row["amount"] is None for row in profit)
    assert all(row["reason"].startswith("غير متاح:") for row in profit)


def source_order(identity="order-one", **extra):
    return {"id": identity, "created_at": "2026-11-01T01:00:00+03:00",
            "updated_at": "2026-11-01T02:00:00+03:00", "currency": "SAR",
            "status": "in_progress", "items": [], **extra}


def provider_state(*, captured="100.00", cancelled="0.00"):
    state = state_for()
    opening(state, "bank", "bank", "1000.00")
    payment = {"provider": "provider-one", "gross": "100.00", "cancelled": cancelled,
               "captures": ([{"id": "capture-one", "amount": captured, "captured_at": NOW}]
                            if captured != "0.00" else [])}
    policy = {"id": "fee-policy", "provider": "provider-one", "currency": "SAR", "status": "active",
              "effective_from": "2026-01-01", "percentage": "10", "fixed_amount": "0",
              "vat_treatment": "exempt", "cancellation_fee_treatment": "retain"}
    return reconcile(state, {"orders": [source_order(payment=payment)], "fee_policies": [policy]}, NOW)


def test_provider_net_cash_and_actual_fees_are_not_subtracted_twice():
    state = provider_state()
    state["movements"] = [{"id": "settlement-one", "kind": "settlement", "direction": "incoming",
                           "party_type": "provider", "party_id": "provider-one", "currency": "SAR",
                           "bank_kind": "bank", "bank_id": "bank", "amount": "35.00", "actual_fee_amount": "5.00",
                           "allocations": [{"obligation_id": "provider:order-one", "amount": "40.00"}]}]
    before = deepcopy(state)
    result = build_financial_position(state)
    assert result["currencies"]["SAR"]["metrics"]["liquidity"] == "1035.00"
    assert table(result, "providers")["totals"] == {"name": "الإجمالي", "orders": 1, "gross": "100.00",
                                                     "fees": "11.00", "refunded": "0.00", "net": "54.00",
                                                     "tariff_fees": "10.00", "tariff_net": "90.00",
                                                     "actual_settlement_fees": "5.00", "known_actual_settlement_fees": "5.00",
                                                     "fee_variance": None}
    assert table(result, "provider_collections_expected")["totals"]["amount"] == "0.00"
    assert result["currencies"]["SAR"]["metrics"]["monetary_position"] == "1089.00"
    assert state == before


def test_uncaptured_provider_forecast_is_net_after_one_fee_deduction():
    result = build_financial_position(provider_state(captured="0.00"))
    assert table(result, "providers")["totals"]["net"] == "0.00"
    # The engine's expected obligation is net 90. Gross 100 minus a single 10
    # future fee is 90; using expected 90 and deducting 10 again would be 80.
    assert table(result, "provider_collections_expected")["rows"][0]["amount"] == "90.00"
    assert table(result, "providers")["rows"][0]["fees"] == "0.00"
    assert table(result, "provider_collections_expected")["complete"] is False


def test_net_zero_provider_with_retained_fee_is_unknown_never_invented_zero():
    result = build_financial_position(provider_state(captured="0.00", cancelled="100.00"))
    assert table(result, "providers")["rows"][0]["fees"] is None
    assert table(result, "providers")["complete"] is False


def test_missing_provider_policy_cannot_disappear_as_complete_zero_fees():
    state = state_for()
    opening(state, "bank", "bank", "1000.00")
    order = source_order(payment={"provider": "provider-one", "gross": "100.00",
                                  "captures": [{"id": "capture-one", "amount": "100.00", "captured_at": NOW}]})
    state = reconcile(state, {"orders": [order], "fee_policies": []}, NOW)
    assert "provider:order-one" not in state["engine"]["obligations"]
    result = build_financial_position(state)
    assert table(result, "providers")["complete"] is False
    assert table(result, "providers")["totals"]["fees"] is None
    assert table(result, "provider_collections_expected")["complete"] is False
    assert table(result, "provider_collections_expected")["totals"]["amount"] is None
    assert result["currencies"]["SAR"]["metrics"]["monetary_position"] is None


def cost_exposure(*, complete=True):
    groups = [
        ("prestart", "supplier", "awaiting_review", "supplier", "10.00", 2, 1),
        ("poststart", "supplier", "under_fulfillment", "supplier", "20.00", 1, 1),
        ("prestart", "supplier", "received_without_approved_invoice", "supplier", "30.00", 2, 1),
        ("poststart", "supplier", "received_without_approved_invoice", "supplier", "40.00", 1, 1),
        ("prestart", "courier", "not_delivered", "carrier", "5.00", 1, 1),
        ("poststart", "shipping", "not_delivered", "carrier", "7.00", 1, 1),
        ("poststart", "supplier", "confirmed_in_financial_balances", "supplier", "900.00", 1, 1),
    ]
    return {"status": "preview", "as_of": NOW, "engine_as_of": NOW, "started_at": START,
            "complete": complete, "issues": [] if complete else [{"code": "supplier_identity_missing", "count": 1}],
            "currencies": {"SAR": {
                "prestart_supplier_unreceived": "10.00", "poststart_supplier_unreceived": "20.00",
                "prestart_supplier_received_uninvoiced": "30.00", "poststart_supplier_received_uninvoiced": "40.00",
                "prestart_shipping_expected": "5.00", "current_shipping_expected": "7.00",
                "prestart_supplier_expected": "40.00", "current_supplier_expected": "60.00",
                "current_supplier_confirmed": "900.00", "current_shipping_confirmed": "50.00"}},
            "groups": [{"period": period, "kind": kind, "stage": stage, "party_id": party,
                        "party_name": party, "party_type": kind, "currency": "SAR", "amount": amount,
                        "units": units, "orders": orders}
                       for period, kind, stage, party, amount, units, orders in groups]}


def test_cost_stage_buckets_replace_rollups_and_never_add_confirmed_costs():
    state = state_for()
    opening(state, "bank", "bank", "100.00")
    obligation(state, "supplier:order:item", "supplier", "supplier", "0.00", expected="999.00")
    obligation(state, "shipping:order", "shipping", "courier", "50.00", expected="123.00")
    exposure = cost_exposure()
    before = deepcopy((state, exposure))
    result = build_financial_position(state, cost_exposure=exposure)
    assert table(result, "products_unreceived")["totals"]["amount"] == "30.00"
    assert table(result, "products_received_uninvoiced")["totals"]["amount"] == "70.00"
    assert table(result, "shipping_expected")["totals"]["amount"] == "12.00"
    # Groups overlap by order and source units do not necessarily mean pieces.
    assert table(result, "products_unreceived")["totals"]["orders"] is None
    assert all(row["quantity"] is None for row in table(result, "products_unreceived")["rows"])
    assert result["currencies"]["SAR"]["metrics"]["forecast_monetary_position"] is None
    assert (state, exposure) == before


def test_partial_cost_projection_preserves_visible_evidence_but_not_fake_full_total():
    state = state_for()
    opening(state, "bank", "bank", "100.00")
    result = build_financial_position(state, cost_exposure=cost_exposure(complete=False))
    products = table(result, "products_unreceived")
    assert products["rows"][0]["amount"] == "10.00"
    assert products["totals"]["amount"] is None and products["complete"] is False
    assert any(issue["code"] == "supplier_identity_missing" for issue in result["issues"])
    assert result["currencies"]["SAR"]["completeness"]["actual"] is True


def test_empty_complete_stage_snapshot_is_proven_zero_only_for_its_cost_scope():
    state = state_for()
    opening(state, "bank", "bank", "100.00")
    exposure = {"status": "preview", "complete": True, "currencies": {}, "groups": [], "issues": []}
    result = build_financial_position(state, cost_exposure=exposure)
    assert table(result, "products_unreceived")["complete"] is True
    assert table(result, "products_unreceived")["totals"]["amount"] == "0.00"
    assert table(result, "cod_expected")["totals"]["amount"] is None


def delivery_state(method="cash", *, confirmed=True, delivered=True, courier=False, prepaid=False):
    state = state_for()
    opening(state, "bank", "bank", "1000.00")
    carrier = {"id": "carrier" if courier else "driver", "party_type": "courier" if courier else "store_driver",
               "cost": "12.00", "fee_components": [
                   {"id": "base_shipping", "amount": "10.00", "complete": True},
                   {"id": "cod_commission", "amount": "2.00", "complete": True}]}
    order = source_order(status="delivered" if delivered else "in_progress", carrier=carrier)
    if not prepaid:
        cash = method == "cash"
        collection_proven = delivered and (cash or confirmed) and not courier
        cod = {"gross": "300.00", "collected": "300.00" if collection_proven else "100.00",
               "outstanding": "0.00" if collection_proven else "200.00",
               "custody_amount": "200.00" if delivered and (cash or courier) else "0.00",
               "payment_method": "cod" if courier else method if delivered else "cod",
               "evidence_ids": ["collection-one"] if delivered and not courier else [],
               "evidence_complete": delivered or courier}
        if delivered and not courier:
            cod.update(collection_amount="200.00", collection_confirmed=cash or confirmed)
        order.update(cod=cod, cod_amount=cod["custody_amount"], cod_required=True)
    return reconcile(state, {"orders": [order]}, NOW), order


def delivery(result):
    return result["currencies"]["SAR"]["deliveries"]["rows"][0]


def test_driver_cash_responsibility_is_separate_from_delivered_fee_and_bank():
    state, _ = delivery_state()
    result = build_financial_position(state)
    row = delivery(result)
    assert row["shipping_confirmed"] == row["shipping_outstanding"] == "12.00"
    assert row["driver_custody"] == row["collection_outstanding"] == "200.00"
    assert row["customer_outstanding"] == "0.00"
    assert row["collection_destination"] == "driver_custody" and row["collection_status"] == "confirmed"
    assert row["store_collection"] is None and row["order_number"] is None
    assert table(result, "drivers")["totals"]["receivable"] == "200.00"
    assert table(result, "drivers")["totals"]["payable"] == "12.00"
    assert result["currencies"]["SAR"]["metrics"]["liquidity"] == "1000.00"
    assert table(result, "cod_expected")["rows"] == []


@pytest.mark.parametrize("method,destination", [("bank_transfer", "store_bank_account_unknown"),
                                               ("card_terminal", "store_pos_account_unknown")])
def test_non_cash_collection_requires_explicit_review_and_never_creates_driver_cash(method, destination):
    state, order = delivery_state(method, confirmed=False)
    pending = build_financial_position(state)
    row = delivery(pending)
    assert row["collection_status"] == "pending_review"
    assert row["collection_destination"] == destination
    assert row["driver_custody"] == "0.00"
    assert row["store_collection"] is None and row["customer_outstanding"] == "200.00"
    assert table(pending, "drivers")["totals"]["receivable"] == "0.00"
    order["updated_at"] = "2026-11-01T03:00:00+03:00"
    order["cod"].update(collection_confirmed=True, collected="300.00", outstanding="0.00")
    state = reconcile(state, {"orders": [order]}, NOW)
    confirmed = build_financial_position(state)
    row = delivery(confirmed)
    assert row["collection_status"] == "confirmed" and row["store_collection"] == "200.00"
    assert row["driver_custody"] == "0.00" and row["customer_outstanding"] == "0.00"
    assert row["collection_destination"] == destination
    assert confirmed["currencies"]["SAR"]["metrics"]["liquidity"] == "1000.00"
    assert table(confirmed, "banks_network")["totals"]["unsettled"] == "0.00"
    assert confirmed["currencies"]["SAR"]["deliveries"]["complete"] is False


def test_prepaid_delivered_order_remains_visible_without_any_cod_report():
    state, _ = delivery_state(prepaid=True)
    assert state["engine"]["cod_reports"] == {}
    row = delivery(build_financial_position(state))
    assert row["order_id"] == "order-one" and row["shipping_confirmed"] == "12.00"
    assert row["collection_status"] == "not_applicable"
    assert row["collection_amount"] is None and row["customer_outstanding"] is None


def test_carrier_customer_source_remaining_is_not_counted_twice_and_settlement_reduces_only_custody():
    state, _ = delivery_state(courier=True)
    state["movements"] = [{"id": "carrier-remittance", "kind": "settlement", "direction": "incoming",
                           "party_type": "courier", "party_id": "carrier", "currency": "SAR",
                           "bank_kind": "bank", "bank_id": "bank", "amount": "80.00",
                           "allocations": [{"obligation_id": "cod:order-one", "amount": "80.00"}]}]
    before = deepcopy(state)
    result = build_financial_position(state)
    row = delivery(result)
    assert state == before
    assert row["collection_status"] == "courier_collection_unverified"
    assert row["courier_responsibility"] == "200.00"
    assert row["collection_amount"] is row["driver_custody"] is row["store_collection"] is None
    assert result["currencies"]["SAR"]["deliveries"]["complete"] is False
    assert row["collection_outstanding"] == "120.00" and row["collection_settled"] == "80.00"
    assert row["customer_outstanding"] == "200.00" and row["driver_custody"] is None
    assert row["collection_destination"] == "courier_receivable"
    assert result["currencies"]["SAR"]["metrics"]["debts_receivable"] == "120.00"
    assert result["currencies"]["SAR"]["metrics"]["monetary_position"] == "1188.00"


def test_undelivered_driver_forecast_uses_proven_customer_due_not_zero_cash_custody():
    state, _ = delivery_state(delivered=False)
    assert state["engine"]["obligations"]["cod:order-one"]["expected"] == "0.00"
    result = build_financial_position(state)
    expected = table(result, "cod_expected")
    assert len(expected["rows"]) == 1
    assert expected["rows"][0]["amount"] == expected["rows"][0]["known_due"] == "200.00"
    assert expected["totals"]["amount"] is None
    assert delivery(result)["driver_custody"] is None
    assert result["currencies"]["SAR"]["metrics"]["debts_receivable"] == "0.00"


@pytest.mark.parametrize("status", ["cancelled", "in_progress"])
def test_customer_due_without_positive_active_shipping_evidence_is_unknown_not_zero(status):
    state, order = delivery_state(delivered=False)
    order.update(status=status, updated_at="2026-11-01T03:00:00+03:00")
    if status == "in_progress":
        order["carrier"].update(cost="0.00", fee_components=[])
        state = state_for()
        opening(state, "bank", "bank", "1000.00")
    state = reconcile(state, {"orders": [order]}, NOW)
    row = table(build_financial_position(state), "cod_expected")["rows"][0]
    assert row["amount"] is None and row["known_due"] == "200.00"


def test_delivery_executor_and_fee_remain_frozen_after_later_reassignment():
    state, order = delivery_state()
    order.update(updated_at="2026-11-01T03:00:00+03:00", carrier={"id": "replacement", "party_type": "store_driver", "cost": "999.00"})
    state = reconcile(state, {"orders": [order]}, NOW)
    row = delivery(build_financial_position(state))
    assert row["party_id"] == "driver" and row["shipping_confirmed"] == "12.00"


def test_delivery_without_collection_proof_never_infers_review_from_delivered_status():
    state, _ = delivery_state("bank_transfer")
    del state["engine"]["facts"]["cod_collection:order-one"]
    result = build_financial_position(state)
    row = delivery(result)
    assert row["delivered_at"] is not None
    assert row["collection_status"] == "unavailable"
    assert row["store_collection"] is row["driver_custody"] is None


def test_old_confirmed_cod_without_rich_metrics_is_not_mislabeled_prepaid():
    state = state_for()
    opening(state, "bank", "bank", "1000.00")
    obligation(state, "shipping:legacy", "shipping", "store_driver", "12.00", party="driver")
    obligation(state, "cod:legacy", "cod", "store_driver", "200.00", party="driver", direction="receivable")
    state["engine"]["facts"]["shipping:legacy"] = {"id": "driver", "party_type": "store_driver", "delivered_at": NOW}
    row = delivery(build_financial_position(state))
    assert row["collection_status"] != "not_applicable"
    assert row["collection_outstanding"] == "200.00"
    assert row["driver_custody"] is None and row["store_collection"] is None


def synthetic_report_fixture():
    """Local UI fixture made entirely from synthetic evidence; never persisted."""
    state = state_for()
    for identity, kind, amount, direction in (
        ("Test bank", "bank", "1000.00", "for_us"),
        ("Test cashbox", "cash", "100.00", "for_us"),
        ("Test terminal", "pos_receivable", "50.00", "for_us"),
        ("Test supplier", "supplier", "200.00", "for_party"),
        ("Test employee", "employee", "80.00", "for_party"),
        ("Test external", "external_person", "70.00", "for_us"),
        ("Test ad account", "ad_account", "40.00", "for_us"),
        ("Test custody", "employee_custody", "60.00", "for_us"),
    ):
        opening(state, identity, kind, amount, direction)
    opening(state, "Test USD bank", "bank", "12.34", currency="USD")
    orders = []
    for index, options in enumerate(({}, {"method": "bank_transfer", "confirmed": False},
                                    {"method": "bank_transfer"}, {"method": "card_terminal", "confirmed": False},
                                    {"method": "card_terminal"}, {"courier": True}, {"prepaid": True},
                                    {"delivered": False}), 1):
        _, order = delivery_state(**options)
        order["id"] = "test-order-" + str(index)
        if order.get("cod", {}).get("evidence_ids"):
            order["cod"]["evidence_ids"] = ["test-collection-" + str(index)]
        orders.append(order)
    payment = {"provider": "Test payment platform", "gross": "100.00",
               "captures": [{"id": "test-provider-capture", "amount": "100.00", "captured_at": NOW}]}
    orders.append(source_order("test-provider-order", payment=payment))
    policy = {"id": "test-fee-policy", "provider": "Test payment platform", "currency": "SAR", "status": "active",
              "effective_from": "2026-01-01", "percentage": "10", "fixed_amount": "0", "vat_treatment": "exempt"}
    state = reconcile(state, {"orders": orders, "fee_policies": [policy]}, NOW)
    return build_financial_position(state, cost_exposure=cost_exposure(), generated_at=NOW)


def test_public_table_contract_keeps_exact_nullable_money_and_stable_rows():
    fixture = synthetic_report_fixture()
    required = {"banks_network", "cash", "suppliers", "employees", "external", "ads", "employee_custody",
                "couriers", "drivers", "providers", "shipping_expected", "products_unreceived",
                "products_received_uninvoiced", "cod_expected", "provider_collections_expected"}
    for currency in fixture["currencies"].values():
        assert required <= {table["id"] for table in currency["tables"]}
        for tab in currency["tables"]:
            assert len({row["id"] for row in tab["rows"]}) == len(tab["rows"])
            for row in [*tab["rows"], tab["totals"]]:
                for column in tab["columns"]:
                    assert column["key"] in row
                    value = row[column["key"]]
                    if column["type"] == "money" and value is not None:
                        assert isinstance(value, str) and re.fullmatch(r"-?\d+\.\d{2}", value)
                    if column["type"] == "count" and value is not None:
                        assert isinstance(value, int) and not isinstance(value, bool)
    assert len(fixture["currencies"]["SAR"]["deliveries"]["rows"]) == 8
    assert fixture == synthetic_report_fixture()


def test_uncollected_courier_forecast_never_has_booked_responsibility_or_collectors_cash():
    state, _ = delivery_state(courier=True, delivered=False)
    row = delivery(build_financial_position(state))
    assert row["courier_responsibility"] is None
    assert row["collection_amount"] is None and row["store_collection"] is None
    assert row["collection_status"] != "confirmed"
