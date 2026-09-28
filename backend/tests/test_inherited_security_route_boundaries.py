"""In-memory ASGI coverage for inherited diagnostic and product boundaries."""
from __future__ import annotations

import io
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from fastapi import APIRouter, FastAPI
from httpx import ASGITransport, AsyncClient
from mongomock_motor import AsyncMongoMockClient
from openpyxl import Workbook

import audit_routes
import auth
import custom_app_routes
import meta_routes
import product_costs
import product_v2_workspace_routes as workspace
from ai_store_access_contract import ROLE_ASSIGNMENTS, ROLE_ASSIGNMENT_OWNER_FIELD
from bnpl import balance_service, routes as bnpl_routes
from bnpl.clients.tabby import TabbyError
from salla_integration.service import SallaError


PRIVATE = "private-host-a8 /srv/private/key.pem opaque-provider-credential-a8"
OWNER = "owner-a8"
OTHER_OWNER = "other-owner-a8"
EMPLOYEE = "employee-a8"


class BoundaryProxy:
    """Override a boundary without relying on mongomock wrapper identity."""
    def __init__(self, wrapped, **overrides):
        self.wrapped = wrapped
        self.overrides = overrides

    def __getattr__(self, name):
        if name in self.overrides:
            return self.overrides[name]
        return getattr(self.wrapped, name)

    def __getitem__(self, name):
        if name in self.overrides:
            return self.overrides[name]
        return self.wrapped[name]


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def reject(*_args, **_kwargs):
        raise AssertionError("Live network is forbidden in boundary tests")

    async def async_reject(*_args, **_kwargs):
        reject()

    # Windows asyncio needs a local socket pair; block provider transports.
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", reject)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", async_reject)


@pytest.fixture
def db():
    return AsyncMongoMockClient()["inherited_route_security"]


async def actor(db, kind="owner", permission=None):
    await db.users.insert_many([
        {"id": OWNER, "role": "owner", "is_active": True},
        {"id": OTHER_OWNER, "role": "owner", "is_active": True},
        {"id": EMPLOYEE, "role": "employee", "created_by": OWNER,
         "is_active": True},
    ])
    if kind == "owner":
        return {"id": OWNER, "role": "owner"}
    if kind == "disabled_owner":
        await db.users.update_one({"id": OWNER}, {"$set": {"disabled": True}})
        return {"id": OWNER, "role": "owner"}
    if kind == "stale_owner":
        await db.users.update_one({"id": OWNER}, {"$set": {"role": "employee"}})
        return {"id": OWNER, "role": "owner"}
    if permission:
        await db[ROLE_ASSIGNMENTS].insert_one({
            ROLE_ASSIGNMENT_OWNER_FIELD: OTHER_OWNER if kind == "foreign_grant" else OWNER,
            "user_id": EMPLOYEE,
            "enabled": True,
            "extra_permissions": [permission],
        })
    if kind == "direct_employee":
        return {"id": EMPLOYEE, "role": "employee", "created_by": OWNER}
    # Native sessions can carry the merchant's shape; authority is the actor.
    return {"id": OWNER, "role": "owner", "_mobile_actor_id": EMPLOYEE}


def dependency(user):
    async def current_user():
        return user

    return current_user


def app_for(router):
    app = FastAPI()
    app.include_router(router, prefix="/api")
    return app


async def request(app, method, path, **kwargs):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://test.example") as client:
        return await client.request(method, path, **kwargs)


def assert_private_absent(response):
    for marker in PRIVATE.split():
        assert marker not in response.text


def workbook_bytes():
    workbook = Workbook()
    workbook.active.append(["sku", "product_name", "cost_price"])
    workbook.active.append(["SKU-A8", "Synthetic product", 12])
    content = io.BytesIO()
    workbook.save(content)
    workbook.close()
    return content.getvalue()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["employee", "stale_owner", "disabled_owner"])
