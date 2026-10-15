"""Supplier setup capability: owner serialization without financial unpause.

Only three setup stores are writable. No raw database/session is handed to the
callback. Denials poison the transaction, even when caught by callback code.
"""
from copy import deepcopy

from fastapi import HTTPException
from pymongo import ReadPreference
from pymongo.read_concern import ReadConcern
from pymongo.write_concern import WriteConcern

from accounting_atomic import SessionDatabase
from accounting_ledger_v2 import verify_active_opening_v2, get_journal_v2
from accounting_write_control import AccountingDatabase
from operational_atomic import _ACTIVE, _reject

MAPPINGS = 'mz2_supplier_debit_mappings_v2'
EXPENSES = 'mz2_supplier_expense_identities_v2'
AUDIT = 'mz2_supplier_debit_identity_audit_v2'
_MUTABLE = frozenset({MAPPINGS, EXPENSES})
_READABLE = _MUTABLE | {AUDIT, 'users', 'mezan_products_v2', 'mezan_cost_resources_v2',
    'settings', 'mz2_opening_balance_drafts', 'mz2_opening_balance_audit',
    'mz2_opening_evidence', 'accounting_source_files'}
_READS = frozenset({'find', 'find_one', 'count_documents'})
_INSERTS = frozenset({'insert_one', 'insert_many'})
_WRITES = _INSERTS | {'replace_one', 'update_one'}
_PROFILE = 'supplier_debit_setup'


def _deny(state, code='supplier_debit_setup_operation_forbidden'):
    _reject(state, code)


def _live(state):
    if state['failed'] or not state['open']:
        _deny(state)


def _safe_query(value):
    if isinstance(value, dict):
        return not ({'$where', '$function', '$accumulator'} & value.keys()) and all(_safe_query(v) for v in value.values())
    return not isinstance(value, list) or all(_safe_query(v) for v in value)


class _Cursor:
    __slots__ = ('__cursor', '__state')

    def __init__(self, cursor, state):
        self.__cursor, self.__state = cursor, state

    def __getattr__(self, name):
        _deny(self.__state)

    def sort(self, *args):
        _live(self.__state)
        self.__cursor = self.__cursor.sort(*args)
        return self

    def limit(self, value):
        _live(self.__state)
        self.__cursor = self.__cursor.limit(value)
        return self

    async def to_list(self, length=None):
        _live(self.__state)
        return await self.__cursor.to_list(length)

    def __aiter__(self):
        return self

    async def __anext__(self):
        _live(self.__state)
        return await self.__cursor.__anext__()


class _Collection:
    __slots__ = ('__collection', '__session', '__state', '__owner', '__name')

    def __init__(self, collection, session, state, owner, name):
        self.__collection, self.__session = collection, session
        self.__state, self.__owner, self.__name = state, owner, name

    def __getattr__(self, method):
        _live(self.__state)
        if method in _READS:
            def read(query, *args, **kwargs):
                _live(self.__state)
                if kwargs or len(args) > 1 or not isinstance(query, dict) or not _safe_query(query):
                    _deny(self.__state)
                key = 'id' if self.__name == 'users' else 'user_id'
                if query.get(key, self.__owner) != self.__owner:
                    _deny(self.__state, 'supplier_debit_setup_owner_conflict')
                query = {**deepcopy(query), key: self.__owner}
                if self.__name == 'users':
                    if method != 'find_one':
                        _deny(self.__state)
                    args = ({'_id': 0, 'id': 1, 'role': 1, 'created_by': 1,
                        'accounting_permissions': 1, 'is_active': 1, 'disabled': 1},)
                result = getattr(self.__collection, method)(query, *args, session=self.__session)
                return _Cursor(result, self.__state) if method == 'find' else result
            return read
        if method not in _WRITES or (self.__name not in _MUTABLE and not (self.__name == AUDIT and method in _INSERTS)):
            _deny(self.__state)

        async def write(*args, **kwargs):
            _live(self.__state)
            if set(kwargs) - {'upsert'} or ('upsert' in kwargs and method in _INSERTS):
                _deny(self.__state)
            values = list(deepcopy(args))
            if method in _INSERTS:
                if len(values) != 1:
                    _deny(self.__state)
                documents = [values[0]] if method == 'insert_one' else values[0]
                if not isinstance(documents, list) or not documents:
                    _deny(self.__state)
                if any(not isinstance(doc, dict) or doc.get('user_id') != self.__owner for doc in documents):
                    _deny(self.__state, 'supplier_debit_setup_owner_conflict')
            else:
                if len(values) != 2 or not isinstance(values[0], dict) or not _safe_query(values[0]):
                    _deny(self.__state)
                if values[0].get('user_id', self.__owner) != self.__owner:
                    _deny(self.__state, 'supplier_debit_setup_owner_conflict')
                values[0]['user_id'] = self.__owner
                document = values[1]
                if not isinstance(document, dict):
                    _deny(self.__state)
                if method == 'replace_one':
                    if document.get('user_id') != self.__owner or any(k.startswith('$') for k in document):
                        _deny(self.__state)
                else:
                    if not document or set(document) - {'$set', '$unset', '$inc', '$setOnInsert'}:
                        _deny(self.__state)
                    for operator, changes in document.items():
                        if not isinstance(changes, dict):
                            _deny(self.__state)
                        for key, value in changes.items():
                            if key.split('.')[0] in {'_id', 'user_id'} and not (
                                    operator == '$setOnInsert' and key == 'user_id' and value == self.__owner):
                                _deny(self.__state)
            return await getattr(self.__collection, method)(*values, session=self.__session, **kwargs)
        return write


