"""Closed setup boundary: only advertising setup/audit collections can mutate.

No public arbitrary callback and no write-control bypass. Setup and posting
serialize on an advertising-only owner row; financial posting additionally
uses the existing MZ2 owner write barrier.
"""
from pymongo import ReadPreference
from pymongo.errors import DuplicateKeyError
from pymongo.read_concern import ReadConcern
from pymongo.write_concern import WriteConcern

from accounting_advertising_contract import (
    AUDIT, BINDINGS, EXPENSES, FACTS, FX, LOCKS, POLICIES, OPENINGS, SETUP_COLLECTIONS,
    Binding, Expense, FxSnapshot, SpendApproval, AutomationPolicy, WalletOpening, decimal, digest, fail, now,
)
from accounting_advertising_sources import ACCOUNTS, SOURCES, daily_source, require_account
from accounting_module_contract import accounting_owner_id, require_owner
from accounting_write_control import fresh_actor
from accounting_ledger_v2 import GROUPS_COLLECTION, GENERAL_LEDGER_COLLECTION, AUDIT_COLLECTION

READ_COLLECTIONS = SETUP_COLLECTIONS | {ACCOUNTS, "users", "mz2_financial_accounts"} | {
    collection for _, collection in SOURCES.values()}
ZERO_OPENING_READ_COLLECTIONS = {
    "settings", "mz2_opening_balance_drafts", GROUPS_COLLECTION,
    GENERAL_LEDGER_COLLECTION, AUDIT_COLLECTION}


class _SetupCollection:
    __slots__ = ("__collection", "__session", "__state")

    def __init__(self, collection, session, state):
        self.__collection, self.__session, self.__state = collection, session, state

    def __getattr__(self, method):
        allowed = {"find", "find_one"}
        if self.__collection.name in SETUP_COLLECTIONS:
            allowed.add("insert_one")
        if self.__collection.name in {BINDINGS, LOCKS}:
            allowed.add("update_one")
        if method not in allowed:
            self.__state["failed"] = True
            fail("ad_setup_operation_forbidden")
        def call(*args, **kwargs):
            if "session" in kwargs:
                self.__state["failed"] = True
                fail("ad_setup_session_override_forbidden")
            return getattr(self.__collection, method)(*args, session=self.__session, **kwargs)
        return call


class _SetupDatabase:
    __slots__ = ("__db", "__session", "__state", "__opening_reads")

    def __init__(self, db, session, state, *, opening_reads=False):
        self.__db, self.__session, self.__state = db, session, state
        self.__opening_reads = opening_reads

    def __getitem__(self, collection):
        if collection not in READ_COLLECTIONS and not (self.__opening_reads and collection in ZERO_OPENING_READ_COLLECTIONS):
            self.__state["failed"] = True
            fail("ad_setup_collection_forbidden", collection=collection)
        return _SetupCollection(self.__db[collection], self.__session, self.__state)

    def __getattr__(self, collection):
        if collection.startswith("_"):
            raise AttributeError(collection)
        return self[collection]


async def owner_actor(db, actor_id, expected_owner=None):
    actor = await fresh_actor(db, {"id": actor_id})
    require_owner(actor)
    owner = accounting_owner_id(actor)
    if not owner or (expected_owner is not None and expected_owner != owner):
        fail("ad_owner_scope_conflict", 403)
    return owner


def binding_key(owner, platform, integration_id):
    return digest([owner, platform, integration_id])


async def validate_binding(db, owner, binding):
    account = await require_account(db, owner, binding["platform"], binding["integration_account_id"])
    if (binding["platform_account_id"], binding["currency"]) != (account["platform_account_id"], account["currency"]):
        fail("ad_binding_identity_or_currency_mismatch")
    if binding.get("status", "active") != "active":
        fail("ad_binding_inactive")
    mode = binding["funding_mode"]
    wallet, payable = binding.get("wallet_financial_account_id"), binding.get("payable_financial_account_id")
    if mode not in {"prepaid", "postpaid", "hybrid"}:
        fail("ad_funding_mode_invalid")
    if ((mode == "prepaid" and (not wallet or payable)) or
            (mode == "postpaid" and (wallet or not payable)) or
            (mode == "hybrid" and (not wallet or not payable or binding.get("hybrid_policy") != "explicit_split")) or
            (mode != "hybrid" and binding.get("hybrid_policy") is not None)):
        fail("ad_binding_funding_contract_invalid")
    for field, kind in (("wallet_financial_account_id", "ad_prepaid_wallet"),
                        ("payable_financial_account_id", "ad_payable")):
        identity = binding.get(field)
        if identity:
            financial = await db["mz2_financial_accounts"].find_one({"user_id": owner, "id": identity})
            if not financial or financial.get("status") != "active" or financial.get("account_type") != kind:
                fail("ad_financial_binding_foreign_inactive_or_wrong_type")
            if financial.get("currency") != binding["currency"]:
                fail("ad_financial_binding_currency_mismatch")
            other = await db[BINDINGS].find_one({"user_id": owner, field: identity,
                "_id": {"$ne": binding_key(owner, binding["platform"], binding["integration_account_id"])}})
            if other:
                fail("ad_financial_identity_already_bound")
    return account


