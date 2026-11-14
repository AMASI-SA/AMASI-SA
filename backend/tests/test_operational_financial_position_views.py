"""Scoped report reads preserve saved money and exclude unopened work."""
import asyncio
from copy import deepcopy
from types import SimpleNamespace
import sys
from urllib.parse import urlencode
from threading import Event

import pytest

from operational_balance_store import STATES, digest
from operational_financial_position import build_financial_position
from operational_financial_position_views import TABLES, PARTY_VIEWS
from test_operational_balance_financial_position import (
    NOW, START, cost_exposure, delivery_state, obligation, opening, provider_state, state_for,
)
from test_operational_balance_financial_position_routes import get
from test_operational_balance_supplier_workspace import ReadOnlyDB


def forbidden(*_args, **_kwargs):
    raise AssertionError("an unopened report section performed work")


def test_summary_route_reads_saved_sources_without_forecast_or_order_lookup(monkeypatch):
    import operational_financial_order_labels
    monkeypatch.setitem(sys.modules, "operational_open_cost_exposure", SimpleNamespace(project=forbidden))
    monkeypatch.setattr(operational_financial_order_labels, "attach_order_numbers", forbidden)
    db = ReadOnlyDB()
    before = deepcopy(db.data)
    response = asyncio.run(get(db, suffix="?view=summary"))
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["view"] == "summary" and result["revision"] == before[STATES][0]["revision"]
    assert result["forecast_pending"] is True
    assert result["as_of"] == before[STATES][0]["engine"]["as_of"]
    data = result["currencies"]["SAR"]
    assert data["debt_totals"]["payable"] == "9135.00"
    assert data["current_rows"] and data["profit_rows"] and data["debt_rows"]
    assert data["tables"] == [] and "deliveries" not in data
    assert data["forecast_rows"] == [] and data["metrics"]["forecast_monetary_position"] is None
    assert db.data == before


def rich_state():
    from operational_balance_engine import reconcile
    from operational_customer_wallet import _evidence
    from operational_profit_projection import LABELS, SOURCE, VERSION
    state = provider_state(captured="50.00")
    state["revision"] = 7
    for identity, kind, value, direction in (
        ("cash", "cash", "100.00", "for_us"),
        ("network", "pos_receivable", "50.00", "for_us"),
        ("supplier", "supplier", "200.00", "for_party"),
        ("employee", "employee", "80.00", "for_party"),
        ("custody", "employee_custody", "60.00", "for_us"),
        ("external", "external_person", "70.00", "for_us"),
        ("ads", "ad_account", "40.00", "for_us"),
        ("other-debt", "operating_expense", "25.00", "for_party"),
    ):
        opening(state, identity, kind, value, direction)
    opening(state, "foreign-bank", "bank", "12.34", currency="USD")
    opening(state, "foreign-ad", "ad_account", "10.00", "for_party", currency="USD")
    state["engine"]["ad_balance_sar_basis"] = {"foreign-ad": {
        "currency": "USD", "source": "mezan_ad_account_cost_settings_v2",
        "exchange_rate_to_sar": "3.75", "bank_commission_pct": "2.00",
        "bank_commission_vat_pct": "15", "apply_bank_commission": True,
    }}
    orders = []
    for courier in (False, True):
        _, order = delivery_state(courier=courier)
        order["id"] = "courier-order" if courier else "driver-order"
        orders.append(order)
    state = reconcile(state, {"orders": orders}, NOW)
    evidence = _evidence({"merchant_id": "synthetic-store", "currency": "SAR", "as_of": START,
        "remaining_balance": "75.00", "source_reference": "synthetic-wallet-statement"})
    state["customer_wallet_snapshots"] = {"SAR": {**evidence, "id": "wallet",
        "confirmed_by": state["owner_id"], "confirmed_at": NOW, "evidence_hash": digest(evidence)}}
    state["engine"]["customer_tender_coverage"] = {"by_currency": {"SAR": [{
        "method": "wallet", "name": "wallet", "orders": 1, "paid": "5.00", "remaining": "0.00",
        "incomplete_orders": 0,
    }]}}
    state["engine"]["profit_snapshot"] = {
        "version": VERSION, "source": SOURCE, "owner_id": state["owner_id"], "currency": "SAR", "as_of": NOW,
        "period": {"from": START, "to": NOW, "timezone": "Asia/Riyadh"},
        "components": {key: {"amount": "300.00" if key == "net_sales" else
            "0.00" if key in {"financing", "tax"} else "10.00", "complete": True}
            for key in LABELS if key not in {"operating_profit", "net_profit"}}, "quality": {"complete": True},
    }
    state["engine"].setdefault("issues", []).extend([
        {"code": "courier_fee_unresolved", "source_id": "courier-unassigned", "currency": "SAR",
         "required_action": "راجع الشحنة", "courier_fee_review": {
             "order_id": "courier-unassigned", "order_number": "SYN-C", "party_name": "شركة غير مثبتة", "currency": "SAR"}},
        {"code": "driver_fee_unresolved", "source_id": "driver-unassigned", "currency": "SAR",
         "required_action": "راجع الأجرة", "driver_fee_review": {
             "order_id": "driver-unassigned", "order_number": "SYN-D", "party_name": "مندوب غير مثبت",
             "currency": "SAR", "candidate_amount": "9.00"}},
    ])
    return state


