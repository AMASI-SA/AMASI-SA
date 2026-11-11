"""Real HTTP scan -> reconcile -> financial approval, without test rewrites."""
import uuid
from datetime import datetime, timedelta, timezone

import pytest

import supplier_receiving_routes as r
import supplier_scan_attempts as attempts
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


async def test_expired_scan_refresh_preserves_nineteen_pieces_and_closes_exactly_once(env):
    db, http, _ = env
    session, payload = await seed(db, (2100,) * 19)
    session_id = session['id']
    piece_ids = sorted(
        row['piece_id'] for row in await db[r.PIECES].find({}).to_list(19)
    )
    expired = await attempts.server_now(db) - timedelta(seconds=10)
    attempt_id = attempts.attempt_key('merchant', session_id, 'expired-before-approval')
    await db[ATTEMPTS].insert_one({
        '_id': attempt_id, 'user_id': 'merchant', 'session_id': session_id,
        'client_request_id': 'expired-before-approval', 'state': 'in_progress',
        'token': 'expired-approval-fence', 'expires_at': expired,
    })
    await db[r.SESSIONS].update_one({'id': session_id}, {'$set': {
        'scan_lock_token': 'expired-approval-fence', 'scan_lock_expires_at': expired,
    }})

    blocked = await close(http, session, payload)
    assert blocked.status_code == 409, blocked.text
    assert blocked.json()['detail']['code'] == 'supplier_receiving_scans_pending'
    assert await db[r.SUPPLIER_INVOICES].count_documents({}) == 0
    assert await db.general_ledger.count_documents({}) == 0

    refreshed = await http.post(f'/supplier-receiving-v1/sessions/{session_id}/refresh')
    assert refreshed.status_code == 200, refreshed.text
    assert sorted(row['piece_id'] for row in refreshed.json()['scans']) == piece_ids
    assert refreshed.json()['session']['scan_count'] == 19
    settled = await db[ATTEMPTS].find_one({'_id': attempt_id})
    assert settled['state'] == 'rejected'
    assert settled['detail']['code'] == 'supplier_receiving_scan_expired'
    assert await db[r.SUPPLIER_INVOICES].count_documents({}) == 0
    assert await db.general_ledger.count_documents({}) == 0

    approved = await close(http, session, payload)
    assert approved.status_code == 200, approved.text
    assert approved.json()['financial_integrity_verified'] is True
    invoice = approved.json()['supplier_invoice']
    assert invoice['total_halalas'] == 39900
    replay = await close(http, session, payload)
    assert replay.status_code == 200, replay.text
    assert replay.json()['idempotent'] is True
    assert replay.json()['supplier_invoice']['id'] == invoice['id']
    assert await db[r.SUPPLIER_INVOICES].count_documents({}) == 1
    assert await db.general_ledger.count_documents({}) == 2

    saved_invoice = await db[r.SUPPLIER_INVOICES].find_one({'id': invoice['id']})
    saved_ledger = await db.general_ledger.find({}).sort('id', 1).to_list(2)
    saved_session = await db[r.SESSIONS].find_one({'id': session_id})
    cancelled = await http.post(f'/supplier-receiving-v1/sessions/{session_id}/cancel', json={})
    assert cancelled.status_code == 409, cancelled.text
    assert cancelled.json()['detail']['code'] == 'supplier_receiving_session_closed'
    assert await db[r.SUPPLIER_INVOICES].find_one({'id': invoice['id']}) == saved_invoice
    assert await db.general_ledger.find({}).sort('id', 1).to_list(2) == saved_ledger
    assert await db[r.SESSIONS].find_one({'id': session_id}) == saved_session


@pytest.mark.parametrize('expiry_kind', ['active', 'missing', 'invalid'])
async def test_close_keeps_active_or_unproven_scan_locks_blocked(env, expiry_kind):
    db, http, _ = env
    session, payload = await seed(db)
    lock = {'scan_lock_token': 'pending-scan-fence'}
    if expiry_kind == 'active':
        lock['scan_lock_expires_at'] = await attempts.server_now(db) + timedelta(minutes=2)
    elif expiry_kind == 'invalid':
        lock['scan_lock_expires_at'] = 'not-a-verified-database-date'
    await db[r.SESSIONS].update_one({'id': session['id']}, {'$set': lock})
    before = await db[r.SESSIONS].find_one({'id': session['id']})

    blocked = await close(http, session, payload)

    assert blocked.status_code == 409, blocked.text
    assert blocked.json()['detail']['code'] == 'supplier_receiving_scan_busy'
    assert await db[r.SUPPLIER_INVOICES].count_documents({}) == 0
    assert await db.general_ledger.count_documents({}) == 0
    assert await db[r.SESSIONS].find_one({'id': session['id']}) == before
