"""Public preview/apply with real owner transactions and isolated Mongo data.

These checks deliberately use provider-shaped local orders, actual frozen
catalog expansion and actual plan reservations. No live tenant or supplier is
read. Transaction tests require the loopback replica supplied by the CI gate.
"""
import asyncio
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
import os
from types import SimpleNamespace
from urllib.parse import urlsplit
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from httpx import ASGITransport, AsyncClient
from motor.motor_asyncio import AsyncIOMotorClient
import pytest
import pytest_asyncio

import bundle_catalog_v2 as catalog
import bundle_supplier_adoption as adoption
import operational_atomic as atomic
import stock_component_consumption_service as stock
from bundle_supplier_pieces import REFERENCE_FIELD, validate_supplier_leaf_piece
from fulfillment_v2_routes import _component_order_lines, component_order_fingerprint
from order_engine.repository import MongoOrderRepository
from order_engine.service import get_order
from preparation_piece_barcode import preparation_piece_id


OWNER = "adoption-owner"
OTHER = "adoption-other"
EMPLOYEE = "adoption-employee"
ORDER = "synthetic-adoption-order"
WHEN = "2026-11-20T09:00:00+00:00"
FINANCIAL = ("mezan_supplier_invoices_v2", "mezan_supplier_receiving_events_v1",
    "mezan_inventory_receipts_v2", "warehouse_locations", "purchase_invoices",
    "mz2_journal_entries", "mz2_ledger_entries", "mz2_supplier_payables", "payment_transactions")


def recipe():
    return [{"kind": "current_product", "quantity": str(len(sides)),
        "condition": {"option_id": "kind", "value_id": choice},
        "sourcing_mode": "supplier", "supplier_id": "synthetic-supplier",
        "slots": [{"slot_id": side, "selection_option_ids": [side + "_size", side + "_color"]}
                  for side in sides]}
        for choice, sides in (("first", ("first",)), ("second", ("second",)), ("pair", ("first", "second")))]


def catalog_options():
    result = [{"id": "kind", "name": "Hoodie selection", "type": "radio", "required": True,
        "values": [{"id": value, "name": value} for value in ("first", "second", "pair")]}]
    for side, excluded in (("first", "second"), ("second", "first")):
        for field, values in (("size", ("M", "XL")), ("color", ("Black", "White"))):
            result.append({"id": side + "_" + field, "name": side + " " + field, "type": "radio", "required": True,
                "visibility": "on_condition", "visibility_condition_type": "!=",
                "visibility_condition_option": "kind", "visibility_condition_value": excluded,
                "values": [{"id": value.lower(), "name": value} for value in values]})
    return result


def provider_order(choice="pair", quantity=1):
    picked = {"kind": choice}
    if choice != "second":
        picked.update(first_size="xl", first_color="black")
    if choice != "first":
        picked.update(second_size="m", second_color="white")
    options = []
    for field in catalog_options():
        if field["id"] in picked:
            value = next(row for row in field["values"] if row["id"] == picked[field["id"]])
            options.append({"id": field["id"], "name": field["name"], "value": value})
    return {"id": "synthetic-native-order", "reference_id": ORDER, "date": WHEN, "updated_at": WHEN,
        "status": {"slug": "in_progress", "name": "in_progress"}, "payment_method": "cod",
        "amounts": {"total": {"amount": 260.60, "currency": "SAR"}},
        "shipping_address": {"city": "Synthetic city", "street": "Synthetic street"},
        "items": [{"id": "synthetic-line", "product_id": "hoodie", "name": "Synthetic hoodie set",
            "sku": "SYN-HOODIE", "quantity": quantity, "options": options,
            "amounts": {"price_without_tax": {"amount": 254, "currency": "SAR"},
                "total": {"amount": 260.60, "currency": "SAR"}}}]}


