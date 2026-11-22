"""Synthetic HTTP contracts for employee custody, dated queues and scan receipts."""
from copy import deepcopy
import asyncio
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI, Header
from mongomock_motor import AsyncMongoMockClient

from fulfillment_v2_routes import make_fulfillment_v2_router
from mobile_app_permissions import MOBILE_APP_ACCESS
from mobile_app_request_context import mobile_app_request_user


BASE = "/api/fulfillment-v2/carrier-handoff"
NOW = datetime.now(timezone.utc).replace(microsecond=0)
STAFF = {"X-Actor": "staff"}


@pytest.fixture(autouse=True)
def synthetic_owner_fence(monkeypatch):
    # The in-memory Mongo adapter has no transactions. Exercise the real HTTP
    # callback with owner serialization; operational_atomic has separate tests
    # for its Mongo transaction, ownership and rollback guarantees.
    locks = {}
    async def serialize(db, owner, action, **kwargs):
        from operational_atomic import OperationalDatabase
        async with locks.setdefault(owner, asyncio.Lock()):
            state = {"owner": owner, "profile": "fulfillment", "failed": False, "review_session": None}
            scoped = OperationalDatabase(db, None, state, owner)
            state["db"] = scoped
            result = await action(scoped)
            assert state["failed"] is False
            return result
    monkeypatch.setattr("fulfillment_v2_routes.operational_owner", serialize)
    monkeypatch.setattr("mobile_carrier_handoff.now", lambda: NOW)
    monkeypatch.setattr("carrier_handoff._now", lambda: NOW.isoformat())


def source(number="1001", *, owner="owner", completed=None, created=None, status="completed", carrier="iMile", **changes):
    completed = completed or NOW - timedelta(hours=3)
    native = {"id": "order-" + number, "reference_id": number,
        "date": (created or NOW - timedelta(days=10)).isoformat(),
        "updated_at": (NOW - timedelta(minutes=1)).isoformat(),
        "status": {"slug": status, "name": "تم التنفيذ" if status == "completed" else status,
                   "changed_at": completed.isoformat()},
        "customer": {"name": "عميل تجريبي"}, "shipping": {"company": carrier}}
    return {"user_id": owner, "order_number": number, "salla_order_id": native["id"],
        "order_date": native["date"][:10], "order_date_inferred": False,
        "order_status": native["status"]["name"], "order_status_slug": status,
        "shipping_company": carrier, "shipping_city": "الرياض",
        "salla_shipping_current": {"company_name": carrier, "company_code": "imile", "source_kind": "order",
            "shipment_id": "shipment-" + number, "tracking_number": "AWB" + number, "status": "created"},
        "raw_by_source": {"salla_direct": native}, **changes}


def workflow(number="1001", *, owner="owner", employee=None, **changes):
    return {"user_id": owner, "order_number": number, "stage": "completed",
        "carrier_label_type": "carrier", "carrier_name": "iMile", "carrier_tracking_number": "AWB" + number,
        "carrier_label_barcode": "AWB" + number, "carrier_label_print_confirmed": True,
        "carrier_handoff_employee_id": employee,
        "carrier_handoff_employee_name": "موظف " + employee if employee else None,
        "carrier_handoff_state": "with_handoff_employee" if employee else "awaiting_carrier_handoff",
        "carrier_handoff_scanned_at": (NOW - timedelta(hours=1)).isoformat() if employee else None,
        **changes}


async def setup():
    db = AsyncMongoMockClient()["mobile_handoff_synthetic"]
    await db.users.insert_many([{"id": "owner", "role": "owner", "name": "مالك المتجر"},
        {"id": "staff", "role": "employee", "created_by": "owner", "name": "موظف التسليم"},
        {"id": "other", "role": "employee", "created_by": "owner", "name": "موظف آخر"},
        {"id": "foreign", "role": "owner", "name": "مالك آخر"}])
    for actor in ("staff", "other"):
        await db[MOBILE_APP_ACCESS].insert_one({"owner_user_id": "owner", "user_id": actor,
            "enabled": True, "permissions": ["app.page.carrier_handoff"]})
        await db.mezan_employees_v2.insert_one({"id": "employee-" + actor, "user_id": "owner",
            "account_user_id": actor, "status": "active"})

    from fastapi import Request
    async def current_user(request: Request, x_actor: str = Header(default="staff")):
        user = await db.users.find_one({"id": x_actor}, {"_id": 0})
        user["_session_client"] = "amasi_mobile"
        return await mobile_app_request_user(db, user, path=request.url.path, method=request.method)

    app = FastAPI()
    app.include_router(make_fulfillment_v2_router(db, current_user), prefix="/api")
    return db, httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://synthetic")


