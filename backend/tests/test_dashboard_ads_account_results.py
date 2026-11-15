"""Account outcomes reuse the executive cohort and require unique evidence."""
from copy import deepcopy

import mongomock
import pytest

import dashboard_ads_account_results as results
import dashboard_v2_routes as dashboard
from tests.test_dashboard_card_snapshots import PARAMS, card_app


OWNER = "owner"
DAY = "2026-11-15"


def campaign(account, identity, name=None, **changes):
    return {"user_id": OWNER, "provider": "snapchat_ads", "entity_type": "campaign",
            "ad_account_id": account, "external_id": identity, "name": name or identity, **changes}


def order(identity=None, amount=100, **changes):
    return {"source_native": "snapchat", "campaign_id": identity, "total_amount": amount,
            "currency": "SAR", **changes}


class Cursor:
    def __init__(self, raw, reads):
        self.raw, self.reads = raw, reads

    def limit(self, limit):
        self.reads[-1]["limit"] = limit
        self.raw = self.raw.limit(limit)
        return self

    def max_time_ms(self, timeout):
        self.reads[-1]["max_time_ms"] = timeout
        return self

    async def to_list(self, length):
        self.reads[-1]["length"] = length
        return list(self.raw)[:length]


class Catalog:
    def __init__(self, rows=(), *, error=False):
        self.raw = mongomock.MongoClient().db.catalog
        if rows:
            self.raw.insert_many(deepcopy(list(rows)))
        self.error, self.reads = error, []

    def find(self, query, projection):
        self.reads.append({"query": query, "projection": projection})
        if self.error:
            raise RuntimeError("catalog unavailable")
        return Cursor(self.raw.find(query, projection), self.reads)


class Db:
    def __init__(self, rows=(), **kwargs):
        self.catalog = Catalog(rows, **kwargs)

    def __getitem__(self, name):
        assert name == results.CAMPAIGN_COLLECTION, "no order/provider/cost read is allowed"
        return self.catalog


async def build(db, orders, account_ids=("usd", "sar")):
    return await results.build_dashboard_ads_account_results(
        db, OWNER, orders, snapchat_account_ids=list(account_ids), date_from=DAY, date_to=DAY,
    )


def rows_by_id(snapshot):
    return {row["account_id"]: row for row in snapshot["accounts"]}


@pytest.mark.asyncio
async def test_separates_accounts_with_one_small_owner_scoped_catalog_read():
    db = Db([campaign("usd", "c-usd"), campaign("sar", "c-sar", "Saudi winter")])
    snapshot = await build(db, [
        order("c-usd", 125), order(None, 80, campaign_name="Saudi winter"),
        order("c-usd", 400, source_native="meta"),
        order("c-usd", 999, source_native="store"),
    ])
    rows = rows_by_id(snapshot)
    assert rows["usd"]["salla_orders"] == rows["sar"]["salla_orders"] == 1
    assert rows["usd"]["salla_sales_sar"] == 125
    assert rows["sar"]["salla_sales_sar"] == 80
    assert all(row["attribution_complete"] for row in rows.values())
    assert snapshot["date_from"] == snapshot["date_to"] == DAY
    assert snapshot["timezone"] == "Asia/Riyadh"
    assert snapshot["source_only"] is True
    assert snapshot["coverage"]["unassigned_orders"] == 0
    assert len(db.catalog.reads) == 1
    read = db.catalog.reads[0]
    assert read["query"] == {"user_id": OWNER, "provider": "snapchat_ads", "entity_type": "campaign"}
    assert read["projection"] == {"_id": 0, "ad_account_id": 1, "external_id": 1, "name": 1}
    assert read["limit"] == read["length"] == results.MAX_CAMPAIGN_ROWS + 1
    assert read["max_time_ms"] == results.CATALOG_READ_TIMEOUT_MS


@pytest.mark.asyncio
async def test_unassigned_snapchat_orders_never_repeat_provider_totals_on_accounts():
    db = Db([campaign("usd", "c-usd"), campaign("sar", "c-sar")])
    snapshot = await build(db, [order("c-usd", 120), order("c-sar", 40), order(None, 300)])
    rows = rows_by_id(snapshot)
    assert snapshot["coverage"]["status"] == "partial"
    assert snapshot["coverage"]["unassigned_orders"] == 1
    assert rows["usd"]["known_salla_orders"] == rows["sar"]["known_salla_orders"] == 1
    assert rows["usd"]["known_salla_sales_sar"] == 120
    assert rows["sar"]["known_salla_sales_sar"] == 40
    for row in rows.values():
        assert row["attribution_complete"] is False
        assert row["salla_orders"] is row["salla_sales_sar"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["name", "id", "conflicting_ids", "clickid"])
async def test_ambiguous_or_click_only_evidence_stays_unassigned(kind):
    catalog = [campaign("usd", "c-usd", "Winter"), campaign("sar", "c-sar", "Winter")]
    item = order(None, campaign_name="Winter")
    if kind == "id":
        catalog[1]["external_id"] = "c-usd"
        item = order("c-usd")
    elif kind == "conflicting_ids":
        item = order("c-usd", source_campaign_id="c-sar")
    elif kind == "clickid":
        item = order(None, utm_campaign="Using the clickid sccid provided by Snapchat")
    snapshot = await build(Db(catalog), [item])
    assert snapshot["coverage"]["unassigned_orders"] == 1
    assert all(row["known_salla_orders"] == 0 and row["salla_orders"] is None
               for row in snapshot["accounts"])


