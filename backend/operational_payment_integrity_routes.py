"""Owner-only, read-only inspection of captured payment snapshot integrity.

No Salla webhooks, bank postings, provider statement confirmation or mutable
refresh are invoked. This endpoint is not a reconciliation completion proof.
"""
from fastapi import Depends, Request
from operational_balance_store import read, fail
from operational_visible_report_read import run_visible_report_read


def attach_payment_integrity_routes(router, db, current_user, scope):
    @router.get("/payment-events/integrity-preview")
    async def payment_integrity_preview(request: Request, user=Depends(current_user)):
        async def preview():
            actor, owner, _ = await scope(db, user, "reports")
            if actor.get("role") != "owner" or actor.get("id") != owner:
                fail("operational_payment_integrity_owner_required",
                     "مراجعة سلامة القيود متاحة للمالك فقط", 403)
            state = await read(db, owner)
            from operational_payment_snapshot_integrity import audit_payment_snapshot
            from operational_financial_activity_runtime import prepare_in_thread
            result = await prepare_in_thread(audit_payment_snapshot, state)
            latest, latest_owner, _ = await scope(db, user, "reports")
            if latest_owner != owner or latest.get("id") != actor.get("id"):
                fail("operational_actor_scope_changed", "تغير ارتباط الحساب؛ أعد فتح الصفحة", 403)
            return result
        return await run_visible_report_read(request, preview)

    return router
