"""Supplier receipt costs agree across the three owner reporting surfaces."""
from copy import deepcopy
from dataclasses import replace
from datetime import date, datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from mongomock_motor import AsyncMongoMockClient

from sold_product_cost_details import add_group, detail_groups, finish_groups


def _cell(key, amount, *, status="costed", kind="product"):
    return {"key": key, "label": key, "kind": kind, "status": status,
            "unit_cost": amount}


def _mixed_cost(*, estimated_complete=True, same_price=False):
    expected = 40 if same_price else 50 if estimated_complete else 30
    return {
        "calculation_cost_available": estimated_complete,
        "line_total": 40 + expected,
        "cost_stage": "mixed",
        "missing_cost_reasons": [] if estimated_complete else ["option_selection_unresolved:service"],
        "cost_detail_units": [
            {"quantity": 1, "line_total": 40, "calculation_cost_available": True,
             "cost_stage": "actual", "missing_cost_reasons": [],
             "cost_detail_scope_complete": True,
             "cost_detail_cells": [_cell("base", 40)]},
            {"quantity": 1, "line_total": expected,
             "calculation_cost_available": estimated_complete,
             "cost_detail_scope_complete": estimated_complete,
             "cost_stage": "expected",
             "missing_cost_reasons": [] if estimated_complete else ["option_selection_unresolved:service"],
             "cost_detail_cells": [_cell("base", expected), *(
                 [] if estimated_complete else [_cell("service", None, status="unresolved", kind="service")]
             )]},
        ],
    }


def test_partial_receipt_keeps_actual_details_when_unreceived_choice_is_unknown():
    groups = detail_groups(_mixed_cost(estimated_complete=False), {}, Decimal(2))
    assert len(groups) == 2
    actual, estimated = groups
    assert (actual["complete"], actual["cost_stage"], actual["total_cost"]) == (True, "actual", 40)
    assert actual["missing_cost_reasons"] == []
    assert (estimated["complete"], estimated["cost_stage"], estimated["total_cost"]) == (False, "expected", None)
    assert estimated["known_total_cost"] == 30
    assert sum(group["quantity"] for group in groups) == 2
    assert sum(group["known_total_cost"] for group in groups) == 70


def test_equal_actual_and_estimated_prices_keep_their_receipt_basis():
    groups = detail_groups(_mixed_cost(same_price=True), {}, Decimal(2))
    row = {"_groups": {}, "_reasons": set()}
    for group in groups:
        add_group(row, group)
    finish_groups(row)
    assert len(row["cost_groups"]) == 2
    assert {group["cost_stage"] for group in row["cost_groups"]} == {"actual", "expected"}
    assert sum(group["total_cost"] for group in row["cost_groups"]) == 80


def test_mixed_detail_quantity_or_amount_mismatch_cannot_replace_authoritative_total():
    for key, value in (("quantity", 2), ("line_total", 999)):
        cost = _mixed_cost(estimated_complete=False)
        cost["cost_detail_units"][0][key] = value
        groups = detail_groups(cost, {}, Decimal(2))
        assert len(groups) == 1
        assert groups[0]["complete"] is False
        assert groups[0]["quantity"] == 2
        assert groups[0]["known_total_cost"] == 70


def test_complete_composition_union_has_no_false_missing_service_column():
    cost = _mixed_cost()
    cost["cost_detail_units"][0]["cost_detail_cells"] = [
        _cell("base", 30), _cell("supplier:service:manual", 10, kind="service"),
    ]
    row = {"_groups": {}, "_reasons": set()}
    for group in detail_groups(cost, {}, Decimal(2)):
        add_group(row, group)
    finish_groups(row)
    expected = next(group for group in row["cost_groups"] if group["cost_stage"] == "expected")
    assert next(cell for cell in expected["cells"] if cell["key"] == "supplier:service:manual")["status"] == "not_required"
    columns = {column["key"] for column in row["cost_columns"]}
    assert all({cell["key"] for cell in group["cells"]} == columns for group in row["cost_groups"])
    assert sum(group["total_cost"] for group in row["cost_groups"]) == 90


