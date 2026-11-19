import json

import pytest
from fastapi import HTTPException
from mongomock_motor import AsyncMongoMockClient

from test_web_page_access import GRANTS, actor, build_client, save, store
from web_legacy_config_read import CONFIG_PAGES, read_config_page


@pytest.fixture
def db():
    return AsyncMongoMockClient().read_config_tests


def text(report):
    return json.dumps(report, ensure_ascii=False)


def reader(db, path, offset=0):
    return read_config_page(db, "merchant", {"id": "staff", "created_by": "merchant"}, path, offset)


@pytest.mark.asyncio
async def test_tenant_and_explicit_projection_on_all_singleton_configurations(db):
    fixtures = [
        ("webhook_tokens", "/make-webhook", {"created_at": "correct", "token": "SECRET"}),
        ("settings", "/integrations/custom-app", {"custom_app": {"created_at": "correct", "api_key": "SECRET"}}),
        ("salla_integrations", "/settings/salla", {"store_name": "correct", "access_token_encrypted": "SECRET"}),
        ("settings", "/settings", {"payment_methods": [{"name": "correct", "api_key": "SECRET"}]}),
    ]
    for collection, path, item in fixtures:
        await db[collection].delete_many({})
        await db[collection].insert_many([{"user_id": "foreign", **item, "store_name": "foreign"},
                                          {"user_id": "merchant", **item}])
        report = text(await reader(db, path))
        assert "correct" in report and "SECRET" not in report and "foreign" not in report, (path, report)


@pytest.mark.asyncio
@pytest.mark.parametrize(("path", "collection", "owner_key", "field", "extra"), [
    ("/integrations/bnpl", "bnpl_settings", "user_id", "activation_date", {"provider": "tabby"}),
    ("/ads-v2/settings", "ads_accounts", "user_id", "display_name", {"soft_deleted": False}),
    ("/team", "users", "created_by", "name", {}),
    ("/diagnostics/api-permissions", "mezan_integration_permissions_v2", "user_id", "permission_key", {"provider": "meta_ads"}),
])
async def test_config_list_paginates_eight_and_filters_merchant(db, path, collection, owner_key, field, extra):
    for i in range(11):
        await db[collection].insert_one({**extra, owner_key: "foreign" if i == 10 else "merchant",
                                        field: f"row-{i}", "token": "SECRET", "raw_response": {"key": "SECRET"}})
    first = await reader(db, path)
    second = await reader(db, path, 8)
    assert len(first["rows"]) == 8 and len(second["rows"]) == 2 and first["has_more"] and not second["has_more"]
    assert "row-10" not in text([first, second]) and "SECRET" not in text([first, second])


@pytest.mark.asyncio
async def test_first_read_does_not_generate_token_or_persist_cutoff_defaults(db):
    for path in CONFIG_PAGES:
        await reader(db, path)
    assert await db.list_collection_names() == []
    await db.accounting_cutoffs.insert_one({"user_id": "merchant", "provider": "tabby", "accounting_start_date": "2026-10-03"})
    assert "2026-10-03" in text(await reader(db, "/settings/accounting-cutoffs"))
    assert await db.accounting_cutoffs.count_documents({}) == 1
    await db.settings.insert_one({"user_id": "merchant", "operation_account_bindings": {"supplier_pay": ["__none__"]}})
    operations = await reader(db, "/settings/operation-account-bindings")
    assert next(r for r in operations["rows"] if r["operation"] == "سداد مورد")["accounts"] == "0"


