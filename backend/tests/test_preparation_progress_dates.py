"""File creation and explicit employee assignment are distinct date anchors."""
from copy import deepcopy
from datetime import datetime

import httpx
import pytest
from fastapi import FastAPI
from mongomock_motor import AsyncMongoMockClient

from preparation_progress_dates import employee_assignment_at, file_creation_at, progress_day
from preparation_section_reads import _file_day_registries as FILE_DAY_DATABASE_QUERY
from preparation_section_reads import progress_days, progress_workspace
import mobile_employee_monitoring_workspace_routes as monitoring
import preparation_supplier_dispatch as dispatch


def test_assignment_ignores_supplier_dispatch_receipt_and_untyped_reassigned_time():
    registry = {"created_at": "2026-11-01T22:00:00+03:00", "registered_at": "2026-11-02T01:00:00+03:00"}
    piece = {"assigned_at": registry["registered_at"], "reassigned_at": "2026-11-09T12:00:00Z",
        "sent_to_supplier_at": "2026-11-08T12:00:00Z", "assignment_history": [
            {"assignment_id": "assign_legacy", "assigned_at": "2026-11-03T12:00:00Z"},
            {"reason": "supplier_receipt", "source": "supplier_receiving_invoice_approval", "transferred_at": "2026-11-09T12:00:00Z"},
        ]}
    assert file_creation_at(registry) == registry["created_at"]
    assert employee_assignment_at(piece, registry) == "2026-11-03T12:00:00Z"
    assert employee_assignment_at({"reassigned_at": "2026-11-09", "order_date": "2026-10-01"}, registry) is None
    assert file_creation_at({"registered_at": registry["registered_at"], "file_date": "2026-12-01"}) == registry["registered_at"]
    assert employee_assignment_at({"order_date": "2026-10-01"}, {}) is None


def test_employee_history_uses_latest_explicit_assignment_to_current_employee():
    piece = {"responsible_employee_id": "worker", "assigned_at": "2026-11-01T10:00:00Z",
        "assignment_history": [
            {"assignment_id": "assign_later", "responsible_employee_id": "worker", "assigned_at": "2026-11-04T10:00:00Z"},
            {"assignment_id": "assign_previous", "responsible_employee_id": "worker", "assigned_at": "2026-11-03T10:00:00Z"},
            {"assignment_id": "assign_other", "responsible_employee_id": "other", "assigned_at": "2026-11-08T10:00:00Z"},
            {"assignment_id": "assign_receipt", "reason": "supplier_receipt", "assigned_at": "2026-11-09T10:00:00Z"},
        ]}
    assert employee_assignment_at(piece, {}) == "2026-11-04T10:00:00Z"
    piece["employee_assigned_at"] = "2026-11-05T10:00:00Z"
    assert employee_assignment_at(piece, {}) == "2026-11-05T10:00:00Z"


# Mongomock cannot execute Mongo's $convert + timezone $dateToString. Keep only
# that DB-expression boundary synthetic; actual routes/counts/hydration remain
# real. A separate query-shape test below checks the production Mongo selector.
@pytest.fixture(autouse=True)
def file_day_database_expression(monkeypatch):
    import preparation_section_reads as sections
    async def selected(db, *, user_id, day):
        rows = await db[dispatch.REGISTRY].find({"user_id": user_id}).to_list(None)
        return {row["batch_id"]:row for row in rows
                if progress_day(row.get("registered_at")) == day}
    monkeypatch.setattr(sections, "_file_day_registries", selected)


@pytest.mark.parametrize(("value", "expected"), [
    ("2026-11-02T20:59:59Z", "2026-11-02"),
    ("2026-11-02T21:00:00Z", "2026-11-03"),
    (datetime(2026, 11, 2, 21), "2026-11-03"),
    ("2026-11-03T00:00:00+03:00", "2026-11-03"),
    (None, "__unknown__"),
    ("invalid", "__unknown__"),
])
def test_dates_use_riyadh_midnight_and_naive_utc(value, expected):
    assert progress_day(value) == expected


