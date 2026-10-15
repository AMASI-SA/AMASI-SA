"""Expanded scope through the authenticated local API; no production imports."""
import httpx
from fastapi import FastAPI
from test_operational_balance_integration import run, started
from operational_balance_routes import make_operational_balance_router
from operational_balance_store import read, digest


def app_for(db, actor="owner"):
    app = FastAPI()
    async def principal():
        return {"id": actor}
    app.include_router(make_operational_balance_router(db, principal), prefix="/api")
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def test_active_native_expense_and_external_add_are_idempotent_and_permissioned():
    async def scenario(db):
        await started(db)
        async with app_for(db) as client:
            for kind in ("operating_expense", "external_person"):
                payload = {"request_id": "new-type-request1", "name": "نوع تجريبي", "currency": "SAR"}
                first = await client.post(f"/api/operational-balances/entities/{kind}", json=payload)
                assert first.status_code == 200, first.text
                again = await client.post(f"/api/operational-balances/entities/{kind}", json=payload)
                assert again.json() == first.json()
                choices = await client.get(f"/api/operational-balances/entities/{kind}")
                assert first.json()["id"] in [row["id"] for row in choices.json()["items"]]
            category = await db.expense_categories.find_one({"user_id": "owner"})
            assert category["code"] == category["id"]
            assert category["source"] == "operational_balance"
        async with app_for(db, "staff") as client:
            denied = await client.post("/api/operational-balances/entities/operating_expense", json=payload)
            assert denied.status_code == 200  # Active members need no operational role grant.
        assert (await read(db, "owner"))["movements"] == []
    run(scenario)


def test_expense_api_preserves_custody_bank_and_withdrawal_separation():
    async def scenario(db):
        await db.mezan_employees_v2.insert_one({"id": "ahmed", "user_id": "owner", "name": "أحمد", "status": "active"})
        await started(db)
        async with app_for(db) as client:
            common = {"expected_session_scope": digest(["owner", "owner"]), "currency": "SAR", "kind": "payment", "direction": "outgoing", "note": "اختبار معزول"}
            funding = {**common, "request_id": "custody-fund-01", "party_type": "employee_custody", "party_id": "ahmed",
                       "source_account_type": "bank", "bank_id": "bank", "amount": "500"}
            response = await client.post("/api/operational-balances/movements", json=funding)
            assert response.status_code == 200, response.text
            expense = {**common, "request_id": "custody-spend-01", "party_type": "operating_expense", "party_id": "fuel",
                       "source_account_type": "employee_custody", "bank_id": "ahmed", "amount": "50"}
            response = await client.post("/api/operational-balances/movements", json=expense)
            assert response.status_code == 200, response.text
            repeated = await client.post("/api/operational-balances/movements", json=expense)
            assert repeated.json() == response.json()
            result = (await client.get("/api/operational-balances/reports")).json()
            assert result["summary"]["actual_liquidity"] == "500.00"
            custody = next(row for row in result["parties"] if row["party_type"] == "employee_custody")
            assert custody["actual"] == "450.00"
            assert len((await read(db, "owner"))["movements"]) == 2
    run(scenario)
