"""The manual action owns its reads/CPU and observes an admitted atomic save."""
import asyncio
from copy import deepcopy
import json
from threading import Event

from fastapi import APIRouter, FastAPI
import pytest

from operational_balance_store import STATES, digest, fail
from operational_delivery_confirmation import PREFIX
from operational_delivery_confirmation_routes import attach_delivery_confirmation_routes
from operational_financial_activity_runtime import preparation_checkpoint, prepare_in_thread
from operational_demand_refresh import LEASES
from test_operational_balance_demand_refresh import DB
from test_operational_balance_delivery_confirmation import candidate, data_for, NOW, START


BODY = {'order_id': '100', 'order_number': '10', 'party_type': 'courier', 'party_id': 'carrier',
        'expected_session_scope': digest(['owner', 'owner']), 'visible': True}
SAVE = {**BODY, 'request_id': 'owner-confirm-one', 'expected_preview_hash': digest('preview'), 'confirmed_after_start': True}


class LocalDB(DB):
    def __init__(self):
        super().__init__()
        self.data[STATES]['owner']['started_at'] = START
        self.data['users'] = {'owner': {'id': 'owner', 'role': 'owner', 'is_active': True}}

    def __getattr__(self, name):
        return self[name]


def application(db):
    async def principal():
        return {'id': 'owner'}

    async def scope(*args):
        user = db.data['users']['owner'] if db else {'id': 'owner', 'role': 'owner'}
        if user.get('disabled'):
            fail('disabled', 'disabled', 403)
        return user, 'owner', 'web'

    router, app = APIRouter(), FastAPI()
    attach_delivery_confirmation_routes(router, db, principal, scope, lambda permission: principal)
    app.include_router(router)
    return app


def request_task(app, path, body):
    messages, sent = asyncio.Queue(), []
    messages.put_nowait({'type': 'http.request', 'body': json.dumps(body).encode(), 'more_body': False})

    async def receive():
        return await messages.get()

    async def send(message):
        sent.append(message)

    task = asyncio.create_task(app({'type': 'http', 'asgi': {'version': '3.0'}, 'http_version': '1.1',
        'method': 'POST', 'scheme': 'http', 'path': path, 'raw_path': path.encode(), 'query_string': b'',
        'headers': [(b'content-type', b'application/json')], 'client': ('127.0.0.1', 1000),
        'server': ('isolated', 80), 'root_path': ''}, receive, send))
    return task, messages, sent


def status(sent):
    return next(message['status'] for message in sent if message['type'] == 'http.response.start')


def test_preview_disconnect_joins_cpu_and_never_starts_confirmation(monkeypatch):
    import operational_delivery_confirmation_service as service
    async def scenario():
        entered, stopped = Event(), Event()
        writes = []
        def calculate():
            entered.set()
            try:
                while True:
                    preparation_checkpoint()
            finally:
                stopped.set()
        async def preview(*args, **kwargs):
            return await prepare_in_thread(calculate)
        async def forbidden(*args, **kwargs):
            writes.append(True)
        monkeypatch.setattr(service, 'preview', preview)
        monkeypatch.setattr(service, 'confirm', forbidden)
        task, messages, sent = request_task(application(None), '/delivery-after-start/preview', BODY)
        assert await asyncio.to_thread(entered.wait, 2)
        messages.put_nowait({'type': 'http.disconnect'})
        await asyncio.wait_for(task, 2)
        assert stopped.is_set() and not writes and status(sent) == 499
        assert not [task for task in asyncio.all_tasks() if task is not asyncio.current_task()]
    asyncio.run(scenario())


@pytest.mark.parametrize('cpu', [False, True])
def test_confirm_disconnect_before_save_cancels_and_joins_owned_read(cpu, monkeypatch):
    import operational_delivery_confirmation_service as service
    async def scenario():
        db, entered, stopped = LocalDB(), Event(), Event()
        before = deepcopy(db.data[STATES])
        async def snapshot(*args, **kwargs):
            if cpu:
                def calculate():
                    entered.set()
                    try:
                        while True:
                            preparation_checkpoint()
                    finally:
                        stopped.set()
                return await prepare_in_thread(calculate)
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                stopped.set()
        monkeypatch.setattr(service, '_preview', snapshot)
        task, messages, sent = request_task(application(db), '/delivery-after-start/confirm', SAVE)
        assert await asyncio.to_thread(entered.wait, 2)
        messages.put_nowait({'type': 'http.disconnect'})
        await asyncio.wait_for(task, 2)
        assert stopped.is_set() and status(sent) == 499
        assert db.data[STATES] == before and db.data[LEASES] == {}
        assert not [write for write in db.writes if write[0] == STATES]
        assert not [task for task in asyncio.all_tasks() if task is not asyncio.current_task()]
    asyncio.run(scenario())


def test_confirm_disconnect_after_cas_admission_observes_one_saved_proof(monkeypatch):
    import operational_delivery_confirmation_service as service
    event = candidate(data_for())
    monkeypatch.setattr(service, 'now', lambda: NOW)
    async def scenario():
        db = LocalDB()
        db.delay_cas = True
        async def snapshot(*args, **kwargs):
            state = deepcopy(db.data[STATES]['owner'])
            return state, {'can_confirm': True, 'preview_hash': SAVE['expected_preview_hash'],
                'session_scope': BODY['expected_session_scope']}, deepcopy(event), digest('source'), None
        monkeypatch.setattr(service, '_preview', snapshot)
        task, messages, sent = request_task(application(db), '/delivery-after-start/confirm', SAVE)
        await asyncio.wait_for(db.cas_entered.wait(), 2)
        messages.put_nowait({'type': 'http.disconnect'})
        await asyncio.sleep(0)
        db.cas_allowed.set()
        await asyncio.wait_for(task, 2)
        assert status(sent) == 200
        saved = db.data[STATES]['owner']
        assert saved['engine']['facts'][PREFIX + '100']['confirmed_at'] == NOW
        assert len(saved['audit']) == 1 and len(saved['requests']) == 1
        assert saved['movements'] == [] and saved['openings'] == {}
        assert len([write for write in db.writes if write[0] == STATES]) == 1
        assert db.data[LEASES] == {}
        assert not [task for task in asyncio.all_tasks() if task is not asyncio.current_task()]
    asyncio.run(scenario())
