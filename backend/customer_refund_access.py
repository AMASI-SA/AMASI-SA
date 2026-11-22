"""One employee refund grant shared by AMASI and Mezan, without a new ledger.

Selected sources are usable only in a refund policy projection. They never
modify the employee's general bank/provider grants or other financial powers.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import replace
from typing import Literal

from fastapi import Depends, Response
from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator

from operational_balance_store import digest, fail, now


CUSTOMER_REFUNDS_APP_PERMISSION = "app.page.customer_refunds"
REFUND_READ = "customer.refund_read"
REFUND_OPERATIONS = frozenset({REFUND_READ, "customer.refund_request", "customer.refund_execute"})
REFUND_FIELD = "customer_refunds"
EMPLOYEES = "mezan_employees_v2"
_REFUND_SCOPE = ContextVar("customer_refund_access_scope", default=False)


def _active(row):
    return bool(row) and not (row.get("deleted_at") or row.get("disabled") or row.get("active") is False
                              or row.get("is_active") is False)


def _active_employee(row):
    return _active(row) and str(row.get("status") or "active").strip().casefold() == "active"


def normalized_refund_grant(value):
    denied = {"mode": "none", "sources": [], "employee_id": ""}
    if (not isinstance(value, dict) or not isinstance(value.get("mode"), str)
            or value["mode"] not in {"none", "read", "write"}):
        return denied
    employee = value.get("employee_id")
    sources = value.get("sources")
    if not isinstance(employee, str) or not employee or not isinstance(sources, list) or len(sources) > 10000:
        return denied
    clean, seen = [], set()
    for row in sources:
        if not isinstance(row, dict):
            return denied
        kind, identity = row.get("kind"), row.get("id")
        if (not isinstance(kind, str) or kind not in {"bank", "provider"} or not isinstance(identity, str) or not identity
                or identity != identity.strip() or len(identity) > 200 or (kind, identity) in seen):
            return denied
        seen.add((kind, identity))
        clean.append({"kind": kind, "id": identity})
    return {"mode": value["mode"], "sources": clean, "employee_id": employee}


def refund_scope_active():
    return _REFUND_SCOPE.get()


@contextmanager
def bind_customer_refund_scope(enabled=True):
    token = _REFUND_SCOPE.set(bool(enabled) or _REFUND_SCOPE.get())
    try:
        yield
    finally:
        _REFUND_SCOPE.reset(token)


@contextmanager
def bind_customer_refund_request(request):
    path = request.url.path.rstrip("/")
    base = "/api/operational-balances/workspace/"
    scoped = any(path == base + suffix or path.startswith(base + suffix + "/")
                 for suffix in ("refunds", "mobile-refunds"))
    scoped = scoped or path == base + "customer-adjustments" and request.query_params.get("direction") == "refund"
    scoped = scoped or path == base + "context" and request.query_params.get("refund_context") == "true"
    with bind_customer_refund_scope(scoped):
        yield


def _web_mode(actor, owner):
    grant = actor.get("web_page_access")
    if not isinstance(grant, dict) or grant.get("owner_id") != owner or not isinstance(grant.get("grants"), list):
        return "none"
    selected = [row.get("mode") for row in grant["grants"] if isinstance(row, dict) and row.get("id") == "balances.refund"]
    return selected[0] if len(selected) == 1 and isinstance(selected[0], str) and selected[0] in {"read", "write"} else "none"


def _mode(policy, grant):
    if not policy.enabled:
        return "none"
    operations = set(policy.operations)
    if grant is None:
        return "write" if operations & (REFUND_OPERATIONS - {REFUND_READ}) else "read" if REFUND_READ in operations else "none"
    web = _web_mode(policy.actor, policy.owner_id)
    if grant["mode"] == "none" or web == "none" or not operations & REFUND_OPERATIONS:
        return "none"
    return "write" if grant["mode"] == web == "write" and operations & (REFUND_OPERATIONS - {REFUND_READ}) else "read"


async def project_customer_refund_access(db, policy):
    """Project only refund authority; callers must not use this for a ledger."""
    from operational_balance_access import POLICY_FIELD
    owner = policy.actor if policy.owner_override else await db["users"].find_one(
        {"id": policy.owner_id, "role": "owner"}, {"_id": 0, "id": 1, "active": 1, "is_active": 1, "disabled": 1, "deleted_at": 1})
    if not _active(policy.actor) or not _active(owner):
        return replace(policy, enabled=False, operations=(), resources=[])
    if policy.owner_override:
        return replace(policy, operations=tuple(sorted(REFUND_OPERATIONS)))
    raw = policy.actor.get(POLICY_FIELD)
    grant = normalized_refund_grant(raw.get(REFUND_FIELD)) if isinstance(raw, dict) and REFUND_FIELD in raw else None
    mode = _mode(policy, grant)
    if grant is not None and mode != "none":
        linked = await db[EMPLOYEES].find_one({"user_id": policy.owner_id, "id": grant["employee_id"],
                                              "account_user_id": policy.actor["id"]},
                                             {"_id": 0, "id": 1, "deleted_at": 1, "active": 1, "is_active": 1, "disabled": 1, "status": 1})
        if not _active_employee(linked):
            mode = "none"
    if mode == "none":
        return replace(policy, enabled=False, operations=(), resources=[])
    operations = set(policy.operations) & REFUND_OPERATIONS
    operations.add(REFUND_READ)  # Compatibility for established request/execute grants.
    if mode == "read":
        operations = {REFUND_READ}
    if grant is None:
        resources = [deepcopy(row) for row in policy.resources if row["kind"] in {"bank", "provider"}]
    else:
        # All movement visibility is restricted to refund history by this
        # projection; general account/ledger policy remains unchanged.
        resources = [{**row, "use": True, "movements": "all", "balance": False, "report": False, "print": False}
                     for row in grant["sources"]]
    return replace(policy, operations=tuple(sorted(operations)), resources=resources)


async def load_customer_refund_access(db, principal, owner=None):
    from operational_balance_access import AccessPolicy, POLICY_FIELD, OPERATION_LABELS, fresh_identity, normalized_policy
    actor, owner = await fresh_identity(db, principal, owner)
    stored = normalized_policy(actor.get(POLICY_FIELD), owner=owner)
    is_owner = actor.get("role") == "owner" and actor["id"] == owner
    enabled = is_owner or stored["enabled"]
    policy = AccessPolicy(actor=actor, owner_id=owner, owner_override=is_owner, enabled=enabled,
                          version=stored["version"], operations=tuple(OPERATION_LABELS if is_owner else stored["operations"] if enabled else []),
                          resources=deepcopy(stored["resources"] if enabled and not is_owner else []))
    from operational_web_page_policy import limit_workspace_policy
    return limit_workspace_policy(await project_customer_refund_access(db, policy))


def customer_refund_access_signature(policy):
    from operational_balance_access import POLICY_FIELD
    raw = policy.actor.get(POLICY_FIELD)
    return digest([policy.actor["id"], policy.owner_id, policy.owner_override, policy.enabled,
                   policy.version, policy.operations, policy.resources, policy.actor.get("web_page_access"),
                   raw.get(REFUND_FIELD) if isinstance(raw, dict) else None])


class RefundSource(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    kind: Literal["bank", "provider"]
    id: str = Field(min_length=1, max_length=200)


class RefundAccessUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_policy_version: StrictInt = Field(ge=0)
    expected_page_revision: StrictInt = Field(ge=0)
    expected_session_scope: str = Field(pattern=r"^[a-f0-9]{64}$")
    mode: Literal["none", "read", "write"]
    sources: list[RefundSource] = Field(max_length=10000)

    @field_validator("sources")
    @classmethod
    def distinct_sources(cls, rows):
        if len({(row.kind, row.id) for row in rows}) != len(rows):
            raise ValueError("customer_refund_source_duplicate")
        return rows


async def _source_catalog(db, owner):
    from operational_balance_sources import entities
    result = []
    for kind in ("bank", "provider"):
        try:
            rows = await entities(db, owner, kind)
        except ValueError:
            fail("customer_refund_sources_unavailable", "تعذر التحقق من مصادر الاسترجاع", 409)
        result.extend({"kind": kind, "id": row["id"], "name": row["name"], "currency": "SAR"}
                      for row in rows if row.get("currency") == "SAR" and row.get("ready") is not False
                      and row.get("settings_complete") is not False)
    return result


def _scope(owner, actor, employee, account):
    return digest(["customer_refund_access", owner, actor["id"], employee["id"], account["id"]])


def _page_revision(account):
    from web_page_access import _grant_revision
    return _grant_revision(account)


async def _owner(db, principal, expected=None):
    from operational_balance_access import fresh_identity
    actor, owner = await fresh_identity(db, principal, expected)
    if actor.get("role") != "owner" or actor["id"] != owner or not _active(actor):
        fail("customer_refund_access_owner_required", "إدارة صلاحيات الاسترجاع متاحة للمالك فقط", 403)
    return actor, owner


async def _target(db, owner, employee_id):
    employee = await db[EMPLOYEES].find_one({"user_id": owner, "id": employee_id}, {"_id": 0})
    if not _active_employee(employee):
        fail("customer_refund_employee_not_found", "الموظف غير موجود ضمن حساب المالك", 404)
    account_id = employee.get("account_user_id")
    if not account_id:
        fail("customer_refund_account_required", "اربط حساب دخول بالموظف أولًا", 409)
    account = await db["users"].find_one({"id": account_id, "created_by": owner}, {"_id": 0})
    if (not account or account.get("role") in {"owner", "store_driver", "meta_reviewer"} or account.get("is_owner") is True
            or not _active(account)):
        fail("customer_refund_account_unavailable", "حساب الموظف غير متاح", 404)
    return employee, account


def _view(owner, actor, employee, account, sources):
    from operational_balance_access import POLICY_FIELD, normalized_policy
    policy = normalized_policy(account.get(POLICY_FIELD), owner=owner)
    raw = account.get(POLICY_FIELD)
    grant = normalized_refund_grant(raw.get(REFUND_FIELD)) if isinstance(raw, dict) and REFUND_FIELD in raw else None
    if grant is None:
        mode = "write" if set(policy["operations"]) & (REFUND_OPERATIONS - {REFUND_READ}) else "read" if REFUND_READ in policy["operations"] else "none"
        selected = [{"kind": row["kind"], "id": row["id"]} for row in policy["resources"]
                    if row["kind"] in {"bank", "provider"} and row["use"]]
    else:
        mode, selected = grant["mode"], grant["sources"]
    return {"employee_id": employee["id"], "user": {"id": account["id"], "name": account.get("name") or ""},
            "access": {"mode": mode, "policy_version": policy["version"], "page_revision": _page_revision(account),
                       "session_scope": _scope(owner, actor, employee, account), "sources": selected, "enabled": policy["enabled"]},
            "sources": sources}


def install_customer_refund_access_routes(router, db, current_user):
    route = "/management/employees/{employee_id}/customer-refund-access"

    @router.get(route)
    async def current_grant(employee_id: str, response: Response, user=Depends(current_user)):
        actor, owner = await _owner(db, user)
        employee, account = await _target(db, owner, employee_id)
        sources = await _source_catalog(db, owner)
        await _owner(db, user, owner)
        fresh_employee, fresh_account = await _target(db, owner, employee_id)
        if fresh_account["id"] != account["id"]:
            fail("customer_refund_access_changed", "تغير حساب الموظف؛ أعد فتح الصلاحيات", 409)
        response.headers["Cache-Control"] = "no-store, private"
        return _view(owner, actor, fresh_employee, fresh_account, sources)

    @router.put(route)
    async def update_grant(employee_id: str, payload: RefundAccessUpdate, user=Depends(current_user)):
        from operational_balance_access import POLICY_FIELD, normalized_policy
        from web_page_access import FIELD, _member_view
        actor, owner = await _owner(db, user)
        employee, account = await _target(db, owner, employee_id)
        policy = normalized_policy(account.get(POLICY_FIELD), owner=owner)
        if (payload.expected_session_scope != _scope(owner, actor, employee, account)
                or payload.expected_policy_version != policy["version"] or payload.expected_page_revision != _page_revision(account)):
            fail("customer_refund_access_changed", "تغير الحساب أو الصلاحيات؛ أعد تحميلها قبل الحفظ", 409)
        raw = account.get(POLICY_FIELD)
        if raw is not None and (not isinstance(raw, dict) or type(raw.get("enabled")) is not bool
                or raw.get("owner_id") != owner or raw["enabled"] != policy["enabled"]
                or raw.get("operations", []) != policy["operations"] or not isinstance(raw.get("resources", []), list)
                or len(raw.get("resources", [])) != len(policy["resources"])):
            fail("customer_refund_access_invalid", "راجع صلاحيات الحساب الحالية قبل تعديل الاسترجاع", 409)
        other_operations = set(policy["operations"]) - REFUND_OPERATIONS
        from provider_statement_access import STATEMENT_FIELD, normalized_statement_grant
        other_statements = normalized_statement_grant((raw or {}).get(STATEMENT_FIELD))["mode"] != "none"
        if payload.mode != "none" and not policy["enabled"] and (other_operations or other_statements or any(
                row["use"] or row["balance"] or row["report"] or row["print"] or row["movements"] != "none" for row in policy["resources"])):
            fail("customer_refund_workspace_disabled", "صلاحيات الأرصدة الأخرى موقوفة؛ فعّلها صراحة من إدارة الأرصدة قبل إتاحة الاسترجاع", 409)
        sources = await _source_catalog(db, owner)
        selected = [row.model_dump() for row in payload.sources]
        if payload.mode != "none" and not {(row["kind"], row["id"]) for row in selected}.issubset(
                {(row["kind"], row["id"]) for row in sources}):
            fail("customer_refund_source_invalid", "اختر بنكًا أو منصة متاحة ضمن حساب المالك", 422)
        operations = set(other_operations)
        if payload.mode == "read":
            operations.add(REFUND_READ)
        elif payload.mode == "write":
            operations.update(REFUND_OPERATIONS)
        stamp = now()
        updated = {**deepcopy(raw or {}), "owner_id": owner, "version": policy["version"] + 1,
                   "enabled": policy["enabled"] or payload.mode != "none", "operations": sorted(operations),
                   "resources": deepcopy((raw or {}).get("resources", [])),
                   REFUND_FIELD: {"mode": payload.mode, "sources": selected, "employee_id": employee_id},
                   "updated_by": actor["id"], "updated_at": stamp}
        original_web = account.get(FIELD)
        if original_web is not None and (not isinstance(original_web, dict) or original_web.get("owner_id") != owner
                or not isinstance(original_web.get("grants"), list)):
            fail("customer_refund_access_invalid", "راجع صلاحيات صفحات الحساب قبل تعديل الاسترجاع", 409)
        original_grants = (original_web["grants"] if original_web else
                           [{"id": key, "mode": mode} for key, mode in _member_view(account)["page_modes"].items()])
        grants = [deepcopy(row) for row in original_grants if not isinstance(row, dict) or row.get("id") != "balances.refund"]
        if payload.mode != "none":
            grants.append({"id": "balances.refund", "mode": payload.mode})
        web = {**deepcopy(original_web or {}), "owner_id": owner, "grants": grants,
               "revision": payload.expected_page_revision + 1, "updated_at": stamp, "updated_by": actor["id"]}
        await _owner(db, user, owner)
        fresh_employee, fresh_account = await _target(db, owner, employee_id)
        if fresh_account["id"] != account["id"]:
            fail("customer_refund_access_changed", "تغير حساب الموظف؛ أعد فتح الصلاحيات", 409)
        selector = {"id": account["id"], "created_by": owner, "role": account.get("role"),
                    "disabled": {"$ne": True}, "is_active": {"$ne": False}, "active": {"$ne": False},
                    "is_owner": {"$ne": True}, "deleted_at": account.get("deleted_at"),
                    POLICY_FIELD: account[POLICY_FIELD] if POLICY_FIELD in account else {"$exists": False},
                    FIELD: account[FIELD] if FIELD in account else {"$exists": False}}
        result = await db["users"].update_one(selector, {"$set": {POLICY_FIELD: updated, FIELD: web}})
        if result.matched_count != 1:
            fail("customer_refund_access_changed", "تغير الحساب أو الصلاحيات؛ أعد تحميلها قبل الحفظ", 409)
        return _view(owner, actor, fresh_employee, {**account, POLICY_FIELD: updated, FIELD: web}, sources)
