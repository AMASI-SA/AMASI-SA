"""Read-only app adapter parity over the existing canonical refund fixtures."""
from contextlib import asynccontextmanager
from copy import deepcopy
import asyncio

from fastapi import FastAPI, HTTPException, Request
from httpx import ASGITransport, AsyncClient
from pymongo.monitoring import CommandListener, register

from mobile_app_request_context import mobile_app_request_user
from mobile_customer_refund_reads import make_mobile_customer_refund_read_router
from operational_balance_routes import make_operational_balance_router
from operational_balance_store import digest, mutate, read
from test_operational_balance_workspace import grant, resource, run
from test_operational_customer_adjustments import adjusted_order
from test_operational_workspace_refunds import command, EXECUTED_AT, order_fixture


BASE = "/api/operational-balances/workspace/mobile-refunds"
CANONICAL = "/api/operational-balances/workspace"


class Commands(CommandListener):
    def __init__(self):
        self.commands = []
        self.enabled = False

    def started(self, event):
        if self.enabled and event.database_name.startswith("workspace_"):
            self.commands.append((event.command_name, deepcopy(event.command)))

    def succeeded(self, event):
        pass

    def failed(self, event):
        pass


TRACE = Commands()
register(TRACE)


@asynccontextmanager
async def app_client(db):
    async def principal(request: Request):
        actor = await db.users.find_one({"id": request.headers.get("x-test-user", "owner")}, {"_id": 0})
        if actor is None:
            raise HTTPException(401)
        return await mobile_app_request_user(db, {**actor, "_session_client": "amasi_mobile"},
            path=request.url.path, method=request.method)

    app = FastAPI()
    app.include_router(make_mobile_customer_refund_read_router(db, principal), prefix="/api")
    app.include_router(make_operational_balance_router(db, principal), prefix="/api")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://isolated") as client:
        yield client


async def linked_staff(db, *, operations, resources):
    await db.mezan_employees_v2.update_one({"id": "employee", "user_id": "owner"},
        {"$set": {"account_user_id": "staff"}})
    await grant(db, operations=operations, resources=resources)


async def shared_read_grant(db):
    await db.mezan_employees_v2.update_one({"id": "employee", "user_id": "owner"},
        {"$set": {"account_user_id": "staff", "status": "active"}})
    await db.users.update_one({"id": "staff"}, {"$set": {
        "operational_balance_access": {"version": 1, "owner_id": "owner", "enabled": True,
            "operations": ["customer.refund_read"], "resources": [],
            "customer_refunds": {"mode": "read", "employee_id": "employee",
                "sources": [{"kind": "bank", "id": "bank"}]}},
        "web_page_access": {"owner_id": "owner", "revision": 1,
            "grants": [{"id": "balances.refund", "mode": "read"}]}}})


def test_eight_row_pages_preserve_canonical_amounts_and_never_change_financial_state():
    async def scenario(db, _):
        for number in range(1001, 1010):
            await adjusted_order(db, number=str(number), paid="200.00", total="180.00", hint="20.00")
        before = await read(db, "owner")
        orders = await db.unified_orders.find({}).sort("order_number", 1).to_list(20)
        async with app_client(db) as client:
            canonical = await client.get(CANONICAL + "/customer-adjustments", params={"direction": "refund", "page_size": 8})
            assert canonical.status_code == 200, canonical.text
            response = await client.get(BASE)
            assert response.status_code == 200, response.text
            page = response.json()
            assert page["page_size"] == 8 and page["total"] == 9 and page["total_pages"] == 2
            assert len(page["items"]) == 8 and response.headers["Cache-Control"] == "no-store"
            assert [{key: value for key, value in row.items() if key != "order_status"}
                    for row in page["items"]] == canonical.json()["items"]
            assert page["summary"] == canonical.json()["summary"]
            assert all(row["order_status"]["slug"] == "delivered" for row in page["items"])
            assert all(row["status"] == "needs_confirmation" for row in page["items"])
            second = (await client.get(BASE, params={"page": 2})).json()
            assert len(second["items"]) == 1
            assert len({row["order_id"] for row in page["items"] + second["items"]}) == 9
            assert page["session_scope"] == digest(["owner", "owner"])
            assert page["can_record_execution"] is True
            assert "Private customer" not in response.text and "private-phone" not in response.text
        assert await read(db, "owner") == before
        assert await db.unified_orders.find({}).sort("order_number", 1).to_list(20) == orders
    run(scenario)


