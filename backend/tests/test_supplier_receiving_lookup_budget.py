"""Receipt lookups stay within the selected order, without PDF image bytes."""
from copy import deepcopy

from bson import BSON
from motor.motor_asyncio import AsyncIOMotorCollection, AsyncIOMotorCursor
import pytest

import supplier_receiving_routes as routes
from test_supplier_scan_recovery import env, seed, barcode

pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize("by_barcode", [False, True])
async def test_search_returns_same_receipt_decisions_without_loading_embedded_photos(env, monkeypatch, by_barcode):
    db, http, _ = env
    session, pieces = await seed(env, count=6)
    await db[routes.PIECES].update_many({}, {"$set": {"image_b64": "x" * 262144}})
    before = await db[routes.PIECES].find({}, {"_id": 0}).to_list(10)
    expected = [routes._supplier_receiving_search_piece_view(row, session=session) for row in before]
    query = barcode(pieces[0]) if by_barcode else pieces[0]["order_number"]
    original = AsyncIOMotorCursor.to_list
    loaded_bytes = []
    async def to_list(self, *args, **kwargs):
        rows = await original(self, *args, **kwargs)
        if self.collection.name == routes.PIECES:
            loaded_bytes.append(sum(len(BSON.encode(row)) for row in rows))
        return rows
    monkeypatch.setattr(AsyncIOMotorCursor, "to_list", to_list)
    response = await http.get(f"/supplier-receiving-v1/sessions/{session['id']}/search", params={"q": query})
    assert response.status_code == 200, response.text
    assert response.json()["pieces"] == expected
    assert response.json()["read_only_search"] is True and response.json()["financial_writes"] is False
    assert sum(loaded_bytes) < 20000, "order picker must not fetch megabytes of embedded PDF photos"
    assert await db[routes.RECEIVING_EVENTS].count_documents({}) == 0


async def test_receipt_lookup_examines_selected_units_among_unrelated_tenant_orders(env, monkeypatch):
    db, http, _ = env
    session, pieces = await seed(env, count=6)
    await db[routes.PIECES].insert_many([
        {"user_id": "merchant", "piece_id": f"{index + 100:032x}", "order_number": f"other-{index}"}
        for index in range(1500)
    ])
    await db[routes.PIECES].insert_one({**deepcopy(pieces[0]), "user_id": "other-merchant"})
    queries = []
    original = AsyncIOMotorCollection.find
    def find(self, query, *args, **kwargs):
        if self.name == routes.PIECES:
            queries.append(deepcopy(query))
        return original(self, query, *args, **kwargs)
    monkeypatch.setattr(AsyncIOMotorCollection, "find", find)
    response = await http.get(f"/supplier-receiving-v1/sessions/{session['id']}/search",
        params={"q": pieces[0]["order_number"]})
    assert response.status_code == 200, response.text
    assert len(response.json()["pieces"]) == 6
    assert len(queries) == 1 and queries[0]["user_id"] == "merchant"
    plan = await db.command("explain", {"find": routes.PIECES, "filter": queries[0],
        "sort": {"order_item_id": 1, "unit_index": 1, "piece_id": 1}}, verbosity="executionStats")
    assert plan["executionStats"]["totalDocsExamined"] <= 6, "search must not scan unrelated orders or another tenant"
    assert "SORT" not in str(plan["queryPlanner"]["winningPlan"]), "the order picker sort must use the same bounded lookup"
