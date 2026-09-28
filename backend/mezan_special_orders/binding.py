"""Explicit integration scope and session propagation; no import-time activation.

Raw Motor databases keep their existing behavior. A typed wrapper is used rather
than duck-typing Motor attributes (unknown attributes are collection objects).
"""
from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

from .domain import DomainError

_ACTIVE_SESSIONS: ContextVar[dict] = ContextVar("special_order_mongo_sessions", default={})


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
        return collection if self.session is None else SessionCollection(collection, self.session)

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


async def transaction(db: Any, callback):
    """No emulated transaction or standalone fallback is allowed."""
    binding = require_bound(db, write=True)
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
