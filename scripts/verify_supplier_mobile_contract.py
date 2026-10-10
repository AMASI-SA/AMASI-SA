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
from test_supplier_scan_recovery import env, seed


async def main():
    patch = pytest.MonkeyPatch()
    fixture = env.__wrapped__(patch)
    state = await anext(fixture)
    db, http, _ = state
    session, _ = await seed(state, 50)
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
        proc = await asyncio.create_subprocess_exec('node', str(ROOT/'scripts/verify_supplier_mobile_contract.cjs'),
            str(Path(sys.argv[1]).resolve()), f'http://127.0.0.1:{port}', session['id'])
        assert await proc.wait() == 0, 'mobile runtime contract failed'
        assert await db[r.RECEIVING_EVENTS].count_documents({'event_type': 'supplier_piece_scanned'}) == 50
        assert await db[r.PIECES].count_documents({'supplier_receiving_session_id': session['id']}) == 50
        saved = await db[r.SESSIONS].find_one({'id': session['id']})
        assert saved['scan_count'] == 50 and not saved.get('scan_lock_token')
        assert await db[r.SUPPLIER_INVOICES].count_documents({}) == 0
        print('MOBILE_BACKEND_REPLICA_CONTRACT_PASS: 50 exact pieces, no duplicate POST, no invoice side effects')
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
