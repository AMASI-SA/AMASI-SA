"""Personal receiving reads and two employees sharing one supplier, on loopback Mongo."""
import asyncio
from copy import deepcopy

import httpx
import pytest
from fastapi import FastAPI, Request
from motor.motor_asyncio import AsyncIOMotorCollection

import supplier_receiving_routes as routes
import supplier_scan_attempts as attempts
from test_supplier_scan_recovery import env, seed
from test_supplier_native_invoice_v2 import env as native_env, receiving_session, ACTOR, OWNER
from test_supplier_refresh_wrapper import installed_live_cost_support

pytestmark = pytest.mark.asyncio


async def test_selected_session_catalog_skips_history_counts_and_duplicate_scan_hydration(env, monkeypatch):
    db, http, _ = env
    session, _ = await seed(env)
    await db[routes.SUPPLIERS].insert_many([
        {"user_id": "merchant", "id": "supplier-1", "company_name": "Synthetic chosen", "service_links": []},
        {"user_id": "merchant", "id": "supplier-2", "company_name": "Other supplier", "service_links": []},
    ])
    original_find = AsyncIOMotorCollection.find

    def find(self, query, *args, **kwargs):
        assert not (self.name == routes.SESSIONS and query.get("status") == "closed"), "a selected draft does not need invoice history"
        assert self.name not in {routes.PIECES, routes.RECEIVING_EVENTS}, "the mandatory transactional refresh hydrates the selected draft once"
        return original_find(self, query, *args, **kwargs)

    async def no_count(*args, **kwargs):
        raise AssertionError("a selected draft does not need global eligible counts")

    monkeypatch.setattr(AsyncIOMotorCollection, "find", find)
    monkeypatch.setattr(AsyncIOMotorCollection, "count_documents", no_count)
    response = await http.get("/supplier-receiving-v1/catalog", params={"session_only": "true"})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["session_only"] is True
    assert result["active_session"]["id"] == session["id"]
    assert [row["id"] for row in result["suppliers"]] == ["supplier-1"]
    assert result["active_session_scans"] == [] and result["session_refresh_required"] is True
    assert result["sessions"] == [] and "eligible_piece_count" not in result
    assert result["permissions"]["can_scan"] is True


async def actor_http(db):
    app = FastAPI()

    async def user(request: Request):
        # Native middleware rewrites both employees to one merchant principal;
        # receiving must restore the trusted per-request employee identity.
        return {"id": "merchant", "name": "Synthetic receiver", "_session_client": routes.MOBILE_APP_CLIENT,
                "_mobile_actor_id": request.headers["x-synthetic-actor"], "_mobile_owner_id": "merchant",
                "_mobile_app_permissions": [routes.MOBILE_MY_PRODUCTS_PAGE_PERMISSION]}

    app.include_router(routes.make_supplier_receiving_router(db, user))
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://synthetic.test")


async def test_two_employees_same_supplier_have_independent_sessions_and_scan_locks(env, monkeypatch):
    db, _, _ = env
    old_session, pieces = await seed(env, 2)
    await db[routes.SESSIONS].delete_one({"id": old_session["id"]})
    # Production's preparation bootstrap creates the shared event namespace.
    # Otherwise this isolated fixture would race two implicit collection creates.
    await db.create_collection(routes.PIECE_EVENTS)
    await db[routes.SUPPLIERS].insert_one({"user_id": "merchant", "id": "supplier-1", "company_name": "Synthetic supplier"})
    original = routes._supplier_product_reference_price
    entered, release = asyncio.Event(), asyncio.Event()
    original_transaction = attempts.transaction
    failures = []

    async def transaction(db, callback):
        try:
            return await original_transaction(db, callback)
        except Exception as exc:
            failures.append(type(exc).__name__ + ": " + str(exc)[:350])
            raise

    monkeypatch.setattr(attempts, "transaction", transaction)

    async def delayed(*args, **kwargs):
        if kwargs["piece"]["piece_id"] == pieces[0]["piece_id"]:
            entered.set()
            await release.wait()
        return await original(*args, **kwargs)

    monkeypatch.setattr(routes, "_supplier_product_reference_price", delayed)
    async with await actor_http(db) as http:
        async def open_for(actor):
            response = await http.post("/supplier-receiving-v1/sessions", headers={"x-synthetic-actor": actor},
                json={"client_request_id": "open-request-" + actor, "supplier_id": "supplier-1"})
            assert response.status_code == 201, response.text
            return response.json()["session"]

        first, second = await asyncio.gather(open_for("receiver"), open_for("receiver-two"))
        assert first["id"] != second["id"]
        assert first["opened_by"] == "receiver" and second["opened_by"] == "receiver-two"
        assert first["supplier"]["id"] == second["supplier"]["id"] == "supplier-1"

        async def scan(actor, session, piece, request_id):
            return await http.post(f"/supplier-receiving-v1/sessions/{session['id']}/scan",
                headers={"x-synthetic-actor": actor}, json={"barcode": routes.BARCODE_PREFIX + piece["piece_id"],
                    "client_request_id": request_id, "quantity": 1})

        pending = asyncio.create_task(scan("receiver", first, pieces[0], "parallel-first-01"))
        try:
            await asyncio.wait_for(entered.wait(), 5)
            response = await asyncio.wait_for(scan("receiver-two", second, pieces[1], "parallel-second-01"), 5)
            assert response.status_code == 200, response.text
            assert not pending.done(), "the other employee completed while the first request was paused"
        finally:
            release.set()
            first_response = await pending
        assert first_response.status_code == 200, first_response.text
        assert not failures, failures
        assert first_response.json().get("committed") is True, first_response.text
        assert response.json().get("committed") is True, response.text
        for actor, session in [("receiver", first), ("receiver-two", second)]:
            catalog = await http.get("/supplier-receiving-v1/catalog", headers={"x-synthetic-actor": actor}, params={"session_only": "true"})
            assert catalog.status_code == 200, catalog.text
            assert catalog.json()["active_session"]["id"] == session["id"]
            saved = await db[routes.SESSIONS].find_one({"id": session["id"]})
            assert saved["scan_count"] == 1 and not saved.get("scan_lock_token"), {"actor": actor, "first": first_response.json(), "second": response.json(), "saved": saved}
        forbidden = await http.get(f"/supplier-receiving-v1/sessions/{first['id']}", headers={"x-synthetic-actor": "receiver-two"})
        assert forbidden.status_code == 403
    assert await db[routes.RECEIVING_EVENTS].count_documents({"event_type": "supplier_piece_scanned"}) == 2
    assert await db[routes.SUPPLIER_INVOICES].count_documents({}) == 0


