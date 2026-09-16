"""Owner-only manual sample; no scheduler, requeue, activation or cutover route."""
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field

from .probe import ProbeError, collect_order_probe, read_order_probe


class OrderProbeInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    store_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,100}$")
    order_number: str = Field(pattern=r"^[A-Za-z0-9_-]{1,100}$")


def make_salla_orders_v3_probe_router(db, current_user):
    router = APIRouter(prefix="/salla/orders-v3", tags=["salla-orders-v3"])

    def owner(user):
        if (not isinstance(user, dict) or not isinstance(user.get("id"), str)
                or not user["id"].strip()
                or (str(user.get("role") or "").strip().lower() != "owner"
                    and user.get("is_owner") is not True)):
            raise HTTPException(403, detail={"code": "owner_only"})
        return user["id"]

    def error(exc):
        status = 429 if exc.code == "probe_cooldown" else 409
        return HTTPException(status, detail={"code": exc.code})

    @router.post("/probes")
    async def collect(body: OrderProbeInput, response: Response, user=Depends(current_user)):
        uid = owner(user)
        response.headers["Cache-Control"] = "no-store"
        try:
            return await collect_order_probe(db, authenticated_user_id=uid, user_id=uid,
                owner_authorized=True, store_id=body.store_id, order_number=body.order_number)
        except ProbeError as exc:
            raise error(exc) from None

    @router.get("/probes/{probe_id}")
    async def read(probe_id: str, response: Response, user=Depends(current_user)):
        uid = owner(user)
        response.headers["Cache-Control"] = "no-store"
        try:
            return await read_order_probe(db, authenticated_user_id=uid, user_id=uid,
                                         owner_authorized=True, probe_id=probe_id)
        except ProbeError as exc:
            raise error(exc) from None

    return router