@pytest.mark.asyncio
async def test_native_scan_attributes_employee_not_borrowed_owner():
    db, client = await setup()
    await db.unified_orders.insert_one(source())
    await db.order_review_workflows.insert_one(workflow())
    async with client:
        response = await client.post(BASE + "/scan", json={"barcode": "AWB1001"}, headers=STAFF)
    assert response.status_code == 200, response.text
    assert response.json()["carrier_handoff_employee_id"] == "staff"
    saved = await db.order_review_workflows.find_one({"order_number": "1001"})
    assert saved["carrier_handoff_employee_name"] == "موظف التسليم"


@pytest.mark.asyncio
async def test_mobile_waiting_is_dated_paged_and_returns_only_eight_rows():
    db, client = await setup()
    for index in range(11):
        number = str(1001 + index)
        await db.unified_orders.insert_one(source(number, completed=NOW - timedelta(hours=index + 1),
            created=NOW - timedelta(days=2, hours=index)))
        await db.order_review_workflows.insert_one(workflow(number))
    async with client:
        response = await client.get(BASE + "/mobile", params={"bucket": "waiting", "page": 1, "page_size": 8})
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["total"] == 11 and data["total_pages"] == 2
    assert data["counts"] == {"received": 0, "waiting": 11}
    assert len(data["items"]) == 8
    assert data["items"][0]["order_number"] == "1001"
    assert data["items"][0]["executed_at"] == (NOW - timedelta(hours=1)).isoformat()
    assert data["window"]["field"] == "created_at"
    assert data["actor"]["id"] == "staff"


@pytest.mark.asyncio
async def test_receipt_replays_once_and_session_summary_survives_status_release():
    db, client = await setup()
    await db.unified_orders.insert_one(source())
    await db.order_review_workflows.insert_one(workflow())
    async with client:
        page = (await client.get(BASE + "/mobile", params={"bucket": "waiting"})).json()
        command = {"barcode": "AWB1001", "request_id": str(uuid4()), "session_id": str(uuid4()),
            "expected_scope": page["session_scope"]}
        first = await client.post(BASE + "/scan", json=command)
        assert first.status_code == 200, first.text
        receipt = first.json()["receipt"]
        replay = await client.post(BASE + "/scan", json=command)
        assert replay.status_code == 200, replay.text
        assert replay.json()["receipt"] == receipt and replay.json()["outcome"] == "replayed"
        assert replay.json()["idempotent"] is True
        summary = await client.get(BASE + "/mobile/sessions/" + command["session_id"],
            params={"expected_scope": page["session_scope"], "request_id": command["request_id"]})
        assert summary.status_code == 200, summary.text
        assert summary.json()["total"] == 1
        assert summary.json()["companies"] == [{"carrier_key": "imile", "carrier_name": "iMile للتوصيل", "count": 1}]
        assert summary.json()["pending_receipt"] == summary.json()["last_receipt"] == receipt
        await db.unified_orders.update_one({"order_number": "1001"}, {"$set": {
            "order_status": "جاري التوصيل", "order_status_slug": "delivering",
            "raw_by_source.salla_direct.status": {"slug": "delivering", "name": "جاري التوصيل"}}})
        await db.order_review_workflows.update_one({"order_number": "1001"},
            {"$set": {"stage": "delivering", "carrier_handoff_state": "carrier_in_delivery"}})
        assert (await client.get(BASE + "/mobile", params={"bucket": "received"})).json()["total"] == 0
        assert (await client.post(BASE + "/scan", json=command)).json()["receipt"] == receipt
        summary = await client.get(BASE + "/mobile/sessions/" + command["session_id"],
            params={"expected_scope": page["session_scope"]})
        assert summary.json()["total"] == 1
        new_session = {**command, "request_id": str(uuid4()), "session_id": str(uuid4())}
        duplicate = await client.post(BASE + "/scan", json=new_session)
        assert duplicate.status_code == 409 and duplicate.json()["detail"]["not_applied"] is True
        empty = await client.get(BASE + "/mobile/sessions/" + new_session["session_id"],
            params={"expected_scope": page["session_scope"]})
        assert empty.json()["total"] == 0 and empty.json()["last_receipt"] is None
    assert await db.mezan_fulfillment_events_v2.count_documents({}) == 1