async def test_tabby_audit_rejects_before_financial_reads(db, monkeypatch, kind):
    user = await actor(db, kind)
    reads = Mock(side_effect=AssertionError("Audit read before owner check"))
    route_db = BoundaryProxy(db, accounts=SimpleNamespace(find=reads))
    provider = AsyncMock(side_effect=RuntimeError(PRIVATE))
    monkeypatch.setattr(balance_service, "get_bnpl_provider_balance", provider)
    app = app_for(audit_routes.make_tabby_phase2_router(route_db, dependency(user)))

    response = await request(app, "GET", "/api/audit/tabby-phase2")

    assert response.status_code == 403
    reads.assert_not_called()
    provider.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("failed", [False, True])
async def test_tabby_audit_owner_is_scoped_and_balance_fault_is_public(db, monkeypatch, failed):
    user = await actor(db)
    await db.payment_transactions.insert_many([
        {"user_id": OWNER, "provider": "tabby", "amount": 12},
        {"user_id": OTHER_OWNER, "provider": "tabby", "amount": 999},
    ])
    provider = AsyncMock(return_value={"balance": 12, "components": {"sales": 12}})
    if failed:
        provider.side_effect = RuntimeError(PRIVATE)
    monkeypatch.setattr(balance_service, "get_bnpl_provider_balance", provider)
    app = app_for(audit_routes.make_tabby_phase2_router(db, dependency(user)))

    response = await request(app, "GET", "/api/audit/tabby-phase2")

    assert response.status_code == 200
    provider.assert_awaited_once_with(db, OWNER, "tabby")
    assert response.json()["payment_transactions"]["total_sales"] == 12
    expected = {"error": "diagnostic_failed"} if failed else {"sales": 12}
    assert response.json()["bnpl_ssot"]["components"] == expected
    assert_private_absent(response)


@pytest.mark.asyncio
async def test_custom_order_validation_has_stable_public_code(db, monkeypatch):
    app, jwt_auth = await custom_app(db, monkeypatch)
    upsert = AsyncMock()
    monkeypatch.setattr(custom_app_routes, "upsert_order", upsert)
    response = await request(app, "POST", "/api/integrations/custom-app/orders",
                             headers={"X-API-Key": "mzn_other_a8"}, json={"orders": PRIVATE})
    assert response.status_code == 400
    assert response.json() == {"detail": "invalid_order_payload"}
    upsert.assert_not_awaited()
    jwt_auth.assert_not_awaited()
    assert_private_absent(response)


@pytest.mark.asyncio
async def test_import_dependency_failure_has_stable_public_code(db, monkeypatch):
    import builtins
    user = await actor(db)
    app = app_for(product_costs._build_router(db, dependency(user)))
    payload = workbook_bytes()
    original_import = builtins.__import__

    def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "openpyxl":
            raise ImportError(PRIVATE)
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    response = await request(app, "POST", "/api/product-costs/import",
                             files={"file": ("test.xlsx", payload)})
    assert response.status_code == 500
    assert response.json() == {"detail": "import_unavailable"}
    assert_private_absent(response)
    assert await db.product_costs.count_documents({}) == 0



async def custom_app(db, monkeypatch):
    await actor(db)
    await db.settings.insert_many([
        {"user_id": OWNER, "custom_app": {"api_key": "mzn_synthetic_a8", "enabled": True}},
        {"user_id": OTHER_OWNER, "custom_app": {"api_key": "mzn_other_a8", "enabled": True}},
        {"user_id": "revoked-a8", "custom_app": {"api_key": "mzn_revoked_a8", "enabled": False}},
    ])
    jwt_auth = AsyncMock(side_effect=AssertionError("API-key orders must not require JWT"))
    monkeypatch.setattr(custom_app_routes, "get_current_user_from_db", jwt_auth)
    router = APIRouter()
    custom_app_routes.attach_custom_app_routes(router, db)
    return app_for(router), jwt_auth


@pytest.mark.asyncio
@pytest.mark.parametrize("headers", [{}, {"X-API-Key": "mzn_invalid_a8"}, {"X-API-Key": "mzn_revoked_a8"}])
async def test_custom_orders_reject_invalid_capabilities(db, monkeypatch, headers):
    app, jwt_auth = await custom_app(db, monkeypatch)
    upsert = AsyncMock(side_effect=AssertionError("Invalid key reached ingestion"))
    monkeypatch.setattr(custom_app_routes, "upsert_order", upsert)

    response = await request(app, "POST", "/api/integrations/custom-app/orders", headers=headers,
                             json={"order_number": "order-a8"})

    assert response.status_code == 401
    upsert.assert_not_awaited()
    jwt_auth.assert_not_awaited()
    assert await db.integration_events.count_documents({}) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("failed", [False, True])
