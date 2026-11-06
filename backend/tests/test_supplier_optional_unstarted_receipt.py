"""Optional direct receipt must work before a supplier file starts.

Real HTTP, transactions and disposable loopback Mongo; no provider/live writes.
"""
import uuid

import pytest

import supplier_receiving_routes as routes
from test_supplier_scan_recovery import env, seed, barcode
from test_supplier_invoice_financial_integrity import env as invoice_env, seed as invoice_seed, close

pytestmark = pytest.mark.asyncio


async def unassigned(env):
    db, http, _ = env
    session, pieces = await seed(env, status=routes.PIECE_STATUS_ASSIGNED)
    piece = pieces[0]
    await db[routes.PIECES].update_one({"piece_id": piece["piece_id"]}, {
        "$set": {"execution_status": "assigned"},
        "$unset": {"supplier_id": "", "supplier_name": "", "supplier_dispatch_status": ""},
    })
    return db, http, session, piece


async def mode(db, value):
    await db.settings.update_one({"user_id": "merchant"}, {
        "$set": {"operational_flow.supplier_assignment_mode": value},
    }, upsert=True)


async def test_optional_search_offers_unstarted_unassigned_piece_without_writing_it(env):
    db, http, session, piece = await unassigned(env)
    await mode(db, "optional")
    before = await db[routes.PIECES].find_one({"piece_id": piece["piece_id"]})
    response = await http.get(f"/supplier-receiving-v1/sessions/{session['id']}/search", params={"q": piece["order_number"]})
    assert response.status_code == 200, response.text
    row = response.json()["pieces"][0]
    assert row["can_add_to_current_invoice"] is True, row
    assert row["requires_direct_assignment_confirmation"] is True
    assert await db[routes.PIECES].find_one({"piece_id": piece["piece_id"]}) == before


@pytest.mark.parametrize("undo", ["cancel", "remove"])
async def test_optional_scan_requires_consent_then_reserves_and_can_cancel_exactly(env, undo):
    db, http, session, piece = await unassigned(env)
    await mode(db, "optional")
    url = f"/supplier-receiving-v1/sessions/{session['id']}/scan"
    plain = {"barcode": barcode(piece), "quantity": 1, "client_request_id": "optional-offer-001"}
    offered = await http.post(url, json=plain)
    assert offered.status_code == 409, offered.text
    assert offered.json()["detail"]["code"] == "supplier_receiving_direct_assignment_confirmation_required"
    assert await db[routes.RECEIVING_EVENTS].count_documents({}) == 0
    approved = {**plain, "client_request_id": "optional-consent-001", "confirm_direct_receiving_assignment": True}
    received = await http.post(url, json=approved)
    assert received.status_code == 200, received.text
    saved = await db[routes.PIECES].find_one({"piece_id": piece["piece_id"]})
    assert saved["supplier_id"] == session["supplier_id"]
    assert saved["status"] == routes.PIECE_STATUS_IN_PROGRESS
    assert saved["responsible_employee_id"] == "preparer", "custody transfers at invoice approval only"
    assert saved["supplier_receiving_session_id"] == session["id"]
    repeat = await http.post(url, json=approved)
    assert repeat.status_code == 200, repeat.text
    assert await db[routes.RECEIVING_EVENTS].count_documents({"event_type": "supplier_piece_scanned"}) == 1
    if undo == "cancel":
        cancelled = await http.post(f"/supplier-receiving-v1/sessions/{session['id']}/cancel", json={"note": "Synthetic rollback"})
    else:
        cancelled = await http.post(f"/supplier-receiving-v1/sessions/{session['id']}/scans/remove", json={
            "barcode": barcode(piece), "expected_event_id": saved["receipt_event_id"],
        })
    assert cancelled.status_code == 200, cancelled.text
    restored = await db[routes.PIECES].find_one({"piece_id": piece["piece_id"]})
    assert restored["status"] == routes.PIECE_STATUS_ASSIGNED
    assert restored["responsible_employee_id"] == "preparer"
    assert not restored.get("supplier_id") and not restored.get("supplier_receiving_session_id")
    assert await db[routes.SUPPLIER_INVOICES].count_documents({}) == 0
    assert await db.general_ledger.count_documents({}) == 0