async def seed():
    db = AsyncMongoMockClient().progress_dates
    await db[dispatch.REGISTRY].insert_one({"user_id": "merchant", "batch_id": "batch", "file_number": "file",
        "created_at": "2026-11-01T22:00:00+03:00", "registered_at": "2026-11-02T01:00:00+03:00"})
    rows = [{"user_id": "merchant", "piece_id": f"piece-{index:02}", "batch_id": "batch", "order_number": "order",
        "order_item_id": "line", "unit_index": index + 1, "responsible_employee_id": "worker",
        "sent_to_supplier_by_id": "worker", "file_number": "file", "product_id": "product", "product_name": "منتج تجريبي",
        "status": "in_progress", "supplier_dispatch_status": "sent", "supplier_id": "supplier", "supplier_dispatch_id": "dispatch",
        "assigned_at": "2026-11-02T01:00:00+03:00", "assignment_history": ([{"assignment_id": "assign_1", "assigned_at": "2026-11-03T09:00:00+03:00"}] if index else []),
        "reassigned_at": "2026-11-08T09:00:00+03:00", "sent_to_supplier_at": "2026-11-07T10:00:00+03:00"} for index in range(13)]
    await db[dispatch.PIECES].insert_many(deepcopy(rows))
    await db[dispatch.DISPATCHES].insert_one({"user_id": "merchant", "id": "dispatch", "sent_by_id": "worker", "status": "sent",
        "supplier_id": "supplier", "piece_count": len(rows), "source_files": [{"cards": [{"piece_id": row["piece_id"]} for row in rows]}]})
    await db.unified_orders.insert_one({"user_id": "merchant", "order_number": "order", "order_status": "processing", "order_date": "2026-10-01"})
    return db


@pytest.mark.asyncio
async def test_dates_use_creation_for_my_products_and_explicit_assignment_for_monitoring():
    db = await seed()
    mine = await progress_days(db, user_id="merchant", employee_id="worker")
    monitor = await progress_days(db, user_id="merchant", employee_id="worker", date_basis="employee_assigned_at")
    assert mine["days"] == [{"key": "2026-11-02"}]
    assert monitor["days"] == [{"key": "2026-11-03"}, {"key": "2026-11-02"}]
    assert mine["date_basis"] == "file_created_at"
    assert monitor["date_basis"] == "employee_assigned_at"


@pytest.mark.asyncio
async def test_same_batch_split_across_days_keeps_counts_and_pages_in_exact_piece_scope(monkeypatch):
    db = await seed()
    seen = []
    async def hydrate(_db, **kwargs):
        seen.append(kwargs)
        assert len(kwargs["piece_ids"]) <= 10
        return {"files": [], "supplier_accounts": []}
    monkeypatch.setattr(dispatch, "_employee_workspace", hydrate)
    first = await progress_workspace(db, user_id="merchant", employee_id="worker", day="2026-11-03", date_basis="employee_assigned_at")
    second = await progress_workspace(db, user_id="merchant", employee_id="worker", day="2026-11-03", after=first["next_cursor"], date_basis="employee_assigned_at")
    assert first["progress_day_summary"]["assigned"] == 12
    assert first["progress_day_summary"]["remaining"] == second["progress_day_summary"]["remaining"] == 12
    assert seen[0]["piece_ids"] == [f"piece-{index:02}" for index in range(1, 11)]
    assert seen[1]["piece_ids"] == ["piece-11", "piece-12"]
    assert "piece-00" not in first["progress_active_piece_ids"]
    earlier = await progress_workspace(db, user_id="merchant", employee_id="worker", day="2026-11-02", date_basis="employee_assigned_at")
    assert earlier["progress_day_summary"]["assigned"] == 1
    assert seen[-1]["piece_ids"] == ["piece-00"]
    mine = await progress_workspace(db, user_id="merchant", employee_id="worker", day="2026-11-02")
    assert mine["progress_day_summary"]["assigned"] == 13