@pytest.mark.parametrize("bearer", [False, True])
async def test_custom_orders_keep_key_capability_and_tenant_with_safe_error(db, monkeypatch, failed, bearer):
    app, jwt_auth = await custom_app(db, monkeypatch)
    upsert = AsyncMock(return_value={"created": True})
    if failed:
        upsert.side_effect = RuntimeError(PRIVATE)
    monkeypatch.setattr(custom_app_routes, "upsert_order", upsert)
    headers = {"Authorization": "Bearer mzn_other_a8"} if bearer else {"X-API-Key": "mzn_other_a8"}

    response = await request(app, "POST", "/api/integrations/custom-app/orders", headers=headers,
                             json={"order_number": "order-a8", "user_id": OWNER})

    assert response.status_code == 200
    jwt_auth.assert_not_awaited()
    assert upsert.await_count == 1
    assert upsert.await_args.args[1:3] == (OTHER_OWNER, "order-a8")
    result = response.json()["results"][0]
    assert result["ok"] is not failed
    if failed:
        assert result["error"] == "order_ingest_failed"
    assert await db.integration_events.count_documents({"user_id": OTHER_OWNER}) == 1
    assert await db.integration_events.count_documents({"user_id": OWNER}) == 0
    assert_private_absent(response)


def meta_app(db, monkeypatch, user):
    monkeypatch.setattr(auth, "get_current_user_from_db", AsyncMock(return_value=user))
    router = APIRouter()
    meta_routes.attach_meta_routes(router, db)
    return app_for(router)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["employee", "stale_owner", "disabled_owner"])
async def test_meta_billing_rejects_before_credentials(db, monkeypatch, kind):
    user = await actor(db, kind)
    read = AsyncMock(side_effect=AssertionError("Credentials read before owner check"))
    route_db = BoundaryProxy(db, meta_connections=SimpleNamespace(find_one=read))
    app = meta_app(route_db, monkeypatch, user)

    response = await request(app, "GET", "/api/meta/diagnose-billing-permissions")

    assert response.status_code == 403
    read.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("failed_probe", ["debug_token", "adaccounts", "billing"])
@pytest.mark.parametrize("failure", ["http", "exception"])
async def test_meta_billing_owner_provider_errors_are_fixed(db, monkeypatch, failed_probe, failure):
    app = meta_app(db, monkeypatch, await actor(db))
    await db.meta_connections.insert_many([
        {"user_id": OWNER, "access_token": "synthetic-owner-token"},
        {"user_id": OTHER_OWNER, "access_token": "other-owner-token"},
    ])

    async def get(url, *, params):
        assert params["access_token"] == "synthetic-owner-token"
        probe = "debug_token" if url.endswith("debug_token") else "adaccounts" if url.endswith("adaccounts") else "billing"
        if probe == failed_probe:
            if failure == "exception":
                raise RuntimeError(PRIVATE)
            return httpx.Response(403, text=PRIVATE)
        payload = {"data": {"scopes": ["ads_read"]}} if probe == "debug_token" else {"data": [{"id": "act_synthetic"}]}
        return httpx.Response(200, json=payload)

    provider = AsyncMock(side_effect=get)
    context = AsyncMock()
    context.__aenter__.return_value = SimpleNamespace(get=provider)
    monkeypatch.setattr(meta_routes.httpx, "AsyncClient", Mock(return_value=context))

    response = await request(app, "GET", "/api/meta/diagnose-billing-permissions")

    assert response.status_code == 200
    checks = response.json()["checks"]
    failed_checks = [check for check in checks if check["ok"] is False]
    assert len(failed_checks) == 1
    key = "status" if failure == "exception" else "detail"
    assert failed_checks[0][key] == ("diagnostic_failed" if failure == "exception" else "provider_operation_failed")
    assert provider.await_count >= 2
    assert_private_absent(response)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["employee", "foreign_grant", "wrong_permission", "stale_owner", "disabled_owner"])
