"""Snapchat reports consume the same frozen sale cost, once per cohort."""
from copy import deepcopy
from datetime import datetime, timezone

import pytest
from mongomock_motor import AsyncMongoMockClient

from bundle_catalog_v2 import BUNDLES, expand_bundle
from integrations_control_center import snapchat_campaign_profitability as profit
from integrations_control_center import snapchat_campaign_profitability_exact_reuse as exact
from integrations_control_center import snapchat_campaign_created_order_semantics as created
from product_v2_details_routes import COST_PROFILES
from product_v2_routes import PRODUCTS


OWNER = "snap-bundle-owner"
PRODUCT = "735616589"
CAMPAIGN = ("account-1", "campaign-1")
LIFECYCLES = "mezan_component_order_lifecycle_v1"


async def _sale_fixture(*, quantities=(4,), rose=True, freeze=True):
    db = AsyncMongoMockClient().snap_bundle_authority
    await db[PRODUCTS].insert_many([
        {"user_id": OWNER, "salla_product_id": PRODUCT,
         "mezan_product_id": "mpv2_" + PRODUCT, "name": "Winter shawl",
         "sku": "AMS13281", "cost_price_from_salla": 70, "variants": [],
         "options": [{"id": "wrap", "name": "Rose wrapping", "values": [
             {"id": "yes", "name": "Yes"}, {"id": "no", "name": "No"}]}]},
        {"user_id": OWNER, "salla_product_id": "rose",
         "mezan_product_id": "mpv2_rose", "name": "Rose bouquet",
         "sku": "AMA10071", "cost_price_from_salla": 40, "variants": []},
    ])
    await db[COST_PROFILES].insert_many([
        {"user_id": OWNER, "salla_product_id": PRODUCT, "base_cost": 15},
        {"user_id": OWNER, "salla_product_id": "rose", "base_cost": 40},
    ])
    await db[BUNDLES].insert_one({
        "user_id": OWNER, "product_id": PRODUCT, "product_type": "composite",
        "schema_version": 2, "revision": 1, "items": [
            {"kind": "current_product", "quantity": "1", "rule_id": "shawl"},
            {"kind": "product", "product_id": "rose", "quantity": "1",
             "rule_id": "bouquet", "condition": {"option_id": "wrap", "value_id": "yes"}},
        ],
    })
    choice = {"option_id": "wrap", "value_id": "yes" if rose else "no"}
    snapshot = await expand_bundle(db, OWNER, PRODUCT, selected_options=[choice])
    orders = []
    for index, quantity in enumerate(quantities):
        number = f"order-{index + 1}"
        line_id = f"line-{index + 1}"
        order = {
            "user_id": OWNER, "order_number": number, "order_status": "completed",
            "order_date": "2026-11-01", "campaign_id": CAMPAIGN[1],
            "source": "snapchat", "currency": "SAR", "total_amount": quantity * 125,
            "products": [{"product_id": PRODUCT, "order_item_id": line_id,
                          "name": "Winter shawl", "sku": "AMS13281", "quantity": quantity,
                          "total": quantity * 125, "options_raw": [choice]}],
        }
        orders.append(order)
        if freeze:
            await db[LIFECYCLES].insert_one({
                "user_id": OWNER, "order_number": number,
                "bundle_intake_snapshots": {line_id: deepcopy(snapshot)},
            })
    return db, orders


async def _context(db, orders):
    context = await profit._load_cost_context(db, OWNER)
    context["order_cost_authority"] = await profit.load_order_cost_authority(db, OWNER, orders)
    return context


@pytest.mark.asyncio
@pytest.mark.parametrize("rose, expected", [(True, 220), (False, 60)])
async def test_order_cost_uses_selected_frozen_bundle_once(rose, expected):
    db, orders = await _sale_fixture(rose=rose)
    before = await db[LIFECYCLES].find({}).to_list(length=10)
    result = profit._order_cost_and_products(orders[0], await _context(db, orders))

    assert result["product_cost_sar"] == expected
    assert result["missing_everywhere"] is False
    assert len(result["lines"]) == 1
    assert result["lines"][0]["units"] == 4
    assert result["lines"][0]["cost_sar"] == expected
    assert await db[LIFECYCLES].find({}).to_list(length=10) == before


@pytest.mark.asyncio
async def test_frozen_bundle_survives_catalog_edits_and_foreign_sale_currency():
    db, orders = await _sale_fixture()
    await db[COST_PROFILES].update_many({"user_id": OWNER}, {"$set": {"base_cost": 999}})
    orders[0]["currency"] = "QAR"
    result = profit._order_cost_and_products(orders[0], await _context(db, orders))

    assert result["product_cost_sar"] == 220
    assert result["lines"][0]["cost_sar"] == 220


@pytest.mark.asyncio
async def test_partial_return_policy_is_applied_once_after_frozen_cost():
    db, orders = await _sale_fixture()
    orders[0]["actual_partial_refund_amount"] = 100
    result = profit._order_cost_and_products(orders[0], await _context(db, orders))

    assert result["lines"][0]["raw_cost"] == 220
    assert result["product_cost_sar"] == 176
    assert result["lines"][0]["cost_sar"] == 176
    assert result["lines"][0]["units"] == pytest.approx(3.2)


