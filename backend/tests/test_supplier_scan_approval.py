"""Real HTTP scan -> reconcile -> financial approval, without test rewrites."""
import uuid
from datetime import datetime, timedelta, timezone

import pytest

import supplier_receiving_routes as r
from supplier_scan_attempts import ATTEMPTS
from test_supplier_invoice_financial_integrity import env, seed, close

pytestmark = pytest.mark.asyncio


async def test_approval_requires_settled_scans_then_posts_exactly_once(env, monkeypatch):
    db, http, _ = env
    session, payload = await seed(db, (2100, 3200), services=True)
    originals = await db[r.PIECES].find({}).to_list(10)
    await db[r.RECEIVING_EVENTS].delete_many({})
    await db[r.PIECE_EVENTS].delete_many({})
    await db[r.SESSIONS].update_one({'id': session['id']}, {'$set': {'scan_count': 0}})
    async def no_instruction(*args, **kwargs):
        return None
    monkeypatch.setattr(r, 'enforce_stage_instructions', no_instruction)
    for index, piece in enumerate(originals):
        piece_id = uuid.uuid4().hex
        await db[r.PIECES].update_one({'_id': piece['_id']}, {
            '$set': {'piece_id': piece_id, 'supplier_id': 'supplier', 'supplier_dispatch_status': 'sent'},
            '$unset': {'supplier_receiving_session_id': '', 'receipt_event_id': ''},
        })
        payload['invoice_lines'][index]['piece_ids'] = [piece_id]
        response = await http.post(f"/supplier-receiving-v1/sessions/{session['id']}/scan", json={
            'barcode': 'MEZAN-PIECE:' + piece_id, 'client_request_id': f'approval-scan-{index}', 'quantity': 1,
        })
        assert response.status_code == 200, response.text
        assert response.json()['committed'] and response.json()['outcome'] == 'confirmed'

    # Even an expired, orphaned attempt blocks financial finalization until
    # backend reconciliation fences it; lease age alone is not receipt proof.
    await db[ATTEMPTS].insert_one({'_id': 'orphan', 'user_id': 'merchant', 'session_id': session['id'],
                                  'state': 'in_progress', 'expires_at': datetime.now(timezone.utc)-timedelta(seconds=1)})
    blocked = await close(http, session, payload)
    assert blocked.status_code == 409, blocked.text
    assert blocked.json()['detail']['code'] == 'supplier_receiving_scans_pending'
    assert await db[r.SUPPLIER_INVOICES].count_documents({}) == 0
    assert await db.general_ledger.count_documents({}) == 0
    await db[ATTEMPTS].update_one({'_id': 'orphan'}, {'$set': {'state': 'rejected'}})
    first = await close(http, session, payload)
    assert first.status_code == 200, first.text
    assert first.json()['financial_integrity_verified'] is True
    again = await close(http, session, payload)
    assert again.status_code == 200, again.text
    assert first.json()['supplier_invoice']['id'] == again.json()['supplier_invoice']['id']
    assert await db[r.SUPPLIER_INVOICES].count_documents({}) == 1
    assert await db.general_ledger.count_documents({}) == 2
    assert first.json()['supplier_invoice']['total_halalas'] == 5950
    for index in range(2):
        recovered = await http.get(f"/supplier-receiving-v1/sessions/{session['id']}/scan-requests/approval-scan-{index}")
        assert recovered.status_code == 200, recovered.text
        assert recovered.json()['outcome'] == 'confirmed'
