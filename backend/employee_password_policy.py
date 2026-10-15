"""Password minimum for accounts linked to an MZ2 employee identity."""
from fastapi import HTTPException

EMPLOYEE_PASSWORD_MIN_LENGTH = 6


async def validate_account_password(db, account, password, *, default_minimum):
    minimum = default_minimum
    if account.get("id") and account.get("created_by") and account.get("role") != "owner":
        employee = await db.mezan_employees_v2.find_one({
            "account_user_id": account["id"], "user_id": account["created_by"],
        }, {"_id": 1})
        if employee:
            minimum = EMPLOYEE_PASSWORD_MIN_LENGTH
    if len(password) < minimum:
        raise HTTPException(status_code=422, detail=[{
            "loc": ["body", "new_password"], "type": "string_too_short",
            "msg": f"String should have at least {minimum} characters",
            "ctx": {"min_length": minimum},
        }])
