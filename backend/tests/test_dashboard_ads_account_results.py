"""Account outcomes reuse the executive cohort and require unique evidence."""
from copy import deepcopy
from datetime import datetime, timezone

import mongomock
import pytest
from mongomock_motor import AsyncMongoMockClient

import dashboard_ads_account_results as results
import dashboard_v2_routes as dashboard
from dashboard_ads_costs import DashboardAdsCostCollector
from tests.test_dashboard_card_snapshots import PARAMS, card_app
from tests.test_dashboard_bundle_cost_authority import ReadOnlyDB


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
    assert snapshot["direct_result"]["provider"] == "direct"
    assert snapshot["direct_result"]["ad_spend_applicable"] is False
    assert snapshot["direct_result"]["salla_orders"] == 1
    assert snapshot["direct_result"]["salla_sales_sar"] == 999
    assert snapshot["daily_total"]["cohort_basis"] == "executive_filtered_orders"
    assert snapshot["daily_total"]["salla_orders"] == 4
    assert snapshot["daily_total"]["salla_sales_sar"] == 1604
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
async def test_unassigned_row_reconciles_four_orders_without_reallocating_them():
    db = Db([campaign("usd", "c-usd"), campaign("sar", "c-sar")])
    orders = [order("c-usd", 100), order("c-sar", 200),
              order(None, 50, source_native="store"), order(None, 70, source_native="meta")]
    orders += [order(None, value) for value in (400, 500, 326, 400.01)]
    before = deepcopy(orders)
    snapshot = await build(db, orders)
    unassigned = snapshot["unassigned_result"]
    assert unassigned["provider"] == "snapchat"
    assert unassigned["cohort_basis"] == "snapchat_unassigned_orders"
    assert unassigned["ad_spend_allocation_complete"] is False
    assert unassigned["attribution_complete"] is False
    assert "account_id" not in unassigned
    assert unassigned["salla_orders"] is unassigned["salla_sales_sar"] is None
    assert unassigned["known_salla_orders"] == snapshot["coverage"]["unassigned_orders"] == 4
    assert unassigned["known_salla_sales_sar"] == 1626.01
    assert unassigned["sales_currency_conversion_complete"] is True
    represented = snapshot["accounts"] + list(snapshot["provider_results"].values()) + [snapshot["direct_result"], unassigned]
    assert sum(row["known_salla_orders"] for row in represented) == snapshot["daily_total"]["salla_orders"] == 8
    assert round(sum(row["known_salla_sales_sar"] for row in represented), 2) == snapshot["daily_total"]["salla_sales_sar"] == 2046.01
    assert [row["known_salla_orders"] for row in snapshot["accounts"]] == [1, 1]
    assert orders == before
    assert len(db.catalog.reads) == 1


@pytest.mark.asyncio
async def test_unassigned_row_excludes_known_outside_accounts_and_direct_orders():
    db = Db([campaign("usd", "c-usd"), campaign("outside", "c-outside")])
    snapshot = await build(db, [order("c-usd", 100), order(None, 300), order("c-outside", 400),
                                order(None, 50, source_native="store"), order(None, 25, source_native="google")])
    unassigned = snapshot["unassigned_result"]
    assert (unassigned["known_salla_orders"], unassigned["known_salla_sales_sar"]) == (1, 300)
    assert snapshot["coverage"]["outside_account_scope_orders"] == 1
    assert snapshot["coverage"]["unrepresented_orders"] == 2
    assert snapshot["direct_result"]["salla_sales_sar"] == 50
    assert snapshot["provider_results"]["google"]["salla_sales_sar"] == 25
    assert (snapshot["daily_total"]["salla_orders"], snapshot["daily_total"]["salla_sales_sar"]) == (5, 875)


@pytest.mark.asyncio
async def test_unassigned_row_keeps_unverified_currency_out_of_known_sar_sales():
    snapshot = await build(Db([campaign("usd", "c-usd")]), [
        order(None, 100), order(None, 28.84, currency="KWD"),
        order(None, 302.20, currency="QAR", total_amount_sar=311.01,
              accounting_currency="SAR", currency_conversion_status="verified"),
    ])
    unassigned = snapshot["unassigned_result"]
    assert unassigned["known_salla_orders"] == 3
    assert unassigned["known_salla_sales_sar"] == 411.01
    assert unassigned["sales_currency_conversion_complete"] is False
    assert unassigned["salla_sales_sar"] is None


