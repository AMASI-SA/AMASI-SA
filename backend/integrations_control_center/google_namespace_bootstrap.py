"""Explicit prepare/execute of absent fixed namespaces; never ads authority.

Intentionally unattached. Catalog observations bracket work; they are not an
atomic catalog snapshot or a barrier against unrelated privileged DB writers.
"""
import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import re
import uuid

from bson import BSON, Binary
from bson.int64 import Int64
from fastapi import Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from pymongo import ReadPreference, timeout
from pymongo.read_concern import ReadConcern
from pymongo.write_concern import WriteConcern


TARGETS = {
    'standing_policy': 'mezan_google_ads_automation_policies_v1',
    'creation_policy': 'mezan_google_ads_automation_creation_policies_v1',
    'account_guard': 'mezan_google_ads_automation_budget_guards_v1',
    'engine_probe': 'google_ads_budget_guard_engine_probes_v1',
}
INDEX_NAMES = {'standing_policy': 'google_automation_tenant_account',
               'creation_policy': 'google_creation_tenant_account'}
SLOTS = 'google_storage_commit_probe_receipts_v1'
SLOT_ID = 'namespace_bootstrap_v1'
OWNER_FIELDS = ('id', 'role', 'disabled', 'is_active', 'deleted_at', 'password_updated_at')
TIMEOUT_SECONDS = 20
COMMAND_MS = 1500
CLEANUP_SECONDS = 2
PREPARATION_SECONDS = 600
MAX_REPLY_BYTES = 256 * 1024  # Post-materialization bound, not a transport memory cap.
WC = {'w': 'majority', 'j': True, 'wtimeout': 3000}
_active = None


def _ordered(value):
    if isinstance(value, dict): return {k: _ordered(value[k]) for k in sorted(value)}
    if isinstance(value, list): return [_ordered(x) for x in value]
    return value


def _bytes(value): return BSON.encode(_ordered(value))
def _digest(value): return hashlib.sha256(_bytes(value)).hexdigest()


MANIFEST = {'schema': 1, 'targets': TARGETS, 'policy_index_names': INDEX_NAMES,
            'policy_keys': [['user_id', 1], ['account_id', 1]], 'ordinary_defaults': True}
MANIFEST_DIGEST = _digest(MANIFEST)


class PrepareRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    request_nonce: str = Field(pattern=r'^[0-9a-f]{32}$')
    acknowledge_preparation_only: bool


class ExecuteRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    request_nonce: str = Field(pattern=r'^[0-9a-f]{32}$')
    execution_token: str = Field(pattern=r'^[0-9a-f]{64}$')
    observation_digest: str = Field(pattern=r'^[0-9a-f]{64}$')
    manifest_digest: str = Field(pattern=r'^[0-9a-f]{64}$')
    acknowledge_storage_only: bool


def _result(status, **extra):
    return {'scope': 'fixed_namespace_bootstrap', 'status': status,
        'namespace_contract_verified': False, 'execution_authorized': False,
        'funding_ready': False, 'policy_configuration_verified': False,
        'legacy_bindings_verified': False, 'future_permissions_guaranteed': False,
        'atomic_catalog_snapshot_verified': False, 'new_uuid_lineage_verified': False,
        'provider_writes': 0, 'business_document_writes': 0, **extra}


def _fail(code, status=503):
    raise HTTPException(status, detail={'code': 'google_namespace_bootstrap_' + code,
        'message_ar': 'لم تكتمل تهيئة التخزين. لا تعِد الإرسال بالتخمين؛ لم يُمنح تفويض تشغيل.',
        **_result(code)})


def _check(state):
    if state['stop'] or asyncio.get_running_loop().time() >= state['deadline']:
        _fail('deadline')


def _ack(reply, count=None):
    if (not isinstance(reply, dict) or type(reply.get('ok')) not in (int, float)
        or reply['ok'] != 1 or 'writeConcernError' in reply or 'writeErrors' in reply
        or count is not None and (type(reply.get('n')) is not int or reply['n'] != count)):
        _fail('ack_unconfirmed')


