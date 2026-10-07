"""Global-first Accounting V2 / local admission in one existing Mongo transaction.

Internal integration only. No initialization, resume, transition, opening, router
registration or fallback. Callbacks must be database-only and safe to retry.
"""
from __future__ import annotations

from contextvars import ContextVar
from typing import Any

from .access import current_actor
from .binding import (SpecialOrdersDatabase, _ACTIVE_SESSIONS, _WRITE_ADMISSIONS,
                      require_bound, require_admitted)
from .domain import DomainError
from .service import require
from .write_control import admit, validate_scopes

_OWNERS: ContextVar[dict] = ContextVar("special_v2_owner_scopes", default={})
LEGACY = frozenset({"general_ledger", "accounts", "account_transactions", "accounting_audit_log"})
RAW_V2 = frozenset({"accounting_general_ledger_v2", "accounting_journal_groups_v2",
                    "accounting_audit_log_v2", "accounting_ledger_sequences_v2"})


class V2LocalDatabase(SpecialOrdersDatabase):
    """Local source owners cannot accidentally read/write either raw ledger."""
    def __getitem__(self, name):
        if name in LEGACY or name in RAW_V2:
            raise DomainError("special_v2_public_ledger_api_required")
        return super().__getitem__(name)

    def in_session(self, session):
        if self.session is not None and self.session is not session:
            raise DomainError("different_mongo_session")
        return V2LocalDatabase(self.raw_database, self.enablement, session=session)


def accounting_scope(local, owner):
    """Obtain the already admitted public owner scope, never manufacture one."""
    binding = require_bound(local, write=True, finance=True, tenant_id=owner)
    active = _OWNERS.get().get(id(binding.raw_database))
    require_admitted(binding, owner, {"financial"})
    if (active is None or active._owner != owner or active._db is not binding.raw_database
            or active._session is not binding.session or not active._session.in_transaction):
        raise DomainError("special_v2_global_first_transaction_required")
    return active


async def execute_in_v2_owner(db: Any, *, tenant_id: str, session_user: dict,
                              callback, scopes=frozenset({"workflow", "financial", "evidence"}),
                              expected_epoch=None, required_permission="accounting.receivables.post"):
    """Join native atomic_owner first, then the unchanged local epoch barrier.

    A legacy local-first transaction is rejected. A nested V2 call can only
    retain the same session, owner and a subset of the admitted local scopes.
    The Accounting module, not this adapter, owns commit, abort and retries.
    """
    from accounting_atomic import atomic_owner, SessionDatabase
    from accounting_module_contract import accounting_owner_id, require_accounting_permission
    from accounting_write_control import fresh_actor, write_state
    from accounting_writer_transition import assert_writer_allowed
    from accounting_mz2_balances import read_mz2_write_balances
    from .mz2_v2_port import WRITE_PERMISSIONS

    if required_permission not in WRITE_PERMISSIONS:
        raise DomainError("special_v2_permission_contract_required")
    scopes = validate_scopes(scopes)
    if "financial" not in scopes:
        raise DomainError("special_v2_financial_scope_required")
    binding = require_bound(db, write=True, finance=True, tenant_id=tenant_id)
    identity = id(binding.raw_database)
    parent = _OWNERS.get().get(identity)
    if binding.session is not None and (parent is None or parent._session is not binding.session):
        raise DomainError("special_v2_global_first_transaction_required")

    async def business(scoped):
        if (not isinstance(scoped, SessionDatabase) or scoped._owner != tenant_id
                or scoped._db is not binding.raw_database or not scoped._session.in_transaction):
            raise DomainError("special_v2_owner_session_mismatch")
        if (await write_state(scoped, tenant_id))["paused"]:
            raise DomainError("special_v2_global_writes_paused", 423)
        await assert_writer_allowed(scoped, tenant_id, "v2")
        local = V2LocalDatabase(binding.raw_database, binding.enablement, session=scoped._session)
        principal = await current_actor(local, session_user)
        if principal.tenant_id != tenant_id:
            raise DomainError("special_v2_actor_scope_mismatch", 403)
        require(principal, "special_orders.finance")
        actor = await fresh_actor(scoped, {"id": principal.actor_id})
        require_accounting_permission(actor, required_permission)
        if accounting_owner_id(actor) != tenant_id:
            raise DomainError("special_v2_actor_scope_mismatch", 403)
        # Serialize against concurrent persisted-user revocation, not only an old JWT.
        changed = await scoped.users.update_one({"id": principal.actor_id,
            "role": actor["role"], "disabled": {"$ne": True}, "deleted": {"$ne": True},
            "is_active": {"$ne": False}}, {"$inc": {"special_v2_authority_fence": 1}})
        if changed.matched_count != 1:
            raise DomainError("active_user_required", 403)
        # This shared reader proves approved opening, safe_active and V2-only balances.
        await read_mz2_write_balances(scoped, owner=tenant_id)
        inherited = _WRITE_ADMISSIONS.get().get(identity)
        if parent is not None:
            if (inherited is None or inherited["tenant_id"] != tenant_id
                    or not scopes.issubset(inherited["scopes"])):
                raise DomainError("nested_write_scope_escalation_forbidden")
            if expected_epoch is not None and (type(expected_epoch) is not int
                    or inherited["write_epoch"] != expected_epoch):
                raise DomainError("special_write_epoch_stale")
            permission = inherited
        else:
            permission = await admit(local, tenant_id, scopes, expected_epoch=expected_epoch)
        session_token = _ACTIVE_SESSIONS.set({**_ACTIVE_SESSIONS.get(), identity: scoped._session})
        admission_token = _WRITE_ADMISSIONS.set({**_WRITE_ADMISSIONS.get(), identity: permission})
        owner_token = _OWNERS.set({**_OWNERS.get(), identity: scoped})
        try:
            return await callback(local, scoped, principal)
        finally:
            _OWNERS.reset(owner_token)
            _WRITE_ADMISSIONS.reset(admission_token)
            _ACTIVE_SESSIONS.reset(session_token)

    return await atomic_owner(parent if parent is not None else binding.raw_database, tenant_id, business)