@pytest.mark.asyncio
async def test_unassigned_row_reuses_collected_costs_without_claiming_allocated_spend():
    orders = [order(None, value, payment_method="mada", shipping_company="carrier",
                    products=[{"quantity": 1}]) for value in (100, 200)]
    collector = DashboardAdsCostCollector()
    collector.capture_settings({
        "payment_methods": [{"name": "mada", "commission_percent": 1, "fixed_fee": 1, "vat_percent": 15}],
        "shipping_companies": [{"name": "carrier", "cost_per_order": 10, "vat_percent": 0}],
    })
    for item, cost in zip(orders, (10, 20)):
        collector.begin_order(item)
        collector.observe_line(item, item["products"][0], {"base_complete": True, "quantity": 1})
        collector.finish_order(item, cost)
    before = deepcopy(collector.product_orders)
    db = Db([campaign("usd", "c-usd")])
    snapshot = await results.build_dashboard_ads_account_results(
        db, OWNER, orders, snapchat_account_ids=["usd", "sar"], date_from=DAY, date_to=DAY,
        cost_collector=collector,
    )
    summary = snapshot["unassigned_result"]["hover_summary"]
    assert summary["known_pieces"] == 2
    assert summary["known_product_cost_sar"] == 30
    assert summary["known_shipping_cost_sar"] == 20
    assert summary["known_payment_fees_sar"] == 5.75
    assert summary["complete"] is False
    assert summary["total_cost_sar"] is None
    assert "account_attribution_incomplete" in summary["reason_codes"]
    assert snapshot["daily_total"]["hover_summary"]["total_cost_sar"] == 55.75
    assert not any("spend" in key for key in snapshot["unassigned_result"] if key != "ad_spend_allocation_complete")
    assert collector.product_orders == before
    assert len(db.catalog.reads) == 1


@pytest.mark.asyncio
async def test_unassigned_half_days_use_riyadh_source_times_without_duplication():
    snapshot = await results.build_dashboard_ads_account_results(
        Db([campaign("usd", "c-usd")]), OWNER,
        [order(None, 400, order_date_raw=f"{DAY}T08:59:59+00:00"),
         order(None, 500, order_date_raw=f"{DAY}T09:00:00+00:00")],
        snapchat_account_ids=["usd", "sar"], date_from=DAY, date_to=DAY,
        now=datetime(2026, 11, 16, 12, tzinfo=timezone.utc),
    )
    assert snapshot["unassigned_result"]["known_salla_orders"] == 2
    assert snapshot["unassigned_result"]["known_salla_sales_sar"] == 900
    segments = snapshot["segments"]
    assert [segment["coverage"]["unassigned_orders"] for segment in segments] == [1, 1]
    assert [segment["unassigned_result"]["known_salla_orders"] for segment in segments] == [1, 1]
    assert [segment["unassigned_result"]["known_salla_sales_sar"] for segment in segments] == [400, 500]
    assert all(segment["unassigned_result"]["time_assignment_complete"] for segment in segments)
    assert all(segment["unassigned_result"]["attribution_complete"] is False for segment in segments)


