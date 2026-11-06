"""Uncategorized products must not strand a supplier invoice draft.

Real HTTP, pricing, transactions and persisted invoice/ledger readback against
disposable loopback Mongo. No provider or Production writes.
"""
import uuid

import pytest

import supplier_receiving_routes as routes
from test_supplier_invoice_financial_integrity import env, seed, close

pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize("direct_assignment", [False, True])
async def test_uncategorized_receipt_allows_next_piece_refresh_and_one_invoice(env, monkeypatch, direct_assignment):
    db, http, _ = env
    session, payload = await seed(db, amounts=(2100, 1700))
    await db[routes.RECEIVING_EVENTS].delete_many({})
    await db[routes.PIECE_EVENTS].delete_many({})
    await db[routes.SESSIONS].update_one({"id": session["id"]}, {"$set": {
        "scan_count": 0, "supplier_snapshot.category_ids": ["clothes"],
    }})
    await db.settings.insert_one({"user_id": "merchant", "operational_flow": {
        "supplier_assignment_mode": "optional",
    }})
    update = {"$set": {
        "status": routes.PIECE_STATUS_ASSIGNED if direct_assignment else routes.PIECE_STATUS_IN_PROGRESS,
        "execution_status": "assigned" if direct_assignment else "supplier_sent",
        "responsible_employee_id": "employee",
    }, "$unset": {"supplier_receiving_session_id": "", "receipt_event_id": ""}}
    if not direct_assignment:
        update["$set"].update({"supplier_id": "supplier", "supplier_dispatch_status": "sent"})
    await db[routes.PIECES].update_many({}, update)
    for line in payload["invoice_lines"]:
        piece_id = uuid.uuid4().hex
        await db[routes.PIECES].update_one({"piece_id": line["piece_ids"][0]}, {"$set": {"piece_id": piece_id}})
        line["piece_ids"] = [piece_id]
    products_before = await db[routes.PRODUCTS].find({}, {"_id": 0}).sort("id", 1).to_list(10)
    assert len(products_before) == 2 and all(not row.get("cost_category_id") for row in products_before)
    async def no_instruction(*_args, **_kwargs):
        return None
    monkeypatch.setattr(routes, "enforce_stage_instructions", no_instruction)
    url = f"/supplier-receiving-v1/sessions/{session['id']}"
    pieces = await db[routes.PIECES].find({}, {"_id": 0}).sort("piece_id", 1).to_list(10)
    for index, piece in enumerate(pieces):
        scan = {"barcode": "MEZAN-PIECE:" + piece["piece_id"], "quantity": 1,
            "client_request_id": f"uncategorized-receipt-{index:03}"}
        if direct_assignment:
            offered = await http.post(url + "/scan", json=scan)
            assert offered.status_code == 409, offered.text
            assert offered.json()["detail"]["code"] == "supplier_receiving_direct_assignment_confirmation_required"
            scan.update({"client_request_id": f"uncategorized-consent-{index:03}",
                "confirm_direct_receiving_assignment": True})
        received = await http.post(url + "/scan", json=scan)
        assert received.status_code == 200, received.text
        refreshed = await http.post(url + "/refresh")
        assert refreshed.status_code == 200, refreshed.text
        scans = refreshed.json()["scans"]
        assert {row["piece_id"] for row in scans} == {row["piece_id"] for row in pieces[:index + 1]}
        assert all(row["reference_product_price_complete"] for row in scans)
        assert await db[routes.SUPPLIER_INVOICES].count_documents({}) == 0
        assert await db.general_ledger.count_documents({}) == 0
    approved = await close(http, session, payload)
    assert approved.status_code == 200, approved.text
    assert approved.json()["financial_integrity_verified"] is True
    invoice = approved.json()["supplier_invoice"]
    assert invoice["total_halalas"] == 3800
    repeated = await close(http, session, payload)
    assert repeated.status_code == 200, repeated.text
    assert repeated.json()["supplier_invoice"]["id"] == invoice["id"]
    assert await db[routes.SUPPLIER_INVOICES].count_documents({}) == 1
    assert await db.general_ledger.count_documents({}) == 2
    assert await db[routes.PRODUCTS].find({}, {"_id": 0}).sort("id", 1).to_list(10) == products_before
