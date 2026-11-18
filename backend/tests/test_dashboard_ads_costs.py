"""Advertising hover finances retain existing cost authority and uncertainty."""
import ast
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import inspect
import json

import pytest

from dashboard_ads_account_results import build_dashboard_ads_account_results
from dashboard_ads_costs import DashboardAdsCostCollector
import dashboard_v2_routes as dashboard
from excel_parser import match_settings
from orders_db import orders_to_parsed
from shipping_cost_ssot import aggregate_breakdown
from tests.test_dashboard_ads_account_results import Db, campaign, rows_by_id
from tests.test_dashboard_bundle_cost_authority import (
    OWNER, ReadOnlyDB, db as bundle_db, freeze, sale, sale_line,
)
from tests.test_dashboard_runtime_source_contract import (
    RemoveCheckpoints, SelectDefaultCostCollector, SelectDefaultProductRows,
)


DAY = "2026-11-15"
PAST_NOW = datetime(2026, 11, 16, 12, tzinfo=timezone.utc)
SETTINGS = {
    "payment_methods": [
        {"name": "mada", "commission_percent": 0.78, "fixed_fee": 1, "vat_percent": 15},
        {"name": "tamara", "commission_percent": 0.5, "fixed_fee": 0, "vat_percent": 0},
        {"name": "bank_transfer", "commission_percent": 0, "fixed_fee": 0, "vat_percent": 0},
    ],
    "shipping_companies": [{"name": "carrier", "cost_per_order": 10, "vat_percent": 15,
                            "is_deferred": True}],
}


def ad_order(number, provider="snapchat", account="usd", hour="10:00:00", quantity=1, **extra):
    fields = {
        "order_date": DAY, "order_date_raw": f"{DAY}T{hour}+03:00",
        "source_native": provider, "campaign_id": "c-" + account,
        "payment_method": "mada", "shipping_company": "carrier", **extra,
    }
    return sale(
        number, sale_line(number, quantity, product="plain"),
        **fields,
    )


async def snapshot(bundle_db, orders, *, settings=None, now=PAST_NOW, date_to=DAY):
    costs = DashboardAdsCostCollector()
    costs.capture_settings(SETTINGS if settings is None else settings)
    await dashboard.build_mezan_v2_product_cost_cooperative(
        ReadOnlyDB(bundle_db), OWNER, orders, include_product_rows=False, cost_collector=costs,
    )
    identity_db = Db([campaign("usd", "c-usd", user_id=OWNER),
                      campaign("sar", "c-sar", user_id=OWNER)])
    result = await build_dashboard_ads_account_results(
        identity_db, OWNER, orders, snapchat_account_ids=["usd", "sar"],
        date_from=DAY, date_to=date_to, now=now, cost_collector=costs,
    )
    return result, costs, identity_db


def test_default_observer_hooks_preserve_the_exact_frozen_product_oracle():
    source = inspect.getsource(dashboard.build_mezan_v2_product_cost)
    # Exact independently reviewed received-unit invoice cost rule. Observer
    # hooks must preserve the shared reducer, its output and all read counts.
    assert hashlib.sha256(source.rstrip().encode()).hexdigest() == "0d5a1726a6cb805a464ab6c0d0fa450e6e5e35cad67e8bf338fa8249502fc196"
    before = ast.parse(source).body[0]
    adopted = ast.parse(inspect.getsource(dashboard.build_mezan_v2_product_cost_cooperative)).body[0]
    assert [arg.arg for arg in adopted.args.kwonlyargs] == ["include_product_rows", "cost_collector"]
    assert [ast.unparse(value) for value in adopted.args.kw_defaults] == ["True", "None"]
    adopted.args.kwonlyargs, adopted.args.kw_defaults = [], []
    observer = SelectDefaultCostCollector()
    adopted = observer.visit(adopted)
    assert observer.calls == ["cost_collector.begin_order(order)",
                              "cost_collector.observe_line(order, item, result)",
                              "cost_collector.finish_order(order, adjusted_total)"]
    adopted = SelectDefaultProductRows().visit(adopted)
    adopted = RemoveCheckpoints().visit(adopted)
    adopted.name = before.name
    assert ast.dump(before) == ast.dump(adopted)


