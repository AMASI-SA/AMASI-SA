"""Real Mongo/HTTP proof of one employee+supplier publication and recovery.

Only the external provider transport is replaced. Transaction capability,
canonical allocation/materializer/dispatch, file PDF and employee readers run.
"""
from copy import deepcopy
import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI, Header, Request
from motor.motor_asyncio import AsyncIOMotorClient

import preparation_assignment_v2 as assignment
import preparation_piece_operations as pieces
import preparation_piece_line_services as line_services
import preparation_file_registry as registry
import preparation_supplier_dispatch as dispatch
import reviewed_preparation_batches as batches
import reviewed_products_catalog as catalog
from mobile_app_permissions import MOBILE_APP_ACCESS
from operational_atomic import operational_owner
from product_fulfillment_rules import PRODUCT_RESOURCE_BINDINGS
from product_option_cost_routes import RESOURCES
from product_v2_routes import PRODUCTS
from supplier_dispatch_pdf import make_supplier_dispatch_pdf_router
from supplier_dispatch_share_evidence import make_supplier_dispatch_share_evidence_router, EVIDENCE

BASE = "/api/preparation-assignment-v2"
OWNER = "assignment-owner"
REVIEWER = "assignment-reviewer"
EMPLOYEE = "assignment-employee"
SUPPLIER = "assignment-supplier"


@pytest_asyncio.fixture
async def db():
    uri = os.environ.get("MZ2_TEST_MONGO_URI", "")
    if not uri:
        pytest.skip("isolated real replica set required")
    assert uri.startswith("mongodb://127.0.0.1:")
    client = AsyncIOMotorClient(uri, tz_aware=True, serverSelectionTimeoutMS=5000)
    database = client["combined_assignment_writer_" + uuid4().hex]
    hello = await database.command("hello")
    assert hello.get("setName") and hello.get("isWritablePrimary")
    try:
        yield database
    finally:
        await client.drop_database(database.name)
        client.close()


@pytest.fixture(autouse=True)
def production_helpers(monkeypatch):
    # Importing the native context installs two process-wide wrappers. Keep
    # those real wrappers inside this fixture, not pytest collection, so the
    # separate low-level legacy workspace tests retain their own read model.
    import preparation_route_history as route_history
    import supplier_receipt_employee_custody as receipt_custody
    import supplier_receiving_routes as receiving
    for module, name in ((dispatch, "_employee_workspace"), (route_history, "_INSTALLED"),
                         (receiving, "supplier_service_completion_update"), (receipt_custody, "_INSTALLED")):
        monkeypatch.setattr(module, name, getattr(module, name))
    route_history.install_supplier_dispatch_route_guard()
    receipt_custody.install_supplier_receipt_employee_custody()
    for module in (pieces, line_services, registry, batches):
        for name, value in list(vars(module).items()):
            if callable(value) or name.startswith("_ORIGINAL_") or name == "_INSTALLED":
                monkeypatch.setattr(module, name, value)
    pieces.install_preparation_piece_operations()
    line_services.install_preparation_piece_line_services()
    async def provider_status(*args, **kwargs):
        from operational_atomic import _ACTIVE
        assert _ACTIVE.get() is None, "provider status must stay outside publication"
        return "sent", None
    async def provider_read(*args, **kwargs):
        from operational_atomic import _ACTIVE
        assert _ACTIVE.get() is None, "provider source read must stay outside publication"
        return {"ok": True, "found": True}
    monkeypatch.setattr(pieces, "_sync_salla_in_progress", provider_status)
    monkeypatch.setattr(batches, "refresh_order_from_salla", provider_read)


