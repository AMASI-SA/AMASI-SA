"""Production-like UAT boundaries against a disposable local Mongo replica only."""
from copy import deepcopy
import pytest
from tests.test_accounting_onboarding import api, request, pause, fingerprint, action, CREATE
from tests.test_financial_accounts_real_mongo import mongo_db, OWNER


async def seed_catalog(api):
    await api.db.mezan_products_v2.insert_many([
        {"user_id": OWNER, "mezan_product_id": "v2-abaya", "name": "عباية", "sku": "ABAYA", "barcode": "123", "main_image": "https://catalog.example/abaya.jpg", "options": [{"id": "color", "name": "اللون", "values": [{"id": "black", "name": "أسود"}, {"id": "beige", "name": "بيج"}]}], "variants": [{"id": "black54", "sku": "BLACK54", "selections": [{"name": "اللون", "value": "أسود"}, {"name": "المقاس", "value": "54"}]}, {"id": "beige56", "sku": "BEIGE56"}]},
        {"user_id": "foreign-owner", "mezan_product_id": "foreign-product", "name": "Foreign"},
    ])
    await api.db.products.insert_one({"user_id": OWNER, "id": "legacy-product", "name": "Legacy-only"})
    await api.db.mezan_cost_resources_v2.insert_many([
        {"user_id": OWNER, "id": "v2-fabric", "name": "قماش", "code": "FABRIC", "unit": "meter", "kind": "stock_component", "track_inventory": True, "category_ids": ["fabric"]},
        {"user_id": OWNER, "id": "service", "kind": "service", "track_inventory": True},
    ])
    await api.db.components.insert_one({"user_id": OWNER, "id": "legacy-component"})
    await api.db.mezan_component_categories_v2.insert_one({"user_id": OWNER, "id": "fabric", "name": "أقمشة"})
    await api.db.warehouse_locations_warehouses.insert_one({"user_id": OWNER, "id": "warehouse", "name": "مستودع", "status": "active"})
    await api.db.warehouse_locations.insert_one({"user_id": OWNER, "id": "bin", "warehouse_id": "warehouse", "code": "A1"})


def inventory_rows():
    return [{"item_type": "PRODUCT", "product_id": "v2-abaya", "variant_id": variant, "selected_options": {}, "opening_quantity": qty, "opening_unit_cost": "10.00", "opening_total_cost": total, "allocations": []} for variant, qty, total in [("black54", "2", "20.00"), ("beige56", "3", "30.00")]]


def setup_payload(row, rows, key="inventory-setup-save-001"):
    return {"version": row["version"], "idempotency_key": key, "setup_draft": {"active_stage": "inventory", "sections": {"inventory": {"rows": rows}}}}


async def indexes(api):
    return {name: await api.db[name].index_information() for name in await api.db.list_collection_names()}


@pytest.mark.asyncio
async def test_readonly_catalogs_have_zero_document_or_index_mutations(api):
    await seed_catalog(api)
    await pause(api)
    before = await fingerprint(api, exclude=())
    before_indexes = await indexes(api)
    catalog = await request(api, "GET", "/inventory-catalog")
    assert catalog["read_only"] is True
    assert [p["id"] for p in catalog["products"]] == ["v2-abaya"]
    assert catalog["products"][0]["main_image"] == "https://catalog.example/abaya.jpg"
    assert [v["id"] for v in catalog["products"][0]["variants"]] == ["black54", "beige56"]
    assert [c["id"] for c in catalog["components"]] == ["v2-fabric"]
    assert catalog["locations"][0]["id"] == "bin"
    await request(api, "GET", "/source-gaps")
    await request(api, "GET", "/recurring-obligations")
    assert await fingerprint(api, exclude=()) == before
    assert await indexes(api) == before_indexes


@pytest.mark.asyncio
async def test_two_catalog_variants_roundtrip_as_separate_setup_facts_without_physical_approval(api):
    await seed_catalog(api)
    await pause(api)
    row = await request(api, "POST", "/sessions", CREATE)
    before = await fingerprint(api)
    rows = inventory_rows()
    saved = await request(api, "PUT", f"/sessions/{row['id']}/setup-draft", setup_payload(row, rows))
    assert saved["version"] == row["version"] + 1
    resumed = await request(api, "GET", f"/sessions/{row['id']}")
    assert resumed["setup_draft"]["sections"]["inventory"]["rows"] == rows
    assert resumed["setup_draft"]["active_stage"] == "inventory"
    assert resumed["sections"]["inventory"]["status"] == "incomplete"
    assert not resumed.get("inventory_physical_approval_verified", False)
    assert await fingerprint(api) == before
    # Navigation-only save must preserve the same facts and keep preview pending.
    payload = setup_payload(resumed, rows, "inventory-next-stage-001")
    payload["setup_draft"]["active_stage"] = "payment_fees"
    saved = await request(api, "PUT", f"/sessions/{row['id']}/setup-draft", payload)
    assert saved["setup_draft"]["sections"]["inventory"]["rows"] == rows
    assert saved["preview"] is None
    assert await fingerprint(api) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("patch", [{"product_id": "legacy-product"}, {"product_id": "foreign-product"}, {"variant_id": "invented-variant"}, {"selected_options": {"color": "invented-option"}}, {"item_type": "STOCK_COMPONENT", "resource_id": "legacy-component", "product_id": "", "variant_id": ""}])
async def test_setup_rejects_legacy_foreign_and_fabricated_inventory_ids_atomically(api, patch):
    await seed_catalog(api)
    await pause(api)
    row = await request(api, "POST", "/sessions", CREATE)
    before = await fingerprint(api, exclude=())
    changed = {**inventory_rows()[0], **patch}
    denied = await request(api, "PUT", f"/sessions/{row['id']}/setup-draft", setup_payload(row, [changed]), status=422)
    assert denied["detail"]["code"] == "onboarding_inventory_identity_invalid"
    assert denied["detail"]["stage"] == 10
    assert await fingerprint(api, exclude=()) == before


@pytest.mark.asyncio
async def test_readiness_is_diagnostic_and_pause_prevents_handoff_before_session_load(api):
    await pause(api)
    row = await request(api, "POST", "/sessions", CREATE)
    before = await fingerprint(api, exclude=())
    ready = await request(api, "GET", f"/sessions/{row['id']}/readiness")
    assert ready["financial_writes_paused"] is True
    assert ready["ready_for_live_post"] is False
    assert ready["p02_activation_allowed"] is False
    assert ready["g47_activation_allowed"] is False
    assert ready["blockers"]
    for blocker in ready["blockers"]:
        assert blocker["code"] and blocker["stage"]
        assert blocker["message_ar"] and blocker["corrective_action"]
        assert blocker["message_ar"] != "تعذر إكمال الطلب"
    assert "mz2_writes_paused" in {b["code"] for b in ready["blockers"]}
    denied = await action(api, {"id": "guaranteed-nonexistent-uat-session", "version": 1}, "opening-draft", status=423)
    assert denied["detail"]["code"] == "mz2_writes_paused"
    assert await fingerprint(api, exclude=()) == before
    for name in ("mz2_opening_balance_drafts", "mz2_journals", "mz2_ledger_entries", "general_ledger", "liabilities"):
        assert await api.db[name].count_documents({}) == 0
