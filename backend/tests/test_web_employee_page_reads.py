import pytest
from fastapi import FastAPI, HTTPException, Request
from httpx import ASGITransport, AsyncClient
from mongomock_motor import AsyncMongoMockClient

from web_employee_page_policy import enforce_web_employee_tool_boundary, make_web_employee_tools_router

STAFF = "employees.staff"
DRIVERS = "employees.drivers"
COLLECTIONS = "finance.collections"
SETTLEMENTS = "finance.settlements"
SHIPPING = "shipping.settings"
MIGRATION = "employees.migration"


def managed(grants=(), **changes):
    return {
        "id": "staff", "role": "admin", "created_by": "merchant",
        "web_page_access": {"owner_id": "merchant", "grants": [{"id": key, "mode": "read"} for key in grants]},
        **changes,
    }


def app_for(db, user):
    async def auth(request: Request):
        from web_page_access import enforce_web_catalog_access

        enforce_web_catalog_access(user, request.url.path, request.method, request.query_params)
        return user

    app = FastAPI()
    app.include_router(make_web_employee_tools_router(db, auth), prefix="/api/web-page-access-v1")
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://tests/api/web-page-access-v1/")


@pytest.mark.asyncio
async def test_scoped_pages_never_share_salary_accounts_or_unfiltered_settings():
    db = AsyncMongoMockClient().db
    await db.users.insert_one({"id": "merchant", "role": "owner"})
    for owner in ("merchant", "other"):
        await db.mezan_employees_v2.insert_many([
            {"user_id": owner, "id": f"{owner}-{index}", "display_name": f"{owner} {index}",
             "status": "active", "private_notes": "private", "account_user_id": "account"}
            for index in range(10)])
        await db.mezan_employee_events_v2.insert_one({"user_id": owner, "employee_id": f"{owner}-0", "id": owner,
            "event_type": "employee_salary_changed", "before": {"salary": 999}, "after": {"salary": 1000}})
        await db.settings.insert_one({"user_id": owner, "api_key": "private", "shipping_companies": [
            {"name": owner, "cost_per_order": 11, "token": "secret", "is_deferred": True}]})
    async with app_for(db, managed([STAFF, SHIPPING])) as client:
        report = await client.get("employee-tools/employees.staff")
        assert report.status_code == 200
        data = report.json()
        assert data["has_next"] and len(data["items"]) == 8
        assert all(row["id"].startswith("merchant-") and set(row) <= {"id", "display_name", "status"} for row in data["items"])
        second = (await client.get("employee-tools/employees.staff?page=2")).json()
        assert not second["has_next"] and len(second["items"]) == 2
        assert not set(row["id"] for row in data["items"]) & set(row["id"] for row in second["items"])
        events = (await client.get("employee-tools/employees.staff/merchant-0/events")).json()["items"]
        assert events == [{"id": "merchant", "event_type": "employee_salary_changed"}]
        assert (await client.get("employee-tools/employees.staff/other-0/events")).status_code == 404
        assert (await client.get("employee-tools/employees.migration")).status_code == 403
        shipping = (await client.get("employee-tools/shipping.settings")).json()["items"]
        assert shipping == [{"name": "merchant", "cost_per_order": 11, "vat_percent": None,
                             "payment_mode": "deferred", "cod_fee_fixed_per_order": None}]


@pytest.mark.asyncio
async def test_collections_join_is_tenant_scoped_and_actions_are_not_granted():
    db = AsyncMongoMockClient().db
    await db.users.insert_one({"id": "merchant", "role": "owner"})
    for owner in ("other", "merchant"):
        await db.store_drivers.insert_one({"user_id": owner, "id": "driver", "name": owner, "login_email": "private"})
        await db.store_delivery_assignments.insert_one({"user_id": owner, "id": "asn", "order_number": owner})
        await db.store_delivery_payment_reviews.insert_one({"user_id": owner, "assignment_id": "asn", "driver_id": "driver",
            "status": "pending", "amount": 25, "payment_method": "bank_transfer", "receipt_url": "private"})
        await db.store_delivery_driver_settlements.insert_one({"user_id": owner, "id": owner, "amount": 25,
            "driver_name_snapshot": owner, "private_note": "private"})
    async with app_for(db, managed([COLLECTIONS])) as client:
        result = (await client.get("employee-tools/finance.collections")).json()["items"]
        assert result == [{"id": "asn", "order": "merchant", "driver": "merchant", "amount": 25, "method": "bank_transfer", "date": None}]
        assert (await client.get("employee-tools/finance.settlements")).status_code == 403
        assert (await client.post("employee-tools/finance.collections")).status_code == 405
    async with app_for(db, managed([DRIVERS, SETTLEMENTS])) as client:
        assert (await client.get("employee-tools/employees.drivers")).json()["items"] == [{"id": "driver", "name": "merchant"}]
        assert (await client.get("employee-tools/finance.settlements")).json()["items"] == [{"id": "merchant", "driver_name_snapshot": "merchant", "amount": 25}]
        assert (await client.get("employee-tools/finance.collections")).status_code == 403
    async with app_for(db, managed([COLLECTIONS], denied_permissions=["store_delivery.payments.review"])) as client:
        assert (await client.get("employee-tools/finance.collections")).status_code == 403
    await db.users.update_one({"id": "merchant"}, {"$set": {"disabled": True}})
    async with app_for(db, managed([COLLECTIONS])) as client:
        assert (await client.get("employee-tools/finance.collections")).status_code == 403


@pytest.mark.parametrize("path,method", [
    ("/api/employees-v2/management", "GET"), ("/api/settings", "GET"), ("/api/settings", "PUT"),
    ("/api/store-delivery/drivers/driver/account/password", "PUT"),
    ("/api/store-delivery/settlements/driver/test/net-settlement", "POST"),
    ("/api/store-delivery/payment-review/asn", "POST"), ("/api/store-delivery/payment-review/pending", "GET"),
])
def test_browser_read_grants_cannot_inherit_privileged_legacy_actions(path, method):
    with pytest.raises(HTTPException) as denied:
        enforce_web_employee_tool_boundary(managed([STAFF, DRIVERS, COLLECTIONS, SETTLEMENTS, SHIPPING]), path, method)
    assert denied.value.status_code == 403
    enforce_web_employee_tool_boundary(managed([STAFF], _session_client="amasi_mobile"), path, method)
    enforce_web_employee_tool_boundary({"id": "merchant", "role": "owner"}, path, method)


@pytest.mark.asyncio
async def test_migration_report_is_separate_from_directory_and_projects_the_existing_comparison(monkeypatch):
    db = AsyncMongoMockClient().db
    await db.users.insert_one({"id": "merchant", "role": "owner"})

    async def audit(source, tenant):
        assert source is db and tenant == "merchant"
        return {"employees": [{"employee_id": "e", "name": "منال", "migration_status": "already_migrated",
            "salary_contract": {"monthly_amount": 1200}, "financial_snapshot": {"salary_payable": 400},
            "account": {"email": "private"}, "blockers": []}]}

    monkeypatch.setattr("employees_v2_routes._preview_from_db", audit)
    async with app_for(db, managed([MIGRATION])) as client:
        report = await client.get("employee-tools/employees.migration")
        assert report.status_code == 200
        assert report.json()["items"] == [{"id": "e", "name": "منال", "status": "already_migrated", "salary": 1200, "due": 400}]
        assert (await client.get("employee-tools/employees.staff")).status_code == 403
