"""Authenticated owner-only Salla scheduled source diagnostic (GET, no writes).

The endpoint only reads one allowlisted metadata row and produces fixed-code
coverage diagnostics. It never invokes Salla APIs, triggers a sync, refreshes
balances, posts money or exposes sync log/customer details.
"""
from datetime import datetime, timezone
import asyncio

from fastapi import Depends, Request
import pymongo
from operational_balance_store import fail
from operational_visible_report_read import run_visible_report_read

FIELDS = {
    "_id": 0, "schedule_version": 1, "status": 1,
    "started_at": 1, "last_success_at": 1, "completed_slot": 1,
    "attempts": 1, "last_error_type": 1,
    "last_result.errors_count": 1,
    "last_result.incomplete_count": 1,
    "last_result.truncated": 1,
    "last_result.coverage_complete": 1,
}


def attach_salla_schedule_diagnostics(router, db, current_user, scope):
    @router.get("/payment-events/salla-schedule-health")
    async def read_salla_schedule_health(request: Request, user=Depends(current_user)):
        async def read_page():
            actor, owner, _ = await scope(db, user, "reports")
            if actor.get("role") != "owner" or actor.get("id") != owner:
                fail("operational_salla_health_owner_required",
                     "هذه التشخيصات متاحة لمالك المؤسسة فقط", 403)
            try:
                with pymongo.timeout(1.5):
                    async with asyncio.timeout(2):
                        row = await db["salla_scheduled_reconciliation"].find_one(
                            {"_id": owner}, FIELDS, max_time_ms=1500)
            except Exception:
                # Only a fixed code; database and Salla driver diagnostics
                # must never leak through this owner-only endpoint.
                fail("operational_salla_health_unavailable",
                     "تعذر قراءة حالة مزامنة سلة المحفوظة", 503)
            from operational_salla_schedule_diagnostics import diagnose_salla_daily_cycle
            data = diagnose_salla_daily_cycle(row, now=datetime.now(timezone.utc))
            latest, latest_owner, _ = await scope(db, user, "reports")
            if latest_owner != owner or latest.get("id") != actor.get("id") or latest.get("role") != "owner":
                fail("operational_actor_scope_changed", "تغير ارتباط الحساب", 403)
            return data
        return await run_visible_report_read(request, read_page)

    return router
