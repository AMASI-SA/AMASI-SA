"""Explicit browser page grants for employee accounts in one merchant's team."""
from __future__ import annotations

from datetime import datetime, timezone
import re
from typing import Any, Callable, Literal

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field

from accounting_module_contract import ACCOUNTING_PAGES, ACCOUNTING_PAGE_PERMISSION_KEYS, ACCOUNTING_PAGE_WRITES
from auth import account_is_disabled

FIELD = "web_page_access"
PRODUCTS_PAGE = "products.catalog"
INTAKE_PAGE = "products.intake"
COMPONENTS_PAGE = "products.components"
WAREHOUSES_PAGE = "products.warehouses"
INVENTORY_PAGE = "products.inventory"
RECEIVING_PAGE = "products.receiving"
ORDERS_PAGE = "orders.list"

PAGES = (
    {"id": PRODUCTS_PAGE, "section": "products", "label": "إدارة المنتجات",
     "to": "/products-v2", "write_label": "حفظ مسودات تعديل بيانات المنتج"},
    {"id": INVENTORY_PAGE, "section": "products", "label": "المخزون", "to": "/inventory-v2"},
    {"id": INTAKE_PAGE, "section": "products", "label": "استقبال المنتجات",
     "to": "/products-v2?workspace=intake", "write_label": "إعداد مسودات المنتجات الجديدة"},
    {"id": RECEIVING_PAGE, "section": "products", "label": "استلام المخزون", "to": "/inventory-receiving-v2"},
    {"id": COMPONENTS_PAGE, "section": "products", "label": "مكونات المنتجات",
     "to": "/components-v2", "write_label": "تعديل التصنيفات والمجموعات وربط المكونات بها"},
    {"id": WAREHOUSES_PAGE, "section": "products", "label": "الفروع والمخازن",
     "to": "/components-v2?workspace=warehouse", "write_label": "إضافة الفروع والأقسام والأدراج"},
    {"id": ORDERS_PAGE, "section": "orders", "label": "كل الطلبات", "to": "/orders-v2"},
    *(
        {
            "id": row["permission"],
            "section": "accounting",
            "label": row["label"],
            "to": f"/integrations-v2?workspace=financial&page={row['id']}",
            **({"write_label": "الإجراءات المحاسبية الممنوحة للموظف"} if row["permission"] in ACCOUNTING_PAGE_WRITES else {}),
        }
        for row in ACCOUNTING_PAGES
    ),
)
PAGES_BY_ID = {page["id"]: page for page in PAGES}
PageMode = Literal["read", "write"]
EXCLUDED_ROLES = ("owner", "store_driver", "meta_reviewer")
_TEAM_FIELDS = {
    "_id": 0, "id": 1, "name": 1, "email": 1, "role": 1, "is_owner": 1,
    "created_by": 1, "is_active": 1, "disabled": 1, "deleted_at": 1,
    "accounting_permissions": 1, FIELD: 1,
}


def _role(user: dict[str, Any]) -> str:
    return str(user.get("role") or "").strip().casefold()


def _team_member(user: dict[str, Any]) -> bool:
    actor_id = str(user.get("id") or "").strip()
    owner_id = str(user.get("created_by") or "").strip()
    return (
        bool(actor_id and owner_id and actor_id != owner_id)
        and _role(user) not in EXCLUDED_ROLES
        and user.get("is_owner") is not True
    )


def configured_page_modes(user: dict[str, Any]) -> dict[str, PageMode] | None:
    """Missing grant preserves existing accounts. An empty grant denies all pages."""
    if not _team_member(user) or FIELD not in user or user.get("_session_client") == "amasi_mobile":
        return None
    grant = user.get(FIELD)
    if not isinstance(grant, dict) or grant.get("owner_id") != user.get("created_by"):
        return {}
    rows = grant.get("grants")
    if not isinstance(rows, list):
        return {}
    modes = {}
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("id"), str):
            continue
        page = PAGES_BY_ID.get(row["id"])
        mode = row.get("mode")
        if page and (mode == "read" or (mode == "write" and "write_label" in page)):
            modes[row["id"]] = mode
    return modes


