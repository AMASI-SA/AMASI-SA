"""Native operational settlement audit; never a financial journal writer.

Use the caller's database capability so a lifecycle audit participates in the
same transaction as its draft, receipt and journal. Draft-only activity keeps
its existing authorization and write-control behavior.
"""
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import HTTPException
from accounting_atomic import SessionDatabase
from accounting_write_control import AccountingDatabase

COLLECTION = "mz2_settlement_audit"


async def write_audit(db, *, user_id: str, actor_id: str, actor_name: str,
        entity_type: str, entity_id: str | None, action: str,
        reason_code: str | None = None, notes: str | None = "",
        before_state: dict | None = None, after_state: dict | None = None,
        ledger_entry_id: str | None = None) -> str:
    scoped = db.current() if isinstance(db, AccountingDatabase) else db
    if isinstance(scoped, SessionDatabase) and scoped._owner != user_id:
        raise HTTPException(403, "settlement_audit_owner_scope_mismatch")
    audit_id = str(uuid4())
    await scoped[COLLECTION].insert_one({
        "id": audit_id, "user_id": user_id, "actor_id": actor_id,
        "actor_name": actor_name, "timestamp": datetime.now(timezone.utc).isoformat(),
        "entity_type": entity_type, "entity_id": entity_id, "action": action,
        "reason_code": reason_code, "notes": notes or "",
        "before_state": before_state, "after_state": after_state,
        "ledger_entry_id": ledger_entry_id,
    })
    return audit_id