async def seed(db, *, quantity=1, number="A001", owner=OWNER):
    if not await db.users.find_one({"id": owner}):
        await db.users.insert_many([
            {"id": owner, "role": "owner", "name": "مالك تجريبي", "email": "owner@example.test"},
            {"id": REVIEWER, "role": "employee", "name": "موظف الإسناد", "email": "reviewer@example.test", "created_by": owner},
            {"id": EMPLOYEE, "role": "employee", "name": "موظف التجهيز", "email": "employee@example.test", "created_by": owner},
        ])
        for actor, permission in ((REVIEWER, "app.page.reviewed_preparation"), (EMPLOYEE, "app.page.my_products")):
            await db[MOBILE_APP_ACCESS].insert_one({"owner_user_id": owner, "user_id": actor,
                "enabled": True, "permissions": [permission]})
            await db.mezan_employees_v2.insert_one({"user_id": owner, "id": "profile-" + actor,
                "account_user_id": actor, "status": "active"})
        await db[dispatch.MEZAN_SUPPLIERS_V2].insert_one({"user_id": owner, "id": SUPPLIER,
            "company_name": "مورد تجريبي", "status": "active", "category_ids": ["cat"], "service_ids": ["service"]})
        await db[PRODUCTS].insert_one({"user_id": owner, "id": "p", "salla_product_id": "p",
            "sku": "P-1", "name": "منتج تجريبي", "cost_category_id": "cat"})
        await db[RESOURCES].insert_one({"user_id": owner, "id": "service", "kind": "service", "name": "تطريز الاسم", "status": "active"})
        await db[PRODUCT_RESOURCE_BINDINGS].insert_one({"user_id": owner, "salla_product_id": "p", "resource_id": "service", "quantity": 1})
    native = {"id": "salla-" + number, "reference_id": number,
        "date": {"date": "2026-11-22T08:00:00+00:00"}, "status": {"slug": "reviewed", "name": "تمت المراجعة"},
        "customer": {"full_name": "عميل تجريبي"}, "amounts": {"total": {"amount": 10, "currency": "SAR"}},
        "items": [{"id": "line-" + number, "quantity": quantity,
            "product": {"id": "p", "name": "منتج تجريبي", "sku": "P-1"},
            "options": [{"name": "المقاس", "value": "8"}]}]}
    await db.unified_orders.insert_one({"user_id": owner, "order_number": number,
        "order_date": "2026-11-22T08:00:00+00:00", "order_status": "reviewed",
        "raw_by_source": {"salla_direct": native}})
    await db[catalog.WORKFLOWS].insert_one({"user_id": owner, "order_number": number,
        "stage": "reviewed", "reviewed_at": "2026-11-22T10:00:00+03:00", "revision": 1,
        "items": [{"order_item_id": f"salla:{number}:line-{number}", "product_id": "p",
            "product_key": "product:p", "product_name": "منتج تجريبي", "quantity": quantity,
            "sku": "P-1", "supplier_export": True, "review_status": "reviewed"}]})
    return number


def client_for(db):
    async def current_user(request: Request, x_actor: str = Header(default=REVIEWER)):
        from mobile_app_request_context import mobile_app_request_user
        user = await db.users.find_one({"id": x_actor}, {"_id": 0})
        user["_session_client"] = "amasi_mobile"
        return await mobile_app_request_user(db, user, path=request.url.path, method=request.method)
    app = FastAPI()
    app.include_router(assignment.make_preparation_assignment_v2_router(db, current_user), prefix="/api")
    app.include_router(dispatch.make_preparation_supplier_dispatch_router(db, current_user), prefix="/api")
    app.include_router(make_supplier_dispatch_pdf_router(db, current_user), prefix="/api")
    app.include_router(make_supplier_dispatch_share_evidence_router(db, current_user), prefix="/api")
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url="http://synthetic")


async def command_for(db, client, *, mode="assign", number="A001", quantity=None):
    context = await catalog.load_reviewed_product_context(db, user_id=OWNER, limit=1, order_numbers=[number])
    selected = context["catalog"]["products"]
    if quantity is not None:
        selected = selected[:quantity]
    assert selected
    options = await client.get(BASE + "/options")
    assert options.status_code == 200, options.text
    command = {"client_request_id": str(uuid4()), "file_title": "دفعة تجريبية",
        "responsible_employee_id": EMPLOYEE, "expected_quantity": len(selected), "selected_product_count": len(selected),
        "schedule_mode": "automatic", "required_due_at": None,
        "selections": [{"group_key": row["group_key"], "quantity": 1, "revision": row["revision"], "order_number": number} for row in selected],
        "supplier_assignment": {"mode": mode, **({"supplier_id": SUPPLIER} if mode == "assign" else {})},
        "expected_session_scope": options.json()["session_scope"]}
    return command, options.json()


