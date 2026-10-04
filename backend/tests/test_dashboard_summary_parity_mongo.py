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