@pytest.mark.asyncio
async def test_unknown_composite_cannot_be_reported_as_parent_only_profit():
    db, orders = await _sale_fixture(freeze=False)
    result = profit._order_cost_and_products(orders[0], await _context(db, orders))
    bucket = profit._new_campaign_bucket()
    profit._add_order_to_campaign(bucket, result)
    campaign = profit._finalize_campaign(bucket, spend_sar=100)

    assert result["missing_everywhere"] is True
    assert campaign["product_cost_sar"] is None
    assert campaign["contribution_profit_sar"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("reader", ["base", "exact", "financial"])
async def test_public_campaign_readers_load_one_authority_for_cohort(monkeypatch, reader):
    db, orders = await _sale_fixture(quantities=(2, 2))
    profit._CACHE.clear()
    exact._CACHE.clear()
    created._PROFIT_CACHE.clear()
    calls = []
    original_loader = profit.load_order_cost_authority

    async def counted_loader(source_db, user_id, cohort, **kwargs):
        calls.append((user_id, [row["order_number"] for row in cohort]))
        return await original_loader(source_db, user_id, cohort, **kwargs)

    monkeypatch.setattr(profit, "load_order_cost_authority", counted_loader)
    dates = {"date_from": "2026-11-01", "date_to": "2026-11-01"}
    if reader == "base":
        async def accounts(*args, **kwargs):
            return [{"ad_account_id": CAMPAIGN[0]}]

        async def native_rows(*args, **kwargs):
            return [{"ad_account_id": CAMPAIGN[0], "campaign_id": CAMPAIGN[1], "spend_sar": 100}]

        async def identities(*args, **kwargs):
            return [{"account_id": CAMPAIGN[0], "campaign_id": CAMPAIGN[1], "campaign_name": "Winter"}]

        async def filtered(*args, **kwargs):
            return deepcopy(orders)

        monkeypatch.setattr(profit, "_load_selected_accounts", accounts)
        monkeypatch.setattr(profit, "_campaign_native_rows", native_rows)
        monkeypatch.setattr(profit, "_campaign_identities", identities)
        monkeypatch.setattr(profit, "_filtered_orders", filtered)
        response = await profit.build_campaign_profitability(db, OWNER, **dates)
        by_campaign, totals = response["by_campaign"], response["totals"]
        await profit.build_campaign_profitability(db, OWNER, **dates)
    elif reader == "exact":
        kwargs = {**dates, "matched_orders": {CAMPAIGN: orders}, "campaign_spend": {CAMPAIGN: 100}}
        by_campaign, totals = await exact.calculate_profitability_from_exact_matches(db, OWNER, **kwargs)
        await exact.calculate_profitability_from_exact_matches(db, OWNER, **kwargs)
    else:
        kwargs = {**dates, "account_id": CAMPAIGN[0], "financial_matched": {CAMPAIGN: orders},
                  "campaign_spend": {CAMPAIGN: 100}}
        by_campaign, totals = await created.calculate_financial_profitability(db, OWNER, **kwargs)
        await created.calculate_financial_profitability(db, OWNER, **kwargs)

    assert calls == [(OWNER, ["order-1", "order-2"])]
    assert by_campaign[CAMPAIGN]["orders"] == 2
    assert by_campaign[CAMPAIGN]["product_cost_sar"] == 220
    assert by_campaign[CAMPAIGN]["contribution_profit_sar"] == 180
    assert by_campaign[CAMPAIGN]["products"][0]["units"] == 4
    assert totals["product_cost_sar"] == 220


@pytest.mark.asyncio
@pytest.mark.parametrize("reader", ["decision", "diagnostic"])
async def test_decision_and_diagnostic_views_share_frozen_cost(monkeypatch, reader):
    import dashboard_v2_routes
    from integrations_control_center import snapchat_campaign_result_source_routes as sources
    from integrations_control_center import snapchat_decision_diagnostics as diagnostics
    from integrations_control_center import snapchat_decision_metrics as metrics

    db, orders = await _sale_fixture(quantities=(2, 2))
    await db[metrics.SNAPCHAT_PERFORMANCE_COLLECTION].insert_one({
        "user_id": OWNER, "provider": metrics.SNAPCHAT_PROVIDER_ID,
        "ad_account_id": CAMPAIGN[0], "campaign_id": CAMPAIGN[1],
        "entity_type": "campaign", "date": "2026-11-01", "spend_sar": 100,
    })
    calls = []
    original_loader = profit.load_order_cost_authority

    async def counted_loader(source_db, user_id, cohort, **kwargs):
        calls.append((user_id, [row["order_number"] for row in cohort]))
        return await original_loader(source_db, user_id, cohort, **kwargs)

    async def filtered(*args, **kwargs):
        return deepcopy(orders)

    async def identities(*args, **kwargs):
        return [{"account_id": CAMPAIGN[0], "campaign_id": CAMPAIGN[1], "campaign_name": "Winter"}]

    monkeypatch.setattr(profit, "load_order_cost_authority", counted_loader)
    monkeypatch.setattr(dashboard_v2_routes, "_filtered_orders", filtered)
    monkeypatch.setattr(sources, "_campaign_identities", identities)
    if reader == "decision":
        result = await metrics.capture_decision_baseline(
            db, OWNER, account_id=CAMPAIGN[0], campaign_id=CAMPAIGN[1],
            captured_at=datetime(2026, 11, 2, 10, tzinfo=timezone.utc),
            completed_days_only=True,
        )
        window = next(row for row in result["windows"] if row["days"] == 1)
        campaign, store = window["campaign"], window["store"]
    else:
        result = await diagnostics._load_comparison_evidence(
            db, OWNER, account_id=CAMPAIGN[0],
            periods=diagnostics._periods("2026-11-01", "2026-11-01"),
        )
        campaign, store = result["campaigns"]["selected"][CAMPAIGN], result["store"]["selected"]

    assert calls == [(OWNER, ["order-1", "order-2"])]
    assert campaign["product_cost_sar"] == 220
    assert campaign["contribution_profit_sar"] == 180
    assert store["product_cost_sar"] == 220