@pytest_asyncio.fixture
async def case(request):
    uri = os.environ.get("MZ2_TEST_MONGO_URI")
    if not uri:
        pytest.skip("requires isolated loopback replica set")
    assert urlsplit(uri).hostname in {"127.0.0.1", "localhost", "::1"}
    client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=3000)
    db = client["mz2_bundle_adoption_test_" + uuid4().hex]
    assert (await db.command("hello")).get("setName")
    await db.mz2_atomic_owners.insert_one({"_id": OWNER, "revision": 0, "writes_paused": False, "control_revision": 0})
    await db.settings.insert_one({"user_id": OWNER, "g47_inventory": {"component_lifecycle_starts_at": "2026-09-01T00:00:00+00:00"}})
    await db.users.insert_many([{"id": OWNER, "role": "owner", "is_active": True},
        {"id": OTHER, "role": "owner", "is_active": True},
        {"id": EMPLOYEE, "role": "employee", "created_by": OWNER, "is_active": True}])
    await db[adoption.PRODUCTS].insert_one({"user_id": OWNER, "id": "hoodie", "mezan_product_id": "hoodie",
        "salla_product_id": "hoodie", "name": "Synthetic hoodie set", "variants": [], "options": catalog_options()})
    await db[adoption.COSTS].insert_one({"user_id": OWNER, "salla_product_id": "hoodie", "base_cost": 50})
    await db[adoption.COST_REVISIONS].insert_one({"_id": "product-cost:" + OWNER, "user_id": OWNER, "revision": 1})
    await db[adoption.SUPPLIERS].insert_one({"user_id": OWNER, "id": "synthetic-supplier", "name": "Synthetic supplier", "status": "active"})
    choice = getattr(request, "param", "pair")
    raw = provider_order(choice)
    await db[adoption.SOURCES].insert_one({"user_id": OWNER, "order_number": ORDER, "order_date": WHEN,
        "order_status": "in_progress", "order_status_slug": "in_progress",
        "g47_salla_snapshot": {"revision": 1, "requires_authoritative_refresh": False, "component_pending": False},
        "raw_by_source": {"salla_direct": deepcopy(raw)}})
    order = await get_order(MongoOrderRepository(db), user_id=OWNER, order_number=ORDER)
    lines = _component_order_lines(order, {})
    line = lines[0]["order_line_id"]
    await db[adoption.LIFECYCLES].insert_one({"user_id": OWNER, "order_number": ORDER, "accepted": True,
        "state": "reserved", "retry_required": False, "generation": 1, "snapshot_revision": 1,
        "source_fingerprint": component_order_fingerprint(order), "bundle_intake_snapshots": {line: None}})
    await stock.ensure_component_consumption_indexes(db)
    await stock.reserve_component_stock(db, merchant_id=OWNER, order_id=ORDER, lines=lines,
        source_version=1, source_created_at=WHEN, actor_id=OWNER)
    await db[adoption.WORKFLOWS].insert_one({"user_id": OWNER, "order_number": ORDER,
        "stage": "in_progress", "revision": 1, "items": [{"order_item_id": line, "review_status": "reviewed", "supplier_export": True}]})
    old_id = preparation_piece_id(user_id=OWNER, order_number=ORDER, order_item_id=line, unit_index=1)
    old = {"id": old_id, "piece_id": old_id, "barcode": "MEZAN-PIECE:" + old_id,
        "user_id": OWNER, "order_number": ORDER, "order_item_id": line, "unit_index": 1, "quantity": 1,
        "product_id": "hoodie", "product_name": "Synthetic hoodie set", "sku": "SYN-HOODIE",
        "batch_id": "synthetic-batch", "file_number": "synthetic-file", "total_products_in_order": 1,
        "responsible_employee_id": OWNER, "responsible_employee_name": "Synthetic owner",
        "status": "in_progress", "execution_status": "sent_to_supplier", "services": [], "assembly_status": "pending",
        "supplier_dispatch_id": "original-dispatch", "supplier_dispatch_status": "sent",
        "supplier_id": "synthetic-supplier", "assigned_at": WHEN, "sent_to_supplier_at": WHEN,
        "original_history": [{"event": "assigned", "at": WHEN}]}
    await db[adoption.PIECES].insert_one(deepcopy(old))
    await db[adoption.DISPATCHES].insert_one({"id": "original-dispatch", "user_id": OWNER,
        "status": "sent", "supplier_id": "synthetic-supplier", "sent_by_id": OWNER,
        "supplier_file_number": "synthetic-supplier-file", "piece_ids": [old["piece_id"]], "piece_count": 1, "sent_at": WHEN})
    await catalog.save_bundle(db, OWNER, "hoodie", {"expected_revision": 0, "schema_version": 2,
        "product_type": "composite", "items": recipe()}, OWNER)
    app = FastAPI()
    async def current_user(request: Request):
        actor = request.headers.get("x-test-actor", OWNER)
        if actor == "mobile-employee":
            return {"id": OWNER, "role": "owner", "_mobile_actor_id": EMPLOYEE}
        return {"id": actor, "role": "owner"}
    app.include_router(adoption.make_bundle_supplier_adoption_router(db, current_user))
    web = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    value = SimpleNamespace(db=db, web=web, app=app, line=line, old=old, raw=raw,
        path=f"/fulfillment-v2/orders/{ORDER}/bundle-adoption", choice=choice)
    try:
        yield value
    finally:
        await web.aclose()
        assert db.name.startswith("mz2_bundle_adoption_test_")
        await client.drop_database(db.name)
        client.close()


async def preview(case):
    result = await case.web.post(case.path + "/preview", json={"order_line_id": case.line})
    assert result.status_code == 200, result.text
    return result.json()


