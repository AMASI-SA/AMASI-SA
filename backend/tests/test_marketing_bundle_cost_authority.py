"""All-provider marketing readers share frozen, read-only, batched order cost."""
from collections import Counter
from copy import deepcopy
from datetime import date
from unittest.mock import AsyncMock

import pytest

import marketing_reports
from marketing_report_orders import apply_store_orders
from integrations_control_center import snapchat_campaign_profitability as profit
from order_cost_authority import CORRECTIONS, LIFECYCLES, SNAPSHOTS
from product_v2_details_routes import COST_PROFILES
from stock_component_consumption_service import PLANS, UNITS
from test_snapchat_bundle_cost_authority import _sale_fixture, OWNER, CAMPAIGN


DAY = date(2026, 11, 1)
PROVIDER = "tiktok"
AUTHORITY_COLLECTIONS = {"mezan_bundle_definitions_v2", LIFECYCLES, SNAPSHOTS, PLANS, UNITS, CORRECTIONS}


class ReadOnlyCollection:
    def __init__(self, collection, reads):
        self.collection, self.reads = collection, reads

    def __getattr__(self, method):
        if method not in {"find", "find_one", "aggregate", "count_documents", "distinct"}:
            raise AssertionError("report attempted mutation or index operation: " + method)

        def read(*args, **kwargs):
            self.reads.append((self.collection.name, method, deepcopy(args[0]) if args else {}))
            return getattr(self.collection, method)(*args, **kwargs)
        return read


class ReadOnlyDatabase:
    def __init__(self, db):
        self.db, self.reads = db, []

    def __getitem__(self, name):
        return ReadOnlyCollection(self.db[name], self.reads)

    def __getattr__(self, name):
        return self[name]


async def dataset(monkeypatch, *, quantities=(4,), rose=True, freeze=True, duplicates=False):
    import shipping_cost_ssot
    db, orders = await _sale_fixture(quantities=quantities, rose=rose, freeze=freeze)
    await db.settings.insert_one({"user_id": OWNER, "report_included_statuses": ["completed"]})
    monkeypatch.setattr(shipping_cost_ssot, "get_company_configs",
                        AsyncMock(return_value={"pickup": {"cost_per_order": 0}}))
    for index, order in enumerate(orders):
        order.update(source="salla_direct", utm_source=PROVIDER, shipping_company="pickup",
                     payment_fee_status="actual", actual_payment_fee=0,
                     updated_at="2026-11-01T12:00:00Z",
                     raw_by_source={"salla_direct": {"id": index + 100, "source": PROVIDER}})
    await db.unified_orders.insert_many(deepcopy(orders))
    if duplicates:
        duplicate = deepcopy(orders[0])
        duplicate["order_number"] = "legacy-number-alias"
        duplicate["updated_at"] = "2026-11-01T11:00:00Z"
        await db.unified_orders.insert_one(duplicate)
    # Same identity in another tenant must never enter this cohort or its costs.
    foreign = deepcopy(orders[0])
    foreign.update(user_id="foreign-owner", total_amount=9999)
    await db.unified_orders.insert_one(foreign)
    return db, orders


def campaign():
    return {"id": "tiktok:campaign-one", "provider": PROVIDER,
            "campaign_id": CAMPAIGN[1], "account_id": "account-one",
            "name": "Synthetic winter", "timezone": "Asia/Riyadh", "spend_sar": 10}


async def report(db, reader):
    row = campaign()
    if reader == "campaign":
        await marketing_reports.attach_commerce(db, OWNER, PROVIDER, DAY, DAY, [row])
        reconciliation = None
    else:
        reports = [{"provider": PROVIDER, "rows": [row]}]
        reconciliation = await apply_store_orders(db, OWNER, DAY, DAY, reports)
    return row["profitability"], row["salla"], reconciliation


async def state(db):
    return {name: await db[name].find({}).sort("_id", 1).to_list(None)
            for name in sorted(await db.list_collection_names())}


@pytest.mark.asyncio
@pytest.mark.parametrize("reader", ["campaign", "store"])
@pytest.mark.parametrize("rose,expected", [(True, 220), (False, 60)])
async def test_marketing_reads_frozen_bundle_cost_after_catalog_edits(monkeypatch, reader, rose, expected):
    db, _ = await dataset(monkeypatch, rose=rose)
    await db[COST_PROFILES].update_many({"user_id": OWNER}, {"$set": {"base_cost": 999}})
    before = await state(db)
    result, salla, _ = await report(ReadOnlyDatabase(db), reader)
    assert result["product_cost_sar"] == expected
    assert result["products"][0]["product_cost_sar"] == expected
    assert result["products"][0]["units"] == 4
    assert result["net_profit_sar"] == 500 - expected - 10
    assert result["cost_status"] == "complete"
    assert salla["orders"] == 1
    assert await state(db) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("reader", ["campaign", "store"])
