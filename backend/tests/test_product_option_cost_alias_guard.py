"""Option-cost writes preserve legacy identities without creating hidden aliases.

HTTP handlers and Mongo query/update semantics are real. The database is an
in-memory Mongo fake; deterministic hooks exercise deletion and creation races.
No server, network database, or historical order writer is involved.
"""
import asyncio
from copy import deepcopy
from datetime import datetime, timezone
import json

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from mongomock_motor import AsyncMongoMockClient
import pytest
import pytest_asyncio

import product_option_cost_routes as routes
from product_cost_revision import PRODUCT_COST_REVISIONS
from product_v2_routes import PRODUCTS

OWNER = "option-cost-owner"
OTHER = "other-option-cost-owner"
PRODUCT = "option-cost-product"
SALLA = "option-cost-salla"
CREATED = datetime(2026, 1, 1, tzinfo=timezone.utc)
FILL_TYPES = ("text", "textarea", "long_text", "number", "date", "time", "file")


class CachedFakeDatabase:
    """Keep fake collection wrappers stable so tests can intercept DB boundaries."""

    def __init__(self):
        self.raw = AsyncMongoMockClient().option_cost_guard
        self.collections = {}

    def __getitem__(self, name):
        return self.collections.setdefault(name, self.raw[name])


@pytest.fixture
def runtime_field_support(monkeypatch):
    """Use production's text/custom-field validation, restoring its global hooks."""
    import order_option_cost_snapshot_routes as snapshots
    import product_v2_details_routes as details
    import product_v2_routes as products
    import supplier_receiving_routes as suppliers
    from product_field_cost_support import install_product_field_cost_support

    for module, name in (
        (snapshots, "selected_option_tokens"), (routes, "_option_value"),
        (details, "_details_patch"), (products, "make_product_v2_router"),
        (suppliers, "_supplier_product_reference_price"), (suppliers, "_recent_session_events"),
    ):
        monkeypatch.setattr(module, name, getattr(module, name))
    install_product_field_cost_support()


@pytest_asyncio.fixture
async def api(runtime_field_support, request):
    db = CachedFakeDatabase()
    await db[PRODUCTS].insert_one({
        "user_id": OWNER, "id": PRODUCT, "mezan_product_id": PRODUCT,
        "salla_product_id": SALLA, "name": "Synthetic shawl",
        "options": [{"id": "name", "name": "الاسم", "type": "text", "values": [],
                     "additional_price": 10}],
        "custom_fields": [{"id": "message", "name": "Message", "type": "text"}],
    })
    await db[routes.RESOURCES].insert_one({
        "user_id": OWNER, "id": "embroidery", "code": "EMBROIDERY",
        "name": "تطريز الاسم", "kind": "service", "unit_cost": 8, "status": "active",
    })
    app = FastAPI()
    principal = getattr(request, "param", {"id": OWNER})

    async def current_user():
        return principal

    app.include_router(routes.make_product_option_cost_router(db, current_user))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield db, client


def binding(value_id="__option__", **overrides):
    return {
        "id": "legacy-name" if value_id == "__option__" else "current-name",
        "user_id": OWNER, "salla_product_id": SALLA, "option_id": "name",
        "value_id": value_id, "option_name": "الاسم", "value_name": "الاسم",
        "mode": "direct", "direct_amount": 10, "resource_id": None,
        "quantity": 1, "created_at": CREATED, **overrides,
    }


def url(value_id="filled", option_id="name"):
    return f"/products-v2/{PRODUCT}/option-costs/{option_id}/{value_id}"


async def saved_rows(db):
    return await db[routes.BINDINGS].find({"user_id": OWNER}, {"_id": 0}).to_list(100)


async def audits(db):
    return await db[routes.AUDIT].find({"user_id": OWNER}, {"_id": 0}).to_list(100)


async def revision(db):
    row = await db[PRODUCT_COST_REVISIONS].find_one({"user_id": OWNER})
    return (row or {}).get("revision", 0)