@pytest.mark.asyncio
async def test_http_routes_and_real_card_hydration_use_the_same_date_scope(monkeypatch):
    import preparation_route_history as route_history

    monkeypatch.setattr(route_history, "_INSTALLED", False)
    monkeypatch.setattr(dispatch, "_employee_workspace", dispatch._employee_workspace)
    route_history.install_supplier_dispatch_route_guard()
    db = await seed()
    await db[monitoring.EMPLOYEES].insert_one({"user_id": "merchant", "id": "employee-row",
        "account_user_id": "worker", "display_name": "موظف"})
    async def access(*_args, **_kwargs):
        return {"permissions": [monitoring.MONITORING_PERMISSION]}
    monkeypatch.setattr(monitoring, "mobile_app_access_for_user", access)
    actor = {"id": "merchant", "role": "owner", "_mobile_actor_id": "worker"}
    app = FastAPI()
    app.include_router(dispatch.make_preparation_supplier_dispatch_router(db, lambda: actor))
    app.include_router(monitoring.make_mobile_employee_monitoring_workspace_router(db, lambda: actor))
    hydrated = []
    original = dispatch.annotate_waiting_pieces
    async def observe(_db, *, user_id, pieces):
        hydrated.append(len(pieces))
        assert len(pieces) <= 10
        return await original(_db, user_id=user_id, pieces=pieces)
    monkeypatch.setattr(dispatch, "annotate_waiting_pieces", observe)
    prefix = "/mobile/operations-monitoring/preparation/employee-row/"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        mine = await client.get("/supplier-dispatch-v1/progress-days")
        watch = await client.get(prefix + "progress-days")
        assert mine.status_code == watch.status_code == 200
        assert mine.json()["days"] == [{"key": "2026-11-03"}, {"key": "2026-11-02"}]
        assert mine.json()["date_basis"] == "employee_assigned_at"
        assert watch.json()["days"] == [{"key": "2026-11-03"}, {"key": "2026-11-02"}]
        assert watch.json()["date_basis"] == "employee_assigned_at"
        assert hydrated == []
        after = ""
        loaded = []
        for expected_count in (10, 2):
            response = await client.get(prefix + "progress-workspace", params={"day": "2026-11-03", "after": after})
            assert response.status_code == 200, response.text
            result = response.json()
            cards = [card for row in result["files"] for card in row["products"]]
            assert len(cards) == expected_count
            assert all(row["file_created_at"] == "2026-11-01T19:00:00+00:00" for row in result["files"])
            assert all(card["employee_assigned_at"] == "2026-11-03T06:00:00+00:00" for card in cards)
            assert all(card["employee_assigned_at"] == "2026-11-03T06:00:00+00:00"
                       for account in result["supplier_accounts"] for card in account["products"])
            assert result["progress_day_summary"]["assigned"] == result["progress_day_summary"]["remaining"] == 12
            assert result["progress_supplier_totals"] == {"supplier": 12}
            assert result["date_basis"] == "employee_assigned_at"
            # Supplier-file history still contains its whole immutable membership.
            assert result["supplier_accounts"][0]["dispatches"][0]["progress"]["remaining"] == 13
            loaded.extend(card["piece_id"] for card in cards)
            after = result["next_cursor"]
        assert after is None
        assert loaded == [f"piece-{index:02}" for index in range(1, 13)]
        assert hydrated == [10, 2]
        own_page = await client.get("/supplier-dispatch-v1/progress-workspace", params={"day": "2026-11-03"})
        assert own_page.status_code == 200, own_page.text
        assert own_page.json()["date_basis"] == "employee_assigned_at"
        assert own_page.json()["progress_day_summary"]["assigned"] == 12


