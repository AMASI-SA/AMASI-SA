"""Synthetic, isolated fixtures for request-scope Dashboard equivalence only.

CASES contains endpoint kwargs and settings overrides. Reset settings to the
seeded snapshot before each case; do not accumulate settings between cases.
Permission cases supply a different user; the harness must use the real guard.
"""
from copy import deepcopy

import auth
import dashboard_v2_routes as dash
from dashboard_v2_ad_costs import COST_SETTINGS_COLLECTION
from test_dashboard_b_baseline_mongo import populate


OWNER = {"id": "fixture-owner", "role": "owner"}
DEFAULT_KWARGS = {
    "from_date": "2026-10-01", "to_date": "2026-10-08",
    "payment_methods": None, "shipping_companies": None,
}


def _case(name, *, settings=None, user=None, **kwargs):
    return {"name": name, "kwargs": {**DEFAULT_KWARGS, **kwargs},
            "settings": settings or {}, "user": user or OWNER}


CASES = [
    _case("current_month_mixed"),
    _case("overlapping_period", from_date="2026-10-02", to_date="2026-10-06"),
    _case("prior_month", from_date="2026-09-01", to_date="2026-09-30"),
    _case("empty_period", from_date="2026-08-01", to_date="2026-08-02"),
    _case("first_day_inclusive", to_date="2026-10-01"),
    _case("last_day_inclusive", from_date="2026-10-08"),
    _case("no_dates", from_date=None, to_date=None),
    _case("from_only", to_date=None),
    _case("to_only", from_date=None),
    _case("hide_inferred", settings={"hide_inferred_date_orders": True}),
    _case("include_inferred", settings={"hide_inferred_date_orders": False}),
    _case("payment_substring_case", payment_methods=" MaDa , APPLE "),
    _case("bnpl_and_cod", payment_methods="tabby,tamara,cash_on_delivery"),
    _case("shipping_substring", shipping_companies=" fixture , SMSA "),
    _case("combined_filters", payment_methods="mada", shipping_companies="SMSA"),
    _case("no_matching_filter", payment_methods="fixture-no-match"),
    _case("all_statuses", settings={"report_included_statuses": []}),
    _case("completed_status", settings={"report_included_statuses": ["completed"]}),
    _case("arabic_status_normalization", settings={"report_included_statuses": ["بانتظار المراجعة"]}),
    _case("returns_only", settings={"report_included_statuses": ["cancelled", "refunded"]}),
    _case("electronic_no_exclusions", settings={"electronic_net_excluded_statuses": []}),
    _case("rounding_and_known_fx", payment_methods="bank_transfer"),
    _case("other_owner", user={"id": "fixture-other", "role": "owner"}),
    _case("permission_denied", user={"id": "fixture-owner", "role": "viewer"}),
]


