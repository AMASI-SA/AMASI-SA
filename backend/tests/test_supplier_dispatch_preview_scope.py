"""Supplier preview reads the selected physical pieces, never the whole queue."""
from copy import deepcopy

import httpx
import pytest
from fastapi import FastAPI
from mongomock_motor import AsyncMongoMockClient

import preparation_supplier_dispatch as dispatch
import supplier_dispatch_order_group_guard as preview


def piece(identity, file_number="file-selected", **extra):
    return {"user_id": "merchant", "responsible_employee_id": "merchant",
            "piece_id": identity, "file_number": file_number, "status": "in_progress",
            "order_number": identity, "order_item_id": "item", "unit_index": 1,
            "group_key": "same-product", "product_id": "product", "services": [], **extra}


async def post(db, files):
    app = FastAPI()
    app.include_router(preview.make_supplier_dispatch_order_group_preview_router(
        db, lambda: {"id": "merchant", "role": "owner"}))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://synthetic.test") as http:
        return await http.post("/supplier-dispatch-v1/order-group-preview", json={"files": files})


@pytest.mark.asyncio
async def test_native_preview_materializes_only_selected_physical_piece(monkeypatch):
    db = AsyncMongoMockClient().preview
    await db[dispatch.PIECES].insert_many([
        piece("chosen"),
        *(piece(f"same-file-{index}") for index in range(250)),
        *(piece(f"other-file-{index}", f"other-file-{index}") for index in range(1000)),
    ])
    seen = []
    available = dispatch.piece_is_available_for_supplier_dispatch
    def observe(row):
        seen.append(row["piece_id"])
        return available(row)
    monkeypatch.setattr(dispatch, "piece_is_available_for_supplier_dispatch", observe)
    before = await db[dispatch.PIECES].find({}).to_list(None)
    response = await post(db, [{"file_number": "file-selected", "selections": [
        {"group_key": "piece:chosen", "quantity": 1}]}])
    assert response.status_code == 200, response.text
    assert response.json()["files"] == [{"file_number": "file-selected", "selections": [
        {"group_key": "piece:chosen", "quantity": 1}]}]
    assert seen == ["chosen"], "unselected pieces must not be loaded or processed by the preview"
    assert before == await db[dispatch.PIECES].find({}).to_list(None)


@pytest.mark.asyncio
async def test_legacy_product_quantity_reads_only_its_requested_file(monkeypatch):
    db = AsyncMongoMockClient().legacy_preview
    await db[dispatch.PIECES].insert_many([piece("one"), piece("two"), piece("unrelated", "other-file")])
    seen = []
    available = dispatch.piece_is_available_for_supplier_dispatch
    def observe(row):
        seen.append(row["piece_id"])
        return available(row)
    monkeypatch.setattr(dispatch, "piece_is_available_for_supplier_dispatch", observe)
    response = await post(db, [{"file_number": "file-selected", "selections": [
        {"group_key": "same-product::service:none", "quantity": 2}]}])
    assert response.status_code == 200, response.text
    assert response.json()["suggested_piece_count"] == 2
    assert set(seen) == {"one", "two"}


@pytest.mark.asyncio
@pytest.mark.parametrize("change", [
    {"user_id": "foreign"}, {"responsible_employee_id": "colleague"},
    {"supplier_dispatch_status": "sent"}, {"supplier_receiving_session_id": "another-session"},
    {"status": "cancelled"}, {"experiment_archived_at": "archived"},
    {"file_number": "wrong-file"},
])
async def test_narrow_preview_preserves_tenant_employee_status_and_file_guards(change):
    db = AsyncMongoMockClient().refused_preview
    row = piece("chosen", **change)
    await db[dispatch.PIECES].insert_one(deepcopy(row))
    response = await post(db, [{"file_number": "file-selected", "selections": [
        {"group_key": "piece:chosen", "quantity": 1}]}])
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "piece_quantity_exceeds_available"
