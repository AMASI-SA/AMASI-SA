"""Receiving entry never waits for invoice history, product counts or scan hydration."""
import pytest
from motor.motor_asyncio import AsyncIOMotorCollection

import supplier_receiving_routes as routes
from test_supplier_scan_recovery import env, seed

pytestmark = pytest.mark.asyncio


async def test_receiving_entry_reads_only_personal_session_and_supplier_names(env, monkeypatch):
    db, http, _ = env
    session, _ = await seed(env)
    await db[routes.SUPPLIERS].insert_one({"user_id": "merchant", "id": "supplier-1", "company_name": "Synthetic supplier"})
    await db[routes.SUPPLIERS].insert_one({"user_id": "other-merchant", "id": "foreign", "company_name": "Foreign"})
    original_find = AsyncIOMotorCollection.find
    original_count = AsyncIOMotorCollection.count_documents
    reads = []
    def find(self, query, *args, **kwargs):
        reads.append(self.name)
        assert self.name not in {routes.PIECES, routes.RESOURCES, routes.RECEIVING_EVENTS, routes.SUPPLIER_INVOICES}
        assert not (self.name == routes.SESSIONS and query.get("status") == "closed")
        return original_find(self, query, *args, **kwargs)
    async def count(self, *args, **kwargs):
        assert self.name != routes.PIECES, "entry does not need a global eligible product count"
        return await original_count(self, *args, **kwargs)
    monkeypatch.setattr(AsyncIOMotorCollection, "find", find)
    monkeypatch.setattr(AsyncIOMotorCollection, "count_documents", count)
    response = await http.get("/supplier-receiving-v1/catalog", params={"entry_only": "true"})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["entry_only"] is True
    assert result["active_session"]["id"] == session["id"]
    assert [row["id"] for row in result["suppliers"]] == ["supplier-1"]
    assert "active_session_scans" not in result and "eligible_piece_count" not in result
    assert "sessions" not in result and "service_catalog" not in result
