"""Explicit integration scope and session propagation; no import-time activation.

Raw Motor databases keep their existing behavior. A typed wrapper is used rather
than duck-typing Motor attributes (unknown attributes are collection objects).
"""
from __future__ import annotations

from contextvars import ContextVar
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from .domain import DomainError

_ACTIVE_SESSIONS: ContextVar[dict] = ContextVar("special_order_mongo_sessions", default={})
_WRITE_ADMISSIONS: ContextVar[dict] = ContextVar("special_order_write_admissions", default={})
_CONTROL_ADMIN: ContextVar[dict] = ContextVar("special_order_control_admin", default={})
_WRITE_METHODS = frozenset({"insert_one", "insert_many", "update_one", "update_many",
    "replace_one", "find_one_and_update", "find_one_and_replace", "delete_one", "delete_many", "bulk_write"})


class ProtectedSpecialCollection:
    """No writes to special-owned data outside an admitted transaction.

    Native Salla collections are deliberately not intercepted here. Their local
    branches must enter the instrumented native route/ledger owners. Reads and
    explicitly prepared indexes remain possible while business writes are paused.
    """
    def __init__(self, collection, binding):
        self._collection, self._binding = collection, binding

    def __getattr__(self, name):
        allowed = SessionCollection._methods | {"create_index", "index_information", "name", "full_name"}
        if name not in allowed:
            raise AttributeError("unsupported_special_collection_operation:" + name)
        method = getattr(self._collection, name)
        if name == "aggregate":
            def aggregate(pipeline, *args, **kwargs):
                def writes(value, depth=0):
                    if depth > 32:
                        return True
                    if isinstance(value, dict):
                        return bool({"$out", "$merge"}.intersection(value)) or any(writes(v,depth+1) for v in value.values())
                    if isinstance(value, (tuple,list)):
                        return any(writes(v,depth+1) for v in value)
                    return False
                if writes(pipeline):
                    raise DomainError("write_aggregation_forbidden")
                return method(pipeline, *args, **kwargs)
            return aggregate
        if name not in _WRITE_METHODS:
            return method
        def call(*args, **kwargs):
            # No package owner currently uses bulk mutations. A mixed bulk can
            # hide deletes/replacements behind a permitted method name. Require
            # a future individually reviewed adapter instead of forwarding it.
            if name == "bulk_write":
                raise DomainError("special_bulk_write_requires_explicit_adapter")
            if name in {"delete_one", "delete_many"}:
                raise DomainError("special_history_deletion_forbidden")
            binding = self._binding
            identity = id(binding.raw_database)
            admission = _WRITE_ADMISSIONS.get().get(identity)
            administrator = _CONTROL_ADMIN.get().get(identity)
            control_name = self._collection.name in {
                "mezan_special_order_write_control_v1", "mezan_special_order_write_control_audit_v1"}
            if (binding.session is None or not binding.session.in_transaction
                    or (not admission and not (administrator and control_name))
                    or (control_name and not administrator)):
                raise DomainError("special_order_unfenced_write_forbidden")
            scopes_by_collection = {
                "mezan_special_orders_v1": "workflow",
                "mezan_special_order_evidence_v1": "evidence",
                "mezan_special_order_ledger_fences_v1": "financial",
                "mezan_special_order_financial_events_v1": "financial",
                "mezan_special_order_bank_bindings_v1": "financial",
                "mezan_special_inventory_valuations_v1": "financial",
                "mezan_special_order_accounting_policies_v1": "configuration",
                "mezan_special_fx_snapshots_v1": "configuration",
                "mezan_special_order_access_v1": "configuration",
                "mezan_special_order_access_audit_v1": "configuration",
                "mezan_special_order_notifications_v1": "dispatch",
            }
            if not control_name:
                scope = scopes_by_collection.get(self._collection.name)
                if scope is None or scope not in admission["scopes"]:
                    raise DomainError("special_collection_scope_not_admitted")
            return method(*args, **kwargs)
        return call



