"""Explicit prepare/activate/pause/audit control plane; no send endpoint."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from . import recovery_campaign as service
from .recovery_adapter import ProductionPorts, runtime_identity


class Prepare(BaseModel):
    model_config = ConfigDict(extra="forbid")
    order_numbers: list[str]


class Activate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fingerprint: str
    confirmation: str


class ReviewRelease(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fingerprint: str


def make_recovery_router(db, current_user, *, identity_fn=runtime_identity,
                         external_factory=ProductionPorts, owner_fn=None):
    if owner_fn is None:
        from integrations.qoyod.orders_owner import orders_owner_id
        owner_fn = orders_owner_id
    router = APIRouter(prefix="/recovery-404")

    async def public_report():
        result = await service.report(db)
        try:
            identity = identity_fn()
        except ValueError:
            identity = None
        result["release_review_required"] = bool(result.get("fingerprint")
            and result.get("release_identity") != identity)
        result["can_activate"] = bool(identity and result.get("can_activate")
            and not result["release_review_required"])
        return result

    async def require_data_owner(user):
        owner = str(owner_fn(user))
        existing = await db.qoyod_404_campaigns.find_one({"_id": service.CAMPAIGN})
        if existing and existing["orders_owner"] != owner:
            raise HTTPException(403, "campaign_owner_mismatch")
        return owner

    async def require_control_owner(user):
        # orders_owner_id resolves employee membership too; it is not authority.
        actor = str((user or {}).get("id") or "").strip()
        role = str((user or {}).get("role") or "").strip().lower()
        if not actor or role != "owner":
            raise HTTPException(403, "campaign_owner_role_required")
        owner = await require_data_owner(user)
        if actor != owner:
            raise HTTPException(403, "campaign_owner_mismatch")
        return owner

    @router.get("")
    async def status(user=Depends(current_user)):
        await require_data_owner(user)
        return await public_report()

    @router.post("/prepare")
    async def prepare(payload: Prepare, user=Depends(current_user)):
        owner = await require_control_owner(user)
        try:
            await service.prepare(db, payload.order_numbers, owner,
                str((user or {}).get("id") or "authenticated-ui"), identity_fn())
            return await public_report()
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.post("/activate")
    async def activate(payload: Activate, user=Depends(current_user)):
        owner = await require_control_owner(user)
        if payload.confirmation != "ACTIVATE_REVIEWED_404_COHORT":
            raise HTTPException(409, "explicit_activation_required")
        try:
            campaign = await db.qoyod_404_campaigns.find_one({"_id": service.CAMPAIGN})
            if not campaign or not await external_factory(db, campaign).authorized(identity_fn()):
                raise ValueError("worker_or_release_not_ready")
            await service.activate(db, payload.fingerprint, owner,
                str((user or {}).get("id") or "authenticated-ui"), identity_fn())
            return await public_report()
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.post("/pause")
    async def pause(user=Depends(current_user)):
        await require_control_owner(user)
        await service.pause(db)
        return await public_report()

    @router.post("/review-release")
    async def review_release(payload: ReviewRelease, user=Depends(current_user)):
        owner = await require_control_owner(user)
        try:
            await service.review_release(db, payload.fingerprint, owner,
                str(user["id"]), identity_fn())
            return await public_report()
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.post("/audit")
    async def audit(user=Depends(current_user)):
        await require_control_owner(user)
        try:
            await service.audit_pending(db, external_factory)
            return await public_report()
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    return router
