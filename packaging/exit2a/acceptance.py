"""Real synthetic Mongo/ASGI acceptance. Execute only in the network-none harness."""
import asyncio
import importlib.metadata
import json
import os
import socket
import sys
from datetime import datetime, timezone
from pathlib import Path

from rehearsal import SYNTHETIC_ENV, create_app, validate_environment, validate_network

ROOT = Path('/opt/mezan/backend')


def snapshot(db):
    return {n: list(db[n].find({}).sort('_id', 1)) for n in sorted(db.list_collection_names()) if n != 'system.profile'}


def assert_no_writes(db, since):
    commands = {'insert', 'update', 'delete', 'findAndModify', 'create', 'createIndexes', 'drop', 'dropDatabase', 'collMod', 'renameCollection'}
    bad = [r for r in db['system.profile'].find({'ts': {'$gte': since}})
           if r.get('op') in {'insert', 'update', 'remove'} or commands.intersection(r.get('command', {}))]
    assert not bad, 'application import/lifecycle performed a Mongo write'


async def smoke(db):
    before = snapshot(db)
    marker = datetime.now(timezone.utc)
    tasks_before = asyncio.all_tasks()
    app = create_app()
    after_import = snapshot(db)
    if after_import != before:
        print('Import collection delta:', json.dumps({
            'added': sorted(set(after_import) - set(before)),
            'removed': sorted(set(before) - set(after_import)),
            'changed': sorted(n for n in set(before) & set(after_import) if before[n] != after_import[n]),
        }))
        print('Import operation metadata:', json.dumps([
            {'op': r.get('op'), 'ns': r.get('ns'), 'command_names': sorted(r.get('command', {}))}
            for r in db['system.profile'].find({'ts': {'$gte': marker}})
        ]))
    assert after_import == before, 'import changed fixture state'
    assert_no_writes(db, marker)
    import httpx
    async with app.router.lifespan_context(app):
        await asyncio.sleep(0.2)
        assert asyncio.all_tasks() == tasks_before, 'startup created a task'
        assert snapshot(db) == before, 'startup changed fixture state'
        assert_no_writes(db, marker)
        from boot_runtime import process_local_readiness_event
        assert not process_local_readiness_event.is_set()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://127.0.0.1:8001') as c:
            assert (await c.get('/api/live')).status_code == 200
            ready = await c.get('/api/ready')
            assert ready.status_code == 200 and ready.json()['phase'] == 'rehearsal_ready_no_initialization'
            health = await c.get('/api/health')
            assert health.status_code == 200
            assert not health.json().get('release', {}).get('verified_identity_available'), 'rehearsal must not claim governed release acceptance'
            assert (await c.get('/api/orders')).status_code == 401
            assert (await c.post('/api/qoyod/send', json={})).status_code == 403
            assert (await c.post('/api/auth/register', json={})).status_code == 403
            assert_no_writes(db, marker)
            # Auth writes below are explicitly allowed synthetic test operations.
            login = await c.post('/api/auth/login', json={'email': 'exit2a@example.com', 'password': 'Exit2A-synthetic-password!'})
            assert login.status_code == 200, 'synthetic login failed: ' + str(login.status_code)
            token = login.json()['access_token']
            c.headers['Authorization'] = 'Bearer ' + token
            me = await c.get('/api/auth/me')
            assert me.status_code == 401, 'password-only session must still require OTP'
            # Synthetic verified-session fixture, not an OTP delivery test or
            # policy override. Sign with the real helper and disposable key.
            from auth import create_access_token
            c.headers['Authorization'] = 'Bearer ' + create_access_token('exit2a-user', 'exit2a@example.com', mfa_verified=True)
            me = await c.get('/api/auth/me')
            assert me.status_code == 200 and me.json()['id'] == 'exit2a-user'
            orders = await c.get('/api/orders', params={'limit': 10})
            assert orders.status_code == 200
            assert orders.json()['total'] == 1 and orders.json()['items'][0]['order_number'] == 'EXIT2A-001'
            assert (await c.post('/api/auth/logout')).status_code == 200
        assert not process_local_readiness_event.is_set()
    assert app.state.readiness == 'stopped'
    assert asyncio.all_tasks() == tasks_before, 'shutdown leaked a task'
    assert db.qoyod_settings.find_one({'user_id': 'exit2a-user'})['auto_send'] is True
    print('PASS: real import; zero import/startup Mongo writes and tasks; health/readiness; password-only OTP denial; verified synthetic session/orders; shutdown')