async def test_selected_context_avoids_repeated_product_pricing_but_refresh_stays_fresh(native_env, installed_live_cost_support):
    db, commands = native_env
    session, _ = await receiving_session(db)
    piece = await db[routes.PIECES].find_one({"piece_id": "piece"}, {"_id": 0})
    event = await db[routes.RECEIVING_EVENTS].find_one({"id": "scan-event"}, {"_id": 0})
    count = 20
    for index in range(1, count):
        clone = {**deepcopy(piece), "piece_id": f"piece-{index}", "unit_index": index + 1,
                 "receipt_event_id": f"scan-event-{index}", "product_options_snapshot": {"Synthetic name": str(index)}}
        scan = {**deepcopy(event), **clone, "id": clone["receipt_event_id"]}
        await db[routes.PIECES].insert_one(deepcopy(clone))
        await db[routes.RECEIVING_EVENTS].insert_one(deepcopy(scan))
        await db[routes.PIECE_EVENTS].insert_one(deepcopy(scan))
    await db[routes.SESSIONS].update_one({"id": session["id"]}, {"$set": {"scan_count": count}})
    installed_live_cost_support()
    app = FastAPI()
    async def current():
        return deepcopy(ACTOR)
    app.include_router(routes.make_supplier_receiving_router(db, current))
    config_collections = {routes.PRODUCTS, routes.COST_PROFILES, routes.PRODUCT_RESOURCE_BINDINGS, routes.BINDINGS, routes.RESOURCES}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://synthetic.test") as http:
        commands.collections.clear()
        full = await http.get("/supplier-receiving-v1/catalog")
        assert full.status_code == 200, full.text
        assert len(full.json()["active_session_scans"]) == count
        full_config_reads = sum(name in config_collections for name in commands.collections)
        assert full_config_reads <= 8, "a legacy catalog response must not repeat product setup reads for each physical piece"
        assert {row["reference_product_unit_price_halalas"] for row in full.json()["active_session_scans"]} == {10000}
        await db[routes.COST_PROFILES].update_one({"user_id": OWNER, "salla_product_id": "product"}, {"$set": {"base_cost": 117}})
        await db[routes.PIECES].update_one({"user_id": OWNER, "piece_id": "piece"}, {"$set": {"product_name": "Fresh synthetic item"}})
        commands.collections.clear()
        second = await http.get("/supplier-receiving-v1/catalog")
        assert second.status_code == 200, second.text
        assert {row["reference_product_unit_price_halalas"] for row in second.json()["active_session_scans"]} == {11700}
        assert next(row for row in second.json()["active_session_scans"] if row["piece_id"] == "piece")["product_name"] == "Fresh synthetic item"
        second_config_reads = sum(name in config_collections for name in commands.collections)
        assert 0 < second_config_reads <= 8, "a second response must read product setup again rather than reuse the preceding response"
        commands.collections.clear()
        narrow = await http.get("/supplier-receiving-v1/catalog", params={"session_only": "true"})
        assert narrow.status_code == 200, narrow.text
        assert narrow.json()["session_refresh_required"] is True
        assert not config_collections.intersection(commands.collections)
        # A change after the context read must be visible in the actual draft;
        # navigation metadata is never a price snapshot or a cached invoice.
        await db[routes.COST_PROFILES].update_one({"user_id": OWNER, "salla_product_id": "product"}, {"$set": {"base_cost": 119}})
        commands.collections.clear()
        refreshed = await http.post(f"/supplier-receiving-v1/sessions/{session['id']}/refresh")
        assert refreshed.status_code == 200, refreshed.text
        scans = refreshed.json()["scans"]
        assert len(scans) == count
        assert {row["reference_product_unit_price_halalas"] for row in scans} == {11900}
        refresh_config_reads = sum(name in config_collections for name in commands.collections)
        assert refresh_config_reads < count, "one transaction coalesces repeated product setup reads"
        print({"synthetic_receipt_pieces": count, "full_catalog_config_reads": full_config_reads,
               "second_fresh_catalog_config_reads": second_config_reads,
               "selected_context_config_reads": 0, "mandatory_refresh_config_reads": refresh_config_reads,
               "current_unit_price_halalas": 11900})
    assert await db[routes.SUPPLIER_INVOICES].count_documents({}) == 0