def native_event(number, at, *, completed=True, owner="owner", suffix=""):
    return {"user_id": owner, "order_number": number, "provider_order_id": "order-" + number,
        "source_event_id": "event-" + number + suffix,
        "native_status_transition": [{"delivered": False, "at": at.isoformat(),
            **({"target_status": "completed"} if completed else {})}]}


@pytest.mark.asyncio
async def test_created_window_inclusive_offsets_and_missing_execution_still_visible():
    db, client = await setup()
    start = NOW - timedelta(days=30)
    rows = [source("1001", created=start), source("1002", created=start - timedelta(microseconds=1)),
        source("1003", created=NOW), source("1004", created=NOW + timedelta(microseconds=1)),
        source("1005", created=start.astimezone(timezone(timedelta(hours=-5)))),
        source("1006", created=NOW - timedelta(days=31), completed=NOW - timedelta(hours=1)),
        source("1007"), source("1008")]
    rows[-2]["raw_by_source"]["salla_direct"]["status"].pop("changed_at")
    rows[-1]["raw_by_source"]["salla_direct"]["date"] = {
        "date": (NOW - timedelta(days=2)).astimezone(timezone(timedelta(hours=3))).replace(tzinfo=None).isoformat(),
        "timezone": "Asia/Riyadh"}
    await db.unified_orders.insert_many(rows)
    async with client:
        response = await client.get(BASE + "/mobile", params={"bucket": "waiting"})
    assert response.status_code == 200, response.text
    items = {row["order_number"]: row for row in response.json()["items"]}
    assert set(items) == {"1001", "1003", "1005", "1007", "1008"}
    assert items["1001"]["created_at"] == items["1005"]["created_at"] == start.isoformat()
    assert items["1007"]["executed_at"] is None
    assert items["1008"]["created_at"] == (NOW - timedelta(days=2)).isoformat()
    assert all(row["can_receive"] is False for row in items.values())  # Missing label workflow is displayed.
    assert response.json()["window"] == {"basis": "created_at", "field": "created_at", "days": 30,
        "from": start.isoformat(), "to": NOW.isoformat()}


@pytest.mark.asyncio
async def test_exact_current_status_external_only_and_current_employee_custody():
    db, client = await setup()
    sources = [source("1001"), source("1002"), source("1003"), source("1004", status="delivered"),
        source("1005", status="cancelled"), source("1006", status="in_progress"), source("1007", carrier="مندوب الرياض"),
        source("1008"), source("1009", owner="foreign"), source("1010"), source("1011")]
    sources[-2]["order_status"] = "تم التوصيل"  # Contradictory current signals cannot use the completed slug.
    sources[-1]["raw_by_source"]["salla_direct"]["status"] = {"name": "تم التنفيذ"}
    await db.unified_orders.insert_many(sources)
    await db.order_review_workflows.insert_many([workflow("1001", employee="staff"), workflow("1002", employee="other"),
        workflow("1003"), workflow("1008", carrier_label_type="store_courier"), workflow("1009", owner="foreign")])
    async with client:
        received = await client.get(BASE + "/mobile", params={"bucket": "received"})
        waiting = await client.get(BASE + "/mobile", params={"bucket": "waiting"})
        owner = await client.get(BASE + "/mobile", params={"bucket": "received"}, headers={"X-Actor": "owner"})
    assert received.status_code == waiting.status_code == owner.status_code == 200
    assert [row["order_number"] for row in received.json()["items"]] == ["1001"]
    assert received.json()["items"][0]["employee_id"] == "staff"
    assert {row["order_number"] for row in waiting.json()["items"]} == {"1003", "1011"}
    assert waiting.json()["counts"] == {"received": 1, "waiting": 2}
    assert owner.json()["total"] == 0