@pytest.mark.asyncio
async def test_midnight_selection_counts_and_cards_match_directory():
    db = await seed()
    await db[dispatch.REGISTRY].update_one({"batch_id": "batch"}, {"$set": {"created_at": datetime(2026, 11, 1, 21)}})
    await db[dispatch.PIECES].update_many({}, {"$set": {"assignment_history": [], "assigned_at": "2026-11-02T20:59:59Z"}})
    await db[dispatch.PIECES].update_one({"piece_id": "piece-00"}, {"$set": {"assigned_at": datetime(2026, 11, 2, 21)}})
    mine = await progress_days(db, user_id="merchant", employee_id="worker")
    assert mine["days"] == [{"key": "2026-11-02"}]
    days = await progress_days(db, user_id="merchant", employee_id="worker", date_basis="employee_assigned_at")
    assert days["days"] == [{"key": "2026-11-03"}, {"key": "2026-11-02"}]
    page = await progress_workspace(db, user_id="merchant", employee_id="worker", day="2026-11-03", date_basis="employee_assigned_at")
    assert page["progress_day_summary"]["assigned"] == 1
    assert page["progress_active_piece_ids"] == ["piece-00"]
    assert [card["piece_id"] for row in page["files"] for card in row["products"]] == ["piece-00"]
    assert page["files"][0]["file_created_at"] == "2026-11-01T21:00:00+00:00"
    assert page["files"][0]["products"][0]["employee_assigned_at"] == "2026-11-02T21:00:00+00:00"


@pytest.mark.asyncio
async def test_missing_dates_remain_explicit_null_in_unknown_directory_and_cards():
    db = await seed()
    await db[dispatch.REGISTRY].update_many({}, {"$unset": {"created_at": "", "registered_at": ""},
        "$set": {"file_date": "2026-12-01"}})
    await db[dispatch.PIECES].update_many({}, {"$unset": {"assigned_at": "", "assignment_history": ""}})
    for basis in ("file_created_at", "employee_assigned_at"):
        days = await progress_days(db, user_id="merchant", employee_id="worker", date_basis=basis)
        assert days["days"] == [{"key": "__unknown__"}]
        page = await progress_workspace(db, user_id="merchant", employee_id="worker", day="__unknown__", date_basis=basis)
        assert page["progress_day_summary"]["assigned"] == 13
        assert all(row["file_created_at"] is None for row in page["files"])
        assert all(card["employee_assigned_at"] is None for row in page["files"] for card in row["products"])


@pytest.mark.asyncio
async def test_directory_does_not_hydrate_or_add_received_terminal_or_undispatched_dates(monkeypatch):
    db = await seed()
    await db[dispatch.PIECES].update_one({"piece_id": "piece-00"}, {"$set": {"status": "received", "supplier_dispatch_status": "received"}})
    original = await db[dispatch.PIECES].find_one({"piece_id": "piece-01"}, {"_id": 0})
    await db[dispatch.PIECES].insert_one({**original, "piece_id": "never-dispatched",
        "assignment_history": [], "assigned_at": "2026-11-09T12:00:00Z"})
    async def forbidden(*_args, **_kwargs):
        raise AssertionError("directory reads must not hydrate products or current order DTOs")
    monkeypatch.setattr(dispatch, "annotate_waiting_pieces", forbidden)
    days = await progress_days(db, user_id="merchant", employee_id="worker", date_basis="employee_assigned_at")
    assert days["days"] == [{"key": "2026-11-03"}]
    await db.unified_orders.update_one({"order_number": "order"}, {"$set": {"order_status": "completed"}})
    days = await progress_days(db, user_id="merchant", employee_id="worker", date_basis="employee_assigned_at")
    assert days["days"] == []


