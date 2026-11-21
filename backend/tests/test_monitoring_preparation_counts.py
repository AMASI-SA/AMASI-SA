"""Monitoring entry returns exact unfinished-unit counts, never product details."""
from copy import deepcopy
from datetime import datetime, timezone
import asyncio

import httpx
import pytest
from fastapi import FastAPI
from mongomock_motor import AsyncMongoMockClient
from pymongo.errors import OperationFailure

import mobile_operations_monitoring_routes as monitoring
from review_local_policy import LOCAL_COMPLETION_MODE, WORKFLOWS, OPERATIONS


OWNER = "monitor-owner"
WHEN = datetime(2026, 11, 20, tzinfo=timezone.utc)


async def employee(db, key, *, owner=OWNER, status="active"):
    account = f"login-{key}"
    await db[monitoring.EMPLOYEES].insert_one({
        "user_id": owner, "id": key, "account_user_id": account,
        "display_name": key, "status": status,
    })
    await db[monitoring.MOBILE_APP_ACCESS].insert_one({
        monitoring.MOBILE_APP_ACCESS_OWNER_FIELD: owner, "user_id": account,
        "enabled": True, "permissions": ["app.page.my_products"],
    })
    return account


async def order(db, number, *, status="in_progress", local=False, proof=True,
                payment=None, owner=OWNER, workflow_mode=None):
    await db.unified_orders.insert_one({
        "user_id": owner, "order_number": number, "order_status": status,
        "order_date": WHEN, "raw_by_source": {"salla_direct": {
            "id": f"source-{number}", "reference_id": number,
            "date": WHEN.isoformat(), "status": status, "payment_method": "cod",
            "customer": {"private": "must not be fetched"},
            "items": [{"name": "must not be fetched", "image": "x" * 10000}],
            **(payment or {}),
        }},
    })
    workflow = {"user_id": owner, "order_number": number, "stage": "in_progress",
                "items": [{"product_options": "must not be fetched"}]}
    if local:
        workflow.update(completion_mode=LOCAL_COMPLETION_MODE, review_completion_operation_id=f"op-{number}")
        if proof:
            await db[OPERATIONS].insert_one({
                "_id": f"op-{number}", "user_id": owner, "order_number": number,
                "state": "completed", "completion_mode": LOCAL_COMPLETION_MODE,
            })
    elif workflow_mode:
        workflow["completion_mode"] = workflow_mode
    await db[WORKFLOWS].insert_one(workflow)


def piece(key, account, number="active", **changes):
    return {
        "user_id": OWNER, "piece_id": key, "order_number": number,
        "responsible_employee_id": account, "assignment_status": "assigned",
        "status": "assigned", "services": [],
        "image_b64": "x" * 10000, "product_options_snapshot": {"private": "not a count"},
        **changes,
    }


class ReadBudgetDB:
    """Real Mongo-like aggregation with guards at the storage boundary."""
    def __init__(self, db):
        self.db, self.calls = db, []

    def __getattr__(self, name):
        return self[name]

    def __getitem__(self, name):
        assert name in {
            monitoring.EMPLOYEES, monitoring.MOBILE_APP_ACCESS, monitoring.PIECES,
            "users", "unified_orders", WORKFLOWS, OPERATIONS,
        }, f"Unexpected detail/history collection: {name}"
        parent, collection = self, self.db[name]

        class Collection:
            def find(self, query, projection=None, *args, **kwargs):
                assert name != monitoring.PIECES, "List must aggregate counts, not hydrate physical pieces"
                assert projection and projection.get("_id") in (0, 1)
                assert "raw_by_source" not in projection and "raw_by_source.salla_direct" not in projection
                assert not any("items" in field or "customer" in field or "image" in field for field in projection)
                parent.calls.append((name, "find", deepcopy(query), deepcopy(projection)))
                return collection.find(query, projection, *args, **kwargs)

            async def find_one(self, query, projection=None, *args, **kwargs):
                assert name == "users", f"No per-employee/per-order lookup: {name}"
                parent.calls.append((name, "find_one", deepcopy(query), deepcopy(projection)))
                return await collection.find_one(query, projection, *args, **kwargs)

            def aggregate(self, pipeline, *args, **kwargs):
                assert name == monitoring.PIECES
                assert pipeline[0]["$match"]["user_id"] == OWNER
                text = repr(pipeline)
                assert "image_b64" not in text and "product_options" not in text and "service_name" not in text
                assert "$group" in text and "$sum" in text
                parent.calls.append((name, "aggregate", deepcopy(pipeline), kwargs))
                return collection.aggregate(pipeline, *args, **kwargs)

        return Collection()


