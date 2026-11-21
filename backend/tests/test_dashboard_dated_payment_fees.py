"""Dashboard estimates use the saved historical fee authority, never today's alias badge."""
from copy import deepcopy
from datetime import date
from pathlib import Path
import ast

import pytest

from dashboard_ads_costs import DashboardAdsCostCollector
from dashboard_payment_fees import (
    dashboard_fee_breakdown, enforce_dashboard_fee_completeness, attach_fee_source_evidence,
)
from dashboard_v2_ad_costs import merge_ad_bank_fees_into_dashboard
from excel_parser import match_settings
from orders_db import orders_to_parsed


def policy(**changes):
    return {"id": "emkan-terms", "provider": "emkan", "currency": "SAR", "status": "active",
            "effective_from": "2026-11-01", "fee_model_version": 2,
            "confirmed_by": "owner", "confirmed_at": "2026-11-01T00:00:00Z", "evidence": "approved terms",
            "components": [{"id": "fee", "percentage": "6.99", "fixed_amount": "1.50",
                            "vat_treatment": "exclusive", "vat_rate": "15", "vat_basis": "unrounded_fee",
                            "refund_fee_treatment": "retain"}], **changes}


def context(**changes):
    return {"policies": [policy()], "source_mode": "mz2_policies", "complete": True,
            "statement_plans": {}, "as_of": "2026-11-21", **changes}


def order(**changes):
    return {"order_number": "order-a", "payment_method": "EmkanInstallment", "total_amount": 100,
            "currency": "SAR", "order_date": "2026-11-15", "order_status": "completed",
            "shipping_company": "carrier", "products": [{"quantity": 1}], **changes}


def legacy(rate=50):
    return [{"name": "إمكان", "commission_percent": rate, "fixed_fee": 8, "vat_percent": 15}]


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["إمكان", "امكان", "Emkan", "EmkanInstallment", "emkan_installment", "imkan"])
async def test_emkan_alias_uses_dated_fee_even_without_legacy_row(name):
    source = order(payment_method=name)
    before = deepcopy(source)
    result = await dashboard_fee_breakdown([source], [], context())
    assert result["payment_fees_complete"] is True
    assert result["total_payment_fees"] == 9.76
    assert result["payment_breakdown"][0]["fee_sources"] == ["dated_provider_policy"]
    assert source == before


@pytest.mark.asyncio
async def test_dated_policy_wins_conflicting_legacy_and_collector_matches_executive_adapter():
    orders = [order(), order(order_number="order-b", total_amount=200)]
    expected = await dashboard_fee_breakdown(orders, legacy(), context())
    collector = DashboardAdsCostCollector()
    collector.capture_settings({"payment_methods": legacy(), "shipping_companies": [{"name": "carrier", "cost_per_order": 0}]})
    collector.capture_fee_context(context())
    for source in orders:
        collector.begin_order(source)
        collector.observe_line(source, source["products"][0], {"base_complete": True, "quantity": 1})
        collector.finish_order(source, 10)
    actual = await collector.summary(orders, scope_complete=True)
    assert actual["payment_fees_sar"] == expected["total_payment_fees"] == 27.56
    assert actual["payment_fees_complete"] is True
    assert actual["total_cost_sar"] == 47.56


@pytest.mark.asyncio
@pytest.mark.parametrize("terms,source,reason", [
    (policy(effective_from="2026-11-16"), order(), "provider_fee_policy_incomplete_or_ambiguous"),
    (policy(effective_to="2026-11-14"), order(), "provider_fee_policy_incomplete_or_ambiguous"),
    (policy(plan_id="six-month"), order(), "provider_payment_plan_required"),
    (policy(), order(order_date=None), "provider_fee_transaction_date_required"),
    (policy(), order(order_date_inferred=True), "provider_fee_transaction_date_required"),
    (policy(), order(order_date="2026-11-22"), "provider_fee_transaction_date_invalid"),
])
async def test_uncovered_expired_plan_or_date_never_borrows_legacy(terms, source, reason):
    result = await dashboard_fee_breakdown([source], legacy(), context(policies=[terms]))
    assert result["payment_fees_complete"] is False
    assert result["total_payment_fees"] == 0  # Known subset only, not a proved free fee.
    assert reason in result["fee_review_reasons"]
    collector = DashboardAdsCostCollector()
    collector.capture_settings({"payment_methods": legacy()})
    collector.capture_fee_context(context(policies=[terms]))
    summary = await collector.summary([source], scope_complete=True)
    assert summary["payment_fees_sar"] is None