async def _command(db, value, session, state):
    _check(state)
    try:
        reply = await db.command(value, session=session, read_preference=ReadPreference.PRIMARY)
    except BaseException:
        if next(iter(value)) in ('listCollections', 'listIndexes'):
            state['resource_unconfirmed'] = True
        raise
    _ack(reply)
    if len(_bytes(reply)) > MAX_REPLY_BYTES: _fail('reply_bound')
    return reply


async def _rows(db, command, session, state, limit):
    reply = await _command(db, command, session, state)
    cursor = reply.get('cursor')
    if not isinstance(cursor, dict) or not isinstance(cursor.get('firstBatch'), list):
        state['resource_unconfirmed'] = True; _fail('cursor_unproved')
    cid = cursor.get('id')
    if type(cid) not in (int, Int64) or cid < 0:
        state['resource_unconfirmed'] = True; _fail('cursor_unproved')
    if cid:
        ns = cursor.get('ns'); prefix = db.name + '.'
        if not isinstance(ns, str) or not ns.startswith(prefix):
            state['resource_unconfirmed'] = True; _fail('cursor_unproved')
        try:
            closed = await db.command({'killCursors': ns[len(prefix):], 'cursors': [cid]},
                session=session, read_preference=ReadPreference.PRIMARY)
            _ack(closed)
            if cid not in closed.get('cursorsKilled', []) and cid not in closed.get('cursorsNotFound', []):
                raise ValueError('unconfirmed cursor close')
        except BaseException:
            state['resource_unconfirmed'] = True; _fail('cursor_cleanup_unconfirmed')
        _fail('incomplete_cursor')
    rows = cursor['firstBatch']
    if len(rows) > limit or any(not isinstance(r, dict) for r in rows): _fail('rows_unproved')
    return rows


async def _find(db, name, query, session, state, *, limit=2, projection=None, transactional=False):
    command = {'find': name, 'filter': query, 'limit': limit, 'singleBatch': True, 'maxTimeMS': COMMAND_MS}
    if projection is not None: command['projection'] = projection
    if not transactional: command['readConcern'] = {'level': 'majority'}
    return await _rows(db, command, session, state, limit)


def _auth(user):
    if (not isinstance(user, dict) or not isinstance(user.get('id'), str)
        or not 1 <= len(user['id']) <= 200 or not isinstance(user.get('role'), str)
        or user['role'].strip().lower() != 'owner' or user.get('disabled') is True
        or user.get('is_active') is False or user.get('deleted_at') or user.get('_review_session') is not None):
        _fail('owner_required', 403)
    if any(k in user and user[k] is not None and type(user[k]) is not bool for k in ('disabled', 'is_active')):
        _fail('owner_unproved', 403)
    private = user.get('_session_client')
    if private is not None and (not isinstance(private, str) or len(private) > 100): _fail('owner_unproved', 403)
    return {k: user[k] for k in OWNER_FIELDS if k in user}


def _auth_digest(user):
    return _digest({'owner': _auth(user), 'session_client': user.get('_session_client'), 'review_session': None})


async def _owner(db, user, session, state, transactional=False):
    expected = _auth(user)
    rows = await _find(db, 'users', {'id': expected['id']}, session, state,
        projection={'_id': 0, **{k: 1 for k in OWNER_FIELDS}}, transactional=transactional)
    if len(rows) != 1 or _bytes(_auth(rows[0])) != _bytes(expected): _fail('owner_changed', 403)
    return expected


