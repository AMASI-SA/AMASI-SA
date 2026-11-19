from datetime import datetime, timezone

from fastapi import FastAPI, Request
from httpx import ASGITransport, AsyncClient
from mongomock_motor import AsyncMongoMockClient
import pytest

from operational_balance_access import POLICY_FIELD
from web_extra_pages import EXTRA_WEB_PAGES
from web_page_access import enforce_web_catalog_access, make_web_page_access_router


async def fixture(ids, resources=(), operations=()):
    db = AsyncMongoMockClient().extra_pages
    user = {"id": "staff", "role": "user", "created_by": "owner",
            "web_page_access": {"owner_id": "owner", "grants": [{"id": key, "mode": mode} for key, mode in ids]},
            POLICY_FIELD: {"owner_id": "owner", "version": 1, "enabled": True, "resources": list(resources), "operations": list(operations)}}
    await db.users.insert_many([{"id": "owner", "role": "owner"}, user])
    async def auth(request: Request):
        actor = await db.users.find_one({"id": "staff"})
        enforce_web_catalog_access(actor, request.url.path, request.method, request.query_params)
        return actor
    app = FastAPI()
    app.include_router(make_web_page_access_router(db, auth), prefix="/api")
    return db, app, AsyncClient(transport=ASGITransport(app=app), base_url="https://test/api/web-page-access-v1/extra-read/")


def resource(kind, id_, mode="all", use=True):
    return {"kind": kind, "id": id_, "movements": mode, "balance": True, "report": True, "use": use}


@pytest.mark.asyncio
async def test_stock_paging_owner_filter_supplier_scope_and_source_grants():
    from operational_balance_store import STATES
    from standalone_inventory_entry import ENTRIES
    db, _, client = await fixture([("extra.inventory_purchases", "read"), ("extra.inventory_opening", "read"), ("extra.inventory_purchase", "read")],
                                  [resource("supplier", "s", "own"), resource("supplier", "all", use=False)])
    for who in ("owner", "other"):
        invoices = [{"id": str(i), "supplier_id": "s", "invoice_number": who + str(i), "gross": "12.00",
                     "actor_id": "staff", "lines": [{"secret_cost": 10}]} for i in range(10)]
        invoices += [{"id": "forbidden", "supplier_id": "s", "invoice_number": "private", "actor_id": "another"},
                     {"id": "unassigned", "supplier_id": "hidden", "invoice_number": "hidden", "actor_id": "staff"}]
        await db[STATES].insert_one({"_id": who, "owner_id": who, "inventory_purchases": invoices})
        await db[ENTRIES].insert_one({"user_id": who, "id": "batch", "result": {"lines": [
            {"name": who, "quantity": 7, "unit_cost": 33, "unit": "piece", "location_name": "رف"}]}})
        await db.mezan_suppliers_v2.insert_many([{"user_id": who, "id": key, "name": who + key} for key in ("s", "hidden", "all")])
    async with client:
        report = (await client.get("extra.inventory_purchases")).json()
        assert report["has_more"] and len(report["rows"]) == 8
        assert all(row["invoice_number"].startswith("owner") for row in report["rows"])
        assert not any("lines" in row for row in report["rows"])
        rest = (await client.get("extra.inventory_purchases?page=2")).json()
        assert len(rest["rows"]) == 2 and not rest["has_more"]
        assert (await client.get("extra.inventory_purchase")).json()["rows"] == [{"name": "owners", "currency": "SAR"}]
        opened = (await client.get("extra.inventory_opening")).json()["rows"]
        assert opened == [{"recorded_at": None, "name": "owner", "quantity": 7, "unit": "piece", "location": "رف"}]
        assert (await client.post("extra.inventory_opening")).status_code == 405
        assert (await client.get("extra.financial_entry")).status_code == 403


