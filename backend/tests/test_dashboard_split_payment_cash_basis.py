"""The canonical dashboard separates store-wallet sales from gateway cash."""
from copy import deepcopy

import pytest
from mongomock_motor import AsyncMongoMockClient

from legacy_dashboard_reader import dashboard
from tests.test_dashboard_bundle_cost_authority import ReadOnlyDB
from tests.test_dashboard_split_payment_fees import (
    DAY, OWNER, fee_context, split_order, synthetic_bnpl_wallet, synthetic_bnpl_wallet_policy,
)


async def read_dashboard(orders, fee_context_override=None, **settings):
    db = AsyncMongoMockClient().split_payment_cash_basis
    await db.settings.insert_one({"user_id": OWNER, "payment_methods": [],
        "shipping_companies": [{"name": "carrier", "cost_per_order": 0}],
        "net_sales_config": {}, **settings})
    await db.unified_orders.insert_many(deepcopy(orders))
    return await dashboard(database=ReadOnlyDB(db), owner_id=OWNER,
        from_date=DAY, to_date=DAY, include_legacy_analyses=False,
        allow_self_heal=False, persist_settings=False,
        fee_context=fee_context_override or fee_context())


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


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["tabby", "tamara", "emkan"])
async def test_bnpl_wallet_full_sale_keeps_profit_but_only_external_share_is_provider_receivable(provider):
    source = synthetic_bnpl_wallet(provider)
    policy = synthetic_bnpl_wallet_policy(provider)
    result = await read_dashboard([source], fee_context_override=fee_context(policies=[policy]))
    totals = result["totals"]
    assert result["payment_fee_authority"]["complete"] is True
    assert result["payment_fee_authority"]["bnpl_cash_basis_complete"] is True
    assert totals["total_sales"] == 178.87
    assert totals["total_payment_fees"] == 2.3
    assert totals["internal_wallet_paid_sar"] == 33.01
    assert totals["net_profit"] == 166.57  # Full sale minus product cost and provider fee.
    assert totals["bnpl_net"] == 143.56  # Only external BNPL share less provider fee.
    assert totals["expected_salla_transfer"] == 143.56


@pytest.mark.asyncio
async def test_unknown_bnpl_wallet_cash_basis_never_becomes_full_provider_receivable():
    source = synthetic_bnpl_wallet("tabby")
    source.update(payment_fee_status="actual", actual_payment_fee=2, actual_payment_vat=.30)
    source["raw_by_source"]["salla_direct"]["payment_methods"][1]["amount"] = "unverified"
    result = await read_dashboard([source], fee_context_override=fee_context(policies=[]))
    assert result["payment_fee_authority"]["complete"] is True
    assert result["payment_fee_authority"]["bnpl_cash_basis_complete"] is False
    assert result["totals"]["bnpl_net"] is None
    assert result["totals"]["internal_wallet_paid_sar"] is None
    assert result["totals"]["net_profit"] == 166.57


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["tabby", "tamara", "emkan"])
async def test_incomplete_bnpl_tariff_does_not_claim_an_external_receivable(provider):
    """A verified wallet split proves principal, not an unknown commission."""
    source = synthetic_bnpl_wallet(provider)
    known_policy = synthetic_bnpl_wallet_policy(provider)
    context = fee_context(policies=[{**known_policy, "plan_id": "requires-plan-proof"}])
    result = await read_dashboard([source], fee_context_override=context)
    authority, totals = result["payment_fee_authority"], result["totals"]
    assert authority["complete"] is False
    assert authority["bnpl_cash_basis_complete"] is True
    assert authority["bnpl_fees_complete"] is False
    assert totals["total_sales"] == 178.87
    assert totals["internal_wallet_paid_sar"] == 33.01
    assert totals["bnpl_net"] is None
    assert totals["expected_salla_transfer"] is None
    assert totals["net_profit"] is None


@pytest.mark.asyncio
async def test_incomplete_card_fee_never_shows_known_partial_as_final_cash_or_profit():
    source = split_order("wallet")
    source["raw_by_source"]["salla_direct"]["payment_methods"][1]["amount"] = "31.00"
    result = await read_dashboard([source])
    authority, totals = result["payment_fee_authority"], result["totals"]
    assert authority["complete"] is False
    assert authority["electronic_complete"] is False
    assert authority["review_order_count"] == 1
    assert totals["electronic_net"] is None
    assert totals["electronic_net_before_settlements"] is None
    assert totals["expected_salla_transfer"] is None
    assert totals["net_profit"] is None



@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["tabby", "tamara"])
async def test_actual_statement_bnpl_wallet_fee_deducts_only_external_share_once(provider):
    from tests.test_dashboard_split_payment_fees import accepted_wallet_provider_fee

    source = synthetic_bnpl_wallet(provider)
    accepted = accepted_wallet_provider_fee(source, provider)
    # Deliberately differ from the saved tariff (2.30 SAR) so the public
    # rollup proves actual-statement precedence, not just equal arithmetic.
    accepted.update(fee_net="3.00", fee_vat="0.45", fee_total="3.45")
    context = fee_context(policies=[synthetic_bnpl_wallet_policy(provider)],
        operational_statement_fees={source["order_number"]: accepted})
    original = deepcopy((source, context))
    result = await read_dashboard([source], fee_context_override=context)
    totals = result["totals"]
    assert result["payment_fee_authority"]["complete"] is True
    assert totals["total_sales"] == 178.87
    assert totals["internal_wallet_paid_sar"] == 33.01
    assert totals["total_payment_fees"] == 3.45
    assert totals["net_profit"] == 165.42
    assert totals["bnpl_net"] == 142.41
    assert totals["expected_salla_transfer"] == 142.41
    # The canonical public rollup does not expose raw fee_sources. The
    # lower-level fee authority separately asserts the exact source label.
    assert result["payment_breakdown"][0]["fee_amount"] == 3.45
    assert result["payment_breakdown"][0]["vat_amount"] == 0.45
    assert not result["payment_breakdown"][0].get("fees_estimated")
    assert (source, context) == original