@dataclass(frozen=True)
class Enablement:
    reads: bool = False
    creation: bool = False
    commands: bool = False
    financial: bool = False
    tenants: frozenset[str] | None = None

    def __post_init__(self):
        if self.tenants is not None and (not isinstance(self.tenants,frozenset) or any(not isinstance(t,str) or not t for t in self.tenants)):
            raise ValueError("invalid_merchant_allowlist")
        if any(type(v) is not bool for v in (self.reads, self.creation, self.commands, self.financial)):
            raise ValueError("boolean_enablement_required")
        if self.creation and not self.commands:
            raise ValueError("creation_requires_commands")
        if self.financial and not self.commands:
            raise ValueError("financial_requires_commands")
        if (self.creation or self.commands or self.financial) and not self.reads:
            raise ValueError("inflight_reads_required")


class SessionCollection:
    """Apply one real Mongo session to existing ledger helper calls.

    Index creation is forbidden: ensure calls only verify an already prepared
    definition. No method silently falls back to a non-session business write.
    """
    _methods = frozenset({
        "find", "find_one", "aggregate", "count_documents", "distinct",
        "insert_one", "insert_many", "update_one", "update_many",
        "replace_one", "find_one_and_update", "find_one_and_replace",
        "delete_one", "delete_many", "bulk_write",
    })

    def __init__(self, collection: Any, session: Any):
        self._collection, self._session = collection, session

    def __getattr__(self, name):
        if name == "create_index":
            async def require_existing_index(keys, **options):
                # Existing helpers call ensure_indexes inside their workflow.
                # Read the already prepared index definition; never run DDL or
                # silently accept a missing constraint inside a transaction.
                normalized = [(keys, 1)] if isinstance(keys, str) else list(keys)
                definitions = await self._collection.index_information()
                for index_name, spec in definitions.items():
                    if list(spec["key"]) != normalized:
                        continue
                    if options.get("name") and options["name"] != index_name:
                        continue
                    fields = ("unique", "sparse", "partialFilterExpression", "expireAfterSeconds", "collation")
                    if all(spec.get(k, False if k in {"unique", "sparse"} else None) == options.get(k, False if k in {"unique", "sparse"} else None) for k in fields):
                        return index_name
                raise DomainError("required_transaction_index_not_prepared")
            return require_existing_index
        if name in {"name", "full_name"}:
            return getattr(self._collection, name)
        if name not in self._methods:
            raise AttributeError("unsupported_transaction_collection_operation:" + name)
        method = getattr(self._collection, name)
        def invoke(*args, **kwargs):
            supplied = kwargs.get("session")
            if supplied is not None and supplied is not self._session:
                raise DomainError("different_mongo_session")
            return method(*args, **{**kwargs, "session": self._session})
        return invoke


class SpecialOrdersDatabase:
    def __init__(self, database: Any, enablement: Enablement, *, session: Any = None):
        if isinstance(database, SpecialOrdersDatabase):
            raise ValueError("nested_database_binding_not_allowed")
        self.raw_database = database
        self.enablement = enablement
        self._explicit_session = session

    @property
    def session(self):
        return self._explicit_session or _ACTIVE_SESSIONS.get().get(id(self.raw_database))

    @property
    def client(self):
        return self.raw_database.client

    @property
    def name(self):
        return self.raw_database.name

    def __getitem__(self, name):
        collection = self.raw_database[name]
        result = collection if self.session is None else SessionCollection(collection, self.session)
        if name.startswith("mezan_special_"):
            return ProtectedSpecialCollection(result, self)
        return result

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        return self[name]

    def in_session(self, session):
        if self.session is not None and self.session is not session:
            raise DomainError("different_mongo_session")
        return SpecialOrdersDatabase(self.raw_database, self.enablement, session=session)


def bound(db: Any) -> SpecialOrdersDatabase | None:
    return db if isinstance(db, SpecialOrdersDatabase) else None


