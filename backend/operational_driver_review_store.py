"""Narrow transaction capability for operational noncash review only.

No native journal/control writer and no extension of operational_atomic profiles.
The owner revision fence serializes this decision against native phase changes;
the aggregate revision also conflicts with a concurrent operational refresh.
"""
from bson import BSON
from pymongo import ReadPreference
from pymongo.read_concern import ReadConcern
from pymongo.write_concern import WriteConcern

from operational_balance_store import AUDIT_PAGES, STATES, MAX_STATE_BYTES, fail

EVENTS = 'operational_driver_review_events_v1'
READS = frozenset({STATES, AUDIT_PAGES, EVENTS, 'users', 'settings', 'mz2_atomic_owners',
    'store_delivery_payment_reviews', 'store_delivery_collections', 'store_delivery_receipts',
    'store_delivery_assignments', 'unified_orders', 'store_drivers', 'mz2_financial_accounts',
    'mz2_opening_facts_v2', 'mz2_daily_movements', 'mz2_daily_movement_files', 'accounting_source_files',
    'mz2_shipping_native_events_v2', 'mz2_courier_delivery_evidence_v1',
    'mezan_mobile_app_access_v1', 'mezan_employees_v2'})
META = {'financial_source', 'operational_event_id', 'operational_review_revision',
        'operational_approval_status', 'reviewed_at', 'reviewed_by'}
SETS = {
    'store_delivery_payment_reviews': META | {'status', 'review_note'},
    'store_delivery_collections': META | {'review_status', 'payment_confirmed', 'payment_status'},
    'store_delivery_receipts': META | {'review_status'},
    'store_delivery_assignments': META | {'payment_review_status', 'payment_confirmed', 'payment_status',
        'payment_reviewed_at', 'payment_reviewed_by'},
    'unified_orders': {'store_delivery_payment_review_status', 'store_delivery_payment_confirmed',
        'store_delivery_payment_status', 'store_delivery_operational_event_id'},
    'mz2_daily_movements': {'status', 'operational_event_id', 'operational_consumed_at'},
}


class ReviewDatabase:
    """Reads are owner-scoped; writes are checked before reaching Mongo."""
    def __init__(self, root, session, owner):
        self.__root, self.__session, self.__owner = root, session, owner
        self.failed = False

    def reject(self):
        self.failed = True
        fail('operational_driver_review_capability_denied')

    def __getitem__(self, name):
        if name not in READS:
            self.reject()
        return _Collection(self, self.__root[name], self.__session, self.__owner)

    def __getattr__(self, name):
        if name.startswith('_') or name in {'client', 'command', 'get_collection', 'get_database'}:
            self.reject()
        return self[name]


class _Collection:
    def __init__(self, database, collection, session, owner):
        self.__db, self.__collection, self.__session, self.__owner = database, collection, session, owner

    def _check(self, query):
        name = self.__collection.name
        if not isinstance(query, dict):
            self.__db.reject()
        if name == 'users':
            valid = isinstance(query.get('id'), str) and bool(query['id'])
        elif name == 'mezan_mobile_app_access_v1':
            valid = (query.get('owner_user_id') == self.__owner
                     and isinstance(query.get('user_id'), str) and bool(query['user_id']))
        elif name == 'mezan_employees_v2':
            valid = (query.get('user_id') == self.__owner
                     and isinstance(query.get('account_user_id'), str) and bool(query['account_user_id']))
        elif name == AUDIT_PAGES:
            valid = (set(query) == {'_id', 'owner_id'} and query['owner_id'] == self.__owner
                     and isinstance(query['_id'], str) and bool(query['_id']))
        elif name in {STATES, 'mz2_atomic_owners'}:
            valid = query.get('_id') == self.__owner
        else:
            valid = query.get('user_id') == self.__owner
        if not valid:
            self.__db.reject()

    def find(self, query, *args, **kwargs):
        self._check(query)
        if 'session' in kwargs or self.__collection.name == AUDIT_PAGES:
            self.__db.reject()
        return self.__collection.find(query, *args, session=self.__session, **kwargs)

    async def find_one(self, query, *args, **kwargs):
        self._check(query)
        if 'session' in kwargs or (self.__collection.name == AUDIT_PAGES and (args or kwargs)):
            self.__db.reject()
        return await self.__collection.find_one(query, *args, session=self.__session, **kwargs)

    async def insert_one(self, row):
        if (self.__collection.name != EVENTS or row.get('user_id') != self.__owner
                or not isinstance(row.get('_id'), str) or not row['_id']):
            self.__db.reject()
        return await self.__collection.insert_one(row, session=self.__session)

    async def replace_one(self, query, row):
        self._check(query)
        if (self.__collection.name != STATES or row.get('_id') != self.__owner
                or row.get('owner_id') != self.__owner or type(query.get('revision')) is not int
                or row.get('revision') != query['revision'] + 1):
            self.__db.reject()
        if len(BSON.encode(row)) > MAX_STATE_BYTES:
            fail('operational_capacity_requires_archive')
        return await self.__collection.replace_one(query, row, session=self.__session)

    async def update_one(self, query, update):
        self._check(query)
        name = self.__collection.name
        values = update.get('$set', {})
        if (name not in SETS or set(update) != {'$set'} or not set(values) <= SETS[name]
                or query.get('_id') is None or not values):
            self.__db.reject()
        if name == 'mz2_daily_movements':
            if query.get('status') != 'unclassified' or values.get('status') != 'operational_consumed':
                self.__db.reject()
        elif name != 'unified_orders' and values.get('financial_source') != 'operational':
            self.__db.reject()
        return await self.__collection.update_one(query, update, session=self.__session)

    def __getattr__(self, name):
        self.__db.reject()


async def transact(db, owner, callback, authorize):
    """A real snapshot/majority transaction; no standalone or mock fallback."""
    from accounting_write_control import AccountingDatabase
    from accounting_atomic import SessionDatabase
    from operational_atomic import OperationalDatabase
    if isinstance(db, AccountingDatabase):
        db = db.current()
    if isinstance(db, (SessionDatabase, OperationalDatabase, ReviewDatabase)):
        fail('operational_driver_review_nested_transaction_denied')
    await authorize(db)
    hello = await db.command('hello')
    if not hello.get('setName') or hello.get('logicalSessionTimeoutMinutes') is None:
        fail('operational_review_requires_transactional_replica_set', status=503)
    async with await db.client.start_session() as session:
        async def commit(active_session):
            from inventory_write_gate import fence
            await fence(db, owner, active_session)
            await db.mz2_atomic_owners.update_one({'_id': owner}, {'$inc': {'revision': 1}},
                                                 upsert=True, session=active_session)
            scoped = ReviewDatabase(db, active_session, owner)
            await authorize(db)  # fresh external role check, not just a transaction snapshot
            await authorize(scoped)
            result = await callback(scoped)
            await authorize(db)
            if scoped.failed:
                scoped.reject()
            return result
        return await session.with_transaction(commit, read_concern=ReadConcern('snapshot'),
            write_concern=WriteConcern('majority', j=True), read_preference=ReadPreference.PRIMARY)
