from datetime import date, datetime, timezone
from types import SimpleNamespace
import os
from uuid import uuid4

import mongomock
import pytest

from snapchat_v2.salla_outcomes import load_salla_campaign_outcomes
from snapchat_v2.period_candidates import bounded_period_cursor, period_candidate_query


class Cursor:
    def __init__(self, cursor):
        self.cursor = cursor

    async def to_list(self, length):
        return list(self.cursor.limit(length))


class Orders:
    def __init__(self, rows):
        self.collection = mongomock.MongoClient(tz_aware=True).db.orders
        self.collection.insert_many(rows)

    def find(self, query, projection):
        return Cursor(self.collection.find(query, projection))


class Settings:
    async def find_one(self, *_args):
        return {"report_included_statuses": ["completed"]}


def database(rows):
    return SimpleNamespace(unified_orders=Orders(rows), settings=Settings())


async def report(db, start, end):
    return await load_salla_campaign_outcomes(
        db, "owner", account_id="account", date_from=date.fromisoformat(start),
        date_to=date.fromisoformat(end), timezone_name="America/Los_Angeles",
        identities=[{"account_id": "account", "campaign_id": "campaign", "campaign_name": "Campaign"}],
    )


def row(**overrides):
    return {"user_id": "owner", "order_number": "1", "order_date": "2026-08-20",
            "source": "snapchat", "campaign_id": "campaign", "total_amount": 100,
            "order_status": "completed", **overrides}


@pytest.mark.asyncio
@pytest.mark.parametrize("timestamp", [
    {"created_at": "2026-08-26T07:00:00Z"},
    {"order_created_at": "2026-08-26T10:00:00+03:00"},
    {"created_at_utc": datetime(2026, 8, 26, 7, tzinfo=timezone.utc)},
    {"source_created_at": {"value": "2026-08-26 10:00:00", "timezone": "Asia/Riyadh"}},
    {"raw_by_source": {"salla_direct": {"date": {"date": "2026-08-26 10:00:00", "timezone": "Asia/Riyadh"}}}},
    {"created_at": " 20260826T070000Z "},
    {"raw_by_source": {"salla_direct": {"date": {"value": "2026-08-26 10:00:00", "timezone": "Asia/Riyadh"}}}},
])
async def test_partition_reports_retain_orders_with_different_stored_dates(timestamp):
    db = database([row(**timestamp), row(user_id="other", **timestamp)])
    whole = await report(db, "2026-08-01", "2026-09-17")
    left = await report(db, "2026-08-01", "2026-08-24")
    right = await report(db, "2026-08-25", "2026-09-17")
    assert whole["by_campaign"]["campaign"]["orders"] == 1
    assert right["by_campaign"]["campaign"]["orders"] == 1
    assert sum(r["by_campaign"].get("campaign", {}).get("orders", 0) for r in (left, right)) == 1
    assert right["by_campaign"]["campaign"]["sales_sar"] == 100
    # The legacy account calendar remains the stored Aug20 date.
    assert right["summary"]["snapchat_attributed_orders"] == 0


@pytest.mark.asyncio
async def test_timestamp_precedence_fallback_and_dst_do_not_double_count():
    db = database([
        row(order_number="1", order_date="2026-01-01", created_at="2026-11-01T08:30:00Z"),
        row(order_number="2", order_date="2026-01-01", created_at="2026-11-01T09:30:00Z"),
        row(order_number="3", order_date="2026-11-01", created_at="invalid"),
        row(order_number="4", order_date="2026-01-01", created_at="2026-11-02T08:00:00Z", order_created_at="2026-11-01T09:00:00Z"),
    ])
    result = await report(db, "2026-11-01", "2026-11-01")
    assert result["by_campaign"]["campaign"] == {"orders": 3, "sales_sar": 300.0}


@pytest.mark.asyncio
async def test_candidate_overflow_never_returns_partial_totals(monkeypatch):
    monkeypatch.setattr("snapchat_v2.salla_outcomes.MAX_ORDER_ROWS", 1)
    db = database([row(order_number=str(n), created_at="2026-08-26T07:00:00Z") for n in range(2)])
    with pytest.raises(ValueError, match="safe row limit"):
        await report(db, "2026-08-25", "2026-09-17")


def test_candidates_exclude_distant_canonical_history_and_other_tenants():
    orders = Orders([
        row(order_number="old", order_date="2020-01-01", created_at="2020-01-01T12:00:00Z"),
        row(order_number="current", order_date="2020-01-01", created_at="2026-08-26T07:00:00Z"),
        row(order_number="hidden", order_date="2026-08-26", order_date_inferred=True),
        row(user_id="other", order_date="2026-08-26"),
    ])
    query = period_candidate_query("owner", date(2026, 8, 26), date(2026, 8, 26), hide_inferred=True)
    assert [r["order_number"] for r in orders.collection.find(query)] == ["current"]


@pytest.mark.asyncio
@pytest.mark.parametrize("stamp", ["2026-08-27T03:00:00+14:00", "2026-08-25T23:00:00-14:00"])
async def test_date_line_offsets_are_candidates_then_localized(stamp):
    result = await report(database([row(order_date="2020-01-01", created_at=stamp)]), "2026-08-26", "2026-08-26")
    assert result["by_campaign"]["campaign"]["orders"] == 1


def test_real_cursor_receives_server_row_and_time_bounds():
    class Collection:
        def find(self, query, projection):
            return self
        def limit(self, value):
            self.row_limit = value
            return self
        def max_time_ms(self, value):
            self.deadline = value
            return self
    collection = Collection()
    bounded_period_cursor(collection, {}, {}, 100)
    assert collection.row_limit == 101
    assert collection.deadline == 15000


@pytest.mark.skipif(os.environ.get("SNAP_PERIOD_TEST_MONGO") != "1", reason="isolated Mongo service is CI-only")
def test_candidate_query_on_real_mongo_service():
    from pymongo import MongoClient
    client = MongoClient("mongodb://127.0.0.1:27017", serverSelectionTimeoutMS=15000, tz_aware=True)
    collection = client.snap_period_tests["orders_" + uuid4().hex]
    try:
        collection.insert_many([
            row(order_number="timestamp", order_date="2020-01-01", created_at="2026-08-26T07:00:00Z"),
            row(order_number="bson", order_date="2020-01-01", created_at_utc=datetime(2026, 8, 26, 7, tzinfo=timezone.utc)),
            row(order_number="basic", order_date="2020-01-01", created_at="20260826T070000Z"),
            row(order_number="nested", order_date="2020-01-01", raw_by_source={"salla_direct": {"date": {"date": "2026-08-26 10:00:00", "timezone": "Asia/Riyadh"}}}),
            row(order_number="old", order_date="2020-01-01", created_at="2020-01-01T07:00:00Z"),
            row(order_number="other", user_id="another", created_at="2026-08-26T07:00:00Z"),
        ])
        query = period_candidate_query("owner", date(2026, 8, 26), date(2026, 8, 26))
        found = list(bounded_period_cursor(collection, query, {"order_number": 1}, 100))
        assert {r["order_number"] for r in found} == {"timestamp", "bson", "basic", "nested"}
    finally:
        collection.drop()
        client.close()