def require_bound(db: Any, *, write=False, finance=False, tenant_id=None) -> SpecialOrdersDatabase:
    binding = bound(db)
    if binding is None or not binding.enablement.reads:
        raise DomainError("special_order_integration_disabled", 403)
    if tenant_id is not None and binding.enablement.tenants is not None and str(tenant_id) not in binding.enablement.tenants:
        raise DomainError("special_order_merchant_not_enabled",403)
    if write and not binding.enablement.commands:
        raise DomainError("special_order_commands_disabled", 403)
    if finance and not binding.enablement.financial:
        raise DomainError("special_order_financial_posting_disabled", 403)
    return binding


async def _atomic_transaction(db: Any, callback):
    """Real session boundary; private control administration also uses it."""
    binding = require_bound(db)
    if binding.session is not None:
        return await callback(binding)
    from pymongo.read_concern import ReadConcern
    from pymongo.write_concern import WriteConcern
    async with await binding.client.start_session() as session:
        async def execute(s):
            context = _ACTIVE_SESSIONS.set({**_ACTIVE_SESSIONS.get(), id(binding.raw_database): s})
            try:
                return await callback(binding.in_session(s))
            finally:
                _ACTIVE_SESSIONS.reset(context)
        return await session.with_transaction(
            execute, read_concern=ReadConcern("snapshot"),
            write_concern=WriteConcern("majority"),
        )


async def transaction(db: Any, callback, *, tenant_id: str, scopes: frozenset[str], expected_epoch=None):
    """Fail-closed, scoped admission renewed on every genuine driver retry."""
    from .write_control import admit, validate_scopes
    from .contracts import Key
    from pydantic import TypeAdapter
    tenant = TypeAdapter(Key).validate_python(tenant_id)
    scopes = validate_scopes(scopes)
    binding = require_bound(db, write=True, finance="financial" in scopes, tenant_id=tenant)
    parent = _WRITE_ADMISSIONS.get().get(id(binding.raw_database))
    if binding.session is not None:
        if parent is None or parent["tenant_id"] != tenant:
            raise DomainError("nested_write_admission_scope_mismatch")
        if not scopes.issubset(parent["scopes"]):
            raise DomainError("nested_write_scope_escalation_forbidden")
        if expected_epoch is not None and (type(expected_epoch) is not int or expected_epoch != parent["write_epoch"]):
            raise DomainError("special_write_epoch_stale")
        return await callback(binding)
    async def execute(scoped):
        permission = await admit(scoped, tenant, scopes, expected_epoch=expected_epoch)
        marker = _WRITE_ADMISSIONS.set({**_WRITE_ADMISSIONS.get(), id(binding.raw_database): permission})
        try:
            return await callback(scoped)
        finally:
            _WRITE_ADMISSIONS.reset(marker)
    return await _atomic_transaction(binding, execute)


def require_admitted(db, tenant_id, scopes):
    """Internal native write owners cannot be called outside their fenced root."""
    binding = require_bound(db, write=True, finance="financial" in scopes, tenant_id=tenant_id)
    admission = _WRITE_ADMISSIONS.get().get(id(binding.raw_database))
    if (binding.session is None or not binding.session.in_transaction or admission is None
            or admission["tenant_id"] != str(tenant_id) or not set(scopes).issubset(admission["scopes"])):
        raise DomainError("special_order_write_admission_required")
    return admission


@asynccontextmanager
async def native_owner_session(db, mongo_client):
    """Preserve an ordinary native transaction, reuse a fenced local one."""
    binding = bound(db)
    if binding is not None and binding.session is not None:
        admission = _WRITE_ADMISSIONS.get().get(id(binding.raw_database))
        if admission is None:
            raise DomainError("special_order_write_admission_required")
        yield binding.session
    else:
        async with await mongo_client.start_session() as session:
            async with session.start_transaction():
                yield session