async def read_counts(db, monkeypatch, *, allowed=True):
    async def access(*_args):
        return {"permissions": [monitoring.MONITORING_PERMISSION] if allowed else []}
    monkeypatch.setattr(monitoring, "mobile_app_access_for_user", access)
    app = FastAPI()
    app.include_router(monitoring.make_mobile_operations_monitoring_router(db, lambda: {"id": OWNER, "role": "owner"}))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://synthetic.test") as client:
        return await client.get("/mobile/operations-monitoring/preparation", params={"pending_only": "true"})


@pytest.mark.asyncio
async def test_only_current_unprepared_assigned_units_rank_employees_without_details(monkeypatch):
    db = AsyncMongoMockClient().pending_monitoring
    a, b, zero = [await employee(db, name) for name in ("A", "B", "Zero")]
    await order(db, "active")
    pending_services = [
        {"service_name": "private-name", "status": "pending", "required_quantity": 1, "completed_quantity": 0},
        {"service_name": "another-private-name", "status": "pending", "required_quantity": 2, "completed_quantity": 0},
    ]
    rows = [
        piece("waiting", a), piece("sent", a, status="in_progress", supplier_dispatch_status="sent"),
        piece("partial", a, status="in_progress", supplier_dispatch_status="partial_received", services=pending_services),
        piece("held", a, status="blocked", active_hold_id="hold-1"),
        piece("supplier-ready", b, status="ready_for_employee_receipt", supplier_dispatch_status="ready"),
        piece("new-owner", b, previous_responsible_employee_id=a),
        piece("prepared", zero, status="received", supplier_dispatch_status="received", received_at=WHEN),
        piece("quantity-complete", zero, status="in_progress", supplier_dispatch_status="partial_received",
              services=[{"status": "pending", "required_quantity": 2, "completed_quantity": 2}]),
        piece("handed-off", a, status="ready_for_assembly", branch_handoff_at=WHEN),
        piece("archived", a, experiment_archived_at=WHEN),
        piece("unassigned", a, assignment_status="unassigned_after_rejection"),
        piece("cancelled-piece", a, status="cancelled"),
        piece("foreign", a, user_id="foreign"),
    ]
    await db[monitoring.PIECES].insert_many(rows)
    before = await db[monitoring.PIECES].find({}, {"_id": 0}).to_list(None)
    observed = ReadBudgetDB(db)
    response = await read_counts(observed, monkeypatch)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["counts_only"] is True and data["read_only"] is True
    assert [(r["employee_id"], r["outstanding_preparation_count"]) for r in data["employees"]] == [("A", 4), ("B", 2)]
    assert all(set(r) <= {"employee_id", "employee_name", "job_title", "department", "status", "outstanding_preparation_count"}
               for r in data["employees"])
    assert "range" not in data and "products" not in data and "summary" not in data
    assert await db[monitoring.PIECES].find({}, {"_id": 0}).to_list(None) == before
    assert sum(call[1] == "aggregate" for call in observed.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["completed", "shipped", "delivering", "out_for_delivery", "delivered", "cancelled", "refunded"])
