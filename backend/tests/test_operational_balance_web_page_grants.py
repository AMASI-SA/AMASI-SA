"""End-to-end web page + resource grants, with independent mobile/owner access."""
import asyncio

from operational_balance_store import STATES, digest
from operational_provider_statement_reconciliation import STATEMENTS
from test_operational_balance_access import BASE, grant, headers, run, save_policy
from test_operational_balance_statement_intake import content, workbook
from test_operational_balance_statement_reconciliation import START, client_for, document


async def pages(db, *grants, owner="owner", revision=1):
    await db.users.update_one({"id": "staff"}, {"$set": {"web_page_access": {
        "owner_id": owner, "revision": revision, "grants": [{"id": key, "mode": mode} for key, mode in grants],
    }}})


def test_browser_page_and_assigned_accounts_are_both_required():
    async def scenario(db, client):
        async def get(path, mobile=False):
            return await client.get(BASE + path, headers=headers(mobile=mobile))
        await pages(db, ("balances.assets", "read"))
        assert (await get("/workspace/context")).status_code == 403
        await save_policy(db, ["supplier.payment"], [
            grant("bank", "bank", movements="all", balance=True),
            grant("supplier", "supplier", movements="all", balance=True)])
        ctx = await get("/workspace/context")
        assert ctx.status_code == 200, ctx.text
        assert [(r["kind"], r["id"]) for r in ctx.json()["resources"]] == [("bank", "bank")]
        assert not ctx.json()["actor"]["is_owner"] and ctx.json()["operations"] == []
        assert (await get("/workspace/ledger/bank/bank")).status_code == 200
        assert (await get("/workspace/ledger/bank/bank2")).status_code == 404
        assert (await get("/workspace/ledger/bank/foreign-bank")).status_code == 404
        for route in ("/workspace/ledger/supplier/supplier", "/workspace/context?home_preview=true",
                      "/workspace/movements", "/workspace/reviews", "/workspace/refunds",
                      "/financial-position", "/provider-statements", "/access/catalog"):
            assert (await get(route)).status_code == 403, route
        assert (await get("/workspace/ledger/supplier/supplier", mobile=True)).status_code == 200
        await pages(db, ("balances.entry", "read"), revision=2)
        assert (await client.post(BASE + "/workspace/entries", headers=headers(), json={})).status_code == 403
        await pages(db, ("balances.assets", "read"), owner="other", revision=3)
        assert (await get("/workspace/context")).status_code == 403
    run(scenario)


def test_delegated_statement_history_detail_and_upload_are_provider_scoped():
    async def scenario():
        db, client = await client_for()
        async with client:
            statement = BASE + "/provider-statements"
            def staff_http():
                return {"x-test-user": "staff"}
            async def upload_staff(raw, request):
                return await client.post(statement + "/upload", headers=staff_http(), data={
                    "request_id": request, "expected_session_scope": digest(["owner", "staff"]),
                    "expected_started_at": START}, files={"file": ("test.xlsx", raw)})
            await pages(db, ("balances.statements", "write"))
            assert (await client.get(statement, headers=staff_http())).status_code == 403
            await save_policy(db, ["provider.statement_upload"], [
                {**grant("provider", "tabby", use=True, movements="all", balance=True), "report": True}])
            tabby, tamara = document("tabby"), document("tamara")
            await db[STATEMENTS].insert_many([tabby, tamara])
            await pages(db, ("balances.statements", "read"), revision=2)
            history = await client.get(statement, headers=staff_http())
            assert history.status_code == 200, history.text
            assert [s["provider"] for s in history.json()["items"]] == ["tabby"]
            assert history.json()["permissions"] == {"upload": False, "confirm": False}
            detail = await client.get(statement + "/" + tabby["id"] + "/review", headers=staff_http())
            assert detail.status_code == 200 and not detail.json()["permissions"]["confirm"]
            assert (await client.get(statement + "/" + tamara["id"] + "/review", headers=staff_http())).status_code in {403, 404}
            assert (await upload_staff(content(workbook("tabby")), "readonly-upload-one")).status_code == 403
            await pages(db, ("balances.statements", "write"), revision=3)
            before = await db[STATES].find_one({"_id": "owner"})
            assert (await upload_staff(content(workbook("tamara")), "wrong-provider-upload")).status_code == 403
            assert (await db[STATES].find_one({"_id": "owner"})) == before
            uploaded = await upload_staff(content(workbook("tabby")), "tabby-write-upload")
            assert uploaded.status_code == 200, uploaded.text
            assert uploaded.json()["statement"]["provider"] == "tabby"
            assert uploaded.json()["permissions"] == {"upload": True, "confirm": False}
            assert uploaded.json()["session_scope"] == digest(["owner", "staff"])
            assert (await client.post(statement + "/" + tabby["id"] + "/confirm",
                                      headers=staff_http(), json={})).status_code == 403
            # A confirmation-only employee can inspect decisions but cannot upload.
            await save_policy(db, ["provider.statement_confirm"], [
                {**grant("provider", "tabby", movements="all", balance=True), "report": True}])
            confirm_view = await client.get(statement + "/" + tabby["id"] + "/review", headers=staff_http())
            assert confirm_view.json()["permissions"] == {"upload": False, "confirm": True}
            assert (await upload_staff(content(workbook("tabby")), "confirm-only-upload")).status_code == 403
            # Revoking the resource write keeps only the review surface, including on replay.
            await save_policy(db, ["provider.settle"], [
                {**grant("provider", "tabby", movements="all", balance=True), "report": True}])
            assert (await upload_staff(content(workbook("tabby")), "tabby-write-upload")).status_code == 403
            assert (await client.get(statement + "/" + tabby["id"] + "/review", headers=staff_http())).status_code == 200
    asyncio.run(scenario())


