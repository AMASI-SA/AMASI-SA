"""Read-only owner prerequisites. Never migration, commit or ads authority."""
import asyncio
import hashlib
import json
import re

from fastapi import Depends, HTTPException
from pymongo import ReadPreference, timeout

from .google_ads_automation_policy import _owner
from .google_ads_management import problem

NAMES = {
    'standing_policy': 'mezan_google_ads_automation_policies_v1',
    'creation_policy': 'mezan_google_ads_automation_creation_policies_v1',
    'account_guard': 'mezan_google_ads_automation_budget_guards_v1',
    'engine_probe': 'google_ads_budget_guard_engine_probes_v1',
}
REQUIRED = {label: ('insert',) if label == 'engine_probe' else ('find', 'insert', 'update') for label in NAMES}
MAX_ROWS = 1000
TIMEOUT_SECONDS = 12
MAX_COMMAND_MS = 1000
_active = None


class _CursorCleanupUnconfirmed(Exception):
    """A server cursor may survive: deny additional probes in this process."""



def _clock(): return asyncio.get_running_loop().time()


def _base():
    return {'scope': 'namespace_and_legacy_read_only', 'namespace_prerequisites_verified': False,
        'engine_commit_verified': False, 'legacy_bindings_verified': False,
        'inventory_scope': 'owner_policy_and_guard_shapes_only', 'policy_configuration_verified': False,
        'shape_scope': 'identity_revision_flags_and_private_container_types', 'migration_ready': False, 'execution_authorized': False,
        'funding_ready': False, 'future_permissions_guaranteed': False, 'provider_writes': 0,
        'business_writes': 0, 'blocked_codes': [], 'namespaces': {}}


def _problem(code='unverified'):
    return problem('google_namespace_audit_' + code, 'تعذر إثبات متطلبات الحفظ بالقراءة؛ لم تتغير السياسات أو صلاحيات التشغيل.', 503)


def _ack(reply):
    if (not isinstance(reply, dict) or type(reply.get('ok')) not in (int, float) or reply['ok'] != 1
        or 'writeErrors' in reply or 'writeConcernError' in reply): raise _problem()


async def _end(session):
    future = asyncio.ensure_future(session.end_session())
    cancelled = False
    while not future.done():
        try: await asyncio.shield(future)
        except asyncio.CancelledError: cancelled = True
    future.result()
    if cancelled: raise asyncio.CancelledError()


async def _command(db, value, session):
    return await db.command(value, session=session, read_preference=ReadPreference.PRIMARY)


async def _rows(db, command, session):
    try:
        reply = await _command(db, command, session); _ack(reply)
        cursor = reply.get('cursor')
        if not isinstance(cursor, dict) or not isinstance(cursor.get('firstBatch'), list): raise _CursorCleanupUnconfirmed()
        rows, cursor_id = cursor['firstBatch'], cursor.get('id')
        if not isinstance(cursor_id, int) or isinstance(cursor_id, bool) or cursor_id < 0: raise _CursorCleanupUnconfirmed()
    except Exception:
        # A lost reply can conceal a newly created server cursor. Do not open
        # another diagnostic until this process is repaired/restarted.
        raise _CursorCleanupUnconfirmed() from None
    complete = cursor_id == 0
    if cursor_id:
        # Close only the cursor returned by THIS command, in this database and
        # session. No guessed IDs, getMore loops or leaked server cursors.
        prefix = db.name + '.'
        ns = cursor.get('ns')
        if not isinstance(ns, str) or not ns.startswith(prefix): raise _CursorCleanupUnconfirmed()
        try:
            closed = await _command(db, {'killCursors': ns[len(prefix):], 'cursors': [cursor_id]}, session)
            _ack(closed)
            if cursor_id not in closed.get('cursorsKilled', []) and cursor_id not in closed.get('cursorsNotFound', []):
                raise _CursorCleanupUnconfirmed()
        except Exception:
            raise _CursorCleanupUnconfirmed() from None
    if any(not isinstance(row, dict) for row in rows): raise _problem()
    return rows, complete


def _resource_matches(resource, database, collection):
    if not isinstance(resource, dict): return False
    if resource.get('anyResource') is True: return True
    return (set(resource) == {'db', 'collection'} and resource['db'] in ('', database)
        and resource['collection'] in ('', collection))


def _grants(reply, database):
    _ack(reply)
    auth = reply.get('authInfo')
    if (not isinstance(auth, dict) or not isinstance(auth.get('authenticatedUsers'), list)
        or not auth['authenticatedUsers'] or not isinstance(auth.get('authenticatedUserPrivileges'), list)
        or not auth['authenticatedUserPrivileges']): return None
    grants = auth['authenticatedUserPrivileges']
    if any(not isinstance(item, dict) or not isinstance(item.get('actions'), list)
        or any(not isinstance(action, str) for action in item['actions']) or not isinstance(item.get('resource'), dict) for item in grants): return None
    return {label: {action: any(_resource_matches(item['resource'], database, collection)
        and (action in item['actions'] or 'anyAction' in item['actions']) for item in grants)
        for action in (*REQUIRED[label], *(('createIndex',) if label in ('standing_policy', 'creation_policy') else ()))}
        for label, collection in NAMES.items()}


