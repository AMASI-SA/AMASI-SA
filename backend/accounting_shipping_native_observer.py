"""Durable delivery retry marker, after canonical source transaction commits.

Only configured Track F owners are observed. A failed/locked financial action
is retained with its exact code and never changes operational delivery success.
"""
from fastapi import HTTPException
from uuid import uuid4
from accounting_ledger_v2 import AccountingLedgerV2Error
from accounting_sales_tax import TaxError
from accounting_shipping_native_contract import SETUP, INBOX, digest, now
from accounting_shipping_native import recognize_fee_delivery


async def observe_delivery(db, *, owner, order_number, actor_id=None):
    return await _observe(db, owner=owner, order_number=order_number, actor_id=actor_id)


async def observe_driver_delivery(db, *, owner, assignment_id, actor_id=None):
    return await _observe(db, owner=owner, assignment_id=assignment_id, actor_id=actor_id)


async def _observe(db, *, owner, order_number=None, assignment_id=None, actor_id=None):
    if not await db[SETUP].find_one({"_id": owner, "user_id": owner}, {"_id": 1}):
        return {"state": "not_configured"}
    key = digest([owner, "driver", assignment_id]) if assignment_id else digest([owner, order_number])
    attempt = str(uuid4())
    await db[INBOX].update_one({"_id": key, "user_id": owner}, {"$set": {
        "order_number": order_number, "assignment_id": assignment_id,
        "state": "pending", "observed_at": now(), "attempt": attempt,
    }}, upsert=True)
    try:
        result = await recognize_fee_delivery(db, owner=owner, actor_id=actor_id or owner,
                                              order_number=order_number, assignment_id=assignment_id)
    except (HTTPException, AccountingLedgerV2Error, TaxError) as exc:
        detail = exc.detail if isinstance(exc, HTTPException) else {"code": getattr(exc, "code", str(exc))}
        result = {"state": "pending", "code": detail.get("code") if isinstance(detail, dict) else str(detail)}
    await db[INBOX].update_one({"_id": key, "user_id": owner, "attempt": attempt}, {"$set": {
        "state": "pending" if result["state"] == "pending" else "processed", "result": result, "checked_at": now(),
    }})
    return result