def command(case, evidence, request_id="synthetic-request-1"):
    return {"order_line_id": case.line, "preview_hash": evidence["preview_hash"],
        "client_request_id": request_id, "reason": "Reconcile physical supplier pieces before receipt"}


async def counts(db):
    return {name: await db[name].count_documents({}) for name in await db.list_collection_names()}


async def unchanged(case, source, lifecycle, old, *, no_adoptions=True):
    assert adoption._without_fence(await case.db[adoption.SOURCES].find_one({"user_id": OWNER})) == adoption._without_fence(source)
    assert adoption._without_fence(await case.db[adoption.LIFECYCLES].find_one({"user_id": OWNER})) == adoption._without_fence(lifecycle)
    if no_adoptions:
        assert await case.db[adoption.ADOPTIONS].count_documents({}) == 0
        assert await case.db[adoption.PIECES].find_one({"piece_id": old["piece_id"]}) == old
        assert await case.db[adoption.PIECES].count_documents({}) == 1


@pytest.mark.asyncio
async def test_public_preview_is_read_only_and_quotes_two_distinct_50_sar_pieces(case):
    before = await counts(case.db)
    response = await preview(case)
    assert await counts(case.db) == before
    assert response["commercial_quantity"] == 1 and response["physical_quantity"] == 2
    assert Decimal(response["expected_total_cost"]) == 100
    assert response["previous_piece_ids"] == [case.old["piece_id"]]
    assert len({row["piece_id"] for row in response["requirements"]}) == 2
    assert all(row["unit_index"] == 1 and Decimal(row["unit_cost"]) == 50 for row in response["requirements"])
    assert {tuple(sorted(row["specifications"].items())) for row in response["requirements"]} == {
        (("first_color", "Black"), ("first_size", "XL")), (("second_color", "White"), ("second_size", "M"))}


@pytest.mark.asyncio
async def test_deleted_owner_cannot_preview_or_apply(case):
    await case.db.users.update_one({"id": OWNER}, {"$set": {"deleted_at": WHEN}})
    for endpoint, payload in (("preview", {"order_line_id": case.line}),
            ("apply", command(case, {"preview_hash": "0" * 64}))):
        response = await case.web.post(case.path + "/" + endpoint, json=payload)
        assert response.status_code == 403, response.text


@pytest.mark.asyncio
async def test_apply_retires_parent_preserves_sale_intake_and_replays_exactly(case):
    evidence = await preview(case)
    payload = command(case, evidence)
    source = await case.db[adoption.SOURCES].find_one({"user_id": OWNER})
    lifecycle = await case.db[adoption.LIFECYCLES].find_one({"user_id": OWNER})
    parent = await case.db[adoption.PIECES].find_one({"piece_id": case.old["piece_id"]})
    old_dispatch = await case.db[adoption.DISPATCHES].find_one({"id": "original-dispatch"})
    old_unit = await case.db[adoption.UNITS].find_one({"order_id": ORDER})
    result = await case.web.post(case.path + "/apply", json=payload)
    assert result.status_code == 200, result.text
    applied = result.json()
    assert applied["state"] == "applied" and applied["duplicate"] is False
    assert applied["piece_ids"] == [row["piece_id"] for row in evidence["requirements"]]
    await unchanged(case, source, lifecycle, parent, no_adoptions=False)
    retired = await case.db[adoption.PIECES].find_one({"piece_id": parent["piece_id"]})
    assert retired["status"] == "cancelled" and retired["bundle_supplier_retired"] is True
    assert retired["original_history"] == parent["original_history"]
    assert retired["barcode"] == parent["barcode"] and retired["supplier_dispatch_id"] == "original-dispatch"
    assert adoption._without_fence(await case.db[adoption.DISPATCHES].find_one({"id": "original-dispatch"})) == old_dispatch
    unit = await case.db[adoption.UNITS].find_one({"order_id": ORDER})
    assert unit["state"] == "reserved" and unit["consumption_id"] == old_unit["consumption_id"]
    assert unit["allocations"] == old_unit["allocations"] == []
    assert unit["resource_demands"] == old_unit["resource_demands"] == []
    assert len(unit["bundle_leaves"]) == 2
    assert all(leaf["actual_cost"] is None and leaf["received_quantity"] == "0" for leaf in unit["bundle_leaves"])
    pieces = await case.db[adoption.PIECES].find({"piece_id": {"$in": applied["piece_ids"]}}).to_list(10)
    assert len(pieces) == 2 and all(piece["unit_index"] == 1 for piece in pieces)
    for piece in pieces:
        await validate_supplier_leaf_piece(case.db, OWNER, piece)
        assert piece["original_piece_id"] == parent["piece_id"] and piece["responsible_employee_id"] == OWNER
    dispatch = await case.db[adoption.DISPATCHES].find_one({"bundle_supplier_adoption_id": applied["adoption_id"]})
    assert dispatch["piece_count"] == 2 and dispatch["reconciles_dispatch_id"] == "original-dispatch"
    assert sum(file["piece_count"] for file in dispatch["source_files"]) == 2
    audit = await case.db[adoption.ADOPTIONS].find_one({"id": applied["adoption_id"]})
    assert audit["before"]["intake"] is None and audit["financial_effect"] == "none"
    assert audit["seal"] == adoption._digest({key:value for key,value in audit.items() if key not in {"_id", "seal"}})
    before_counts = await counts(case.db)
    again = await case.web.post(case.path + "/apply", json=payload)
    assert again.status_code == 200 and again.json() == {**applied, "duplicate": True}, again.text
    assert await counts(case.db) == before_counts
    for name in FINANCIAL:
        assert await case.db[name].count_documents({}) == 0, name


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["first", "second"], indirect=True)
async def test_single_selection_materializes_one_50_sar_piece(case):
    evidence = await preview(case)
    assert evidence["physical_quantity"] == 1 and Decimal(evidence["expected_total_cost"]) == 50
    result = await case.web.post(case.path + "/apply", json=command(case, evidence))
    assert result.status_code == 200, result.text
    assert len(result.json()["piece_ids"]) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("actor, expected", [(EMPLOYEE,403), ("mobile-employee",403), (OTHER,409), ("unknown",403)])
