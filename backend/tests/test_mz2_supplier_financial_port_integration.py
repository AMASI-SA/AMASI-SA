"""Exercise the production supplier router through the existing Track A port."""
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from test_mz2_supplier_payments_v2 import db, OWNER, USER, invoice, payment, OPERATIONS
from mezan_supplier_management_routes import make_mezan_supplier_management_router


@pytest.mark.asyncio
async def test_production_router_uses_canonical_funds_and_keeps_advance_separate(db):
    await db.mz2_financial_accounts.insert_one(dict(
        user_id=OWNER, id="canonical-bank", account_type="bank", currency="SAR", status="active",
    ))
    await invoice(db)
    app = FastAPI()
    app.include_router(make_mezan_supplier_management_router(db, lambda: USER))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://synthetic") as client:
        view = await client.get("/suppliers-v2/payment-workspace")
        assert view.status_code == 200
        assert view.json()["payment_available"] is True
        assert [row["id"] for row in view.json()["payment_accounts"]] == ["canonical-bank"]
        request = payment(invoice_id="inv-1", amount="400").model_dump(mode="json")
        result = await client.post("/suppliers-v2/s-v2/payments", json=request)
        assert result.status_code == 200, result.text
        replay = await client.post("/suppliers-v2/s-v2/payments", json=request)
        assert replay.status_code == 200 and replay.json()["replayed"]
        assert await db[OPERATIONS].count_documents({}) == 1
        request = payment(unallocated_kind="advance", amount="200").model_dump(mode="json")
        result = await client.post("/suppliers-v2/s-v2/payments", json=request)
        assert result.status_code == 200, result.text
        view = await client.get("/suppliers-v2/payment-workspace", params={"supplier_id": "s-v2"})
        assert view.json()["summary"]["outstanding_halalas"] == 60000
        assert view.json()["summary"]["advance_halalas"] == 20000
        before = await db.accounting_general_ledger_v2.count_documents({})
        for changes in ({"financial_account_id": "legacy-bank"}, {"financial_account_id": "missing"}):
            request = payment(amount="1", unallocated_kind="advance", **changes).model_dump(mode="json")
            result = await client.post("/suppliers-v2/s-v2/payments", json=request)
            assert result.status_code == 409
            assert result.json()["detail"]["code"] == "MZ2_LINK_REQUIRED"
        assert await db.accounting_general_ledger_v2.count_documents({}) == before
        assert await db.general_ledger.count_documents({}) == 0
