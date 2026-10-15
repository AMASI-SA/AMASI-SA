"""Memoize immutable product setup reads inside one receiving transaction.

Never caches pieces, source events, sessions, invoices or accounting data.
The caller must discard this object before changing product setup.
"""
from copy import deepcopy
from bson import json_util


class ReceivingProductReadScope:
    def __init__(self, db, *, mongo_session, collections):
        self._source_db = db
        self._session = mongo_session
        self._collections = frozenset(collections)
        self._reads = {}

    def __getattr__(self, name):
        return getattr(self._source_db, name)

    def __getitem__(self, name):
        collection = self._source_db[name]
        if name not in self._collections:
            return collection
        return _ProductCollection(self, name, collection)

    def key(self, name, operation, args, kwargs):
        if kwargs.get('session') is not self._session:
            return None
        options = {k: v for k, v in kwargs.items() if k != 'session'}
        # Preserve document order: Mongo sorts and compound expressions can
        # have order-sensitive semantics. Do not coalesce different queries.
        return (name, operation, json_util.dumps([args, options]))


class _ProductCollection:
    def __init__(self, scope, name, collection):
        self._scope, self._name, self._collection = scope, name, collection

    def __getattr__(self, name):
        return getattr(self._collection, name)

    async def find_one(self, *args, **kwargs):
        key = self._scope.key(self._name, 'find_one', args, kwargs)
        if key is None:
            return await self._collection.find_one(*args, **kwargs)
        if key not in self._scope._reads:
            self._scope._reads[key] = deepcopy(await self._collection.find_one(*args, **kwargs))
        return deepcopy(self._scope._reads[key])

    def find(self, *args, **kwargs):
        cursor = self._collection.find(*args, **kwargs)
        key = self._scope.key(self._name, 'find', args, kwargs)
        return _ProductCursor(self._scope, cursor, key)


class _ProductCursor:
    def __init__(self, scope, cursor, key):
        self._scope, self._cursor, self._key = scope, cursor, key

    def __getattr__(self, name):
        # Any cursor customization falls back to Mongo, rather than reusing
        # an entry that may have a different sort, limit or collation.
        self._key = None
        return getattr(self._cursor, name)

    async def to_list(self, length=None):
        if self._key is None:
            return await self._cursor.to_list(length)
        key = (*self._key, length)
        if key not in self._scope._reads:
            self._scope._reads[key] = deepcopy(await self._cursor.to_list(length))
        return deepcopy(self._scope._reads[key])
