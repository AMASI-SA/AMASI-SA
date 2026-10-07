"""Real Mongo/full legacy response parity; no server import or startup."""
import os
import sys
import uuid

import pytest
import pytest_asyncio
from motor.motor_asyncio import AsyncIOMotorClient

from dashboard_summary_fixture import BASE_SHA, extract_dashboard, seed_dashboard, financial_business_payload
from dashboard_order_reads import dashboard_order_read_scope


@pytest_asyncio.fixture
async def database():
    uri = os.environ["DASHBOARD_TEST_MONGO_URI"]
    assert uri.startswith("mongodb://127.0.0.1:")
    client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=3000)
    db = client["dashboard_summary_parity_" + uuid.uuid4().hex]
    await client.admin.command("ping")
    try:
        yield db
    finally:
        await client.drop_database(db.name)
        client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("filters", [{}, {"payment_methods": "mada"}, {"shipping_companies": "smsa"}])
async def test_actual_legacy_dashboard_full_response_before_after(database, filters):
    before_server = sys.modules.get("server")
    await seed_dashboard(database, count=265)
    base = extract_dashboard(database, revision=BASE_SHA)
    current = extract_dashboard(database)
    kwargs = {"user": {"id": "owner"}, "from_date": "2026-09-01", "to_date": "2026-09-30",
              "include_legacy_analyses": False, "allow_self_heal": False, **filters}
    expected = await base(**kwargs)
    async with dashboard_order_read_scope(bounded=True):
        actual = await current(**kwargs)
    assert financial_business_payload(actual) == expected
    assert actual["totals"] == expected["totals"]
    assert sys.modules.get("server") is before_server
    assert await database.unified_orders.count_documents({}) == 266


@pytest.mark.asyncio
async def test_empty_legacy_dashboard_full_response(database):
    await seed_dashboard(database, count=0)
    kwargs = {"user": {"id": "owner"}, "from_date": "2026-09-01", "to_date": "2026-09-30",
              "include_legacy_analyses": False, "allow_self_heal": False}
    expected = await extract_dashboard(database, revision=BASE_SHA)(**kwargs)
    async with dashboard_order_read_scope(bounded=True):
        actual = await extract_dashboard(database)(**kwargs)
    assert financial_business_payload(actual) == expected


@pytest.mark.asyncio
async def test_dashboard_payment_classification_reuses_only_request_local_labels(database, monkeypatch):
    import payment_methods
    await seed_dashboard(database, count=1027)
    kwargs = {"user": {"id": "owner"}, "from_date": "2026-09-01", "to_date": "2026-09-30",
              "include_legacy_analyses": False, "allow_self_heal": False}
    expected = await extract_dashboard(database, revision=BASE_SHA)(**kwargs)
    original = payment_methods.normalize_payment_method
    calls = []
    def record(value):
        calls.append(value)
        return original(value)
    monkeypatch.setattr(payment_methods, "normalize_payment_method", record)
    current = extract_dashboard(database)
    async with dashboard_order_read_scope(bounded=True):
        actual = await current(**kwargs)
    assert financial_business_payload(actual) == expected
    assert len(calls) < 100
    first = len(calls)
    async with dashboard_order_read_scope(bounded=True):
        repeated = await current(**kwargs)
    assert financial_business_payload(repeated) == expected
    assert len(calls) == first * 2  # no cache survives the request


@pytest.mark.asyncio
@pytest.mark.parametrize("statuses", [[], ["completed", "delivered"]])
async def test_electronic_reduction_reuses_primary_parse_without_changing_totals(database, monkeypatch, statuses):
    import dashboard_order_accumulator as accumulator
    await seed_dashboard(database, count=265)
    await database.settings.update_one({"user_id": "owner"}, {"$set": {"report_included_statuses": statuses}})
    kwargs = {"user": {"id": "owner"}, "from_date": "2026-09-01", "to_date": "2026-09-30",
              "include_legacy_analyses": False, "allow_self_heal": False}
    expected = await extract_dashboard(database, revision=BASE_SHA)(**kwargs)
    original = accumulator.orders_to_parsed
    calls = []
    def record(rows):
        calls.extend(str(row["order_number"]) for row in rows)
        return original(rows)
    monkeypatch.setattr(accumulator, "orders_to_parsed", record)
    async with dashboard_order_read_scope(bounded=True):
        actual = await extract_dashboard(database)(**kwargs)
    assert financial_business_payload(actual) == expected
    assert len(calls) == expected["totals"]["total_orders"]
    assert len(calls) == len(set(calls))
