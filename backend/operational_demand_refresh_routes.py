"""Explicit visible-page refresh; report GETs remain snapshot readers."""
from typing import Literal

from fastapi import Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator
from starlette.requests import Request

from operational_balance_store import STATES, digest, fail
from operational_demand_refresh import run_visible_refresh


class VisibleRefreshInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    visible: Literal[True]
    expected_session_scope: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")

    @field_validator("visible", mode="before")
    @classmethod
    def require_visible_page(cls, value):
        if value is not True:
            raise ValueError("visible page required")
        return value


def attach_demand_refresh_routes(router, db, current_user, scope):
    @router.post("/refresh")
    async def refresh_visible(payload: VisibleRefreshInput, request: Request,
                              user=Depends(current_user)):
        from operational_balance_service import active_gate, refresh

        actor, owner, _ = await scope(db, user, "reports")
        actor_id = actor["id"]
        session_scope = digest([owner, actor_id])
        if payload.expected_session_scope is not None and payload.expected_session_scope != session_scope:
            fail("operational_actor_scope_changed", "تغير ارتباط الحساب؛ أعد فتح الصفحة", 403)

        async def gate_state():
            fresh_actor, fresh_owner, _ = await scope(db, user, "reports")
            if (fresh_owner, fresh_actor["id"]) != (owner, actor_id):
                fail("operational_actor_scope_changed", "تغير ارتباط الحساب؛ أعد فتح الصفحة", 403)
            # Authorization needs status metadata, not repeated archive decode.
            return await db[STATES].find_one(
                {"_id": owner, "owner_id": owner},
                {"status": 1, "revision": 1, "engine.as_of": 1}) or {"status": "draft", "revision": 0}

        async def verify():
            await active_gate(db, owner, await gate_state())

        if await request.is_disconnected():
            fail("operational_refresh_cancelled", "أُلغي تحديث الصفحة", 499)
        metadata = await gate_state()
        try:
            await active_gate(db, owner, metadata)
        except HTTPException as error:
            code = error.detail.get("code") if isinstance(error.detail, dict) else None
            if code not in {"operational_not_active", "operational_accounting_active"}:
                raise
            return {"status": "accounting_active" if code == "operational_accounting_active" else metadata["status"],
                    "as_of": metadata.get("engine", {}).get("as_of"),
                    "revision": metadata.get("revision", 0), "session_scope": session_scope}

        async def update():
            from operational_visible_evidence import acquire_visible_evidence
            await acquire_visible_evidence(db, owner)
            state = await refresh(db, owner)
            return {"status": "refreshed", "as_of": state.get("engine", {}).get("as_of"),
                    "revision": state.get("revision"), "session_scope": session_scope}

        return await run_visible_refresh(request, db, owner, actor_id, verify, update)

    return router