def test_exact_arabic_digit_search_finds_nonpending_order_without_running_candidate_scan(monkeypatch):
    import mobile_customer_refund_reads as adapter

    async def no_scan(*args, **kwargs):
        raise AssertionError("Exact search must not scan the pending candidate inbox")

    monkeypatch.setattr(adapter, "customer_adjustments_page", no_scan)

    async def scenario(db, _):
        await order_fixture(db, number="2002", paid="200.00", total="200.00")
        async with app_client(db) as client:
            response = await client.get(BASE, params={"order_number": "٢٠٠٢", "page": 6})
            assert response.status_code == 200, response.text
            result = response.json()
            assert result["page"] == result["total"] == result["total_pages"] == 1
            assert result["items"][0]["order_number"] == "2002"
            assert result["items"][0]["pending_refund_amount"] == "0.00"
            assert result["items"][0]["paid_amount"] == "200.00"
            assert result["summary"]["refund_pending_count"] == 0
            assert (await client.get(BASE, params={"order_number": "999999"})).status_code == 404
    run(scenario)


def test_form_reuses_canonical_capacity_methods_and_fenced_scope():
    async def scenario(db, _):
        await adjusted_order(db, total="180.00", hint="20.00")
        before = await read(db, "owner")
        async with app_client(db) as client:
            canonical = await client.get(CANONICAL + "/refunds/order/1001")
            response = await client.get(BASE + "/order/1001")
            assert response.status_code == canonical.status_code == 200, response.text
            dto = response.json()
            for key, value in canonical.json().items():
                assert dto[key] == value, key
            assert dto["session_scope"] == digest(["owner", "owner"])
            assert isinstance(dto["permission_version"], int)
            assert dto["reference_required"] is True
            assert dto["suggested_refund_amount"] == "20.00" and dto["available_amount"] == "200.00"
            assert "refund_type" not in dto  # No claim that the unchanged writer accepts this field.
            assert "Private customer" not in response.text and "Private product" not in response.text
            assert response.headers["Cache-Control"] == "no-store"
        assert await read(db, "owner") == before
    run(scenario)


def test_source_scope_and_other_merchant_orders_remain_private():
    async def scenario(db, _):
        await adjusted_order(db)
        await order_fixture(db, number="2002", method="tabby")
        await linked_staff(db, operations=["customer.refund_execute"], resources=[resource("provider", "tamara")])
        async with app_client(db) as client:
            staff = {"x-test-user": "staff"}
            response = await client.get(BASE + "/order/1001", headers=staff)
            assert response.status_code == 200, response.text
            assert response.json()["banks"] == []
            assert response.json()["methods"] == [{"id": "original", "label": "تمارا"}]
            assert response.json()["session_scope"] == digest(["owner", "staff"])
            assert (await client.get(BASE + "/order/2002", headers=staff)).status_code == 404
            assert (await client.get(BASE, params={"order_number": "2002"}, headers=staff)).status_code == 404
            assert (await client.get(BASE + "/order/1001", headers={"x-test-user": "other"})).status_code == 404
            assert (await client.get(BASE, headers={"x-test-user": "missing"})).status_code == 401
    run(scenario)


def test_current_access_is_rechecked_after_async_read_before_returning_money(monkeypatch):
    import mobile_customer_refund_reads as adapter
    original = adapter.customer_adjustments_page

    async def revoke_after_read(db, *args, **kwargs):
        result = await original(db, *args, **kwargs)
        await db.users.update_one({"id": "staff"}, {"$set": {"disabled": True}})
        return result

    monkeypatch.setattr(adapter, "customer_adjustments_page", revoke_after_read)

    async def scenario(db, _):
        await adjusted_order(db)
        await linked_staff(db, operations=["customer.refund_execute"], resources=[resource("provider", "tamara")])
        async with app_client(db) as client:
            response = await client.get(BASE, headers={"x-test-user": "staff"})
            assert response.status_code == 403, response.text
            assert "paid_amount" not in response.text and "200.00" not in response.text
    run(scenario)


