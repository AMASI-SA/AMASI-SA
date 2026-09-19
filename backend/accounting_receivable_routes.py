"""Permission-checked manual sales tax and accountant recognition endpoints."""
from fastapi import Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from accounting_module_contract import accounting_owner_id, require_accounting_permission
from accounting_sales_tax import TaxError
from accounting_sales_tax_service import read_policy, save_policy
from accounting_receivable_service import prepare, execute
from accounting_recognition_evidence import EvidenceError


class TaxPolicyInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rate: str = Field(min_length=1, max_length=30)
    effective_at: str = Field(min_length=1, max_length=40)
    revision: int = Field(ge=0)
    reason: str = Field(min_length=1, max_length=500)


class RecognitionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: str = Field(pattern="^(tamara|tabby|emkan)$")
    payment_id: str = Field(min_length=1, max_length=200)
    refund_id: str | None = Field(default=None, max_length=200)


class ExecuteInput(RecognitionInput):
    preview_hash: str = Field(min_length=64, max_length=64)


def reject(exc):
    raise HTTPException(409, detail={"code": str(exc), "message": str(exc)}) from None


def install_accounting_receivable_routes(router, db, current_user):
    async def actor_for(user, permission):
        actor = await db.users.find_one({"id": user.get("id")}, {
            "_id": 0, "id": 1, "name": 1, "role": 1, "created_by": 1,
            "accounting_permissions": 1, "is_active": 1, "disabled": 1,
        })
        if not actor or actor.get("is_active") is False or actor.get("disabled") is True:
            raise HTTPException(403, "accounting_actor_unavailable")
        require_accounting_permission(actor, permission)
        return actor, accounting_owner_id(actor)

    @router.get("/accounting-module/sales-tax")
    async def get_tax(user: dict = Depends(current_user)):
        _, owner = await actor_for(user, "accounting.settlements.view")
        result = dict(await read_policy(db, owner))
        result.pop("_id", None)
        return result

    @router.put("/accounting-module/sales-tax")
    async def put_tax(payload: TaxPolicyInput, user: dict = Depends(current_user)):
        actor, owner = await actor_for(user, "accounting.rules.manage")
        try:
            result = dict(await save_policy(db, owner=owner, actor_id=actor["id"], **payload.model_dump()))
        except TaxError as exc:
            reject(exc)
        result.pop("_id", None)
        return result

    @router.get("/accounting-module/receivables/sources")
    async def sources(provider: str, order_number: str = "", user: dict = Depends(current_user)):
        _, owner = await actor_for(user, "accounting.settlements.view")
        if provider not in {"tamara", "tabby", "emkan"}:
            raise HTTPException(400, "unsupported_provider")
        query = {"user_id": owner, "provider": provider}
        if order_number:
            query["order_reference_id"] = order_number
        payments = await db.payment_transactions.find(query, {
            "_id": 0, "provider_id": 1, "order_reference_id": 1,
            "amount": 1, "currency": 1, "status": 1,
        }).limit(50).to_list(50)
        for payment in payments:
            payment["refunds"] = await db.payment_refunds.find({
                "user_id": owner, "provider": provider,
                "provider_payment_id": payment.get("provider_id"),
            }, {"_id": 0, "provider_refund_id": 1, "amount": 1, "status": 1}).limit(50).to_list(50)
        return {"payments": payments, "limit": 50}

    @router.post("/accounting-module/receivables/preview")
    async def preview(payload: RecognitionInput, user: dict = Depends(current_user)):
        _, owner = await actor_for(user, "accounting.settlements.view")
        try:
            return await prepare(db, owner=owner, **payload.model_dump())
        except (EvidenceError, TaxError) as exc:
            return {"state": "rejected", "reasons": [str(exc)]}

    @router.post("/accounting-module/receivables/execute")
    async def post(payload: ExecuteInput, user: dict = Depends(current_user)):
        actor, owner = await actor_for(user, "accounting.receivables.post")
        try:
            return await execute(db, owner=owner, actor_id=actor["id"],
                                 actor_name=actor.get("name") or actor["id"], **payload.model_dump())
        except (EvidenceError, TaxError) as exc:
            reject(exc)
