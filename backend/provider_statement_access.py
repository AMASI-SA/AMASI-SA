"""Independent employee statement grants, shared by Mezan and the native app.

The grant authorizes the canonical statement reader/uploader only. It does not
create general ledger, provider balance, refund or statement-confirm authority.
"""
from copy import deepcopy
from typing import Literal

from fastapi import Depends, Response
from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator

from operational_balance_store import digest, fail, now

PROVIDER_STATEMENTS_APP_PERMISSION = "app.page.provider_statements"
STATEMENT_FIELD = "provider_statements"
PROVIDER_NAMES = {"tabby": "تابي", "tamara": "تمارا", "salla": "سلة"}
EMPLOYEES = "mezan_employees_v2"


def _active(row):
    return bool(row) and not (row.get("disabled") or row.get("deleted_at") or row.get("archived")
        or row.get("is_archived") or row.get("deleted") or row.get("is_deleted")
        or row.get("active") is False or row.get("is_active") is False)


def _active_employee(row):
    return _active(row) and str(row.get("status") or "active").strip().casefold() == "active"


def normalized_statement_grant(value):
    denied = {"mode": "none", "providers": [], "employee_id": ""}
    if (not isinstance(value, dict) or value.get("mode") not in ("none", "read", "write")
            or not isinstance(value.get("employee_id"), str) or not value["employee_id"]
            or not isinstance(value.get("providers"), list) or len(value["providers"]) > 3
            or any(not isinstance(key, str) or key not in PROVIDER_NAMES for key in value["providers"])
            or len(set(value["providers"])) != len(value["providers"])
            or (value["mode"] == "none") != (not value["providers"])):
        return denied
    return {"mode": value["mode"], "providers": list(value["providers"]), "employee_id": value["employee_id"]}


def _web_mode(actor, owner):
    page = actor.get("web_page_access")
    if not isinstance(page, dict) or page.get("owner_id") != owner or not isinstance(page.get("grants"), list):
        return "none"
    modes = [row.get("mode") for row in page["grants"] if isinstance(row, dict) and row.get("id") == "balances.statements"]
    return modes[0] if len(modes) == 1 and modes[0] in ("read", "write") else "none"


def statement_navigation_enabled(actor, owner):
    """A navigation hint from the authenticated document; data routes recheck the link."""
    from operational_balance_access import POLICY_FIELD, normalized_policy
    raw = actor.get(POLICY_FIELD)
    grant = normalized_statement_grant(raw.get(STATEMENT_FIELD)) if isinstance(raw, dict) else normalized_statement_grant(None)
    return bool(_active(actor) and actor.get("role") not in {"owner", "store_driver", "meta_reviewer"}
        and not actor.get("is_owner") and normalized_policy(raw, owner=owner)["enabled"]
        and grant["mode"] != "none" and _web_mode(actor, owner) != "none")


async def shared_statement_grant(db, actor, owner):
    """None preserves the existing browser grant; a present malformed grant denies."""
    from operational_balance_access import POLICY_FIELD, normalized_policy
    raw = actor.get(POLICY_FIELD)
    if not isinstance(raw, dict) or STATEMENT_FIELD not in raw:
        return None
    grant = normalized_statement_grant(raw.get(STATEMENT_FIELD))
    denied = {**grant, "mode": "none", "providers": []}
    if not statement_navigation_enabled(actor, owner) or not normalized_policy(raw, owner=owner)["enabled"]:
        return denied
    employee = await db[EMPLOYEES].find_one({"user_id": owner, "id": grant["employee_id"],
        "account_user_id": actor["id"]}, {"_id": 0})
    if not _active_employee(employee):
        return denied
    # An account cannot inherit another employee's capability after a relink.
    other = await db[EMPLOYEES].find_one({"user_id": owner, "account_user_id": actor["id"],
        "id": {"$ne": grant["employee_id"]}}, {"_id": 0, "id": 1})
    if other:
        return denied
    return {**grant, "mode": "write" if grant["mode"] == _web_mode(actor, owner) == "write" else "read"}


class StatementAccessUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    mode: Literal["none", "read", "write"]
    providers: list[Literal["tabby", "tamara", "salla"]] = Field(max_length=3)
    expected_policy_version: StrictInt = Field(ge=0)
    expected_page_revision: StrictInt = Field(ge=0)
    expected_session_scope: str = Field(pattern=r"^[a-f0-9]{64}$")

    @field_validator("providers")
    @classmethod
    def unique(cls, values):
        if len(set(values)) != len(values):
            raise ValueError("provider_statement_provider_duplicate")
        return values


async def _owner(db, principal, expected=None):
    from operational_balance_access import fresh_identity
    actor, owner = await fresh_identity(db, principal, expected)
    if actor.get("role") != "owner" or actor["id"] != owner or not _active(actor):
        fail("provider_statement_access_owner_required", "إدارة صلاحيات كشوف التسويات متاحة للمالك فقط", 403)
    return actor, owner


async def _target(db, owner, employee_id):
    employee = await db[EMPLOYEES].find_one({"user_id": owner, "id": employee_id}, {"_id": 0})
    if not _active_employee(employee):
        fail("provider_statement_employee_not_found", "الموظف غير موجود ضمن حساب المالك", 404)
    account_id = employee.get("account_user_id")
    if not account_id:
        fail("provider_statement_account_required", "اربط حساب دخول بالموظف أولًا", 409)
    account = await db["users"].find_one({"id": account_id, "created_by": owner}, {"_id": 0})
    if (not _active(account) or account.get("role") in {"owner", "store_driver", "meta_reviewer"}
            or account.get("is_owner") is True):
        fail("provider_statement_account_unavailable", "حساب الموظف غير متاح", 404)
    duplicate = await db[EMPLOYEES].find_one({"user_id": owner, "account_user_id": account_id,
        "id": {"$ne": employee_id}}, {"_id": 0, "id": 1})
    if duplicate:
        fail("provider_statement_account_ambiguous", "حساب الدخول مرتبط بأكثر من موظف", 409)
    return employee, account


def _scope(owner, actor, employee, account):
    return digest(["provider_statement_access", owner, actor["id"], employee["id"], account["id"]])


def _view(owner, actor, employee, account):
    from operational_balance_access import POLICY_FIELD, normalized_policy
    from web_page_access import _grant_revision
    raw = account.get(POLICY_FIELD)
    policy = normalized_policy(raw, owner=owner)
    grant = normalized_statement_grant(raw.get(STATEMENT_FIELD)) if isinstance(raw, dict) else normalized_statement_grant(None)
    return {"employee_id": employee["id"], "user": {"id": account["id"], "name": account.get("name") or ""},
        "access": {"mode": grant["mode"], "providers": grant["providers"], "enabled": policy["enabled"],
            "policy_version": policy["version"], "page_revision": _grant_revision(account),
            "session_scope": _scope(owner, actor, employee, account)},
        "providers": [{"id": key, "name": name} for key, name in PROVIDER_NAMES.items()]}


