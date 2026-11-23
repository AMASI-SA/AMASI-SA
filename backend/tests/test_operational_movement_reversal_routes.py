"""Exercise visible-account authorization and one monetary result through the real router."""
import asyncio
from copy import deepcopy

from operational_balance_store import digest, read
from test_operational_balance_web_page_grants import pages
from test_operational_balance_workspace import BASE, balance, entry, grant, resource, run


def command(actor, key="reverse-test-key"):
    return {"request_id": key, "reason": "تسجيل الحركة بالخطأ", "expected_session_scope": digest(["owner", actor])}


async def transfer(client, *, actor="owner", amount="25"):
    payload = entry(party_type="cash", party_id="cash", side="for_us", reason="transfer", amount=amount,
                    expected_session_scope=digest(["owner", actor]))
    response = await client.post(BASE + "/entries", headers={"X-Test-User": actor}, json=payload)
    assert response.status_code == 200, response.text
    return response.json()["movement_id"]


def test_repeat_concurrent_transfer_reversal_preserves_source_ledger_and_actor():
    async def scenario(db, client):
        mid = await transfer(client)
        stored = await read(db, "owner")
        original = deepcopy(next(m for m in stored["movements"] if m["id"] == mid))
        path = f"{BASE}/movements/{mid}/reverse"
        for endpoint in ["/movements", "/movements?page_only=true", "/ledger/cash/cash?page_only=true",
                         "/ledger/bank/bank?date_from=2026-11-01&date_to=2026-11-30"]:
            rows = (await client.get(BASE + endpoint)).json()["items"]
            assert next(row for row in rows if row["id"] == mid)["can_reverse"]
        assert (await client.post(path, json={**command("owner"), "amount": "10000"})).status_code == 422
        assert (await client.post(path, json={**command("owner"), "reason": " "})).status_code == 422
        saves = await asyncio.gather(*(client.post(path, json=command("owner", key))
                                       for key in ["reverse-key-a", "reverse-key-b", "reverse-key-a"]))
        assert [r.status_code for r in saves] == [200, 200, 200], [r.text for r in saves]
        assert saves[0].json() == saves[1].json() == saves[2].json()
        rid = saves[0].json()["id"]
        assert saves[0].json()["reverse_of"] == mid
        state = await read(db, "owner")
        assert next(m for m in state["movements"] if m["id"] == mid) == original
        assert [(m["id"], m["actor_id"]) for m in state["movements"] if m.get("reverse_of") == mid] == [(rid, "owner")]
        assert balance(state, "bank", "bank")["actual"] == "1000.00"
        assert balance(state, "cash", "cash")["actual"] == "500.00"
        assert (await client.post(f"{BASE}/movements/{rid}/reverse", json=command("owner", "reverse-reversal"))).status_code == 409
        for endpoint in ["/movements", "/ledger/bank/bank?page_only=true", "/movements?page_only=true"]:
            rows = (await client.get(BASE + endpoint)).json()["items"]
            by_id = {row["id"]: row for row in rows}
            assert by_id[mid]["reversal_id"] == rid and not by_id[mid].get("can_reverse")
            assert by_id[rid]["reverse_of"] == mid and not by_id[rid].get("can_reverse")
    run(scenario)


def test_employee_needs_log_write_on_web_and_separate_grant_and_both_accounts_on_web_and_mobile():
    async def scenario(db, client):
        accounts = [resource("bank", "bank", movements="all"), resource("cash", "cash", movements="all")]
        await grant(db, operations=["accounts.transfer"], resources=accounts)
        own = await transfer(client, actor="staff")
        another = await transfer(client)
        path = f"{BASE}/movements/{own}/reverse"
        web = {"X-Test-User": "staff"}
        mobile = {**web, "X-Test-Mobile": "yes"}
        assert (await client.post(path, headers=mobile, json=command("staff"))).status_code == 403
        await grant(db, operations=["movements.reverse"], resources=accounts, version=2)
        await pages(db, ("balances.log", "read"), ("balances.entry", "write"))
        journal = (await client.get(BASE + "/movements?page_only=true", headers=web)).json()["items"]
        assert not next(row for row in journal if row["id"] == own).get("can_reverse")
        assert (await client.post(path, headers=web, json=command("staff"))).status_code == 403
        await pages(db, ("balances.log", "write"))
        for headers in (web, mobile):
            assert (await client.post(f"{BASE}/movements/{another}/reverse", headers=headers, json=command("staff"))).status_code == 404
            good = (await client.get(BASE + "/movements?page_only=true", headers=headers)).json()["items"]
            assert next(row for row in good if row["id"] == own)["can_reverse"]
        await grant(db, operations=["movements.reverse"], resources=[accounts[0]], version=3)
        for headers in (web, mobile):
            assert (await client.post(path, headers=headers, json=command("staff"))).status_code == 404
        await grant(db, operations=["movements.reverse"], resources=accounts, version=4)
        assert (await client.post(path, headers=mobile, json=command("owner"))).status_code == 409
        saved = await client.post(path, headers=mobile, json=command("staff"))
        assert saved.status_code == 200, saved.text
        state = await read(db, "owner")
        reverse = next(m for m in state["movements"] if m["id"] == saved.json()["id"])
        assert reverse["actor_id"] == "staff" and reverse["source"] == "employee_app"
        await grant(db, operations=[], resources=accounts, version=5)
        assert (await client.post(path, headers=mobile, json=command("staff"))).status_code == 403
    run(scenario)

def test_resource_revocation_immediately_before_commit_aborts_both_legs(monkeypatch):
    import operational_balance_store as store

    async def scenario(db, client):
        accounts = [resource("bank", "bank"), resource("cash", "cash")]
        await grant(db, operations=["accounts.transfer", "movements.reverse"], resources=accounts)
        mid = await transfer(client, actor="staff")
        original_stage = store.stage_pages

        async def revoke_before_cas(database, audit_pages):
            await original_stage(database, audit_pages)
            await grant(db, operations=["movements.reverse"], resources=accounts[:1], version=2)

        monkeypatch.setattr(store, "stage_pages", revoke_before_cas)
        result = await client.post(f"{BASE}/movements/{mid}/reverse", headers={"X-Test-User": "staff"},
                                   json=command("staff"))
        assert result.status_code in {403, 404}, result.text
        state = await read(db, "owner")
        assert not any(m.get("reverse_of") for m in state["movements"])
        assert balance(state, "bank", "bank")["actual"] == "975.00"
        assert balance(state, "cash", "cash")["actual"] == "525.00"
    run(scenario)
