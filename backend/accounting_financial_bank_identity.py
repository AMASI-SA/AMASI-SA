"""Exact owner-scoped bank identity shared by P01 preparation and posting."""
from fastapi import HTTPException


async def find_financial_bank(db, owner_id: str, bank_id: str):
    identity = str(bank_id or "").strip()
    if not identity:
        return None
    canonical = await db.mz2_financial_accounts.find_one(
        {"user_id": owner_id, "id": identity},
        {"_id": 0, "id": 1, "name": 1, "account_type": 1, "status": 1},
    )
    legacy = await db.accounts.find_one(
        {"user_id": owner_id, "id": identity, "account_type": {"$in": ["bank", "cash"]}},
        {"_id": 0, "id": 1, "name": 1, "account_type": 1},
    )
    if canonical and legacy:
        raise HTTPException(409, detail={"code": "opening_bank_identity_ambiguous"})
    if canonical:
        if canonical.get("status") != "active" or canonical.get("account_type") != "bank":
            return None
        return {key: canonical.get(key) for key in ("id", "name", "account_type")}
    return legacy