class SupplierDebitSetupDatabase:
    __slots__ = ('__database', '__session', '__state', '__owner')

    def __init__(self, db, session, state, owner):
        self.__database, self.__session, self.__state, self.__owner = db, session, state, owner

    def __getitem__(self, name):
        _live(self.__state)
        if name not in _READABLE:
            _deny(self.__state)
        return _Collection(self.__database[name], self.__session, self.__state, self.__owner, name)

    def __getattr__(self, name):
        return self[name]  # Handles/client/commands and unlisted stores are denied.

    async def verified_opening(self, cutover):
        """Sealed read API; never expose the backing financial DB/session."""
        _live(self.__state)
        settings = await self['settings'].find_one({'user_id': self.__owner}) or {}
        if cutover != (settings.get('mezan2_financial_cutover') or {}):
            _deny(self.__state)
        return await verify_active_opening_v2(self.__database, user_id=self.__owner,
            cutover=cutover, mongo_session=self.__session)

    async def opening_metadata(self, group_id):
        _live(self.__state)
        settings = await self['settings'].find_one({'user_id': self.__owner}) or {}
        cutover = settings.get('mezan2_financial_cutover') or {}
        if group_id != cutover.get('opening_active_txn_group_id') or not await self.verified_opening(cutover):
            _deny(self.__state)
        read_db = SessionDatabase(self.__database, self.__session)
        read_db._owner = self.__owner
        journal = await get_journal_v2(read_db, user_id=self.__owner, txn_group_id=group_id)
        metadata = ((journal or {}).get('group') or {}).get('metadata') or {}
        return {'approved_preview_hash': metadata.get('approved_preview_hash')}


async def supplier_debit_setup_atomic_owner(db, owner, callback):
    """Permit metadata setup while paused; share the financial owner lock."""
    active = _ACTIVE.get()
    if active is not None:
        # Joining an operational/financial transaction would make its authority
        # available to setup. Nested attempts poison the existing restricted scope.
        _deny(active, 'supplier_debit_setup_nested_transaction_forbidden')
    if isinstance(db, AccountingDatabase):
        db = db.current()
    if isinstance(db, SessionDatabase):
        raise HTTPException(409, detail={'code': 'supplier_debit_setup_nested_transaction_forbidden'})
    if not isinstance(owner, str) or not owner.strip():
        raise HTTPException(409, detail={'code': 'supplier_debit_setup_owner_required'})
    hello = await db.command('hello')
    if not hello.get('setName') or hello.get('logicalSessionTimeoutMinutes') is None:
        raise HTTPException(503, 'accounting_requires_transactional_replica_set')
    async with await db.client.start_session() as session:
        async def commit(active_session):
            # This is the only coordination write. Never set/reset financial
            # pause, activation, transition or authentication control fields.
            await db.mz2_atomic_owners.update_one({'_id': owner}, {'$inc': {'revision': 1}},
                upsert=True, session=active_session)
            state = {'owner': owner, 'failed': False, 'open': True, 'profile': _PROFILE}
            scoped = SupplierDebitSetupDatabase(db, active_session, state, owner)
            state['db'] = scoped
            token = _ACTIVE.set(state)
            try:
                result = await callback(scoped)
                _live(state)
                return result
            finally:
                state['open'] = False
                _ACTIVE.reset(token)
        return await session.with_transaction(commit, read_concern=ReadConcern('snapshot'),
            write_concern=WriteConcern('majority', j=True), read_preference=ReadPreference.PRIMARY)
