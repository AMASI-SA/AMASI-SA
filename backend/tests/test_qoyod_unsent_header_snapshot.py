from copy import deepcopy
from datetime import datetime, timezone
from functools import partial
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from mongomock_motor import AsyncMongoMockClient

from integrations.qoyod import unsent_orders
from integrations.qoyod.routes import make_qoyod_router
from integrations.qoyod.unsent_header import read_header_count, refresh_header_count


def report(count=7, at="2026-11-04T12:00:00.000001+00:00", **extra):
    return {"ok": True, "captured_at": at, "scan_truncated": False, "truncated": True,
            "counts": {unsent_orders.UNSENT: count}, **extra}


@pytest.mark.asyncio
async def test_passive_badge_reads_one_snapshot_and_nothing_else(monkeypatch):
    scan = AsyncMock(side_effect=AssertionError("passive request must not scan"))
    monkeypatch.setattr(unsent_orders, "list_unsent_orders", scan)
    lookup = AsyncMock(return_value=None)
    db = SimpleNamespace(qoyod_unsent_header_snapshots=SimpleNamespace(find_one=lookup))
    assert await read_header_count(db, "main", "A") == {"count": None, "as_of": None}
    lookup.assert_awaited_once_with({"_id": {"tenant": "main", "orders_owner": "A", "from_date": "2026-07-01"}},
                                    {"_id": 0, "count": 1, "as_of": 1})
    scan.assert_not_awaited()


@pytest.mark.asyncio
async def test_explicit_refresh_uses_canonical_counts_and_monotonic_scope(monkeypatch):
    db = AsyncMongoMockClient().header_test
    scan = AsyncMock(return_value=report())
    monkeypatch.setattr(unsent_orders, "list_unsent_orders", scan)
    saved = await refresh_header_count(db, "main", "A")
    assert saved == {"count": 7, "as_of": report()["captured_at"], "partial": False}
    scan.assert_awaited_once_with(db, user_id="main", orders_user_id="A", from_date="2026-07-01",
                                 status=unsent_orders.UNSENT, limit=1)
    for tenant, owner in (("main", "B"), ("other", "A")):
        assert (await read_header_count(db, tenant, owner))["count"] is None

    scan.return_value = report(100, at="2026-11-04T11:59:59+00:00")
    assert await refresh_header_count(db, "main", "A") == saved
    scan.return_value = report(0, at="2026-11-04T12:01:00+00:00", scan_truncated=True)
    assert await refresh_header_count(db, "main", "A") == {**saved, "partial": True}
    assert await refresh_header_count(db, "main", "B") == {"count": None, "as_of": None, "partial": True}

    scan.side_effect = RuntimeError("isolated report failure")
    with pytest.raises(RuntimeError):
        await refresh_header_count(db, "main", "A")
    assert (await read_header_count(db, "main", "A"))["count"] == 7
    assert await db.list_collection_names() == ["qoyod_unsent_header_snapshots"]


@pytest.mark.asyncio
async def test_owner_http_routes_use_the_signed_actor_and_linked_tenant(monkeypatch):
    db = AsyncMongoMockClient().header_http
    await db.users.insert_many([{"id": name, "role": role} for name, role in
                                (("A", "owner"), ("B", "owner"), ("staff", "employee"))])
    await db.qoyod_settings.insert_one({"user_id": "main", "security_owner_id": "A"})
    principal = {"id": "A", "role": "owner"}

    async def user():
        return deepcopy(principal)

    app = FastAPI()
    app.include_router(make_qoyod_router(db, user), prefix="/api")
    scan = AsyncMock(return_value=report(0))
    monkeypatch.setattr(unsent_orders, "list_unsent_orders", scan)
    route = "/api/integrations/qoyod/unsent-orders/header-count"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        for actor in ({"id": "B"}, {"id": "A", "_mobile_actor_id": "staff"}):
            principal.clear()
            principal.update(actor)
            assert (await client.get(route)).status_code == 403
            assert (await client.post(route + "/refresh")).status_code == 403
        scan.assert_not_awaited()
        principal.clear()
        principal.update(id="A")
        assert (await client.get(route)).json() == {"count": None, "as_of": None}
        refreshed = await client.post(route + "/refresh")
        assert refreshed.json()["count"] == 0 and refreshed.json()["partial"] is False
        result = await client.get(route)
        assert result.json() == {"count": 0, "as_of": report()["captured_at"]}
        assert "no-store" in result.headers["Cache-Control"]
    assert set(await db.list_collection_names()) == {"qoyod_unsent_header_snapshots", "users", "qoyod_settings"}
    scan.assert_awaited_once()


@pytest.mark.asyncio
async def test_snapshot_matches_full_installed_report_without_changing_sender_state(monkeypatch):
    from integrations.qoyod_manual import auto_send

    assert auto_send._unified_source_patch_installed
    read_report = partial(unsent_orders.list_unsent_orders, now=datetime(2026, 11, 4, tzinfo=timezone.utc))
    monkeypatch.setattr(unsent_orders, "list_unsent_orders", read_report)
    db = AsyncMongoMockClient().header_parity
    order = {"user_id": "A", "order_date": "2026-08-08", "order_status_slug": "completed",
             "order_status_native": "تم التنفيذ", "payment_method": "cod", "total_amount": 100}
    await db.unified_orders.insert_many([
        {**order, "order_number": "1"}, {**order, "order_number": "2"},
        {**order, "order_number": "3"}, {**order, "order_number": "4", "user_id": "B"},
        {**order, "order_number": "5", "order_date": "2026-06-30"},
    ])
    await db.qoyod_invoices.insert_one({"user_id": "main", "reference": "3",
                                        "qoyod_official_reference": "3", "reference_provenance": "qoyod.reference",
                                        "qoyod_invoice_id": "invoice-3"})
    inputs = await db.list_collection_names()
    before = {name: await db[name].find({}).to_list(None) for name in inputs}
    detailed = await read_report(db, user_id="main", orders_user_id="A", from_date="2026-07-01", limit=5000)
    header = await refresh_header_count(db, "main", "A")
    assert header["count"] == detailed["counts"][unsent_orders.UNSENT] == 2
    assert not header["partial"]
    assert set(await db.list_collection_names()) == {*inputs, "qoyod_unsent_header_snapshots"}
    assert {name: await db[name].find({}).to_list(None) for name in inputs} == before
