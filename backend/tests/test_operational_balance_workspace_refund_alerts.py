from copy import deepcopy

from operational_balance_store import STATES, read
from test_operational_balance_workspace import BASE, grant, resource, run
from test_operational_workspace_refunds import order_fixture


ENDPOINT = BASE + "/refund-alerts"
MOBILE = {"x-test-mobile": "yes"}


def test_salla_observations_are_owner_only_read_only_and_separate_from_registered_refunds():
    async def scenario(db, client):
        raw = await order_fixture(db)
        evidence = {"amount": {"amount": "18.75", "currency": "SAR"}, "date": "2026-11-07T12:30:00Z"}
        await db.unified_orders.update_one({"user_id": "owner"}, {"$set": {
            "raw_by_source.salla_direct.payment_actions.refund_action.refunded_transactions": [evidence]}})
        await order_fixture(db, number="1002")
        await db.unified_orders.update_one({"order_number": "1002"}, {"$set": {
            "raw_by_source.salla_direct.status": {"slug": "restored", "name": "مسترجع"}}})
        await order_fixture(db, number="1003")
        await db.unified_orders.update_one({"order_number": "1003"}, {"$set": {
            "raw_by_source.salla_direct.status": "cancelled"}})
        foreign = deepcopy(raw)
        foreign.update(id="other-order", reference_id="777", status="refunded")
        await db.unified_orders.insert_one({"user_id": "other", "raw_by_source": {"salla_direct": foreign}})
        await grant(db, operations=["customer.refund_request", "customer.refund_execute"],
                    resources=[resource("provider", "tabby", movements="all", balance=True)])
        staff = {**MOBILE, "x-test-user": "staff"}
        assert (await client.get(BASE + "/context", headers=staff)).json()["capabilities"]["refund_alerts"] is False
        assert (await client.get(ENDPOINT, headers=staff)).status_code == 403
        assert (await client.get(BASE + "/context", headers=MOBILE)).json()["capabilities"]["refund_alerts"] is True
        before = await read(db, "owner")
        page = await client.get(ENDPOINT + "?page_size=1", headers=MOBILE)
        assert page.status_code == 200, page.text
        assert page.json() == {"page": 1, "page_size": 1, "total": 2, "total_pages": 2, "items": [{
            "order_number": "1001", "amount": "18.75", "currency": "SAR", "observed_at": "2026-11-07T12:30:00+00:00"}]}
        second = (await client.get(ENDPOINT + "?page_size=1&page=99", headers=MOBILE)).json()
        assert second["page"] == 2 and second["items"][0]["order_number"] == "1002"
        assert second["items"][0]["amount"] is None and second["items"][0]["currency"] is None
        assert (await client.get(BASE + "/refunds", headers=MOBILE)).json()["total"] == 0
        assert before == await read(db, "owner")
        assert (await client.post(ENDPOINT, json={}, headers=MOBILE)).status_code == 405
    run(scenario)


def test_alerts_use_uncovered_amount_ignore_stale_sources_and_preopening_status_changes():
    async def scenario(db, client):
        async def source(number, **fields):
            await order_fixture(db, number=number)
            await db.unified_orders.update_one({"user_id": "owner", "order_number": number},
                {"$set": {"raw_by_source.salla_direct." + key: value for key, value in fields.items()}})

        def event(amount, date=None, currency="SAR"):
            return {"amount": {"amount": amount, "currency": currency}, **({"date": date} if date else {})}

        field = "payment_actions.refund_action.refunded_transactions"
        await source("2001", **{field: [event("15.00")]})
        await source("2002", **{field: [event("20.00")]})
        await source("2003", **{field: [event("25", currency="USD")]})
        await source("2004", status="refunded", created_at="2026-10-01T00:00:00Z")
        await source("2005", created_at="2026-10-01T00:00:00Z", **{field: [
            event("100", "2026-10-04T00:00:00Z"), event("5", "2026-11-04T00:00:00Z")]})
        await source("2006", status="refunded")
        await db.unified_orders.update_one({"order_number": "2006"}, {"$set": {"g47_salla_snapshot.requires_authoritative_refresh": True}})
        def case(number, amount, status="salla_refund_confirmed"):
            return {"id": "record-" + number + status, "order_id": "order-" + number,
                    "status": status, "amount": amount}
        await db[STATES].update_one({"_id": "owner"}, {"$set": {"customer_returns": [
            case("2001", "2.50"), case("2001", "10.00", "pending"), case("2002", "20.00")]}})
        before = await read(db, "owner")
        result = await client.get(ENDPOINT, headers=MOBILE)
        assert result.status_code == 200, result.text
        assert {r["order_number"]: r["amount"] for r in result.json()["items"]} == {
            "2001": "12.50", "2003": None, "2005": "5.00"}
        assert await read(db, "owner") == before
    run(scenario)