async def test_public_owner_and_tenant_boundaries(case, actor, expected):
    result = await case.web.post(case.path + "/preview", json={"order_line_id": case.line}, headers={"x-test-actor": actor})
    assert result.status_code == expected, result.text
    assert await case.db[adoption.ADOPTIONS].count_documents({}) == 0


@pytest.mark.asyncio
async def test_concurrent_same_request_commits_once_and_changed_payload_conflicts(case):
    evidence = await preview(case)
    payload = command(case, evidence)
    results = await asyncio.gather(*(case.web.post(case.path + "/apply", json=payload) for _ in range(2)))
    assert all(result.status_code == 200 for result in results), [result.text for result in results]
    assert sorted(result.json()["duplicate"] for result in results) == [False, True]
    assert len({result.json()["adoption_id"] for result in results}) == 1
    assert await case.db[adoption.ADOPTIONS].count_documents({}) == 1
    assert await case.db[adoption.PIECES].count_documents({}) == 3
    altered = await case.web.post(case.path + "/apply", json={**payload, "reason": "Different approved reason"})
    assert altered.status_code == 409 and altered.json()["detail"]["code"] == "bundle_adoption_request_conflict"


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", ["source", "price", "catalog", "custody", "supplier", "cost_revision"])
async def test_preview_binds_exact_source_catalog_cost_supplier_and_dispatch(case, changed):
    evidence = await preview(case)
    changes = {
        "source": (adoption.SOURCES, {"user_id": OWNER}, {"g47_salla_snapshot.revision": 2}),
        "price": (adoption.COSTS, {"user_id": OWNER}, {"base_cost": 51}),
        "catalog": (adoption.DEFINITIONS, {"user_id": OWNER}, {"revision": 2}),
        "custody": (adoption.DISPATCHES, {"id": "original-dispatch"}, {"sent_at": "changed evidence"}),
        "supplier": (adoption.SUPPLIERS, {"user_id": OWNER}, {"name": "Updated supplier name"}),
        "cost_revision": (adoption.COST_REVISIONS, {"user_id": OWNER}, {"revision": 2}),
    }
    name, query, change = changes[changed]
    await case.db[name].update_one(query, {"$set": change})
    source = await case.db[adoption.SOURCES].find_one({"user_id": OWNER})
    lifecycle = await case.db[adoption.LIFECYCLES].find_one({"user_id": OWNER})
    old = await case.db[adoption.PIECES].find_one({"piece_id": case.old["piece_id"]})
    result = await case.web.post(case.path + "/apply", json=command(case, evidence))
    assert result.status_code == 409, result.text
    await unchanged(case, source, lifecycle, old)


@pytest.mark.asyncio
@pytest.mark.parametrize("blocker", ["receipt", "invoice", "scan", "service", "assembly", "consumption",
    "allocations", "hold", "unknown_intake", "missing_cost", "missing_cost_revision", "retired_receipt",
    "stale_acceptance", "missing_acceptance", "pending_source"])