@pytest.mark.asyncio
@pytest.mark.parametrize("field_type", FILL_TYPES)
async def test_new_filled_binding_cannot_hide_existing_option_level_cost(api, field_type):
    db, client = api
    await db[PRODUCTS].update_one({"id": PRODUCT}, {"$set": {"options.0.type": field_type}})
    await db[routes.BINDINGS].insert_one(binding())
    before = await saved_rows(db)

    response = await client.put(url(), json={"mode": "resource", "resource_id": "embroidery"})

    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "option_cost_binding_conflict"
    assert await saved_rows(db) == before
    assert await audits(db) == []
    assert await revision(db) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("field_type", FILL_TYPES)
async def test_new_option_level_alias_is_not_created_for_fill_based_fields(api, field_type):
    db, client = api
    await db[PRODUCTS].update_one({"id": PRODUCT}, {"$set": {"options.0.type": field_type}})

    response = await client.put(url("__option__"), json={"mode": "direct", "direct_amount": 10})

    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "option_cost_binding_conflict"
    assert await saved_rows(db) == []
    assert await audits(db) == []
    assert await revision(db) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("value_id", ("__option__", "filled"))
async def test_existing_exact_binding_is_editable_even_with_preexisting_alias_conflict(api, value_id):
    db, client = api
    await db[routes.BINDINGS].insert_many([binding(), binding("filled", direct_amount=8)])
    before = {row["value_id"]: row for row in await saved_rows(db)}

    response = await client.put(url(value_id), json={"mode": "direct", "direct_amount": 12})

    assert response.status_code == 200, response.text
    after = {row["value_id"]: row for row in await saved_rows(db)}
    assert set(after) == {"filled", "__option__"}
    assert after[value_id]["id"] == before[value_id]["id"]
    assert after[value_id]["created_at"] == before[value_id]["created_at"]
    assert after[value_id]["direct_amount"] == 12
    other = "filled" if value_id == "__option__" else "__option__"
    assert after[other] == before[other]
    history = await audits(db)
    assert len(history) == 1
    assert history[0]["before"]["direct_amount"] == before[value_id]["direct_amount"]
    assert history[0]["after"]["direct_amount"] == 12
    assert history[0]["binding_id"] == before[value_id]["id"]
    assert history[0]["actor_id"] == OWNER
    assert history[0]["source"] == "products_v2_option_cost_api"
    assert await revision(db) == 1


@pytest.mark.asyncio
async def test_fresh_filled_resource_binding_has_audited_cost_without_salla_sale_price(api):
    db, client = api

    response = await client.put(url(), json={"mode": "resource", "resource_id": "embroidery"})

    assert response.status_code == 200, response.text
    result = response.json()["binding"]
    assert result["value_id"] == "filled"
    assert result["direct_amount"] is None
    assert result["resolved_amount"] == 8
    history = await audits(db)
    assert len(history) == 1
    assert history[0]["event_type"] == "product_option_cost_saved"
    assert history[0]["before"] is None
    assert history[0]["after"]["resource_id"] == "embroidery"
    assert history[0]["after"]["direct_amount"] is None
    assert history[0]["binding_id"] == result["id"]
    assert history[0]["salla_product_id"] == SALLA
    assert history[0]["option_id"] == "name"
    assert history[0]["value_id"] == "filled"
    assert history[0]["actor_id"] == OWNER
    assert history[0]["source"] == "products_v2_option_cost_api"


@pytest.mark.asyncio
async def test_legacy_binding_can_switch_to_resource_without_changing_its_identity(api):
    db, client = api
    await db[routes.BINDINGS].insert_one(binding())

    response = await client.put(url("__option__"), json={"mode": "resource", "resource_id": "embroidery"})

    assert response.status_code == 200, response.text
    result = response.json()["binding"]
    assert result["id"] == "legacy-name" and result["value_id"] == "__option__"
    assert result["mode"] == "resource" and result["direct_amount"] is None
    assert result["resolved_amount"] == 8
    assert len(await saved_rows(db)) == 1
    history = await audits(db)
    assert history[0]["before"]["direct_amount"] == 10
    assert history[0]["after"]["direct_amount"] is None
    assert history[0]["after"]["resource_id"] == "embroidery"


