"""Verified supplier identity: the existing exact-ID bridge, never name matching."""
from fastapi import HTTPException


async def require_linked_supplier(db, owner, entity_id):
    key = {"user_id": owner, "id": entity_id}
    supplier = await db.suppliers.find_one(key)
    counterparty = await db.counterparties.find_one({**key, "kind": "supplier"})
    if (not supplier or not counterparty or
            supplier.get("status") in {"inactive", "archived", "deleted"} or
            counterparty.get("status") in {"inactive", "archived", "deleted"}):
        raise HTTPException(409, detail={"code": "supplier_verified_identity_required"})
    return {"id": entity_id, "entity_id": entity_id, "counterparty_id": entity_id,
            "kind": "supplier", "name": supplier.get("company_name") or supplier.get("name") or counterparty.get("name") or entity_id}