@pytest.mark.asyncio
async def test_unknown_order_time_stays_in_day_but_cannot_complete_either_half_day():
    snapshot = await results.build_dashboard_ads_account_results(
        Db([campaign("usd", "c-usd")]), OWNER,
        [order(None, 100, order_date_raw=f"{DAY}T10:00:00+03:00"), order(None, 200, order_date=DAY)],
        snapchat_account_ids=["usd", "sar"], date_from=DAY, date_to=DAY,
        now=datetime(2026, 11, 16, 12, tzinfo=timezone.utc),
    )
    assert snapshot["unassigned_result"]["known_salla_orders"] == 2
    assert snapshot["unassigned_result"]["known_salla_sales_sar"] == 300
    assert snapshot["unassigned_result"]["time_assignment_complete"] is True
    assert [segment["unassigned_result"]["known_salla_orders"] for segment in snapshot["segments"]] == [1, 0]
    for segment in snapshot["segments"]:
        unassigned = segment["unassigned_result"]
        assert unassigned["time_assignment_complete"] is False
        assert "order_time_unverified" in unassigned["hover_summary"]["reason_codes"]
        assert unassigned["salla_orders"] is unassigned["salla_sales_sar"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("future", [False, True])
async def test_unassigned_future_or_multiday_segments_do_not_claim_observed_zero(future):
    snapshot = await results.build_dashboard_ads_account_results(
        Db([campaign("usd", "c-usd")]), OWNER, [order(None, 100, order_date_raw=f"{DAY}T10:00:00+03:00")],
        snapchat_account_ids=["usd", "sar"], date_from=DAY, date_to=DAY if future else "2026-11-16",
        now=datetime(2026, 11, 15, 8, tzinfo=timezone.utc),
    )
    segments = snapshot["segments"][1:] if future else snapshot["segments"]
    assert all(segment["status"] == ("not_started" if future else "unavailable") for segment in segments)
    for segment in segments:
        unassigned = segment["unassigned_result"]
        assert unassigned["known_salla_orders"] == 0
        assert unassigned["salla_orders"] is unassigned["salla_sales_sar"] is None
        assert unassigned["hover_summary"]["complete"] is False


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
    assert snapshot["coverage"]["unrepresented_orders"] == 1
    assert snapshot["direct_result"]["salla_orders"] == 0
    assert snapshot["daily_total"]["salla_orders"] == 2
    assert snapshot["daily_total"]["salla_sales_sar"] == 960


@pytest.mark.asyncio
async def test_all_order_total_includes_unassigned_and_outside_snapchat_without_relabeling_them_direct():
    db = Db([campaign("usd", "c-usd"), campaign("sar", "c-sar"), campaign("outside", "c-outside")])
    snapshot = await build(db, [
        order("c-usd", 100), order("c-sar", 200),
        order(None, 50, source_native="store"), order(None, 60, source_native=None),
        order(None, 300), order("c-outside", 400),
    ])
    direct, total = snapshot["direct_result"], snapshot["daily_total"]
    assert (direct["salla_orders"], direct["salla_sales_sar"]) == (2, 110)
    assert direct["ad_spend_applicable"] is False
    assert not any("spend" in key for key in direct if key != "ad_spend_applicable")
    assert (total["salla_orders"], total["salla_sales_sar"]) == (6, 1110)
    assert total["attribution_complete"] is True
    assert total["cohort_basis"] == "executive_filtered_orders"
    assert snapshot["coverage"]["unassigned_orders"] == 1
    assert snapshot["coverage"]["outside_account_scope_orders"] == 1
    assert snapshot["coverage"]["unrepresented_orders"] == 2
    assert all(row["salla_orders"] is None for row in snapshot["accounts"])
    assert sum(row["known_salla_orders"] for row in snapshot["accounts"]) == 2
    assert len(db.catalog.reads) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("account_ids", [[], ["usd", "sar"]])
async def test_catalog_or_accounts_unavailable_cannot_hide_total_or_convert_snapchat_to_direct(account_ids):
    db = Db(error=True)
    snapshot = await build(db, [order(None, 100), order(None, 40, source_native="store")], account_ids=account_ids)
    assert snapshot["coverage"]["status"] == "unavailable"
    assert snapshot["coverage"]["unrepresented_orders"] == 1
    assert snapshot["direct_result"]["salla_orders"] == 1
    assert snapshot["direct_result"]["salla_sales_sar"] == 40
    assert snapshot["daily_total"]["salla_orders"] == 2
    assert snapshot["daily_total"]["salla_sales_sar"] == 140
    assert snapshot["daily_total"]["attribution_complete"] is True
    assert len(db.catalog.reads) == (1 if account_ids else 0)


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
    assert snapshot["unassigned_result"]["known_salla_orders"] == 0
    assert snapshot["unassigned_result"]["known_salla_sales_sar"] == 0


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
    assert snapshot["unassigned_result"]["known_salla_orders"] == 1
    assert snapshot["unassigned_result"]["known_salla_sales_sar"] == 100
    assert snapshot["unassigned_result"]["attribution_complete"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("accounts", [[], [{"account_id": "usd"}, {"account_id": "sar"}]])
async def test_ad_account_ids_are_opt_in_without_changing_spend_or_saved_read_counts(monkeypatch, accounts):
    db = AsyncMongoMockClient().dashboard_account_ids_synthetic
    saved = {
        "total_sar": 10.0, "known_total_sar": 10.0,
        "rows": [{"date": DAY, "spend_sar": 10.0}],
        "daily_sar": {DAY: 10.0}, "accounts": accounts,
        "bank_commissions": {"accounts": [], "total_fee_sar": 0.0},
        "quality": {"amount_available": True, "amount_complete": True, "coverage_complete": True},
    }

    async def saved_snapchat(*args, **kwargs):
        return deepcopy(saved)

    monkeypatch.setattr(dashboard, "load_unified_marketing_dashboard_spend", saved_snapchat)
    off_db, on_db = ReadOnlyDB(db), ReadOnlyDB(db)
    off = await dashboard.build_mezan_v2_ads(off_db, OWNER, from_date=DAY, to_date=DAY)
    on = await dashboard.build_mezan_v2_ads(
        on_db, OWNER, from_date=DAY, to_date=DAY, include_account_ids=True,
    )
    assert "account_ids" not in off["providers"]["snapchat"]
    assert on["providers"]["snapchat"].pop("account_ids") == [row["account_id"] for row in accounts]
    assert on == off
    assert on_db.reads == off_db.reads


@pytest.mark.asyncio
@pytest.mark.parametrize("path", [
    "/dashboard-v2", "/dashboard-v2/cards/period-summary", "/dashboard-v2/cards/top-products",
])
async def test_legacy_routes_do_not_capture_or_emit_account_results_or_pass_new_kwargs(card_app, monkeypatch, path):
    original_filtered = dashboard._filtered_orders
    original_cost = dashboard.build_mezan_v2_product_cost_cooperative
    original_ads = dashboard.build_mezan_v2_ads

    # The default route also supports the immutable gate's pre-adjunct shims.
    async def filtered(db, user_id, *, from_date, to_date, payment_methods, shipping_companies,
                       include_marketing_attribution=False, report_statuses=None):
        return await original_filtered(
            db, user_id, from_date=from_date, to_date=to_date,
            payment_methods=payment_methods, shipping_companies=shipping_companies,
            include_marketing_attribution=include_marketing_attribution,
            **({"report_statuses": report_statuses} if report_statuses is not None else {}),
        )

    async def cost(db, user_id, orders, *, include_product_rows=True):
        return await original_cost(db, user_id, orders, include_product_rows=include_product_rows)

    async def ads(db, user_id, *, from_date, to_date):
        return await original_ads(db, user_id, from_date=from_date, to_date=to_date)

    def forbidden(*args, **kwargs):
        raise AssertionError("account results and cost capture belong only to the executive card")

    monkeypatch.setattr(dashboard, "_filtered_orders", filtered)
    monkeypatch.setattr(dashboard, "build_mezan_v2_product_cost_cooperative", cost)
    monkeypatch.setattr(dashboard, "build_mezan_v2_ads", ads)
    monkeypatch.setattr(dashboard, "DashboardAdsCostCollector", forbidden)
    monkeypatch.setattr(dashboard, "build_dashboard_ads_account_results", forbidden)
    response = await card_app.client.get(path, params=PARAMS)
    assert response.status_code == 200, response.text
    ads_result = response.json().get("ads_v2", {})
    assert "account_results" not in ads_result
    assert "account_ids" not in ads_result.get("providers", {}).get("snapchat", {})


@pytest.mark.asyncio
@pytest.mark.parametrize("matched", [False, True])
async def test_executive_snapshot_reuses_filtered_orders_and_its_existing_cache(card_app, monkeypatch, matched):
    env = card_app
    original_ads = dashboard.build_mezan_v2_ads

    async def ads(*args, **kwargs):
        assert kwargs.pop("include_account_ids", False) is True
        result = await original_ads(*args, **kwargs)
        result["providers"]["snapchat"]["account_ids"] = ["usd", "sar"]
        return result

    monkeypatch.setattr(dashboard, "build_mezan_v2_ads", ads)
    if matched:
        await env.db[results.CAMPAIGN_COLLECTION].insert_one(campaign("usd", "c-usd", user_id="a"))
    await env.db.unified_orders.update_many({"user_id": "a"}, {"$set": {
        "source_native": "snapchat", "campaign_id": "c-usd",
    }})
    response = await env.client.get("/dashboard-v2/cards/executive", params=PARAMS)
    assert response.status_code == 200, response.text
    snapshot = response.json()["ads_v2"]["account_results"]
    assert snapshot["date_from"] == snapshot["date_to"] == PARAMS["from_date"]
    assert rows_by_id(snapshot)["usd"]["salla_orders"] == (1 if matched else None)
    assert rows_by_id(snapshot)["usd"]["salla_sales_sar"] == (100 if matched else None)
    assert rows_by_id(snapshot)["sar"]["salla_orders"] == (0 if matched else None)
    assert snapshot["unassigned_result"]["known_salla_orders"] == (0 if matched else 1)
    assert snapshot["unassigned_result"]["known_salla_sales_sar"] == (0 if matched else 100)
    assert snapshot["unassigned_result"]["hover_summary"]["known_product_cost_sar"] == (0 if matched else 10)
    assert snapshot["daily_total"]["salla_orders"] == 1
    assert len(env.reads) == 1
    assert env.reads[0][1]["payment_methods"] == PARAMS["payment_methods"]
    assert env.reads[0][1]["shipping_companies"] == PARAMS["shipping_companies"]
    assert env.reads[0][1]["report_statuses"] == PARAMS["report_statuses"]
    assert env.calls["ads"] == 1
    cached = await env.client.get("/dashboard-v2/cards/executive", params=PARAMS)
    assert cached.json()["ads_v2"]["account_results"] == snapshot
    assert len(env.reads) == 1
    assert env.calls["ads"] == 1


@pytest.mark.asyncio
async def test_direct_and_all_order_total_use_the_exact_filtered_executive_cohort(card_app):
    env = card_app
    await env.db.unified_orders.update_many({"user_id": "a"}, {"$set": {
        "source_native": "store",
        "order_date_raw": PARAMS["from_date"] + "T10:00:00+03:00",
    }})
    response = await env.client.get("/dashboard-v2/cards/executive", params=PARAMS)
    assert response.status_code == 200, response.text
    body = response.json()
    result = body["ads_v2"]["account_results"]
    assert result["daily_total"]["salla_orders"] == body["totals"]["total_orders"] == 1
    assert result["daily_total"]["salla_sales_sar"] == body["totals"]["total_sales"] == 100
    assert result["direct_result"]["salla_orders"] == 1
    assert result["direct_result"]["hover_summary"]["product_cost_sar"] == 10
    assert [segment["direct_result"]["salla_orders"] for segment in result["segments"]] == [1, 0]
    assert [segment["total"]["salla_orders"] for segment in result["segments"]] == [1, 0]
    assert len(env.reads) == len(env.costs) == 1


@pytest.mark.asyncio
async def test_existing_hydration_query_carries_only_precise_source_time_and_settings_without_extra_reads(card_app):
    env = card_app
    await env.db.unified_orders.update_one({"user_id": "a", "order_number": "selected"}, {"$set": {
        "order_date_raw": PARAMS["from_date"],
        "source_native": "google",
        "raw_by_source": {"salla_direct": {
            "date": {"date": PARAMS["from_date"] + " 12:00:00", "timezone": "Asia/Riyadh"},
            "created_at": PARAMS["from_date"] + "T09:00:00+00:00",
            "customer": {"name": "PRIVATE-NOT-REQUESTED"},
            "source": "google",
        }},
    }})
    off_db, on_db = ReadOnlyDB(env.db), ReadOnlyDB(env.db)
    off = await dashboard._filtered_orders(off_db, "a", **PARAMS, include_marketing_attribution=True)
    collector = DashboardAdsCostCollector()
    on = await dashboard._filtered_orders(on_db, "a", **PARAMS, include_marketing_attribution=True,
                                         cost_collector=collector)
    assert on_db.reads == off_db.reads
    assert len(on) == len(off) == 1
    assert collector.payment_settings is not None
    raw = on[0]["raw_by_source"]["salla_direct"]
    assert raw["date"]["timezone"] == "Asia/Riyadh"
    assert raw["created_at"] == PARAMS["from_date"] + "T09:00:00+00:00"
    assert "customer" not in raw
    assert "date" not in off[0]["raw_by_source"]["salla_direct"]
    assert "created_at" not in off[0]["raw_by_source"]["salla_direct"]
    legacy_shape = deepcopy(on)
    del legacy_shape[0]["raw_by_source"]["salla_direct"]["date"]
    del legacy_shape[0]["raw_by_source"]["salla_direct"]["created_at"]
    assert legacy_shape == off
    response = await env.client.get("/dashboard-v2/cards/executive", params=PARAMS)
    assert response.status_code == 200, response.text
    result = response.json()["ads_v2"]["account_results"]
    assert result["provider_results"]["google"]["salla_orders"] == 1
    assert [segment["provider_results"]["google"]["salla_orders"] for segment in result["segments"]] == [0, 1]
    assert result["segments"][1]["provider_results"]["google"]["hover_summary"]["product_cost_sar"] == 10