@pytest.mark.asyncio
async def test_assign_commits_exact_units_and_first_employee_progress_http(db):
    await seed(db, quantity=2)
    async with client_for(db) as client:
        command, options = await command_for(db, client)
        absent = await client.get(BASE + "/requests/" + command["client_request_id"])
        response = await client.post(BASE + "/requests", json=command)
        assert response.status_code == 200, response.text
        result = response.json()
        assert result["state"] == "COMMITTED", result
        receipt = result["receipt"]
        assert len(receipt["piece_ids"]) == receipt["piece_count"] == 2
        assert receipt["piece_ids"] == receipt["dispatch"]["piece_ids"] == receipt["batch"]["piece_ids"]
        stored_dispatch = await db[dispatch.DISPATCHES].find_one({"id": receipt["dispatch"]["id"]})
        assert stored_dispatch["sent_by_id"] == EMPLOYEE
        assert stored_dispatch["performed_by_id"] == REVIEWER
        assert stored_dispatch["sent_by_name"] == "موظف التجهيز"
        assert await db[pieces.PIECES].count_documents({"responsible_employee_id": EMPLOYEE,
            "sent_to_supplier_by_id": REVIEWER, "supplier_dispatch_status": "sent", "status": "in_progress"}) == 2
        assert (await db[pieces.PIECES].find_one({}))["services"][0]["service_id"] == "service"
        employee = {"X-Actor": EMPLOYEE}
        days = await client.get("/api/supplier-dispatch-v1/progress-days?supplier_dates=true", headers=employee)
        assert days.status_code == 200 and len(days.json()["days"]) == 1, days.text
        progress = await client.get("/api/supplier-dispatch-v1/progress-workspace", params={
            "supplier_dates": "true", "day": days.json()["days"][0]["key"]}, headers=employee)
        assert progress.status_code == 200, progress.text
        account = progress.json()["supplier_accounts"][0]
        assert account["sent_quantity"] == 2 and len(account["products"]) == 2, progress.text
        assert {product["piece_id"] for product in account["products"]} == set(receipt["piece_ids"])
        pdf = await client.get("/api/supplier-dispatch-pdf-v1/" + receipt["dispatch"]["id"] + "/remaining-pdf", headers=employee)
        assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF"), pdf.text[:200]
        recovered = await client.get(BASE + "/requests/" + command["client_request_id"])
        replay = await client.post(BASE + "/requests", json=command)
        assert recovered.json() == result == replay.json()
        assert await db[dispatch.DISPATCHES].count_documents({}) == 1
        assert await db[catalog.PREPARATION_UNIT_ALLOCATIONS].count_documents({"status": "committed"}) == 2
        path = os.environ.get("ASSIGNMENT_CONTRACT_SAMPLE")
        if path:
            Path(path).write_text(json.dumps({"options": options, "command": command,
                "absent": absent.json(), "committed": result, "recovered": recovered.json(),
                "progress": progress.json()}, ensure_ascii=False, indent=2))


@pytest.mark.asyncio
async def test_explicit_defer_remains_waiting_and_does_not_dispatch(db):
    await seed(db)
    async with client_for(db) as client:
        command, _ = await command_for(db, client, mode="defer")
        response = await client.post(BASE + "/requests", json=command)
        assert response.status_code == 200, response.text
        assert response.json()["state"] == "COMMITTED", response.text
        receipt = response.json()["receipt"]
        assert receipt["destination"] == "waiting" and receipt["dispatch"] is None
        assert await db[dispatch.DISPATCHES].count_documents({}) == 0
        assert await db[pieces.PIECES].count_documents({"status": "assigned", "responsible_employee_id": EMPLOYEE}) == 1
        waiting = await client.get("/api/supplier-dispatch-v1/waiting-page", headers={"X-Actor": EMPLOYEE})
        assert waiting.status_code == 200, waiting.text
        assert "piece_" in waiting.text or receipt["piece_ids"][0] in waiting.text
        path = os.environ.get("ASSIGNMENT_CONTRACT_SAMPLE")
        if path:
            sample = json.loads(Path(path).read_text())
            sample.update({"defer_command": command, "deferred": response.json()})
            Path(path).write_text(json.dumps(sample, ensure_ascii=False, indent=2))


