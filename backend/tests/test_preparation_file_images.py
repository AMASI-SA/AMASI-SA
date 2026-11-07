"""Complete, bounded PDF image jobs; no provider or live customer data."""
import asyncio
import base64
import io
import os
from pathlib import Path
from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException
from fastapi import FastAPI
from mongomock_motor import AsyncMongoMockClient
from PIL import Image

import preparation_pdf_reference_layout as layout
from preparation_file_images import hydrate_preparation_images
import reviewed_preparation_batches as batches
from reviewed_preparation_v3 import stable_ready_item_id, stable_ready_unit_id
from preparation_file_registry import REGISTRY
from preparation_piece_operations import PIECES
from mobile_product_file_pdf import make_mobile_product_file_pdf_router


def image_bytes(color="red"):
    out = io.BytesIO()
    Image.new("RGB", (80, 80), color).save(out, format="PNG")
    return out.getvalue()


@pytest.mark.asyncio
async def test_reference_builder_fetches_eight_identical_images_once(monkeypatch):
    rows = [{"order_number": str(i), "order_item_id": "line", "selected_image_url": "https://cdn.salla.sa/shared.png"} for i in range(8)]
    async def canonical(_context, _planned):
        return deepcopy(rows)
    calls = []
    async def fetch(request):
        calls.append(str(request.url))
        await asyncio.sleep(.01)
        return httpx.Response(200, headers={"Content-Type": "image/png"}, content=image_bytes())
    original = httpx.AsyncClient
    monkeypatch.setattr(layout, "_ORIGINAL_BATCH_LINE_BUILDER", canonical)
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original(**kwargs, transport=httpx.MockTransport(fetch)))
    result = await layout.build_reference_batch_lines({"pairs": []}, [{"line": {}} for _ in rows])
    assert len(calls) == 1, "eight physical cards with one exact photo must share one in-flight download"
    assert len(result) == 8 and all(row.get("image_b64") for row in result)


@pytest.mark.asyncio
async def test_private_image_is_tenant_scoped_and_options_are_unchanged():
    db = AsyncMongoMockClient().images
    url = "/api/order-reviews-v1/mezan-images/synthetic"
    await db.order_review_mezan_images.insert_one({"user_id": "owner", "id": "synthetic",
        "data_base64": base64.b64encode(image_bytes()).decode()})
    fields = [{"spec_key": "customer-name", "name": "Name", "value": "Synthetic example"}]
    rows = [{"selected_image_url": url, "file_spec_fields": fields, "quantity": 1, "unit_indices": [7]}]
    result = await hydrate_preparation_images(db, "owner", rows)
    assert result[0]["image_b64"] and result[0]["file_spec_fields"] == fields
    assert result[0]["quantity"] == 1 and result[0]["unit_indices"] == [7]
    assert rows[0].get("image_b64") is None
    with pytest.raises(HTTPException) as refused:
        await hydrate_preparation_images(db, "foreign-owner", rows)
    assert refused.value.detail["code"] == "preparation_images_unavailable"


@pytest.mark.asyncio
async def test_failed_photo_blocks_partial_output_and_can_succeed_on_original_snapshot_retry():
    calls = []
    available = False
    async def fetch(request):
        calls.append(str(request.url))
        return httpx.Response(200 if available else 404,
            headers={"Content-Type": "image/png"}, content=image_bytes() if available else b"")
    rows = [{"selected_image_url": "https://cdn.salla.sa/one.png", "quantity": 1} for _ in range(8)]
    async with httpx.AsyncClient(transport=httpx.MockTransport(fetch)) as client:
        with pytest.raises(HTTPException) as refused:
            await hydrate_preparation_images(None, "owner", rows, client=client)
        assert refused.value.detail["missing_image_count"] == 8 and len(calls) == 1
        assert all(row.get("image_b64") is None for row in rows)
        available = True
        result = await hydrate_preparation_images(None, "owner", rows, client=client)
        assert all(row.get("image_b64") for row in result) and len(calls) == 2


@pytest.mark.asyncio
async def test_unsafe_image_reference_never_performs_network_io():
    calls = []
    async def fetch(request):
        calls.append(str(request.url))
        raise AssertionError("unsafe URL contacted")
    async with httpx.AsyncClient(transport=httpx.MockTransport(fetch)) as client:
        for url in ["http://127.0.0.1/private", "https://169.254.169.254/meta", "https://cdn.salla.sa@127.0.0.1/private",
                    "https://untrusted.test/image.png", "/api/order-reviews-v1/mezan-images/../foreign"]:
            with pytest.raises(HTTPException):
                await hydrate_preparation_images(None, "owner", [{"selected_image_url": url}], client=client)
    assert calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["missing_customer_options", "changed_revision"])
