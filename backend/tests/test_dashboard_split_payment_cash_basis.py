"""The canonical dashboard separates store-wallet sales from gateway cash."""
from copy import deepcopy

import pytest
from mongomock_motor import AsyncMongoMockClient

from legacy_dashboard_reader import dashboard
from tests.test_dashboard_bundle_cost_authority import ReadOnlyDB
from tests.test_dashboard_split_payment_fees import DAY, OWNER, fee_context, split_order


async def read_dashboard(orders, **settings):
    db = AsyncMongoMockClient().split_payment_cash_basis
    await db.settings.insert_one({"user_id": OWNER, "payment_methods": [],
        "shipping_companies": [{"name": "carrier", "cost_per_order": 0}],
        "net_sales_config": {}, **settings})
    await db.unified_orders.insert_many(deepcopy(orders))
    return await dashboard(database=ReadOnlyDB(db), owner_id=OWNER,
        from_date=DAY, to_date=DAY, include_legacy_analyses=False,
        allow_self_heal=False, persist_settings=False, fee_context=fee_context())


@pytest.mark.asyncio
async def test_wallet_sale_remains_in_profit_but_not_expected_gateway_cash():
    result = await read_dashboard([split_order(), split_order("mada")])
    totals = result["totals"]
    assert result["payment_fee_authority"]["complete"] is True
    assert totals["total_sales"] == 706.99
    assert totals["total_payment_fees"] == 13.21
    assert totals["net_profit"] == 673.78  # Full sales less fees and product cost20.
    assert totals["internal_wallet_paid_sar"] == 33.01
    assert totals["expected_salla_transfer"] == 660.77
    assert totals["electronic_net_before_settlements"] == totals["electronic_net"] == 660.77
    detail = totals["electronic_net_breakdown"]
    assert detail["gross_before_filter"] == detail["gross_after_filter"] == 673.98
    assert detail["fees_before_filter"] == detail["fees_after_filter"] == 13.21


@pytest.mark.asyncio
async def test_electronic_status_filter_keeps_separate_wallet_and_fee_cohorts():
    result = await read_dashboard([split_order(), split_order("mada")],
        electronic_net_excluded_statuses=["completed"])
    totals = result["totals"]
    assert totals["total_sales"] == 706.99 and totals["net_profit"] == 673.78
    assert totals["expected_salla_transfer"] == 660.77
    assert totals["electronic_net"] == 0
    detail = totals["electronic_net_breakdown"]
    assert (detail["included_count"], detail["excluded_count"]) == (0, 2)
    assert detail["gross_before_filter"] == 673.98 and detail["fees_before_filter"] == 13.21
    assert detail["gross_after_filter"] == detail["fees_after_filter"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("excluded", [False, True])
async def test_actual_fees_do_not_turn_unknown_wallet_cash_into_full_order_principal(excluded):
    current = split_order()
    current.update(payment_fee_status="actual", actual_payment_fee=4, actual_payment_vat=.6)
    current["raw_by_source"]["salla_direct"]["payment_methods"][1]["amount"] = "unverified"
    result = await read_dashboard([current],
        electronic_net_excluded_statuses=["completed"] if excluded else [])
    totals = result["totals"]
    assert result["payment_fee_authority"]["complete"] is True
    assert totals["total_sales"] == 178.87 and totals["total_payment_fees"] == 4.6
    assert totals["net_profit"] == 164.27
    assert totals["internal_wallet_paid_sar"] is None
    assert totals["expected_salla_transfer"] is None
    assert totals["electronic_net"] == totals["electronic_net_before_settlements"] == (0 if excluded else None)
    detail = totals["electronic_net_breakdown"]
    assert detail["gross_before_filter"] is None
    assert detail["gross_after_filter"] == (0 if excluded else None)
    assert detail["fees_before_filter"] == 4.6
    assert detail["fees_after_filter"] == (0 if excluded else 4.6)
