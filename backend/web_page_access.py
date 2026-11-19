"""Explicit browser page grants for employee accounts in one merchant's team."""
from __future__ import annotations

from datetime import datetime, timezone
import re
from typing import Any, Callable, Literal

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field

from accounting_module_contract import ACCOUNTING_PAGES, ACCOUNTING_PAGE_PERMISSION_KEYS, ACCOUNTING_PAGE_WRITES

from operational_web_page_policy import OPERATIONAL_WEB_PAGES, enforce_operational_web_pages
from web_workspace_pages import CORE_WEB_PAGES, SECTION_LABELS, enforce_workspace_read, workspace_read_pages
from web_marketing_page_policy import MARKETING_WEB_PAGES, enforce_marketing_web_pages, make_web_marketing_router
from web_legacy_page_policy import LEGACY_WEB_PAGES, enforce_legacy_web_pages, make_legacy_web_router
from web_detail_pages import DETAIL_WEB_PAGES, enforce_web_detail_pages, make_web_detail_router
from web_extra_pages import EXTRA_WEB_PAGES, make_extra_web_router
from web_employee_page_policy import enforce_web_employee_tool_boundary, make_web_employee_tools_router

FIELD = "web_page_access"
PRODUCTS_PAGE = "products.catalog"
INTAKE_PAGE = "products.intake"
COMPONENTS_PAGE = "products.components"
WAREHOUSES_PAGE = "products.warehouses"
INVENTORY_PAGE = "products.inventory"
MISSING_COST_PAGE = "products.missing_costs"
RECEIVING_PAGE = "products.receiving"
ORDERS_PAGE = "orders.list"