@pytest.mark.asyncio
async def test_actual_statement_fee_and_rebate_supersede_policy_once():
    sources = [order(payment_fee_status="actual", actual_payment_fee=1, actual_payment_vat=.15),
               order(order_number="refund", payment_fee_status="actual", actual_payment_fee=-.5, actual_payment_vat=-.08)]
    result = await dashboard_fee_breakdown(sources, legacy(), context(complete=False))
    assert result["payment_fees_complete"] is True
    assert result["total_payment_fees"] == .57
    assert result["payment_breakdown"][0]["fee_sources"] == ["actual_statement"]
    unknown = await dashboard_fee_breakdown([order(payment_fee_status="actual", actual_payment_fee=0)], legacy(), context())
    assert unknown["payment_fees_complete"] is False


@pytest.mark.asyncio
async def test_raw_statement_plan_is_bound_and_conflict_remains_unknown():
    ctx = context(policies=[policy(plan_id="six-month")], statement_plans={
        ("emkan", "order-a"): {"plan_id": "six-month", "plan_evidence": {"source": "settlement_entries"}}})
    assert (await dashboard_fee_breakdown([order()], [], ctx))["total_payment_fees"] == 9.76
    conflict = order(_dashboard_fee_source={"payment": {"plan_id": "another-product"}})
    result = await dashboard_fee_breakdown([conflict], legacy(), ctx)
    assert result["payment_fees_complete"] is False
    assert "provider_payment_plan_conflict" in result["fee_review_reasons"]


@pytest.mark.asyncio
async def test_executed_refund_uses_existing_terms_but_missing_or_foreign_refund_proof_is_unknown():
    refund = {"id": "refund-a", "amount": "50", "status": "executed", "refunded_at": "2026-11-16T12:00:00Z"}
    source = order(actual_refund_amount=50, _dashboard_fee_source={"currency": "SAR", "payment": {"refunds": [refund]}})
    result = await dashboard_fee_breakdown([source], [], context())
    assert result["payment_fees_complete"] is True
    assert result["total_payment_fees"] == 9.76  # Retained fee is not charged again.
    source["_dashboard_fee_source"]["payment"]["refunds"] = []
    assert (await dashboard_fee_breakdown([source], legacy(), context()))["payment_fees_complete"] is False
    source["_dashboard_fee_source"] = {"currency": "QAR", "payment": {"refunds": [refund]}}
    assert (await dashboard_fee_breakdown([source], legacy(), context()))["payment_fees_complete"] is False


@pytest.mark.asyncio
async def test_currency_and_split_proof_do_not_become_sar_or_single_provider():
    unknown = await dashboard_fee_breakdown([order(currency="QAR")], legacy(), context())
    assert not unknown["payment_fees_complete"]
    split = order(_dashboard_fee_source={"payment_methods": [{"payment_method": "emkan"}, {"payment_method": "cod"}]})
    assert not (await dashboard_fee_breakdown([split], legacy(), context()))["payment_fees_complete"]


@pytest.mark.asyncio
async def test_capture_proof_prevents_pricing_changed_gross_and_can_supply_missing_order_date():
    capture = {"id": "capture-a", "amount": "100", "currency": "SAR", "captured_at": "2026-11-15T12:00:00Z"}
    source = order(order_date=None, order_date_inferred=True,
                   _dashboard_fee_source={"payment": {"method": "emkan", "captures": [capture]}})
    result = await dashboard_fee_breakdown([source], [], context())
    assert result["payment_fees_complete"] is True and result["total_payment_fees"] == 9.76
    source["total_amount"] = 50
    result = await dashboard_fee_breakdown([source], legacy(), context())
    assert result["fee_review_reasons"] == ["provider_capture_original_basis_required"]
    conflict = order(_dashboard_fee_source={"payment": {"method": "paypal"}})
    assert (await dashboard_fee_breakdown([conflict], [], context()))["fee_review_reasons"] == ["provider_payment_method_conflict"]