async def test_product_import_denies_before_file_processing(db, monkeypatch, kind):
    permission = {"foreign_grant": "products.cost.write", "wrong_permission": "products.publish"}.get(kind)
    user = await actor(db, kind, permission)
    reader = AsyncMock(side_effect=AssertionError("File processed before permission"))
    monkeypatch.setattr(product_costs, "read_safe_xlsx_upload", reader)
    app = app_for(product_costs._build_router(db, dependency(user)))

    response = await request(app, "POST", "/api/product-costs/import",
                             files={"file": ("test.xlsx", b"not an xlsx")})

    assert response.status_code == 403
    reader.assert_not_awaited()
    assert await db.product_costs.count_documents({}) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["owner", "employee", "direct_employee"])
@pytest.mark.parametrize("failure", ["none", "parser", "row"])
async def test_product_import_permission_and_safe_faults(db, monkeypatch, kind, failure):
    user = await actor(db, kind, "products.cost.write")
    route_db = db
    await db.product_costs.insert_one({"user_id": OTHER_OWNER, "sku_normalized": "sku-a8", "cost_price": 777})
    monkeypatch.setattr(product_costs, "_reprocess_orders_for_keys", AsyncMock(return_value=0))
    recompute = AsyncMock(return_value=0)
    monkeypatch.setattr(product_costs, "_recompute_recent_orders", recompute)
    payload = workbook_bytes()
    if failure == "parser":
        monkeypatch.setattr("openpyxl.load_workbook", Mock(side_effect=ValueError(PRIVATE)))
    elif failure == "row":
        update = AsyncMock(side_effect=RuntimeError(PRIVATE))
        route_db = BoundaryProxy(db, product_costs=BoundaryProxy(db.product_costs, update_one=update))
    app = app_for(product_costs._build_router(route_db, dependency(user)))

    response = await request(app, "POST", "/api/product-costs/import",
                             files={"file": ("test.xlsx", payload)})

    if failure == "parser":
        assert response.status_code == 400
        assert response.json() == {"detail": "invalid_import_file"}
        recompute.assert_not_awaited()
    else:
        assert response.status_code == 200
        recompute.assert_awaited_once_with(route_db, OWNER, days=2)
        if failure == "row":
            assert response.json()["errors"] == [{"row": 2, "error": "import_row_failed"}]
            assert update.await_args.args[0]["user_id"] == OWNER
        else:
            assert response.json()["created"] == 1
            assert response.json()["errors"] == []
            row = await db.product_costs.find_one({"user_id": OWNER})
            assert row["cost_price"] == 12
    other = await db.product_costs.find_one({"user_id": OTHER_OWNER})
    assert other["cost_price"] == 777
    assert_private_absent(response)


async def sku_app(db, monkeypatch, kind, permission=None, failed=False):
    user = await actor(db, kind, permission)
    await db[workspace.PRODUCTS].insert_many([
        {"user_id": OWNER, "salla_product_id": "product-a8", "name": "Owned product"},
        {"user_id": OTHER_OWNER, "salla_product_id": "foreign-a8", "name": "Other product"},
    ])
    provider = AsyncMock(return_value={"success": True})
    if failed:
        provider.side_effect = SallaError(PRIVATE, needs_reauth=True, status_code=401)
    monkeypatch.setattr(workspace, "call_salla", provider)
    return app_for(workspace.make_product_v2_workspace_router(db, dependency(user))), provider


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["employee", "foreign_grant", "wrong_permission", "stale_owner", "disabled_owner"])
async def test_sku_apply_denies_before_sequence_or_provider(db, monkeypatch, kind):
    permission = {"foreign_grant": "products.publish", "wrong_permission": "products.cost.write"}.get(kind)
    app, provider = await sku_app(db, monkeypatch, kind, permission)
    reserve = AsyncMock(side_effect=AssertionError("Sequence reserved before permission"))
    monkeypatch.setattr(workspace, "_reserve_sku_number", reserve)

    response = await request(app, "POST", "/api/products-v2/workspace/sku/apply",
                             json={"confirmation": workspace.SKU_CONFIRMATION})

    assert response.status_code == 403
    reserve.assert_not_awaited()
    provider.assert_not_awaited()
    assert await db[workspace.SEQUENCES].count_documents({}) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["owner", "employee", "direct_employee"])
