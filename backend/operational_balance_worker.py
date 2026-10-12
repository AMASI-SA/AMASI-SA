"""Automatic source reconciliation for explicitly started temporary systems."""
import asyncio
import logging

from operational_balance_service import freeze, refresh
from operational_balance_store import STATES, mutate, now

log = logging.getLogger(__name__)


async def tick(db):
    async for item in db[STATES].find({"status": "active"}, {"_id": 1, "owner_id": 1}):
        try:
            await refresh(db, item["owner_id"])
        except Exception as exc:
            # A failed adapter never converts missing information into zero.
            detail = getattr(exc, "detail", {})
            code = detail.get("code", "operational_refresh_failed") if isinstance(detail, dict) else "operational_refresh_failed"
            if code == "operational_accounting_active":
                # Freeze only this independent system. Do not call activation or
                # create any accounting record. Snapshot retains its data as-of.
                await freeze(db, item["owner_id"], "operational_worker", {
                    "request_id": "automatic-accounting-activation-freeze",
                    "reason": "توقفت الحسابات التشغيلية بسبب تفعيل المحاسبة؛ يلزم مطابقة آخر رصيد موثق",
                })
                continue
            async def mark(state):
                if state["status"] == "active":
                    issues = state.setdefault("engine", {}).setdefault("issues", [])
                    issue = {"code": code, "message": "تعذر تحديث المصادر؛ الأرصدة تعرض آخر تحديث ناجح"}
                    if issue not in issues:
                        issues.append(issue)
                return None
            await mutate(db, item["owner_id"], mark)
            log.warning("Operational source reconciliation incomplete: %s", code)


async def run(db, interval=30):
    while True:
        try:
            await tick(db)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Operational reconciliation iteration failed")
        await asyncio.sleep(interval)