def test_old_snapshot_without_composition_proof_does_not_gain_absent_services():
    cost = _mixed_cost()
    cost["cost_detail_units"][0]["cost_detail_cells"] = [
        _cell("base", 30), _cell("supplier:service:manual", 10, kind="service"),
    ]
    cost["cost_detail_units"][1].pop("cost_detail_scope_complete")
    row = {"_groups": {}, "_reasons": set()}
    for group in detail_groups(cost, {}, Decimal(2)):
        add_group(row, group)
    finish_groups(row)
    expected = next(group for group in row["cost_groups"] if group["cost_stage"] == "expected")
    assert "supplier:service:manual" not in {cell["key"] for cell in expected["cells"]}


OWNER = "supplier-cost-report-owner"
DAY = "2026-11-12"
PARAMS = {"from_date": DAY, "to_date": DAY}


@pytest_asyncio.fixture
async def report_app(monkeypatch):
    import dashboard_v2_routes as dashboard
    import resource_governor
    from auth import DEFAULT_PAYMENT_METHODS, PAYMENT_FEE_DEFAULTS_VERSION
    from product_option_cost_routes import BINDINGS
    from product_v2_details_routes import COST_PROFILES
    from product_v2_routes import PRODUCTS
    from tests.test_dashboard_bundle_cost_authority import ReadOnlyDB

    db = AsyncMongoMockClient().supplier_cost_report_integration
    await db.settings.insert_one({
        "user_id": OWNER, "payment_methods": deepcopy(DEFAULT_PAYMENT_METHODS),
        "payment_fee_defaults_version": PAYMENT_FEE_DEFAULTS_VERSION,
        "report_included_statuses": ["تم التنفيذ"],
    })
    await db[PRODUCTS].insert_one({
        "user_id": OWNER, "salla_product_id": "p", "mezan_product_id": "mpv2_p",
        "sku": "SYNTHETIC-P", "name": "Synthetic product",
        "options": [{"id": "custom", "type": "radio", "name": "Customization",
                     "values": [{"id": "yes", "name": "Yes"}, {"id": "no", "name": "No"}]}],
    })
    await db[COST_PROFILES].insert_one({"user_id": OWNER, "salla_product_id": "p", "base_cost": 40})
    await db[BINDINGS].insert_one({
        "user_id": OWNER, "id": "custom-cost", "salla_product_id": "p",
        "option_id": "custom", "value_id": "yes", "option_name": "Customization",
        "value_name": "Yes", "mode": "direct", "direct_amount": 10, "quantity": 1,
    })
    order = {
        "user_id": OWNER, "order_number": "sale", "order_date": DAY,
        "order_status": "تم التنفيذ", "currency": "SAR", "total_amount": 200,
        "products": [{"order_item_id": "line", "product_id": "p", "quantity": 2,
                      "total": 200, "sku": "SYNTHETIC-P",
                      "options_raw": [{"option_id": "custom", "value_id": "yes"}]}],
    }
    await db.unified_orders.insert_one(deepcopy(order))
    await db.mezan_suppliers_v2.insert_one({"user_id": OWNER, "id": "supplier", "name": "Synthetic supplier"})

    async def receive(indexes, *, unit_halalas=4000, services=None, owner=OWNER,
                      invoice_id="invoice", status="payable_posted", experiment=False,
                      finalized=True):
        # Approval precedes the selected sale date on purpose: these reports
        # select sales, not invoices approved between their from/to dates.
        approved = datetime(2026, 10, 25, 10, tzinfo=timezone.utc)
        session_id = "session-" + invoice_id
        ids = [f"{owner}:piece:{index}" for index in indexes]
        service_rows = deepcopy(services or [])
        for service in service_rows:
            service["total_halalas"] = int(service["unit_price_halalas"] * service["quantity_per_piece"] * len(ids))
        product_total = unit_halalas * len(ids)
        services_total = sum(service["total_halalas"] for service in service_rows)
        await db.mezan_supplier_invoices_v2.insert_one({
            "user_id": owner, "id": invoice_id, "session_id": session_id,
            "invoice_number": "SI-SYNTHETIC-" + invoice_id, "supplier_id": "supplier",
            "status": status, "experiment_mode": experiment,
            "approved_at": approved if finalized else None,
            "currency": "SAR", "subtotal_halalas": product_total + services_total,
            "total_halalas": product_total + services_total,
            "lines": [{"piece_ids": ids, "quantity": len(ids),
                       "product_charge_eligible": True, "product_price_authority": "mezan_v2",
                       "product_unit_price_halalas": unit_halalas,
                       "product_total_halalas": product_total,
                       "services_total_halalas": services_total, "services": service_rows,
                       "total_halalas": product_total + services_total}],
        })
        for index, piece_id in zip(indexes, ids):
            await db.mezan_preparation_pieces_v1.insert_one({
                "user_id": owner, "id": piece_id, "piece_id": piece_id,
                "order_number": "sale", "order_item_id": "line", "unit_index": index,
                "product_id": "p", "supplier_id": "supplier",
                "services": [], "service_plan_status": "completed", "remaining_service_count": 0,
                "supplier_dispatch_status": "received" if finalized else "dispatched",
                **({"received_at": approved} if finalized else {}),
                "product_options_snapshot": {},
            })
            if not finalized:
                continue
            await db.mezan_supplier_receiving_events_v1.insert_one({
                "user_id": owner, "id": "event-" + invoice_id + "-" + str(index),
                "event_type": "supplier_piece_service_recorded", "session_id": session_id,
                "piece_id": piece_id, "order_number": "sale", "order_item_id": "line",
                "unit_index": index, "product_id": "p", "supplier_invoice_id": invoice_id,
                "finalized_at": approved, "financial_invoice_created": True,
                "liability_created": True, "supplier_context": {"id": "supplier"},
            })

    async def ads(*args, **kwargs):
        return {
            "total": 4, "breakdown": {}, "bank_commissions": {"total_fee_sar": 0},
            "spend_quality": {"status": "complete", "amount_available": True, "amount_complete": True},
            "source_contract": {},
            "providers": {provider: {"spend": 4 if provider == "snapchat" else 0,
                         "orders": 0, "revenue": 0, "roas": 0}
                          for provider in ("snapchat", "tiktok", "meta", "google")},
        }

    async def recurring(*args, **kwargs):
        return {"total": 0, "rentals_total": 0, "utilities_total": 0, "renewals_total": 0, "by_type": {}}

    async def legacy(**kwargs):
        assert kwargs["allow_self_heal"] is False
        return {"totals": {"total_orders": 1, "total_sales": 200, "total_product_cost": 0,
                           "total_ads_cost": 0, "operating_expenses_total": 0,
                           "operating_salaries_total": 0, "total_payment_fees": 0,
                           "total_shipping_cost": 0, "net_profit": 200, "net_sales": 200},
                "payment_breakdown": [], "net_sales_config": {}}

    async def no_provider(*args, **kwargs):
        raise AssertionError("report read must not call a provider")

    async def user():
        return {"id": OWNER, "role": "owner"}

    healthy = replace(resource_governor.memory_snapshot(), usage_ratio=0.25)
    monkeypatch.setattr(resource_governor, "memory_snapshot", lambda: healthy)
    monkeypatch.setattr(dashboard, "_today_riyadh", lambda: date.fromisoformat(DAY))
    monkeypatch.setattr(dashboard, "build_mezan_v2_ads", ads)
    monkeypatch.setattr(dashboard, "compute_recurring_obligations_for_range", recurring)
    monkeypatch.setattr(dashboard, "call_salla", no_provider)
    readonly = ReadOnlyDB(db)
    app = FastAPI()
    app.include_router(dashboard.make_dashboard_v2_router(readonly, user, legacy, lambda current: None))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        yield SimpleNamespace(db=db, readonly=readonly, client=client, order=order, receive=receive)