async def confirmed_binding(db, owner, platform, integration_id):
    row = await db[BINDINGS].find_one({"_id": binding_key(owner, platform, integration_id), "user_id": owner})
    if not row or not row.get("confirmed_by") or not row.get("confirmed_at"):
        fail("ad_binding_missing")
    await validate_binding(db, owner, row)
    return row


async def setup(db, actor_id, payload):
    """Only typed owner-confirmed setup operations; never accepts a callback."""
    if type(payload) not in {Binding, Expense, FxSnapshot, SpendApproval, AutomationPolicy, WalletOpening}:
        fail("ad_setup_contract_invalid", 422)
    owner = await owner_actor(db, actor_id)
    hello = await db.command("hello")
    if not hello.get("setName") or hello.get("logicalSessionTimeoutMinutes") is None:
        fail("ad_setup_requires_transactional_replica_set", 503)
    # Only an advertising setup lock may be bootstrapped. No financial/control row.
    try:
        await db[LOCKS].update_one({"_id": owner}, {"$setOnInsert": {"user_id": owner, "revision": 0}}, upsert=True)
    except DuplicateKeyError:
        pass
    async with await db.client.start_session() as session:
        async def commit(active):
            state = {"failed": False}
            scoped = _SetupDatabase(db, active, state, opening_reads=(
                isinstance(payload, WalletOpening) and decimal(payload.original_currency_amount) == 0))
            await scoped[LOCKS].update_one({"_id": owner}, {"$inc": {"revision": 1}})
            await owner_actor(scoped, actor_id, owner)
            body = payload.model_dump(mode="json")
            previous = None
            if isinstance(payload, Binding):
                collection = BINDINGS
                identity = binding_key(owner, payload.platform, payload.integration_account_id)
                previous = await scoped[BINDINGS].find_one({"_id": identity, "user_id": owner})
                if payload.version != (previous or {}).get("version", 0):
                    fail("ad_binding_version_conflict")
                await validate_binding(scoped, owner, body)
                body["version"] += 1
            elif isinstance(payload, Expense):
                collection, identity = EXPENSES, digest([owner, payload.purpose])
                other = await scoped[EXPENSES].find_one({"user_id": owner, "entity_id": payload.entity_id,
                                                       "purpose": {"$ne": payload.purpose}})
                if other:
                    fail("ad_expense_purposes_must_be_separate")
                body.update(entity_type="expense", sub_account=None, version=1)
            elif isinstance(payload, FxSnapshot):
                collection = FX
                rate = decimal(payload.fx_rate_to_sar)
                if not rate or (payload.currency == "SAR" and rate != 1):
                    fail("ad_fx_rate_invalid")
                identity = digest([owner, body])
                body["version"] = 1
            elif isinstance(payload, AutomationPolicy):
                from accounting_advertising_policy import validate_policy
                collection = POLICIES
                body = await validate_policy(scoped, owner, body)
                identity = digest([owner, payload.platform, payload.integration_account_id, body["version"]])
            elif isinstance(payload, WalletOpening):
                from accounting_advertising_wallet import validate_opening_evidence, opening_key, opening_hash
                collection = OPENINGS
                binding = await confirmed_binding(scoped, owner, payload.platform, payload.integration_account_id)
                body = await validate_opening_evidence(scoped, owner, binding, body)
                body["content_hash"] = opening_hash(body)
                identity = opening_key(owner, binding)
            else:
                collection = FACTS
                fact = await daily_source(scoped, owner, payload.platform, payload.integration_account_id, payload.business_date)
                if fact["source_revision"] != payload.expected_source_revision:
                    fail("ad_source_changed_refresh_required")
                await confirmed_binding(scoped, owner, payload.platform, payload.integration_account_id)
                # No client amount, currency, source ID or timezone can substitute for V2.
                body = {**body, **fact, "native_approval_required": False, "missing_contract_reason": None,
                        "native_accounting_eligible": True, "adapter_policy_version": 1, "version": 1}
                identity = digest([owner, fact["source_revision"]])
            prior = await scoped[collection].find_one({"_id": identity, "user_id": owner})
            # Approval retries compare the immutable user decision, not later
            # provider refresh timestamps. Keep the first captured provenance.
            request_hash = digest(payload.model_dump(mode="json") if collection == FACTS else body)
            if prior and collection != BINDINGS:
                if prior.get("request_hash") != request_hash:
                    fail("ad_immutable_contract_conflict")
                return {k: v for k, v in prior.items() if k != "_id"}
            document = dict(body, id=identity, user_id=owner, owner=owner, status="active",
                confirmed_at=now(), confirmed_by=actor_id, request_hash=request_hash)
            if collection == BINDINGS:
                await scoped[collection].update_one({"_id": identity}, {"$set": document}, upsert=True)
            else:
                await scoped[collection].insert_one({"_id": identity, **document})
            await scoped[AUDIT].insert_one(dict(user_id=owner, actor_id=actor_id, at=now(),
                contract_collection=collection, contract_id=identity, version=document["version"],
                previous=({k: v for k, v in previous.items() if k != "_id"} if previous else None),
                confirmed=document))
            if state["failed"]:
                fail("ad_setup_capability_violation")
            return document
        return await session.with_transaction(commit, read_concern=ReadConcern("snapshot"),
            write_concern=WriteConcern("majority", j=True), read_preference=ReadPreference.PRIMARY)