@pytest.mark.parametrize("change", ["hold", "missing_status", "policy_changed"])
async def test_optional_offer_is_revalidated_before_consent(env, change):
    db, http, session, piece = await unassigned(env)
    await mode(db, "optional")
    url = f"/supplier-receiving-v1/sessions/{session['id']}/scan"
    plain = {"barcode": barcode(piece), "quantity": 1, "client_request_id": "before-change-001"}
    assert (await http.post(url, json=plain)).status_code == 409
    if change == "hold":
        await db[routes.PIECES].update_one({"piece_id": piece["piece_id"]}, {"$set": {"active_hold_id": "hold-1"}})
    elif change == "missing_status":
        await db[routes.PIECES].update_one({"piece_id": piece["piece_id"]}, {"$unset": {"status": ""}})
    else:
        await mode(db, "required")
    response = await http.post(url, json={**plain, "client_request_id": "after-change-001",
        "confirm_direct_receiving_assignment": True})
    assert response.status_code == 409, response.text
    assert await db[routes.RECEIVING_EVENTS].count_documents({}) == 0


async def test_optional_assigned_scan_still_approves_invoice_and_custody_once(invoice_env, monkeypatch):
    from supplier_receipt_employee_custody import install_supplier_receipt_employee_custody
    # The production mobile request context installs this real custody wrapper.
    # A bare ASGI router fixture must compose it explicitly, not rely on which
    # unrelated test happened to import mobile_app_request_context first.
    install_supplier_receipt_employee_custody()
    db, http, _ = invoice_env
    session, payload = await invoice_seed(db)
    pid = uuid.uuid4().hex
    await db[routes.RECEIVING_EVENTS].delete_many({})
    await db[routes.PIECE_EVENTS].delete_many({})
    await db[routes.SESSIONS].update_one({"id": session["id"]}, {"$set": {"scan_count": 0}})
    await db[routes.PIECES].update_one({}, {"$set": {
        "piece_id": pid, "status": routes.PIECE_STATUS_ASSIGNED,
        "execution_status": "assigned", "responsible_employee_id": "preparer",
        "responsible_employee_name": "Synthetic preparer",
    }, "$unset": {"supplier_receiving_session_id": "", "receipt_event_id": ""}})
    payload["invoice_lines"][0]["piece_ids"] = [pid]
    await mode(db, "optional")
    async def no_instruction(*_args, **_kwargs):
        return None
    monkeypatch.setattr(routes, "enforce_stage_instructions", no_instruction)
    response = await http.post(f"/supplier-receiving-v1/sessions/{session['id']}/scan", json={
        "barcode": "MEZAN-PIECE:" + pid, "quantity": 1, "client_request_id": "approved-direct-001",
        "confirm_direct_receiving_assignment": True,
    })
    assert response.status_code == 200, response.text
    draft = await db[routes.PIECES].find_one({"piece_id": pid})
    assert draft["responsible_employee_id"] == "preparer"
    assert await db.general_ledger.count_documents({}) == 0
    approved = await close(http, session, payload)
    assert approved.status_code == 200, approved.text
    assert approved.json()["financial_integrity_verified"] is True
    repeated = await close(http, session, payload)
    assert repeated.status_code == 200
    assert approved.json()["supplier_invoice"]["id"] == repeated.json()["supplier_invoice"]["id"]
    assert await db[routes.SUPPLIER_INVOICES].count_documents({}) == 1
    assert await db.general_ledger.count_documents({}) == 2
    saved = await db[routes.PIECES].find_one({"piece_id": pid})
    assert saved["responsible_employee_id"] == "employee"


@pytest.mark.parametrize("value", ["required", None, "OPTIONAL"])
async def test_required_or_invalid_mode_still_blocks_unstarted_piece(env, value):
    db, http, session, piece = await unassigned(env)
    await mode(db, value)
    response = await http.post(f"/supplier-receiving-v1/sessions/{session['id']}/scan", json={
        "barcode": barcode(piece), "quantity": 1, "client_request_id": "mandatory-consent-001",
        "confirm_direct_receiving_assignment": True,
    })
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "supplier_piece_not_started"
    assert await db[routes.RECEIVING_EVENTS].count_documents({}) == 0


@pytest.mark.parametrize("status", [routes.PIECE_STATUS_BLOCKED, routes.PIECE_STATUS_CANCELLED, routes.PIECE_STATUS_RECEIVED, "unknown"])
async def test_optional_mode_never_makes_stopped_or_unknown_piece_receivable(env, status):
    db, http, session, piece = await unassigned(env)
    await mode(db, "optional")
    await db[routes.PIECES].update_one({"piece_id": piece["piece_id"]}, {"$set": {"status": status}})
    response = await http.post(f"/supplier-receiving-v1/sessions/{session['id']}/scan", json={
        "barcode": barcode(piece), "quantity": 1, "client_request_id": "blocked-consent-001",
        "confirm_direct_receiving_assignment": True,
    })
    assert response.status_code == 409, response.text
    assert await db[routes.RECEIVING_EVENTS].count_documents({}) == 0