async def test_terminal_current_order_hides_stale_supplier_work(monkeypatch, status):
    db = AsyncMongoMockClient().terminal_monitoring
    account = await employee(db, "A")
    await order(db, "terminal", status=status)
    await db[monitoring.PIECES].insert_many([
        piece("sent", account, "terminal", status="in_progress", supplier_dispatch_status="sent"),
        piece("partial", account, "terminal", status="in_progress", supplier_dispatch_status="partial_received",
              services=[{"status": "pending"}]),
    ])
    response = await read_counts(ReadBudgetDB(db), monkeypatch)
    assert response.status_code == 200, response.text
    assert response.json()["employees"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("status,local,proof,payment,expected", [
    ("reviewed", False, True, None, 1), ("processing", False, True, None, 1),
    ("under_review", False, True, None, 0), ("under_review", True, True, None, 1),
    ("under_review", True, False, None, 0),
    ("under_review", True, True, {"payment_method": "mada", "payment_status": "unpaid",
      "total": 100, "paid_amount": 0, "remaining_amount": 100}, 0),
])
async def test_waiting_assignment_retains_current_review_and_local_payment_rules(monkeypatch, status, local, proof, payment, expected):
    db = AsyncMongoMockClient().eligibility_monitoring
    account = await employee(db, "A")
    await order(db, "waiting", status=status, local=local, proof=proof, payment=payment)
    await db[monitoring.PIECES].insert_one(piece("waiting", account, "waiting"))
    response = await read_counts(ReadBudgetDB(db), monkeypatch)
    assert response.status_code == 200, response.text
    assert sum(row["outstanding_preparation_count"] for row in response.json()["employees"]) == expected


@pytest.mark.asyncio
async def test_underway_review_status_and_owner_identity_are_preserved(monkeypatch):
    db = AsyncMongoMockClient().owner_monitoring
    await db.users.insert_one({"id": OWNER, "name": "Owner"})
    await order(db, "old-review", status="under_review")
    await db[monitoring.PIECES].insert_one(piece("sent-owner", OWNER, "old-review", status="blocked",
                                                 supplier_dispatch_status="sent", active_hold_id="hold"))
    response = await read_counts(ReadBudgetDB(db), monkeypatch)
    assert response.status_code == 200, response.text
    assert [(row["employee_id"], row["outstanding_preparation_count"]) for row in response.json()["employees"]] == [(OWNER, 1)]


@pytest.mark.asyncio
async def test_shared_order_across_group_batches_is_counted_once_per_unit_and_read_once(monkeypatch):
    db = AsyncMongoMockClient().batched_monitoring
    await order(db, "shared")
    rows = []
    for index in range(105):
        account = await employee(db, f"employee-{index:03}")
        rows.append(piece(f"piece-{index}", account, "shared"))
    await db[monitoring.PIECES].insert_many(rows)
    observed = ReadBudgetDB(db)
    response = await read_counts(observed, monkeypatch)
    assert response.status_code == 200, response.text
    assert len(response.json()["employees"]) == 105
    assert sum(row["outstanding_preparation_count"] for row in response.json()["employees"]) == 105
    assert sum(call[:2] == ("unified_orders", "find") for call in observed.calls) == 1
    assert sum(call[:2] == (WORKFLOWS, "find") for call in observed.calls) == 1


@pytest.mark.asyncio
async def test_count_mode_permission_denied_before_data_reads(monkeypatch):
    observed = ReadBudgetDB(AsyncMongoMockClient().denied_monitoring)
    response = await read_counts(observed, monkeypatch, allowed=False)
    assert response.status_code == 403
    assert observed.calls == []


@pytest.mark.asyncio
async def test_underway_counts_need_no_order_dto_payment_or_review_hydration(monkeypatch):
    import monitoring_preparation_counts as reader
    db = AsyncMongoMockClient().status_only_monitoring
    account = await employee(db, "A")
    await order(db, "active")
    await order(db, "closed", status="cancelled")
    await db[monitoring.PIECES].insert_many([
        piece("open", account, "active", status="in_progress", supplier_dispatch_status="sent"),
        piece("closed", account, "closed", status="in_progress", supplier_dispatch_status="sent"),
    ])
    def no_mapping(_source):
        raise AssertionError("Underway status filtering must not build an OrderDTO")
    monkeypatch.setattr(reader, "projected_order", no_mapping)
    observed = ReadBudgetDB(db)
    response = await read_counts(observed, monkeypatch)
    assert response.status_code == 200, response.text
    assert response.json()["employees"][0]["outstanding_preparation_count"] == 1
    assert not any(call[0] in {WORKFLOWS, OPERATIONS} for call in observed.calls)


@pytest.mark.asyncio
async def test_count_storage_failure_is_not_a_successful_empty_directory(monkeypatch):
    import monitoring_preparation_counts as reader
    db = AsyncMongoMockClient().failed_monitoring
    await employee(db, "A")
    async def failed(*_args):
        raise OperationFailure("synthetic count failure")
    monkeypatch.setattr(reader, "_read_counts", failed)
    response = await read_counts(ReadBudgetDB(db), monkeypatch)
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "monitoring_preparation_counts_unavailable"
    assert "employees" not in response.json()


@pytest.mark.asyncio
async def test_count_timeout_cancels_work_and_never_returns_partial_counts(monkeypatch):
    import monitoring_preparation_counts as reader
    db = AsyncMongoMockClient().timeout_monitoring
    await employee(db, "A")
    cancelled = []
    async def stalled(*_args):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.append(True)
    monkeypatch.setattr(reader, "_read_counts", stalled)
    monkeypatch.setattr(reader, "READ_TIMEOUT_SECONDS", 0.01)
    response = await read_counts(ReadBudgetDB(db), monkeypatch)
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "monitoring_preparation_counts_unavailable"
    assert cancelled == [True]