@pytest.mark.parametrize("view", [key for key in TABLES if key not in {"summary", "forecast"}])
def test_each_view_retains_exact_full_report_tables_and_money(view):
    state, exposure = rich_state(), cost_exposure()
    before = deepcopy((state, exposure))
    whole = build_financial_position(state, cost_exposure=exposure, generated_at=NOW)
    scoped = build_financial_position(state, cost_exposure=exposure, generated_at=NOW, view=view)
    assert scoped["view"] == view and scoped["revision"] == 7
    for currency, data in scoped["currencies"].items():
        baseline = whole["currencies"][currency]
        expected = []
        for table in baseline["tables"]:
            if table["id"] not in TABLES[view]:
                continue
            selected = deepcopy(table)
            if table["id"] == "balance_origin":
                selected["rows"] = [row for row in selected["rows"] if row["id"].startswith(PARTY_VIEWS[view] + ":")]
            expected.append(selected)
        assert [table for table in data["tables"] if not table["id"].startswith("unassigned_")] == expected
        for key in ("debt_rows", "debt_totals", "known_debt_totals", "current_rows", "profit_rows"):
            assert data[key] == baseline[key]
        assert data["completeness"]["actual"] == baseline["completeness"]["actual"]
        assert data["metrics"]["net_profit"] == baseline["metrics"]["net_profit"]
        if view in {"couriers", "drivers"}:
            assert data["deliveries"]["rows"] == [row for row in baseline["deliveries"]["rows"]
                if row["party_type"] == PARTY_VIEWS[view]]
        elif view != "expected":
            assert "deliveries" not in data
    assert (state, exposure) == before


def test_summary_omits_unopened_detail_builders_but_keeps_all_saved_financial_families(monkeypatch):
    import operational_financial_position_core as core
    state = rich_state()
    whole = build_financial_position(state, cost_exposure=cost_exposure(), generated_at=NOW)
    for name in ("_provider_table", "_balance_origin", "_cost_tables", "_provider_collection_table", "_cod_table", "_deliveries"):
        monkeypatch.setattr(core, name, forbidden)
    result = build_financial_position(state, generated_at=NOW, view="summary")
    for currency, data in result["currencies"].items():
        for key in ("debt_rows", "debt_totals", "known_debt_totals", "current_rows", "profit_rows"):
            assert data[key] == whole["currencies"][currency][key]
        assert data["tables"] == [] and "deliveries" not in data
    assert result["currencies"]["SAR"]["metrics"]["net_profit"] == "240.00"
    # Unassigned obligations keep the full total unknown, while all verified
    # families (including the wallet) remain in the independently known sums.
    assert result["currencies"]["SAR"]["metrics"]["debts_payable"] is None
    assert result["currencies"]["SAR"]["known_debt_totals"]["payable"] is not None
    assert next(row for row in result["currencies"]["SAR"]["debt_rows"]
                if row["id"] == "customer_wallets")["payable"] == "75.00"
    assert result["currencies"]["USD"]["metrics"]["liquidity"] == "12.34"


