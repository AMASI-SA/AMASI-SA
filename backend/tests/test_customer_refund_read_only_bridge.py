"""Mezan and AMASI share refund-only grants, with no financial write upgrade."""
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from httpx import ASGITransport, AsyncClient

from operational_balance_routes import make_operational_balance_router
from operational_balance_store import read
from operational_web_page_policy import bind_page_request
from test_mobile_customer_refund_reads import shared_read_grant
from test_operational_balance_workspace import run
from test_operational_customer_adjustments import adjusted_order
from test_operational_workspace_refunds import command, EXECUTED_AT, order_fixture

BASE = "/api/operational-balances/workspace"


@asynccontextmanager
async def web_client(db):
    async def principal(request: Request):
        actor = await db.users.find_one({"id": "staff"}, {"_id": 0})
        with bind_page_request(actor, request):
            yield actor

    app = FastAPI()
    app.include_router(make_operational_balance_router(db, principal), prefix="/api")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://isolated") as client:
        yield client


def test_shared_read_grant_can_read_canonical_inbox_order_and_refund_context_but_not_write():
    async def scenario(db, _):
        await adjusted_order(db, total="180.00", hint="20.00")
        await shared_read_grant(db)
        before = await read(db, "owner")
        async with web_client(db) as client:
            inbox = await client.get(BASE + "/customer-adjustments", params={"direction": "refund", "page_size": 8})
            assert inbox.status_code == 200, inbox.text
            assert inbox.json()["items"][0]["pending_refund_amount"] == "20.00"
            order = await client.get(BASE + "/refunds/order/1001")
            assert order.status_code == 200, order.text
            assert order.json()["paid_amount"] == "200.00"
            assert order.json()["can_request"] is False and order.json()["can_record_execution"] is False
            assert [bank["id"] for bank in order.json()["banks"]] == ["bank"]
            context = await client.get(BASE + "/context", params={"refund_context": "true", "directory_only": "true"})
            assert context.status_code == 200, context.text
            assert [row["key"] for row in context.json()["operations"]] == ["customer.refund_read"]
            assert all(row["access"]["balance"] is False for row in context.json()["resources"])
            payload = command(amount="20.00", expected_basis_hash=order.json()["basis_hash"],
                executed_at=EXECUTED_AT, confirmed=True, reference="isolated-refund-reference")
            assert (await client.post(BASE + "/refunds/record-execution", json=payload)).status_code == 403
            assert (await client.get(BASE + "/customer-adjustments", params={"direction": "collection"})).status_code == 403
            assert (await client.get(BASE + "/ledger/bank/bank")).status_code == 403
        assert await read(db, "owner") == before
    run(scenario)


def test_provider_only_read_grant_cannot_read_other_provider_order_amounts():
    async def scenario(db, _):
        await order_fixture(db, number="1001", method="tamara")
        await order_fixture(db, number="2002", method="tabby")
        await shared_read_grant(db)
        await db.users.update_one({"id": "staff"}, {"$set": {
            "operational_balance_access.customer_refunds.sources": [{"kind": "provider", "id": "tamara"}]}})
        before = await read(db, "owner")
        async with web_client(db) as client:
            own = await client.get(BASE + "/refunds/order/1001")
            assert own.status_code == 200, own.text
            assert own.json()["methods"] == [{"id": "original", "label": "تمارا"}]
            hidden = await client.get(BASE + "/refunds/order/2002")
            assert hidden.status_code == 404, hidden.text
            assert "paid_amount" not in hidden.text
        assert await read(db, "owner") == before
    run(scenario)