PAGES = (
    {"id": PRODUCTS_PAGE, "section": "products", "label": "إدارة المنتجات",
     "to": "/products-v2", "write_label": "حفظ مسودات تعديل بيانات المنتج"},
    {"id": INVENTORY_PAGE, "section": "products", "label": "المخزون", "to": "/inventory-v2", "aliases": ["/inventory-v2?tab=stock"]},
    {"id": MISSING_COST_PAGE, "section": "products", "label": "المنتجات بدون تكلفة",
     "to": "/product-costs?tab=missing", "review_component": "missing_costs"},
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
            **({"aliases": ["/integrations-v2?workspace=financial"]} if row["id"] == "home" else {}),
            **({"write_label": "الإجراءات المحاسبية الممنوحة للموظف"} if row["permission"] in ACCOUNTING_PAGE_WRITES else {}),
        }
        for row in ACCOUNTING_PAGES
    ),
)
# Retired routes were real bookmarks in the previous navigation. A managed
# browser opens the currently granted destination using its existing page reader.
_LEGACY_BOOKMARKS = {
    "/shipping/orders-ledger": ("/shipping-accounts", "/shipping/ledger", "/shipping/cod-settlements"),
    "/settlements-overview": ("/settlements",),
    "/new-transaction": ("/transfers", "/financial-input-hub", "/advances"),
    "/accounting/reconciliation": ("/reconciliation",),
    "/financial-position-ledger": ("/financial-position",),
    "/suppliers-new": ("/counterparties",),
    "/suppliers-ledger": ("/suppliers-new?tab=balances",),
    "/integrations-v2/qoyod?tab=settings": ("/integrations/qoyod/settings",),
    "/integrations-v2/qoyod?tab=reconciliation": ("/integrations/qoyod/reconciliation",),
    "/integrations-v2/qoyod?tab=exceptions": ("/integrations/qoyod/unsent-orders", "/admin/qoyod-manual-send"),
}
PAGES = tuple(
    {**page, "section_label": page.get("section_label") or SECTION_LABELS.get(page["section"], "صفحات ميزان القديم")}
    for page in (*PAGES, *CORE_WEB_PAGES, *OPERATIONAL_WEB_PAGES, *MARKETING_WEB_PAGES, *LEGACY_WEB_PAGES, *EXTRA_WEB_PAGES, *DETAIL_WEB_PAGES)
)
PAGES = tuple(
    {**page, "aliases": list(dict.fromkeys((*page.get("aliases", ()), *_LEGACY_BOOKMARKS[page["to"]])))}
    if page["to"] in _LEGACY_BOOKMARKS else page for page in PAGES
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
        if page and not page.get("owner_only") and (mode == "read" or (mode == "write" and "write_label" in page)):
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
    from auth import account_is_disabled

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
    ("GET", r"/api/product-costs/missing", MISSING_COST_PAGE, False),
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


# Only the signed-in identity is accepted by these existing handlers. They
# retain password verification, email uniqueness and session revocation.
_SELF_SERVICE_ROUTES = {
    ("/api/auth/me", "GET"), ("/api/auth/logout", "POST"), ("/api/auth/refresh", "POST"),
    *((f"/api/auth/profile/{field}", "PUT") for field in ("name", "email", "password", "security-question")),
}


def enforce_web_catalog_access(user: dict[str, Any], path: str, method: str, query=None) -> None:
    modes = configured_page_modes(user)
    if modes is None:
        return
    enforce_workspace_read(modes, path, method, query)
    enforce_operational_web_pages(modes, path, method, query)
    detail_read = enforce_web_detail_pages(modes, path, method, query)
    marketing_read = enforce_marketing_web_pages(modes, path, method, query)
    enforce_legacy_web_pages(modes, path, method, query)
    enforce_web_employee_tool_boundary(user, path, method)
    normalized = path.rstrip("/")
    if not _CATALOG_SCOPE.match(path):
        if detail_read or marketing_read or workspace_read_pages(normalized, query) is not None:
            return
        if normalized.startswith("/api/operational-balances/"):
            return  # Fail-closed operational_web_page_policy checked above.
        if normalized in {"/api/orders-v2", "/api/orders-v2/filters/summary"} and method == "GET":
            return require_page_mode(modes, ORDERS_PAGE)
        if normalized.startswith(("/api/financial-provider-apps/accounting-module/", "/api/accounting-module/")):
            # Existing accounting routes enforce independent page + action grants.
            return
        if method == "GET" and re.fullmatch(r"/api/web-page-access-v1/(?:employee-tools|marketing-read|legacy-read|extra-read)/[a-zA-Z0-9_.-]+", normalized):
            return  # Each projection authorizes its exact selected page.
        if method == "GET" and re.fullmatch(r"/api/web-page-access-v1/employee-tools/employees[.]staff/[^/]+/events", normalized):
            return
        if (normalized, method) in _SELF_SERVICE_ROUTES:
            return
        raise HTTPException(403, detail={"code": "web_page_operation_not_available"})
    for allowed_method, pattern, page, write in _CATALOG_ROUTES:
        if method == allowed_method and re.fullmatch(pattern, normalized):
            require_page_mode(modes, page, write=write)
            return
    raise HTTPException(403, detail={"code": "web_page_operation_not_available"})


async def bind_workspace_reader(db: Any, user: dict[str, Any], path: str, method: str, query) -> None:
    user.pop("_web_workspace_read", None)
    if method != "GET":
        return
    modes = configured_page_modes(user)
    pages = workspace_read_pages(path, query) if modes is not None else None
    if not pages:
        return
    page_id = next((selected for selected in pages if selected in modes), None)
    if page_id:
        owner = await delegated_page_owner(db, user, page_id)
        user["_web_workspace_read"] = (page_id, owner)


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
    from auth import account_is_disabled

    router = APIRouter(prefix="/web-page-access-v1", tags=["Mezan web page access"])

    router.include_router(make_web_employee_tools_router(db, current_user))
    router.include_router(make_web_marketing_router(db, current_user))
    router.include_router(make_legacy_web_router(db, current_user))
    router.include_router(make_extra_web_router(db, current_user))
    router.include_router(make_web_detail_router(db, current_user))

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
            if page is None or page.get("owner_only") or (mode == "write" and "write_label" not in page):
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