@pytest.mark.asyncio
async def test_qoyod_views_pin_shared_main_and_saved_generation_to_the_employee_merchant(db):
    root = "/integrations-v2/qoyod"
    await db.qoyod_settings.insert_one({"user_id": "main", "security_owner_id": "foreign", "api_key": "SECRET",
                                       "invoice_date_source": "send_date", "auto_send": True})
    await db.qoyod_invoices.insert_many([{"user_id": "main", "qoyod_invoice_id": 992, "invoice_number": "inv-visible",
        "issue_date": "2026-11-19", "raw_response": {"key": "SECRET"}},
        {"user_id": "main", "qoyod_invoice_id": "DRY:fake", "invoice_number": "fake"}])
    for suffix in ("", "?tab=overview", "?tab=invoices", "?tab=settings", "?tab=exceptions", "?tab=reconciliation"):
        assert (await reader(db, root + suffix))["rows"] == []
    await db.qoyod_settings.update_one({"user_id": "main"}, {"$set": {"security_owner_id": "merchant"}})
    assert "inv-visible" in text(await reader(db, root + "?tab=invoices"))
    assert "fake" not in text(await reader(db, root + "?tab=invoices"))
    assert "send_date" in text(await reader(db, root + "?tab=settings"))
    assert "send_date" not in text(await reader(db, root))
    await db.qoyod_full_scans.insert_one({"_id": "main", "last_success": {
        "orders_user_id": "foreign", "generation": "current", "report": {"rows": [{"order_number": "match"}]}}})
    await db.qoyod_scan_views.insert_many([
        {"tenant": "main", "generation": "current", "offset": 0, "rows": [{"order_number": "mine", "reason": "مراجعة", "internal": "SECRET"}]},
        {"tenant": "main", "generation": "old", "offset": 0, "rows": [{"order_number": "stale"}]},
    ])
    assert (await reader(db, root + "?tab=exceptions"))["rows"] == []
    assert (await reader(db, root + "?tab=reconciliation"))["rows"] == []
    await db.qoyod_full_scans.update_one({"_id": "main"}, {"$set": {"last_success.orders_user_id": "merchant"}})
    exc = text(await reader(db, root + "?tab=exceptions"))
    assert "mine" in exc and "SECRET" not in exc and "stale" not in exc
    assert (await reader(db, root + "?tab=reconciliation"))["rows"][0]["order_number"] == "match"
    with pytest.raises(HTTPException):
        await reader(db, "/integrations-v2/qoyod?tab=send")


@pytest.mark.asyncio
async def test_qoyod_native_tabs_have_independent_employee_grants(store):
    root = "legacy.integrations_v2.qoyod"
    route = f"{GRANTS}/legacy-read/{root}"
    path = "/integrations-v2/qoyod"
    staff = actor("employee")
    async with await build_client(store) as client:
        await store.qoyod_settings.insert_one({"user_id": "main", "security_owner_id": "owner",
                                               "invoice_date_source": "send_date", "api_key": "SECRET"})
        await store.qoyod_invoices.insert_one({"user_id": "main", "qoyod_invoice_id": 34,
                                               "issue_date": "2026-11-19", "invoice_number": "inv-34"})
        catalog = (await client.get(GRANTS, headers=actor("owner"))).json()["pages"]
        tabs = {page["id"]: page for page in catalog if page["id"].startswith(root)}
        assert tabs[root]["to"] == path and path + "?tab=overview" in tabs[root]["aliases"]
        for tab in ("invoices", "settings", "exceptions", "reconciliation"):
            assert tabs[f"{root}.{tab}"]["to"] == f"{path}?tab={tab}"
        saved = await save(client, {root + ".invoices": "read"})
        assert saved.status_code == 200, saved.text
        invoice = await client.get(route + ".invoices", headers=staff)
        assert invoice.status_code == 200 and "inv-34" in invoice.text
        for suffix in ("", ".settings", ".exceptions", ".reconciliation"):
            assert (await client.get(route + suffix, headers=staff)).status_code == 403
        saved = await save(client, {root + ".settings": "read"}, revision=1)
        assert saved.status_code == 200, saved.text
        settings = await client.get(route + ".settings", headers=staff)
        assert settings.status_code == 200 and "send_date" in settings.text and "SECRET" not in settings.text
        assert (await client.get(route + ".invoices", headers=staff)).status_code == 403
        # The second merchant can grant the same renderer, but has no access to this integration.
        saved = await save(client, {root + ".invoices": "read"}, member="outsider", by="other-owner")
        assert saved.status_code == 200, saved.text
        other = await client.get(route + ".invoices", headers=actor("outsider"))
        assert other.status_code == 200 and other.json()["rows"] == []
