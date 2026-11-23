"""Running balances from the paginated mobile path must match the scoped account."""
import pytest

from operational_balance_store import STATES
from tests.test_operational_balance_workspace import BASE, grant, resource, run


@pytest.mark.parametrize("route", ["/ledger/store_driver/driver", "/movements?kind=store_driver&id=driver"])
def test_driver_balance_before_pagination_and_cache(route, monkeypatch):
    import operational_balance_workspace as ws

    async def scenario(db, client):
        await db.store_drivers.insert_one({"user_id": "owner", "id": "driver", "name": "الموصل", "status": "active"})
        def claim(key, amount, direction, day):
            return {"id": key, "party_type": "store_driver", "party_id": "driver", "currency": "SAR",
                    "kind": "shipping" if direction == "payable" else "cod", "direction": direction, "name": "الموصل",
                    "confirmed": amount, "expected": "0.00", "business_date": day}
        claims = [claim("delivered-a", "348.44", "receivable", "2026-11-20"),
                  claim("fee-b", "20.00", "payable", "2026-11-21")]
        await db[STATES].update_one({"_id": "owner"}, {"$set": {
            "engine.obligations": {row["id"]: row for row in claims}}})
        reconciliations = []
        project = ws.with_running_balances
        def observed(resource, rows):
            reconciliations.append(resource["kind"])
            return project(resource, rows)
        monkeypatch.setattr(ws, "with_running_balances", observed)
        sep = "&" if "?" in route else "?"
        url = BASE + route + sep + "page_only=true&account_history=true&page_size=1"
        for page, identity, amount in [(1, "fee-b", "-328.44"), (2, "delivered-a", "-348.44"), (1, "fee-b", "-328.44")]:
            response = await client.get(url + f"&page={page}")
            assert response.status_code == 200, response.text
            body = response.json()
            assert body["balance_status"] == "available"
            assert [(r["id"], r["balance_after"]) for r in body["items"]] == [(identity, amount)]
        assert reconciliations == ["store_driver"]

        for version, (balance, mode) in enumerate(((False, "all"), (True, "own")), start=1):
            await grant(db, operations=[], resources=[resource("store_driver", "driver", balance=balance, movements=mode)], version=version)
            restricted = await client.get(url, headers={"x-test-user": "staff"})
            assert restricted.status_code == 200, restricted.text
            assert restricted.json()["balance_status"] == "restricted"
            assert all(r["balance_after"] is None for r in restricted.json()["items"])
        assert reconciliations == ["store_driver"]

        # A mixed page only reconciles the carriers/drivers actually displayed.
        mixed = (await client.get(BASE + "/movements?page_only=true&account_history=true&page_size=2")).json()
        assert [(r["id"], r["balance_after"]) for r in mixed["items"]] == [
            ("fee-b", "-328.44"), ("delivered-a", "-348.44")]
    run(scenario)


def test_selected_bank_history_is_reconciled_and_legacy_fast_clients_keep_their_contract():
    async def scenario(db, client):
        url = BASE + "/ledger/bank/bank?page_only=true"
        legacy = (await client.get(url)).json()
        assert legacy["items"][0]["balance_after"] is None
        current = (await client.get(url + "&account_history=true")).json()
        assert current["balance_status"] == "available"
        assert current["items"][0]["balance_after"] == "1000.00"
    run(scenario)


def test_unreconciled_account_leaves_the_cell_empty():
    async def scenario(db, client):
        await db[STATES].update_one({"_id": "owner"}, {"$set": {
            "started_at": "", "openings.bank:bank.created_at": "", "openings.bank:bank.business_date": "invalid-date"}})
        response = await client.get(BASE + "/ledger/bank/bank?page_only=true&account_history=true")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["balance_status"] == "unreconciled" and body["balance_note"]
        assert body["items"][0]["balance_after"] is None
    run(scenario)