def install_provider_statement_access_routes(router, db, current_user):
    path = "/management/employees/{employee_id}/provider-statement-access"

    @router.get(path)
    async def current(employee_id: str, response: Response, user=Depends(current_user)):
        actor, owner = await _owner(db, user)
        employee, account = await _target(db, owner, employee_id)
        await _owner(db, user, owner)
        fresh_employee, fresh_account = await _target(db, owner, employee_id)
        if fresh_account["id"] != account["id"]:
            fail("provider_statement_access_changed", "تغير حساب الموظف؛ أعد فتح الصلاحيات", 409)
        response.headers["Cache-Control"] = "no-store, private"
        return _view(owner, actor, fresh_employee, fresh_account)

    @router.put(path)
    async def update(employee_id: str, payload: StatementAccessUpdate, response: Response, user=Depends(current_user)):
        from operational_balance_access import POLICY_FIELD, normalized_policy
        from web_page_access import FIELD, _grant_revision, _member_view
        actor, owner = await _owner(db, user)
        employee, account = await _target(db, owner, employee_id)
        raw = account.get(POLICY_FIELD)
        policy = normalized_policy(raw, owner=owner)
        if (payload.expected_session_scope != _scope(owner, actor, employee, account)
                or payload.expected_policy_version != policy["version"]
                or payload.expected_page_revision != _grant_revision(account)):
            fail("provider_statement_access_changed", "تغير الحساب أو الصلاحيات؛ أعد التحميل قبل الحفظ", 409)
        if (payload.mode == "none") != (not payload.providers):
            fail("provider_statement_access_providers_required", "اختر منصة واحدة على الأقل أو ألغ الصلاحية", 422)
        if raw is not None and (not isinstance(raw, dict) or type(raw.get("enabled")) is not bool
                or raw.get("owner_id") != owner or raw["enabled"] != policy["enabled"]
                or raw.get("operations", []) != policy["operations"] or not isinstance(raw.get("resources", []), list)
                or len(raw.get("resources", [])) != len(policy["resources"])):
            fail("provider_statement_access_invalid", "راجع صلاحيات الحساب الحالية قبل تعديل الكشوف", 409)
        other_grant = (raw or {}).get("customer_refunds")
        active_other_grant = isinstance(other_grant, dict) and other_grant.get("mode") in {"read", "write"}
        if payload.mode != "none" and not policy["enabled"] and (policy["operations"] or active_other_grant or any(
                row["use"] or row["balance"] or row["report"] or row["print"] or row["movements"] != "none" for row in policy["resources"])):
            fail("provider_statement_access_workspace_disabled", "الصلاحيات المالية الأخرى موقوفة؛ راجع صلاحيات الأرصدة أولًا", 409)
        stamp = now()
        updated = {**deepcopy(raw or {}), "owner_id": owner, "version": policy["version"] + 1,
            "enabled": policy["enabled"] or payload.mode != "none", "operations": list(policy["operations"]),
            "resources": deepcopy((raw or {}).get("resources", [])),
            STATEMENT_FIELD: {"mode": payload.mode, "providers": list(payload.providers), "employee_id": employee_id},
            "updated_by": actor["id"], "updated_at": stamp}
        original_web = account.get(FIELD)
        if original_web is not None and (not isinstance(original_web, dict) or original_web.get("owner_id") != owner
                or not isinstance(original_web.get("grants"), list)):
            fail("provider_statement_access_invalid", "راجع صلاحيات صفحات الحساب قبل تعديل الكشوف", 409)
        original_grants = (original_web["grants"] if original_web else
            [{"id": key, "mode": mode} for key, mode in _member_view(account)["page_modes"].items()])
        grants = [deepcopy(row) for row in original_grants if not isinstance(row, dict) or row.get("id") != "balances.statements"]
        if payload.mode != "none":
            grants.append({"id": "balances.statements", "mode": payload.mode})
        web = {**deepcopy(original_web or {}), "owner_id": owner, "grants": grants,
            "revision": payload.expected_page_revision + 1, "updated_by": actor["id"], "updated_at": stamp}
        await _owner(db, user, owner)
        fresh_employee, fresh_account = await _target(db, owner, employee_id)
        if fresh_account["id"] != account["id"]:
            fail("provider_statement_access_changed", "تغير حساب الموظف؛ أعد فتح الصلاحيات", 409)
        selector = {"id": account["id"], "created_by": owner, "role": account.get("role"),
            "disabled": {"$ne": True}, "is_active": {"$ne": False}, "active": {"$ne": False},
            "is_owner": {"$ne": True}, "deleted_at": account.get("deleted_at"),
            "archived": account.get("archived"), "is_archived": account.get("is_archived"),
            "deleted": account.get("deleted"), "is_deleted": account.get("is_deleted"),
            POLICY_FIELD: raw if POLICY_FIELD in account else {"$exists": False},
            FIELD: original_web if FIELD in account else {"$exists": False}}
        result = await db["users"].update_one(selector, {"$set": {POLICY_FIELD: updated, FIELD: web}})
        if result.matched_count != 1:
            fail("provider_statement_access_changed", "تغير الحساب أو الصلاحيات؛ أعد التحميل قبل الحفظ", 409)
        response.headers["Cache-Control"] = "no-store, private"
        return _view(owner, actor, fresh_employee, {**account, POLICY_FIELD: updated, FIELD: web})
