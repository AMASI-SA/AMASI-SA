"""Campaign hover acceptance with query-aware Mongo doubles (never live data)."""
from copy import deepcopy
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException
from order_engine import campaign_hover as hover

NOW = datetime(2026, 11, 13, 12, 30, tzinfo=timezone.utc)


class Clock(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW.astimezone(tz) if tz else NOW.replace(tzinfo=None)


@pytest.fixture(autouse=True)
def fixed_clock(monkeypatch):
    monkeypatch.setattr(hover, "datetime", Clock)
    hover._CACHE.clear()
    hover._PENDING.clear()


def nested(row, path):
    value = row
    for key in path.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def matches(row, query):
    for key, expected in query.items():
        if key == "$or":
            if not any(matches(row, item) for item in expected):
                return False
            continue
        value = nested(row, key)
        if not isinstance(expected, dict):
            if value != expected:
                return False
            continue
        for op, rhs in expected.items():
            if op == "$in" and value not in rhs:
                return False
            if op == "$ne" and value == rhs:
                return False
            if op == "$gte" and (value is None or value < rhs):
                return False
            if op == "$lt" and (value is None or value >= rhs):
                return False
            if op == "$type" and rhs == "object" and not isinstance(value, dict):
                return False
            assert op in {"$in", "$ne", "$gte", "$lt", "$type"}, op
    return True


class Cursor:
    def __init__(self, rows):
        self.rows = deepcopy(rows)
    def max_time_ms(self, value):
        assert value <= 1500
        return self
    def limit(self, value):
        self.rows = self.rows[:value]
        return self
    async def to_list(self, value):
        return self.rows[:value]


class Collection:
    def __init__(self, rows):
        self.rows = deepcopy(rows)
        self.queries = []
        self.error = None
    def find(self, query, projection):
        self.queries.append(deepcopy(query))
        if self.error:
            raise self.error
        return Cursor([row for row in self.rows if matches(row, query)])


class DB:
    def __init__(self, mapping):
        self.collections = {name: Collection(rows) for name, rows in mapping.items()}
    def __getitem__(self, key):
        return self.collections.setdefault(key, Collection([]))


def order(cid="ny-campaign", number="order-1", at="2026-11-13T06:00:00+00:00", user="owner"):
    return {"user_id": user, "order_number": number, "order_date": at, "order_status": "processing",
            "raw_by_source": {"salla_direct": {
                "date": {"date": at}, "utm_source": "snapchat", "utm_campaign": cid,
                "amounts": {"total": {"amount": 375, "currency": "SAR"}},
                "items": [{"id": "p", "type": "product", "quantity": 3},
                          {"id": "p2", "type": "product", "quantity": 1},
                          {"id": "s", "type": "service", "quantity": 1}],
            }}}


def database(orders=None):
    return DB({
        "mezan_integration_accounts_v2": [
            {"user_id": "owner", "provider": "snapchat_ads", "ad_account_id": "snap-ny",
             "timezone": "America/New_York", "currency": "USD"},
            {"user_id": "owner", "provider": "snapchat_ads", "ad_account_id": "snap-sa",
             "timezone": "Asia/Riyadh", "currency": "SAR"}],
        "mezan_snapchat_entities_v2": [
            {"user_id": "owner", "entity_type": "campaign", "external_id": "ny-campaign", "ad_account_id": "snap-ny", "name": "winter"},
            {"user_id": "owner", "entity_type": "campaign", "external_id": "sa-campaign", "ad_account_id": "snap-sa", "name": "winter"}],
        "unified_orders": [order()] if orders is None else orders,
    })


def local_spend(**changes):
    return {"user_id": "owner", "ad_account_id": "snap-ny", "campaign_id": "ny-campaign",
            "date": "2026-11-13", "account_timezone": "America/New_York",
            "source_mode": "snapchat_native_account_day_v2", "spend_sar": 93.75,
            "coverage_status": "provisional_hourly", "coverage_complete": True,
            "coverage_start_utc": "2026-11-13T05:00:00+00:00", "coverage_end_utc": "2026-11-13T12:00:00+00:00",
            "updated_at": "2026-11-13T12:05:00+00:00", "hours_observed": 7, **changes}


async def read(db, cid="ny-campaign"):
    return (await hover.read_campaign_hover(db, "owner", [cid]))["campaigns"][cid]


def test_ny_and_riyadh_midnight_and_dst():
    at = datetime(2026, 11, 13, 1, tzinfo=timezone.utc)
    ny_start, ny_day = hover._window(at, "America/New_York")
    sa_start, sa_day = hover._window(at, "Asia/Riyadh")
    assert ny_day == "2026-11-12" and ny_start.isoformat() == "2026-11-12T05:00:00+00:00"
    assert sa_day == "2026-11-13" and sa_start.isoformat() == "2026-11-12T21:00:00+00:00"
    summer, _ = hover._window(datetime(2026, 7, 15, 16, tzinfo=timezone.utc), "America/New_York")
    assert summer.isoformat() == "2026-07-15T04:00:00+00:00"


def test_unknown_timezone_quantity_and_amount_are_not_zero():
    assert hover._window(NOW, "Fake/Zone") is None
    assert hover._quantity({}) is None
    assert hover._quantity({"items": [{"quantity": 3}, {"quantity": 2, "type": "service"}]}) == 3
    assert hover._amount(None) is None
    assert hover._amount("NaN") is None
    assert hover._amount(float("inf")) is None
    assert hover._amount(0) == 0
    assert hover._timestamp("2026-11-13T00:00:00") is None


@pytest.mark.asyncio
async def test_two_snap_accounts_catalog_resolves_salla_metrics_without_any_spend():
    db = database()
    row = await read(db)
    assert row["timezone"] == "America/New_York"
    assert row["orders"] == 1 and row["pieces"] == 4 and row["sales_sar"] == 375
    assert row["spend_sar"] is row["cpa_sar"] is row["roas"] is None
    assert row["status"] == "partial"
    query = db["unified_orders"].queries[0]
    assert {"raw_by_source.salla_direct.utm_campaign": {"$in": ["ny-campaign"]}} in query["$or"]


@pytest.mark.asyncio
async def test_ny_account_spend_preferred_and_riyadh_report_not_relabelled():
    db = database()
    db["mezan_snapchat_campaign_account_day_v2"].rows = [local_spend()]
    db["mezan_snapchat_performance_daily_v2"].rows = [{
        "user_id": "owner", "ad_account_id": "snap-ny", "entity_type": "campaign",
        "campaign_id": "ny-campaign", "date": "2026-11-13", "date_timezone": "Asia/Riyadh", "spend_sar": 300}]
    row = await read(db)
    assert row["spend_sar"] == row["cpa_sar"] == 93.75
    assert row["roas"] == 4 and row["orders"] == 1
    assert row["spend_coverage"] == "provisional_hourly"
    assert row["spend_warning"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("changes", [
    {"coverage_complete": False}, {"coverage_status": "incomplete_hourly"}, {"missing_hours": 2},
    {"coverage_complete": False, "coverage_status": "incomplete_hourly", "missing_hours": 1,
     "spend_evidence_valid": True, "hours_observed": 6},
])
async def test_missing_hours_do_not_block_available_spend_cpa_or_roas(changes):
    db = database()
    db["mezan_snapchat_campaign_account_day_v2"].rows = [local_spend(**changes)]
    row = await read(db)
    assert row["orders"] == 1 and row["pieces"] == 4 and row["sales_sar"] == 375
    assert row["spend_sar"] == row["cpa_sar"] == 93.75
    assert row["roas"] == 4 and row["status"] == "available"
    assert row["spend_reason"] is None
    assert row["spend_warning"] == "snapchat_hours_incomplete"


@pytest.mark.asyncio
@pytest.mark.parametrize("changes", [
    {"coverage_start_utc": "2026-11-13T06:00:00+00:00"},
    {"coverage_end_utc": "2026-11-14T00:00:00+00:00"},
    {"coverage_status": "invalid_hourly"}, {"spend_evidence_valid": False}, {"hours_observed": 0},
])
async def test_invalid_hour_evidence_or_wrong_window_still_cannot_enter_metrics(changes):
    db = database()
    db["mezan_snapchat_campaign_account_day_v2"].rows = [local_spend(**changes)]
    row = await read(db)
    assert row["orders"] == 1 and row["sales_sar"] == 375
    assert row["spend_sar"] is row["cpa_sar"] is row["roas"] is None
    assert row["spend_reason"] == "snapchat_hour_evidence_invalid"


@pytest.mark.asyncio
async def test_legacy_unknown_partial_amount_is_not_promoted_from_unaudited_known_total():
    db = database()
    db["mezan_snapchat_campaign_account_day_v2"].rows = [local_spend(
        coverage_complete=False, coverage_status="incomplete_hourly", spend_sar=None,
        spend_native=None, known_spend_native=25, missing_hours=1)]
    row = await read(db)
    assert row["orders"] == 1
    assert row["spend_sar"] is row["cpa_sar"] is row["roas"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("rate,expected", [(None, None), (3.75, 93.75)])
async def test_partial_native_spend_still_requires_saved_currency_rate(rate, expected):
    db = database()
    db["mezan_snapchat_campaign_account_day_v2"].rows = [local_spend(
        coverage_complete=False, coverage_status="incomplete_hourly", spend_evidence_valid=True,
        spend_sar=None, spend_native=25, currency_native="USD", missing_hours=1)]
    if rate is not None:
        db["ads_v2_spend_daily"].rows = [{"user_id": "owner", "date": "2026-11-13",
            "account_id": "snap-ny", "currency_native": "USD", "fx_rate_to_sar": rate}]
    row = await read(db)
    assert row["spend_sar"] == expected
    assert row["cpa_sar"] == expected
    assert row["roas"] == (4 if rate else None)
    assert row["spend_warning"] == "snapchat_hours_incomplete"


@pytest.mark.asyncio
async def test_wrong_snapshot_timezone_does_not_hide_salla_or_enter_spend():
    db = database()
    db["mezan_snapchat_campaign_account_day_v2"].rows = [local_spend(account_timezone="Asia/Riyadh")]
    row = await read(db)
    assert row["orders"] == 1 and row["spend_sar"] is None


@pytest.mark.asyncio
async def test_midnight_ranges_cancellation_and_distinct_orders():
    rows = [order(at="2026-11-13T04:59:59+00:00", number="before"), order(), order(),
            order(number="future", at="2026-11-13T13:00:00+00:00"), order(number="cancelled")]
    rows[-1]["order_status"] = "cancelled"
    row = await read(database(rows))
    assert row["orders"] == 1 and row["pieces"] == 4


@pytest.mark.asyncio
async def test_riyadh_campaign_includes_hour_before_ny_midnight():
    db = database([order("sa-campaign", at="2026-11-13T02:00:00+00:00")])
    row = await read(db, "sa-campaign")
    assert row["timezone"] == "Asia/Riyadh" and row["orders"] == 1


@pytest.mark.asyncio
async def test_same_name_is_not_campaign_identity_and_other_tenant_is_excluded():
    db = database([order(user="someone-else"), order(cid="other-campaign")])
    row = await read(db)
    assert row["orders"] == 0 and row["sales_sar"] == 0


@pytest.mark.asyncio
async def test_ambiguous_campaign_account_never_guessed():
    db = database()
    db["mezan_snapchat_entities_v2"].rows.append({"user_id": "owner", "entity_type": "campaign",
        "external_id": "ny-campaign", "ad_account_id": "snap-sa"})
    row = await read(db)
    assert row["status"] == "ambiguous_account"
    assert row["orders"] is None


@pytest.mark.asyncio
async def test_connected_timezone_replaces_outdated_legacy_metadata():
    db = database()
    db["ads_v2_accounts"].rows = [{"user_id": "owner", "id": "internal-ny", "provider": "snapchat",
        "external_account_id": "snap-ny", "timezone": "Asia/Riyadh", "currency_native": "USD"}]
    row = await read(db)
    assert row["timezone"] == "America/New_York"


@pytest.mark.asyncio
async def test_failed_salla_read_is_unknown_not_zero_and_spend_read_failure_is_independent():
    db = database()
    db["unified_orders"].error = TimeoutError()
    assert (await read(db))["orders"] is None
    db = database()
    db["mezan_snapchat_campaign_account_day_v2"].error = TimeoutError()
    assert (await read(db))["orders"] == 1


@pytest.mark.asyncio
async def test_missing_currency_rate_does_not_relabel_foreign_sales():
    payload = order()
    payload["raw_by_source"]["salla_direct"]["amounts"]["total"]["currency"] = "QAR"
    row = await read(database([payload]))
    assert row["orders"] == 1 and row["pieces"] == 4 and row["sales_sar"] is None


@pytest.mark.asyncio
async def test_zero_spend_and_zero_orders_keep_division_undefined():
    db = database([])
    db["mezan_snapchat_campaign_account_day_v2"].rows = [local_spend(spend_sar=0)]
    row = await read(db)
    assert row["orders"] == row["spend_sar"] == 0
    assert row["cpa_sar"] is row["roas"] is None


@pytest.mark.asyncio
async def test_partial_explicit_zero_is_usable_and_division_by_zero_stays_undefined():
    db = database()
    db["mezan_snapchat_campaign_account_day_v2"].rows = [local_spend(
        spend_sar=0, coverage_complete=False, coverage_status="incomplete_hourly", missing_hours=1)]
    row = await read(db)
    assert row["orders"] == 1 and row["spend_sar"] == row["cpa_sar"] == 0
    assert row["roas"] is None
    assert row["spend_warning"] == "snapchat_hours_incomplete"


@pytest.mark.asyncio
async def test_batch_query_count_does_not_grow_per_campaign():
    from collections import Counter
    from mongomock_motor import AsyncMongoMockClient
    from .test_dashboard_bundle_cost_authority import ReadOnlyDB

    async def measured_batch(campaign_count):
        # Exercise successful financial reads: the lightweight commerce double
        # above lacks aggregate/find_one and would stop those paths early.
        db = AsyncMongoMockClient().campaign_hover_query_budget
        campaign_ids = [f"batch-campaign-{index}" for index in range(campaign_count)]
        orders = []
        for index, cid in enumerate(campaign_ids):
            row = order(cid=cid, number=f"batch-order-{index}")
            row.update(order_date="2026-11-13", total_amount=375, currency="SAR",
                shipping_company="Carrier", payment_fee_status="actual", actual_payment_fee=2,
                products=[{"order_item_id": f"line-{index}", "product_id": f"product-{index}",
                    "quantity": 1, "sku": f"SKU-{index}"}])
            row["raw_by_source"]["salla_direct"].update(reference_id=row["order_number"],
                items=[{"id": f"line-{index}", "product_id": f"product-{index}",
                    "quantity": 1, "type": "product"}])
            orders.append(row)
        sources = {name: collection.rows for name, collection in database(orders).collections.items()}
        sources.update({
            "mezan_snapchat_entities_v2": [{"user_id": "owner", "entity_type": "campaign",
                "external_id": cid, "ad_account_id": "snap-ny"} for cid in campaign_ids],
            "mezan_snapchat_campaign_account_day_v2": [local_spend(campaign_id=cid, spend_sar=10)
                for cid in campaign_ids],
            "mezan_products_v2": [{"user_id": "owner", "salla_product_id": f"product-{index}",
                "sku": f"SKU-{index}"} for index in range(campaign_count)],
            "mezan_product_cost_profiles_v2": [{"user_id": "owner",
                "salla_product_id": f"product-{index}", "base_cost": 10} for index in range(campaign_count)],
            "settings": [{"user_id": "owner", "shipping_companies": [{"name": "Carrier", "cost_per_order": 5}]}],
            "mezan_employees_v2": [{"user_id": "owner", "id": "employee", "status": "active",
                "hire_date": "2026-11-01"}],
            "mezan_employee_salary_contracts_v2": [{"user_id": "owner", "id": "salary",
                "employee_id": "employee", "monthly_amount": 3000, "effective_from": "2026-11-01"}],
            "operating_recurring_obligations_v2": [{"user_id": "owner", "id": "water", "status": "active",
                "expense_type": "water", "cycle": "monthly", "start_date": "2026-11-01", "auto_renew": True}],
            "operating_recurring_invoices_v2": [{"user_id": "owner", "obligation_id": "water",
                "amount": 600, "period_start": "2026-11-01", "period_end": "2026-11-30"}],
        })
        for name, rows in sources.items():
            if rows:
                await db[name].insert_many(deepcopy(rows))
        readonly = ReadOnlyDB(db)
        result = await hover.read_campaign_hover(readonly, "owner", campaign_ids)
        assert set(result["campaigns"]) == set(campaign_ids)
        for row in result["campaigns"].values():
            assert row["orders"] == 1 and row["net_profit_available"] is True
            assert row["product_cost_sar"] == 10 and row["total_cost_sar"] == 17.23
            assert row["operating_basis"]["expenses_sar"] == 120
        return readonly.reads

    single = await measured_batch(1)
    maximum = await measured_batch(hover.MAX_CAMPAIGNS)
    assert hover.MAX_CAMPAIGNS == 20
    assert single == maximum
    # Exact successful-path budget for one authority chunk (1..20 orders):
    # 10 account/commerce/spend + 12 catalog/authority/merchant + 1 ad settings
    # + 7 executive operating reads = 30. No new read may scale by campaign.
    # Commerce and executive sales intentionally read unified_orders twice;
    # settings has one merchant find_one and one executive find projection.
    find_once = {
        "ads_v2_accounts", "mezan_integration_accounts_v2", "mezan_snapchat_entities_v2",
        "ads_v2_spend_raw", "mezan_snapchat_performance_daily_v2",
        "mezan_meta_campaign_performance_daily_v2", "mezan_snapchat_campaign_account_day_v2",
        "mezan_snapchat_performance_account_day_v3",
        "ads_v2_spend_daily", "mezan_products_v2", "mezan_product_cost_profiles_v2",
        "mezan_product_resource_bindings_v2", "mezan_product_option_cost_bindings_v2",
        "mezan_cost_resources_v2", "mezan_bundle_definitions_v2", "mezan_order_item_cost_snapshots_v2",
        "mezan_component_consumption_units_v1", "mezan_order_cost_corrections_v1",
        "mezan_ad_account_cost_settings_v2", "mezan_employee_salary_contracts_v2",
        "mezan_employees_v2", "operating_salaries", "operating_recurring_obligations_v2",
        "operating_recurring_invoices_v2",
    }
    expected = Counter({(name, "find"): 1 for name in find_once})
    expected.update({("unified_orders", "find"): 2, ("settings", "find"): 1,
        ("settings", "find_one"): 1, ("mezan_component_order_lifecycle_v1", "aggregate"): 1,
        ("mezan_component_consumption_plans_v1", "aggregate"): 1})
    assert maximum == expected
    assert sum(maximum.values()) == sum(expected.values()) == 30


def test_owner_only_and_daily_cache_invalidation():
    with pytest.raises(HTTPException):
        hover._valid_user({"role": "employee", "id": "x"})
    assert hover._valid_user({"role": "OWNER", "id": "owner"}) == "owner"
    assert not hover._local_days_still_current({"campaigns": {"a": {"date": "2026-11-12", "timezone": "Asia/Riyadh"}}})