@pytest.mark.asyncio
@pytest.mark.parametrize("raw,extra,reason", [
    ({"payment_methods": [None]}, {}, "provider_payment_shape_invalid"),
    ({"payment_methods": [{"payment_method": "emkan", "amount": 50, "transaction_reference": "a"},
                          {"payment_method": "emkan", "amount": 50, "transaction_reference": "b"}]},
     {}, "provider_split_payment_review_required"),
    ({"payment_methods": [{"payment_method": "emkan", "amount": 40, "transaction_reference": "a"}]},
     {}, "provider_payment_amount_conflict"),
    ({"payment_methods": [{"payment_method": "emkan", "amount": 100, "transaction_reference": "a", "currency": "QAR"}]},
     {}, "provider_payment_currency_conflict"),
    ({"payment": {"plan_id": "six"}, "payment_methods": [
        {"payment_method": "emkan", "amount": 100, "transaction_reference": "a", "plan_id": "four"}]},
     {}, "provider_payment_plan_conflict"),
    ({"payment": {"cancelled": "100", "cancellations": [{"id": "c1", "amount": "100",
        "status": "executed", "stage": "before_capture", "currency": "SAR"}]}},
     {"actual_canceled_amount": 100}, "provider_cancellation_execution_evidence_required"),
    ({}, {"actual_canceled_amount": 100}, "provider_cancellation_execution_evidence_required"),
    ({"created_at": "2026-11-15T12:00:00Z", "payment": {"captures": [{"id": "c1", "amount": 100,
        "currency": "SAR", "captured_at": "2026-11-01T12:00:00Z"}]}}, {}, "provider_capture_timestamp_conflict"),
    ({"created_at": "2026-11-15T12:00:00Z", "payment": {"captures": [{"id": "c1", "amount": 100,
        "currency": "SAR", "captured_at": "2026-11-15T11:00:00Z"}]}}, {}, "provider_capture_timestamp_conflict"),
])
async def test_conflicting_native_tenders_cancellations_and_capture_dates_remain_incomplete(raw, extra, reason):
    result = await dashboard_fee_breakdown([order(_dashboard_fee_source=raw, **extra)], legacy(), context())
    assert result["payment_fees_complete"] is False
    assert result["total_payment_fees"] == 0
    assert result["fee_review_reasons"] == [reason]


@pytest.mark.asyncio
@pytest.mark.parametrize("version", [1, 2])
async def test_individual_refund_currency_never_assumes_order_currency(version):
    source = order(actual_refund_amount=50, _dashboard_fee_source={"currency": "SAR", "payment": {"refunds": [
        {"id": "r1", "amount": 50, "currency": "QAR", "status": "executed", "refunded_at": "2026-11-16T12:00:00Z"}]}})
    result = await dashboard_fee_breakdown([source], legacy(), context(policies=[policy(fee_model_version=version)]))
    assert result["payment_fees_complete"] is False
    assert result["fee_review_reasons"] == ["provider_refund_currency_conversion_required"]


@pytest.mark.asyncio
async def test_native_card_rail_ambiguity_is_not_resolved_by_normalized_method():
    terms = [policy(id=rail, provider="salla", payment_method=rail) for rail in ("mada", "visa")]
    source = order(payment_method="mada", _dashboard_fee_source={"payment_methods": [
        {"payment_method": "mada", "amount": 40, "transaction_reference": "a"},
        {"payment_method": "visa", "amount": 60, "transaction_reference": "b"}]})
    result = await dashboard_fee_breakdown([source], [], context(policies=terms))
    assert result["payment_fees_complete"] is False
    assert result["fee_review_reasons"] == ["provider_payment_method_required_for_fee"]
    # A wallet wrapper and its card network for the same payment remain one fee.
    source["_dashboard_fee_source"]["payment_methods"] = [
        {"payment_method": method, "amount": 100, "transaction_reference": "one"} for method in ("apple_pay", "mada")]
    result = await dashboard_fee_breakdown([source], [], context(policies=terms))
    assert result["payment_fees_complete"] is True
    assert result["total_payment_fees"] == 9.76


@pytest.mark.asyncio
async def test_actual_statement_fee_still_wins_cancelled_or_ambiguous_native_evidence():
    source = order(payment_fee_status="actual", actual_payment_fee=-1, actual_payment_vat=-.15,
        actual_canceled_amount=100, _dashboard_fee_source={"payment_methods": [None], "payment": {"cancelled": 100}})
    result = await dashboard_fee_breakdown([source], legacy(), context())
    assert result["payment_fees_complete"] is True
    assert result["total_payment_fees"] == -1.15


