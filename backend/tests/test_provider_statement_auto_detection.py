"""Mobile statement upload infers the provider from source bytes, never the UI.

Exercises the real FastAPI route and scoped canonical writer with synthetic
merchant data. No Salla/PayPal network, customer PII, or production writes.
"""
import pytest
import openpyxl

from operational_balance_store import STATES, digest, read
from operational_provider_statement_reconciliation import STATEMENTS
from test_provider_statement_employee_access import setup, grant, MOBILE, STAFF
from test_operational_balance_statement_intake import content, workbook
from test_operational_balance_statement_reconciliation import START


async def auto_upload(client, raw, filename, *, request_id="auto-provider-synthetic"):
    return await client.post(MOBILE + "/upload", headers=STAFF, data={
        # Intentionally NO provider: the canonical parser owns recognition.
        "request_id": request_id,
        "expected_session_scope": digest(["owner", "staff"]),
        "expected_started_at": START,
    }, files={"file": (filename, raw, "text/csv" if filename.endswith(".csv") else
                       "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["tabby", "tamara", "salla"])
async def test_native_file_only_detects_excel_platform_without_changing_financial_ledger(provider):
    db, client = await setup()
    async with client:
        assert (await grant(client, providers=[provider]))[0].status_code == 200
        before = await read(db, "owner")
        file_bytes = content(workbook(provider))
        result = await auto_upload(client, file_bytes, "settlement.xlsx")
        assert result.status_code == 200, result.text
        assert result.json()["statement"]["provider"] == provider
        assert result.json()["replayed"] is False
        saved = await read(db, "owner")
        assert saved["engine"]["facts"] == before["engine"]["facts"]
        assert saved["movements"] == before["movements"]
        replay = await auto_upload(client, file_bytes, "settlement.xlsx")
        assert replay.status_code == 200, replay.text
        assert replay.json()["replayed"] is True
        assert await read(db, "owner") == saved
        assert await db[STATEMENTS].count_documents({"owner_id": "owner"}) == 1


@pytest.mark.asyncio
async def test_native_autodetect_denies_employee_without_detected_provider_grant_before_file_save():
    db, client = await setup()
    async with client:
        assert (await grant(client, providers=["tabby"]))[0].status_code == 200
        before = await read(db, "owner")
        result = await auto_upload(client, content(workbook("tamara")), "settlement.xlsx")
        assert result.status_code == 403, result.text
        assert result.json()["detail"]["code"] == "provider_statement_provider_not_permitted"
        assert result.json()["detail"]["not_applied"] is True
        assert await db[STATEMENTS].count_documents({}) == 0
        assert await db.accounting_source_files.count_documents({}) == 0
        assert await read(db, "owner") == before


@pytest.mark.asyncio
async def test_native_autodetect_rejects_unrecognized_excel_and_never_creates_evidence():
    db, client = await setup()
    async with client:
        assert (await grant(client, providers=["tabby"]))[0].status_code == 200
        result = await auto_upload(client, content(openpyxl.Workbook()), "unknown.xlsx")
        assert result.status_code == 422, result.text
        assert result.json()["detail"].get("not_applied") is True
        assert await db[STATEMENTS].count_documents({}) == 0


def emkan_bytes():
    wb = openpyxl.Workbook()
    sheet = wb.active
    sheet.append(["Merchant name:", "Synthetic"])
    sheet.append(["Date:", "2026-11-27"])
    sheet.append(["Total settlement today:", 96.55])
    names = [
        "Merchant name", "Merchant Code", "Order ID", "Order creation date",
        "Original bill Amount", "REFUND STATUS", "Refunded amount",
        "Net bill amount", "Commission rate", "Commission Amount",
        "Refundable commission Rate", "Refundable commission Amount",
        "Non-Refundable commission rate", "Non-Refundable commission Amount",
        "Fixed Fee", "Total Fee", "VAT rate", "VAT amount",
        "Total deduction for EMKAN", "Settelment:", "PO:",
    ]
    sheet.append(names)
    values = {"Merchant name": "Synthetic", "Merchant Code": "demo",
              "Order ID": "synthetic-order", "Order creation date": "2026-11-27",
              "Original bill Amount": 100, "REFUND STATUS": "NO REFUND",
              "Refunded amount": 0, "Net bill amount": 100,
              "Commission rate": 3, "Commission Amount": 3,
              "Refundable commission Rate": 0, "Refundable commission Amount": 0,
              "Non-Refundable commission rate": 0, "Non-Refundable commission Amount": 3,
              "Fixed Fee": 0, "Total Fee": 3, "VAT rate": .15,
              "VAT amount": .45, "Total deduction for EMKAN": 3.45,
              "Settelment:": 96.55, "PO:": "synthetic-po"}
    sheet.append([values.get(name) for name in names])
    return content(wb)


@pytest.mark.asyncio
@pytest.mark.parametrize("provider,raw,filename", [
    ("emkan", emkan_bytes(), "report.xlsx"),
    ("paypal", (
        b"Date,Time,TimeZone,Name,Type,Status,Currency,Gross,Fee,Net,Transaction ID\n"
        b"11/27/2026,12:00,GMT,Synthetic,Payment,Completed,USD,100.00,-3.00,97.00,TEST-01\n"
    ), "activity.csv"),
])
async def test_native_auto_detects_new_providers_as_review_only(provider, raw, filename):
    db, client = await setup()
    async with client:
        assert (await grant(client, providers=[provider]))[0].status_code == 200
        before = await read(db, "owner")
        uploaded = await auto_upload(client, raw, filename)
        assert uploaded.status_code == 200, uploaded.text
        data = uploaded.json()
        assert data["statement"]["provider"] == provider
        assert data["counts"]["blocked"] >= 1
        assert data["applied_count"] == 0
        after = await read(db, "owner")
        assert after["movements"] == before["movements"]
        assert after["engine"]["facts"] == before["engine"]["facts"]
        assert after["engine"]["obligations"] == before["engine"]["obligations"]