@pytest.mark.asyncio
async def test_granted_diagnostics_remove_raw_payloads_and_require_provider_scope():
    from salla_integration.webhook_incident_recovery import MERCHANT_ID
    from salla_integration.webhook_inbox import COLLECTION
    db, _, client = await fixture([("extra.salla_recovery", "read"), ("extra.bnpl_diagnostics", "read"), ("extra.financial_entry", "read")],
                                  [resource("provider", "tabby")])
    await db.salla_integrations.insert_one({"user_id": "owner", "store_id": MERCHANT_ID, "updated_at": datetime.now(timezone.utc)})
    for who in ("owner", "other"):
        await db[COLLECTION].insert_one({"user_id": who, "merchant_id": MERCHANT_ID, "state": "quarantined", "payload": "SECRET"})
        await db.payment_transactions.insert_many([{"user_id": who, "provider": p, "amount": 44, "raw_data": "SECRET"} for p in ("tabby", "tamara")])
    async with client:
        queue = await client.get("extra.salla_recovery")
        assert queue.status_code == 200 and "SECRET" not in queue.text
        assert next(row for row in queue.json()["rows"] if row["state"] == "متوقفة")["count"] == 1
        bnpl = await client.get("extra.bnpl_diagnostics")
        assert bnpl.status_code == 200 and bnpl.json()["rows"] == [{"provider":"تابي", "transactions_count":1, "refunds_count":0, "latest_payment_at":None}]
        assert (await client.get("extra.financial_entry")).json()["links"] == []
        assert (await client.get("extra.ads_report")).status_code == 403


@pytest.mark.asyncio
async def test_explicit_ads_range_scoped_source_and_snapchat_read_boundaries(monkeypatch):
    db, app, client = await fixture([("extra.ads_report", "read"), ("extra.snapchat_period", "read")])
    calls = []
    async def report(db_, owner, start, end):
        assert db_ is db
        calls.append((owner, start, end))
        return {"platforms":[{"label":"Snapchat","spend":10, "account_id":"SECRET"}], "combined":{"label":"الإجمالي","spend":10}}
    app.state.unified_ads_report_for_tenant = report
    async def account(db_, owner):
        assert db_ is db and owner == "owner"
        return {"timezone": "Asia/Riyadh", "ad_account_id": "PRIVATE"}
    async def audit(db_, owner, **parameters):
        assert db_ is db and owner == "owner" and parameters["max_rows"] == 10000
        return {"timezone": parameters["timezone_name"], "all_orders": dict(whole=4,left=1,right=3,gap=0),
                "explicit_snapchat_source": dict(whole=2,left=1,right=1,gap=0)}
    monkeypatch.setattr("snapchat_v2.routes._selected_account_or_404", account)
    monkeypatch.setattr("snapchat_v2.period_diagnostics.audit_period_partition", audit)
    async with client:
        ads = await client.get("extra.ads_report?date_from=2026-11-01&date_to=2026-11-05")
        assert ads.status_code == 200 and calls == [("owner", "2026-11-01", "2026-11-05")] and "SECRET" not in ads.text
        assert (await client.get("extra.ads_report?date_from=2025-01-01&date_to=2026-11-19")).status_code == 422
        assert (await client.get("extra.snapchat_period?date_from=2026-11-01&date_to=2026-11-03&split_on=2026-11-01")).status_code == 422
        snap = await client.get("extra.snapchat_period?date_from=2026-11-01&date_to=2026-11-03&split_on=2026-11-02")
        assert snap.status_code == 200 and snap.json()["rows"][0]["orders"] == 4 and "PRIVATE" not in snap.text


def test_all_eight_destinations_have_distinct_read_grants():
    assert len({page["to"] for page in EXTRA_WEB_PAGES}) == len(EXTRA_WEB_PAGES) == len({p["id"] for p in EXTRA_WEB_PAGES}) == 8
    assert all("write_label" not in p and p["review_component"] == "extra_read" for p in EXTRA_WEB_PAGES)
