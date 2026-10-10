"""Recovery journals are permanent evidence; lease expiry is not a TTL policy."""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import APIRouter, FastAPI

import supplier_receiving_routes as r
import supplier_scan_attempts as attempts
from test_supplier_scan_recovery import env, seed, post_scan


@pytest.mark.asyncio
async def test_old_terminal_attempts_keep_dedup_and_recovery_evidence(env):
    db, http, _ = env
    session, pieces = await seed(env, 2)
    first = await post_scan(http, session['id'], pieces[0], 'retention-confirmed-1', 1)
    assert first.status_code == 200, first.text
    rejected = await http.post(f"/supplier-receiving-v1/sessions/{session['id']}/scan", json={
        'barcode': 'MEZAN-PIECE:' + 'f' * 32, 'client_request_id': 'retention-rejected-1',
    })
    assert rejected.status_code >= 400
    old = datetime.now(timezone.utc) - timedelta(days=400)
    await db[attempts.ATTEMPTS].update_many({}, {'$set': {'created_at': old, 'expires_at': old}})
    saved = await db[attempts.ATTEMPTS].find({}).to_list(10)
    assert {row['state'] for row in saved} == {'confirmed', 'rejected'}

    router = APIRouter()
    attempts.install_reconciler(router, db)
    app = FastAPI()
    app.include_router(router)
    async with app.router.lifespan_context(app):
        await attempts.expire_attempts(db)
        indexes = await db[attempts.ATTEMPTS].index_information()
        assert all('expireAfterSeconds' not in index for index in indexes.values())
        assert await db[attempts.ATTEMPTS].count_documents({}) == 2

    proof = await http.get(f"/supplier-receiving-v1/sessions/{session['id']}/scan-requests/retention-confirmed-1")
    assert proof.json()['outcome'] == 'confirmed' and proof.json()['committed']
    assert (await post_scan(http, session['id'], pieces[0], 'retention-confirmed-1', 1)).status_code == 200
    conflict = await post_scan(http, session['id'], pieces[1], 'retention-confirmed-1', 1)
    assert conflict.status_code == 409
    assert await db[r.RECEIVING_EVENTS].count_documents({'event_type': 'supplier_piece_scanned'}) == 1
    assert await db[r.PIECES].count_documents({'supplier_receiving_session_id': session['id']}) == 1
    rejected_proof = await http.get(f"/supplier-receiving-v1/sessions/{session['id']}/scan-requests/retention-rejected-1")
    assert rejected_proof.json()['outcome'] == 'rejected'
