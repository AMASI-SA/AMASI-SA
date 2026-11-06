"""Supplier files repair only their selected immutable image snapshots."""
import base64
import io
import os
from pathlib import Path

import fitz
import pytest
from fastapi import HTTPException
from mongomock_motor import AsyncMongoMockClient
from PIL import Image

import supplier_dispatch_pdf as pdf


def photo(color):
    output = io.BytesIO()
    Image.new("RGB", (80, 80), color).save(output, format="PNG")
    return base64.b64encode(output.getvalue()).decode()


async def seed():
    db = AsyncMongoMockClient().supplier_pdf_images
    rows = []
    for index, color in enumerate(["red", "blue", "green", "orange", "purple", "yellow", "pink", "cyan"]):
        image_id = f"synthetic-photo-{index}"
        await db.order_review_mezan_images.insert_one({"user_id": "merchant", "id": image_id, "data_base64": photo(color)})
        rows.append({"order_number": str(900000001 + index), "order_item_id": f"item-{index}",
            "quantity": 1, "unit_index": 1, "unit_indices": [1], "product_id": f"product-{index}",
            "product_name": f"منتج تجريبي {index + 1}", "order_date": "2026-11-06", "shipping_company": "iMile",
            "selected_image_url": "/api/order-reviews-v1/mezan-images/" + image_id,
            "file_spec_fields": [{"spec_key": "size", "name": "المقاس", "value": "M"}], "image_b64": None})
        await db[pdf.PIECES].insert_one({"user_id": "merchant", "piece_id": f"piece-{index}", "batch_id": "batch",
            "order_number": rows[-1]["order_number"], "order_item_id": f"item-{index}", "unit_index": 1})
    await db[pdf.BATCHES].insert_one({"user_id": "merchant", "id": "batch", "lines": rows})
    dispatch = {"id": "synthetic-dispatch", "piece_ids": [f"piece-{index}" for index in range(8)],
        "supplier_id": "supplier", "supplier_name": "مورد تجريبي", "sent_by_name": "موظف تجريبي",
        "supplier_file_number": "SF-SYNTHETIC", "sent_at": "2026-11-06T12:00:00+03:00"}
    return db, dispatch


async def snapshot(db):
    return {name: await db[name].find({}).to_list(None) for name in await db.list_collection_names()}


@pytest.mark.asyncio
async def test_supplier_pdf_hydrates_all_eight_images_without_writes():
    db, dispatch = await seed()
    before = await snapshot(db)
    content = await pdf.build_supplier_dispatch_pdf(db, user_id="merchant", dispatch=dispatch)
    document = fitz.open(stream=content, filetype="pdf")
    assert document.page_count == 1
    assert len(document[0].get_images(full=True)) >= 16, "all eight product photos and eight permanent QR codes must print"
    assert await snapshot(db) == before, "printing cannot rewrite a reviewed batch, assignment, receipt or invoice"
    output = os.environ.get("SUPPLIER_PDF_QA_OUTPUT")
    if output:
        path = Path(output)
        assert path.is_absolute()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)


@pytest.mark.asyncio
async def test_supplier_pdf_refuses_a_partial_image_file_and_retries_same_dispatch():
    db, dispatch = await seed()
    await db.order_review_mezan_images.delete_one({"id": "synthetic-photo-0"})
    before = await snapshot(db)
    with pytest.raises(HTTPException) as refused:
        await pdf.build_supplier_dispatch_pdf(db, user_id="merchant", dispatch=dispatch)
    assert refused.value.detail["code"] == "preparation_images_unavailable"
    assert await snapshot(db) == before
    await db.order_review_mezan_images.insert_one({"user_id": "merchant", "id": "synthetic-photo-0", "data_base64": photo("red")})
    content = await pdf.build_supplier_dispatch_pdf(db, user_id="merchant", dispatch=dispatch)
    assert len(fitz.open(stream=content, filetype="pdf")[0].get_images(full=True)) >= 16


@pytest.mark.asyncio
async def test_supplier_pdf_never_loads_unselected_sibling_images():
    db, dispatch = await seed()
    dispatch["piece_ids"] = ["piece-1"]
    await db.order_review_mezan_images.delete_one({"id": "synthetic-photo-0"})
    content = await pdf.build_supplier_dispatch_pdf(db, user_id="merchant", dispatch=dispatch)
    assert len(fitz.open(stream=content, filetype="pdf")[0].get_images(full=True)) == 2