async def test_preview_refuses_existing_evidence_and_unsupported_state(case, blocker):
    if blocker == "receipt":
        await case.db["mezan_supplier_receiving_events_v1"].insert_one({"user_id": OWNER,
            "piece_id": case.old["piece_id"], "event_type": "supplier_piece_service_recorded"})
    elif blocker == "invoice":
        await case.db["mezan_supplier_invoices_v2"].insert_one({"user_id": OWNER, "status": "approved",
            "lines": [{"piece_ids": [case.old["piece_id"]]}]})
    elif blocker in {"scan", "service", "assembly"}:
        changes = {"scan": {"supplier_receiving_session_id": "open-session"},
            "service": {"services": [{"service_id": "printing", "status": "completed"}]},
            "assembly": {"assembly_status": "completed"}}
        await case.db[adoption.PIECES].update_one({"piece_id": case.old["piece_id"]}, {"$set": changes[blocker]})
    elif blocker in {"consumption", "allocations"}:
        await case.db[adoption.UNITS].update_one({"order_id": ORDER}, {"$set":
            {"state": "consumed"} if blocker == "consumption" else {"allocations": [{"id": "allocation"}]}})
    elif blocker == "hold":
        await case.db[adoption.WORKFLOWS].update_one({"user_id": OWNER}, {"$set": {"customer_service_hold_active": True}})
    elif blocker == "unknown_intake":
        await case.db[adoption.LIFECYCLES].update_one({"user_id": OWNER}, {"$unset": {"bundle_intake_snapshots": ""}})
    elif blocker == "missing_cost":
        await case.db[adoption.COSTS].delete_many({"user_id": OWNER})
    elif blocker == "missing_cost_revision":
        await case.db[adoption.COST_REVISIONS].delete_many({"user_id": OWNER})
    elif blocker == "stale_acceptance":
        await case.db[adoption.LIFECYCLES].update_one({"user_id": OWNER}, {"$set": {"snapshot_revision": 0}})
    elif blocker == "missing_acceptance":
        await case.db[adoption.LIFECYCLES].update_one({"user_id": OWNER}, {"$unset": {"snapshot_revision": ""}})
    elif blocker == "pending_source":
        await case.db[adoption.SOURCES].update_one({"user_id": OWNER}, {"$set": {"g47_salla_snapshot.component_pending": True}})
    else:
        await case.db[adoption.PIECES].insert_one({**case.old, "id": "retired-alias", "piece_id": "retired-alias",
            "status": "cancelled", "received_at": WHEN})
    before = await counts(case.db)
    result = await case.web.post(case.path + "/preview", json={"order_line_id": case.line})
    assert result.status_code == 409, result.text
    assert await counts(case.db) == before


@pytest.mark.asyncio
async def test_failure_after_materialization_rolls_back_every_write(case, monkeypatch):
    import bundle_supplier_pieces as bridge
    evidence = await preview(case)
    source = await case.db[adoption.SOURCES].find_one({"user_id": OWNER})
    lifecycle = await case.db[adoption.LIFECYCLES].find_one({"user_id": OWNER})
    old = await case.db[adoption.PIECES].find_one({"piece_id": case.old["piece_id"]})
    plan = await case.db[adoption.PLANS].find_one({"order_id": ORDER})
    unit = await case.db[adoption.UNITS].find_one({"order_id": ORDER})
    original = bridge.validate_supplier_leaf_piece
    async def fail_after_real_validation(*args, **kwargs):
        await original(*args, **kwargs)
        raise HTTPException(409, detail={"code": "injected_after_piece_insert"})
    monkeypatch.setattr(bridge, "validate_supplier_leaf_piece", fail_after_real_validation)
    result = await case.web.post(case.path + "/apply", json=command(case, evidence))
    assert result.status_code == 409 and result.json()["detail"]["code"] == "injected_after_piece_insert", result.text
    await unchanged(case, source, lifecycle, old)
    assert await case.db[adoption.PLANS].find_one({"order_id": ORDER}) == plan
    assert await case.db[adoption.UNITS].find_one({"order_id": ORDER}) == unit
    assert await case.db[adoption.DISPATCHES].count_documents({}) == 1
    assert await case.db[adoption.PIECE_EVENTS].count_documents({}) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("race", ["source", "cost", "dispatch", "scan", "owner"])
async def test_external_change_between_read_and_fence_cannot_commit_stale_preview(case, monkeypatch, race):
    evidence = await preview(case)
    original = adoption._fence
    changed = False
    async def racing_fence(tx, name, row, owner):
        nonlocal changed
        if not changed:
            changed = True
            collection, query, patch = {
                "source": (adoption.SOURCES, {"user_id": OWNER}, {"g47_salla_snapshot.revision": 2}),
                "cost": (adoption.COSTS, {"user_id": OWNER}, {"base_cost": 99}),
                "dispatch": (adoption.DISPATCHES, {"id": "original-dispatch"}, {"sent_by_id": EMPLOYEE}),
                "scan": (adoption.PIECES, {"piece_id": case.old["piece_id"]}, {"supplier_receiving_session_id": "racing-scan"}),
                "owner": ("users", {"id": OWNER}, {"is_active": False}),
            }[race]
            await case.db[collection].update_one(query, {"$set": patch})
        return await original(tx, name, row, owner)
    monkeypatch.setattr(adoption, "_fence", racing_fence)
    result = await case.web.post(case.path + "/apply", json=command(case, evidence))
    assert result.status_code in {403,409}, result.text
    assert changed is True
    assert await case.db[adoption.ADOPTIONS].count_documents({}) == 0
    assert await case.db[adoption.PIECES].count_documents({}) == 1
    assert not (await case.db[adoption.PLANS].find_one({"order_id": ORDER})).get("bundle_snapshots")
    old = await case.db[adoption.PIECES].find_one({"piece_id": case.old["piece_id"]})
    assert old["status"] == "in_progress" and not old.get("bundle_supplier_retired")