@pytest.mark.asyncio
async def test_native_capture_uses_existing_normalizer_and_capture_day_tariff():
    old, new = policy(id="old", effective_to="2026-11-14"), policy(id="new", effective_from="2026-11-15")
    old["components"][0]["percentage"] = "1"
    new["components"][0]["percentage"] = "10"
    source = order(order_date="2026-11-10", _dashboard_fee_source={"created_at": "2026-11-10T12:00:00Z", "payment": {
        "method": "emkan", "status": "paid", "paid_amount": 100, "paid_at": "2026-11-16T12:00:00Z", "reference": "capture1"}})
    result = await dashboard_fee_breakdown([source], legacy(), context(policies=[old, new]))
    assert result["payment_fees_complete"] is True
    assert result["total_payment_fees"] == 13.23  # Creation-day terms would incorrectly charge 2.88.
    source["_dashboard_fee_source"]["payment"]["paid_amount"] = 40
    result = await dashboard_fee_breakdown([source], legacy(), context(policies=[old, new]))
    assert result["payment_fees_complete"] is False
    assert result["fee_review_reasons"] == ["provider_capture_original_basis_required"]
    source["_dashboard_fee_source"]["payment"].pop("reference")
    assert not (await dashboard_fee_breakdown([source], legacy(), context()))["payment_fees_complete"]


@pytest.mark.asyncio
@pytest.mark.parametrize("stamp", [None, "2026-11-25T12:00:00Z", "2026-11-01T12:00:00Z"])
async def test_executed_refund_without_valid_period_evidence_does_not_reduce_fee(stamp):
    source = order(actual_refund_amount=100, _dashboard_fee_source={"currency": "SAR", "payment": {"refunds": [
        {"id": "r1", "amount": 100, "currency": "SAR", "status": "executed", "refunded_at": stamp}]}})
    result = await dashboard_fee_breakdown([source], legacy(), context())
    assert result["payment_fees_complete"] is False
    assert result["fee_review_reasons"] == ["provider_refund_timestamp_required" if stamp is None else "provider_refund_timestamp_conflict"]


@pytest.mark.asyncio
@pytest.mark.parametrize("raw", [
    {"payment": {"refunded_amount": 50}}, {"refunded_amount": 50},
    {"payment_actions": {"refund_action": {"refund_amount": 50}}},
    {"payment_actions": {"refund_action": {"refunded_transactions": [{"id": "native-r1", "amount": 50}]}}},
])
async def test_native_refund_observation_is_projected_and_requires_execution_evidence(raw):
    source = order()
    attach_fee_source_evidence([source], [{"order_number": "order-a", "raw_by_source": {"salla_direct": raw}}])
    result = await dashboard_fee_breakdown([source], legacy(), context())
    assert result["payment_fees_complete"] is False
    assert result["fee_review_reasons"] == ["provider_refund_execution_evidence_required"]


@pytest.mark.asyncio
async def test_no_policy_retains_aggregate_rounding_and_cod_or_bank_settings():
    sources = [order(payment_method="tamara", total_amount=1), order(payment_method="tamara", total_amount=1)]
    terms = [{"name": "tamara", "commission_percent": .5, "fixed_fee": 0, "vat_percent": 0}]
    result = await dashboard_fee_breakdown(sources, terms, context(policies=[], source_mode="legacy_settings"))
    assert result["total_payment_fees"] == match_settings(orders_to_parsed(sources), terms, [])["total_payment_fees"] == .01
    for name in ("cod", "bank_transfer"):
        terms = [{"name": name, "commission_percent": 0, "fixed_fee": 0, "vat_percent": 0}]
        result = await dashboard_fee_breakdown([order(payment_method=name)], terms, context())
        assert result["payment_fees_complete"] is True and result["total_payment_fees"] == 0


@pytest.mark.asyncio
async def test_similar_substrings_and_waiting_never_acquire_emkan_tariff():
    for name in ("not_emkan_test", "waiting", "wating"):
        result = await dashboard_fee_breakdown([order(payment_method=name)], [], context())
        assert not result["payment_fees_complete"]
        assert result["total_payment_fees"] == 0
        if name in {"waiting", "wating"}:
            assert result["fee_review_reasons"] == ["payment_awaiting_collection"]
            assert result["payment_breakdown"][0]["fee_sources"] == []
        else:
            assert result["payment_breakdown"][0]["fee_sources"] == []


