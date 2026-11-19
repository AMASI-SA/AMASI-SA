"""Transaction capability limited to immutable courier bank-statement evidence.

No native writer, account setup, journal, balance, receipt or provider mutation is
available through this capability. Approval has its own consumption capability.
"""
from bson import BSON
from pymongo import ReadPreference
from pymongo.read_concern import ReadConcern
from pymongo.write_concern import WriteConcern

from operational_balance_store import STATES, fail

FILES = "operational_driver_bank_files_v1"
MOVEMENTS = "operational_driver_bank_movements_v1"
IMPORTS = "operational_driver_bank_imports_v1"
SOURCE = "operational_driver_bank_statement"
READS = frozenset({FILES, MOVEMENTS, IMPORTS, STATES, "users", "settings", "mz2_atomic_owners",
    "mz2_financial_accounts", "accounts", "mz2_operational_bank_accounts", "mz2_daily_movement_files",
    "store_delivery_assignments", "store_delivery_payment_reviews", "store_delivery_collections"})
COMMON = {"_id", "id", "user_id", "source", "financial_source", "bank_account_id", "bank_identity_source",
          "bank_identity_version", "started_at", "seal"}
FIELDS = {
    FILES: COMMON | {"file_hash", "filename", "content", "row_count", "movement_ids", "uploaded_by",
                     "uploaded_at", "session_scope", "assignment_id"},
    MOVEMENTS: COMMON | {"file_id", "file_hash", "row_no", "row_hash", "movement_date", "amount", "currency",
                         "direction", "reference", "description", "explicit_provider", "suggested_provider", "status"},
    IMPORTS: {"_id", "id", "user_id", "request_id", "fingerprint", "file_id", "actor_id", "created_at"},
}


class BankEvidenceDatabase:
    def __init__(self, root, session, owner):
        self.__root, self.__session, self.__owner = root, session, owner
        self.failed = False

    def reject(self):
        self.failed = True
        fail("operational_driver_bank_capability_denied")

    def __getitem__(self, name):
        if name not in READS:
            self.reject()
        return _Collection(self, self.__root[name], self.__session, self.__owner)

    def __getattr__(self, name):
        if name.startswith("_") or name in {"client", "command", "get_collection", "get_database"}:
            self.reject()
        return self[name]


class _Collection:
    def __init__(self, database, collection, session, owner):
        self.__db, self.__collection, self.__session, self.__owner = database, collection, session, owner

    def _check(self, query):
        if not isinstance(query, dict):
            self.__db.reject()
        name = self.__collection.name
        if name == "users":
            valid = isinstance(query.get("id"), str) and bool(query["id"])
        elif name in {STATES, "mz2_atomic_owners"}:
            valid = query.get("_id") == self.__owner
        else:
            valid = query.get("user_id") == self.__owner
        if not valid:
            self.__db.reject()

    def find(self, query, *args, **kwargs):
        self._check(query)
        if "session" in kwargs:
            self.__db.reject()
        return self.__collection.find(query, *args, session=self.__session, **kwargs)

    async def find_one(self, query, *args, **kwargs):
        self._check(query)
        if "session" in kwargs:
            self.__db.reject()
        return await self.__collection.find_one(query, *args, session=self.__session, **kwargs)

    async def insert_one(self, row):
        name = self.__collection.name
        if (name not in FIELDS or not isinstance(row, dict) or set(row) - FIELDS[name]
                or row.get("user_id") != self.__owner or row.get("_id") != row.get("id")
                or not isinstance(row.get("id"), str) or not row["id"]):
            self.__db.reject()
        if name != IMPORTS and (row.get("source") != SOURCE or row.get("financial_source") != "operational"
                               or row.get("bank_identity_version") != 2 or not row.get("seal")):
            self.__db.reject()
        if name == MOVEMENTS and row.get("status") not in {"unclassified", "not_courier_payment"}:
            self.__db.reject()
        if len(BSON.encode(row)) > 9 * 1024 * 1024:
            fail("operational_driver_bank_evidence_too_large", status=413)
        return await self.__collection.insert_one(row, session=self.__session)

    def __getattr__(self, name):
        self.__db.reject()


async def transact(db, owner, callback, authorize):
    from accounting_write_control import AccountingDatabase
    from accounting_atomic import SessionDatabase
    from operational_atomic import OperationalDatabase
    from operational_driver_review_store import ReviewDatabase

    if isinstance(db, AccountingDatabase):
        db = db.current()
    if isinstance(db, (SessionDatabase, OperationalDatabase, ReviewDatabase, BankEvidenceDatabase)):
        fail("operational_driver_bank_nested_transaction_denied")
    await authorize(db)
    hello = await db.command("hello")
    if not hello.get("setName") or hello.get("logicalSessionTimeoutMinutes") is None:
        fail("operational_driver_bank_requires_transactional_replica_set", status=503)
    async with await db.client.start_session() as session:
        async def commit(active_session):
            # The same owner revision fence serializes intake with native phase
            # changes. It deliberately does not alter writes_paused or activation.
            await db.mz2_atomic_owners.update_one({"_id": owner}, {"$inc": {"revision": 1}},
                                                upsert=True, session=active_session)
            scoped = BankEvidenceDatabase(db, active_session, owner)
            await authorize(db)
            await authorize(scoped)
            result = await callback(scoped)
            await authorize(db)
            if scoped.failed:
                scoped.reject()
            return result
        return await session.with_transaction(commit, read_concern=ReadConcern("snapshot"),
            write_concern=WriteConcern("majority", j=True), read_preference=ReadPreference.PRIMARY)