async def _read_surfaces(env):
    responses = {}
    for name, path in (
        ("dashboard", "/dashboard-v2"),
        ("executive", "/dashboard-v2/cards/executive"),
        ("top", "/dashboard-v2/cards/top-products"),
        ("sold", "/dashboard-v2/sold-products"),
    ):
        params = {**PARAMS, "statuses" if name == "sold" else "report_statuses": "default"}
        response = await env.client.get(path, params=params)
        assert response.status_code == 200, response.text
        responses[name] = response.json()
    return responses


def _assert_costs(responses, expected, *, complete=True):
    for name in ("dashboard", "executive", "top"):
        assert responses[name]["product_cost_v2"]["total"] == expected
        assert responses[name]["product_cost_v2"]["missing_all_cost_products_count"] == (0 if complete else 1)
    for name in ("dashboard", "executive"):
        assert responses[name]["totals"]["total_product_cost"] == expected
        assert responses[name]["totals"]["product_cost_complete"] is complete
        assert responses[name]["totals"]["net_profit"] == (200 - expected - 4 if complete else None)
    sold = responses["sold"]
    assert sold["count"] == 1
    assert sold["totals"]["known_cost_total"] == expected
    assert sold["totals"]["total_cost"] == (expected if complete else None)
    assert sold["incomplete_count"] == (0 if complete else 1)
    assert sold["items"][0]["units_sold"] == 2
    assert sum(group["known_total_cost"] for group in sold["items"][0]["cost_groups"]) == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("received,expected", [((), 100), ((1,), 90), ((1, 2), 80)])
