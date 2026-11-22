"""Customer price adjustments remain observations until a financial confirmation."""
from copy import deepcopy

from operational_balance_store import mutate, read
from test_operational_balance_workspace import run, grant, resource
from test_operational_workspace_refunds import order_fixture, bank_amount, command


BASE = "/api/operational-balances/workspace"


async def adjusted_order(db, *, number="1001", paid="200.00", total="199.20", hint="0.80"):
    raw = await order_fixture(db, number=number, paid=paid, total=total)
    raw["payment_actions"] = {
        "refund_action": {"paid_amount": paid, "refund_amount": hint, "refunded_transactions": []},
    }
    await db.unified_orders.update_one({"user_id": "owner", "order_number": number},
                                      {"$set": {"raw_by_source.salla_direct": raw}})
    return raw


def test_inbox_separates_both_directions_and_read_never_moves_or_reserves_money():
    async def scenario(db, client):
        await adjusted_order(db)
        await adjusted_order(db, number="1002", total="300.00", hint="0.00")
        await adjusted_order(db, number="1003", total="200.00", hint="0.00")
        before = await read(db, "owner")
        response = await client.get(BASE + "/customer-adjustments", params={"direction": "refund"})
        assert response.status_code == 200, response.text
        result = response.json()
        assert result["page_size"] == 8 and result["total"] == 1
        assert result["summary"] == {"refund_pending_amount": "0.80", "refund_pending_count": 1,
                                     "collection_pending_amount": "100.00", "collection_pending_count": 1,
                                     "unknown_count": 0}
        row = result["items"][0]
        assert row["order_number"] == "1001" and row["paid_amount"] == "200.00"
        assert row["pending_refund_amount"] == "0.80" and row["status"] == "needs_confirmation"
        assert row["confirmed_refunded_amount"] == "0.00"
        lookup = await client.get(BASE + "/refunds/order/1001")
        assert lookup.status_code == 200, lookup.text
        assert lookup.json()["available_amount"] == "200.00"
        assert lookup.json()["suggested_refund_amount"] == "0.80"
        assert lookup.json()["basis_hash"] == row["basis_hash"]
        assert "Private customer" not in response.text and "private-phone" not in response.text
        assert await read(db, "owner") == before
        assert bank_amount(before) == "1000.00"
    run(scenario)


def test_documented_refund_is_not_reported_as_executed_and_remains_visible():
    async def scenario(db, client):
        await adjusted_order(db, total="100.00", hint="100.00")
        async def document(state):
            state["customer_returns"] = [{"id": "documented", "case_type": "order_compensation",
                "order_id": "order-1001", "order_number": "1001", "currency": "SAR",
                "amount": "100.00", "status": "salla_refund_recorded", "refund_reference": "old-refund",
                "created_at": "2026-11-10T12:00:00+00:00", "created_by": "owner",
                "refund_source_type": None, "refund_source_id": None}]
        await mutate(db, "owner", document)
        result = (await client.get(BASE + "/customer-adjustments")).json()
        assert result["items"][0]["confirmed_refunded_amount"] == "0.00"
        assert result["items"][0]["pending_refund_amount"] == "100.00"
        lookup = await client.get(BASE + "/refunds/order/1001")
        assert lookup.status_code == 200, lookup.text
        assert lookup.json()["executed_refunded_amount"] == "0.00"
        assert lookup.json()["unconfirmed_refund_amount"] == "100.00"
        assert lookup.json()["available_amount"] == "100.00"
    run(scenario)


def test_adjustment_source_and_scope_changes_do_not_leak_other_owners():
    async def scenario(db, client):
        raw = await adjusted_order(db)
        foreign = deepcopy(raw)
        foreign.update(id="other-order", reference_id="9999")
        await db.unified_orders.insert_one({"user_id": "other", "order_number": "9999",
            "order_id": "other-order", "raw_by_source": {"salla_direct": foreign}})
        await grant(db, operations=["customer.refund_execute"],
                    resources=[resource("provider", "tamara")])
        headers = {"x-test-user": "staff"}
        response = await client.get(BASE + "/customer-adjustments", headers=headers)
        assert response.status_code == 200, response.text
        assert response.json()["total"] == 1 and "9999" not in response.text
        await grant(db, operations=[], resources=[], version=2)
        assert (await client.get(BASE + "/customer-adjustments", headers=headers)).status_code == 403
    run(scenario)