def test_status_enrichment_is_bounded_projected_and_conflict_is_explicit():
    async def scenario(db, _):
        for number in range(1001, 1010):
            await adjusted_order(db, number=str(number))
        await db.unified_orders.update_one({"order_number": "1009"}, {"$set": {"order_status_slug": "in_progress"}})
        TRACE.commands.clear()
        TRACE.enabled = True
        try:
            async with app_client(db) as client:
                response = await client.get(BASE)
                assert response.status_code == 200, response.text
                rows = response.json()["items"]
                conflict = next(row for row in rows if row["order_number"] == "1009")
                assert conflict["order_status"] == {"slug": "unknown", "label": "حالة الطلب غير مؤكدة"}
        finally:
            TRACE.enabled = False
        status_reads = [command for name, command in TRACE.commands if name == "find"
            and command.get("find") == "unified_orders"
            and "raw_by_source.salla_direct.id" in command.get("filter", {})]
        assert len(status_reads) == 1
        command = status_reads[0]
        assert command["limit"] == 17 and command["maxTimeMS"] == 1500
        assert len(command["filter"]["raw_by_source.salla_direct.id"]["$in"]) == 8
        assert "raw_by_source.salla_direct.items" not in command["projection"]
        assert "raw_by_source.salla_direct.customer" not in command["projection"]
        assert command["projection"]["raw_by_source.salla_direct.status"] == 1
    run(scenario)


def test_adapter_accepts_only_eight_row_gets_and_creates_no_write_route():
    async def scenario(db, _):
        await adjusted_order(db)
        before = await read(db, "owner")
        async with app_client(db) as client:
            for params in ({"page_size": 9}, {"page_size": 7}, {"page": 0}, {"order_number": "x" * 161}):
                assert (await client.get(BASE, params=params)).status_code == 422
            for method in ("POST", "PUT", "DELETE"):
                assert (await client.request(method, BASE, json={})).status_code == 405
                assert (await client.request(method, BASE + "/order/1001", json={})).status_code == 405
        assert await read(db, "owner") == before
    run(scenario)


def test_true_read_only_refund_grant_lists_rows_without_granting_money_or_collection_routes():
    async def scenario(db, _):
        await adjusted_order(db)
        await shared_read_grant(db)
        before = await read(db, "owner")
        headers = {"x-test-user": "staff"}
        async with app_client(db) as client:
            listing = await client.get(BASE, headers=headers)
            assert listing.status_code == 200, listing.text
            assert listing.json()["total"] == 1
            assert listing.json()["can_record_execution"] is False
            form = await client.get(BASE + "/order/1001", headers=headers)
            assert form.status_code == 200, form.text
            assert form.json()["can_record_execution"] is False and form.json()["can_request"] is False
            body = {**command(expected_session_scope=digest(["owner", "staff"])),
                "expected_basis_hash": form.json()["basis_hash"], "reference": "read-must-not-write",
                "executed_at": EXECUTED_AT, "confirmed": True}
            assert (await client.post(CANONICAL + "/refunds/record-execution", headers=headers, json=body)).status_code == 403
            assert (await client.post(CANONICAL + "/refunds", headers=headers,
                json=command(expected_session_scope=digest(["owner", "staff"])))).status_code == 403
            assert (await client.get(CANONICAL + "/customer-adjustments", headers=headers,
                params={"direction": "collection"})).status_code == 403
            ledger = await client.get(CANONICAL + "/ledger/bank/bank", headers=headers)
            assert ledger.status_code == 404 and ledger.json()["detail"]["code"] == "operational_workspace_not_found"
        assert await read(db, "owner") == before
    run(scenario)


