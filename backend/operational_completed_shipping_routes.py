"""Owner-only route attachment for explicit completion-fee historical review."""
from fastapi import Depends
from pydantic import BaseModel, ConfigDict, Field
from starlette.requests import Request

from operational_balance_store import fail
from operational_visible_report_read import run_visible_report_read


class PreviewInput(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    order_number: str = Field(min_length=1, max_length=160)


class ApprovalInput(PreviewInput):
    request_id: str = Field(min_length=8, max_length=160)
    expected_preview_hash: str = Field(pattern=r'^[a-f0-9]{64}$')
    expected_session_scope: str = Field(pattern=r'^[a-f0-9]{64}$')
    evidence_ref: str = Field(min_length=3, max_length=300)
    confirm_opening_and_payouts: bool = Field(strict=True)


def attach_completed_shipping_routes(router, db, current_user, scope, guarded):
    async def check_same(user, owner, actor):
        fresh, fresh_owner, _ = await scope(db, user, 'bank_manage')
        if (fresh_owner, fresh['id']) != (owner, actor):
            fail('operational_actor_scope_changed', 'تغير ارتباط الحساب؛ أعد المعاينة', 403)

    @router.post('/completed-shipping-fees/preview')
    async def preview_fee(payload: PreviewInput, request: Request, user=Depends(current_user)):
        from operational_completed_shipping_service import preview
        async def read_page():
            actor, owner, _ = await scope(db, user, 'bank_manage')
            result = await preview(db, owner, actor['id'], payload.order_number)
            await check_same(user, owner, actor['id'])
            return result
        return await run_visible_report_read(request, read_page)

    @router.post('/completed-shipping-fees/approve')
    async def approve_fee(payload: ApprovalInput, user=Depends(guarded('bank_manage'))):
        from operational_completed_shipping_service import approve
        actor, owner, _ = await scope(db, user, 'bank_manage')
        return await approve(db, owner, actor['id'], payload.model_dump())

    return router