@pytest.mark.asyncio
async def test_delete_orphan_uses_exact_keys_and_records_only_cost_provenance(api):
    db, client = api
    await db[PRODUCTS].update_one({"id": PRODUCT}, {"$set": {"options": []}})
    await db[routes.BINDINGS].insert_many([
        binding(customer_value="PRIVATE-CUSTOMER-TEXT", options_raw=[{"value": "PRIVATE-CUSTOMER-TEXT"}]),
        binding("filled", direct_amount=8),
        binding(user_id=OTHER, id="foreign-binding"),
    ])

    response = await client.delete(url("__option__"))

    assert response.status_code == 200 and response.json()["deleted"] == 1
    assert [row["value_id"] for row in await saved_rows(db)] == ["filled"]
    assert await db[routes.BINDINGS].count_documents({"user_id": OTHER}) == 1
    history = await audits(db)
    assert len(history) == 1
    assert history[0]["event_type"] == "product_option_cost_deleted"
    assert history[0]["before"]["direct_amount"] == 10
    assert history[0]["before"]["value_id"] == "__option__"
    assert history[0]["after"] is None
    assert history[0]["actor_id"] == OWNER
    assert history[0]["source"] == "products_v2_option_cost_api"
    assert "PRIVATE-CUSTOMER-TEXT" not in json.dumps(history, default=str)
    assert await revision(db) == 1

    response = await client.delete(url("__option__"))
    assert response.json()["deleted"] == 0
    assert await audits(db) == history
    assert await revision(db) == 1


@pytest.mark.asyncio
async def test_legacy_deleted_during_update_is_never_recreated(api, monkeypatch):
    db, client = api
    await db[routes.BINDINGS].insert_one(binding())
    collection = db[routes.BINDINGS]
    original = collection.find_one_and_update
    intercepted = []

    async def delete_before_update(selector, update, *args, **kwargs):
        intercepted.append(True)
        await collection.delete_one({"user_id": OWNER, "id": "legacy-name"})
        return await original(selector, update, *args, **kwargs)

    monkeypatch.setattr(collection, "find_one_and_update", delete_before_update)
    response = await client.put(url("__option__"), json={"mode": "direct", "direct_amount": 12})

    assert intercepted
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "option_cost_binding_conflict"
    assert await saved_rows(db) == []
    assert await audits(db) == []
    assert await revision(db) == 0


@pytest.mark.asyncio
async def test_simultaneous_new_alias_requests_can_only_create_filled(api):
    db, client = api

    filled, legacy = await asyncio.gather(
        client.put(url(), json={"mode": "direct", "direct_amount": 8}),
        client.put(url("__option__"), json={"mode": "direct", "direct_amount": 10}),
    )

    assert filled.status_code == 200, filled.text
    assert legacy.status_code == 409, legacy.text
    rows = await saved_rows(db)
    assert len(rows) == 1 and rows[0]["value_id"] == "filled"
    assert len(await audits(db)) == 1


@pytest.mark.asyncio
async def test_concurrent_filled_creations_share_unique_binding_and_audit_actual_preimages(api, monkeypatch):
    db, client = api
    collection = db[routes.BINDINGS]
    original = collection.find_one
    both_read = asyncio.Event()
    reads = []

    async def hold_initial_reads(selector, *args, **kwargs):
        row = await original(selector, *args, **kwargs)
        if selector.get("value_id") == "filled" and row is None and len(reads) < 2:
            reads.append(True)
            if len(reads) == 2:
                both_read.set()
            await asyncio.wait_for(both_read.wait(), timeout=2)
        return deepcopy(row)

    monkeypatch.setattr(collection, "find_one", hold_initial_reads)
    results = await asyncio.gather(
        client.put(url(), json={"mode": "direct", "direct_amount": 8}),
        client.put(url(), json={"mode": "direct", "direct_amount": 9}),
    )

    assert all(response.status_code == 200 for response in results), [response.text for response in results]
    assert len(reads) == 2
    rows = await saved_rows(db)
    assert len(rows) == 1
    history = await audits(db)
    assert len(history) == 2
    created = next(event for event in history if event["before"] is None)
    updated = next(event for event in history if event["before"] is not None)
    assert updated["before"]["direct_amount"] == created["after"]["direct_amount"]
    assert {event["binding_id"] for event in history} == {rows[0]["id"]}
    assert {event["after"]["direct_amount"] for event in history} == {8, 9}