def _index(row, name, database):
    if not isinstance(row, dict) or set(row) - {'v', 'key', 'name', 'unique', 'ns'}: _fail('index_contract_unproved')
    if type(row.get('v')) is not int or row['v'] != 2: _fail('index_contract_unproved')
    if 'ns' in row and row['ns'] != database + '.' + name: _fail('index_contract_unproved')
    if not isinstance(row.get('key'), dict) or any(type(v) is not int or v != 1 for v in row['key'].values()):
        _fail('index_contract_unproved')
    result = {k: row[k] for k in ('v', 'key', 'name', 'unique') if k in row}
    if result.get('name') == '_id_' and list(result['key'].items()) == [('_id', 1)]:
        if 'unique' in result and result['unique'] is not True: _fail('index_contract_unproved')
        result.pop('unique', None)
        return result
    label = next((label for label, target in TARGETS.items() if target == name), None)
    if (label not in INDEX_NAMES or result.get('name') != INDEX_NAMES[label]
        or list(result['key'].items()) != [('user_id', 1), ('account_id', 1)] or result.get('unique') is not True):
        _fail('index_contract_unproved')
    return result


def _metadata(row, name, database):
    if (set(row) != {'name', 'type', 'options', 'info', 'idIndex'} or row.get('name') != name
        or row.get('type') != 'collection' or row.get('options') != {} or not isinstance(row.get('info'), dict)
        or set(row['info']) != {'readOnly', 'uuid'} or row['info']['readOnly'] is not False):
        _fail('collection_contract_unproved')
    uid = row['info']['uuid']
    if not isinstance(uid, Binary) or uid.subtype != 4 or len(uid) != 16: _fail('collection_uuid_unproved')
    ident = _index(row['idIndex'], name, database)
    if ident.get('name') != '_id_': _fail('collection_contract_unproved')
    return {'uuid': uid, 'options': {}, 'id_index': ident}


async def _catalog(db, session, state, names):
    rows = await _rows(db, {'listCollections': 1, 'filter': {'name': {'$in': list(names)}},
        'nameOnly': False, 'authorizedCollections': False, 'cursor': {'batchSize': len(names) + 1},
        'maxTimeMS': COMMAND_MS}, session, state, len(names))
    result = {name: None for name in names}
    for row in rows:
        name = row.get('name')
        if name not in result or result[name] is not None: _fail('collection_set_unproved')
        entry = _metadata(row, name, db.name)
        indexes = await _rows(db, {'listIndexes': name, 'cursor': {'batchSize': 8}, 'maxTimeMS': COMMAND_MS},
            session, state, 3)
        normalized = [_index(index, name, db.name) for index in indexes]
        label = next((label for label, target in TARGETS.items() if target == name), None)
        expected = {'_id_', INDEX_NAMES[label]} if label in INDEX_NAMES else {'_id_'}
        if len(normalized) != len(expected) or {i['name'] for i in normalized} != expected: _fail('index_set_unproved')
        entry['indexes'] = sorted(normalized, key=lambda index: index['name'])
        result[name] = entry
    if result.get(SLOTS) is None: _fail('receipt_namespace_unproved')
    return result


def _grant(privileges, database, collection, action):
    for privilege in privileges:
        resource = privilege['resource']
        matched = resource.get('anyResource') is True or (set(resource) == {'db', 'collection'}
            and resource['db'] in ('', database) and resource['collection'] in ('', collection))
        if matched and (action in privilege['actions'] or 'anyAction' in privilege['actions']): return True
    return False


async def _permissions(db, session, state):
    reply = await _command(db, {'connectionStatus': 1, 'showPrivileges': True}, session, state)
    auth = reply.get('authInfo')
    if (not isinstance(auth, dict) or not isinstance(auth.get('authenticatedUsers'), list)
        or not auth['authenticatedUsers'] or not isinstance(auth.get('authenticatedUserPrivileges'), list)):
        _fail('effective_privileges_unproved')
    grants = auth['authenticatedUserPrivileges']
    if not grants or any(not isinstance(g, dict) or not isinstance(g.get('resource'), dict)
        or not isinstance(g.get('actions'), list) or any(not isinstance(a, str) for a in g['actions']) for g in grants):
        _fail('effective_privileges_unproved')
    required = [('', 'listCollections'), ('users', 'find'), (SLOTS, 'find'), (SLOTS, 'insert'), (SLOTS, 'update'), (SLOTS, 'listIndexes')]
    for label, name in TARGETS.items():
        required.extend((name, action) for action in ('find', 'insert', 'listIndexes'))
        if label in INDEX_NAMES: required.append((name, 'createIndex'))
    if not all(_grant(grants, db.name, collection, action) for collection, action in required):
        _fail('namespace_privileges_unproved')


