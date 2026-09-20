"""Mezan 2 multi-document commit boundary.

No standalone fallback and no in-process lock: Mongo owns commit/abort and
crash recovery. The callback must contain database work only, since the
driver can rerun it after a transient transaction error.
"""
from functools import partial
from decimal import Decimal

from fastapi import HTTPException
from pymongo import ReadPreference
from pymongo.read_concern import ReadConcern
from pymongo.write_concern import WriteConcern
from pymongo.errors import DuplicateKeyError


class SessionCollection:
    _operations = frozenset({
        "find", "find_one", "aggregate", "count_documents", "distinct",
        "insert_one", "insert_many", "update_one", "update_many",
        "replace_one", "delete_one", "delete_many", "find_one_and_update",
        "find_one_and_replace", "find_one_and_delete", "bulk_write",
    })

    def __init__(self, collection, session, ledger_groups):
        self._collection = collection
        self._session = session
        self._ledger_groups = ledger_groups

    def __getattr__(self, name):
        if name not in self._operations:
            raise AttributeError(f"unsupported transactional collection operation: {name}")
        if self._collection.name == "general_ledger" and name in {"insert_one", "insert_many"}:
            async def insert_leg(document, **kwargs):
                documents = [document] if name == "insert_one" else list(document)
                groups = set()
                for leg in documents:
                    group = leg.get("txn_group_id")
                    if not group or leg.get("status") != "posted":
                        raise HTTPException(409, "atomic_journal_group_required")
                    groups.add((leg["user_id"], group))
                result = await getattr(self._collection, name)(
                    document if name == "insert_one" else documents, session=self._session, **kwargs)
                self._ledger_groups.update(groups)
                return result
            return insert_leg
        if self._collection.name == "general_ledger" and name not in {
                "find", "find_one", "aggregate", "count_documents", "distinct"}:
            raise HTTPException(409, "posted_accounting_journals_are_append_only")
        return partial(getattr(self._collection, name), session=self._session)


class SessionDatabase:
    """Bind every collection operation to the same explicit Mongo session."""
    def __init__(self, db, session):
        self._db = db
        self._session = session
        self._ledger_groups = set()

    def __getitem__(self, name):
        return SessionCollection(self._db[name], self._session, self._ledger_groups)

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        return self[name]


async def atomic_owner(db, owner, callback):
    return await _owner_transaction(db, owner, callback)


async def _owner_transaction(db, owner, callback, *, control=False):
    from accounting_write_control import AccountingDatabase
    if isinstance(db, AccountingDatabase):
        db = db.current()
    if isinstance(db, SessionDatabase):
        if db._owner != owner or control:
            raise HTTPException(409, "accounting_transaction_scope_conflict")
        return await callback(db)
    hello = await db.command("hello")
    if not hello.get("setName") or hello.get("logicalSessionTimeoutMinutes") is None:
        raise HTTPException(503, "accounting_requires_transactional_replica_set")
    # A permanent coordination row, never a lease. It is safe to create before
    # the transaction and cannot affect financial balances. Concurrent inserts
    # race only on its built-in unique _id.
    try:
        await db.mz2_atomic_owners.update_one(
            {"_id": owner}, {"$setOnInsert": {"revision": 0}}, upsert=True)
    except DuplicateKeyError:
        pass
    async with await db.client.start_session() as session:
        async def commit_work(active_session):
            scoped = SessionDatabase(db, active_session)
            scoped._owner = owner
            # First transactional operation serializes all Mezan 2 recognition
            # and settlement decisions for this owner. Aborts release it.
            await scoped.mz2_atomic_owners.update_one(
                {"_id": owner}, {"$inc": {"revision": 1}})
            state = await scoped.mz2_atomic_owners.find_one({"_id": owner})
            if not control and state.get("writes_paused", False) is not False:
                raise HTTPException(423, detail={
                    "code": "mz2_writes_paused",
                    "message": "كتابات ميزان 2 متوقفة؛ القراءة متاحة",
                })
            result = await callback(scoped)
            for user_id, group in scoped._ledger_groups:
                rows = await scoped.general_ledger.find({
                    "user_id": user_id, "txn_group_id": group,
                }).to_list(1000)
                from accounting_periods import assert_open_journal_periods
                await assert_open_journal_periods(scoped, user_id, rows)
                debit = sum((Decimal(str(r["amount"])) for r in rows if r["side"] == "debit"), Decimal(0))
                credit = sum((Decimal(str(r["amount"])) for r in rows if r["side"] == "credit"), Decimal(0))
                if len(rows) < 2 or debit <= 0 or debit != credit or any(r["status"] != "posted" for r in rows):
                    raise HTTPException(409, "atomic_journal_unbalanced")
            return result
        return await session.with_transaction(
            commit_work, read_concern=ReadConcern("snapshot"),
            write_concern=WriteConcern("majority", j=True),
            read_preference=ReadPreference.PRIMARY,
        )

