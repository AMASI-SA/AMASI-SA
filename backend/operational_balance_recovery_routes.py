"""Explicit owner-only read of a compact committed recovery receipt."""
import asyncio

from fastapi import Depends, Request, Response

from operational_balance_store import digest, fail
from operational_financial_activity_runtime import run_visible_daily_read


def attach_recovery_routes(router, db, current_user, scope):
    async def owner_scope(user):
        actor, owner, _ = await scope(db, user, 'reports')
        if actor.get('role') != 'owner' or actor.get('id') != owner:
            fail('operational_recovery_owner_required', 'تقرير الاستعادة متاح للمالك فقط', 403)
        return owner

    @router.get('/recovery-proof')
    async def recovery_proof(request: Request, response: Response, user=Depends(current_user)):
        response.headers['Cache-Control'] = 'no-store'

        async def read():
            from operational_balance_recovery_proof import read_proof
            owner = await owner_scope(user)
            result = await read_proof(db, owner)
            if await owner_scope(user) != owner:
                fail('operational_actor_scope_changed', 'تغير ارتباط الحساب؛ أعد فتح الصفحة', 403)
            return {**result, 'schema_version': 1, 'session_scope': digest([owner, owner])}

        try:
            async with asyncio.timeout(8):
                return await run_visible_daily_read(request, read,
                    code='operational_recovery_read_cancelled', message='أُلغيت قراءة تقرير الاستعادة بعد مغادرة الصفحة')
        except TimeoutError:
            fail('operational_recovery_read_timeout', 'انتهت مهلة قراءة تقرير الاستعادة؛ لم تُرسل عملية احتساب', 503)
