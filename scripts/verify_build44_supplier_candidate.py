"""Run real mobile queue/repository code against local HTTP + replica-set Mongo.

Usage: python scripts/verify_supplier_mobile_contract.py <mobile-checkout>
Requires BUILD20_SCAN_TEST_MONGO_URL naming disposable loopback Mongo only.
No Android build, live credentials, remote API or production database.
"""
import asyncio
from pathlib import Path
import socket
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'backend'), str(ROOT / 'backend/tests')]
import pytest
import uvicorn
import supplier_receiving_routes as r
from test_supplier_invoice_financial_integrity import env, seed, close


async def main():
    patch = pytest.MonkeyPatch()
    fixture = env.__wrapped__(patch)
    state = await anext(fixture)
    db, http, _ = state
    session, payload = await seed(db, tuple(2100 for _ in range(50)), services=True)
    # Reset only this disposable fixture's pre-seeded receipts, then scan each
    # physical piece through the real HTTP route. Financial logic stays real.
    originals = await db[r.PIECES].find({}).sort('order_item_id', 1).to_list(50)
    await db[r.RECEIVING_EVENTS].delete_many({})
    await db[r.PIECE_EVENTS].delete_many({})
    await db[r.SESSIONS].update_one({'id': session['id']}, {'$set': {'scan_count': 0}})
    async def no_instruction(*args, **kwargs):
        return None
    patch.setattr(r, 'enforce_stage_instructions', no_instruction)
    for piece in originals:
        index = int(piece['order_item_id'])
        piece_id = f'{index + 1:032x}'
        await db[r.PIECES].update_one({'_id': piece['_id']}, {
            '$set': {'piece_id': piece_id, 'supplier_id': 'supplier', 'supplier_dispatch_status': 'sent'},
            '$unset': {'supplier_receiving_session_id': '', 'receipt_event_id': ''},
        })
        payload['invoice_lines'][index]['piece_ids'] = [piece_id]
    await r.ensure_supplier_receiving_indexes(db)
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(http._transport.app, host='127.0.0.1', log_level='error'))
    task = asyncio.create_task(server.serve(sockets=[sock]))
    try:
        while not server.started:
            if task.done():
                await task
                raise RuntimeError('local test server failed')
            await asyncio.sleep(.01)
        proc = await asyncio.create_subprocess_exec('node', str(ROOT/'scripts/verify_build44_supplier_candidate.cjs'),
            str(Path(sys.argv[1]).resolve()), f'http://127.0.0.1:{port}', session['id'])
        assert await proc.wait() == 0, 'mobile runtime contract failed'
        assert await db[r.RECEIVING_EVENTS].count_documents({'event_type': 'supplier_piece_scanned'}) == 50
        assert await db[r.PIECES].count_documents({'supplier_receiving_session_id': session['id']}) == 50
        saved = await db[r.SESSIONS].find_one({'id': session['id']})
        assert saved['scan_count'] == 50 and not saved.get('scan_lock_token')
        assert await db[r.SUPPLIER_INVOICES].count_documents({}) == 0
        first = await close(http, session, payload)
        assert first.status_code == 200, first.text
        body = first.json()
        assert body['financial_integrity_verified'] is True
        assert body['supplier_invoice']['total_halalas'] == 121250  # 50 * (2100 + 325)
        replay = await close(http, session, payload)
        assert replay.status_code == 200, replay.text
        assert replay.json()['supplier_invoice']['id'] == body['supplier_invoice']['id']
        assert await db[r.SUPPLIER_INVOICES].count_documents({}) == 1
        assert await db.general_ledger.count_documents({}) == 2
        assert await db.accounting_audit_log.count_documents({}) == 2
        for n in range(1, 51):
            proof = await http.get(f"/supplier-receiving-v1/sessions/{session['id']}/scan-requests/integrated-{n}")
            assert proof.status_code == 200 and proof.json()['outcome'] == 'confirmed', proof.text
        print('BUILD44_CANDIDATE_PASS: 50 scanner captures, lost ACK, offline GET, reconstructed queue; one invoice 121250 halalas, two ledger legs, replay idempotent')
    finally:
        server.should_exit = True
        await task
        sock.close()
        try:
            await anext(fixture)
        except StopAsyncIteration:
            pass
        patch.undo()


asyncio.run(main())