@pytest.mark.asyncio
async def test_execution_date_current_transition_wins_and_stale_history_is_not_invented():
    db, client = await setup()
    sources = [source(str(1001 + index)) for index in range(5)]
    for row in sources[1:]:
        row["raw_by_source"]["salla_direct"]["status"].pop("changed_at")
    await db.unified_orders.insert_many(sources)
    previous, transition, latest = NOW - timedelta(days=7), NOW - timedelta(days=2), NOW - timedelta(hours=3)
    events = [native_event("1001", previous), native_event("1001", transition, completed=False, suffix="processing"),
        native_event("1002", previous), native_event("1002", transition, completed=False, suffix="processing"),
        native_event("1002", latest, suffix="again"),
        native_event("1003", previous), native_event("1003", transition, completed=False, suffix="processing"),
        native_event("1004", latest, owner="foreign"),
        {**native_event("1005", latest), "provider_order_id": "foreign-order"}]
    await db.order_activity_events_v2.insert_many(events)
    async with client:
        response = await client.get(BASE + "/mobile", params={"bucket": "waiting"})
    assert response.status_code == 200, response.text
    items = {row["order_number"]: row for row in response.json()["items"]}
    assert items["1001"]["executed_at"] == latest.isoformat()  # Current native time, not the older completion.
    assert items["1002"]["executed_at"] == latest.isoformat()  # Latest validated typed transition.
    assert all(items[number]["executed_at"] is None for number in ("1003", "1004", "1005"))
    assert response.json()["total"] == 5


@pytest.mark.asyncio
async def test_exact_search_keeps_counts_and_pages_beyond_one_hundred(monkeypatch):
    import mobile_carrier_handoff as module
    db, client = await setup()
    await db.unified_orders.insert_many([source(str(1001 + index), created=NOW - timedelta(hours=index + 1)) for index in range(145)])
    await db.order_review_workflows.insert_many([workflow(str(1001 + index)) for index in range(145)])
    reads, original = [], module._read
    async def observe(collection, selector, projection, **kwargs):
        reads.append((collection.name, deepcopy(selector), deepcopy(projection), kwargs["limit"]))
        return await original(collection, selector, projection, **kwargs)
    monkeypatch.setattr(module, "_read", observe)
    async with client:
        response = await client.get(BASE + "/mobile", params={"bucket": "waiting", "page": 19})
        assert response.status_code == 200, response.text
        assert response.json()["total"] == 145 and len(response.json()["items"]) == 1
        assert response.json()["items"][0]["order_number"] == "1145"
        assert len(reads) == 5  # One streamed source cursor; two workflow batches + three visible-page reads.
        assert all(limit <= 129 for name, _, _, limit in reads if name != "order_activity_events_v2")
        proof_reads = [(query, projection) for name, query, projection, _ in reads
            if name == "unified_orders" and "salla_shipping_current" in projection]
        assert len(proof_reads) == 1 and proof_reads[0][0]["order_number"]["$in"] == ["1145"]
        assert all("raw_by_source.salla_direct.items" not in projection and "raw_by_source.salla_direct" not in projection
            for _, _, projection, _ in reads)
        history_reads = [query for name, query, _, _ in reads if name == "order_activity_events_v2"]
        assert len(history_reads) == 1 and history_reads[0]["order_number"]["$in"] == ["1145"]
        for query in ("1001", "AWB-1001"):
            exact = await client.get(BASE + "/mobile", params={"bucket": "waiting", "query": query})
            assert exact.json()["total"] == 1 and exact.json()["items"][0]["order_number"] == "1001"
            assert exact.json()["counts"] == {"received": 0, "waiting": 145}
        reads.clear()
        for query in ("100", "AWB100", "iMile"):
            empty = await client.get(BASE + "/mobile", params={"bucket": "waiting", "query": query})
            assert empty.json()["total"] == 0 and empty.json()["counts"]["waiting"] == 145
        assert not any(name == "order_activity_events_v2" for name, *_ in reads)
        assert (await client.get(BASE + "/mobile", params={"bucket": "waiting", "page": 20})).status_code == 409