def test_authorized_single_reservation_exposes_only_exact_canonical_confirmation_requirements():
    async def scenario(db, _):
        await adjusted_order(db, total="200.00", hint="0.00")
        async with app_client(db) as client:
            requested = await client.post(CANONICAL + "/refunds", json=command(amount="20.00"))
            assert requested.status_code == 200, requested.text
            case = requested.json()["item"]
            before = await read(db, "owner")
            result = (await client.get(BASE + "/order/1001")).json()
            assert result["pending_refund_amount"] == result["exact_amount_required"] == "20.00"
            assert result["pending_case_id"] == case["id"]
            assert result["required_method"] == "bank" and result["required_source"]["id"] == "bank"
            assert result["recording_blocked_reason"] is None and result["can_record_execution"] is True
            assert await read(db, "owner") == before
            # A second reservation cannot be silently selected or combined.
            requested = await client.post(CANONICAL + "/refunds", json=command(amount="10.00"))
            assert requested.status_code == 200, requested.text
            result = (await client.get(BASE + "/order/1001")).json()
            assert result["pending_case_id"] is None and result["recording_blocked_reason"]
            assert result["can_record_execution"] is False
    run(scenario)


def test_single_document_requires_its_exact_reference_and_amount_without_fake_partial_support():
    async def scenario(db, _):
        await adjusted_order(db, total="200.00", hint="0.00")
        async def document(state):
            state["customer_returns"] = [{"id": "documented", "case_type": "order_compensation",
                "order_id": "order-1001", "order_number": "1001", "currency": "SAR",
                "amount": "20.00", "status": "salla_refund_recorded", "refund_reference": "document-reference",
                "created_at": EXECUTED_AT, "created_by": "owner",
                "refund_source_type": None, "refund_source_id": None}]
        await mutate(db, "owner", document)
        before = await read(db, "owner")
        async with app_client(db) as client:
            result = (await client.get(BASE + "/order/1001")).json()
            assert result["exact_amount_required"] == "20.00"
            assert result["required_reference"] == "document-reference"
            assert result["pending_case_id"] is None and result["recording_blocked_reason"] is None
            assert await read(db, "owner") == before
            async def remove_reference(state):
                state["customer_returns"][0]["refund_reference"] = ""
            await mutate(db, "owner", remove_reference)
            response = await client.get(BASE + "/order/1001")
            assert response.status_code == 200, response.text
            assert response.json()["recording_blocked_reason"]
            assert response.json()["can_record_execution"] is False
    run(scenario)


def test_ordinary_hint_remains_editable_and_relinked_read_grant_is_denied():
    async def scenario(db, _):
        await adjusted_order(db, total="180.00", hint="20.00")
        await shared_read_grant(db)
        async with app_client(db) as client:
            result = (await client.get(BASE + "/order/1001")).json()
            assert result["suggested_refund_amount"] == "20.00"
            assert result["exact_amount_required"] is None and result["recording_blocked_reason"] is None
            await db.mezan_employees_v2.update_one({"id": "employee"}, {"$set": {"account_user_id": "different"}})
            response = await client.get(BASE, headers={"x-test-user": "staff"})
            assert response.status_code == 403 and "paid_amount" not in response.text
    run(scenario)


def test_deadline_returns_retryable_error_and_cancels_the_owned_reader(monkeypatch):
    import mobile_customer_refund_reads as adapter
    cancelled = asyncio.Event()

    async def stalled(*args, **kwargs):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    monkeypatch.setattr(adapter, "customer_adjustments_page", stalled)
    monkeypatch.setattr(adapter, "READ_TIMEOUT_SECONDS", .05)

    async def scenario(db, _):
        before = await read(db, "owner")
        async with app_client(db) as client:
            response = await client.get(BASE)
            assert response.status_code == 504, response.text
            assert response.json()["detail"]["code"] == "customer_refund_read_timeout"
            assert cancelled.is_set()
        assert await read(db, "owner") == before
    run(scenario)


def test_empty_inbox_returns_a_complete_empty_page_without_status_fanout(monkeypatch):
    import mobile_customer_refund_reads as adapter
    original = adapter._with_current_status
    batches = []

    async def observe(db, owner, rows):
        batches.append(len(rows))
        return await original(db, owner, rows)

    monkeypatch.setattr(adapter, "_with_current_status", observe)

    async def scenario(db, _):
        before = await read(db, "owner")
        async with app_client(db) as client:
            response = await client.get(BASE)
            assert response.status_code == 200, response.text
            result = response.json()
            assert result["items"] == [] and result["total"] == 0
            assert result["page"] == result["total_pages"] == 1 and result["page_size"] == 8
            assert result["summary"]["refund_pending_count"] == 0
            assert batches == [0]
        assert await read(db, "owner") == before
    run(scenario)