def web_page_access_for_user(user: dict[str, Any]) -> dict | None:
    modes = configured_page_modes(user)
    return None if modes is None else {
        "pages": [{**page, "mode": modes[page["id"]]} for page in PAGES if page["id"] in modes]
    }


def require_page_mode(modes: dict[str, PageMode], page_id: str, *, write: bool = False) -> None:
    mode = modes.get(page_id)
    if mode is None or (write and mode != "write"):
        raise HTTPException(403, detail={"code": "web_page_permission_required"})


async def delegated_page_owner(db: Any, user: dict[str, Any], page_id: str, *, write: bool = False) -> str | None:
    """Resolve a granted merchant without replacing the authenticated actor."""
    modes = configured_page_modes(user)
    if modes is None:
        return None
    require_page_mode(modes, page_id, write=write)
    owner_id = str(user["created_by"]).strip()
    owner = await db.users.find_one({"id": owner_id, "role": "owner"}, _TEAM_FIELDS)
    if owner is None or account_is_disabled(owner):
        raise HTTPException(403, detail={"code": "web_page_store_unavailable"})
    return owner_id


# Staff with an explicit web profile use only the endpoints reviewed for each
# Product workspace. Old operational roles cannot bypass the new page mode.
_RESOURCE = r"[^/]+"
_PRODUCT = rf"/api/products-v2/{_RESOURCE}"
_CREATION = "/api/products-v2/creation-drafts"
_COMPONENT = "/api/components-v2"
_WAREHOUSE = "/api/warehouse-locations-v2"
_CATALOG_ROUTES = (
    ("GET", r"/api/products-v2", PRODUCTS_PAGE, False),
    ("GET", rf"{_PRODUCT}/control-center", PRODUCTS_PAGE, True),
    ("PUT", rf"{_PRODUCT}/control-center/draft", PRODUCTS_PAGE, True),
    ("GET", _CREATION, INTAKE_PAGE, True),
    ("POST", _CREATION, INTAKE_PAGE, True),
    ("GET", rf"{_CREATION}/{_RESOURCE}", INTAKE_PAGE, True),
    ("PUT", rf"{_CREATION}/{_RESOURCE}", INTAKE_PAGE, True),
    ("GET", r"/api/ai-store-operations/product-intake", INTAKE_PAGE, False),
    ("GET", r"/api/products-v2/category-catalog", INTAKE_PAGE, False),
    ("GET", r"/api/operational-balances/inventory", INVENTORY_PAGE, False),
    ("GET", r"/api/inventory-v2/purchase-receiving/catalog", RECEIVING_PAGE, False),
    ("GET", rf"{_COMPONENT}/workspace", COMPONENTS_PAGE, False),
    ("POST", rf"{_COMPONENT}/(?:categories|groups)", COMPONENTS_PAGE, True),
    ("PUT", rf"{_COMPONENT}/(?:categories/{_RESOURCE}|groups/{_RESOURCE}|{_RESOURCE}/categories)", COMPONENTS_PAGE, True),
    ("GET", r"/api/warehouse-locations/warehouses", WAREHOUSES_PAGE, False),
    ("GET", rf"{_WAREHOUSE}/(?:catalog|section-capabilities|warehouses/{_RESOURCE}/sections)", WAREHOUSES_PAGE, False),
    ("POST", rf"{_WAREHOUSE}/(?:warehouses|sections|sections/{_RESOURCE}/cabinets)", WAREHOUSES_PAGE, True),
    ("GET", _PRODUCT, PRODUCTS_PAGE, False),
)
_CATALOG_SCOPE = re.compile(
    r"/api/(?:products-v2|components-v2|inventory-v2|warehouse-locations(?:-v2)?|product-costs)(?:/|$)"
    r"|/api/operational-balances/(?:inventory(?:[-/]|$)|standalone-inventory(?:/|$))"
    r"|/api/ai-store-operations/product-intake(?:/|$)"
)