def _index_contract(label, rows):
    expected = [('_id', 1)] if label in ('account_guard', 'engine_probe') else [('user_id', 1), ('account_id', 1)]
    found = False
    for row in rows:
        key = row.get('key')
        if not isinstance(key, dict) or 'expireAfterSeconds' in row: return False
        ordered = list(key.items())
        if row.get('unique') is True and ordered not in ([('_id', 1)], expected): return False
        if ordered == expected:
            simple = 'collation' not in row or isinstance(row['collation'], dict) and row['collation'].get('locale') == 'simple'
            found |= (label in ('account_guard', 'engine_probe') or row.get('unique') is True) and not row.get('sparse') and not row.get('hidden') and 'partialFilterExpression' not in row and simple
    return bool(found)


def _object_size(field):
    return {'$cond': [{'$eq': [{'$type': '$' + field}, 'object']}, {'$size': {'$objectToArray': '$' + field}}, 0]}


def _array_size(field):
    return {'$cond': [{'$isArray': '$' + field}, {'$size': '$' + field}, 0]}


def _projection(label):
    result = {'_id': 1 if label == 'account_guard' else 0, 'account': '$account_id'}
    if label in ('standing_policy', 'creation_policy'):
        result.update(schema='$schema_version', revision='$revision', enabled='$enabled')
        claim = '_dispatch_claim' if label == 'standing_policy' else '_creation_claim'
        result['claim_type'] = {'$type': '$' + claim}
        private = '_dispatch_days' if label == 'standing_policy' else '_creation_ledger'
        result['private_type'] = {'$type': '$' + private}
        result['private_count'] = _array_size(private) if label == 'standing_policy' else _object_size(private)
    else:
        result.update(protocol='$protocol_version', epoch='$epoch', allocations_type={'$type': '$allocations'},
            allocations_count=_object_size('allocations'), claim_type={'$type': '$active_claim'})
    return result