def test_forecast_response_can_replace_entire_summary_without_client_arithmetic():
    state, exposure = rich_state(), cost_exposure()
    whole = build_financial_position(state, cost_exposure=exposure, generated_at=NOW)
    forecast = build_financial_position(state, cost_exposure=exposure, generated_at=NOW, view="forecast")
    assert forecast["forecast_pending"] is False
    for currency, actual in forecast["currencies"].items():
        expected = deepcopy(whole["currencies"][currency])
        expected["tables"] = []
        for key in ("deliveries", "driver_fee_review", "shipping_fee_review_orders"):
            expected.pop(key, None)
        assert actual == expected
    assert forecast["issues"] == whole["issues"] and forecast["dependencies"] == whole["dependencies"]


def test_unassigned_exception_orders_remain_reachable_without_new_money():
    state = rich_state()
    whole = build_financial_position(state, cost_exposure=cost_exposure())
    expected = build_financial_position(state, cost_exposure=cost_exposure(), view="expected")
    baseline = {table["id"]: table for table in whole["currencies"]["SAR"]["tables"]}
    tables = {table["id"]: table for table in expected["currencies"]["SAR"]["tables"]}
    for kind, table_id in (("courier", "couriers"), ("store_driver", "drivers")):
        entries = baseline[table_id]["unassigned_order_details"]
        assert entries
        scoped = tables["unassigned_" + kind]
        assert scoped["unassigned_order_details"] == entries
        assert scoped["totals"] == {} and scoped["complete"] is False


@pytest.mark.parametrize("view", ["summary", "forecast", "accounts", "providers", "couriers", "drivers", "expected"])
def test_hidden_driver_approval_effect_conflict_still_invalidates_global_nets(view):
    state = state_for()
    opening(state, "bank", "bank", "100.00")
    state["driver_payment_review_facts"] = {"damaged": {"id": "damaged"}}
    result = build_financial_position(state, cost_exposure=cost_exposure(), view=view)
    data = result["currencies"]["SAR"]
    assert data["completeness"]["actual"] is False
    assert data["metrics"]["monetary_position"] is None
    assert data["metrics"]["displayed_position"] is None
    assert data["metrics"]["forecast_monetary_position"] is None
    assert data["metrics"]["liquidity"] == "100.00"
    assert any(issue["code"] == "operational_driver_review_effect_conflict" for issue in result["issues"])


@pytest.mark.parametrize("view", ["summary", "forecast", "providers", "couriers", "drivers"])
def test_valid_approval_for_a_different_delivery_executor_cannot_restore_complete_nets(view):
    from test_operational_balance_driver_review_report import recorded
    approved, _ = recorded("bank_transfer")
    state = state_for()
    state.update(_id="owner", owner_id="owner", driver_payment_review_facts=approved["driver_payment_review_facts"])
    state["movements"] = [{**row, "kind": "payment"} for row in approved["movements"]]
    opening(state, "bank", "bank", "100.00")
    obligation(state, "shipping:order", "shipping", "courier", "20.00", party="different-courier")
    state["engine"]["facts"]["shipping:order"] = {
        "id": "different-courier", "party_type": "courier", "delivered_at": NOW,
    }
    result = build_financial_position(state, cost_exposure=cost_exposure(), view=view)
    data = result["currencies"]["SAR"]
    assert any(issue["code"] == "operational_driver_review_executor_conflict" for issue in result["issues"])
    assert data["completeness"]["actual"] is False
    assert data["metrics"]["monetary_position"] is None
    assert data["metrics"]["forecast_monetary_position"] is None


def binding(db, view="forecast", **changes):
    state = db.data[STATES][0]
    params = {"view": view, "expected_revision": state["revision"],
        "expected_as_of": state["engine"]["as_of"], "expected_started_at": state["started_at"]}
    params.update(changes)
    return "?" + urlencode(params)


@pytest.mark.parametrize("suffix", ["?view=invalid", "?view=forecast", "?view=forecast&expected_revision=0",
    "?view=forecast&expected_revision=-1", "?view=all&expected_revision=0"])
def test_invalid_view_or_partial_binding_is_rejected_before_forecast_work(monkeypatch, suffix):
    monkeypatch.setitem(sys.modules, "operational_open_cost_exposure", SimpleNamespace(project=forbidden))
    response = asyncio.run(get(ReadOnlyDB(), suffix=suffix))
    assert response.status_code == 422


