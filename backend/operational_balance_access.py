"""Fresh, explicit access to the scoped operational workspace.

The legacy browser/native grants are not an implicit grant here. A resource's
use, movement visibility and balance visibility are independent. Money stays in
the canonical operational aggregate; this module only authorizes access to it.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from copy import deepcopy
from dataclasses import dataclass, field

from operational_balance_store import AUTHORIZATION_GUARD, fail


POLICY_FIELD = "operational_balance_access"
OPERATION_LABELS = {
    "employee.accrual": "تسجيل مستحق موظف",
    "employee.payment": "دفع مبلغ لموظف",
    "employee.collection": "استلام مبلغ من موظف",
    "employee.adjustment": "تسوية رصيد موظف",
    "supplier.accrual": "تسجيل مستحق مورد",
    "supplier.payment": "دفع مبلغ لمورد",
    "supplier.collection": "استلام مبلغ من مورد",
    "supplier.adjustment": "تسوية رصيد مورد",
    "external.receive": "استلام مبلغ من جهة خارجية",
    "external.pay": "تسليم مبلغ لجهة خارجية",
    "external.salary": "صرف راتب موظف من جهة خارجية",
    "external.charity_company": "صرف صدقات المنشأة من جهة خارجية",
    "external.charity_owner": "صرف صدقات المالك من جهة خارجية",
    "external.household": "صرف مصروفات منزلية من جهة خارجية",
    "external.personal": "صرف مصروفات شخصية من جهة خارجية",
    "accounts.transfer": "التحويل بين الحسابات",
    "custody.fund": "تمويل عهدة موظف",
    "custody.spend": "الصرف من عهدة موظف",
    "custody.return": "رد المتبقي من عهدة موظف",
    "expense.pay": "دفع مصروف تشغيلي",
    "owner.withdraw": "سحب مبلغ للمالك",
    "owner.return": "إرجاع مبلغ من المالك",
    "provider.settle": "تسوية مزود دفع أو شبكة",
    "ads.fund": "شحن أو سداد حساب إعلاني",
    "courier.receive": "استلام مبلغ من موصل أو شركة شحن",
    "courier.pay": "دفع مبلغ لموصل أو شركة شحن",
    "customer.refund_request": "تسجيل طلب استرجاع عميل",
    "customer.refund_execute": "تنفيذ رد مبلغ لعميل",
}
RESOURCE_KIND_LABELS = {
    "bank": "الحسابات البنكية",
    "cash": "الصناديق",
    "employee_custody": "عهد الموظفين",
    "employee": "الموظفون",
    "supplier": "الموردون",
    "external_person": "الجهات الخارجية",
    "provider": "مزودو الدفع",
    "courier": "شركات الشحن",
    "store_driver": "الموصلون",
    "ad_account": "الحسابات الإعلانية",
    "pos_receivable": "حسابات الشبكة",
    "operating_expense": "تصنيفات المصروفات",
    "owner_withdrawal": "مستفيدو سحوبات المالك",
}
RESOURCE_KINDS = frozenset(RESOURCE_KIND_LABELS)
MOVEMENT_SCOPES = frozenset({"none", "own", "all"})
DENIED_RESOURCE = {"use": False, "movements": "none", "balance": False}
OWNER_RESOURCE = {"use": True, "movements": "all", "balance": True}


def policy_version(value):
    version = value.get("version", 0) if isinstance(value, dict) else 0
    return version if type(version) is int and version >= 0 else 0


def normalized_policy(value, *, owner=None):
    """Fail closed for malformed stored grants, including permissive strings."""
    empty = {"version": policy_version(value), "enabled": False,
             "operations": [], "resources": []}
    if (not isinstance(value, dict) or type(value.get("enabled")) is not bool
            or owner is not None and value.get("owner_id") != owner):
        return empty
    operations, resources = value.get("operations", []), value.get("resources", [])
    if (not isinstance(operations, list) or len(operations) > len(OPERATION_LABELS)
            or any(not isinstance(key, str) or key not in OPERATION_LABELS for key in operations)
            or len(set(operations)) != len(operations)
            or not isinstance(resources, list) or len(resources) > 10000):
        return empty
    clean, seen = [], set()
    for row in resources:
        if not isinstance(row, dict):
            return empty
        kind, identity = row.get("kind"), row.get("id")
        if (not isinstance(kind, str) or kind not in RESOURCE_KINDS
                or not isinstance(identity, str) or not identity.strip() or len(identity) > 200
                or identity != identity.strip()
                or type(row.get("use")) is not bool or type(row.get("balance")) is not bool
                or not isinstance(row.get("movements"), str) or row["movements"] not in MOVEMENT_SCOPES):
            return empty
        key = (kind, identity)
        if key in seen:
            return empty
        seen.add(key)
        clean.append({"kind": kind, "id": identity, "use": row["use"],
                      "movements": row["movements"], "balance": row["balance"]})
    return {"version": empty["version"], "enabled": value["enabled"],
            "operations": list(operations), "resources": clean}


async def fresh_identity(db, principal, expected_owner=None):
    """Recover the real mobile actor and recheck membership on every call."""
    identity = principal.get("_mobile_actor_id") or principal.get("id")
    actor = await db["users"].find_one({"id": identity}, {"_id": 0}) if identity else None
    if (not actor or actor.get("disabled") or actor.get("is_active") is False
            or actor.get("deleted_at")):
        fail("operational_actor_inactive", "الحساب غير مخول", 403)
    owner = actor["id"] if actor.get("role") == "owner" else actor.get("created_by")
    if not owner:
        fail("operational_owner_missing", "حساب الموظف غير مرتبط بالمالك", 403)
    if expected_owner is not None and expected_owner != owner:
        fail("operational_actor_scope_changed", "تغير ارتباط الحساب؛ أعد تسجيل الدخول", 403)
    owner_actor = actor if actor["id"] == owner else await db["users"].find_one({"id": owner}, {"_id": 0})
    if (not owner_actor or owner_actor.get("role") != "owner" or owner_actor.get("disabled")
            or owner_actor.get("is_active") is False or owner_actor.get("deleted_at")):
        fail("operational_owner_inactive", "حساب المالك غير متاح", 403)
    return actor, owner


@dataclass(frozen=True)
class AccessPolicy:
    actor: dict
    owner_id: str
    owner_override: bool
    enabled: bool
    version: int
    operations: tuple[str, ...]
    resources: list[dict]
    _resource_index: dict = field(init=False, repr=False, compare=False)

    def __post_init__(self):
        object.__setattr__(self, "_resource_index", {
            (row["kind"], row["id"]): dict(row) for row in self.resources
        })

    def can_operation(self, key):
        return key in OPERATION_LABELS and self.enabled and (
            self.owner_override or key in self.operations)

    def resource(self, kind, identity):
        if not self.enabled or kind not in RESOURCE_KINDS or not isinstance(identity, str) or not identity:
            return dict(DENIED_RESOURCE)
        if self.owner_override:
            return dict(OWNER_RESOURCE)
        row = self._resource_index.get((kind, identity))
        return {key: row[key] for key in DENIED_RESOURCE} if row else dict(DENIED_RESOURCE)

    def can_reference(self, kind, identity):
        access = self.resource(kind, identity)
        return access["use"] or access["balance"] or access["movements"] != "none"

    def can_read_movement(self, kind, identity, actor_id):
        mode = self.resource(kind, identity)["movements"]
        return mode == "all" or mode == "own" and bool(actor_id) and actor_id == self.actor["id"]

    def require_enabled(self):
        if not self.enabled:
            fail("operational_workspace_access_required", "ليست لديك صلاحية استخدام الأرصدة", 403)

    def require_operation(self, key):
        if not self.can_operation(key):
            fail("operational_operation_forbidden", "ليست لديك صلاحية تنفيذ هذه العملية", 403)

    def require_use(self, kind, identity):
        if not self.resource(kind, identity)["use"]:
            fail("operational_resource_forbidden", "ليست لديك صلاحية استخدام هذا الحساب أو الجهة", 403)

    def current(self):
        return {"actor": {"id": self.actor["id"], "name": self.actor.get("name") or ""},
                "owner_id": self.owner_id, "owner_override": self.owner_override,
                "enabled": self.enabled, "version": self.version,
                "operations": list(self.operations), "resources": deepcopy(self.resources)}


async def load_access(db, actor, owner):
    actor, owner = await fresh_identity(db, actor, owner)
    owner_override = actor.get("role") == "owner" and actor["id"] == owner
    policy = normalized_policy(actor.get(POLICY_FIELD), owner=owner)
    enabled = owner_override or policy["enabled"]
    return AccessPolicy(actor=actor, owner_id=owner, owner_override=owner_override,
                        enabled=enabled, version=policy["version"],
                        operations=tuple(OPERATION_LABELS if owner_override else policy["operations"] if enabled else []),
                        resources=deepcopy(policy["resources"] if enabled and not owner_override else []))


@asynccontextmanager
async def guard_access(db, actor, owner, operation, resources=()):
    """Compose fresh checks with the existing guard, including just before CAS."""
    actor_id = actor.get("_mobile_actor_id") or actor.get("id")
    operations = (operation,) if isinstance(operation, str) else tuple(operation)
    required_resources = tuple(tuple(resource) for resource in resources)
    previous = AUTHORIZATION_GUARD.get()

    async def verify():
        if previous is not None:
            await previous()
        policy = await load_access(db, {"id": actor_id}, owner)
        policy.require_enabled()
        for key in operations:
            policy.require_operation(key)
        for kind, identity in required_resources:
            policy.require_use(kind, identity)
        return policy

    policy = await verify()
    token = AUTHORIZATION_GUARD.set(verify)
    try:
        yield policy
    finally:
        AUTHORIZATION_GUARD.reset(token)