async def test_image_loading_never_bypasses_customer_options_or_revision_guards(monkeypatch, case):
    db = AsyncMongoMockClient().negative
    order = SimpleNamespace(order_id="synthetic-order", order_number="123456789", created_at=datetime.now(timezone.utc),
        source=SimpleNamespace(source_order_id="source-1"), shipping=SimpleNamespace(company="iMile"), items=[])
    workflow = {"order_number": order.order_number, "items": [{"order_item_id": "line-1", "quantity": 1,
        "specifications_snapshot": {"Customer name": "Synthetic example"}}]}
    line = {"identity_source": "reviewed_ready", "order_number": order.order_number, "order_item_id": "line-1",
        "quantity": 1, "line_index": 0, "file_spec_fields": [], "available_unit_indices": [1],
        "ready_item_identity": {"order_item_id": "line-1", "quantity": 1, "product_name": "Synthetic product",
            "selected_image_url": "https://cdn.salla.sa/image.png", "options": {"Customer name": "Synthetic example"}}}
    line["ready_item_id"] = stable_ready_item_id(line)
    line["ready_unit_id"] = stable_ready_unit_id(line, 1)
    context = {"pairs": [(order, workflow)], "truncated": False, "catalog": {"products": [{
        "group_key": line["ready_unit_id"], "name": "Synthetic product", "quantity": 1, "remaining_quantity": 1,
        "piece_level": True, "revision": "a" * 64, "source_lines": [line]}]}}
    async def load(*args, **kwargs):
        return deepcopy(context)
    async def no_image(*args, **kwargs):
        raise AssertionError("customer options must validate before image I/O")
    async def no_instructions(*args, **kwargs):
        return None
    def no_pdf(*args, **kwargs):
        raise AssertionError("invalid customer options must not generate PDF")
    monkeypatch.setattr(batches, "load_reviewed_product_context", load)
    monkeypatch.setattr(batches, "enforce_stage_instructions", no_instructions)
    monkeypatch.setattr(batches, "generate_preparation_pdf", no_pdf)
    import preparation_file_images
    monkeypatch.setattr(preparation_file_images, "hydrate_preparation_images", no_image)
    app = FastAPI()
    app.include_router(batches.make_reviewed_preparation_batches_router(db, lambda: {"id": "owner", "role": "owner"}))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://synthetic.test") as http:
        response = await http.post("/reviewed-preparation-batches-v1/batches", json={"client_request_id": "negative-options-request",
            "selections": [{"group_key": line["ready_unit_id"], "quantity": 1, "revision": ("b" if case == "changed_revision" else "a") * 64}]})
    assert response.status_code == 409, response.text
    code = response.json()["detail"]["code"]
    assert code == ("reviewed_selection_stale" if case == "changed_revision" else "preparation_customer_options_snapshot_incomplete"), response.text
    assert await db[batches.BATCHES].count_documents({}) == 0
    assert await db[batches.PREPARATION_UNIT_ALLOCATIONS].count_documents({}) == 0
    assert await db[PIECES].count_documents({}) == 0 and await db[REGISTRY].count_documents({}) == 0


@pytest.mark.asyncio
async def test_old_numbered_file_recovers_all_photos_and_preserves_options_without_assignment_writes():
    import fitz
    db = AsyncMongoMockClient().pdf_download
    owner = {"id": "owner", "role": "owner"}
    lines = []
    for index in range(8):
        identity = f"synthetic-photo-{index}"
        await db.order_review_mezan_images.insert_one({"user_id": "owner", "id": identity,
            "data_base64": base64.b64encode(image_bytes(["red", "blue", "green", "orange", "purple", "yellow", "pink", "cyan"][index])).decode()})
        lines.append({"order_number": str(900000001 + index), "order_item_id": f"synthetic-line-{index}",
            "product_name": f"منتج تجريبي {index + 1}", "quantity": 1, "unit_indices": [1], "product_id": f"product-{index}",
            "selected_image_url": "/api/order-reviews-v1/mezan-images/" + identity, "image_b64": None,
            "order_date": "2026-11-06T10:00:00+03:00", "shipping_company": "iMile", "total_products_in_order": 1,
            "file_spec_fields": [{"spec_key": "customer name", "name": "الاسم", "value": "مثال تجريبي"},
                                 {"spec_key": "size", "name": "المقاس", "value": "M"}],
            "product_options": {"الاسم": "مثال تجريبي", "المقاس": "M"}})
    await db[batches.BATCHES].insert_one({"id": "synthetic-batch", "user_id": "owner", "status": "ready", "lines": lines})
    await db[REGISTRY].insert_one({"user_id": "owner", "batch_id": "synthetic-batch", "status": "ready",
        "file_number": "PF-SYNTHETIC-0001", "responsible_employee_id": "owner", "responsible_employee_name": "موظف تجريبي",
        "file_date": "2026-11-06"})
    before = {name: await db[name].find({}).to_list(None) for name in await db.list_collection_names()}
    app = FastAPI()
    app.include_router(make_mobile_product_file_pdf_router(db, lambda: owner))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://synthetic.test") as http:
        response = await http.get("/mobile-product-file-v1/synthetic-batch/pdf")
        assert response.status_code == 200, response.text
        assert response.content.startswith(b"%PDF")
        document = fitz.open(stream=response.content, filetype="pdf")
        assert document.page_count == 1
        images = document[0].get_images(full=True)
        assert len(images) >= 16, "all eight unique product photos and all eight physical QR cards must be embedded"
        assert before == {name: await db[name].find({}).to_list(None) for name in await db.list_collection_names()}
        output = os.environ.get("PREPARATION_PDF_QA_OUTPUT")
        if output:
            path = Path(output)
            assert path.is_absolute()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(response.content)
            document[0].get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False).save(str(path.with_suffix(".png")))
        await db.order_review_mezan_images.delete_one({"id": "synthetic-photo-0"})
        unavailable = await http.get("/mobile-product-file-v1/synthetic-batch/pdf")
        assert unavailable.status_code == 409 and unavailable.json()["detail"]["code"] == "preparation_images_unavailable"
        assert not unavailable.content.startswith(b"%PDF"), "missing photos must not produce a partial printable response"
    assert await db[PIECES].count_documents({}) == 0 and await db[batches.PREPARATION_UNIT_ALLOCATIONS].count_documents({}) == 0
