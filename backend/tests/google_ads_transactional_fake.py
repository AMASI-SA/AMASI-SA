"""Unit-only Mongo collaborator with isolated snapshots and explicit commits.

This does not prove Mongo concurrency or durability. Replica tests own those
claims. Existing collection behavior (validation hooks, indexes, logs) remains
observable while transaction rows are isolated until commit and discarded on
abort. Transactions serialize in this deterministic unit collaborator.
"""
import asyncio
from copy import copy, deepcopy
from types import SimpleNamespace


def matches(row, query):
    for key, expected in query.items():
        if key == '$or':
            if not any(matches(row, clause) for clause in expected): return False
        elif key == '$and':
            if not all(matches(row, clause) for clause in expected): return False
        elif isinstance(expected, dict):
            value = row.get(key)
            for operator, operand in expected.items():
                if operator == '$in' and value not in operand: return False
                if operator == '$exists' and (key in row) != operand: return False
                if operator == '$eq' and value != operand: return False
                if operator == '$ne' and value == operand: return False
                if operator == '$gt' and (value is None or value <= operand): return False
                if operator == '$gte' and (value is None or value < operand): return False
        elif row.get(key) != expected: return False
    return True


class Cursor:
    def __init__(self, rows): self.rows = deepcopy(rows)
    def sort(self, *args): return self
    async def to_list(self, length=None): return self.rows[:length]


class BasicCollection:
    def __init__(self): self.rows=[]; self.updates=[]; self.indexes=[]; self.before_update=None
    async def create_index(self, *args, **kwargs): self.indexes.append((args,kwargs))
    async def find_one(self, query, projection=None):
        return next((deepcopy(row) for row in self.rows if matches(row,query)), None)
    def find(self, query, projection=None): return Cursor([row for row in self.rows if matches(row,query)])
    async def insert_one(self, row): self.rows.append(deepcopy(row)); return SimpleNamespace(inserted_id=row.get('_id'))
    async def update_one(self, query, update):
        if self.before_update: self.before_update(self)
        self.updates.append((deepcopy(query),deepcopy(update)))
        row=next((row for row in self.rows if matches(row,query)),None)
        if row is None:return SimpleNamespace(matched_count=0)
        row.update(deepcopy(update.get('$set',{})))
        for key,value in update.get('$inc',{}).items():row[key]=row.get(key,0)+value
        for key,value in update.get('$push',{}).items():
            row[key]=(row.get(key,[])+deepcopy(value['$each']))[value.get('$slice',0):] if '$slice' in value else row.get(key,[])+deepcopy(value['$each'])
        return SimpleNamespace(matched_count=1)


class TransactionalCollection:
    def __init__(self, db, name, original): self.db=db; self.name=name; self.original=original
    def __setattr__(self,key,value):
        if key in {'db','name','original','rows','find_one','find','insert_one','update_one'} or 'original' not in self.__dict__:
            object.__setattr__(self,key,value)
        else:setattr(self.original,key,value)
    @property
    def rows(self): return self.original.rows
    @rows.setter
    def rows(self, value): self.original.rows=value
    def __getattr__(self, key): return getattr(self.original,key)
    def _target(self,session):
        if session is None:return self.original
        assert session.db is self.db and session.in_transaction
        target=copy(self.original)
        target.rows=session.rows.setdefault(self.name,deepcopy(self.original.rows))
        return target
    async def find_one(self,*args,session=None,**kwargs):return await self._target(session).find_one(*args,**kwargs)
    def find(self,query,projection=None,*,session=None):
        target=self._target(session)
        if hasattr(target,'find'):return target.find(query,projection)
        return Cursor([row for row in target.rows if matches(row,query)])
    async def insert_one(self,*args,session=None,**kwargs):
        result = await self._target(session).insert_one(*args,**kwargs)
        if result is None: result = SimpleNamespace()
        result.acknowledged = True
        return result
    async def update_one(self,*args,session=None,**kwargs):return await self._target(session).update_one(*args,**kwargs)
    async def create_index(self,*args,**kwargs):return await self.original.create_index(*args,**kwargs)


class Session:
    def __init__(self, db):self.db=db;self.in_transaction=False;self.commit_calls=0;self.locked=False
    async def __aenter__(self):
        if not self.locked:await self.db._tx_lock.acquire();self.locked=True
        return self
    async def __aexit__(self,*args):
        if self.in_transaction:await self.abort_transaction()
        if self.locked:self.db._tx_lock.release();self.locked=False
    def start_transaction(self,**kwargs):
        self.rows={name:deepcopy(collection.rows) for name,collection in self.db._tx_collections.items()}
        self.in_transaction=True
    async def abort_transaction(self):self.in_transaction=False
    async def end_session(self):
        if self.in_transaction: await self.abort_transaction()
        if self.locked:self.db._tx_lock.release();self.locked=False
    async def commit_transaction(self):
        self.commit_calls+=1
        for name,rows in self.rows.items():self.db._tx_get(name).rows=deepcopy(rows)
        self.in_transaction=False
        if self.db.after_commit:await self.db.after_commit()


class TransactionalDBMixin:
    def _tx_setup(self,collections):
        self._tx_lock=asyncio.Lock();self._tx_collections={};self.client=self;self.admin=self;self.sessions=[];self.after_commit=None
        for name,collection in collections.items():self._tx_collections[name]=TransactionalCollection(self,name,collection)
    def _tx_get(self,name):
        if name not in self._tx_collections:self._tx_collections[name]=TransactionalCollection(self,name,BasicCollection())
        return self._tx_collections[name]
    async def start_session(self):
        session=Session(self);await self._tx_lock.acquire();session.locked=True;self.sessions.append(session);return session
    async def command(self,name,*,session):
        assert session.db is self
        if isinstance(name, dict):
            if 'commitTransaction' in name:
                await session.commit_transaction()
            else:
                assert name['abortTransaction'] == 1
                await session.abort_transaction()
            return {'ok':1}
        assert name=='hello'
        return {'setName':'unit-only','logicalSessionTimeoutMinutes':30}
