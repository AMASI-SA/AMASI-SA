"""Owner-assigned MZ2 banks for native operational entry; no accounting writes."""
from fastapi import HTTPException
from mobile_app_permissions import find_mobile_app_access, OPERATIONAL_APP_WRITE
from operational_balance_sources import entities


async def bank_choices(db, owner):
    return [r for r in await entities(db, owner, "bank")
            if r.get("ready") is not False and r.get("settings_complete") is not False]


async def validate_assignment(db, owner, permissions, enabled, payload):
    if not enabled or OPERATIONAL_APP_WRITE not in permissions:
        return {"bank_ids": [], "default_bank_id": None}
    if not isinstance(payload, dict):
        raise HTTPException(422, {"code": "operational_banks_required"})
    ids = payload.get("bank_ids")
    default = payload.get("default_bank_id")
    if (not isinstance(ids, list) or not ids or len(ids) > 100
            or any(not isinstance(i, str) for i in ids) or len(set(ids)) != len(ids)):
        raise HTTPException(422, {"code": "operational_banks_required"})
    allowed = {r["id"] for r in await bank_choices(db, owner)}
    if not set(ids).issubset(allowed):
        raise HTTPException(422, {"code": "operational_bank_not_mz2"})
    if len(ids) == 1:
        default = ids[0]
    if default not in ids:
        raise HTTPException(422, {"code": "operational_default_bank_required"})
    return {"bank_ids": ids, "default_bank_id": default}


async def assigned_banks(db, owner, actor):
    rows = await bank_choices(db, owner)
    if actor.get("role") == "owner":
        return {"bank_ids": [r["id"] for r in rows], "default_bank_id": None}
    access = await find_mobile_app_access(db, owner_user_id=owner, user_id=actor["id"])
    config = (access or {}).get("operational_banks") or {}
    valid = {r["id"] for r in rows}
    ids = [i for i in config.get("bank_ids", []) if i in valid]
    default = config.get("default_bank_id")
    return {"bank_ids": ids, "default_bank_id": default if default in ids else None}


async def require_assigned_bank(db, owner, actor, payload):
    config = await assigned_banks(db, owner, actor)
    if payload.get("source_account_type", "bank_auto") not in {"bank", "bank_auto"} or payload.get("bank_id") not in config["bank_ids"]:
        raise HTTPException(403, {"code": "operational_bank_not_assigned", "message": "اختر بنكًا مسندًا لك من المالك"})