async def seed_mixed(db):
    """Seed only the explicitly supplied disposable database; no provider I/O."""
    await populate(db, 40)
    await auth.ensure_user_settings(db, "fixture-other")
    await db.settings.update_one({"user_id": OWNER["id"]}, {"$set": {
        "report_included_statuses": [], "hide_inferred_date_orders": False,
    }})
    methods = ["mada", "Apple Pay", "bank_transfer", "tabby", "tamara", "cash_on_delivery"]
    statuses = ["completed", "cancelled", "refunded", "pending", "بإنتظار المُراجعة", "failed"]
    sources = ["snapchat", "facebook", "tiktok", "google_ads", "store"]
    for i in range(40):
        await db.unified_orders.update_one(
            {"user_id": OWNER["id"], "order_number": str(1000000 + i)},
            {"$set": {"payment_method": methods[i % len(methods)],
                      "order_status": statuses[i % len(statuses)],
                      "shipping_company": "SMSA Express" if i % 3 == 0 else "Fixture carrier",
                      "raw_by_source.salla_direct.source": sources[i % len(sources)],
                      "data_source": "make" if i % 2 else "excel"}})

    async def order(index, fields, unset=()):
        update = {"$set": fields}
        if unset:
            update["$unset"] = {key: "" for key in unset}
        await db.unified_orders.update_one(
            {"user_id": OWNER["id"], "order_number": str(1000000 + index)}, update)

    for index, day in [(0, "2026-09-30"), (1, "2026-10-01"),
                       (2, "2026-10-08"), (3, "2026-10-09")]:
        await order(index, {"order_date": day})
    await order(4, {"order_date_inferred": True})
    await order(5, {"actual_partial_refund_amount": 33.33, "order_status": "completed"})
    await order(6, {"actual_refund_amount": 100, "order_status": "completed"})
    await order(7, {"order_status": "special-confirmed"})
    await db.order_status_policy.insert_one({"user_id": OWNER["id"],
        "status": "special-confirmed", "category": "confirmed"})
    await order(8, {"currency": "AED", "total_amount": 558.01,
        "total_amount_sar": 570.01, "exchange_rate_to_sar": "1.02150472",
        "accounting_currency": "SAR", "currency_conversion_status": "verified",
        "payment_method": "bank_transfer"})
    await order(9, {"currency": "QAR", "total_amount": 302.20,
        "raw_by_source.salla_direct": {"source": "snapchat", "currency": "QAR",
            "amounts": {"total": {"amount": "302.20", "currency": "QAR"}},
            "exchange_rate": {"base_currency": "SAR", "exchange_currency": "QAR", "rate": "1.02914116"}}},
        unset=("total_amount_sar",))
    await order(10, {"currency": "KWD", "total_amount": 28.84,
        "raw_by_source.salla_direct": {"source": "facebook", "currency": "KWD"}},
        unset=("total_amount_sar",))
    await order(11, {"total_amount": 100.005, "total_amount_sar": 100.005,
        "payment_method": "bank_transfer", "products": [
            {"product_id": "11", "name": "Fractional", "quantity": 3, "total": 100.005, "price": 33.335}]})
    await order(12, {"products": []})
    await order(13, {"products": [None, "malformed", {"product_id": "13", "quantity": 1, "price": 100}]})
    await order(14, {"payment_method": "", "shipping_company": "", "order_status": ""})

    await db[dash.COST_PROFILES].update_one({"salla_product_id": "15"}, {"$set": {"base_cost": 0}})
    await db[dash.COST_PROFILES].update_one({"salla_product_id": "16"},
        {"$set": {"base_cost": 10, "variant_costs": {"v16": 17}}})
    await db[dash.PRODUCTS].update_one({"salla_product_id": "16"},
        {"$set": {"variants": [{"id": "v16", "sku": "SKU16", "cost_price_from_salla": 35}]}})
    await order(16, {"products": [{"product_id": "16", "variant_id": "v16", "sku": "SKU16", "quantity": 2, "price": 50}]})
    await db[dash.COST_PROFILES].delete_many({"salla_product_id": {"$in": ["17", "18", "19"]}})
    await db[dash.PRODUCTS].update_one({"salla_product_id": "17"},
        {"$unset": {"cost_price": ""}, "$set": {"raw_salla_details": {"cost_price": {"amount": "21.55"}}}})
    await db[dash.PRODUCTS].update_one({"salla_product_id": "18"}, {"$unset": {"cost_price": ""}})
    await db[dash.PRODUCTS].update_one({"salla_product_id": "19"},
        {"$set": {"variants": [{"id": "v19", "cost_price": {"amount": "41.50"}}]}})
    await order(19, {"products": [{"product_id": "19", "variant_id": "v19", "quantity": 1, "price": 100}]})
    await order(20, {"products": [{"product_id": "historical", "name": "Fixture product 20", "quantity": 1, "price": 100}]})
    await db[dash.PRODUCTS].update_many({"salla_product_id": {"$in": ["21", "22"]}},
        {"$set": {"name": "Duplicate historical", "sku": "SAME-SKU"}})
    await order(21, {"products": [{"product_id": "historical", "name": "Duplicate historical", "quantity": 1, "price": 100}]})
    await order(23, {"products": [{"product_id": "23", "quantity": 2, "price": 50,
        "options": [{"name": "التغليف", "value": "فاخر", "price": 2.25}]}]})
    await db[dash.RESOURCES].insert_one({"user_id": OWNER["id"], "id": "r23", "unit_cost": 3.335})
    binding = {"user_id": OWNER["id"], "salla_product_id": "23", "id": "b23", "resource_id": "r23", "quantity": 2}
    await db[dash.PRODUCT_RESOURCE_BINDINGS].insert_many([deepcopy(binding), deepcopy(binding)])
    await db[dash.BINDINGS].insert_one({"user_id": OWNER["id"], "salla_product_id": "23",
        "id": "o23", "option_name": "التغليف", "value_name": "فاخر", "mode": "direct", "direct_amount": 4.25})

    for provider, collection in [("meta", dash.META_FACTS), ("tiktok", dash.TIKTOK_FACTS)]:
        provider_id = dash.PROVIDER_IDS[provider]
        for selected in (True, False):
            account = provider + ("-selected" if selected else "-unselected")
            await db.mezan_integration_accounts_v2.insert_one({"user_id": OWNER["id"],
                "provider": provider_id, "external_account_id": account, "ad_account_id": account,
                "mezan_integration_account_id": "m-" + account, "currency": "USD",
                "connection_status": "connected", "connection_provenance": "api_connection",
                "mezan_selected": selected, "display_name": account})
            await db[COST_SETTINGS_COLLECTION].insert_one({"user_id": OWNER["id"],
                "provider": provider_id, "external_account_id": account,
                "mezan_integration_account_id": "m-" + account, "native_currency": "USD",
                "exchange_rate_to_sar": 3.7544, "bank_commission_pct": 2.3, "apply_bank_commission": True})
            await db[collection].insert_many([{"user_id": OWNER["id"], "provider": provider_id,
                "ad_account_id": account, "date": day, "spend_native": 10.015,
                "spend_sar": 37.5, "currency": "USD", "purchases": 3, "purchase_value_sar": 450,
                "impressions": 123, "clicks": 7} for day in ("2026-09-30", "2026-10-01", "2026-10-08")])
    await db.daily_costs.insert_many([{"user_id": OWNER["id"], "date": day, "google_ads": 11.115}
                                     for day in ("2026-09-30", "2026-10-01", "2026-10-08")])
    other = await db.unified_orders.find_one({"user_id": OWNER["id"], "order_number": "1000000"}, {"_id": 0})
    other.update(user_id="fixture-other", order_date="2026-10-05", total_amount=999, total_amount_sar=999)
    await db.unified_orders.insert_one(other)
    await db[dash.PRODUCTS].insert_one({"user_id": "fixture-other", "salla_product_id": "0", "name": "Other owner", "cost_price": 999})
    await db[dash.COST_PROFILES].insert_one({"user_id": "fixture-other", "salla_product_id": "0", "base_cost": 999})
    return await db.settings.find_one({"user_id": OWNER["id"]}, {"_id": 0})