async def _topology(db, session, state):
    row = await _command(db, {'hello': 1, 'maxTimeMS': COMMAND_MS}, session, state)
    if (not isinstance(row.get('setName'), str) or not row['setName'] or row.get('msg')
        or row.get('isWritablePrimary') is not True or type(row.get('logicalSessionTimeoutMinutes')) is not int
        or type(row.get('maxWireVersion')) is not int or row['maxWireVersion'] < 9): _fail('unsupported_topology')
    return {k: row.get(k) for k in ('setName', 'primary', 'me', 'maxWireVersion')}


async def _empty(db, catalog, session, state, transactional=False):
    for name in TARGETS.values():
        if catalog.get(name) is not None:
            if await _find(db, name, {}, session, state, limit=1, projection={'_id': 1}, transactional=transactional):
                _fail('existing_data_blocks_bootstrap', 409)


async def _snapshot(db, user, session, state):
    await _owner(db, user, session, state)
    topology = await _topology(db, session, state)
    await _permissions(db, session, state)
    names = (*TARGETS.values(), SLOTS)
    first = await _catalog(db, session, state, names)
    await _empty(db, first, session, state)
    second = await _catalog(db, session, state, names)
    if _bytes(first) != _bytes(second) or _bytes(topology) != _bytes(await _topology(db, session, state)):
        _fail('catalog_changed', 409)
    await _owner(db, user, session, state)
    return {'manifest_digest': MANIFEST_DIGEST, 'catalog': first, 'topology': topology}


async def _external_fence(db, user, observation, state):
    """Fresh reads outside the transaction; do not mislabel its old snapshot."""
    session = await db.client.start_session()
    try:
        await _owner(db, user, session, state)
        if _bytes(await _topology(db, session, state)) != _bytes(observation['topology']):
            _fail('topology_changed', 409)
        existing = {name: entry for name, entry in observation['catalog'].items() if entry is not None}
        before = await _catalog(db, session, state, tuple(existing))
        await _empty(db, before, session, state)
        after = await _catalog(db, session, state, tuple(existing))
        if _bytes(existing) != _bytes(before) or _bytes(before) != _bytes(after):
            _fail('existing_uuid_changed', 409)
        await _owner(db, user, session, state)
    finally: await _end(session, state)