def _inventory(label, rows, owner):
    seen, malformed, duplicates, pending = set(), 0, 0, 0
    for row in rows:
        account = row.get('account')
        valid_account = isinstance(account, str) and re.fullmatch(r'[0-9]{1,20}', account)
        if valid_account:
            duplicates += account in seen; seen.add(account)
        valid = bool(valid_account)
        if label in ('standing_policy', 'creation_policy'):
            valid &= (type(row.get('schema')) is int and row['schema'] == 1 and type(row.get('revision')) is int
                and 1 <= row['revision'] < 2**63 and type(row.get('enabled')) is bool
                and row.get('claim_type') in ('missing', 'null', 'object'))
            expected_type, limit = ('array', 32) if label == 'standing_policy' else ('object', 100)
            valid &= row.get('private_type') in ('missing', expected_type)
            size = row.get('private_count')
            valid &= type(size) is int and 0 <= size <= limit
            pending += row.get('claim_type') == 'object' or type(size) is int and size > 0
        else:
            expected = 'google_budget_' + hashlib.sha256(json.dumps({'user_id': owner, 'account_id': account},
                ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest() if valid_account else None
            valid &= (row.get('_id') == expected and type(row.get('protocol')) is int and row['protocol'] == 1
                and type(row.get('epoch')) is int and 0 <= row['epoch'] < 2**63
                and row.get('allocations_type') == 'object' and type(row.get('allocations_count')) is int
                and 0 <= row['allocations_count'] <= 100 and row.get('claim_type') in ('null', 'object'))
            pending += row.get('claim_type') == 'object' or type(row.get('allocations_count')) is int and row['allocations_count'] > 0
        malformed += not valid
    return {'records_checked': len(rows), 'malformed_count': int(malformed), 'duplicate_scope_count': int(duplicates),
        'legacy_evidence_count': int(pending)}


async def _audit(db, owner, state):
    result = _base(); entries = result['namespaces']; blocked = result['blocked_codes']
    session = await db.client.start_session()
    try:
        try: grants = _grants(await _command(db, {'connectionStatus': 1, 'showPrivileges': True}, session), db.name)
        except Exception: grants = None
        if grants is None: blocked.append('effective_privileges_unproved')
        try:
            metadata, complete = await _rows(db, {'listCollections': 1, 'filter': {'name': {'$in': list(NAMES.values())}},
                'nameOnly': False, 'cursor': {'batchSize': 5}, 'maxTimeMS': MAX_COMMAND_MS}, session)
            if not complete or len(metadata) > len(NAMES): raise _problem()
            metadata = {row['name']: row for row in metadata if row.get('name') in NAMES.values()}
        except _CursorCleanupUnconfirmed: raise
        except Exception:
            metadata = {}; blocked.append('collection_metadata_unproved')
        for label, name in NAMES.items():
            if state['stop'] or _clock() >= state['deadline']: raise _problem('deadline')
            actions = {action: bool(grants and grants[label][action]) for action in REQUIRED[label]}
            entry = {'actions_verified': actions, 'required_actions_verified': all(actions.values()),
                'collection_contract_verified': False, 'unique_index_verified': False, 'inventory_complete': False,
                'legacy_inventory_applicable': label != 'engine_probe',
                'records_checked': 0, 'malformed_count': 0, 'duplicate_scope_count': 0, 'legacy_evidence_count': 0,
                'blocked_codes': []}
            entries[label] = entry; codes = entry['blocked_codes']
            if not entry['required_actions_verified']: codes.append('namespace_actions_unproved')
            if label in ('standing_policy', 'creation_policy'):
                entry['create_index_for_put_verified'] = bool(grants and grants[label]['createIndex'])
            row = metadata.get(name)
            if not row:
                codes.append('collection_absent_or_unproved'); continue
            options = row.get('options')
            if (row.get('type') != 'collection' or not isinstance(options, dict)
                or options.get('validator') not in (None, {}) or options.get('capped') is True
                or 'timeseries' in options or options.get('clusteredIndex') is not None
                or 'encryptedFields' in options or 'viewOn' in options):
                codes.append('custom_collection_contract_unproved'); continue
            entry['collection_contract_verified'] = True
            try:
                indexes, complete = await _rows(db, {'listIndexes': name, 'cursor': {'batchSize': 32},
                    'maxTimeMS': MAX_COMMAND_MS}, session)
                entry['unique_index_verified'] = complete and _index_contract(label, indexes)
            except _CursorCleanupUnconfirmed: raise
            except Exception: pass
            if not entry['unique_index_verified']: codes.append('unique_index_unproved')
            if label == 'engine_probe':
                entry['inventory_complete'] = True  # No business inventory belongs to this technical namespace.
                continue
            try:
                rows, complete = await _rows(db, {'aggregate': name, 'pipeline': [{'$match': {'user_id': owner}},
                    {'$limit': MAX_ROWS + 1}, {'$project': _projection(label)}], 'cursor': {'batchSize': MAX_ROWS + 2},
                    'readConcern': {'level': 'majority'}, 'maxTimeMS': MAX_COMMAND_MS}, session)
                entry.update(_inventory(label, rows[:MAX_ROWS + 1], owner))
                entry['inventory_complete'] = complete and len(rows) <= MAX_ROWS
            except _CursorCleanupUnconfirmed: raise
            except Exception: pass
            if not entry['inventory_complete']: codes.append('legacy_inventory_incomplete')
            if entry['malformed_count'] or entry['duplicate_scope_count']: codes.append('legacy_shape_unproved')
            if entry['legacy_evidence_count']: codes.append('legacy_bindings_unproved')
        namespace_ok = all(entry['required_actions_verified'] and entry['collection_contract_verified']
            and entry['unique_index_verified'] and entry['inventory_complete'] and not entry['blocked_codes'] for entry in entries.values())
        put_ok = all(entries[label].get('create_index_for_put_verified') for label in ('standing_policy', 'creation_policy'))
        if not put_ok: blocked.append('put_index_action_unproved')
        result['namespace_prerequisites_verified'] = namespace_ok and put_ok and not blocked
        return result
    finally: await _end(session)


async def audit_namespace_prerequisites(db, user):
    global _active
    if _active is not None and (_active.get('resource_unconfirmed') or not _active['task'].done()): raise _problem('busy')
    state = {'deadline': _clock() + TIMEOUT_SECONDS, 'stop': False}
    async def original():
        remaining = state['deadline'] - _clock()
        if remaining <= 0: raise _problem('deadline')
        with timeout(remaining):
            actor = await _owner(db, user)
            try: return await _audit(db, actor['user_id'], state)
            except _CursorCleanupUnconfirmed:
                state['resource_unconfirmed'] = True
                raise _problem('cursor_cleanup_unconfirmed') from None
    state['task'] = asyncio.create_task(original()); _active = state
    def consume(done):
        global _active
        if not done.cancelled(): done.exception()
        if _active is state and not state.get('resource_unconfirmed'): _active = None
    state['task'].add_done_callback(consume)
    try:
        done, _ = await asyncio.wait({state['task']}, timeout=max(0, state['deadline'] - _clock()))
        if not done or _clock() >= state['deadline']:
            state['stop'] = True; raise _problem('deadline')
        return state['task'].result()
    except asyncio.CancelledError:
        state['stop'] = True; raise
    except Exception as error:
        if isinstance(error, HTTPException) and error.status_code in (401, 403): raise
        if isinstance(error, HTTPException) and isinstance(error.detail, dict) and str(error.detail.get('code', '')).startswith('google_namespace_audit_'): raise
        raise _problem() from None


def attach_google_ads_namespace_prerequisite_routes(router, db, current_user):
    @router.get('/google_ads/automation/namespace-prerequisites')
    async def audit(user=Depends(current_user)):
        return await audit_namespace_prerequisites(db, user)
