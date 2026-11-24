"""Owner-only automation settings; these routes never calculate balances."""
from fastapi import Depends, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field

from operational_balance_store import digest, fail


class AutomationInput(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    enabled: bool = Field(strict=True)
    expected_session_scope: str = Field(pattern=r'^[a-f0-9]{64}$')


def attach_automation_routes(router, db, current_user, scope):
    async def owner_scope(user):
        actor, owner, _ = await scope(db, user, 'reports')
        if actor.get('role') != 'owner' or actor.get('id') != owner:
            fail('operational_automation_owner_required', 'إعداد الاحتساب التلقائي متاح للمالك فقط', 403)
        return owner

    @router.get('/automation')
    async def status(response: Response, user=Depends(current_user),
                     read_key: str | None = Query(None, pattern=r"^[a-f0-9]{64}$"),
                     read_generation: str | None = Query(None, pattern=r"^[a-f0-9]{32}$")):
        from operational_balance_automation import read_status
        response.headers['Cache-Control'] = 'no-store'
        owner = await owner_scope(user)
        if (read_key is None) != (read_generation is None):
            fail("operational_read_diagnostics_reference_required", "يلزم مرجع القراءة وجيلها لعرض قياساتها", 422)
        try:
            result = await read_status(db, owner)
        except Exception:
            fail('operational_automation_unavailable', 'تعذر قراءة حالة الاحتساب التلقائي', 503)
        if read_key is not None:
            trace = {"available": False}
            try:
                from demand_read_diagnostics import read_owner_trace
                trace = await read_owner_trace(db, owner, read_key, read_generation)
            except Exception:
                pass  # Telemetry cannot make the existing automation status fail.
            result = {**result, "read_diagnostics": trace}
        if await owner_scope(user) != owner:
            fail('operational_actor_scope_changed', 'تغير ارتباط الحساب؛ أعد فتح الصفحة', 403)
        return result

    @router.post('/automation')
    async def configure(payload: AutomationInput, response: Response, user=Depends(current_user)):
        from operational_balance_automation import set_enabled
        response.headers['Cache-Control'] = 'no-store'
        owner = await owner_scope(user)
        if payload.expected_session_scope != digest([owner, owner]):
            fail('operational_actor_scope_changed', 'تغير ارتباط الحساب؛ أعد فتح الصفحة', 403)

        async def verify():
            if await owner_scope(user) != owner:
                fail('operational_actor_scope_changed', 'تغير ارتباط الحساب؛ أعد فتح الصفحة', 403)

        try:
            result = await set_enabled(db, owner, payload.enabled, verify)
        except HTTPException:
            raise
        except Exception:
            fail('operational_automation_unavailable', 'تعذر حفظ إعداد الاحتساب التلقائي؛ تحقق من الحالة قبل تكراره', 503)
        await verify()
        return result