@pytest.mark.parametrize("view", ["forecast", "all"])
@pytest.mark.parametrize("changed", ["expected_revision", "expected_as_of", "expected_started_at"])
def test_mismatched_snapshot_binding_rejects_before_any_live_forecast_read(monkeypatch, view, changed):
    monkeypatch.setitem(sys.modules, "operational_open_cost_exposure", SimpleNamespace(project=forbidden))
    db = ReadOnlyDB()
    before = deepcopy(db.data)
    response = asyncio.run(get(db, suffix=binding(db, view, **{changed: 8 if changed == "expected_revision" else NOW})))
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "operational_financial_snapshot_changed"
    assert db.data == before


@pytest.mark.parametrize("view", ["forecast", "all"])
def test_bound_report_rejects_aggregate_change_during_cost_read(monkeypatch, view):
    async def preview(db, owner, state, *, as_of=None):
        db.data[STATES][0]["revision"] += 1  # Simulate a separate authorized writer.
        return {"status": "preview", "as_of": as_of, "engine_as_of": as_of,
            "started_at": state["started_at"], "currencies": {}, "groups": [], "issues": [], "complete": True}
    monkeypatch.setitem(sys.modules, "operational_open_cost_exposure", SimpleNamespace(project=preview))
    db = ReadOnlyDB()
    response = asyncio.run(get(db, suffix=binding(db, view)))
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "operational_financial_snapshot_changed"


def test_successful_forecast_response_has_the_exact_requested_identity(monkeypatch):
    from test_operational_balance_financial_position_routes import install_cost_reader
    calls = []
    install_cost_reader(monkeypatch, calls)
    db = ReadOnlyDB()
    before = deepcopy(db.data)
    response = asyncio.run(get(db, suffix=binding(db)))
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["view"] == "forecast" and result["forecast_pending"] is False
    assert result["revision"] == before[STATES][0]["revision"]
    assert result["as_of"] == before[STATES][0]["engine"]["as_of"]
    assert result["started_at"] == before[STATES][0]["started_at"]
    assert result["session_scope"] == digest(["owner", "owner"])
    assert result["currencies"]["SAR"]["forecast_rows"]
    assert result["currencies"]["SAR"]["metrics"]["forecast_monetary_position"] is None
    assert result["currencies"]["SAR"]["tables"] == []
    assert calls == [("owner", "owner", result["as_of"])] and db.data == before


@pytest.mark.parametrize("view", ["summary", "employees", "accounts", "suppliers", "advertising", "other"])
def test_balance_only_routes_do_not_call_live_cost_or_order_label_readers(monkeypatch, view):
    import operational_financial_order_labels
    monkeypatch.setitem(sys.modules, "operational_open_cost_exposure", SimpleNamespace(project=forbidden))
    monkeypatch.setattr(operational_financial_order_labels, "attach_order_numbers", forbidden)
    response = asyncio.run(get(ReadOnlyDB(), suffix="?view=" + view))
    assert response.status_code == 200, response.text


@pytest.mark.parametrize("stage", ["snapshot", "summary", "forecast"])
def test_disconnect_cancels_and_joins_each_scoped_read_stage(monkeypatch, stage):
    import operational_financial_position as position
    import operational_financial_position_state as saved
    from operational_financial_activity_runtime import preparation_checkpoint
    from test_operational_balance_visible_report_reads import app_for, begin_get, response

    async def scenario():
        db = ReadOnlyDB()
        before = deepcopy(db.data)
        started, stopped = Event(), Event()

        def cpu(*_args, **_kwargs):
            started.set()
            try:
                while True:
                    preparation_checkpoint()
            finally:
                stopped.set()

        async def source(*_args, **_kwargs):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                stopped.set()

        if stage == "snapshot":
            monkeypatch.setattr(saved, "_report_state", cpu)
        elif stage == "summary":
            monkeypatch.setattr(position, "build_financial_position", cpu)
        else:
            monkeypatch.setitem(sys.modules, "operational_open_cost_exposure", SimpleNamespace(project=source))
        suffix = binding(db) if stage == "forecast" else "?view=summary"
        task, queue, sent = begin_get(app_for(db), "/financial-position" + suffix)
        assert await asyncio.to_thread(started.wait, 2)
        queue.put_nowait({"type": "http.disconnect"})
        await asyncio.wait_for(task, 2)
        assert stopped.is_set()
        status, result = response(sent)
        assert status == 499 and result["detail"]["code"] == "operational_report_read_cancelled"
        assert db.data == before
        assert asyncio.all_tasks() == {asyncio.current_task()}
    asyncio.run(scenario())