@pytest.mark.asyncio
async def test_guard_is_owner_scoped_and_preserves_custom_field_and_non_text_subjects(api):
    db, client = api
    await db[routes.BINDINGS].insert_one(binding(user_id=OTHER, id="foreign-binding"))
    response = await client.put(url(), json={"mode": "direct", "direct_amount": 8})
    assert response.status_code == 200, response.text

    response = await client.put(url("filled", "field:message"), json={"mode": "direct", "direct_amount": 3})
    assert response.status_code == 200, response.text

    await db[PRODUCTS].update_one({"id": PRODUCT}, {"$push": {"options": {
        "id": "other", "name": "Non-text option", "type": "select", "values": [],
    }}})
    response = await client.put(url("__option__", "other"), json={"mode": "direct", "direct_amount": 4})
    assert response.status_code == 200, response.text
    assert await db[routes.BINDINGS].count_documents({"user_id": OTHER}) == 1


@pytest.mark.asyncio
async def test_separate_real_option_costs_remain_independent(api):
    db, client = api
    await db[PRODUCTS].update_one({"id": PRODUCT}, {"$push": {"options": {
        "id": "embroidery-choice", "name": "هل تريد تطريز الاسم", "type": "radio",
        "values": [{"id": "yes", "name": "نعم"}],
    }}})
    direct = await client.put(url(), json={"mode": "direct", "direct_amount": 10})
    resource = await client.put(url("yes", "embroidery-choice"),
                                json={"mode": "resource", "resource_id": "embroidery"})
    assert direct.status_code == resource.status_code == 200
    assert {(row["option_id"], row["value_id"]) for row in await saved_rows(db)} == {
        ("name", "filled"), ("embroidery-choice", "yes"),
    }


@pytest.mark.asyncio
async def test_audit_failure_does_not_leave_changed_cost_in_stale_cache(api, monkeypatch):
    db, client = api

    async def unavailable_audit(*args, **kwargs):
        raise RuntimeError("synthetic audit unavailable")

    monkeypatch.setattr(db[routes.AUDIT], "insert_one", unavailable_audit)
    # Audit is a separate write, as in neighboring cost routes. A failure must
    # not report success or stop cache invalidation for the completed mutation.
    with pytest.raises(RuntimeError, match="synthetic audit unavailable"):
        await client.put(url(), json={"mode": "direct", "direct_amount": 8})
    assert len(await saved_rows(db)) == 1
    assert await revision(db) == 1
    assert await audits(db) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("api", [{"id": OWNER, "_mobile_actor_id": "option-cost-worker"}], indirect=True)
async def test_mobile_audit_attributes_worker_while_preserving_owner_scope(api):
    db, client = api

    saved = await client.put(url(), json={"mode": "resource", "resource_id": "embroidery"})
    assert saved.status_code == 200, saved.text
    assert saved.json()["binding"]["user_id"] == OWNER
    deleted = await client.delete(url())
    assert deleted.status_code == 200 and deleted.json()["deleted"] == 1

    history = await audits(db)
    assert len(history) == 2
    assert {event["actor_id"] for event in history} == {"option-cost-worker"}
    assert {event["user_id"] for event in history} == {OWNER}
    assert {event["event_type"] for event in history} == {
        "product_option_cost_saved", "product_option_cost_deleted",
    }
    assert await revision(db) == 2