@pytest.mark.asyncio
@pytest.mark.parametrize("include_product_rows", [False, True])
async def test_collector_on_off_keeps_product_output_and_all_read_counts(bundle_db, include_product_rows):
    frozen = sale("frozen", sale_line("frozen-line", 4), actual_partial_refund_amount=100)
    await freeze(bundle_db, frozen)
    orders = [frozen, sale("ordinary", sale_line("plain", 2, product="plain")),
              sale("missing", {"product_id": "unpriced", "quantity": 2, "total": 25}),
              sale("cancelled", sale_line("cancelled-line", product="plain"), order_status="ملغي"),
              sale("no-products")]
    original_orders = deepcopy(orders)
    off_db, on_db = ReadOnlyDB(bundle_db), ReadOnlyDB(bundle_db)
    off = await dashboard.build_mezan_v2_product_cost_cooperative(
        off_db, OWNER, orders, include_product_rows=include_product_rows)
    collector = DashboardAdsCostCollector()
    on = await dashboard.build_mezan_v2_product_cost_cooperative(
        on_db, OWNER, orders, include_product_rows=include_product_rows, cost_collector=collector)
    assert on == off
    assert on_db.reads == off_db.reads
    assert orders == original_orders
    assert sum(row["known_product_cost_sar"] for row in collector.product_orders.values()) == on["total"]
    assert collector.product_orders[id(frozen)]["known_product_cost_sar"] == 165
    assert collector.product_orders[id(frozen)]["known_pieces"] == 4
    assert collector.product_orders[id(orders[-2])]["known_product_cost_sar"] == 0
    assert collector.product_orders[id(orders[-1])]["product_cost_complete"] is False


@pytest.mark.asyncio
async def test_all_five_rows_and_day_use_the_loaded_same_cost_sources(bundle_db):
    orders = []
    for provider, account in (("snapchat", "usd"), ("snapchat", "sar"),
                              ("google", ""), ("tiktok", ""), ("meta", "")):
        orders += [ad_order(f"{provider}-{account}-am", provider, account, quantity=1),
                   ad_order(f"{provider}-{account}-pm", provider, account, hour="12:00:00", quantity=2)]
    orders[0].update({"customer_name": "PRIVATE-SYNTHETIC-CUSTOMER", "actual_payment_fee": 999,
                      "payment_fee_status": "actual", "operating_allocation": 999})
    result, costs, identities = await snapshot(bundle_db, orders)
    assert len(identities.catalog.reads) == 1
    all_rows = result["accounts"] + list(result["provider_results"].values())
    assert len(all_rows) == 5
    for row in all_rows:
        hover = row["hover_summary"]
        assert row["salla_orders"] == 2
        assert row["salla_sales_sar"] == 300
        assert hover["pieces"] == 3
        assert hover["product_cost_sar"] == 27
        assert hover["shipping_cost_sar"] == 23  # Includes deferred shipping.
        assert hover["complete"] is True
        assert hover["total_cost_sar"] == round(50 + hover["payment_fees_sar"], 2)
    day = result["daily_total"]
    assert day["salla_orders"] == 10 and day["salla_sales_sar"] == 1500
    hover = day["hover_summary"]
    assert hover["pieces"] == 15 and hover["product_cost_sar"] == 135
    assert hover["shipping_cost_sar"] == aggregate_breakdown(orders, costs.company_configs)["total_with_tax"]
    assert hover["payment_fees_sar"] == match_settings(orders_to_parsed(orders), SETTINGS["payment_methods"], [])["total_payment_fees"]
    assert hover["half_day_payment_fees_rounding_delta_sar"] == 0
    assert "PRIVATE-SYNTHETIC-CUSTOMER" not in json.dumps(result)
    assert "operating" not in json.dumps(result)
    assert "bank_fee" not in json.dumps(result)
    assert [segment["total"]["salla_orders"] for segment in result["segments"]] == [5, 5]
    assert [segment["total"]["hover_summary"]["pieces"] for segment in result["segments"]] == [5, 10]


@pytest.mark.asyncio
async def test_aggregate_payment_rounding_is_reported_never_allocated(bundle_db):
    orders = [ad_order("round-am", hour="11:59:59", total_amount=1, payment_method="tamara"),
              ad_order("round-pm", hour="12:00:00", total_amount=1, payment_method="tamara")]
    result, _, _ = await snapshot(bundle_db, orders)
    daily = rows_by_id(result)["usd"]["hover_summary"]
    halves = [rows_by_id(segment)["usd"]["hover_summary"] for segment in result["segments"]]
    expected_day = match_settings(orders_to_parsed(orders), SETTINGS["payment_methods"], [])["total_payment_fees"]
    expected_halves = [match_settings(orders_to_parsed([order]), SETTINGS["payment_methods"], [])["total_payment_fees"] for order in orders]
    assert daily["payment_fees_sar"] == expected_day
    assert [row["payment_fees_sar"] for row in halves] == expected_halves
    assert daily["half_day_payment_fees_rounding_delta_sar"] == round(sum(expected_halves) - expected_day, 2) != 0


