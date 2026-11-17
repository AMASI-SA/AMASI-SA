"""Owner-managed, versioned workspace access; never exposes balances."""
from __future__ import annotations

from collections import defaultdict
from typing import Literal

from fastapi import Depends
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, field_validator

from operational_balance_access import (
    OPERATION_LABELS, POLICY_FIELD, RESOURCE_KIND_LABELS, RESOURCE_KINDS,
    load_access, normalized_policy, policy_version,
)
from operational_balance_sources import entities
from operational_balance_store import fail, now


class ResourceGrant(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    kind: str = Field(min_length=1, max_length=50)
    id: str = Field(min_length=1, max_length=200)
    use: StrictBool
    movements: Literal["none", "own", "all"]
    balance: StrictBool

    @field_validator("kind")
    @classmethod
    def valid_kind(cls, value):
        if value not in RESOURCE_KINDS:
            raise ValueError("operational_access_resource_kind")
        return value


class AccessUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    expected_version: StrictInt = Field(ge=0)
    enabled: StrictBool
    operations: list[str] = Field(max_length=len(OPERATION_LABELS))
    resources: list[ResourceGrant] = Field(max_length=10000)

    @field_validator("operations")
    @classmethod
    def valid_operations(cls, value):
        if len(set(value)) != len(value) or any(key not in OPERATION_LABELS for key in value):
            raise ValueError("operational_access_operations_invalid")
        return value

    @field_validator("resources")
    @classmethod
    def distinct_resources(cls, value):
        keys = {(row.kind, row.id) for row in value}
        if len(keys) != len(value):
            raise ValueError("operational_access_resource_duplicate")
        return value


def _user_view(user):
    return {"id": user["id"], "name": user.get("name") or "", "role": user.get("role") or ""}


def _access_view(user, owner):
    if user.get("role") == "owner" and user["id"] == owner:
        return {"version": policy_version(user.get(POLICY_FIELD)), "enabled": True,
                "operations": list(OPERATION_LABELS), "resources": [], "owner_override": True}
    return normalized_policy(user.get(POLICY_FIELD), owner=owner)


async def _resource_rows(db, owner, kind):
    try:
        return await entities(db, owner, kind)
    except ValueError:
        fail("operational_access_catalog_incomplete", "تعذر التحقق من هوية الحسابات؛ راجع إعدادات الجهات", 409)


def install_access_routes(router, db, current_user, scope):
    async def owner_scope(principal, expected_owner=None):
        actor, owner, _ = await scope(db, principal, "workspace")
        policy = await load_access(db, actor, owner)
        if not policy.owner_override:
            fail("operational_access_owner_required", "إدارة صلاحيات الأرصدة متاحة للمالك فقط", 403)
        if expected_owner is not None and owner != expected_owner:
            fail("operational_actor_scope_changed", "تغير ارتباط الحساب؛ أعد تسجيل الدخول", 403)
        return policy.actor, owner

    async def target_user(owner, user_id):
        user = await db["users"].find_one({"id": user_id}, {"_id": 0})
        if (not user or user.get("deleted_at")
                or (user_id != owner and (user.get("created_by") != owner or user.get("role") == "owner"))):
            fail("operational_access_user_not_found", "المستخدم غير موجود ضمن فريق المالك", 404)
        return user

    @router.get("/access/current")
    async def current_access(user=Depends(current_user)):
        actor, owner, _ = await scope(db, user, "workspace")
        return (await load_access(db, actor, owner)).current()

    @router.get("/access/catalog")
    async def access_catalog(user=Depends(current_user)):
        _, owner = await owner_scope(user)
        resources = []
        for kind in RESOURCE_KIND_LABELS:
            for row in await _resource_rows(db, owner, kind):
                resources.append({"kind": kind, "id": row["id"], "name": row["name"],
                                  "currency": row.get("currency")})
        await owner_scope(user, owner)
        return {"operations": [{"key": key, "label": label} for key, label in OPERATION_LABELS.items()],
                "resources": resources,
                "resource_kinds": [{"key": key, "label": label} for key, label in RESOURCE_KIND_LABELS.items()]}

    @router.get("/access/users")
    async def access_users(user=Depends(current_user)):
        _, owner = await owner_scope(user)
        users = await db["users"].find(
            {"$or": [{"id": owner, "role": "owner"}, {"created_by": owner, "role": {"$ne": "owner"}}],
             "deleted_at": {"$in": [None, ""]}},
            {"_id": 0, "id": 1, "name": 1, "role": 1},
        ).to_list(5001)
        if len(users) > 5000:
            fail("operational_access_users_too_many", "قائمة المستخدمين تحتاج إلى تقسيم قبل العرض", 409)
        await owner_scope(user, owner)
        return {"items": sorted((_user_view(row) for row in users), key=lambda row: (row["name"], row["id"]))}

    @router.get("/access/users/{user_id}")
    async def user_access(user_id: str, user=Depends(current_user)):
        _, owner = await owner_scope(user)
        target = await target_user(owner, user_id)
        await owner_scope(user, owner)
        return {"user": _user_view(target), "access": _access_view(target, owner)}

    @router.put("/access/users/{user_id}")
    async def update_access(user_id: str, payload: AccessUpdate, user=Depends(current_user)):
        actor, owner = await owner_scope(user)
        target = await target_user(owner, user_id)
        if target["id"] == owner or target.get("role") == "owner":
            fail("operational_access_owner_immutable", "صلاحيات المالك كاملة ولا يمكن تخفيضها", 409)
        current_version = policy_version(target.get(POLICY_FIELD))
        if current_version != payload.expected_version:
            fail("operational_access_changed", "تغيرت الصلاحيات؛ أعد قراءتها قبل الحفظ", 409)
        grouped = defaultdict(set)
        for resource in payload.resources:
            grouped[resource.kind].add(resource.id)
        for kind, selected in grouped.items():
            available = {row["id"] for row in await _resource_rows(db, owner, kind)}
            if not selected.issubset(available):
                fail("operational_access_resource_invalid", "اختر حسابًا أو جهة موجودة ضمن حساب المالك", 422)
        actor, _ = await owner_scope(user, owner)
        stored = {"version": current_version + 1, "owner_id": owner, "enabled": payload.enabled,
                  "operations": sorted(payload.operations),
                  "resources": sorted((row.model_dump() for row in payload.resources), key=lambda row: (row["kind"], row["id"])),
                  "updated_at": now(), "updated_by": actor["id"]}
        selector = {"id": user_id, "created_by": owner, "role": {"$ne": "owner"},
                    "deleted_at": {"$in": [None, ""]}}
        # Compare the observed document as well as the public version. This
        # also protects the absent -> version 1 transition and malformed legacy
        # policies, without accepting a concurrent write that reused a version.
        selector[POLICY_FIELD] = target[POLICY_FIELD] if POLICY_FIELD in target else {"$exists": False}
        result = await db["users"].update_one(selector, {"$set": {POLICY_FIELD: stored}})
        if result.matched_count != 1:
            fail("operational_access_changed", "تغيرت الصلاحيات أو ارتباط المستخدم؛ أعد القراءة قبل الحفظ", 409)
        target[POLICY_FIELD] = stored
        return {"user": _user_view(target), "access": _access_view(target, owner)}