@pytest.mark.asyncio
async def test_waiting_page_is_rejected_if_custody_changes_during_read(monkeypatch):
    import mobile_carrier_handoff as module
    db, client = await setup()
    await db.unified_orders.insert_one(source())
    await db.order_review_workflows.insert_one(workflow())
    original = module._read
    async def change_after_projection(collection, selector, projection, **kwargs):
        result = await original(collection, selector, projection, **kwargs)
        if collection.name == "unified_orders" and "salla_shipping_current" in projection:
            await db.order_review_workflows.update_one({"order_number": "1001"}, {"$set": {
                "carrier_handoff_employee_id": "other", "carrier_handoff_state": "with_handoff_employee",
                "carrier_handoff_scanned_at": NOW.isoformat()}})
        return result
    monkeypatch.setattr(module, "_read", change_after_projection)
    async with client:
        response = await client.get(BASE + "/mobile", params={"bucket": "waiting"})
    assert response.status_code == 409 and response.json()["detail"]["code"] == "carrier_handoff_page_changed"


@pytest.mark.asyncio
async def test_scopes_live_revocation_and_cross_owner_scan_cannot_leak_receipts():
    db, client = await setup()
    await db.unified_orders.insert_many([source(), source("1002", owner="foreign")])
    await db.order_review_workflows.insert_many([workflow(), workflow("1002", owner="foreign")])
    async with client:
        page = (await client.get(BASE + "/mobile", params={"bucket": "waiting"})).json()
        command = {"barcode": "AWB1001", "request_id": str(uuid4()), "session_id": str(uuid4()), "expected_scope": page["session_scope"]}
        assert (await client.post(BASE + "/scan", json=command, headers={"X-Actor": "other"})).status_code == 409
        foreign = await client.post(BASE + "/scan", json={**command, "barcode": "AWB1002"})
        assert foreign.status_code == 404 and foreign.json()["detail"]["not_applied"] is True
        assert (await client.post(BASE + "/scan", json=command)).status_code == 200
        wrong = await client.get(BASE + "/mobile/sessions/" + command["session_id"],
            params={"expected_scope": page["session_scope"]}, headers={"X-Actor": "other"})
        assert wrong.status_code == 409
        await db[MOBILE_APP_ACCESS].update_one({"user_id": "staff"}, {"$set": {"enabled": False, "permissions": []}})
        assert (await client.get(BASE + "/mobile", params={"bucket": "received"})).status_code == 403
        assert (await client.post(BASE + "/scan", json=command)).status_code == 403
        assert (await client.get(BASE + "/mobile/sessions/" + command["session_id"],
            params={"expected_scope": page["session_scope"]})).status_code == 403
    assert await db.mezan_fulfillment_events_v2.count_documents({}) == 1


@pytest.mark.asyncio
async def test_current_status_is_checked_at_scan_and_rejection_does_not_count():
    db, client = await setup()
    await db.unified_orders.insert_one(source(status="cancelled"))
    await db.order_review_workflows.insert_one(workflow())  # Stale completed workflow cannot grant receipt.
    async with client:
        response = await client.post(BASE + "/scan", json={"barcode": "AWB1001"})
    assert response.status_code == 409 and response.json()["detail"]["not_applied"] is True
    assert response.json()["detail"]["code"] == "carrier_shipment_current_status_not_completed"
    assert (await db.order_review_workflows.find_one({"order_number": "1001"}))["carrier_handoff_employee_id"] is None
    assert await db.mezan_fulfillment_events_v2.count_documents({}) == 0