@pytest.mark.asyncio
async def test_missing_fields_stay_unknown_while_proven_zero_is_zero(bundle_db):
    settings = {"payment_methods": SETTINGS["payment_methods"],
                "shipping_companies": [{"name": "carrier", "cost_per_order": 0}]}
    good = ad_order("zero", payment_method="bank_transfer")
    missing = ad_order("missing", account="sar", payment_method="unconfigured", shipping_company="unknown")
    missing["products"] = [{"product_id": "unpriced", "total": 50}]
    result, _, _ = await snapshot(bundle_db, [good, missing], settings=settings)
    zero, unknown = (rows_by_id(result)[key]["hover_summary"] for key in ("usd", "sar"))
    assert zero["shipping_cost_sar"] == zero["payment_fees_sar"] == 0
    assert zero["total_cost_sar"] == 9
    assert all(unknown[key] is None for key in ("pieces", "product_cost_sar", "shipping_cost_sar", "payment_fees_sar", "total_cost_sar"))
    assert unknown["known_product_cost_sar"] == unknown["known_payment_fees_sar"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("currency_fields", [{"currency": "KWD"}, {"currency": "SAR", "original_currency": "KWD"}])
async def test_foreign_shipping_fallback_and_unknown_fx_cannot_become_sar_zero(bundle_db, currency_fields):
    order = ad_order("foreign", **currency_fields, shipping_cost=5, shipping_company="unconfigured")
    result, _, _ = await snapshot(bundle_db, [order])
    row = rows_by_id(result)["usd"]
    assert row["salla_orders"] == 1 and row["salla_sales_sar"] is None
    assert row["hover_summary"]["product_cost_sar"] == 9
    assert row["hover_summary"]["shipping_cost_sar"] is None
    assert row["hover_summary"]["payment_fees_sar"] is None
    assert row["hover_summary"]["total_cost_sar"] is None


@pytest.mark.asyncio
async def test_missing_timestamp_preserves_day_and_only_masks_affected_half_rows(bundle_db):
    unknown = ad_order("date-only")
    unknown["order_date_raw"] = DAY
    result, _, _ = await snapshot(bundle_db, [unknown, ad_order("timed", account="sar"),
                                           ad_order("google", provider="google")])
    assert rows_by_id(result)["usd"]["salla_orders"] == 1
    assert result["daily_total"]["hover_summary"]["total_cost_sar"] is not None
    morning = result["segments"][0]
    assert morning["coverage"]["unassigned_time_orders"] == 1
    assert rows_by_id(morning)["usd"]["salla_orders"] is None
    assert rows_by_id(morning)["usd"]["hover_summary"]["total_cost_sar"] is None
    assert rows_by_id(morning)["sar"]["salla_orders"] == 1
    assert morning["provider_results"]["google"]["salla_orders"] == 1
    assert morning["total"]["salla_orders"] is None


@pytest.mark.asyncio
async def test_unassigned_afternoon_account_does_not_hide_proven_morning(bundle_db):
    unknown = ad_order("unknown", hour="12:30:00")
    unknown["campaign_id"] = None
    result, _, _ = await snapshot(bundle_db, [ad_order("known"), unknown])
    assert rows_by_id(result)["usd"]["salla_orders"] is None
    assert result["daily_total"]["salla_orders"] == 2
    assert result["daily_total"]["known_salla_orders"] == 2
    assert result["direct_result"]["salla_orders"] == 0
    assert result["daily_total"]["hover_summary"]["total_cost_sar"] is not None
    assert [segment["total"]["salla_orders"] for segment in result["segments"]] == [1, 1]
    assert rows_by_id(result["segments"][0])["usd"]["salla_orders"] == 1
    assert all(row["salla_orders"] is None for row in result["segments"][1]["accounts"])


@pytest.mark.asyncio
async def test_known_cohort_costs_remain_proven_without_completing_account_attribution(bundle_db):
    orders = [ad_order("mapped", quantity=2, payment_method="bank_transfer"),
              ad_order("unassigned", hour="12:30:00", campaign_id=None, payment_method="bank_transfer")]
    before = deepcopy(orders)
    result, _, identities = await snapshot(bundle_db, orders)
    mapped = rows_by_id(result)["usd"]
    unassigned = result["unassigned_result"]
    for row, pieces, total in ((mapped, 2, 29.5), (unassigned, 1, 20.5)):
        summary = row["hover_summary"]
        assert row["attribution_complete"] is False
        assert row["salla_orders"] is row["salla_sales_sar"] is None
        assert summary["complete"] is False
        assert summary["total_cost_sar"] is None
        assert summary["known_pieces"] == pieces
        assert summary["known_total_cost_sar"] == total
        assert summary["known_total_cost_complete"] is True
        for flag in ("pieces_complete", "product_cost_complete", "shipping_cost_complete", "payment_fees_complete"):
            assert summary[flag] is False
            assert summary["known_" + flag] is True
    assert result["coverage"]["unassigned_orders"] == 1
    assert result["daily_total"]["hover_summary"]["total_cost_sar"] == 50
    afternoon_unknown = result["segments"][1]["unassigned_result"]["hover_summary"]
    assert afternoon_unknown["known_total_cost_sar"] == 20.5
    assert afternoon_unknown["known_total_cost_complete"] is True
    assert afternoon_unknown["complete"] is False
    assert orders == before
    assert len(identities.catalog.reads) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", ["product", "shipping", "payment", "currency"])
async def test_known_cohort_completeness_does_not_certify_missing_costs_as_zero(bundle_db, missing):
    mapped = ad_order("incomplete")
    if missing == "product":
        mapped["products"] = [{"product_id": "unpriced", "quantity": 1, "total": 50}]
    elif missing == "shipping":
        mapped["shipping_company"] = "unconfigured"
    elif missing == "payment":
        mapped["payment_method"] = "unconfigured"
    else:
        mapped["currency"] = "KWD"
    result, _, _ = await snapshot(bundle_db, [mapped, ad_order("unassigned", campaign_id=None)])
    summary = rows_by_id(result)["usd"]["hover_summary"]
    failed_flag = {"product": "known_product_cost_complete", "shipping": "known_shipping_cost_complete",
                   "payment": "known_payment_fees_complete", "currency": "known_payment_fees_complete"}[missing]
    assert summary[failed_flag] is False
    assert summary["known_total_cost_complete"] is False
    assert summary["known_total_cost_sar"] is None
    assert summary["complete"] is False
    assert summary["total_cost_sar"] is None
    if missing != "currency":
        known_value = {"product": "known_product_cost_sar", "shipping": "known_shipping_cost_sar",
                       "payment": "known_payment_fees_sar"}[missing]
        assert summary[known_value] == 0


@pytest.mark.asyncio
async def test_known_cost_flags_distinguish_unobserved_orders_from_an_empty_cohort():
    costs = DashboardAdsCostCollector()
    costs.capture_settings(SETTINGS)
    missing = await costs.summary([ad_order("not-observed")], scope_complete=False)
    assert missing["known_pieces"] == missing["known_product_cost_sar"] == 0
    assert missing["known_pieces_complete"] is False
    assert missing["known_product_cost_complete"] is False
    assert missing["known_total_cost_complete"] is False
    assert missing["known_total_cost_sar"] is None
    empty = await costs.summary([], scope_complete=False)
    assert empty["known_total_cost_sar"] == 0
    assert empty["known_total_cost_complete"] is True
    assert all(empty["known_" + flag] for flag in (
        "pieces_complete", "product_cost_complete", "shipping_cost_complete", "payment_fees_complete"))
    assert empty["complete"] is False
    assert empty["total_cost_sar"] is None


@pytest.mark.asyncio
async def test_direct_hover_and_all_order_costs_include_unrepresented_paid_orders_once(bundle_db):
    orders = [
        ad_order("direct-am", provider="store"),
        ad_order("direct-pm", provider="", hour="12:00:00", quantity=2),
        ad_order("snap-known"),
        ad_order("snap-unassigned", hour="12:30:00", campaign_id=None),
    ]
    result, costs, identities = await snapshot(bundle_db, orders)
    direct, total = result["direct_result"], result["daily_total"]
    assert (direct["salla_orders"], direct["salla_sales_sar"]) == (2, 300)
    assert direct["ad_spend_applicable"] is False
    assert direct["hover_summary"]["pieces"] == 3
    assert direct["hover_summary"]["product_cost_sar"] == 27
    assert direct["hover_summary"]["shipping_cost_sar"] == 23
    assert direct["hover_summary"]["complete"] is True
    assert [part["direct_result"]["salla_orders"] for part in result["segments"]] == [1, 1]
    assert [part["direct_result"]["hover_summary"]["pieces"] for part in result["segments"]] == [1, 2]
    assert (total["salla_orders"], total["salla_sales_sar"]) == (4, 500)
    assert total["hover_summary"]["pieces"] == 5
    assert total["hover_summary"]["product_cost_sar"] == 45
    assert total["hover_summary"]["shipping_cost_sar"] == aggregate_breakdown(orders, costs.company_configs)["total_with_tax"]
    assert total["hover_summary"]["payment_fees_sar"] == match_settings(orders_to_parsed(orders), SETTINGS["payment_methods"], [])["total_payment_fees"]
    assert total["hover_summary"]["row_payment_fees_rounding_delta_sar"] is None
    assert [part["total"]["salla_orders"] for part in result["segments"]] == [2, 2]
    assert all(part["total"]["cohort_basis"] == "executive_filtered_orders" for part in result["segments"])
    assert len(identities.catalog.reads) == 1


@pytest.mark.asyncio
async def test_direct_missing_time_and_currency_preserve_day_count_and_known_subtotals(bundle_db):
    unknown = ad_order("direct-date-only", provider="store", order_date_raw=DAY, currency="KWD")
    orders = [unknown, ad_order("direct-known", provider="store"), ad_order("google-known", provider="google")]
    result, _, identities = await snapshot(bundle_db, orders)
    direct, total = result["direct_result"], result["daily_total"]
    assert direct["salla_orders"] == 2
    assert direct["salla_sales_sar"] is None and direct["known_salla_sales_sar"] == 100
    assert direct["hover_summary"]["payment_fees_sar"] is None
    assert "payment_sales_currency_unverified" in direct["hover_summary"]["reason_codes"]
    assert total["salla_orders"] == 3
    assert total["salla_sales_sar"] is None and total["known_salla_sales_sar"] == 200
    assert result["coverage"]["unassigned_time_orders"] == 1
    assert result["coverage"]["time_assignment_complete"] is False
    assert "order_time_unverified" in result["coverage"]["reason_codes"]
    for part in result["segments"]:
        assert part["direct_result"]["salla_orders"] is None
        assert part["direct_result"]["time_assignment_complete"] is False
        assert "order_time_unverified" in part["direct_result"]["hover_summary"]["reason_codes"]
        assert part["total"]["salla_orders"] is None
    assert result["segments"][0]["direct_result"]["known_salla_orders"] == 1
    assert result["segments"][0]["provider_results"]["google"]["salla_orders"] == 1
    assert identities.catalog.reads == []


@pytest.mark.asyncio
async def test_outside_account_time_and_costs_stay_in_total_without_affecting_direct_or_accounts(bundle_db):
    orders = [ad_order("direct", provider="store"),
              ad_order("outside", account="outside", order_date_raw=DAY)]
    costs = DashboardAdsCostCollector()
    costs.capture_settings(SETTINGS)
    await dashboard.build_mezan_v2_product_cost_cooperative(
        ReadOnlyDB(bundle_db), OWNER, orders, include_product_rows=False, cost_collector=costs,
    )
    identities = Db([campaign("outside", "c-outside", user_id=OWNER)])
    result = await build_dashboard_ads_account_results(
        identities, OWNER, orders, snapchat_account_ids=["usd", "sar"],
        date_from=DAY, date_to=DAY, now=PAST_NOW, cost_collector=costs,
    )
    assert result["daily_total"]["salla_orders"] == 2
    assert result["daily_total"]["hover_summary"]["product_cost_sar"] == 18
    assert result["daily_total"]["hover_summary"]["row_payment_fees_rounding_delta_sar"] is None
    assert result["coverage"]["unrepresented_orders"] == 1
    assert result["coverage"]["unassigned_time_orders"] == 1
    assert result["direct_result"]["salla_orders"] == 1
    assert all(row["salla_orders"] == 0 for row in result["accounts"])
    morning = result["segments"][0]
    assert morning["total"]["salla_orders"] is None
    assert morning["total"]["known_salla_orders"] == 1
    assert morning["direct_result"]["salla_orders"] == 1
    assert all(row["salla_orders"] == 0 for row in morning["accounts"])


@pytest.mark.asyncio
async def test_before_noon_has_empty_afternoon_and_multi_day_keeps_daily_compatibility(bundle_db):
    result, _, _ = await snapshot(bundle_db, [ad_order("morning")],
                                  now=datetime.fromisoformat(DAY + "T11:30:00+03:00"))
    afternoon = result["segments"][1]
    assert afternoon["status"] == "not_started"
    assert rows_by_id(afternoon)["usd"]["salla_orders"] is None
    assert rows_by_id(afternoon)["usd"]["known_salla_orders"] == 0
    assert rows_by_id(afternoon)["usd"]["hover_summary"]["total_cost_sar"] is None
    multi, _, _ = await snapshot(bundle_db, [ad_order("range")], date_to="2026-11-16")
    assert rows_by_id(multi)["usd"]["salla_orders"] == 1
    assert all(segment["status"] == "unavailable" and "single_day_required" in segment["reason_codes"]
               for segment in multi["segments"])