def test_specialist_read_views_are_paged_and_scoped_by_source_resource_and_actor():
    async def scenario(db, client):
        await pages(db, ("balances.returns", "read"), ("balances.exchanges", "read"), ("balances.fees", "read"))
        await save_policy(db, [], [grant("bank", "bank", movements="own"),
                                   grant("supplier", "supplier", movements="own"),
                                   {**grant("provider", "tabby", balance=True), "report": True}])
        def returned(number, actor, bank):
            return {"id": "return-" + str(number), "order_number": number, "case_type": "item_return",
                    "created_by": actor, "created_at": "2026-11-01", "refund_source_type": "bank",
                    "refund_source_id": bank, "confirmed_by": actor, "status": "refunded", "note": "private"}
        def exchanged(number, actor, supplier):
            return {"id": "exchange-" + str(number), "order_number": number, "actor_id": "owner",
                    "purchases": [{"actor_id": actor, "supplier_id": supplier}], "created_at": "2026-11-02"}
        await db[STATES].update_one({"_id": "owner"}, {"$set": {
            "customer_returns": [returned(n, "staff", "bank") for n in range(1, 11)] + [
                returned(900, "owner", "bank"), returned(901, "staff", "bank2")],
            "customer_exchanges": [exchanged(701, "staff", "supplier"), exchanged(702, "owner", "supplier"),
                                   exchanged(703, "staff", "secret-supplier")],
        }})
        for owner, provider in (("owner", "tabby"), ("owner", "tamara"), ("other", "tabby")):
            await db.mz2_provider_fee_policies_v2.insert_one({"user_id": owner, "provider": provider, "policies": [{
                "id": owner + provider, "provider": provider, "status": "active", "currency": "SAR",
                "percentage": "6", "effective_from": "2026-01-01", "evidence": "private attachment",
            }]})
        async def get(which, page=1):
            response = await client.get(BASE + "/workspace/specialist/" + which, params={"page": page}, headers=headers())
            assert response.status_code == 200, response.text
            return response.json()
        first = await get("returns")
        assert first["page_size"] == len(first["items"]) == 8 and first["total"] == 10
        assert len((await get("returns", 2))["items"]) == 2
        assert {r["id"] for r in (await get("exchanges"))["items"]} == {"exchange-701"}
        fees = await get("fees")
        assert [(r["id"], r["label"]) for r in fees["items"]] == [("ownertabby", "تابي")]
        assert "private" not in str([first, fees])
        await pages(db, ("balances.fees", "read"), revision=2)
        assert (await client.get(BASE + "/workspace/specialist/returns", headers=headers())).status_code == 403
        assert (await client.get(BASE + "/customer-exchanges", headers=headers())).status_code == 403
    run(scenario)