@pytest.mark.asyncio
@pytest.mark.parametrize("attempt", ["finance", "inventory", "cost_change", "source_sale", "intake_erase",
    "consumption_id", "account_role", "account_foreign", "upsert", "replace", "delete", "cross_owner", "arbitrary_parent"])
async def test_capability_denials_abort_even_if_caught(case, attempt):
    source = await case.db[adoption.SOURCES].find_one({"user_id": OWNER})
    old_unit = await case.db[adoption.UNITS].find_one({"order_id": ORDER})
    async def forbidden(tx):
        await tx[adoption.SOURCES].update_one({"_id": source["_id"], "user_id": OWNER}, {"$inc": {adoption.FENCE_FIELD: 1}})
        try:
            if attempt == "finance":
                await tx["mezan_supplier_invoices_v2"].insert_one({"user_id": OWNER, "amount": 100})
            elif attempt == "inventory":
                await tx["warehouse_locations"].update_one({"user_id": OWNER}, {"$set": {"occupancy": {}}})
            elif attempt == "cost_change":
                await tx[adoption.COSTS].update_one({"user_id": OWNER}, {"$set": {"base_cost": 0}})
            elif attempt == "source_sale":
                await tx[adoption.SOURCES].update_one({"_id": source["_id"], "user_id": OWNER}, {"$set": {"raw_by_source": {}}})
            elif attempt == "intake_erase":
                await tx[adoption.LIFECYCLES].update_one({"user_id": OWNER}, {"$unset": {"bundle_intake_snapshots": ""}})
            elif attempt == "consumption_id":
                await tx[adoption.UNITS].update_one({"_id": old_unit["_id"], "user_id": OWNER}, {"$set": {"consumption_id": "changed"}})
            elif attempt == "account_role":
                await tx.users.update_one({"id": OWNER, "role": "owner"}, {"$set": {"role": "admin"}})
            elif attempt == "account_foreign":
                await tx.users.update_one({"id": OTHER, "created_by": OWNER}, {"$inc": {adoption.FENCE_FIELD: 1}})
            elif attempt == "upsert":
                await tx[adoption.SOURCES].update_one({"_id": source["_id"], "user_id": OWNER}, {"$inc": {adoption.FENCE_FIELD: 1}}, upsert=True)
            elif attempt == "replace":
                await tx[adoption.SOURCES].replace_one({"user_id": OWNER}, {"user_id": OWNER})
            elif attempt == "delete":
                await tx[adoption.PIECES].delete_one({"user_id": OWNER})
            elif attempt == "cross_owner":
                await tx[adoption.SOURCES].update_one({"_id": source["_id"], "user_id": OTHER}, {"$inc": {adoption.FENCE_FIELD: 1}})
            else:
                await tx[adoption.PIECES].insert_one({**case.old, "unit_index": 2})
        except HTTPException:
            pass
    with pytest.raises(HTTPException):
        await atomic.operational_owner(case.db, OWNER, forbidden, profile=adoption.PROFILE)
    assert await case.db[adoption.SOURCES].find_one({"user_id": OWNER}) == source
    assert await case.db[adoption.UNITS].find_one({"order_id": ORDER}) == old_unit
    assert await case.db[adoption.PIECES].count_documents({}) == 1
    assert atomic._PROFILES[adoption.PROFILE] == adoption.ADOPTION_WRITE_COLLECTIONS