def main():
    validate_environment(os.environ, ROOT)
    validate_network()
    for key, value in [('MONGO_URL', 'mongodb://198.51.100.1:27017'), ('DB_NAME', 'production'), ('JWT_SECRET', 'not-the-synthetic-key'), ('QOYOD_API_KEY', 'synthetic-invalid-injection')]:
        env = {**os.environ, key: value}
        try:
            validate_environment(env, ROOT)
        except RuntimeError:
            pass
        else:
            raise AssertionError('unsafe environment accepted')
    try:
        with socket.create_connection(('198.51.100.1', 443), timeout=1):
            raise AssertionError('external routing unexpectedly available')
    except OSError:
        pass
    print('PASS: external egress denied by network namespace; invalid configuration rejected')
    import pymongo
    import bcrypt
    client = pymongo.MongoClient(SYNTHETIC_ENV['MONGO_URL'], serverSelectionTimeoutMS=3000)
    db = client[SYNTHETIC_ENV['DB_NAME']]
    # This harness owns only the fresh tmpfs database. No restored/live data.
    assert not db.list_collection_names(), 'refuse nonempty rehearsal database'
    db.users.insert_one({'id': 'exit2a-user', 'email': 'exit2a@example.com', 'name': 'Synthetic Rehearsal', 'role': 'user', 'password_hash': bcrypt.hashpw(b'Exit2A-synthetic-password!', bcrypt.gensalt()).decode()})
    db.unified_orders.insert_one({'id': 'exit2a-order', 'user_id': 'exit2a-user', 'order_number': 'EXIT2A-001', 'order_status': 'pending', 'order_date': '2026-01-01', 'total_amount': 100})
    db.qoyod_settings.insert_one({'user_id': 'exit2a-user', 'enabled': True, 'auto_send': True, 'dry_run_mode': False, 'legacy_pipeline_frozen': False})
    db.command('profile', 2)
    asyncio.run(smoke(db))
    # Distributions are real and no Emergent distribution is installed.
    names = {d.metadata['Name'].lower() for d in importlib.metadata.distributions()}
    assert 'emergentintegrations' not in names
    entrypoints = [ep for d in importlib.metadata.distributions() for ep in d.entry_points]
    assert not any('emergentintegrations' in ep.value for ep in entrypoints)
    print('Installed distribution entry points checked:', len(entrypoints))
    print('Installed PDF versions:', json.dumps({n: importlib.metadata.version(n) for n in ['reportlab', 'pillow', 'PyMuPDF', 'qrcode', 'fonttools', 'python-bidi']}))
    print('Application loaded module count:', len(sys.modules))
    assert not any(n.startswith('emergentintegrations') for n in sys.modules)
    from exports import _register_font
    assert _register_font() == 'DejaVuSans', 'generic Arabic export font missing'
    from fontTools.ttLib import TTFont
    with TTFont(str(ROOT / 'fonts/Cairo-Bold.ttf')) as font:
        assert 0x0627 in font.getBestCmap(), 'bundled font lacks Arabic alef'
    os.environ['PYTEST_DISABLE_PLUGIN_AUTOLOAD'] = '1'
    import pytest
    code = pytest.main(['--noconftest', '-p', 'no:cacheprovider', '-q', str(ROOT/'tests/test_preparation_pdf_media_text_gap.py'), str(ROOT/'tests/test_preparation_pdf_card_file_number.py')])
    assert code == 0, 'locked PDF acceptance failed'
    client.close()


if __name__ == '__main__':
    main()
