"""Read projections for explicitly delegated employee, shipping and delivery pages."""

from fastapi import APIRouter, Depends, HTTPException, Query, Response


PAGE_SIZE = 8
PAGES = {
    "employees.staff", "employees.drivers", "employees.migration",
    "finance.collections", "finance.settlements", "shipping.settings",
}
NATIVE_DENIAL = {
    "employees.drivers": "store_delivery.manage",
    "finance.collections": "store_delivery.payments.review",
    "finance.settlements": "store_delivery.settlements.manage",
}
RAW_PREFIXES = (
    "/api/employees-v2", "/api/store-delivery/drivers",
    "/api/store-delivery/payment-review", "/api/store-delivery/settlements",
)


def enforce_web_employee_tool_boundary(user, path, method):
    """Legacy role routes expose write controls or a wider set of fields."""
    from web_page_access import configured_page_modes

    path = path.rstrip("/")
    if path != "/api/settings" and not any(path == root or path.startswith(root + "/") for root in RAW_PREFIXES):
        return
    if configured_page_modes(user) is not None:
        raise HTTPException(403, detail={"code": "web_page_operation_not_available"})


def _projection(fields):
    return {"_id": 0, **dict.fromkeys(fields, 1)}


async def _rows(collection, query, fields, order, offset):
    return await collection.find(query, _projection(fields)).sort(order).skip(offset).limit(PAGE_SIZE + 1).to_list(PAGE_SIZE + 1)


async def _collection_rows(db, owner_id, offset):
    from store_delivery_driver_app_routes import DRIVER_PAYMENT_REVIEWS
    from store_delivery_driver_routes import STORE_DRIVERS
    from store_delivery_handover_routes import ASSIGNMENTS

    reviews = await _rows(db[DRIVER_PAYMENT_REVIEWS], {"user_id": owner_id, "status": "pending"},
                          ("assignment_id", "order_number", "driver_id", "driver_name_snapshot",
                           "amount", "payment_method", "submitted_at"),
                          [("submitted_at", 1), ("assignment_id", 1)], offset)
    if not reviews:
        return []
    ids = [row["assignment_id"] for row in reviews if row.get("assignment_id")]
    assignments = {row["id"]: row async for row in db[ASSIGNMENTS].find(
        {"user_id": owner_id, "id": {"$in": ids}}, _projection(("id", "order_number", "driver_name_snapshot")))}
    ids = [row["driver_id"] for row in reviews if row.get("driver_id")]
    drivers = {row["id"]: row.get("name") async for row in db[STORE_DRIVERS].find(
        {"user_id": owner_id, "id": {"$in": ids}}, _projection(("id", "name")))}
    result = []
    for row in reviews:
        assignment = assignments.get(row.get("assignment_id"), {})
        result.append({
            "id": row.get("assignment_id"), "order": assignment.get("order_number") or row.get("order_number"),
            "driver": drivers.get(row.get("driver_id")) or assignment.get("driver_name_snapshot") or row.get("driver_name_snapshot"),
            "method": row.get("payment_method"), "amount": row.get("amount"), "date": row.get("submitted_at"),
        })
    return result


async def _page_rows(db, page_id, owner_id, offset):
    tenant = {"user_id": owner_id}
    if page_id == "employees.staff":
        from employees_v2_routes import EMPLOYEES

        return await _rows(db[EMPLOYEES], tenant, ("id", "display_name", "job_title", "department", "status"),
                           [("status", 1), ("display_name", 1), ("id", 1)], offset)
    if page_id == "employees.drivers":
        from store_delivery_driver_routes import STORE_DRIVERS

        return await _rows(db[STORE_DRIVERS], tenant, ("id", "name", "city", "shipping_company", "delivery_fee", "status"),
                           [("status", 1), ("name", 1), ("id", 1)], offset)
    if page_id == "finance.collections":
        return await _collection_rows(db, owner_id, offset)
    if page_id == "finance.settlements":
        from store_delivery_settlement_routes import SETTLEMENTS

        return await _rows(db[SETTLEMENTS], tenant, ("id", "driver_name_snapshot", "settlement_type", "amount", "status", "created_at"),
                           [("created_at", -1), ("id", -1)], offset)
    if page_id == "shipping.settings":
        from auth import DEFAULT_SHIPPING_COMPANIES

        settings = await db.settings.find_one(tenant, _projection(("shipping_companies",)))
        companies = (settings or {}).get("shipping_companies", DEFAULT_SHIPPING_COMPANIES) or []
        return [{
            "name": row.get("name"), "cost_per_order": row.get("cost_per_order"), "vat_percent": row.get("vat_percent"),
            "payment_mode": "deferred" if row.get("is_deferred") else "prepaid",
            "cod_fee_fixed_per_order": row.get("cod_fee_fixed_per_order"),
        } for row in companies[offset:offset + PAGE_SIZE + 1] if isinstance(row, dict)]
    # The migration audit is explicitly opened by its own page grant. Use the
    # same authoritative comparison as the owner's report, never on staff home.
    from employees_v2_routes import _preview_from_db

    report = await _preview_from_db(db, owner_id)
    return [{
        "id": row.get("employee_id"), "name": row.get("name"), "status": row.get("migration_status"),
        "salary": (row.get("salary_contract") or {}).get("monthly_amount"),
        "due": (row.get("financial_snapshot") or {}).get("salary_payable"),
    } for row in report["employees"][offset:offset + PAGE_SIZE + 1]]


async def _read_tenant(db, actor, page_id):
    from web_page_access import delegated_page_owner

    if page_id not in PAGES:
        raise HTTPException(404)
    tenant = await delegated_page_owner(db, actor, page_id)
    if not tenant or NATIVE_DENIAL.get(page_id, "") in (actor.get("denied_permissions") or []):
        raise HTTPException(403, detail={"code": "web_page_permission_required"})
    return tenant


def _page_result(rows, page, response):
    response.headers["Cache-Control"] = "private, no-store"
    return {"items": rows[:PAGE_SIZE], "page": page, "has_next": len(rows) > PAGE_SIZE}


def make_web_employee_tools_router(db, current_user):
    router = APIRouter(prefix="/employee-tools")

    @router.get("/{page_id}")
    async def read_page(page_id: str, response: Response, page: int = Query(1, ge=1, le=10000), actor: dict = Depends(current_user)):
        owner_id = await _read_tenant(db, actor, page_id)
        rows = await _page_rows(db, page_id, owner_id, (page - 1) * PAGE_SIZE)
        return _page_result(rows, page, response)

    @router.get("/employees.staff/{employee_id}/events")
    async def events(employee_id: str, response: Response, page: int = Query(1, ge=1, le=10000), actor: dict = Depends(current_user)):
        from employees_v2_routes import EMPLOYEES, EMPLOYEE_EVENTS

        owner_id = await _read_tenant(db, actor, "employees.staff")
        if not await db[EMPLOYEES].find_one({"user_id": owner_id, "id": employee_id}, _projection(("id",))):
            raise HTTPException(404, detail={"code": "employee_not_found"})
        rows = await _rows(db[EMPLOYEE_EVENTS], {"user_id": owner_id, "employee_id": employee_id},
                           ("id", "event_type", "actor_name", "occurred_at"), [("occurred_at", -1), ("id", -1)],
                           (page - 1) * PAGE_SIZE)
        return _page_result(rows, page, response)

    return router