def test_partial_fee_masks_dependent_totals_again_after_ad_bank_fee_merge():
    response = {"totals": {key: 123 for key in ("total_payment_fees", "net_profit", "net_sales", "expected_salla_transfer",
        "total_vat", "emkan_fees", "bnpl_fees", "bnpl_net", "bank_net", "electronic_net", "other_payment_fees")},
        "net_sales_config": {}, "payment_breakdown": [{"key": "emkan", "fee_amount": 2, "vat_amount": .3}],
        "payment_fee_authority": {"complete": False, "reason_codes": ["provider_payment_plan_required"],
            "incomplete_buckets": ["emkan"], "electronic_complete": True}}
    enforce_dashboard_fee_completeness(response)
    merge_ad_bank_fees_into_dashboard(response, {"bank_commissions": {"total_fee_sar": 2}})
    enforce_dashboard_fee_completeness(response)
    assert all(response["totals"][key] is None for key in ("total_payment_fees", "net_profit", "net_sales", "emkan_fees", "bnpl_net"))
    assert response["totals"]["bank_net"] == response["totals"]["electronic_net"] == 123
    assert response["payment_breakdown"][0]["known_fee_amount_sar"] == 2
    assert response["totals"]["ad_bank_commission_fees"] == 2


def test_raw_projection_does_not_attach_unrequested_customer_fields():
    sources = [order()]
    raw = {"created_at": "2026-11-15T12:00:00Z", "payment": {"method": "emkan"}, "customer": {"name": "private"}}
    attach_fee_source_evidence(sources, [{"order_number": "order-a", "raw_by_source": {"salla_direct": raw}}])
    assert sources[0]["_dashboard_fee_source"] == {key: raw[key] for key in ("created_at", "payment")}


def test_server_uses_shared_adapter_for_whole_and_electronic_cohorts_and_masks_final_response():
    tree = ast.parse((Path(__file__).parents[1] / "server.py").read_text())
    fn = next(node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name == "dashboard")
    calls = [node for node in ast.walk(fn) if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)]
    fee_calls = [node for node in calls if node.func.id == "dashboard_fee_breakdown"]
    assert [ast.unparse(node.args[0]) for node in fee_calls] == ["all_orders", "electronic_orders_included"]
    assert ast.unparse(fn.body[-1]) == "return enforce_dashboard_fee_completeness(response)"


def real_server_dashboard_without_bootstrap():
    """Execute the actual dashboard function without importing server/env/jobs."""
    from datetime import datetime, timezone
    from auth import DEFAULT_PAYMENT_METHODS
    from balances import compute_balances
    from dashboard_cpu_budget import CPUWorkBudget, PARSER_BUDGET_MS
    from order_currency import SALLA_RAW_CURRENCY_PROJECTION, hydrate_order_currency_fields_cooperative, summarize_orders_sar, order_total_sar
    from orders_db import orders_to_parsed_cooperative
    from salla_marketing_attribution import SALLA_RAW_ATTRIBUTION_PROJECTION, attach_projected_salla_attribution
    from settlements_routes import aggregate_settlements_by_provider, classify_14d_window

    async def ensure_user_settings(db, owner, **kwargs):
        return await db.settings.find_one({"user_id": owner})

    async def no_ads(*args):
        return {}

    async def no_operating(*args):
        return {}

    namespace = dict(locals())
    namespace.update(match_settings=match_settings, _dashboard_deepcopy=deepcopy,
        DEFAULT_SHIPPING_COMPANIES=[], DEFAULT_SHIPPING_APPROVED=["completed"], DEFAULT_COD_APPROVED=["completed"],
        DEFAULT_ELECTRONIC_NET_EXCLUDED_STATUSES=["cancelled", "بانتظار الدفع"], DEFAULT_NET_SALES_CONFIG={},
        _spend_by_date_from_ledger=no_ads, compute_operating_expenses_for_range=no_operating,
        _parse_date_or=lambda text, fallback: date.fromisoformat(text) if text else fallback)
    tree = ast.parse((Path(__file__).parents[1] / "server.py").read_text())
    functions = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                 and node.name in {"dashboard", "_is_excluded_for_electronic_net"}]
    exec(compile(ast.Module(body=functions, type_ignores=[]), "server-dashboard-read-only", "exec"), namespace)
    return namespace["dashboard"]