async def test_selected_sales_share_current_or_received_cost_in_all_three_surfaces(report_app, received, expected):
    env = report_app
    if received:
        await env.receive(received)
    responses = await _read_surfaces(env)
    _assert_costs(responses, expected)
    from dashboard_v2_routes import build_mezan_v2_product_cost, build_mezan_v2_product_cost_cooperative
    standard = await build_mezan_v2_product_cost(env.readonly, OWNER, [deepcopy(env.order)])
    cooperative = await build_mezan_v2_product_cost_cooperative(env.readonly, OWNER, [deepcopy(env.order)])
    light = await build_mezan_v2_product_cost_cooperative(env.readonly, OWNER, [deepcopy(env.order)], include_product_rows=False)
    assert standard == cooperative
    assert light == {key: value for key, value in cooperative.items() if key not in {"product_rows", "product_profit_summary"}}
    assert standard["total"] == expected
    assert responses["sold"]["items"][0]["cost_source"] == (
        "supplier_invoice_mixed" if received == (1,) else "supplier_invoice" if received else "mezan"
    )
    assert responses["sold"]["items"][0]["uses_salla_fallback"] is False
    if received == (1,):
        groups = responses["sold"]["items"][0]["cost_groups"]
        assert sorted((group["cost_stage"], group["quantity"], group["total_cost"]) for group in groups) == [
            ("actual", 1, 40), ("expected", 1, 50),
        ]


@pytest.mark.asyncio
async def test_partial_receipt_does_not_hide_genuinely_missing_unreceived_option(report_app):
    env = report_app
    await env.receive((1,))
    await env.db.unified_orders.update_one({"user_id": OWNER}, {"$set": {"products.0.options_raw": []}})
    responses = await _read_surfaces(env)
    _assert_costs(responses, 80, complete=False)
    groups = responses["sold"]["items"][0]["cost_groups"]
    assert len(groups) == 2
    assert {(group["cost_stage"], group["complete"]) for group in groups} == {("actual", True), ("expected", False)}
    assert any(reason.startswith("option_selection_unresolved:") for reason in responses["sold"]["items"][0]["missing_cost_reasons"])


@pytest.mark.asyncio
async def test_manual_invoiced_service_has_a_complete_union_with_current_estimate(report_app):
    env = report_app
    await env.receive((1,), services=[{"service_id": "manual", "name": "Manual service",
                                      "quantity_per_piece": 1, "unit_price_halalas": 700}])
    responses = await _read_surfaces(env)
    _assert_costs(responses, 97)
    row = responses["sold"]["items"][0]
    columns = {column["key"] for column in row["cost_columns"]}
    assert any(column["kind"] == "service" for column in row["cost_columns"])
    for group in row["cost_groups"]:
        cells = {cell["key"]: cell for cell in group["cells"]}
        assert set(cells) == columns
        assert all(cell["status"] in {"costed", "included_in_base", "not_required"} for cell in cells.values())
        priced = sum(Decimal(str(cell["unit_cost"])) for cell in cells.values() if cell["status"] == "costed")
        assert priced * Decimal(str(group["quantity"])) == Decimal(str(group["total_cost"]))