@pytest.mark.parametrize("refund,expected", [({"actual_partial_refund_amount": 100}, 176),
                                          ({"actual_refund_amount": 500}, 0)])
async def test_marketing_refund_policy_applies_once_to_frozen_cost(monkeypatch, reader, refund, expected):
    db, _ = await dataset(monkeypatch)
    await db.unified_orders.update_one({"user_id": OWNER}, {"$set": refund})
    result, _, _ = await report(ReadOnlyDatabase(db), reader)
    assert result["product_cost_sar"] == expected
    assert result["products"][0]["product_cost_sar"] == expected
    assert result["products"][0]["units"] == pytest.approx(4 * expected / 220)


@pytest.mark.asyncio
@pytest.mark.parametrize("reader", ["campaign", "store"])
async def test_marketing_foreign_sales_do_not_convert_sar_cost_or_refund_denominator_twice(monkeypatch, reader):
    db, _ = await dataset(monkeypatch)
    await db.unified_orders.update_one({"user_id": OWNER}, {"$set": {
        "total_amount": 100, "currency": "QAR", "actual_partial_refund_amount": 30,
        "raw_by_source.salla_direct.currency": "QAR",
        "raw_by_source.salla_direct.amounts.total": {"amount": 100, "currency": "QAR"},
        "raw_by_source.salla_direct.exchange_rate": {
            "base_currency": "SAR", "exchange_currency": "QAR", "rate": 1.5},
    }})
    result, _, _ = await report(ReadOnlyDatabase(db), reader)
    assert result["sales_sar"] == 150
    assert result["product_cost_sar"] == 176  # 220 × (1 - 30 / 150).
    assert result["net_profit_sar"] == -36


@pytest.mark.asyncio
@pytest.mark.parametrize("reader", ["campaign", "store"])
async def test_marketing_unknown_sale_fx_does_not_manufacture_profit(monkeypatch, reader):
    db, _ = await dataset(monkeypatch)
    await db.unified_orders.update_one({"user_id": OWNER}, {"$set": {"currency": "QAR"}})
    result, _, proof = await report(ReadOnlyDatabase(db), reader)
    assert result["product_cost_sar"] == 220
    assert result["sales_sar"] is None and result["net_profit_sar"] is None
    assert result["net_profit_available"] is False
    if reader == "store":
        assert proof["unknown_currency_orders"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("reader", ["campaign", "store"])
async def test_marketing_missing_bundle_snapshot_keeps_cost_coverage_unresolved(monkeypatch, reader):
    db, _ = await dataset(monkeypatch, freeze=False)
    before = await state(db)
    result, _, _ = await report(ReadOnlyDatabase(db), reader)
    assert result["cost_status"] == "missing" and result["missing_cost_orders"] == 1
    assert result["product_cost_sar"] is None and result["net_profit_sar"] is None
    assert result["products"][0]["cost_status"] == "missing"
    assert await state(db) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("reader", ["campaign", "store"])
@pytest.mark.parametrize("count", [1, 50])
async def test_marketing_deduplicates_before_one_batched_authority_load(monkeypatch, reader, count):
    db, _ = await dataset(monkeypatch, quantities=(4,) * count, duplicates=True)
    observed, calls = ReadOnlyDatabase(db), []
    loader = profit.load_order_cost_authority

    async def counted(source, owner, orders, **kwargs):
        calls.append((owner, [row["order_number"] for row in orders]))
        return await loader(source, owner, orders, **kwargs)

    monkeypatch.setattr(profit, "load_order_cost_authority", counted)
    result, salla, proof = await report(observed, reader)
    assert len(calls) == 1 and calls[0][0] == OWNER
    assert set(calls[0][1]) == {f"order-{i + 1}" for i in range(count)}
    assert len(calls[0][1]) == count
    assert salla["orders"] == count and result["product_cost_sar"] == 220 * count
    queries = [(name, method, query) for name, method, query in observed.reads if name in AUTHORITY_COLLECTIONS]
    assert Counter(name for name, _, _ in queries) == Counter({name: 1 for name in AUTHORITY_COLLECTIONS})
    for _, method, query in queries:
        assert (query[0]["$match"] if method == "aggregate" else query)["user_id"] == OWNER
    if reader == "store":
        assert proof["duplicates_removed"] == 1 and proof["orders"] == count
