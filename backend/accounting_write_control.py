"""Owner write barrier. Uses the same Mongo serialization row as all MZ2 writes.

No environment flag, process-local lock, policy deletion or legacy fallback.
Control and durable ingress records are the only writes allowed while paused.
"""
from contextvars import ContextVar
from datetime import datetime, timezone
from functools import wraps
import inspect

from fastapi import Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from accounting_module_contract import (
    accounting_owner_id, require_owner, require_any_accounting_page,
)


class AccountingDatabase:
    """Request-local transaction binding for the existing route closures."""
    def __init__(self, db):
        self.root = db
        self.scope = ContextVar("mz2_request_database", default=None)

    def current(self):
        scoped = self.scope.get()
        return scoped if scoped is not None else self.root

    def __getattr__(self, name):
        return getattr(self.current(), name)

    def __getitem__(self, name):
        return self.current()[name]


async def fresh_actor(db, user):
    actor = await db.users.find_one({"id": user.get("id")}, {
        "_id": 0, "id": 1, "role": 1, "created_by": 1,
        "accounting_permissions": 1, "is_active": 1, "disabled": 1,
    })
    if not actor or actor.get("is_active") is False or actor.get("disabled") is True:
        raise HTTPException(403, "accounting_actor_unavailable")
    require_any_accounting_page(actor)
    return actor


async def write_state(db, owner):
    row = await db.mz2_atomic_owners.find_one({"_id": owner}) or {}
    return {
        "paused": row.get("writes_paused", False) is not False,
        "revision": row.get("control_revision", 0),
        "changed_at": row.get("control_changed_at"),
        "changed_by": row.get("control_changed_by"),
        "reason": row.get("control_reason", ""),
    }


async def set_write_state(db, *, owner, actor_id, paused, revision, reason):
    from accounting_atomic import _owner_transaction
    if actor_id != owner or type(paused) is not bool or not reason.strip():
        raise HTTPException(403, "owner_and_reason_required")
    async def change(scoped):
        actor = await fresh_actor(scoped, {"id": actor_id})
        require_owner(actor)
        row = await write_state(scoped, owner)
        if row["revision"] != revision:
            raise HTTPException(409, "write_control_changed_refresh_required")
        now = datetime.now(timezone.utc).isoformat()
        await scoped.mz2_atomic_owners.update_one({"_id": owner}, {"$set": {
            "writes_paused": paused, "control_revision": revision + 1,
            "mezan2_managed": True, "control_changed_at": now,
            "control_changed_by": actor_id, "control_reason": reason.strip(),
        }})
        await scoped.mz2_write_control_audit.insert_one({
            "user_id": owner, "actor_id": actor_id, "paused": paused,
            "previous_paused": row["paused"], "revision": revision + 1,
            "at": now, "reason": reason.strip(),
        })
        return await write_state(scoped, owner)
    # Success is a committed barrier: earlier owner transactions have settled.
    return await _owner_transaction(db, owner, change, control=True)


class WriteControlInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    paused: bool = Field(strict=True)
    revision: int = Field(ge=0, strict=True)
    reason: str = Field(min_length=1, max_length=500)


def install_write_control_routes(router, db, current_user):
    base = "/accounting-module/write-control"

    @router.get(base)
    async def state(user: dict = Depends(current_user)):
        actor = await fresh_actor(db, user)
        owner = accounting_owner_id(actor)
        result = await write_state(db, owner)
        result["can_manage"] = actor.get("role") == "owner"
        result["pending_events"] = await db.mz2_ingress_events.count_documents({
            "user_id": owner, "state": "pending"})
        return result

    @router.put(base)
    async def change(payload: WriteControlInput, user: dict = Depends(current_user)):
        actor = await fresh_actor(db, user)
        require_owner(actor)
        return await set_write_state(db, owner=accounting_owner_id(actor),
            actor_id=actor["id"], **payload.model_dump())

    @router.post(base + "/replay")
    async def replay(user: dict = Depends(current_user)):
        actor = await fresh_actor(db, user)
        require_owner(actor)
        from accounting_ingress import replay_pending
        return await replay_pending(db, accounting_owner_id(actor))


def protect_accounting_routes(router, db):
    """Bind every mutating accounting route to the owner transaction/barrier.

    Preserve FastAPI's dependency/signature metadata. Its dependant calls this
    wrapper after request validation; closures use the request-local DB proxy.
    Read-only POST preview and owner control are intentionally separate.
    """
    from accounting_atomic import atomic_owner
    for route in router.routes:
        path = route.path
        if "/accounting-module/" not in path or not route.methods.intersection({"POST", "PUT", "PATCH", "DELETE"}):
            continue
        if "/write-control" in path or path.endswith(("/receivables/preview", "/drafts/upload")):
            continue
        original = route.dependant.call
        def wrap(endpoint):
            @wraps(endpoint)
            async def guarded(**kwargs):
                actor = await fresh_actor(db.root, kwargs.get("user") or {})
                owner = accounting_owner_id(actor)
                async def operation(scoped):
                    token = db.scope.set(scoped)
                    try:
                        return await endpoint(**kwargs)
                    finally:
                        db.scope.reset(token)
                return await atomic_owner(db.root, owner, operation)
            guarded.__signature__ = inspect.signature(endpoint, eval_str=True)
            return guarded
        route.endpoint = wrap(original)
        route.dependant.call = route.endpoint