async def initialize_synthetic_financial_authority(db):
    """An isolated opening and explicit debit mapping, using actual ledger code."""
    from accounting_atomic import atomic_owner
    from accounting_ledger_v2 import ensure_accounting_ledger_v2_indexes, post_opening_journal_v2
    from accounting_module_contract import EVIDENCE_SECTIONS
    from supplier_debit_identity_v2 import MappingPut, save_mapping
    from test_supplier_native_invoice_v2 import OPENING_MANIFEST, OPENING_HASH, OPENING_LINES
    await ensure_accounting_ledger_v2_indexes(db)
    actor = {"id": OWNER, "role": "owner", "is_active": True, "name": "Synthetic owner",
        "accounting_permissions": ["accounting.purchases.post", "accounting.settlements.view"]}
    await db.users.update_one({"id": OWNER}, {"$set": actor})
    await db.mz2_atomic_owners.update_one({"_id": OWNER}, {"$set": {
        "ledger_backend_state": "v2_active", "ledger_backend_revision": 1,
        "ledger_backend_contract_revision": 1, "ledger_backend_activation_ref": "synthetic-adoption"}})
    async def opening(scoped):
        return await post_opening_journal_v2(db, user_id=OWNER, actor_id=OWNER,
            actor_name="Synthetic owner", opening_operation_id="synthetic-opening",
            approved_preview_hash=OPENING_HASH, effective_at="2026-09-01T00:00:00Z",
            entries=[{"leg_key": "control", "entity_type": "opening_control", "entity_id": "clean-start",
                      "entry_type": "opening_balance", "side": "debit", "amount": "0.01"},
                     {"leg_key": "equity", "entity_type": "equity", "entity_id": "opening-equity",
                      "entry_type": "opening_balance", "side": "credit", "amount": "0.01"}],
            mongo_session=scoped._session)
    opened = await atomic_owner(db, OWNER, opening)
    group = opened["group"]["txn_group_id"]
    await db.settings.update_one({"user_id": OWNER}, {"$set": {"mezan2_financial_cutover": {
        "operation_id": "MZ2-FIN-CUTOVER-001", "ledger_source": "accounting_v2_operation_scoped",
        "status": "active", "cutover_at": "2026-09-01T00:00:00Z",
        "evidence_sheet_ref": "synthetic-local-only",
        "evidence_sections": {row["id"]: "synthetic-local-only" for row in EVIDENCE_SECTIONS},
        "opening_balance_preview_id": "synthetic-preview", "opening_balance_preview_balanced": True,
        "opening_balance_approved_at": "2026-09-01T00:00:00Z", "opening_balance_approved_by": OWNER,
        "opening_balance_txn_group_id": group, "opening_active_txn_group_id": group,
        "opening_root_txn_group_id": group}}})
    await db.mz2_opening_balance_drafts.insert_one({"user_id": OWNER, "status": "posted",
        "id": "synthetic-opening-draft", "cutover_at": "2026-09-01T00:00:00Z",
        "preview_manifest": deepcopy(OPENING_MANIFEST), "preview_hash": OPENING_HASH,
        "txn_group_id": group, "lines": deepcopy(OPENING_LINES)})
    mapping = MappingPut(confirmed=True, reason="Synthetic adoption supplier product mapping",
        source_kind="product", source_id="hoodie", financial_treatment="INVENTORY_ASSET",
        entity_type="asset", entity_id="inventory-main", sub_account="inventory", currency="SAR",
        status="active", version=0)
    await atomic_owner(db, OWNER, lambda scoped: save_mapping(scoped, OWNER, actor, mapping))
    return actor