PUBLICATION_COLLECTIONS = [registry.REGISTRY, batches.BATCHES, catalog.PREPARATION_UNIT_ALLOCATIONS,
    pieces.PIECES, dispatch.DISPATCHES, dispatch.DISPATCH_EVENTS, pieces.PIECE_EVENTS, assignment.EVENTS]


async def assert_no_publication(db):
    for collection in PUBLICATION_COLLECTIONS:
        assert await db[collection].count_documents({}) == 0, collection


@pytest.mark.asyncio
async def test_mid_dispatch_failure_rolls_back_file_units_dispatch_and_receipt(db, monkeypatch):
    await seed(db)
    original = dispatch.publish_supplier_dispatch
    async def fail_after_dispatch(*args, **kwargs):
        await original(*args, **kwargs)
        raise RuntimeError("synthetic failure after dispatch and its events")
    monkeypatch.setattr(dispatch, "publish_supplier_dispatch", fail_after_dispatch)
    async with client_for(db) as client:
        command, _ = await command_for(db, client)
        response = await client.post(BASE + "/requests", json=command)
        assert response.status_code == 500
        await assert_no_publication(db)
        row = await db[assignment.REQUESTS].find_one({})
        assert row["state"] == "PENDING" and "receipt" not in row
        pending = await client.get(BASE + "/requests/" + command["client_request_id"])
        assert pending.json()["state"] == "PENDING" and pending.json()["receipt"] is None
        assert pending.json()["command"]["supplier_assignment"] == command["supplier_assignment"]


@pytest.mark.asyncio
async def test_same_request_is_pending_during_pdf_then_replays_once(db, monkeypatch):
    await seed(db)
    prepared = asyncio.Event(); resume = asyncio.Event()
    original = assignment._prepare_assignment
    async def pause(*args, **kwargs):
        prepared.set()
        await resume.wait()
        return await original(*args, **kwargs)
    monkeypatch.setattr(assignment, "_prepare_assignment", pause)
    async with client_for(db) as client:
        command, _ = await command_for(db, client)
        first = asyncio.create_task(client.post(BASE + "/requests", json=command))
        await asyncio.wait_for(prepared.wait(), 5)
        try:
            pending = await client.get(BASE + "/requests/" + command["client_request_id"])
            duplicate = await client.post(BASE + "/requests", json=command)
            assert pending.status_code == duplicate.status_code == 200
            assert pending.json() == duplicate.json()
            assert pending.json()["state"] == "PENDING" and pending.json()["receipt"] is None
            await assert_no_publication(db)
            path = os.environ.get("ASSIGNMENT_CONTRACT_SAMPLE")
            if path:
                sample = json.loads(Path(path).read_text())
                sample.update({"pending_command": command, "pending": pending.json()})
                Path(path).write_text(json.dumps(sample, ensure_ascii=False, indent=2))
        finally:
            resume.set()
        response = await first
        assert response.status_code == 200 and response.json()["state"] == "COMMITTED", response.text
        assert await db[dispatch.DISPATCHES].count_documents({}) == 1


@pytest.mark.asyncio
async def test_two_request_ids_cannot_consume_the_same_physical_unit(db, monkeypatch):
    await seed(db)
    prepared = 0; both = asyncio.Event()
    original = assignment._prepare_assignment
    async def pause(*args, **kwargs):
        nonlocal prepared
        result = await original(*args, **kwargs)
        prepared += 1
        if prepared == 2:
            both.set()
        await asyncio.wait_for(both.wait(), 5)
        return result
    monkeypatch.setattr(assignment, "_prepare_assignment", pause)
    async with client_for(db) as client:
        command, _ = await command_for(db, client)
        competing = {**command, "client_request_id": str(uuid4())}
        responses = await asyncio.gather(client.post(BASE + "/requests", json=command),
            client.post(BASE + "/requests", json=competing))
        assert sorted(response.status_code for response in responses) == [200, 409], [r.text for r in responses]
        assert await db[catalog.PREPARATION_UNIT_ALLOCATIONS].count_documents({}) == 1
        assert await db[pieces.PIECES].count_documents({}) == 1
        assert await db[dispatch.DISPATCHES].count_documents({}) == 1
        assert await db[assignment.REQUESTS].count_documents({"state": "COMMITTED"}) == 1


@pytest.mark.asyncio
async def test_late_failed_builder_cannot_claim_not_applied_after_lease_takeover(db, monkeypatch):
    await seed(db)
    first_entered = asyncio.Event(); fail_first = asyncio.Event(); calls = 0
    original = assignment._prepare_assignment
    async def prepare(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            first_entered.set()
            await fail_first.wait()
            raise RuntimeError("late failure after another builder committed")
        return await original(*args, **kwargs)
    monkeypatch.setattr(assignment, "_prepare_assignment", prepare)
    async with client_for(db) as client:
        command, _ = await command_for(db, client)
        first = asyncio.create_task(client.post(BASE + "/requests", json=command))
        await asyncio.wait_for(first_entered.wait(), 5)
        try:
            await db[assignment.REQUESTS].update_one({"client_request_id": command["client_request_id"]},
                {"$set": {"prepare_lease_until": datetime(2000, 1, 1, tzinfo=timezone.utc)}})
            second = await client.post(BASE + "/requests", json=command)
            assert second.status_code == 200 and second.json()["state"] == "COMMITTED", second.text
        finally:
            fail_first.set()
        late = await first
        assert late.status_code == 200 and late.json() == second.json(), late.text
        assert await db[dispatch.DISPATCHES].count_documents({}) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("target,mutation", [(REVIEWER, "grant"), (EMPLOYEE, "grant"), (EMPLOYEE, "account")])
async def test_live_revoke_after_pdf_blocks_both_assignments(db, monkeypatch, target, mutation):
    await seed(db)
    original = assignment._prepare_assignment
    async def revoke(*args, **kwargs):
        prepared = await original(*args, **kwargs)
        if mutation == "grant":
            await db[MOBILE_APP_ACCESS].update_one({"user_id": target}, {"$set": {"enabled": False}})
        else:
            await db.users.update_one({"id": target}, {"$set": {"disabled": True}})
        return prepared
    monkeypatch.setattr(assignment, "_prepare_assignment", revoke)
    async with client_for(db) as client:
        command, _ = await command_for(db, client)
        response = await client.post(BASE + "/requests", json=command)
        assert response.status_code == 403, response.text
        await assert_no_publication(db)


@pytest.mark.asyncio
async def test_account_permission_metadata_fence_retries_snapshot_on_revocation(db):
    await seed(db)
    read = asyncio.Event(); resume = asyncio.Event(); attempts = 0
    reviewer = {"id": REVIEWER, "created_by": OWNER, "_session_client": "amasi_mobile"}
    async def callback(tx):
        nonlocal attempts
        attempts += 1
        await assignment._authority(tx, OWNER, reviewer, EMPLOYEE, fence=False)
        if attempts == 1:
            read.set()
            await resume.wait()
        await assignment._authority(tx, OWNER, reviewer, EMPLOYEE, fence=True)
        await tx[assignment.REQUESTS].insert_one({"user_id": OWNER, "client_request_id": "never-published"})
    task = asyncio.create_task(operational_owner(db, OWNER, callback, profile=assignment.PROFILE))
    await asyncio.wait_for(read.wait(), 5)
    await db[MOBILE_APP_ACCESS].update_one({"owner_user_id": OWNER, "user_id": EMPLOYEE}, {"$set": {"enabled": False}})
    resume.set()
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as failure:
        await task
    assert failure.value.status_code == 403 and attempts >= 2
    assert await db[assignment.REQUESTS].count_documents({}) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["status", "revision", "supplier", "hold"])
async def test_current_source_supplier_and_instruction_revalidated_at_publication(db, monkeypatch, change):
    await seed(db)
    original = assignment._prepare_assignment
    async def changed(*args, **kwargs):
        prepared = await original(*args, **kwargs)
        if change == "status":
            await db.unified_orders.update_one({}, {"$set": {"order_status": "cancelled",
                "raw_by_source.salla_direct.status": {"slug": "cancelled", "name": "ملغي"}}})
        elif change == "revision":
            await db.unified_orders.update_one({}, {"$set": {"raw_by_source.salla_direct.items.0.quantity": 2}})
        elif change == "supplier":
            await db[dispatch.MEZAN_SUPPLIERS_V2].update_one({}, {"$set": {"status": "inactive"}})
        else:
            from order_tracking_notes import ORDER_TRACKING_INSTRUCTIONS
            await db[ORDER_TRACKING_INSTRUCTIONS].insert_one({"user_id": OWNER, "id": "hold", "order_number": "A001",
                "status": "active", "target_stages": ["supplier_dispatch"], "scope": "order",
                "action_type": "edit_product", "enforcement": "completion_required", "activation_state": "active"})
        return prepared
    monkeypatch.setattr(assignment, "_prepare_assignment", changed)
    async with client_for(db) as client:
        command, _ = await command_for(db, client)
        response = await client.post(BASE + "/requests", json=command)
        assert response.status_code in {403, 404, 409}, response.text
        await assert_no_publication(db)


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", ["omitted", "missing_supplier", "defer_supplier", "foreign_supplier", "wrong_category", "foreign_employee"])
async def test_invalid_combined_selection_is_rejected_before_publication(db, invalid):
    await seed(db)
    async with client_for(db) as client:
        command, _ = await command_for(db, client)
        if invalid == "omitted":
            del command["supplier_assignment"]
        elif invalid == "missing_supplier":
            command["supplier_assignment"] = {"mode": "assign"}
        elif invalid == "defer_supplier":
            command["supplier_assignment"] = {"mode": "defer", "supplier_id": SUPPLIER}
        elif invalid == "foreign_supplier":
            await db[dispatch.MEZAN_SUPPLIERS_V2].update_one({}, {"$set": {"user_id": "other-owner"}})
        elif invalid == "wrong_category":
            await db[PRODUCTS].update_one({}, {"$set": {"cost_category_id": "other-category"}})
        else:
            await db.users.update_one({"id": EMPLOYEE}, {"$set": {"created_by": "other-owner"}})
        response = await client.post(BASE + "/requests", json=command)
        assert response.status_code in {403, 404, 409, 422}, response.text
        if invalid != "foreign_employee":
            assert response.json()["detail"]["not_applied"] is True, response.text
        await assert_no_publication(db)


@pytest.mark.asyncio
async def test_request_fingerprint_binds_supplier_mode_employee_and_exact_units(db):
    await seed(db, quantity=2)
    async with client_for(db) as client:
        command, _ = await command_for(db, client, quantity=1)
        response = await client.post(BASE + "/requests", json=command)
        assert response.status_code == 200 and response.json()["state"] == "COMMITTED", response.text
        for patch in ({"supplier_assignment": {"mode": "defer"}},
                      {"supplier_assignment": {"mode": "assign", "supplier_id": "other"}},
                      {"responsible_employee_id": REVIEWER},
                      {"selections": [{**command["selections"][0], "revision": "a" * 64}]}):
            conflict = await client.post(BASE + "/requests", json={**command, **patch})
            assert conflict.status_code == 409 and conflict.json()["detail"]["not_applied"] is False, conflict.text
        assert await db[pieces.PIECES].count_documents({}) == 1
        workflow = await db[catalog.WORKFLOWS].find_one({})
        assert workflow["stage"] == "reviewed", "one dispatched file must not advance an incompletely assigned order"
        assert workflow["preparation_progress"]["remaining_quantity"] == 1


@pytest.mark.asyncio
async def test_readback_refuses_a_missing_dispatch_or_changed_receipt(db):
    await seed(db)
    async with client_for(db) as client:
        command, _ = await command_for(db, client)
        response = await client.post(BASE + "/requests", json=command)
        assert response.status_code == 200 and response.json()["state"] == "COMMITTED", response.text
        await db[assignment.REQUESTS].update_one({}, {"$set": {"receipt.dispatch.supplier_id": "wrong"}})
        invalid = await client.get(BASE + "/requests/" + command["client_request_id"])
        assert invalid.json()["state"] == "PENDING" and invalid.json()["receipt"] is None
        await db[assignment.REQUESTS].update_one({}, {"$set": {"receipt": response.json()["receipt"]}})
        await db[dispatch.DISPATCHES].delete_many({})
        missing = await client.get(BASE + "/requests/" + command["client_request_id"])
        assert missing.json()["state"] == "PENDING" and missing.json()["receipt"] is None
        replay = await client.post(BASE + "/requests", json=command)
        assert replay.json()["state"] == "PENDING"
        assert await db[pieces.PIECES].count_documents({}) == 1


@pytest.mark.asyncio
async def test_profile_cannot_create_or_mutate_authority_finance_or_order_state(db):
    await seed(db)
    await db.mz2_atomic_owners.insert_one({"_id": OWNER, "writes_paused": True, "revision": 0})
    from fastapi import HTTPException
    actions = [
        lambda tx: tx.users.update_one({"id": EMPLOYEE}, {"$set": {"role": "owner"}}),
        lambda tx: tx[MOBILE_APP_ACCESS].update_one({"owner_user_id": OWNER, "user_id": EMPLOYEE}, {"$set": {"enabled": True}}),
        lambda tx: tx.users.insert_one({"user_id": OWNER, "id": "new-login", "role": "owner"}),
        lambda tx: tx.users.update_one({"id": "foreign"}, {"$inc": {"preparation_assignment_fence": 1}}),
        lambda tx: tx.unified_orders.update_one({"user_id": OWNER, "order_number": "A001"}, {"$set": {"order_status": "in_progress"}}),
        lambda tx: tx.order_review_workflows.update_one({"user_id": OWNER, "order_number": "A001"}, {"$set": {"stage": "in_progress"}}),
        lambda tx: tx.mz2_ledger_entries.insert_one({"user_id": OWNER, "amount": 500}),
    ]
    for action in actions:
        with pytest.raises(HTTPException):
            await operational_owner(db, OWNER, action, profile=assignment.PROFILE)
    assert (await db.users.find_one({"id": EMPLOYEE}))["role"] == "employee"
    assert (await db.mz2_atomic_owners.find_one({"_id": OWNER}))["writes_paused"] is True
    assert await db.mz2_ledger_entries.count_documents({}) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["direct", "bundle_parent"])
async def test_combined_assignment_cannot_capture_direct_stock_or_bundle_parent_units(db, route):
    await seed(db)
    async with client_for(db) as client:
        command, _ = await command_for(db, client)
        if route == "direct":
            await db[catalog.WORKFLOWS].update_one({}, {"$set": {
                "items.0.supplier_export": False, "items.0.preparation_route": "direct_assembly"}})
        else:
            await db.mezan_component_order_lifecycle_v1.insert_one({"user_id": OWNER, "order_number": "A001",
                "bundle_intake_snapshots": {"salla:A001:line-A001": {"definition_id": "synthetic-bundle"}}})
        response = await client.post(BASE + "/requests", json=command)
        assert response.status_code == 409, response.text
        assert response.json()["detail"]["not_applied"] is True
        assert response.json()["detail"]["code"] == "reviewed_product_not_available"
        await assert_no_publication(db)


