"""Owner-only technical commit diagnostic. Never policy/dispatch authority."""
import asyncio
import hashlib
import uuid
from datetime import datetime, timezone

from fastapi import Depends
from pydantic import BaseModel, ConfigDict
from pymongo import ReadPreference, timeout
from pymongo.read_concern import ReadConcern
from pymongo.write_concern import WriteConcern

from .google_ads_automation_policy import _owner
from .google_ads_management import problem

SLOTS = 'google_storage_commit_probe_receipts_v1'
PAYLOADS = 'google_storage_commit_probe_payloads_v1'
TIMEOUT_SECONDS = 20
WC = {'w': 'majority', 'j': True, 'wtimeout': 4000}
_active = None


class EmptyRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')


def _clock(): return asyncio.get_running_loop().time()


def _result(status='unverified', verified=False):
    return {'scope': 'technical_replica_commit_readback_only', 'status': status,
        'replica_set_verified': verified, 'commit_majority_j_ack_verified': verified,
        'majority_readback_verified': verified, 'cleanup_verified': verified,
        'technical_receipt_retained': (True if status in ('verified', 'historical_completed_receipt', 'reconciliation_required')
            else False if status in ('unsupported_topology', 'no_receipt') else None), 'execution_authorized': False,
        'funding_ready': False, 'policy_permissions_verified': False, 'provider_writes': 0,
        'topology_freshness_guaranteed': False}


def _fail(status='unverified'):
    error = problem('google_storage_commit_' + status,
        'لم يكتمل التحقق التقني. لا تعِد الحفظ أو الحذف بالتخمين؛ لم يُمنح أي تفويض تشغيل.', 503)
    error.detail.update(_result(status))
    raise error


def _ack(reply, count=None):
    if (not isinstance(reply, dict) or type(reply.get('ok')) not in (int, float) or reply['ok'] != 1
        or 'writeConcernError' in reply or 'writeErrors' in reply
        or count is not None and (type(reply.get('n')) is not int or reply['n'] != count)):
        _fail()


async def _end(session):
    future = asyncio.ensure_future(session.end_session())
    cancelled = False
    while not future.done():
        try: await asyncio.shield(future)
        except asyncio.CancelledError: cancelled = True
    future.result()
    if cancelled: raise asyncio.CancelledError()


def _check(state):
    if state['stop'] or _clock() >= state['deadline']: _fail('deadline')


async def _command(db, command, session):
    return await db.command(command, session=session, read_preference=ReadPreference.PRIMARY)


async def _find(db, collection, query, session, majority=True):
    command = {'find': collection, 'filter': query, 'limit': 2, 'singleBatch': True}
    if majority: command['readConcern'] = {'level': 'majority'}
    reply = await _command(db, command, session); _ack(reply)
    rows = (reply.get('cursor') or {}).get('firstBatch')
    if not isinstance(rows, list) or len(rows) > 1 or any(not isinstance(row, dict) for row in rows): _fail()
    return rows[0] if rows else None


async def _update(db, query, values, session, transactional=False):
    command = {'update': SLOTS, 'updates': [{'q': query, 'u': {'$set': values}, 'multi': False, 'upsert': False}]}
    if values.get('state') == 'reserved': command['updates'][0]['u']['$unset'] = {'checked_at': ''}
    if not transactional: command['writeConcern'] = dict(WC)
    reply = await _command(db, command, session)
    _ack(reply, 1)
    if type(reply.get('nModified')) is not int or reply['nModified'] != 1: _fail()


def _slot_id(owner): return 'owner_' + hashlib.sha256(owner.encode()).hexdigest()


def _valid_slot(row, owner):
    return (isinstance(row, dict) and row.get('_id') == _slot_id(owner) and row.get('owner_id') == owner
        and row.get('schema_version') == 1 and type(row.get('version')) is int and row['version'] > 0
        and isinstance(row.get('attempt_id'), str) and len(row['attempt_id']) == 32
        and isinstance(row.get('nonce'), str) and len(row['nonce']) == 32
        and row.get('state') in ('reserved', 'committed', 'completed'))