def test_candidate_inbox_excludes_prestart_and_unrelated_unpaid_unknown_orders():
    async def scenario(db, client):
        await adjusted_order(db)
        raw = await adjusted_order(db, number="1002")
        raw.update(created_at="2026-10-01T00:00:00+00:00", updated_at="2026-10-02T00:00:00+00:00")
        raw["payment"]["paid_at"] = "2026-10-01T01:00:00+00:00"
        await db.unified_orders.update_one({"user_id": "owner", "order_number": "1002"},
                                          {"$set": {"raw_by_source.salla_direct": raw}})
        await db.unified_orders.insert_one({"user_id": "owner", "order_id": "unknown-unpaid", "order_number": "1003",
            "raw_by_source": {"salla_direct": {"id": "unknown-unpaid", "reference_id": "1003", "currency": "SAR",
                "total": "200.00", "created_at": "2026-11-05T00:00:00+00:00", "payment_method": "unknown"}}})
        response = await client.get(BASE + "/customer-adjustments")
        assert response.status_code == 200, response.text
        result = response.json()
        assert result["total"] == 1 and result["items"][0]["order_number"] == "1001"
        assert result["summary"]["unknown_count"] == 0
    run(scenario)


def test_pending_request_without_a_source_hint_is_included_once_in_refund_total():
    async def scenario(db, client):
        await adjusted_order(db, total="200.00", hint="0.00")
        requested = await client.post(BASE + "/refunds", json=command(amount="50.00"))
        assert requested.status_code == 200, requested.text
        before = await read(db, "owner")
        response = await client.get(BASE + "/customer-adjustments", params={"direction": "refund"})
        assert response.status_code == 200, response.text
        result = response.json()
        assert result["total"] == 1 and result["summary"]["refund_pending_amount"] == "50.00"
        assert result["items"][0]["reserved_amount"] == "50.00"
        assert result["items"][0]["confirmed_refunded_amount"] == "0.00"
        assert await read(db, "owner") == before and bank_amount(before) == "1000.00"
    run(scenario)


def test_retained_original_capture_keeps_refund_lookup_and_inbox_when_source_paid_becomes_zero():
    async def scenario(db, client):
        raw = await adjusted_order(db, total="0.00", paid="0.00", hint="200.00")
        raw["payment"]["status"] = "refunded"
        await db.unified_orders.update_one({"user_id": "owner", "order_number": "1001"},
                                          {"$set": {"raw_by_source.salla_direct": raw}})
        async def frozen(state):
            state["engine"] = {"facts": {"capture:capture-1001": {
                "order_id": "order-1001", "provider": "tamara", "amount": "200.00"}},
                "obligations": {"provider:order-1001": {"id": "provider:order-1001", "kind": "provider",
                    "party_type": "provider", "party_id": "tamara", "name": "تمارا", "currency": "SAR",
                    "direction": "receivable", "confirmed": "200.00", "expected": "0.00"}}}
        await mutate(db, "owner", frozen)
        before = await read(db, "owner")
        lookup = await client.get(BASE + "/refunds/order/1001")
        assert lookup.status_code == 200, lookup.text
        assert lookup.json()["paid_amount"] == "200.00"
        assert lookup.json()["suggested_refund_amount"] == "200.00"
        result = await client.get(BASE + "/customer-adjustments", params={"direction": "refund"})
        assert result.status_code == 200, result.text
        assert result.json()["items"][0]["basis_hash"] == lookup.json()["basis_hash"]
        assert result.json()["summary"]["refund_pending_amount"] == "200.00"
        assert await read(db, "owner") == before
    run(scenario)
