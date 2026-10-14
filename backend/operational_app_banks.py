"""Owner-assigned MZ2 banks/cashboxes for native entry; no accounting writes."""
from fastapi import HTTPException
from mobile_app_permissions import find_mobile_app_access, OPERATIONAL_APP_WRITE
from operational_balance_sources import entities


async def bank_choices(db, owner, kind="bank"):
    return [r for r in await entities(db, owner, kind)
            if r.get("ready") is not False and r.get("settings_complete") is not False]


async def validate_assignment(db, owner, permissions, enabled, payload):
    if not enabled or OPERATIONAL_APP_WRITE not in permissions:
        return {"bank_ids": [], "default_bank_id": None}
    if not isinstance(payload, dict):
        raise HTTPException(422, {"code": "operational_banks_required"})
    ids = payload.get("bank_ids", [])
    cash_ids = payload.get("cash_ids", [])
    default = payload.get("default_bank_id")
    if (not isinstance(cash_ids, list) or len(cash_ids) > 100
            or any(not isinstance(i, str) for i in cash_ids) or len(set(cash_ids)) != len(cash_ids)):
        raise HTTPException(422, {"code": "operational_cash_assignment_invalid"})
    if (not isinstance(ids, list) or not (ids or cash_ids) or len(ids) > 100
            or any(not isinstance(i, str) for i in ids) or len(set(ids)) != len(ids)):
        raise HTTPException(422, {"code": "operational_banks_required"})
    allowed = {r["id"] for r in await bank_choices(db, owner)}
    if not set(ids).issubset(allowed):
        raise HTTPException(422, {"code": "operational_bank_not_mz2"})
    if not set(cash_ids).issubset({r["id"] for r in await bank_choices(db, owner, "cash")}):
        raise HTTPException(422, {"code": "operational_cash_not_mz2"})
    if len(ids) == 1:
        default = ids[0]
    if ids and default not in ids:
        raise HTTPException(422, {"code": "operational_default_bank_required"})
    result = {"bank_ids": ids, "default_bank_id": default if ids else None}
    if "cash_ids" in payload:
        result["cash_ids"] = cash_ids
    return result


async def assigned_banks(db, owner, actor):
    rows = await bank_choices(db, owner)
    cash = await bank_choices(db, owner, "cash")
    if actor.get("role") == "owner":
        return {"bank_ids": [r["id"] for r in rows], "cash_ids": [r["id"] for r in cash], "default_bank_id": None}
    access = await find_mobile_app_access(db, owner_user_id=owner, user_id=actor["id"])
    config = (access or {}).get("operational_banks") or {}
    valid = {r["id"] for r in rows}
    ids = [i for i in config.get("bank_ids", []) if i in valid]
    default = config.get("default_bank_id")
    return {"bank_ids": ids, "cash_ids": [r["id"] for r in cash if r["id"] in config.get("cash_ids", [])], "default_bank_id": default if default in ids else None}


async def require_assigned_bank(db, owner, actor, payload):
    config = await assigned_banks(db, owner, actor)
    kind = payload.get("source_account_type", "bank_auto")
    allowed = config["bank_ids"] if kind == "bank" else config["cash_ids"] if kind == "cash" else config["bank_ids"] + config["cash_ids"] if kind == "bank_auto" else []
    custody = await assigned_custody(db, owner, actor)
    if kind == "employee_custody":
        allowed = custody
    if payload.get("party_type") == "employee_custody" and payload.get("party_id") not in custody:
        raise HTTPException(403, {"code": "operational_custody_not_assigned"})
    destination = config["bank_ids"] if payload.get("party_type") == "bank" else config["cash_ids"]
    if payload.get("bank_id") not in allowed or (payload.get("party_type") in {"bank", "cash"} and payload.get("party_id") not in destination):
        raise HTTPException(403, {"code": "operational_bank_not_assigned", "message": "اختر بنكًا أو صندوقًا مسندًا لك من المالك"})


async def assigned_custody(db, owner, actor):
    rows = await entities(db, owner, "employee_custody")
    if actor.get("role") == "owner":
        return [row["id"] for row in rows]
    linked = await db.mezan_employees_v2.find({"user_id": owner, "account_user_id": actor["id"]}).to_list(2)
    if len(linked) != 1:
        return []
    return [row["id"] for row in rows if row["id"] == linked[0].get("id")]
