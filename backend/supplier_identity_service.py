"""Canonical MZ2 identity, with a separate historical G47 bridge."""
from fastapi import HTTPException

SUPPLIERS_V2 = "mezan_suppliers_v2"


async def require_supplier_v2(db, owner, entity_id, *, allow_inactive=False, mongo_session=None):
    """Exact owner-scoped identity. No aliases, legacy IDs, or name matching.

    Archived identities may only be used by a verified reversal or historical
    read; new operational/opening legs require an active canonical supplier.
    """
    if not isinstance(entity_id, str) or not entity_id or entity_id != entity_id.strip():
        raise HTTPException(409, detail={"code": "supplier_v2_identity_required"})
    kwargs = {"session": mongo_session} if mongo_session is not None else {}
    row = await db[SUPPLIERS_V2].find_one({"user_id": owner, "id": entity_id}, **kwargs)
    if not row or (not allow_inactive and (
            row.get("status", "active") != "active" or
            any(row.get(key) is True for key in ("archived", "deleted", "is_archived", "is_deleted")))):
        raise HTTPException(409, detail={"code": "supplier_v2_identity_required"})
    return {"id": entity_id, "entity_id": entity_id, "kind": "supplier",
            "name": row.get("company_name") or row.get("name") or entity_id,
            "source": SUPPLIERS_V2}


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