def _now():
    value = datetime.now(timezone.utc)
    return value.replace(microsecond=value.microsecond // 1000 * 1000)


def _utc(value):
    if not isinstance(value, datetime): _fail('receipt_unproved')
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _token(row):
    return _digest({k: row[k] for k in ('owner_id', 'auth_digest', 'request_nonce', 'server_nonce',
        'manifest_digest', 'observation_digest', 'prepared_at', 'expires_at')})


def _valid_receipt(row):
    keys = {'_id', 'schema_version', 'kind', 'state', 'owner_id', 'auth_digest', 'request_nonce',
        'server_nonce', 'manifest_digest', 'observation', 'observation_digest', 'prepared_at',
        'expires_at', 'attempt_id', 'verified_at', 'verified_catalog_digest'}
    if (not isinstance(row, dict) or set(row) != keys or row['_id'] != SLOT_ID
        or type(row['schema_version']) is not int or row['schema_version'] != 1
        or row['kind'] != 'fixed_namespace_bootstrap' or row['state'] not in ('prepared', 'consumed', 'applied', 'verified')
        or not isinstance(row['owner_id'], str) or not 1 <= len(row['owner_id']) <= 200
        or row['manifest_digest'] != MANIFEST_DIGEST): _fail('receipt_unproved')
    for key, length in (('auth_digest', 64), ('request_nonce', 32), ('server_nonce', 32), ('observation_digest', 64)):
        if not isinstance(row[key], str) or not re.fullmatch('[0-9a-f]{' + str(length) + '}', row[key]): _fail('receipt_unproved')
    if _digest(row['observation']) != row['observation_digest']: _fail('receipt_unproved')
    observation = row['observation']
    if (set(observation) != {'manifest_digest', 'catalog', 'topology'} or observation['manifest_digest'] != MANIFEST_DIGEST
        or not isinstance(observation['catalog'], dict) or set(observation['catalog']) != {*TARGETS.values(), SLOTS}):
        _fail('receipt_unproved')
    if (_utc(row['expires_at']) - _utc(row['prepared_at'])).total_seconds() != PREPARATION_SECONDS: _fail('receipt_unproved')
    if row['state'] == 'prepared':
        if row['attempt_id'] is not None: _fail('receipt_unproved')
    elif not isinstance(row['attempt_id'], str) or not re.fullmatch('[0-9a-f]{32}', row['attempt_id']): _fail('receipt_unproved')
    if row['state'] == 'verified':
        _utc(row['verified_at'])
        if not isinstance(row['verified_catalog_digest'], str) or not re.fullmatch('[0-9a-f]{64}', row['verified_catalog_digest']): _fail('receipt_unproved')
    elif row['verified_at'] is not None or row['verified_catalog_digest'] is not None: _fail('receipt_unproved')
    return row


async def _receipt(db, session, state):
    rows = await _find(db, SLOTS, {'_id': SLOT_ID}, session, state)
    if len(rows) > 1: _fail('receipt_unproved')
    return _valid_receipt(rows[0]) if rows else None


def _public_receipt(row, user):
    if row is None: return _result('no_preparation', execution_permitted=False)
    if row['owner_id'] != user['id'] or row['auth_digest'] != _auth_digest(user):
        return _result('reservation_owned_by_another_auth_context', execution_permitted=False)
    if row['state'] == 'verified': return _result('historical_completed_receipt', execution_permitted=False)
    if row['state'] != 'prepared': return _result('reconciliation_required', execution_permitted=False)
    if _utc(row['expires_at']) <= _now(): return _result('preparation_expired_retained', execution_permitted=False)
    return _result('prepared', execution_permitted=False, preparation_token_available=True,
        execution_token=_token(row), request_nonce=row['request_nonce'], manifest_digest=MANIFEST_DIGEST,
        observation_digest=row['observation_digest'], expires_at=_utc(row['expires_at']).isoformat(),
        would_create=[label for label, name in TARGETS.items() if row['observation']['catalog'][name] is None])


async def _update(db, prior, values, session, state, transactional=False):
    command = {'update': SLOTS, 'updates': [{'q': prior, 'u': {'$set': values}, 'multi': False, 'upsert': False}]}
    if not transactional: command['writeConcern'] = dict(WC)
    reply = await _command(db, command, session, state); _ack(reply, 1)
    if type(reply.get('nModified')) is not int or reply['nModified'] != 1: _fail('receipt_cas_unconfirmed')


async def _end(session, state):
    task = asyncio.create_task(session.end_session())
    done, _ = await asyncio.wait({task}, timeout=CLEANUP_SECONDS)
    if not done:
        state['resource_unconfirmed'] = True
        task.add_done_callback(lambda t: None if t.cancelled() else t.exception())
        _fail('session_cleanup_unconfirmed')
    try: task.result()
    except BaseException:
        state['resource_unconfirmed'] = True; _fail('session_cleanup_unconfirmed')


async def _prepare(db, user, body, state):
    if body.acknowledge_preparation_only is not True: _fail('preparation_ack_required', 422)
    session = await db.client.start_session()
    try:
        await _owner(db, user, session, state)
        await _catalog(db, session, state, (SLOTS,))
        prior = await _receipt(db, session, state)
        if prior is not None: return _public_receipt(prior, user)
        observation = await _snapshot(db, user, session, state)
        if all(observation['catalog'][name] is not None for name in TARGETS.values()):
            return _result('already_compatible_empty_noop', namespace_contract_verified=True)
        stamp = _now()
        row = {'_id': SLOT_ID, 'schema_version': 1, 'kind': 'fixed_namespace_bootstrap', 'state': 'prepared',
            'owner_id': user['id'], 'auth_digest': _auth_digest(user), 'request_nonce': body.request_nonce,
            'server_nonce': uuid.uuid4().hex, 'manifest_digest': MANIFEST_DIGEST, 'observation': observation,
            'observation_digest': _digest(observation), 'prepared_at': stamp,
            'expires_at': stamp + timedelta(seconds=PREPARATION_SECONDS), 'attempt_id': None,
            'verified_at': None, 'verified_catalog_digest': None}
        reply = await _command(db, {'insert': SLOTS, 'documents': [row], 'writeConcern': dict(WC)}, session, state)
        _ack(reply, 1)
        stored = await _receipt(db, session, state)
        if stored is None or _bytes(stored) != _bytes(row): _fail('preparation_readback_unconfirmed')
        await _owner(db, user, session, state)
        return _public_receipt(stored, user)
    finally: await _end(session, state)


async def _inspect(db, user, state):
    session = await db.client.start_session()
    try:
        await _owner(db, user, session, state)
        await _catalog(db, session, state, (SLOTS,))
        row = await _receipt(db, session, state)
        await _owner(db, user, session, state)
        return _public_receipt(row, user)
    finally: await _end(session, state)


async def _execute(db, user, body, state):
    if body.acknowledge_storage_only is not True: _fail('execution_ack_required', 422)
    session = await db.client.start_session()
    try:
        await _owner(db, user, session, state)
        await _catalog(db, session, state, (SLOTS,))
        row = await _receipt(db, session, state)
        if row is None: _fail('preparation_missing', 409)
        if row['state'] == 'verified': return _public_receipt(row, user)
        if row['state'] != 'prepared': _fail('reconciliation_required', 409)
        if (row['owner_id'] != user['id'] or row['auth_digest'] != _auth_digest(user)
            or row['request_nonce'] != body.request_nonce or body.manifest_digest != MANIFEST_DIGEST
            or body.observation_digest != row['observation_digest']
            or not hmac.compare_digest(_token(row), body.execution_token) or _utc(row['expires_at']) <= _now()):
            _fail('preparation_binding_changed', 409)
        observation = await _snapshot(db, user, session, state)
        if _bytes(observation) != _bytes(row['observation']): _fail('preparation_observation_changed', 409)
        consumed = {**row, 'state': 'consumed', 'attempt_id': uuid.uuid4().hex}
        await _update(db, row, {'state': 'consumed', 'attempt_id': consumed['attempt_id']}, session, state)
        if _bytes(await _receipt(db, session, state)) != _bytes(consumed): _fail('consumption_readback_unconfirmed')
        await _owner(db, user, session, state)
        session.start_transaction(read_concern=ReadConcern('local'), write_concern=WriteConcern('majority', j=True),
            read_preference=ReadPreference.PRIMARY, max_commit_time_ms=3000)
        exact = await _find(db, SLOTS, {'_id': SLOT_ID}, session, state, transactional=True)
        if len(exact) != 1 or _bytes(exact[0]) != _bytes(consumed): _fail('consumed_fence_changed')
        await _owner(db, user, session, state, transactional=True)
        created = []
        for label, name in TARGETS.items():
            if observation['catalog'][name] is None:
                # Proven on authenticated Mongo8.0.26: within this local TX,
                # same-options existing namespace raises NamespaceExists(48).
                await _command(db, {'create': name}, session, state)
                created.append(label)
                if label in INDEX_NAMES:
                    await _command(db, {'createIndexes': name, 'indexes': [{'name': INDEX_NAMES[label],
                        'key': {'user_id': 1, 'account_id': 1}, 'unique': True}]}, session, state)
        all_present = {name: {} for name in TARGETS.values()}
        await _empty(db, all_present, session, state, transactional=True)
        await _external_fence(db, user, observation, state)
        await _update(db, consumed, {'state': 'applied'}, session, state, transactional=True)
        _check(state)
        reply = await db.client.admin.command({'commitTransaction': 1, 'writeConcern': dict(WC), 'maxTimeMS': 3000}, session=session)
        _ack(reply)
    finally: await _end(session, state)
    # A raw commit leaves driver transaction bookkeeping unchanged. Close that
    # session, then inspect durable data with a new PRIMARY/majority session.
    session = await db.client.start_session()
    try:
        applied = {**consumed, 'state': 'applied'}
        if _bytes(await _receipt(db, session, state)) != _bytes(applied): _fail('applied_readback_unconfirmed')
        after = await _snapshot(db, user, session, state)
        if any(after['catalog'][name] is None for name in TARGETS.values()): _fail('post_catalog_incomplete')
        for name, before in observation['catalog'].items():
            if before is not None and _bytes(before) != _bytes(after['catalog'][name]): _fail('existing_uuid_changed')
        if _bytes(observation['topology']) != _bytes(after['topology']): _fail('topology_changed')
        completed = {**applied, 'state': 'verified', 'verified_at': _now(), 'verified_catalog_digest': _digest(after['catalog'])}
        await _update(db, applied, {k: completed[k] for k in ('state', 'verified_at', 'verified_catalog_digest')}, session, state)
        if _bytes(await _receipt(db, session, state)) != _bytes(completed): _fail('completion_readback_unconfirmed')
        await _owner(db, user, session, state)
        return _result('verified_at_observation', namespace_contract_verified=True, created_labels=created,
            receipt_retained=True, execution_token_consumed=True)
    finally: await _end(session, state)


async def _run(db, user, work, body=None):
    global _active
    if _active is not None and (_active.get('resource_unconfirmed') or not _active['task'].done()): _fail('busy')
    state = {'stop': False, 'deadline': asyncio.get_running_loop().time() + TIMEOUT_SECONDS}
    async def original():
        with timeout(TIMEOUT_SECONDS):
            if body is None: return await work(db, user, state)
            return await work(db, user, body, state)
    state['task'] = asyncio.create_task(original()); _active = state
    def consume(done):
        global _active
        if not done.cancelled(): done.exception()
        if _active is state and not state.get('resource_unconfirmed'): _active = None
    state['task'].add_done_callback(consume)
    try:
        done, _ = await asyncio.wait({state['task']}, timeout=TIMEOUT_SECONDS)
        if not done: state['stop'] = True; _fail('deadline')
        result = state['task'].result(); _check(state); return result
    except asyncio.CancelledError:
        state['stop'] = True; raise
    except HTTPException: raise
    except Exception: _fail('outcome_unconfirmed')


def attach_google_namespace_bootstrap_routes(router, db, current_user):
    root = '/google_ads/automation/namespace-bootstrap'

    @router.post(root + '/prepare')
    async def prepare(body: PrepareRequest, user=Depends(current_user)):
        return await _run(db, user, _prepare, body)

    @router.post(root + '/execute')
    async def execute(body: ExecuteRequest, user=Depends(current_user)):
        return await _run(db, user, _execute, body)

    @router.get(root + '/receipt')
    async def receipt(user=Depends(current_user)):
        return await _run(db, user, _inspect)