async def _probe(db, owner, state):
    session = await db.client.start_session()
    try:
        hello = await _command(db, 'hello', session)
        if (not isinstance(hello, dict) or not isinstance(hello.get('setName'), str) or not hello['setName']
            or hello.get('msg') == 'isdbgrid' or type(hello.get('logicalSessionTimeoutMinutes')) is not int):
            _fail('unsupported_topology')
        _check(state)
        prior = await _find(db, SLOTS, {'_id': _slot_id(owner)}, session)
        attempt, nonce = uuid.uuid4().hex, uuid.uuid4().hex
        values = {'owner_id': owner, 'schema_version': 1, 'version': (prior['version'] + 1) if _valid_slot(prior, owner) else 1,
            'attempt_id': attempt, 'nonce': nonce, 'state': 'reserved'}
        _check(state)
        if prior is None:
            slot = {'_id': _slot_id(owner), **values}
            _ack(await _command(db, {'insert': SLOTS, 'documents': [slot], 'writeConcern': dict(WC)}, session), 1)
        else:
            if not _valid_slot(prior, owner) or prior['state'] != 'completed': _fail('reconciliation_required')
            await _update(db, {key: prior[key] for key in ('_id', 'owner_id', 'attempt_id', 'version', 'state', 'nonce')}, values, session)
            slot = {'_id': prior['_id'], **values}
        binding = {key: slot[key] for key in ('_id', 'owner_id', 'attempt_id', 'version', 'state', 'nonce')}
        payload = {'_id': 'probe_' + attempt, 'owner_id': owner, 'attempt_id': attempt, 'nonce': nonce, 'probe_only': True}
        _check(state)
        session.start_transaction(read_concern=ReadConcern('snapshot'), write_concern=WriteConcern('majority', j=True),
            read_preference=ReadPreference.PRIMARY, max_commit_time_ms=4000)
        if await _find(db, SLOTS, binding, session, majority=False) != slot: _fail()
        await _update(db, binding, {'state': 'committed'}, session, transactional=True)
        _ack(await _command(db, {'insert': PAYLOADS, 'documents': [payload]}, session), 1)
        _check(state)
        _ack(await db.client.admin.command({'commitTransaction': 1, 'writeConcern': dict(WC), 'maxTimeMS': 4000}, session=session))
    finally:
        await _end(session)
    # Raw commit does not update public driver session state. Close that session
    # before fresh PRIMARY/majority reads, never read back inside the old txn.
    session = await db.client.start_session()
    try:
        committed = {**slot, 'state': 'committed'}
        if await _find(db, SLOTS, {'_id': slot['_id']}, session) != committed: _fail('readback_unverified')
        if await _find(db, PAYLOADS, {'_id': payload['_id']}, session) != payload: _fail('readback_unverified')
        _check(state)
        _ack(await _command(db, {'delete': PAYLOADS, 'deletes': [{'q': payload, 'limit': 1}], 'writeConcern': dict(WC)}, session), 1)
        if await _find(db, PAYLOADS, {'_id': payload['_id']}, session) is not None: _fail('cleanup_unconfirmed')
        _check(state)
        # This marker is written ONLY after payload delete ACK and majority
        # absence were proved. A later explicit POST may CAS a completed receipt;
        # it never resends/deletes an uncertain prior probe.
        checked = datetime.now(timezone.utc).isoformat()
        await _update(db, {**binding, 'state': 'committed'}, {'state': 'completed', 'checked_at': checked}, session)
        if await _find(db, SLOTS, {'_id': slot['_id']}, session) != {**committed, 'state': 'completed', 'checked_at': checked}: _fail()
        return _result('verified', True)
    finally:
        await _end(session)


async def _latest(db, owner, state):
    session = await db.client.start_session()
    try:
        row = await _find(db, SLOTS, {'_id': _slot_id(owner)}, session)
        result = _result('no_receipt' if row is None else 'reconciliation_required')
        if _valid_slot(row, owner) and row['state'] == 'completed': result['status'] = 'historical_completed_receipt'
        return result  # Historical metadata never becomes fresh capability.
    finally: await _end(session)


async def _run(db, owner, work):
    global _active
    if _active is not None and not _active['task'].done(): _fail('busy')
    state = {'deadline': _clock() + TIMEOUT_SECONDS, 'stop': False}
    async def original():
        remaining = state['deadline'] - _clock()
        if remaining <= 0: _fail('deadline')
        with timeout(remaining): return await work(db, owner, state)
    state['task'] = asyncio.create_task(original()); _active = state
    def consume(done):
        global _active
        if not done.cancelled(): done.exception()
        if _active is state: _active = None
    state['task'].add_done_callback(consume)
    try:
        done, _ = await asyncio.wait({state['task']}, timeout=max(0, state['deadline'] - _clock()))
        if not done or _clock() >= state['deadline']:
            state['stop'] = True; _fail('deadline')
        return state['task'].result()
    except asyncio.CancelledError:
        state['stop'] = True; raise
    except Exception as error:
        detail = getattr(error, 'detail', None)
        if isinstance(detail, dict) and detail.get('code', '').startswith('google_storage_commit_'): raise
        _fail()


def attach_google_ads_storage_commit_readiness_routes(router, db, current_user):
    @router.post('/google_ads/automation/storage-commit-readiness')
    async def verify(body: EmptyRequest, user=Depends(current_user)):
        actor = await _owner(db, user)
        return await _run(db, actor['user_id'], _probe)

    @router.get('/google_ads/automation/storage-commit-readiness')
    async def latest(user=Depends(current_user)):
        actor = await _owner(db, user)
        return await _run(db, actor['user_id'], _latest)