@pytest.mark.asyncio
async def test_concurrent_replays_and_second_employee_claim_have_one_authoritative_receipt():
    db, client = await setup()
    await db.unified_orders.insert_one(source())
    await db.order_review_workflows.insert_one(workflow())
    async with client:
        page = (await client.get(BASE + "/mobile", params={"bucket": "waiting"})).json()
        command = {"barcode": "AWB1001", "request_id": str(uuid4()), "session_id": str(uuid4()), "expected_scope": page["session_scope"]}
        responses = await asyncio.gather(*(client.post(BASE + "/scan", json=command) for _ in range(5)))
        assert [response.status_code for response in responses] == [200] * 5
        assert sum(response.json()["outcome"] == "received" for response in responses) == 1
        assert len({response.json()["receipt"]["receipt_id"] for response in responses}) == 1
        other_page = (await client.get(BASE + "/mobile", params={"bucket": "waiting"}, headers={"X-Actor": "other"})).json()
        other = await client.post(BASE + "/scan", json={**command, "request_id": str(uuid4()), "expected_scope": other_page["session_scope"]},
            headers={"X-Actor": "other"})
        assert other.status_code == 409
        summary = await client.get(BASE + "/mobile/sessions/" + command["session_id"], params={"expected_scope": page["session_scope"]})
        assert summary.json()["total"] == 1
    assert await db.mezan_fulfillment_events_v2.count_documents({}) == 1


@pytest.mark.asyncio
async def test_lost_response_after_commit_keeps_receipt_and_replays_without_second_count(monkeypatch):
    from fastapi import HTTPException
    import fulfillment_v2_routes as routes
    db, client = await setup()
    await db.unified_orders.insert_one(source())
    await db.order_review_workflows.insert_one(workflow())
    original = routes.operational_owner
    lost = False
    async def lose_first_response(db, owner, operation, **kwargs):
        nonlocal lost
        result = await original(db, owner, operation, **kwargs)
        if not lost:
            lost = True
            raise HTTPException(503, detail={"code": "synthetic_connection_lost_after_commit"})
        return result
    monkeypatch.setattr(routes, "operational_owner", lose_first_response)
    async with client:
        page = (await client.get(BASE + "/mobile", params={"bucket": "waiting"})).json()
        command = {"barcode": "AWB1001", "request_id": str(uuid4()), "session_id": str(uuid4()), "expected_scope": page["session_scope"]}
        uncertain = await client.post(BASE + "/scan", json=command)
        assert uncertain.status_code == 503 and uncertain.json()["detail"].get("not_applied") is not True
        summary = await client.get(BASE + "/mobile/sessions/" + command["session_id"],
            params={"expected_scope": page["session_scope"], "request_id": command["request_id"]})
        assert summary.json()["total"] == 1 and summary.json()["pending_receipt"]["request_id"] == command["request_id"]
        replay = await client.post(BASE + "/scan", json=command)
        assert replay.status_code == 200 and replay.json()["outcome"] == "replayed"
        assert replay.json()["receipt"] == summary.json()["pending_receipt"]
    assert await db.mezan_fulfillment_events_v2.count_documents({}) == 1


@pytest.mark.asyncio
async def test_indexed_creation_prefilter_keeps_missing_helpers_and_verifies_native_dates(monkeypatch):
    import mobile_carrier_handoff as module
    db, client = await setup()
    rows = [source(str(1001 + index)) for index in range(5)]
    rows[0].pop("order_date")
    rows[1]["order_date"] = None
    rows[2]["order_date"] = ""
    rows[3]["order_date_inferred"] = True  # This helper is not date authority.
    rows[4]["raw_by_source"]["salla_direct"]["date"] = (NOW - timedelta(days=40)).isoformat()
    await db.unified_orders.insert_many(rows + [source("old" + str(index), created=NOW - timedelta(days=100)) for index in range(20)])
    cursor_type = type(db.unified_orders.find({}))
    hints, original_hint = [], cursor_type.hint
    def record_hint(cursor, value):
        hints.append(deepcopy(value))
        return original_hint(cursor, value)
    monkeypatch.setattr(cursor_type, "hint", record_hint)
    async with client:
        response = await client.get(BASE + "/mobile", params={"bucket": "waiting"})
    assert response.status_code == 200, response.text
    assert {row["order_number"] for row in response.json()["items"]} == {"1001", "1002", "1003", "1004"}
    assert hints == [[("user_id", 1), ("order_date", -1)]]
    selector = module.candidate_query("owner", NOW - timedelta(days=30), NOW)
    assert selector["user_id"] == "owner"
    branches = selector["$and"][0]["$or"]
    assert branches[0]["order_date"]["$gte"] == (NOW - timedelta(days=32)).date().isoformat()
    assert branches[1] == {"order_date": {"$in": [None, ""]}}


