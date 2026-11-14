"""Campaign profit acceptance through the real read-only cost authorities."""
from copy import deepcopy
from datetime import datetime, timezone

import pytest

from order_engine import campaign_hover as hover
from .test_dashboard_bundle_cost_authority import OWNER, ReadOnlyDB, db, freeze, sale, sale_line

NOW = datetime(2026, 11, 14, 12, 30, tzinfo=timezone.utc)


class Clock(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW.astimezone(tz) if tz else NOW.replace(tzinfo=None)


@pytest.fixture(autouse=True)
def fixed_clock(monkeypatch):
    monkeypatch.setattr(hover, "datetime", Clock)
    hover._CACHE.clear()
    hover._PENDING.clear()


async def campaign_setup(db, *, spend=50, complete=True):
    await db.mezan_integration_accounts_v2.insert_one({"user_id": OWNER, "provider": "snapchat_ads",
        "ad_account_id": "synthetic-account", "external_account_id": "synthetic-account",
        "timezone": "America/Los_Angeles", "currency": "SAR"})
    await db.mezan_snapchat_entities_v2.insert_one({"user_id": OWNER, "entity_type": "campaign",
        "external_id": "synthetic-campaign", "ad_account_id": "synthetic-account", "name": "Synthetic campaign"})
    if spend is not None:
        await db.mezan_snapchat_campaign_account_day_v2.insert_one({"user_id": OWNER,
            "ad_account_id": "synthetic-account", "campaign_id": "synthetic-campaign", "date": "2026-11-14",
            "account_timezone": "America/Los_Angeles", "source_mode": "snapchat_native_account_day_v2",
            "spend_sar": spend, "currency_native": "SAR", "coverage_status": "provisional_hourly",
            "coverage_complete": complete, "hours_observed": 4, "missing_hours": 0 if complete else 1,
            "coverage_start_utc": "2026-11-14T08:00:00+00:00", "coverage_end_utc": "2026-11-14T12:00:00+00:00",
            "updated_at": "2026-11-14T12:05:00+00:00"})
    await db.settings.insert_one({"user_id": OWNER,
        "payment_methods": [{"name": "mada", "commission_percent": 2, "fixed_fee": 0, "vat_percent": 15}],
        "shipping_companies": [{"name": "Synthetic carrier", "cost_per_order": 15, "vat_percent": 15}]})
    await db.unified_orders.insert_one({"user_id": OWNER, "order_number": "other-store-sales",
        "order_date": "2026-11-14", "order_status": "تم التنفيذ", "currency": "SAR", "total_amount": 9800})
    await db.operating_salaries.insert_one({"user_id": OWNER, "category": "charity",
        "monthly_amount": 9000, "status": "active", "start_date": "2026-11-01"})


def campaign_order(number="one", *, at="2026-11-14T09:00:00+00:00", cid="synthetic-campaign", **fields):
    result = sale(number, sale_line("ordinary-line", 2, product="plain"),
        order_date=at[:10], payment_method="mada", shipping_company="Synthetic carrier", **fields)
    result["raw_by_source"] = {"salla_direct": {"reference_id": number, "date": {"date": at},
        "utm_source": "snapchat", "utm_campaign": cid,
        "amounts": {"total": {"amount": result["total_amount"], "currency": result["currency"]}},
        "items": [{"id": line["order_item_id"], "product_id": line["product_id"],
                   "quantity": line["quantity"], "type": "product"} for line in result["products"]]}}
    return result


async def report(db):
    result = await hover.read_campaign_hover(ReadOnlyDB(db), OWNER, ["synthetic-campaign"])
    return result["campaigns"]["synthetic-campaign"]


@pytest.mark.asyncio
async def test_campaign_direct_cost_uses_same_products_shipping_and_payment_rules(db):
    await campaign_setup(db)
    await freeze(db, campaign_order())
    row = await report(db)
    # 2*9 product + 15*1.15 shipping + 200*2%*1.15 payment + 50*2.3% ad bank fee.
    assert row["total_cost_sar"] == 41
    assert row["product_cost_sar"] == 18
    assert row["shipping_cost_sar"] == 17.25
    assert row["order_payment_fees_sar"] == 4.6
    assert row["ad_bank_fees_sar"] == 1.15
    assert row["costs_complete"] is True
    assert row["operating_basis"]["sales_sar"] == 10000
    assert row["operating_basis"]["expenses_sar"] == 300
    assert row["operating_expenses_ratio"] == .03
    assert row["operating_expenses_sar"] == 6
    assert row["net_profit_sar"] == 103  # 200 - 50 - 41 - 6, no duplicated ad expense.
    assert row["net_profit_available"] is True


@pytest.mark.asyncio
async def test_missing_product_cost_preserves_sales_but_never_certifies_profit(db):
    await campaign_setup(db)
    order = campaign_order()
    order["products"][0]["product_id"] = "unconfigured-product"
    order["products"][0]["sku"] = "UNKNOWN"
    order["raw_by_source"]["salla_direct"]["items"][0]["product_id"] = "unconfigured-product"
    await db.unified_orders.insert_one(order)
    row = await report(db)
    assert row["sales_sar"] == 200 and row["spend_sar"] == 50
    assert row["product_cost_complete"] is False
    assert row["total_cost_sar"] is row["net_profit_sar"] is None
    assert row["net_profit_available"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", ["shipping", "payment"])
async def test_missing_merchant_cost_is_unknown_not_zero(db, missing):
    await campaign_setup(db)
    await freeze(db, campaign_order())
    key = "shipping_companies" if missing == "shipping" else "payment_methods"
    await db.settings.update_one({"user_id": OWNER}, {"$set": {key: []}})
    row = await report(db)
    assert row["total_cost_sar"] is row["net_profit_sar"] is None
    assert row["product_cost_sar"] == 18
    assert row["operating_expenses_sar"] == 6
    assert row["costs_complete"] is False


@pytest.mark.asyncio
async def test_operating_ratio_is_dynamic_unrounded_and_not_a_fixed_three_percent(db):
    await campaign_setup(db)
    await freeze(db, campaign_order())
    await db.operating_salaries.update_one({"user_id": OWNER}, {"$set": {"monthly_amount": 9555}})
    row = await report(db)
    assert row["operating_expenses_ratio"] == .03185
    assert row["operating_expenses_sar"] == 6.37  # Uses3.185%, not displayed3.19%.
    assert row["net_profit_sar"] == 102.63


@pytest.mark.asyncio
async def test_unknown_store_currency_hides_allocated_operating_and_net_only(db):
    await campaign_setup(db)
    await freeze(db, campaign_order())
    await db.unified_orders.update_one({"order_number": "other-store-sales"}, {"$set": {"currency": "KWD"}})
    row = await report(db)
    assert row["total_cost_sar"] == 41 and row["costs_complete"] is True
    assert row["operating_expenses_sar"] is row["net_profit_sar"] is None
    assert row["operating_expenses_complete"] is False


@pytest.mark.asyncio
async def test_partial_snap_hours_keep_a_truthfully_provisional_profit(db):
    await campaign_setup(db, complete=False)
    await freeze(db, campaign_order())
    row = await report(db)
    assert row["net_profit_sar"] == 103
    assert row["net_profit_available"] is row["profit_provisional"] is True
    assert row["spend_warning"] == "snapchat_hours_incomplete"


@pytest.mark.asyncio
async def test_loss_and_empty_campaign_do_not_clamp_profit_or_guess_unknown_spend(db):
    await campaign_setup(db, spend=300)
    row = await report(db)
    assert row["orders"] == 0 and row["sales_sar"] == 0
    assert row["product_cost_sar"] == row["shipping_cost_sar"] == row["order_payment_fees_sar"] == 0
    assert row["ad_bank_fees_sar"] == row["total_cost_sar"] == 6.9
    assert row["operating_expenses_sar"] == 0
    assert row["net_profit_sar"] == -306.9
    await db.mezan_snapchat_campaign_account_day_v2.delete_many({})
    row = await report(db)
    assert row["spend_sar"] is row["net_profit_sar"] is None


@pytest.mark.asyncio
async def test_account_fee_setting_is_applied_once_and_never_crosses_account(db):
    from ads_manager.account_cost_settings import COLLECTION
    await campaign_setup(db)
    await freeze(db, campaign_order())
    await db[COLLECTION].insert_many([
        {"user_id": OWNER, "provider": "snapchat_ads", "external_account_id": "synthetic-account",
         "bank_commission_pct": 4, "apply_bank_commission": True},
        {"user_id": "other-owner", "provider": "snapchat_ads", "external_account_id": "synthetic-account",
         "bank_commission_pct": 90, "apply_bank_commission": True},
        {"user_id": OWNER, "provider": "snapchat_ads", "external_account_id": "different-account",
         "bank_commission_pct": 80, "apply_bank_commission": True}])
    row = await report(db)
    assert row["ad_bank_fees_sar"] == 2
    assert row["total_cost_sar"] == 41.85
    assert row["net_profit_sar"] == 102.15


@pytest.mark.asyncio
async def test_costs_follow_exact_campaign_and_account_day_not_store_wide_sales(db):
    await campaign_setup(db)
    await freeze(db, campaign_order())
    for number, at, cid in [("before-account-day", "2026-11-14T07:59:59+00:00", "synthetic-campaign"),
                            ("other-campaign", "2026-11-14T09:00:00+00:00", "other")]:
        await freeze(db, campaign_order(number, at=at, cid=cid))
    await db.unified_orders.insert_one({**campaign_order("other-owner"), "user_id": "different-owner"})
    row = await report(db)
    assert row["orders"] == 1 and row["sales_sar"] == 200
    assert row["product_cost_sar"] == 18 and row["total_cost_sar"] == 41
    # The operating denominator intentionally includes the whole store day.
    assert row["operating_basis"]["sales_sar"] == 10400
    assert row["operating_expenses_sar"] == 5.77


@pytest.mark.asyncio
async def test_foreign_sales_and_shipping_use_exact_raw_order_fx_once(db):
    await campaign_setup(db)
    order = campaign_order(currency="KWD", shipping_cost=5)
    raw = order["raw_by_source"]["salla_direct"]
    raw["amounts"]["total"]["amount"] = 10
    raw["exchange_rate"] = {"rate": "12.2", "base_currency": "SAR", "exchange_currency": "KWD"}
    # A stale promoted total must not become this campaign's payment-fee base.
    order.update(total_amount_sar=9999, accounting_currency="SAR", currency_conversion_status="verified",
                 exchange_rate_to_sar="12.2")
    await freeze(db, order)
    await db.settings.update_one({"user_id": OWNER}, {"$set": {"shipping_companies": []}})
    row = await report(db)
    assert row["sales_sar"] == 122
    assert row["product_cost_sar"] == 18
    assert row["shipping_cost_sar"] == 61
    assert row["order_payment_fees_sar"] == 2.81
    assert row["total_cost_sar"] == 82.96
    raw.pop("exchange_rate")
    await db.unified_orders.update_one({"order_number": "one"}, {"$set": {"raw_by_source": order["raw_by_source"]}})
    row = await report(db)
    assert row["sales_sar"] is row["shipping_cost_sar"] is row["net_profit_sar"] is None


@pytest.mark.asyncio
async def test_complete_salla_product_fallback_matches_executive_amount_gate(db):
    from product_v2_routes import PRODUCTS
    from product_v2_details_routes import COST_PROFILES
    await campaign_setup(db)
    await db[COST_PROFILES].delete_one({"user_id": OWNER, "salla_product_id": "plain"})
    await db[PRODUCTS].update_one({"user_id": OWNER, "salla_product_id": "plain"},
        {"$set": {"cost_price_from_salla": 11.4}})
    await db.unified_orders.insert_one(campaign_order())
    row = await report(db)
    assert row["product_cost_sar"] == 22.8
    assert row["product_cost_complete"] is True
    assert row["total_cost_sar"] == 45.8 and row["net_profit_sar"] == 98.2


@pytest.mark.asyncio
async def test_frozen_bundle_choices_and_ordinary_components_match_executive(db):
    from dashboard_v2_routes import build_mezan_v2_product_cost_cooperative
    await campaign_setup(db)
    order = campaign_order()
    order["products"] = [sale_line("bundle-line", 4)]
    order["raw_by_source"]["salla_direct"]["items"] = [{"id": "bundle-line", "product_id": "shawl", "quantity": 4,
        "type": "product", "options": deepcopy(order["products"][0]["options"])}]
    await freeze(db, order)
    # Catalogue edits after freezing cannot replace the approved selected costs.
    from product_v2_details_routes import COST_PROFILES
    await db[COST_PROFILES].update_many({"user_id": OWNER}, {"$set": {"base_cost": 999}})
    expected = await build_mezan_v2_product_cost_cooperative(ReadOnlyDB(db), OWNER, [order], include_product_rows=False)
    row = await report(db)
    assert row["product_cost_sar"] == expected["total"] == 220
    assert row["net_profit_sar"] == -99


@pytest.mark.asyncio
async def test_unreconciled_refund_never_reduces_costs_against_unchanged_gross_sales(db):
    await campaign_setup(db)
    await freeze(db, campaign_order(actual_partial_refund_amount=30))
    row = await report(db)
    assert row["sales_sar"] == 200
    assert row["total_cost_sar"] is row["net_profit_sar"] is None
    assert "refund_cost_reconciliation_required" in row["cost_reasons"]


@pytest.mark.asyncio
async def test_failed_cost_reader_preserves_independent_commerce_and_spend(db, monkeypatch):
    import order_cost_reporting_repair as repair
    await campaign_setup(db)
    await freeze(db, campaign_order())
    async def unavailable(*args, **kwargs):
        raise RuntimeError("synthetic read failure")
    monkeypatch.setattr(repair, "_catalog_context", unavailable)
    row = await report(db)
    assert row["sales_sar"] == 200 and row["spend_sar"] == 50
    assert row["total_cost_sar"] is row["net_profit_sar"] is None
    assert "cost_read_unavailable" in row["cost_reasons"]


@pytest.mark.asyncio
async def test_profit_read_is_batched_and_never_mutates_order_or_cost_authority(db):
    await campaign_setup(db)
    for index in range(12):
        await freeze(db, campaign_order(str(index)))
    before = deepcopy(await db.unified_orders.find({}).to_list(length=30))
    readonly = ReadOnlyDB(db)
    row = (await hover.read_campaign_hover(readonly, OWNER, ["synthetic-campaign"]))["campaigns"]["synthetic-campaign"]
    assert row["product_cost_sar"] == 216
    # Snapshot authorities load this cohort once, not once for each order.
    assert readonly.reads[("mezan_component_order_lifecycle_v1", "aggregate")] == 1
    assert readonly.reads[("mezan_order_item_cost_snapshots_v2", "find")] == 1
    assert await db.unified_orders.find({}).to_list(length=30) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["quantity", "missing_line", "extra_line", "duplicate_line", "product"])
async def test_stale_promoted_lines_never_price_the_current_raw_salla_sale(db, change):
    await campaign_setup(db)
    order = campaign_order()
    raw = order["raw_by_source"]["salla_direct"]["items"]
    if change == "quantity":
        order["products"][0]["quantity"] = 1
    elif change == "missing_line":
        raw.append({"id": "new-line", "product_id": "plain", "quantity": 1})
    elif change == "extra_line":
        order["products"].append(sale_line("extra-line", 1, product="plain"))
    elif change == "duplicate_line":
        raw.append(deepcopy(raw[0]))
        order["products"].append(deepcopy(order["products"][0]))
    else:
        raw[0]["product_id"] = "changed-product"
    await freeze(db, order)
    row = await report(db)
    assert row["sales_sar"] == 200
    assert row["product_cost_complete"] is False
    assert row["product_cost_sar"] is row["total_cost_sar"] is row["net_profit_sar"] is None
    assert "current_order_lines_unverified" in row["cost_reasons"]


@pytest.mark.asyncio
@pytest.mark.parametrize("variant_source", ["item_nested", "product_nested", "removed", "contradictory"])
async def test_changed_native_variant_cannot_reuse_old_variant_cost(db, variant_source):
    await campaign_setup(db)
    order = campaign_order()
    order["products"][0]["variant_id"] = "old"
    raw = order["raw_by_source"]["salla_direct"]["items"][0]
    if variant_source == "item_nested":
        raw["variant"] = {"id": "new"}
    elif variant_source == "product_nested":
        raw["product"] = {"id": raw["product_id"], "variant": {"id": "new"}}
    elif variant_source == "contradictory":
        raw.update(variant={"id": "old"}, variant_id="new")
    await freeze(db, order)
    row = await report(db)
    assert row["sales_sar"] == 200
    assert row["product_cost_complete"] is False
    assert row["product_cost_sar"] is row["total_cost_sar"] is row["net_profit_sar"] is None
    assert "current_order_lines_unverified" in row["cost_reasons"]


@pytest.mark.parametrize("variant_source", ["item_nested", "product_nested"])
def test_matching_native_variant_is_accepted(variant_source):
    from order_engine.campaign_hover_profit import _current_lines
    order = campaign_order()
    order["products"][0]["variant_id"] = "same"
    raw = order["raw_by_source"]["salla_direct"]["items"][0]
    if variant_source == "item_nested":
        raw["variant"] = {"id": "same"}
    else:
        raw["product"] = {"id": raw["product_id"], "variant": {"id": "same"}}
    assert _current_lines(order) is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("identity_change", ["parent", "parent_removed", "contradictory_parent", "stale_primary_alias"])
async def test_changed_product_parent_cannot_price_a_stale_catalog_identity(db, identity_change):
    await campaign_setup(db)
    order = campaign_order()
    item = order["products"][0]
    raw = order["raw_by_source"]["salla_direct"]["items"][0]
    if identity_change == "stale_primary_alias":
        item["salla_product_id"] = "old-product"
    else:
        item["parent_product_id"] = "plain"
        if identity_change == "parent":
            raw["parent_product_id"] = "new-parent"
        elif identity_change == "contradictory_parent":
            raw.update(parent_product_id="plain", product={"id": "plain", "parent_id": "new-parent"})
    await freeze(db, order)
    row = await report(db)
    assert row["sales_sar"] == 200
    assert row["product_cost_complete"] is False
    assert row["total_cost_sar"] is row["net_profit_sar"] is None
    assert "current_order_lines_unverified" in row["cost_reasons"]


@pytest.mark.asyncio
async def test_changed_raw_customer_choice_cannot_reuse_a_stale_frozen_bundle_cost(db):
    await campaign_setup(db)
    order = campaign_order()
    order["products"] = [sale_line("bundle-line", 2, rose="no")]
    source = sale_line("bundle-line", 2, rose="yes")
    order["raw_by_source"]["salla_direct"]["items"] = [{"id": "bundle-line", "product_id": "shawl", "quantity": 2,
        "options": source["options"]}]
    await freeze(db, order)
    row = await report(db)
    assert row["product_cost_complete"] is False
    assert row["net_profit_sar"] is None
    assert "current_order_choices_unverified" in row["cost_reasons"]


def test_prefixed_meta_account_fee_and_conflicting_aliases():
    from order_engine.campaign_hover_profit import _ad_bank_fee
    record = {"account_id": "111", "provider": "meta", "spend_sar": 100}
    setting = {"provider": "meta_ads", "external_account_id": "act_111",
        "bank_commission_pct": 5, "apply_bank_commission": True}
    assert _ad_bank_fee(record, [setting]) == 5
    assert setting["external_account_id"] == "act_111"
    assert _ad_bank_fee(record, [setting, {**setting, "external_account_id": "111"}]) is None


@pytest.mark.asyncio
async def test_oversized_cost_cohort_is_rejected_before_copying_or_loading_catalog(db, monkeypatch):
    from order_engine import campaign_hover_profit as profit
    order = campaign_order()
    order["products"] = order["products"] * 1001
    def forbidden_copy(*args):
        raise AssertionError("oversized order was copied before admission")
    monkeypatch.setattr(profit, "deepcopy", forbidden_copy)
    with pytest.raises(ValueError, match="campaign_cost_line_limit"):
        await profit._price_orders(ReadOnlyDB(db), OWNER, [order])


def test_cost_authority_can_initialize_before_order_engine_without_import_cycle():
    import subprocess
    import sys
    result = subprocess.run([sys.executable, "-B", "-c",
        "import bundle_catalog_v2; import order_engine; import order_profitability_summary"],
        capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr

