"""Independent HTTP/real-transaction acceptance for combined preparation assignment.

The canonical seed is shared with the writer's test; assertions and HTTP app are
independent. Only the remote provider boundary is replaced. Planning, mobile
permissions, Mongo transactions, materialization, supplier dispatch, readback,
employee readers and both supplier PDFs use the production implementations.
Run beside an isolated replica set; no MongoMock or production credentials.
"""
import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI, Header, HTTPException, Request
from motor.motor_asyncio import AsyncIOMotorClient

import preparation_assignment_v2 as assignment
import preparation_file_registry as registry
import preparation_piece_line_services as line_services
import preparation_piece_operations as pieces
import preparation_supplier_dispatch as dispatch
import reviewed_preparation_batches as batches
import reviewed_products_catalog as catalog
from mobile_app_permissions import MOBILE_APP_ACCESS
from operational_atomic import operational_owner
from supplier_dispatch_pdf import make_supplier_dispatch_pdf_router
from test_preparation_assignment_v2 import seed, OWNER, REVIEWER, EMPLOYEE, SUPPLIER


BASE = "/api/preparation-assignment-v2"
PUBLICATIONS = (registry.REGISTRY, batches.BATCHES, catalog.PREPARATION_UNIT_ALLOCATIONS,
                pieces.PIECES, pieces.PIECE_EVENTS, dispatch.DISPATCHES,
                dispatch.DISPATCH_EVENTS, "order_review_events")
SAMPLE = os.environ.get("COMBINED_ASSIGNMENT_CONTRACT_SAMPLE")


def sample(key, value):
    if SAMPLE:
        path = Path(SAMPLE)
        current = json.loads(path.read_text()) if path.exists() else {}
        current[key] = value
        path.write_text(json.dumps(current, ensure_ascii=False, indent=2) + "\n")


@pytest_asyncio.fixture
async def db():
    uri = os.environ.get("MZ2_TEST_MONGO_URI", "")
    assert uri.startswith("mongodb://127.0.0.1:"), "an isolated real replica set is required"
    client = AsyncIOMotorClient(uri, tz_aware=True, serverSelectionTimeoutMS=4000)
    database = client["combined_assignment_independent_" + uuid4().hex[:24]]
    try:
        hello = await database.command("hello")
        assert hello.get("setName") and hello.get("isWritablePrimary") and hello.get("logicalSessionTimeoutMinutes")
        yield database
    finally:
        await client.drop_database(database.name)
        client.close()


@pytest.fixture(autouse=True)
def real_helpers_and_provider_boundary(monkeypatch):
    import preparation_route_history as route_history
    import supplier_receipt_employee_custody as custody
    import supplier_receiving_routes as receiving

    # Install the real wrappers while preserving the surrounding test process.
    # Importing the native bridge during collection would install these hooks
    # before any fixture can restore them, changing unrelated legacy test scope.
    monkeypatch.setattr(dispatch, "_employee_workspace", dispatch._employee_workspace)
    monkeypatch.setattr(receiving, "supplier_service_completion_update", receiving.supplier_service_completion_update)
    monkeypatch.setattr(route_history, "_INSTALLED", route_history._INSTALLED)
    monkeypatch.setattr(custody, "_INSTALLED", custody._INSTALLED)
    route_history.install_supplier_dispatch_route_guard()
    custody.install_supplier_receipt_employee_custody()
    for module in (pieces, line_services, registry, batches):
        for name, value in list(vars(module).items()):
            if callable(value) or name.startswith("_ORIGINAL_") or name == "_INSTALLED":
                monkeypatch.setattr(module, name, value)
    pieces.install_preparation_piece_operations()
    line_services.install_preparation_piece_line_services()
    calls = []

    async def provider_status(*args, **kwargs):
        from operational_atomic import _ACTIVE
        assert _ACTIVE.get() is None, "remote provider writes cannot run inside local publication"
        calls.append((args, kwargs))
        return "sent", None

    async def provider_read(*args, **kwargs):
        from operational_atomic import _ACTIVE
        assert _ACTIVE.get() is None, "remote provider reads cannot run inside local publication"
        return {"ok": True, "found": True}

    monkeypatch.setattr(pieces, "_sync_salla_in_progress", provider_status)
    monkeypatch.setattr(batches, "refresh_order_from_salla", provider_read)
    return calls


def app_for(db):
    async def current_user(request: Request, x_actor: str = Header(default=REVIEWER)):
        from mobile_app_request_context import mobile_app_request_user

        account = await db.users.find_one({"id": x_actor}, {"_id": 0})
        if not account:
            raise HTTPException(401, "synthetic account unavailable")
        account["_session_client"] = "amasi_mobile"
        return await mobile_app_request_user(db, account, path=request.url.path, method=request.method)

    app = FastAPI()
    app.include_router(assignment.make_preparation_assignment_v2_router(db, current_user), prefix="/api")
    app.include_router(dispatch.make_preparation_supplier_dispatch_router(db, current_user), prefix="/api")
    app.include_router(make_supplier_dispatch_pdf_router(db, current_user), prefix="/api")
    return app


def client_for(db, *, app=None):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app or app_for(db), raise_app_exceptions=False),
                             base_url="http://synthetic-independent")


async def command_for(db, client, *, mode="assign", number="A001", take=None):
    # The existing read-side catalogue gives real, current physical-unit tokens.
    context = await catalog.load_reviewed_product_context(db, user_id=OWNER, limit=1, order_numbers=[number])
    rows = context["catalog"]["products"]
    if take is not None:
        rows = rows[:take]
    assert rows
    response = await client.get(BASE + "/options")
    assert response.status_code == 200, response.text
    options = response.json()
    command = {"client_request_id": str(uuid4()), "file_title": "إسناد مستقل تجريبي",
               "responsible_employee_id": EMPLOYEE, "expected_quantity": len(rows),
               "selected_product_count": len(rows), "schedule_mode": "automatic", "required_due_at": None,
               "selections": [{"group_key": row["group_key"], "quantity": 1,
                               "revision": row["revision"], "order_number": number} for row in rows],
               "supplier_assignment": {"mode": mode, **({"supplier_id": SUPPLIER} if mode == "assign" else {})},
               "expected_session_scope": options["session_scope"]}
    return command, options


async def assert_no_publication(db):
    for collection in PUBLICATIONS:
        assert await db[collection].count_documents({}) == 0, collection


async def publication_counts(db):
    return {name: await db[name].count_documents({}) for name in PUBLICATIONS}


async def add_account(db, account_id, *, owner=OWNER, permission="app.page.my_products"):
    await db.users.insert_one({"id": account_id, "role": "employee", "created_by": owner,
                               "name": "موظف آخر تجريبي", "email": account_id + "@example.invalid"})
    await db[MOBILE_APP_ACCESS].insert_one({"owner_user_id": owner, "user_id": account_id,
                                          "enabled": True, "permissions": [permission]})


@pytest.mark.asyncio
async def test_actual_http_assign_cross_assignee_reads_audit_pdfs_and_immutable_replay(db):
    await seed(db, quantity=2)
    await add_account(db, "unrelated-worker")
    await add_account(db, "other-reviewer", permission="app.page.reviewed_preparation")
    async with client_for(db) as client:
        command, options = await command_for(db, client)
        assert options["actor"]["id"] == REVIEWER
        assert options["capabilities"] == {"assign": True, "defer": True, "atomic_publication": True}
        assert [item["id"] for item in options["suppliers"]] == [SUPPLIER]
        initial_counts = await publication_counts(db)
        absent = await client.get(BASE + "/requests/" + command["client_request_id"])
        assert absent.json()["state"] == "ABSENT" and absent.json()["command"] is None
        assert initial_counts == await publication_counts(db)
        response = await client.post(BASE + "/requests", json=command)
        assert response.status_code == 200, response.text
        result = response.json()
        assert result["state"] == "COMMITTED" and result["actor"]["id"] == REVIEWER
        receipt = result["receipt"]
        ids = set(receipt["piece_ids"])
        assert len(ids) == receipt["piece_count"] == 2
        assert ids == set(receipt["batch"]["piece_ids"]) == set(receipt["dispatch"]["piece_ids"])
        assert receipt["responsible_employee_id"] == EMPLOYEE != REVIEWER
        stored_dispatch = await db[dispatch.DISPATCHES].find_one({"id": receipt["dispatch"]["id"]})
        assert stored_dispatch["sent_by_id"] == EMPLOYEE
        assert stored_dispatch["performed_by_id"] == REVIEWER
        assert stored_dispatch["performed_by_name"] == "موظف الإسناد"
        units = await db[pieces.PIECES].find({}).to_list(10)
        assert {row["piece_id"] for row in units} == ids
        assert len({(row["order_number"], row["order_item_id"], row["unit_index"]) for row in units}) == 2
        for row in units:
            assert row["responsible_employee_id"] == EMPLOYEE
            assert row["sent_to_supplier_by_id"] == REVIEWER
            assert row["supplier_id"] == SUPPLIER and row["status"] == "in_progress"
            # Canonical cards keep size/color separately from additional options.
            assert any(spec.get("name") == "المقاس" and spec.get("value") == "8"
                       for spec in row["specifications_snapshot"])
            service = row["services"][0]
            assert service["service_id"] == "service" and service["status"] == "pending"
            assert service["completed_quantity"] == 0 and row["completed_service_count"] == 0
        for collection in (pieces.PIECE_EVENTS, dispatch.DISPATCH_EVENTS, "order_review_events"):
            events = await db[collection].find({}).to_list(20)
            assert events and {event["actor_id"] for event in events} == {REVIEWER}
        headers = {"X-Actor": EMPLOYEE}
        days = await client.get("/api/supplier-dispatch-v1/progress-days?supplier_dates=true", headers=headers)
        assert days.status_code == 200 and len(days.json()["days"]) == 1, days.text
        progress = await client.get("/api/supplier-dispatch-v1/progress-workspace", headers=headers,
                                    params={"supplier_dates": "true", "day": days.json()["days"][0]["key"]})
        assert progress.status_code == 200, progress.text
        account = progress.json()["supplier_accounts"][0]
        assert account["supplier_id"] == SUPPLIER
        assert {row["piece_id"] for row in account["products"]} == ids
        assert account["sent_quantity"] == 2
        pdf_proof = {}
        for suffix in ("pdf", "remaining-pdf"):
            url = "/api/supplier-dispatch-pdf-v1/" + receipt["dispatch"]["id"] + "/" + suffix
            pdf = await client.get(url, headers=headers)
            assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF"), pdf.text[:200]
            assert pdf.headers["cache-control"] == "private, no-store"
            assert len(pdf.content) > 500
            pdf_proof[suffix] = {"status": pdf.status_code, "bytes": len(pdf.content), "pdf_header": True}
            foreign_pdf = await client.get(url, headers={"X-Actor": "unrelated-worker"})
            assert foreign_pdf.status_code == 403, {"status": foreign_pdf.status_code,
                "content_type": foreign_pdf.headers.get("content-type"), "pdf_header": foreign_pdf.content.startswith(b"%PDF")}
        foreign_days = await client.get("/api/supplier-dispatch-v1/progress-days?supplier_dates=true",
                                        headers={"X-Actor": "unrelated-worker"})
        assert foreign_days.status_code == 200 and foreign_days.json()["days"] == []
        # The assignment-only actor is not silently granted ordinary MyProducts writes.
        old_dispatch = await client.post("/api/supplier-dispatch-v1/dispatches", json={})
        assert old_dispatch.status_code == 403, old_dispatch.text
        counts = await publication_counts(db)
        recovered = await client.get(BASE + "/requests/" + command["client_request_id"])
        replay = await client.post(BASE + "/requests", json=command)
        assert recovered.json() == result == replay.json()
        assert counts == await publication_counts(db)
        other_actor = await client.get(BASE + "/requests/" + command["client_request_id"], headers={"X-Actor": "other-reviewer"})
        assert other_actor.status_code == 409 and "receipt" not in other_actor.json(), other_actor.text
        sample("proof", {"transport": "FastAPI ASGI HTTP", "mongo": (await db.command("buildInfo"))["version"],
                          "real_replica_transaction": True, "live_writes": 0, "external_provider_replaced": True})
        sample("assign", {"options": options, "command": command, "absent": absent.json(), "committed": result,
                           "recovered": recovered.json(), "replayed": replay.json(), "days": days.json(),
                           "progress": progress.json(), "pdf": pdf_proof})


@pytest.mark.asyncio
async def test_actual_http_explicit_defer_waiting_only_and_service_obligations_remain(db):
    await seed(db)
    async with client_for(db) as client:
        command, options = await command_for(db, client, mode="defer")
        response = await client.post(BASE + "/requests", json=command)
        assert response.status_code == 200, response.text
        result = response.json()
        receipt = result["receipt"]
        assert result["state"] == "COMMITTED" and receipt["supplier_assignment"] == {"mode": "defer"}
        assert receipt["destination"] == "waiting" and receipt["dispatch"] is None
        assert await db[dispatch.DISPATCHES].count_documents({}) == 0
        row = await db[pieces.PIECES].find_one({})
        assert row["responsible_employee_id"] == EMPLOYEE and row["status"] == "assigned"
        assert not row.get("supplier_id") and row["services"][0]["status"] == "pending"
        waiting = await client.get("/api/supplier-dispatch-v1/waiting-page", headers={"X-Actor": EMPLOYEE})
        assert waiting.status_code == 200 and receipt["piece_ids"][0] in waiting.text, waiting.text
        days = await client.get("/api/supplier-dispatch-v1/progress-days?supplier_dates=true", headers={"X-Actor": EMPLOYEE})
        assert days.status_code == 200 and days.json()["days"] == [], days.text
        recovered = await client.get(BASE + "/requests/" + command["client_request_id"])
        assert recovered.json() == result
        sample("defer", {"options": options, "command": command, "committed": result,
                          "recovered": recovered.json(), "waiting": waiting.json(), "days": days.json()})


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["absent_mode", "empty_mode", "null_mode", "blank_supplier", "defer_supplier",
                                     "blank_employee", "extra_field", "duplicate_units", "wrong_count", "bad_revision"])
async def test_invalid_v2_commands_never_fall_back_or_publish(db, mutation):
    await seed(db)
    async with client_for(db) as client:
        command, _ = await command_for(db, client)
        if mutation == "absent_mode": del command["supplier_assignment"]
        elif mutation == "empty_mode": command["supplier_assignment"] = {}
        elif mutation == "null_mode": command["supplier_assignment"] = None
        elif mutation == "blank_supplier": command["supplier_assignment"]["supplier_id"] = "   "
        elif mutation == "defer_supplier": command["supplier_assignment"] = {"mode": "defer", "supplier_id": SUPPLIER}
        elif mutation == "blank_employee": command["responsible_employee_id"] = " "
        elif mutation == "extra_field": command["owner_user_id"] = "another-owner"
        elif mutation == "duplicate_units":
            command["selections"] *= 2
            command["expected_quantity"] = command["selected_product_count"] = 2
        elif mutation == "wrong_count": command["expected_quantity"] = 2
        elif mutation == "bad_revision": command["selections"][0]["revision"] = "stale"
        response = await client.post(BASE + "/requests", json=command)
        assert response.status_code == 422, response.text
        assert response.json()["detail"]["not_applied"] is True
        await assert_no_publication(db)
        assert await db[assignment.REQUESTS].count_documents({}) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["assignee_disabled", "assignee_deleted", "assignee_foreign", "assignee_no_grant",
                                   "actor_no_grant", "supplier_inactive", "supplier_foreign", "supplier_category",
                                   "old_scope", "old_revision"])
async def test_live_permission_identity_supplier_and_revision_rejection_is_all_or_nothing(db, change):
    await seed(db)
    async with client_for(db) as client:
        command, _ = await command_for(db, client)
        if change == "assignee_disabled": await db.users.update_one({"id": EMPLOYEE}, {"$set": {"disabled": True}})
        elif change == "assignee_deleted": await db.users.update_one({"id": EMPLOYEE}, {"$set": {"deleted_at": datetime.now(timezone.utc)}})
        elif change == "assignee_foreign": await db.users.update_one({"id": EMPLOYEE}, {"$set": {"created_by": "foreign-owner"}})
        elif change in {"assignee_no_grant", "actor_no_grant"}:
            await db[MOBILE_APP_ACCESS].update_one({"user_id": EMPLOYEE if change.startswith("assignee") else REVIEWER},
                                                   {"$set": {"permissions": []}})
        elif change == "supplier_inactive": await db[dispatch.MEZAN_SUPPLIERS_V2].update_one({"id": SUPPLIER}, {"$set": {"status": "inactive"}})
        elif change == "supplier_foreign": await db[dispatch.MEZAN_SUPPLIERS_V2].update_one({"id": SUPPLIER}, {"$set": {"user_id": "foreign-owner"}})
        elif change == "supplier_category": await db[dispatch.MEZAN_SUPPLIERS_V2].update_one({"id": SUPPLIER}, {"$set": {"category_ids": ["other-category"]}})
        elif change == "old_scope": command["expected_session_scope"] = "a" * 64
        elif change == "old_revision": command["selections"][0]["revision"] = "a" * 64
        response = await client.post(BASE + "/requests", json=command)
        assert response.status_code in {403, 404, 409}, response.text
        await assert_no_publication(db)


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["employee", "supplier", "mode", "unit", "title"])
async def test_same_request_identity_rejects_changed_command_without_duplicate(db, field):
    await seed(db)
    async with client_for(db) as client:
        command, _ = await command_for(db, client)
        original = await client.post(BASE + "/requests", json=command)
        assert original.status_code == 200, original.text
        counts = await publication_counts(db)
        changed = deepcopy(command)
        if field == "employee": changed["responsible_employee_id"] = "other-worker"
        elif field == "supplier": changed["supplier_assignment"]["supplier_id"] = "other-supplier"
        elif field == "mode": changed["supplier_assignment"] = {"mode": "defer"}
        elif field == "unit": changed["selections"][0]["order_number"] = "another-order"
        elif field == "title": changed["file_title"] = "عنوان آخر"
        response = await client.post(BASE + "/requests", json=changed)
        assert response.status_code == 409, response.text
        assert response.json()["detail"]["code"] == "preparation_assignment_request_conflict"
        assert counts == await publication_counts(db)
        recovered = await client.get(BASE + "/requests/" + command["client_request_id"])
        assert recovered.json() == original.json()


@pytest.mark.asyncio
async def test_true_replica_transaction_rolls_back_every_effect_even_after_receipt_then_same_id_recovers(db, monkeypatch):
    await seed(db, quantity=2)
    async with client_for(db) as client:
        command, _ = await command_for(db, client)
        original = assignment._publish_assignment
        reached = []

        async def fail_after_all_effects(tx, *args, **kwargs):
            result = await original(tx, *args, **kwargs)
            assert result["state"] == "COMMITTED"
            assert await tx[pieces.PIECES].count_documents({"user_id": OWNER}) == 2
            assert await tx[dispatch.DISPATCHES].count_documents({"user_id": OWNER}) == 1
            reached.append(result["receipt"])
            raise RuntimeError("synthetic failure after writes, before transaction commit")

        monkeypatch.setattr(assignment, "_publish_assignment", fail_after_all_effects)
        failed = await client.post(BASE + "/requests", json=command)
        assert failed.status_code == 500 and len(reached) == 1, failed.text
        await assert_no_publication(db)
        pending = await client.get(BASE + "/requests/" + command["client_request_id"])
        assert pending.json()["state"] == "PENDING" and pending.json()["receipt"] is None
        assert pending.json()["command"]["supplier_assignment"] == command["supplier_assignment"]
        # The first request transaction persists; the second transaction's fences do not.
        assert (await db.users.find_one({"id": EMPLOYEE}))["preparation_assignment_fence"] == 1
        assert (await db[MOBILE_APP_ACCESS].find_one({"user_id": EMPLOYEE}))["preparation_assignment_fence"] == 1
        monkeypatch.setattr(assignment, "_publish_assignment", original)
        future = datetime.now(timezone.utc) + timedelta(seconds=assignment.PREPARATION_LEASE_SECONDS + 2)
        monkeypatch.setattr(assignment, "_now", lambda: future)
        retried = await client.post(BASE + "/requests", json=command)
        assert retried.status_code == 200 and retried.json()["state"] == "COMMITTED", retried.text
        assert await db[pieces.PIECES].count_documents({}) == 2
        assert await db[dispatch.DISPATCHES].count_documents({}) == 1
        assert retried.json()["receipt"]["piece_ids"] == reached[0]["piece_ids"]
        sample("rollback", {"command": command, "pending": pending.json(), "committed": retried.json(),
                            "all_effects_existed_in_transaction": True, "all_effects_absent_after_abort": True})


@pytest.mark.asyncio
async def test_concurrent_same_id_is_pending_read_only_until_one_publication(db, monkeypatch):
    await seed(db)
    async with client_for(db) as client:
        command, _ = await command_for(db, client)
        original = assignment._prepare_assignment
        entered, release = asyncio.Event(), asyncio.Event()

        async def pause_preparation(*args, **kwargs):
            prepared = await original(*args, **kwargs)
            entered.set()
            await asyncio.wait_for(release.wait(), timeout=15)
            return prepared

        monkeypatch.setattr(assignment, "_prepare_assignment", pause_preparation)
        first = asyncio.create_task(client.post(BASE + "/requests", json=command))
        try:
            await asyncio.wait_for(entered.wait(), timeout=15)
            second = await client.post(BASE + "/requests", json=command)
            pending = await client.get(BASE + "/requests/" + command["client_request_id"])
            assert second.status_code == 200 and second.json() == pending.json(), second.text
            assert pending.json()["state"] == "PENDING" and pending.json()["receipt"] is None
            await assert_no_publication(db)
        finally:
            release.set()
        result = await first
        assert result.status_code == 200 and result.json()["state"] == "COMMITTED", result.text
        replay = await client.post(BASE + "/requests", json=command)
        assert replay.json() == result.json()
        assert await db[pieces.PIECES].count_documents({}) == 1
        assert await db[dispatch.DISPATCHES].count_documents({}) == 1
        sample("pending", {"command": command, "readback": pending.json(), "committed": result.json()})


@pytest.mark.asyncio
async def test_two_distinct_requests_cannot_assign_the_same_physical_unit(db, monkeypatch):
    await seed(db)
    async with client_for(db) as client:
        first, _ = await command_for(db, client)
        second = {**deepcopy(first), "client_request_id": str(uuid4())}
        original = assignment._prepare_assignment
        ready, release = [], asyncio.Event()

        async def both_prepare(*args, **kwargs):
            prepared = await original(*args, **kwargs)
            ready.append(True)
            if len(ready) == 2:
                release.set()
            await asyncio.wait_for(release.wait(), timeout=15)
            return prepared

        monkeypatch.setattr(assignment, "_prepare_assignment", both_prepare)
        results = await asyncio.gather(client.post(BASE + "/requests", json=first), client.post(BASE + "/requests", json=second))
        assert sorted(row.status_code for row in results) == [200, 409], [row.text for row in results]
        assert sum(row.json().get("state") == "COMMITTED" for row in results) == 1
        for name in (registry.REGISTRY, batches.BATCHES, pieces.PIECES, catalog.PREPARATION_UNIT_ALLOCATIONS, dispatch.DISPATCHES):
            assert await db[name].count_documents({}) == 1, name


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["assignee_permission", "actor_permission", "supplier_inactive", "source_cancelled"])
async def test_live_changes_between_preparation_and_commit_prevent_all_publication(db, monkeypatch, change):
    await seed(db)
    async with client_for(db) as client:
        command, _ = await command_for(db, client)
        original = assignment._prepare_assignment

        async def change_after_preparation(*args, **kwargs):
            prepared = await original(*args, **kwargs)
            if change in {"assignee_permission", "actor_permission"}:
                await db[MOBILE_APP_ACCESS].update_one({"user_id": EMPLOYEE if change == "assignee_permission" else REVIEWER},
                                                       {"$set": {"enabled": False}})
            elif change == "supplier_inactive":
                await db[dispatch.MEZAN_SUPPLIERS_V2].update_one({"id": SUPPLIER}, {"$set": {"status": "inactive"}})
            elif change == "source_cancelled":
                await db.unified_orders.update_one({"order_number": "A001"}, {"$set": {
                    "order_status": "canceled", "raw_by_source.salla_direct.status": {"slug": "canceled", "name": "ملغي"}}})
            return prepared

        monkeypatch.setattr(assignment, "_prepare_assignment", change_after_preparation)
        response = await client.post(BASE + "/requests", json=command)
        assert response.status_code in {403, 404, 409}, response.text
        await assert_no_publication(db)


@pytest.mark.asyncio
async def test_lost_post_response_recovers_same_durable_receipt_without_second_publication(db):
    await seed(db)
    actual_app = app_for(db)
    lost = []

    async def drop_success(scope, receive, send):
        async def guarded_send(message):
            if (scope.get("method") == "POST" and scope.get("path") == BASE + "/requests"
                    and message["type"] == "http.response.start" and message["status"] == 200 and not lost):
                lost.append(True)
                raise ConnectionError("synthetic dropped response after durable commit")
            await send(message)
        await actual_app(scope, receive, guarded_send)

    async with client_for(db, app=drop_success) as client:
        command, _ = await command_for(db, client)
        response = await client.post(BASE + "/requests", json=command)
        assert response.status_code == 500 and lost == [True]
        counts = await publication_counts(db)
        readback = await client.get(BASE + "/requests/" + command["client_request_id"])
        assert readback.status_code == 200 and readback.json()["state"] == "COMMITTED", readback.text
        retried = await client.post(BASE + "/requests", json=command)
        assert retried.status_code == 200 and retried.json() == readback.json()
        assert counts == await publication_counts(db)


@pytest.mark.asyncio
async def test_readback_requires_all_physical_proof_and_never_repairs_on_get(db):
    await seed(db)
    async with client_for(db) as client:
        command, _ = await command_for(db, client)
        committed = await client.post(BASE + "/requests", json=command)
        assert committed.status_code == 200, committed.text
        await db[catalog.PREPARATION_UNIT_ALLOCATIONS].delete_one({})
        counts = await publication_counts(db)
        for _ in range(2):
            response = await client.get(BASE + "/requests/" + command["client_request_id"])
            assert response.status_code == 200 and response.json()["state"] == "PENDING", response.text
            assert response.json()["receipt"] is None
        assert counts == await publication_counts(db)


@pytest.mark.asyncio
async def test_partial_physical_selection_does_not_advance_entire_order_or_finish_services(db, real_helpers_and_provider_boundary):
    await seed(db, quantity=2)
    async with client_for(db) as client:
        command, _ = await command_for(db, client, take=1)
        response = await client.post(BASE + "/requests", json=command)
        assert response.status_code == 200 and response.json()["state"] == "COMMITTED", response.text
        assert await db[pieces.PIECES].count_documents({}) == 1
        assert real_helpers_and_provider_boundary == []
        workflow = await db[catalog.WORKFLOWS].find_one({"order_number": "A001"})
        assert workflow["stage"] == "reviewed"
        row = await db[pieces.PIECES].find_one({})
        assert row["services"][0]["status"] == "pending"


@pytest.mark.asyncio
async def test_required_schedule_preserved_and_timezone_normalized_in_one_publication(db):
    await seed(db)
    async with client_for(db) as client:
        command, options = await command_for(db, client)
        command.update({"schedule_mode": "required", "required_due_at": "2027-01-01T16:25:00+03:00"})
        response = await client.post(BASE + "/requests", json=command)
        assert response.status_code == 200 and response.json()["state"] == "COMMITTED", response.text
        result = response.json()
        expected = datetime(2027, 1, 1, 13, 25, tzinfo=timezone.utc)
        assert datetime.fromisoformat(result["command"]["required_due_at"]) == expected
        assert datetime.fromisoformat(result["receipt"]["file"]["required_due_at"]) == expected
        for collection in (registry.REGISTRY, batches.BATCHES, pieces.PIECES):
            row = await db[collection].find_one({})
            assert row["schedule_mode"] == "required" and row["required_due_at"] == expected
        readback = await client.get(BASE + "/requests/" + command["client_request_id"])
        assert readback.json() == result
        sample("required", {"options": options, "command": command, "committed": result, "recovered": readback.json()})


@pytest.mark.asyncio
async def test_legacy_service_only_supplier_keeps_canonical_category_compatibility(db):
    await seed(db)
    await db[dispatch.MEZAN_SUPPLIERS_V2].update_one({"id": SUPPLIER}, {"$set": {"category_ids": []}})
    async with client_for(db) as client:
        command, options = await command_for(db, client)
        assert options["suppliers"][0]["category_ids"] == []
        assert options["suppliers"][0]["service_ids"] == ["service"]
        response = await client.post(BASE + "/requests", json=command)
        assert response.status_code == 200 and response.json()["state"] == "COMMITTED", response.text
        assert response.json()["receipt"]["dispatch"]["supplier_id"] == SUPPLIER
        row = await db[pieces.PIECES].find_one({})
        assert row["services"][0]["status"] == "pending"


@pytest.mark.asyncio
@pytest.mark.parametrize("target", ["permissions", "user", "order_status", "financial", "stock"])
async def test_assignment_transaction_profile_cannot_widen_authority_or_touch_money_stock_even_if_caught(db, target):
    await seed(db)
    marker = {"user_id": OWNER, "client_request_id": "forbidden-proof-" + uuid4().hex}

    async def attempt(tx):
        await tx[assignment.REQUESTS].insert_one(deepcopy(marker))
        try:
            if target == "permissions":
                await tx[MOBILE_APP_ACCESS].update_one({"owner_user_id": OWNER, "user_id": EMPLOYEE}, {"$set": {"permissions": ["*"]}})
            elif target == "user":
                await tx.users.update_one({"id": EMPLOYEE}, {"$set": {"role": "owner"}})
            elif target == "order_status":
                await tx.unified_orders.update_one({"user_id": OWNER, "order_number": "A001"}, {"$set": {"order_status": "completed"}})
            elif target == "financial":
                await tx["mz2_journal_entries"].insert_one({"user_id": OWNER, "id": "forbidden-finance"})
            elif target == "stock":
                await tx.warehouse_locations.insert_one({"user_id": OWNER, "id": "forbidden-stock"})
        except HTTPException:
            pass

    with pytest.raises(HTTPException):
        await operational_owner(db, OWNER, attempt, profile="preparation_assignment")
    assert await db[assignment.REQUESTS].count_documents(marker) == 0
    assert (await db.users.find_one({"id": EMPLOYEE}))["role"] == "employee"
    assert (await db[MOBILE_APP_ACCESS].find_one({"user_id": EMPLOYEE}))["permissions"] == ["app.page.my_products"]
    assert (await db.unified_orders.find_one({"order_number": "A001"}))["order_status"] == "reviewed"
    assert await db["mz2_journal_entries"].count_documents({}) == 0
    assert await db.warehouse_locations.count_documents({}) == 0
