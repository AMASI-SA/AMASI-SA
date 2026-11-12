"""One visible owner action; preview is read-only and confirmation is atomic."""
from typing import Literal

from fastapi import Depends
from pydantic import BaseModel, ConfigDict, Field, field_validator
from starlette.requests import Request

from operational_balance_store import STATES, digest, fail
from operational_demand_refresh import run_visible_refresh
from operational_visible_report_read import run_visible_report_read


class PreviewInput(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    order_id: str = Field(min_length=1, max_length=300)
    order_number: str = Field(min_length=1, max_length=300)
    party_type: Literal['courier', 'store_driver']
    party_id: str = Field(min_length=1, max_length=300)
    expected_session_scope: str = Field(pattern=r'^[a-f0-9]{64}$')
    visible: Literal[True]

    @field_validator('visible', mode='before')
    @classmethod
    def require_visible(cls, value):
        if value is not True:
            raise ValueError('visible page required')
        return value


class ConfirmationInput(PreviewInput):
    request_id: str = Field(min_length=1, max_length=120)
    expected_preview_hash: str = Field(pattern=r'^[a-f0-9]{64}$')
    confirmed_after_start: Literal[True]

    @field_validator('confirmed_after_start', mode='before')
    @classmethod
    def require_confirmation(cls, value):
        if value is not True:
            raise ValueError('explicit confirmation required')
        return value


def attach_delivery_confirmation_routes(router, db, current_user, scope, guarded):
    @router.post('/delivery-after-start/preview')
    async def preview_delivery(payload: PreviewInput, request: Request, user=Depends(guarded('bank_manage'))):
        from operational_delivery_confirmation_service import preview
        async def read_page():
            actor, owner, _ = await scope(db, user, 'bank_manage')
            result = await preview(db, owner, actor['id'], payload.model_dump())
            latest, latest_owner, _ = await scope(db, user, 'bank_manage')
            if (latest_owner, latest['id']) != (owner, actor['id']):
                fail('operational_actor_scope_changed', 'تغير ارتباط الحساب؛ أعد فتح الصفحة', 403)
            return result
        return await run_visible_report_read(request, read_page)

    @router.post('/delivery-after-start/confirm')
    async def confirm_delivery(payload: ConfirmationInput, request: Request, user=Depends(current_user)):
        from operational_balance_service import active_gate
        from operational_delivery_confirmation_service import confirm
        actor, owner, _ = await scope(db, user, 'bank_manage')
        actor_id = actor['id']
        if payload.expected_session_scope != digest([owner, actor_id]):
            fail('operational_actor_scope_changed', 'تغير ارتباط الحساب؛ أعد فتح الصفحة', 403)

        async def verify():
            fresh, fresh_owner, _ = await scope(db, user, 'bank_manage')
            if (fresh_owner, fresh['id']) != (owner, actor_id):
                fail('operational_actor_scope_changed', 'تغير ارتباط الحساب؛ أعد فتح الصفحة', 403)
            state = await db[STATES].find_one({'_id': owner, 'owner_id': owner}, {'status': 1}) or {'status': 'draft'}
            await active_gate(db, owner, state)

        async def save():
            return await confirm(db, owner, actor_id, payload.model_dump())

        return await run_visible_refresh(request, db, owner, actor_id, verify, save)

    return router