@pytest.mark.asyncio
async def test_current_cost_revision_refreshes_estimates_without_repricing_received_units(report_app):
    from product_cost_revision import bump_product_cost_revision
    from product_v2_details_routes import COST_PROFILES
    env = report_app
    await env.receive((1,))
    _assert_costs(await _read_surfaces(env), 90)
    await env.db[COST_PROFILES].update_one({"user_id": OWNER}, {"$set": {"base_cost": 60}})
    await bump_product_cost_revision(env.db, OWNER)
    _assert_costs(await _read_surfaces(env), 110)


@pytest.mark.asyncio
async def test_partial_receipt_retains_salla_fallback_alongside_invoice_provenance(report_app):
    from product_v2_details_routes import COST_PROFILES
    from product_v2_routes import PRODUCTS
    env = report_app
    await env.db[COST_PROFILES].delete_many({"user_id": OWNER})
    await env.db[PRODUCTS].update_one({"user_id": OWNER}, {"$set": {"cost_price_from_salla": 40}})
    await env.receive((1,))
    responses = await _read_surfaces(env)
    _assert_costs(responses, 90)
    row = responses["sold"]["items"][0]
    assert row["cost_source"] == "supplier_invoice_mixed"
    assert row["uses_salla_fallback"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("changes", [
    {"owner": "foreign-owner"}, {"status": "draft", "finalized": False},
    {"experiment": True, "finalized": False},
])
async def test_unaccepted_or_foreign_invoice_does_not_replace_the_sale_estimate(report_app, changes):
    env = report_app
    await env.receive((1, 2), **changes)
    _assert_costs(await _read_surfaces(env), 100)


@pytest.mark.asyncio
@pytest.mark.parametrize("changes", [{"status": "draft"}, {"experiment": True}])
async def test_finalized_real_receipt_without_accepted_invoice_stays_incomplete(report_app, changes):
    env = report_app
    await env.receive((1, 2), **changes)
    responses = await _read_surfaces(env)
    _assert_costs(responses, 100, complete=False)
    assert any(reason.startswith("supplier_") for reason in responses["sold"]["items"][0]["missing_cost_reasons"])


@pytest.mark.asyncio
@pytest.mark.parametrize("received,expected,complete", [((1, 2), 80, True), ((1,), 90, False)])
async def test_new_catalog_service_does_not_reopen_completed_receipt_scope(report_app, received, expected, complete):
    from product_option_cost_routes import BINDINGS, RESOURCES
    from product_v2_routes import PRODUCTS
    env = report_app
    await env.receive(received)
    await env.db[PRODUCTS].update_one({"user_id": OWNER}, {"$push": {"options": {
        "id": "later", "type": "radio", "name": "Later customization",
        "values": [{"id": "yes", "name": "Yes"}, {"id": "no", "name": "No"}],
    }}})
    await env.db[BINDINGS].insert_one({
        "user_id": OWNER, "id": "later-service", "salla_product_id": "p",
        "option_id": "later", "value_id": "yes", "mode": "resource",
        "resource_id": "later-service", "quantity": 1,
    })
    await env.db[RESOURCES].insert_one({
        "user_id": OWNER, "id": "later-service", "name": "Later service",
        "kind": "service", "unit_cost": 12,
    })
    responses = await _read_surfaces(env)
    _assert_costs(responses, expected, complete=complete)
    groups = responses["sold"]["items"][0]["cost_groups"]
    actual = next(group for group in groups if group["cost_stage"] == "actual")
    assert actual["complete"] is True
    assert next(cell for cell in actual["cells"] if cell["key"] == "option:later-service")["status"] == "not_required"
    if not complete:
        estimate = next(group for group in groups if group["cost_stage"] == "expected")
        assert next(cell for cell in estimate["cells"] if cell["key"] == "option:later-service")["status"] == "unresolved"


@pytest.mark.asyncio
async def test_supplier_product_and_service_cost_preserve_internal_material_breakdown(report_app):
    from product_fulfillment_rules import PRODUCT_RESOURCE_BINDINGS
    from product_option_cost_routes import RESOURCES
    env = report_app
    await env.db[PRODUCT_RESOURCE_BINDINGS].insert_one({
        "user_id": OWNER, "id": "bag-link", "salla_product_id": "p",
        "resource_id": "bag", "quantity": 1,
    })
    await env.db[RESOURCES].insert_one({
        "user_id": OWNER, "id": "bag", "name": "Bag",
        "kind": "component", "unit_cost": 5.5,
    })
    await env.receive((1,), services=[{"service_id": "manual", "service_name": "Manual service",
                                      "quantity_per_piece": 1, "unit_price_halalas": 700}])
    responses = await _read_surfaces(env)
    _assert_costs(responses, 108)
    row = responses["sold"]["items"][0]
    assert row["cost_source"] == "supplier_invoice_mixed"
    assert sorted((group["cost_stage"], group["total_cost"]) for group in row["cost_groups"]) == [
        ("expected", 55.5), ("mixed", 52.5),
    ]
    amounts = {"product": Decimal(0), "component": Decimal(0), "service": Decimal(0)}
    columns = {column["key"] for column in row["cost_columns"]}
    for group in row["cost_groups"]:
        assert group["complete"] is True
        assert {cell["key"] for cell in group["cells"]} == columns
        for cell in group["cells"]:
            assert cell["status"] in {"costed", "included_in_base", "not_required"}
            if cell["status"] == "costed":
                amounts[cell["kind"]] += Decimal(str(cell["unit_cost"])) * Decimal(str(group["quantity"]))
    assert amounts == {"product": Decimal(80), "component": Decimal(11), "service": Decimal(17)}
    assert sum(amounts.values()) == Decimal(str(row["total_cost"]))


@pytest.mark.asyncio
@pytest.mark.parametrize("fallback,received_first", [(False, True), (True, True), (True, False)])
async def test_separate_received_and_unreceived_sales_keep_mixed_product_source(report_app, fallback, received_first):
    from product_v2_details_routes import COST_PROFILES
    from product_v2_routes import PRODUCTS
    env = report_app
    if fallback:
        await env.db[COST_PROFILES].delete_many({"user_id": OWNER})
        await env.db[PRODUCTS].update_one({"user_id": OWNER}, {"$set": {"cost_price_from_salla": 40}})
    first = deepcopy(env.order)
    first["total_amount"] = 100
    first["products"][0].update(quantity=1, total=100)
    second = {**deepcopy(first), "order_number": "unreceived-sale"}
    await env.db.unified_orders.delete_many({"user_id": OWNER})
    await env.db.unified_orders.insert_many([first, second] if received_first else [second, first])
    await env.receive((1,))
    responses = await _read_surfaces(env)
    _assert_costs(responses, 90)
    row = responses["sold"]["items"][0]
    assert row["cost_source"] == "supplier_invoice_mixed"
    assert row["uses_salla_fallback"] is fallback
    assert {(group["cost_stage"], group["quantity"], group["total_cost"]) for group in row["cost_groups"]} == {
        ("actual", 1, 40), ("expected", 1, 50),
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("received,expected", [((), 96), ((1,), 90), ((1, 2), 84)])
async def test_original_service_scope_uses_current_price_until_supplier_receipt(report_app, received, expected):
    """A new choice cannot erase an already proven original service request."""
    from order_cost_authority import capture_reporting_costs, LIFECYCLES
    from product_option_cost_routes import BINDINGS, RESOURCES
    from product_v2_details_routes import COST_PROFILES
    from product_v2_routes import PRODUCTS

    env = report_app
    await env.db[COST_PROFILES].update_one({"user_id": OWNER}, {"$set": {"base_cost": 20}})
    await env.db[BINDINGS].update_one({"user_id": OWNER}, {"$set": {
        "mode": "resource", "resource_id": "embroidery", "quantity": 1,
    }})
    await env.db[RESOURCES].insert_one({
        "user_id": OWNER, "id": "embroidery", "name": "Original embroidery service",
        "kind": "service", "unit_cost": 6,
    })
    frozen = await capture_reporting_costs(env.db, OWNER, env.order["products"], None)
    assert frozen["line"]["cost"]["line_total"] == 52
    await env.db[LIFECYCLES].insert_one({
        "user_id": OWNER, "order_number": "sale", "reporting_cost_snapshots": frozen,
    })

    # The original line and its answers stay unchanged. Only the current
    # catalog condition and prices change after the service was required.
    await env.db[COST_PROFILES].update_one({"user_id": OWNER}, {"$set": {"base_cost": 40}})
    await env.db[RESOURCES].update_one({"user_id": OWNER, "id": "embroidery"}, {"$set": {"unit_cost": 8}})
    await env.db[PRODUCTS].update_one({"user_id": OWNER}, {"$push": {"options": {
        "id": "later", "type": "radio", "name": "New embroidery question",
        "values": [{"id": "yes", "name": "Yes"}, {"id": "no", "name": "No"}],
    }}})
    await env.db[BINDINGS].update_one({"user_id": OWNER}, {"$set": {
        "id": "later-embroidery", "option_id": "later", "value_id": "yes",
    }})
    if received:
        await env.receive(received, unit_halalas=3500, services=[{
            "service_id": "embroidery", "service_name": "Original embroidery service",
            "quantity_per_piece": 1, "unit_price_halalas": 700,
        }])

    responses = await _read_surfaces(env)
    _assert_costs(responses, expected)
    groups = responses["sold"]["items"][0]["cost_groups"]
    assert {group["cost_stage"] for group in groups} == (
        {"actual", "expected"} if len(received) == 1 else {"actual"} if received else {"expected"}
    )
    for group in groups:
        service = next(cell for cell in group["cells"] if cell.get("resource_id") == "embroidery")
        assert service["status"] == "costed"
        assert service["unit_cost"] == (7 if group["cost_stage"] == "actual" else 8)
    stored = await env.db[LIFECYCLES].find_one({"user_id": OWNER, "order_number": "sale"})
    assert stored["reporting_cost_snapshots"] == frozen
    saved_order = await env.db.unified_orders.find_one({"user_id": OWNER, "order_number": "sale"})
    assert saved_order["products"] == env.order["products"]

@pytest.mark.asyncio
@pytest.mark.parametrize("fallback", [False, True])
async def test_invoiced_price_and_unbilled_recorded_choices_in_all_reports(report_app, fallback):
    from product_v2_details_routes import COST_PROFILES
    from product_v2_routes import PRODUCTS

    env = report_app
    if fallback:
        await env.db[COST_PROFILES].delete_many({"user_id": OWNER})
        await env.db[PRODUCTS].update_one({"user_id": OWNER}, {"$set": {"cost_price_from_salla": 39}})
    options = [{"option_id": "size", "name": "Size", "value": "M"}]
    await env.db.unified_orders.update_one({"user_id": OWNER}, {"$set": {
        "products.0.order_item_id": "salla:sale:line", "products.0.options_raw": options,
        "raw_by_source.salla_direct": {"reference_id": "sale", "items": [
            {"id": "line", "product_id": "p", "quantity": 2, "options": options}]},
    }})
    await env.receive((1,), unit_halalas=5100)
    expected_base = 39 if fallback else 40
    responses = await _read_surfaces(env)
    _assert_costs(responses, 51 + expected_base)
    groups = responses["sold"]["items"][0]["cost_groups"]
    assert {(row["cost_stage"], row["total_cost"]) for row in groups} == {
        ("actual", 51), ("expected", expected_base)}
    stored = await env.db.unified_orders.find_one({"user_id": OWNER})
    assert stored["products"][0]["options_raw"] == options
    assert "_report_salla_question_keys" not in stored["products"][0]


@pytest.mark.asyncio
async def test_received_piece_before_invoice_uses_catalog(report_app):
    env = report_app
    await env.receive((1,), finalized=False, status="draft", unit_halalas=7700)
    await env.db.mezan_preparation_pieces_v1.update_many(
        {"user_id": OWNER}, {"$set": {"supplier_dispatch_status": "received"}})
    await env.db.mezan_supplier_receiving_events_v1.insert_one({
        "user_id": OWNER, "event_type": "supplier_piece_service_recorded",
        "order_number": "sale", "order_item_id": "line", "unit_index": 1,
        "piece_id": OWNER + ":piece:1", "financial_invoice_created": False,
    })
    responses = await _read_surfaces(env)
    _assert_costs(responses, 100)
    assert responses["sold"]["items"][0]["cost_source"] == "mezan"


@pytest.mark.asyncio
async def test_cached_cost_cards_detect_new_invoice_without_receipt_side_writes(report_app):
    env = report_app
    _assert_costs(await _read_surfaces(env), 100)
    await env.receive((1,), unit_halalas=4700)
    _assert_costs(await _read_surfaces(env), 97)