@pytest.mark.asyncio
async def test_bounded_read_budget_and_timeout_never_return_partial_counts(monkeypatch):
    import mobile_carrier_handoff as module
    db, client = await setup()
    await db.unified_orders.insert_many([source(str(1001 + index)) for index in range(9)])
    monkeypatch.setattr(module, "MAX_CANDIDATES", 8)
    async with client:
        bounded = await client.get(BASE + "/mobile", params={"bucket": "waiting"})
        assert bounded.status_code == 503 and bounded.json()["detail"]["code"] == "carrier_handoff_read_budget_exceeded"
        assert "items" not in bounded.json() and "counts" not in bounded.json()
        monkeypatch.setattr(module, "MAX_CANDIDATES", 8192)
        monkeypatch.setattr(module, "READ_TIMEOUT_SECONDS", .001)
        original = module._read
        async def slow_read(*args, **kwargs):
            await asyncio.sleep(.01)
            return await original(*args, **kwargs)
        monkeypatch.setattr(module, "_read", slow_read)
        timeout = await client.get(BASE + "/mobile", params={"bucket": "waiting"})
        assert timeout.status_code == 503 and timeout.json()["detail"]["code"] == "carrier_handoff_read_timeout"
        assert "items" not in timeout.json() and "counts" not in timeout.json()
        from pymongo.errors import ExecutionTimeout
        async def mongo_timeout(*args, **kwargs):
            raise ExecutionTimeout("synthetic maxTimeMS")
        monkeypatch.setattr(module, "READ_TIMEOUT_SECONDS", 8)
        monkeypatch.setattr(module, "_read", mongo_timeout)
        server_timeout = await client.get(BASE + "/mobile", params={"bucket": "waiting"})
        assert server_timeout.status_code == 503 and server_timeout.json()["detail"]["code"] == "carrier_handoff_read_timeout"


@pytest.mark.asyncio
async def test_three_company_summary_and_pending_receipt_are_atomic_custody_only(monkeypatch):
    db, client = await setup()
    carriers = [("imile", "iMile"), ("aramex", "Aramex"), ("smsa", "SMSA"), ("imile", "iMile")]
    for index, (key, name) in enumerate(carriers):
        number = str(1001 + index)
        row = source(number, carrier=name)
        row["salla_shipping_current"]["company_code"] = key
        await db.unified_orders.insert_one(row)
        await db.order_review_workflows.insert_one(workflow(number, carrier_name=name))
    async with client:
        page = (await client.get(BASE + "/mobile", params={"bucket": "waiting"})).json()
        session_id, receipts = str(uuid4()), []
        for index in range(4):
            stamp = (NOW - timedelta(seconds=4 - index)).isoformat()
            monkeypatch.setattr("carrier_handoff._now", lambda stamp=stamp: stamp)
            command = {"barcode": "AWB" + str(1001 + index), "request_id": str(uuid4()),
                "session_id": session_id, "expected_scope": page["session_scope"]}
            response = await client.post(BASE + "/scan", json=command)
            assert response.status_code == 200, response.text
            receipts.append(response.json()["receipt"])
        summary = await client.get(BASE + "/mobile/sessions/" + session_id,
            params={"expected_scope": page["session_scope"], "request_id": receipts[0]["request_id"]})
        assert summary.status_code == 200, summary.text
        data = summary.json()
        assert data["total"] == 4
        assert {row["carrier_key"]: row["count"] for row in data["companies"]} == {"imile": 2, "aramex": 1, "smsa": 1}
        assert data["pending_receipt"] == receipts[0] and data["last_receipt"] == receipts[-1]
    assert await db.mezan_fulfillment_events_v2.count_documents({}) == 4
    for collection in ("general_ledger", "operational_balance_states_v1", "store_delivery_assignments"):
        assert await db[collection].count_documents({}) == 0