@pytest.mark.asyncio
@pytest.mark.parametrize("incomplete", [False, True])
async def test_real_server_dashboard_fee_and_profit_share_actual_dated_authority_without_writes(incomplete):
    from mongomock_motor import AsyncMongoMockClient
    db = AsyncMongoMockClient().dashboard_fee_regression
    await db.settings.insert_one({"user_id": "owner", "payment_methods": legacy(),
        "shipping_companies": [{"name": "carrier", "cost_per_order": 0}], "net_sales_config": {}})
    await db.unified_orders.insert_many([
        {**order(), "user_id": "owner", "total_product_cost": 10},
        {**order(order_number="actual", payment_fee_status="actual", actual_payment_fee=1, actual_payment_vat=.15),
         "user_id": "owner", "total_product_cost": 10},
        {**order(order_number="other-owner", total_amount=999), "user_id": "other"},
    ])
    before = {name: await db[name].find({}).to_list(100) for name in await db.list_collection_names()}
    result = await real_server_dashboard_without_bootstrap()(
        database=db, owner_id="owner", from_date="2026-11-01", to_date="2026-11-21", include_legacy_analyses=False,
        allow_self_heal=False, persist_settings=False,
        fee_context=context(policies=[policy(effective_to="2026-11-14")] if incomplete else [policy()]))
    after = {name: await db[name].find({}).to_list(100) for name in await db.list_collection_names()}
    assert after == before
    assert result["totals"]["total_sales"] == 200 and result["totals"]["total_orders"] == 2
    assert result["totals"]["payment_fees_complete"] is not incomplete
    if incomplete:
        assert result["totals"]["total_payment_fees"] is None
        assert result["totals"]["emkan_fees"] is None
        assert result["totals"]["net_profit"] is None
        assert result["payment_fee_authority"]["known_payment_fees_sar"] == 1.15
        assert result["payment_breakdown"][0]["fee_amount"] is None
    else:
        assert result["totals"]["total_payment_fees"] == result["totals"]["emkan_fees"] == 10.91
        assert result["totals"]["net_profit"] == 169.09
        assert result["payment_breakdown"][0]["fee_amount"] == 10.91


@pytest.mark.asyncio
async def test_actual_payment_identity_drives_electronic_subset_without_mutating_orders():
    from mongomock_motor import AsyncMongoMockClient
    db = AsyncMongoMockClient().dashboard_fee_identity
    await db.settings.insert_one({"user_id": "owner", "payment_methods": [{"name": "invalid unused", "commission_percent": "broken"}],
        "shipping_companies": [{"name": "carrier", "cost_per_order": 0}], "net_sales_config": {}})
    await db.unified_orders.insert_many([
        {**order(order_number=str(index), payment_method=method, actual_payment_method="mada", payment_fee_status="actual",
                  actual_payment_fee=1, actual_payment_vat=.15), "user_id": "owner", "total_product_cost": 0}
        for index, method in enumerate(("emkan", "mada"))])
    before = await db.unified_orders.find({}).to_list(100)
    result = await real_server_dashboard_without_bootstrap()(
        database=db, owner_id="owner", include_legacy_analyses=False, allow_self_heal=False, persist_settings=False, fee_context=context())
    assert await db.unified_orders.find({}).to_list(100) == before
    assert result["totals"]["electronic_net"] == 197.70
    assert result["totals"]["other_payment_fees"] == 2.30
    assert result["totals"]["electronic_net_breakdown"]["included_count"] == 2


@pytest.mark.asyncio
async def test_unknown_excluded_electronic_fee_masks_only_before_filter_subtotal():
    from mongomock_motor import AsyncMongoMockClient
    db = AsyncMongoMockClient().dashboard_fee_status_subset
    await db.settings.insert_one({"user_id": "owner", "payment_methods": [],
        "shipping_companies": [{"name": "carrier", "cost_per_order": 0}], "net_sales_config": {}})
    await db.unified_orders.insert_many([
        {**order(order_number="unknown", payment_method="mada", order_status="cancelled"), "user_id": "owner", "total_product_cost": 0},
        {**order(order_number="known", payment_method="mada", payment_fee_status="actual", actual_payment_fee=1,
                  actual_payment_vat=.15), "user_id": "owner", "total_product_cost": 0}])
    result = await real_server_dashboard_without_bootstrap()(
        database=db, owner_id="owner", include_legacy_analyses=False, allow_self_heal=False, persist_settings=False, fee_context=context())
    assert result["totals"]["electronic_net"] == 98.85
    assert result["totals"]["other_payment_fees"] == 1.15
    assert result["totals"]["electronic_net_breakdown"]["fees_before_filter"] is None
    assert result["totals"]["electronic_net_breakdown"]["fees_after_filter"] == 1.15
    assert result["totals"]["total_payment_fees"] is None