@pytest.mark.asyncio
async def test_public_adoption_scan_invoice_custody_and_ready_complete_exact_two(case, monkeypatch):
    """Only provider transport and process wake are replaced; all state is real."""
    import preparation_piece_operations as operations
    import supplier_receiving_routes as receiving
    import supplier_receipt_employee_custody as custody
    from order_cost_authority import load_order_cost_authority, resolve_order_line_cost
    evidence = await preview(case)
    adoption_payload = command(case, evidence)
    response = await case.web.post(case.path + "/apply", json=adoption_payload)
    assert response.status_code == 200, response.text
    ids = response.json()["piece_ids"]
    physical = [await case.db[adoption.PIECES].find_one({"piece_id": pid}) for pid in ids]
    actor = await initialize_synthetic_financial_authority(case.db)
    async def current_actor():
        return deepcopy(actor)
    # The real production custody wrapper is restored after this test.
    monkeypatch.setattr(receiving, "supplier_service_completion_update", receiving.supplier_service_completion_update)
    monkeypatch.setattr(custody, "_INSTALLED", custody._INSTALLED)
    custody.install_supplier_receipt_employee_custody()
    case.app.include_router(receiving.make_supplier_receiving_router(case.db, current_actor))
    case.app.include_router(operations.make_preparation_piece_operations_router(case.db, current_actor))
    provider_reads, wakeups = [], []
    async def provider(db, owner, method, path, **kwargs):
        assert owner == OWNER and method == "GET" and path == "/orders/synthetic-native-order"
        provider_reads.append(path)
        return {"data": deepcopy(case.raw)}
    async def wake(**kwargs):
        wakeups.append(kwargs)
        return None
    monkeypatch.setattr("order_engine.shipping_label_service.call_salla", provider)
    monkeypatch.setattr("background_worker_process.wake_background_process", wake)
    ready_path = lambda pid: f"/preparation-work-v1/assembly/pieces/{pid}/ready"
    premature = await case.web.post(ready_path(ids[0]), json={"client_request_id": "ready-before-receipt"})
    assert premature.status_code == 409, premature.text
    opened = await case.web.post("/supplier-receiving-v1/sessions", json={
        "supplier_id": "synthetic-supplier", "client_request_id": "adoption-receiving-session"})
    assert opened.status_code == 201, opened.text
    session = opened.json()["session"]
    scan_path = f'/supplier-receiving-v1/sessions/{session["id"]}/scan'
    retired = await case.web.post(scan_path, json={"barcode": case.old["barcode"], "client_request_id": "retired-parent-scan"})
    assert retired.status_code == 409, retired.text
    for piece in physical:
        scanned = await case.web.post(scan_path, json={"barcode": piece["barcode"],
            "client_request_id": "scan-" + piece["piece_id"]})
        assert scanned.status_code == 200 and not scanned.json().get("requires_quantity_selection"), scanned.text
    closed = await case.web.post(f'/supplier-receiving-v1/sessions/{session["id"]}/close', json={
        "expected_supplier_id": "synthetic-supplier", "confirmed_total_halalas": 10000,
        "invoice_lines": [{"piece_ids": ids, "product_unit_price_halalas": 5000, "services": []}]})
    assert closed.status_code == 200, closed.text
    invoice = closed.json()["supplier_invoice"]
    assert invoice["piece_count"] == 2 and invoice["total_halalas"] == 10000
    assert sum(line["quantity"] for line in invoice["lines"]) == 2
    for index, pid in enumerate(ids):
        received = await case.web.post(f"/preparation-work-v1/receiving/pieces/{pid}/receive", json={
            "client_request_id": "custody-" + pid})
        assert received.status_code == 200, received.text
        assembled = await case.web.post(ready_path(pid), json={"client_request_id": "ready-" + pid})
        assert assembled.status_code == 200, assembled.text
        progress = assembled.json()["progress"]
        assert progress["total_count"] == 2 and progress["ready_count"] == index + 1
        assert progress["order_completed"] is (index == 1)
        if index == 0:
            assert await case.db.mezan_fulfillment_batches_v2.count_documents({}) == 0
    assert len(provider_reads) == 4 and len(wakeups) == 1
    assert await case.db.mezan_supplier_invoices_v2.count_documents({"user_id": OWNER}) == 1
    assert await case.db.accounting_general_ledger_v2.count_documents({"user_id": OWNER, "entry_type": "supplier_invoice"}) == 2
    assert await case.db.mezan_inventory_receipts_v2.count_documents({}) == 0
    assert await case.db.mezan_purchase_invoices_v2.count_documents({}) == 0
    unit = await case.db[adoption.UNITS].find_one({"order_id": ORDER})
    plan = await case.db[adoption.PLANS].find_one({"order_id": ORDER})
    assert unit["state"] == "consumed"
    canonical = await get_order(MongoOrderRepository(case.db), user_id=OWNER, order_number=ORDER)
    sale_line = _component_order_lines(canonical, {})[0]
    authority = await load_order_cost_authority(case.db, OWNER, [{"order_number": ORDER, "products": [sale_line]}])
    cost = resolve_order_line_cost(sale_line, order_number=ORDER, authority=authority, catalog_cost={})
    assert cost["line_total"] == 100 and cost["cost_stage"] == "actual"
    workflow = await case.db[adoption.WORKFLOWS].find_one({"order_number": ORDER})
    assert workflow["assembly_piece_count"] == 2 and workflow["assembly_ready_piece_count"] == 2
    assert workflow["stage"] == "completed" and workflow["assembly_status"] == "completed"
    source = await case.db[adoption.SOURCES].find_one({"order_number": ORDER})
    assert source["raw_by_source"]["salla_direct"]["items"][0]["quantity"] == 1
    assert source["raw_by_source"]["salla_direct"]["amounts"] == case.raw["amounts"]
    assert (await case.db[adoption.LIFECYCLES].find_one({"order_number": ORDER}))["bundle_intake_snapshots"][case.line] is None
    before_events = await case.db[adoption.PIECE_EVENTS].count_documents({"event_type": "assembly_piece_marked_ready"})
    replay = await case.web.post(ready_path(ids[-1]), json={"client_request_id": "ready-" + ids[-1]})
    assert replay.status_code == 200 and replay.json()["idempotent"] is True, replay.text
    assert await case.db[adoption.PIECE_EVENTS].count_documents({"event_type": "assembly_piece_marked_ready"}) == before_events == 2
    replay = await case.web.post(case.path + "/apply", json=adoption_payload)
    assert replay.status_code == 200 and replay.json()["duplicate"] is True, replay.text

