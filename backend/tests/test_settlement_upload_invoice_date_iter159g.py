"""Iter-159g — Upload endpoint must accept an `invoice_date` form field
and persist it to `header.settlement_date` so the unified overview
shows it as the transfer date for Salla files (which contain no date)."""
import os
import io
import pytest
import openpyxl
from httpx import AsyncClient, ASGITransport
from dotenv import load_dotenv

load_dotenv("/app/backend/.env")
import sys
sys.path.insert(0, "/app/backend")
from server import app  # noqa: E402
from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402


def _build_salla_xlsx() -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Invoice # 9000001"
    ws.append(["رقم الطلب", "إجمالي الطلب", "طريقة الدفع",
               "الرسوم", "المستحق قبل", "الضريبة", "المستحق بعد"])
    ws.append(["1001", 100, "مدى", 2, 98, 3, 95])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


@pytest.mark.asyncio
async def test_upload_persists_invoice_date_as_settlement_date():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Register
        email = f"iter159g-{os.urandom(3).hex()}@test.com"
        r = await client.post("/api/auth/register",
                              json={"name": "T", "email": email,
                                    "password": "pass1234"})
        assert r.status_code == 200, r.text
        token = r.json()["access_token"]
        h = {"Authorization": f"Bearer {token}"}

        # Upload Salla file WITH invoice_date
        xlsx = _build_salla_xlsx()
        files = {"file": ("salla_test.xlsx", xlsx,
                          "application/vnd.openxmlformats-officedocument."
                          "spreadsheetml.sheet")}
        data = {"provider_hint": "salla", "invoice_date": "2026-05-20"}
        r = await client.post("/api/payment-settlements/upload",
                              files=files, data=data, headers=h)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body.get("invoice_date") == "2026-05-20"
        file_id = body["file_id"]

        # Verify the file's header.settlement_date was persisted.
        c = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = c[os.environ["DB_NAME"]]
        doc = await db.settlement_files.find_one({"id": file_id})
        assert doc is not None
        assert doc["header"].get("settlement_date") == "2026-05-20"

        # Verify the unified overview shows it with source=manual.
        r = await client.get(
            "/api/payment-settlements/_overview/unified?year=2026&month=5",
            headers=h,
        )
        assert r.status_code == 200, r.text
        rows = r.json().get("rows", [])
        row = next(x for x in rows if x["file_id"] == file_id)
        assert row["settlement_date"] == "2026-05-20"
        assert row["settlement_date_source"] == "manual"

        # Cleanup
        await db.settlement_files.delete_one({"id": file_id})
        await db.settlement_entries.delete_many({"file_id": file_id})