@pytest.mark.parametrize("failed", [False, True])
async def test_sku_apply_authorized_actor_keeps_tenant_and_safe_provider_error(db, monkeypatch, kind, failed):
    app, provider = await sku_app(db, monkeypatch, kind, "products.publish", failed)

    response = await request(app, "POST", "/api/products-v2/workspace/sku/apply",
                             json={"confirmation": workspace.SKU_CONFIRMATION})

    assert response.status_code == 200
    assert provider.await_count == 1
    assert provider.await_args.args == (db, OWNER, "PUT", "/products/product-a8")
    result = response.json()["results"][0]
    assert result["ok"] is not failed
    if failed:
        assert result["error"] == "provider_operation_failed"
        assert result["needs_reauth"] is True
    else:
        assert result["sku"] == "AMS00001"
        assert await db[workspace.WRITE_LOG].count_documents({"user_id": OWNER}) == 1
    foreign = await db[workspace.PRODUCTS].find_one({"user_id": OTHER_OWNER})
    assert not foreign.get("sku")
    assert await db[workspace.SEQUENCES].count_documents({"user_id": OTHER_OWNER}) == 0
    assert_private_absent(response)


def tabby_app(db, monkeypatch, user):
    monkeypatch.setattr(bnpl_routes, "get_current_user_from_db", AsyncMock(return_value=user))
    router = APIRouter()
    bnpl_routes.attach_bnpl_routes(router, db)
    return app_for(router)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["employee", "stale_owner", "disabled_owner"])
async def test_tabby_debug_denies_before_reading_credentials(db, monkeypatch, kind):
    app = tabby_app(db, monkeypatch, await actor(db, kind))
    secrets = AsyncMock(side_effect=AssertionError("Credentials read before owner check"))
    monkeypatch.setattr(bnpl_routes, "get_raw_secrets", secrets)
    factory = Mock(side_effect=AssertionError("Provider constructed before owner check"))
    monkeypatch.setattr(bnpl_routes, "TabbyClient", factory)

    response = await request(app, "POST", "/api/bnpl/tabby/debug")

    assert response.status_code == 403
    secrets.assert_not_awaited()
    factory.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["none", "initial", "date_filter"])
async def test_tabby_debug_owner_faults_and_date_probe_are_safe(db, monkeypatch, failure):
    app = tabby_app(db, monkeypatch, await actor(db))
    secrets = AsyncMock(return_value={"secret_key": "synthetic-key-a8", "merchant_code": "merchant-a8"})
    settings = AsyncMock(return_value={"secret_key_type": "live", "secret_key_masked": "masked",
                                      "activation_date": "2026-01-01"})
    monkeypatch.setattr(bnpl_routes, "get_raw_secrets", secrets)
    monkeypatch.setattr(bnpl_routes, "get_settings", settings)

    async def get(path, *, params):
        assert path == "/api/v2/payments"
        if failure == "initial" or (failure == "date_filter" and "created_at__gte" in params):
            raise TabbyError(403, PRIVATE)
        return {"payments": [], "pagination": {"total_count": 0}}

    provider = AsyncMock(side_effect=get)
    factory = Mock(return_value=SimpleNamespace(base_url="https://provider.example", _get=provider))
    monkeypatch.setattr(bnpl_routes, "TabbyClient", factory)

    response = await request(app, "POST", "/api/bnpl/tabby/debug")

    assert response.status_code == 200
    secrets.assert_awaited_once_with(db, OWNER, "tabby")
    assert all(call.args == (db, OWNER, "tabby") for call in settings.await_args_list)
    assert factory.call_args.kwargs["secret_key"] == "synthetic-key-a8"
    result = response.json()
    if failure == "initial":
        assert result["ok"] is False
        assert result["error"] == "provider_operation_failed"
        assert provider.await_count == 1
    elif failure == "date_filter":
        assert result["with_date_filter"] == {"error": "provider_operation_failed"}
        assert provider.await_count == 9
    else:
        assert result["ok"] is True
        assert result["with_date_filter"]["raw_count"] == 0
    assert "synthetic-key-a8" not in response.text
    assert_private_absent(response)