def enforce_web_catalog_access(user: dict[str, Any], path: str, method: str) -> None:
    modes = configured_page_modes(user)
    if modes is None or not _CATALOG_SCOPE.match(path):
        return
    normalized = path.rstrip("/")
    for allowed_method, pattern, page, write in _CATALOG_ROUTES:
        if method == allowed_method and re.fullmatch(pattern, normalized):
            require_page_mode(modes, page, write=write)
            return
    raise HTTPException(403, detail={"code": "web_page_operation_not_available"})


def _grant_revision(user: dict[str, Any]) -> int:
    grant = user.get(FIELD)
    value = grant.get("revision") if isinstance(grant, dict) else None
    return value if type(value) is int and value > 0 else 0


def _member_view(user: dict[str, Any]) -> dict:
    modes = configured_page_modes(user)
    assigned = set(user.get("accounting_permissions") or [])
    selected = modes if modes is not None else {
        page: "write" if assigned & ACCOUNTING_PAGE_WRITES.get(page, set()) else "read"
        for page in sorted(assigned & ACCOUNTING_PAGE_PERMISSION_KEYS)
    }
    return {
        "id": user["id"],
        "name": user.get("name") or "",
        "email": user.get("email") or "",
        "revision": _grant_revision(user),
        "configured": modes is not None,
        "page_modes": selected,
    }


def _require_owner(user: dict[str, Any]) -> str:
    owner_id = str(user.get("id") or "").strip()
    if not owner_id or _role(user) != "owner" or user.get("_mobile_actor_id"):
        raise HTTPException(403, detail={"code": "owner_required"})
    return owner_id


class PageGrantUpdate(BaseModel):
    page_modes: dict[str, PageMode] = Field(max_length=len(PAGES))
    expected_revision: int = Field(ge=0, strict=True)


def make_web_page_access_router(db: Any, current_user: Callable) -> APIRouter:
    router = APIRouter(prefix="/web-page-access-v1", tags=["Mezan web page access"])

    @router.get("")
    async def list_members(response: Response, user: dict = Depends(current_user)) -> dict:
        owner_id = _require_owner(user)
        response.headers["Cache-Control"] = "no-store, private"
        members = await db.users.find(
            {"created_by": owner_id, "role": {"$nin": list(EXCLUDED_ROLES)}}, _TEAM_FIELDS
        ).sort([("name", 1), ("id", 1)]).to_list(5000)
        return {"pages": list(PAGES), "members": [
            _member_view(row) for row in members if _team_member(row) and not account_is_disabled(row)
        ]}

    @router.put("/{member_id}")
    async def save_pages(member_id: str, payload: PageGrantUpdate,
                         user: dict = Depends(current_user)) -> dict:
        owner_id = _require_owner(user)
        for page_id, mode in payload.page_modes.items():
            page = PAGES_BY_ID.get(page_id)
            if page is None or (mode == "write" and "write_label" not in page):
                raise HTTPException(422, detail={"code": "invalid_web_page_mode"})
        selector = {
            "id": member_id,
            "created_by": owner_id,
            "role": {"$nin": list(EXCLUDED_ROLES)},
            "is_owner": {"$ne": True},
            "disabled": {"$ne": True},
            "is_active": {"$ne": False},
        }
        target = await db.users.find_one(selector, _TEAM_FIELDS)
        if target is None or not _team_member(target) or account_is_disabled(target):
            raise HTTPException(404, detail={"code": "team_user_not_found"})
        if _grant_revision(target) != payload.expected_revision:
            raise HTTPException(409, detail={"code": "web_page_access_changed"})
        grant = {
            "owner_id": owner_id,
            "grants": [{"id": page, "mode": mode} for page, mode in sorted(payload.page_modes.items())],
            "revision": payload.expected_revision + 1,
            "updated_by": owner_id,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        selector["role"] = target.get("role")
        selector["deleted_at"] = target.get("deleted_at")
        selector[FIELD] = target[FIELD] if FIELD in target else {"$exists": False}
        result = await db.users.update_one(selector, {"$set": {FIELD: grant}})
        if not result.matched_count:
            raise HTTPException(409, detail={"code": "web_page_access_changed"})
        return {"member": _member_view({**target, FIELD: grant})}

    return router