@pytest.mark.asyncio
async def test_employee_assignment_navigation_never_reads_registry_dates(monkeypatch):
    import preparation_section_reads as sections
    db = await seed()
    async def forbidden(*args, **kwargs):
        raise AssertionError("assignment date navigation must not query registry dates")
    monkeypatch.setattr(sections, "_registry_dates", forbidden)
    monkeypatch.setattr(sections, "_file_day_registries", forbidden)
    monkeypatch.setattr(dispatch, "_employee_workspace", lambda *args, **kwargs: _empty_workspace())
    assert (await progress_days(db,user_id="merchant",employee_id="worker",date_basis="employee_assigned_at"))["days"]
    page = await progress_workspace(db,user_id="merchant",employee_id="worker",day="2026-11-03",date_basis="employee_assigned_at")
    assert page["progress_day_summary"]["assigned"] == 12


async def _empty_workspace():
    return {"files":[], "supplier_accounts":[]}


@pytest.mark.asyncio
async def test_file_date_filters_batches_before_piece_metadata_and_keeps_received(monkeypatch):
    db = await seed()
    await db[dispatch.PIECES].update_one({"piece_id":"piece-00"}, {"$set":{"status":"received","supplier_dispatch_status":"received"}})
    queries=[]
    collection=db[dispatch.PIECES]
    original=collection.find
    def observed(query, *args, **kwargs):
        queries.append(query)
        return original(query,*args,**kwargs)
    from types import SimpleNamespace
    proxy = SimpleNamespace(find=observed)
    class QueryObservedDb:
        def __getitem__(self, name):
            return proxy if name == dispatch.PIECES else db[name]
        def __getattr__(self, name):
            return getattr(db, name)
    monkeypatch.setattr(dispatch,"_employee_workspace",lambda *args,**kwargs:_empty_workspace())
    page=await progress_workspace(QueryObservedDb(),user_id="merchant",employee_id="worker",day="2026-11-02")
    assert queries[0]["batch_id"] == {"$in":["batch"]}
    assert queries[0]["status"] == {"$ne":"cancelled"}
    assert page["progress_day_summary"]["assigned"] == 13
    assert page["progress_day_summary"]["ready"] == 1
    assert page["progress_day_summary"]["remaining"] == 12


@pytest.mark.asyncio
@pytest.mark.parametrize('day', ['2026-11-01', '__unknown__'])
async def test_file_day_mongo_expression_scopes_tenant_and_riyadh_day(day):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, Mock
    collection = SimpleNamespace(find=Mock(return_value=SimpleNamespace(to_list=AsyncMock(return_value=[]))))
    await FILE_DAY_DATABASE_QUERY({dispatch.REGISTRY:collection}, user_id='merchant',day=day)
    query, projection = collection.find.call_args.args
    assert query['user_id'] == 'merchant'
    expression, selected_day = query['$expr']['$eq']
    assert selected_day == day
    conversion = expression['$dateToString']
    assert conversion['timezone'] == 'Asia/Riyadh'
    assert conversion['onNull'] == '__unknown__'
    assert conversion['date']['$convert']['input'] == '$registered_at'
    assert conversion['date']['$convert']['onError'] is None
    assert set(projection) == {'_id','batch_id','created_at','registered_at'}


@pytest.mark.asyncio
@pytest.mark.parametrize('status,eligible', [
    ('reviewed',True),('تمت المراجعة',True),('تم المراجعة',True),
    ('processing',True),('in_progress',True),('قيد التنفيذ',True),
    ('under_review',False),('waiting_review',False),('انتظار المراجعة',False),
    ('completed',False),('delivered',False),('shipping',False),('shipped',False),
    ('cancelled',False),('refunded',False),('unknown',False),('',False),
])
async def test_employee_dates_and_products_require_current_reviewed_or_processing(monkeypatch,status,eligible):
    db = await seed()
    await db.unified_orders.update_one({'order_number':'order'}, {'$set':{'order_status':status}})
    monkeypatch.setattr(dispatch,"_employee_workspace",lambda *args,**kwargs:_empty_workspace())
    days=await progress_days(db,user_id='merchant',employee_id='worker',date_basis='employee_assigned_at')
    page=await progress_workspace(db,user_id='merchant',employee_id='worker',day='2026-11-03',date_basis='employee_assigned_at')
    assert bool(days['days']) is eligible
    assert page['progress_day_summary']['assigned'] == (12 if eligible else 0)
    assert bool(page['progress_active_piece_ids']) is eligible
    assert bool(page['progress_supplier_totals']) is eligible