@pytest.mark.asyncio
async def test_raw_provider_campaign_correction_wins_over_stale_normalized_identity():
    db = Db([campaign("usd", "old"), campaign("sar", "new")])
    item = order("old", raw_by_source={"salla_direct": {"source": "snapchat", "campaign": {"id": "new"}}})
    rows = rows_by_id(await build(db, [item]))
    assert rows["usd"]["salla_orders"] == 0
    assert rows["sar"]["salla_orders"] == 1


@pytest.mark.asyncio
async def test_catalog_owner_and_provider_boundaries_cannot_prove_an_account():
    db = Db([campaign("usd", "foreign-tenant", user_id="other"),
             campaign("sar", "foreign-provider", provider="meta_ads")])
    snapshot = await build(db, [order("foreign-tenant"), order("foreign-provider")])
    assert snapshot["coverage"]["unassigned_orders"] == 2
    assert all(row["known_salla_orders"] == 0 for row in snapshot["accounts"])


@pytest.mark.asyncio
async def test_known_foreign_account_is_excluded_without_making_selected_totals_unknown():
    db = Db([campaign("usd", "own"), campaign("disabled", "foreign")])
    snapshot = await build(db, [order("own", 60), order("foreign", 900)])
    assert snapshot["coverage"]["outside_account_scope_orders"] == 1
    assert snapshot["coverage"]["unassigned_orders"] == 0
    assert rows_by_id(snapshot)["usd"]["salla_sales_sar"] == 60
    assert rows_by_id(snapshot)["sar"]["salla_orders"] == 0


@pytest.mark.asyncio
async def test_unverified_sales_currency_does_not_hide_the_known_order_count():
    db = Db([campaign("usd", "c-usd"), campaign("sar", "c-sar")])
    snapshot = await build(db, [
        order("c-usd", 120), order("c-usd", 28.84, currency="KWD"),
        order("c-sar", 302.20, currency="QAR", total_amount_sar=311.01,
              accounting_currency="SAR", currency_conversion_status="verified"),
    ])
    rows = rows_by_id(snapshot)
    assert rows["usd"]["salla_orders"] == 2
    assert rows["usd"]["salla_sales_sar"] is None
    assert rows["usd"]["known_salla_sales_sar"] == 120
    assert rows["usd"]["sales_currency_conversion_complete"] is False
    assert rows["sar"]["salla_sales_sar"] == 311.01


@pytest.mark.asyncio
@pytest.mark.parametrize("orders", [[], [order("anything", source_native="meta")]])
async def test_no_snapchat_orders_prove_zero_without_reading_the_catalog(orders):
    db = Db(error=True)
    snapshot = await build(db, orders)
    assert db.catalog.reads == []
    assert snapshot["coverage"]["catalog_complete"] is True
    assert all(row["salla_orders"] == 0 and row["salla_sales_sar"] == 0
               for row in snapshot["accounts"])


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["error", "overflow"])
async def test_unavailable_or_truncated_catalog_never_certifies_a_partial_match(monkeypatch, failure):
    monkeypatch.setattr(results, "MAX_CAMPAIGN_ROWS", 1)
    db = Db([campaign("usd", "c-usd"), campaign("sar", "c-sar")], error=failure == "error")
    snapshot = await build(db, [order("c-usd")])
    assert snapshot["coverage"]["status"] == "unavailable"
    assert snapshot["coverage"]["catalog_complete"] is False
    assert snapshot["coverage"]["unassigned_orders"] == 1
    assert all(row["salla_orders"] is None and row["known_salla_orders"] == 0
               for row in snapshot["accounts"])


@pytest.mark.asyncio
async def test_executive_snapshot_reuses_filtered_orders_and_its_existing_cache(card_app, monkeypatch):
    env = card_app
    original_ads = dashboard.build_mezan_v2_ads

    async def ads(*args, **kwargs):
        result = await original_ads(*args, **kwargs)
        result["providers"]["snapchat"]["account_ids"] = ["usd", "sar"]
        return result

    monkeypatch.setattr(dashboard, "build_mezan_v2_ads", ads)
    await env.db[results.CAMPAIGN_COLLECTION].insert_one(campaign("usd", "c-usd", user_id="a"))
    await env.db.unified_orders.update_many({"user_id": "a"}, {"$set": {
        "source_native": "snapchat", "campaign_id": "c-usd",
    }})
    response = await env.client.get("/dashboard-v2/cards/executive", params=PARAMS)
    assert response.status_code == 200, response.text
    snapshot = response.json()["ads_v2"]["account_results"]
    assert snapshot["date_from"] == snapshot["date_to"] == PARAMS["from_date"]
    assert rows_by_id(snapshot)["usd"]["salla_orders"] == 1
    assert rows_by_id(snapshot)["usd"]["salla_sales_sar"] == 100
    assert rows_by_id(snapshot)["sar"]["salla_orders"] == 0
    assert len(env.reads) == 1
    assert env.reads[0][1]["payment_methods"] == PARAMS["payment_methods"]
    assert env.reads[0][1]["shipping_companies"] == PARAMS["shipping_companies"]
    assert env.reads[0][1]["report_statuses"] == PARAMS["report_statuses"]
    assert env.calls["ads"] == 1
    cached = await env.client.get("/dashboard-v2/cards/executive", params=PARAMS)
    assert cached.json()["ads_v2"]["account_results"] == snapshot
    assert len(env.reads) == 1
    assert env.calls["ads"] == 1