@pytest.mark.asyncio
async def test_native_pdf_and_share_use_real_custody_without_borrowed_owner_authority(db):
    await seed(db)
    await db.users.insert_many([
        {"id": "third", "role": "employee", "created_by": OWNER, "name": "موظف ثالث"},
        {"id": "manager", "role": "employee", "created_by": OWNER, "name": "مدير التطبيق"},
    ])
    for actor, permissions in (("third", ["app.page.my_products"]), ("manager", ["app.role.manager"])):
        await db[MOBILE_APP_ACCESS].insert_one({"owner_user_id": OWNER, "user_id": actor,
            "enabled": True, "permissions": permissions})
    async with client_for(db) as client:
        command, _ = await command_for(db, client)
        response = await client.post(BASE + "/requests", json=command)
        assert response.status_code == 200 and response.json()["state"] == "COMMITTED", response.text
        dispatch_id = response.json()["receipt"]["dispatch"]["id"]
        for suffix in ("pdf", "remaining-pdf"):
            url = "/api/supplier-dispatch-pdf-v1/" + dispatch_id + "/" + suffix
            denied = await client.get(url, headers={"X-Actor": "third"})
            assert denied.status_code == 403 and denied.json()["detail"]["code"] == "supplier_dispatch_not_owned_by_employee"
            for actor in (EMPLOYEE, OWNER, "manager"):
                allowed = await client.get(url, headers={"X-Actor": actor})
                assert allowed.status_code == 200 and allowed.content.startswith(b"%PDF"), (actor, allowed.text[:200])
        # Legacy web management remains an explicit, non-bridged authority.
        web = FastAPI()
        web.include_router(make_supplier_dispatch_pdf_router(db, lambda: {
            "id": "web-manager", "created_by": OWNER, "role": "operations"}), prefix="/api")
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=web), base_url="http://synthetic") as web_client:
            for suffix in ("pdf", "remaining-pdf"):
                allowed = await web_client.get("/api/supplier-dispatch-pdf-v1/" + dispatch_id + "/" + suffix)
                assert allowed.status_code == 200 and allowed.content.startswith(b"%PDF")

        share = "/api/supplier-dispatch-share-v1/" + dispatch_id
        for actor in ("third", OWNER):
            assert (await client.get(share, headers={"X-Actor": actor})).status_code == 404
            assert (await client.post(share + "/confirm", headers={"X-Actor": actor})).status_code == 404
            denied_upload = await client.post(share + "/evidence", headers={"X-Actor": actor},
                files={"file": ("proof.png", b"\x89PNG\r\n\x1a\nsynthetic", "image/png")})
            assert denied_upload.status_code == 404
        owner_status = await client.get(share, headers={"X-Actor": EMPLOYEE})
        assert owner_status.status_code == 200
        uploaded = await client.post(share + "/evidence", headers={"X-Actor": EMPLOYEE},
            files={"file": ("proof.png", b"\x89PNG\r\n\x1a\nsynthetic", "image/png")})
        assert uploaded.status_code == 200, uploaded.text
        confirmed = await client.post(share + "/confirm", headers={"X-Actor": EMPLOYEE})
        assert confirmed.status_code == 200, confirmed.text
        assert (await db[EVIDENCE].find_one({}))["created_by"] == EMPLOYEE
        event = await db[dispatch.DISPATCH_EVENTS].find_one({"event_type": "supplier_dispatch_share_confirmed"})
        assert event["actor_id"] == EMPLOYEE
        # A genuine owner still owns its own dispatch; another mobile account
        # cannot borrow that identity to read or confirm its sharing evidence.
        await seed(db, number="A002")
        owner_command, _ = await command_for(db, client, number="A002")
        owner_command["responsible_employee_id"] = OWNER
        owned = await client.post(BASE + "/requests", json=owner_command)
        assert owned.status_code == 200 and owned.json()["state"] == "COMMITTED", owned.text
        owner_share = "/api/supplier-dispatch-share-v1/" + owned.json()["receipt"]["dispatch"]["id"]
        assert (await client.get(owner_share, headers={"X-Actor": OWNER})).status_code == 200
        assert (await client.get(owner_share, headers={"X-Actor": "third"})).status_code == 404