@pytest.mark.asyncio
async def test_current_status_change_removes_assignment_day_despite_supplier_history(monkeypatch):
    db = await seed()
    monkeypatch.setattr(dispatch,"_employee_workspace",lambda *args,**kwargs:_empty_workspace())
    async def read():
        return await progress_days(db,user_id='merchant',employee_id='worker',date_basis='employee_assigned_at')
    assert (await read())['days']
    for status in ('under_review','completed','shipped','cancelled'):
        await db.unified_orders.update_one({'order_number':'order'}, {'$set':{'order_status':status}})
        assert (await read())['days'] == []
        page=await progress_workspace(db,user_id='merchant',employee_id='worker',day='2026-11-03',date_basis='employee_assigned_at')
        assert page['progress_day_summary']['assigned'] == 0
        assert page['progress_active_piece_ids'] == []
    # Legacy file-date callers retain their previous nonterminal semantics.
    await db.unified_orders.update_one({'order_number':'order'}, {'$set':{'order_status':'under_review'}})
    assert (await progress_days(db,user_id='merchant',employee_id='worker'))['days']


@pytest.mark.asyncio
async def test_custom_status_uses_order_engine_precedence_for_assignment_dates(monkeypatch):
    db = await seed()
    await db.unified_orders.update_one({'order_number':'order'}, {'$set':{
        'order_status':'under_review','raw_by_source.salla_direct.status':{
            'slug':'under_review','customized':{'name':'تمت المراجعة'}}}})
    assert (await progress_days(db,user_id='merchant',employee_id='worker',date_basis='employee_assigned_at'))['days']
    # A current root terminal status beats an obsolete customized/raw snapshot.
    for status in ('completed', 'shipped'):
        await db.unified_orders.update_one({'order_number':'order'}, {'$set':{'order_status':status}})
        assert (await progress_days(db,user_id='merchant',employee_id='worker',date_basis='employee_assigned_at'))['days'] == []
    await db.unified_orders.delete_many({})
    assert (await progress_days(db,user_id='merchant',employee_id='worker',date_basis='employee_assigned_at'))['days'] == []


@pytest.mark.asyncio
async def test_selected_day_is_employee_assignment_and_dispatch_is_membership_only(monkeypatch):
    db = await seed()
    monkeypatch.setattr(dispatch, "_employee_workspace", lambda *args, **kwargs: _empty_workspace())
    await db[dispatch.PIECES].update_many({}, {"$set": {"employee_assigned_at": "2026-11-05T09:00:00+03:00"}})
    await db[dispatch.DISPATCHES].update_many({}, {"$set": {"sent_at": "2026-11-07T12:00:00+03:00"}})
    for day, count in (("2026-11-05", 13), ("2026-11-07", 0), ("2026-10-01", 0)):
        page = await progress_workspace(db, user_id="merchant", employee_id="worker", day=day, date_basis="employee_assigned_at")
        assert page["progress_day_summary"]["assigned"] == count
    for change in ({"sent_by_id": "another-worker"}, {"sent_by_id": "worker", "status": "building"}, {"status": "sent", "source_files": []}):
        await db[dispatch.DISPATCHES].update_many({}, {"$set": change})
        days = await progress_days(db, user_id="merchant", employee_id="worker", date_basis="employee_assigned_at")
        page = await progress_workspace(db, user_id="merchant", employee_id="worker", day="2026-11-05", date_basis="employee_assigned_at")
        assert days["days"] == []
        assert page["progress_day_summary"]["assigned"] == 0