@pytest.mark.asyncio
async def test_permission_revoked_while_waiting_for_owner_fence_prevents_custody(monkeypatch):
    import fulfillment_v2_routes as routes
    db, client = await setup()
    await db.unified_orders.insert_one(source())
    await db.order_review_workflows.insert_one(workflow())
    original = routes.operational_owner
    async def revoke_before_transaction(db, owner, operation, **kwargs):
        await db[MOBILE_APP_ACCESS].update_one({"user_id": "staff"}, {"$set": {"enabled": False, "permissions": []}})
        return await original(db, owner, operation, **kwargs)
    monkeypatch.setattr(routes, "operational_owner", revoke_before_transaction)
    async with client:
        page = (await client.get(BASE + "/mobile", params={"bucket": "waiting"})).json()
        response = await client.post(BASE + "/scan", json={"barcode": "AWB1001", "request_id": str(uuid4()),
            "session_id": str(uuid4()), "expected_scope": page["session_scope"]})
    assert response.status_code == 403
    assert (await db.order_review_workflows.find_one({"order_number": "1001"}))["carrier_handoff_employee_id"] is None
    assert await db.mezan_fulfillment_events_v2.count_documents({}) == 0


@pytest.mark.asyncio
async def test_request_identity_conflict_and_long_valid_tracking_search_are_safe():
    db, client = await setup()
    tracking = "A" * 140
    row = source()
    row["salla_shipping_current"]["tracking_number"] = tracking
    await db.unified_orders.insert_many([row, source("1002")])
    await db.order_review_workflows.insert_many([workflow(carrier_label_barcode=tracking, carrier_tracking_number=tracking), workflow("1002")])
    async with client:
        page = await client.get(BASE + "/mobile", params={"bucket": "waiting", "query": tracking})
        assert page.status_code == 200 and page.json()["total"] == 1
        command = {"barcode": tracking, "request_id": str(uuid4()), "session_id": str(uuid4()), "expected_scope": page.json()["session_scope"]}
        assert (await client.post(BASE + "/scan", json=command)).status_code == 200
        conflict = await client.post(BASE + "/scan", json={**command, "barcode": "AWB1002"})
        assert conflict.status_code == 409 and conflict.json()["detail"]["code"] == "carrier_handoff_request_conflict"
    assert (await db.order_review_workflows.find_one({"order_number": "1002"}))["carrier_handoff_employee_id"] is None
    assert await db.mezan_fulfillment_events_v2.count_documents({}) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("sentinel", [None, "غير محدد", {"untyped": "name"}])
async def test_confirmed_workflow_carrier_stays_visible_when_source_carrier_names_are_missing(sentinel):
    db, client = await setup()
    row = source()
    row.pop("shipping_company")
    row["salla_shipping_current"].pop("company_name")
    if sentinel is not None:
        row["shipping_company"] = row["salla_shipping_current"]["company_name"] = sentinel
    await db.unified_orders.insert_one(row)
    await db.order_review_workflows.insert_one(workflow())
    async with client:
        waiting = await client.get(BASE + "/mobile", params={"bucket": "waiting"})
        assert waiting.status_code == 200 and waiting.json()["total"] == 1
        assert waiting.json()["items"][0]["carrier_key"] == "imile"
        accepted = await client.post(BASE + "/scan", json={"barcode": "AWB1001"})
        assert accepted.status_code == 200, accepted.text
        received = await client.get(BASE + "/mobile", params={"bucket": "received"})
        assert received.status_code == 200 and received.json()["total"] == 1
        assert received.json()["items"][0]["carrier_key"] == "imile"
