"""Operational-card-only images, per-choice learning, and tenant privacy."""
import base64
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from mongomock_motor import AsyncMongoMockClient

from order_review_operational_images import (
    IMAGES, IMAGE_PREFIX, PREFERENCES, OperationalImageInput,
    _decode_image, preference_identity, upload_operational_image,
    learned_operational_image, remember_operational_image,
    make_order_review_operational_image_router,
)
from preparation_piece_operations import _workflow_assembly_pieces


def source(product="source-product-A", gift="نعم", engraving="WADHA"):
    return SimpleNamespace(
        product_id=product, variant_id=None, parent_product_id=None, sku="AMS000",
        order_item_id="item-1", color=None, size=None, material=None,
        options=[
            SimpleNamespace(name="هل تريد تغليف بوكس؟", value=gift),
            SimpleNamespace(name="الاسم", value=engraving),
        ],
    )


def spec(gift="نعم", name="WADHA"):
    return [
        {"name": "هل تريد تغليف بوكس؟", "key": "هل تريد تغليف بوكس؟", "value": gift},
        {"name": "الاسم", "key": "الاسم", "value": name},
    ]


def image():
    return OperationalImageInput(
        filename="operational-gift.png",
        content_type="image/png",
        data_base64=base64.b64encode(b"\x89PNG\r\n\x1a\nimage-content").decode(),
    )


def test_operational_image_options_never_reuse_customer_names():
    key_a, name_a, sig_a, values_a = preference_identity(source(engraving="WADHA"), "تغليف بوكس", spec(name="WADHA"))
    key_b, name_b, sig_b, values_b = preference_identity(source(engraving="SARA"), "تغليف بوكس", spec(name="SARA"))
    assert (key_a, name_a, sig_a, values_a) == (key_b, name_b, sig_b, values_b)
    assert "الاسم" not in values_a
    assert preference_identity(source(gift="لا"), "تغليف بوكس", spec(gift="لا"))[2] != sig_a
    assert preference_identity(source(product="B"), "تغليف بوكس", spec())[2] != sig_a
    assert preference_identity(source(), "كرت إهداء", spec())[2] != sig_a
    assert preference_identity(source(), "بوكس", [{"key": "الاسم", "name": "الاسم", "value": "private"}])[2] is None


def test_independent_upload_validation_rejects_bad_media_and_overflow():
    assert _decode_image(image())[1] == "image/png"
    with pytest.raises(HTTPException) as exc:
        _decode_image(OperationalImageInput(filename="bad.png", content_type="image/png", data_base64=base64.b64encode(b"not-an-image").decode()))
    assert exc.value.status_code == 422
    with pytest.raises(HTTPException):
        _decode_image(OperationalImageInput(filename="bad.jpg", content_type="image/svg+xml", data_base64="aaaa"))
    with pytest.raises(HTTPException):
        _decode_image(OperationalImageInput(filename="bad.webp", content_type="image/webp", data_base64=base64.b64encode(b"RIFF----evil").decode()))
    with pytest.raises(HTTPException):
        _decode_image(OperationalImageInput(filename="bad.png", content_type="image/png", data_base64="!!!"))


@pytest.mark.asyncio
async def test_uploaded_image_isolated_and_reused_only_for_same_product_choice():
    db = AsyncMongoMockClient()["isolated-operational"]
    uploaded = await upload_operational_image(
        db, user_id="merchant", actor_id="employee", source_item=source(),
        operational_name="تغليف بوكس", linked_specs=spec(),
        payload=image(), order_number="order-1", operational_item_id="op:1",
    )
    assert uploaded["url"].startswith(IMAGE_PREFIX)
    assert await db[IMAGES].count_documents({"user_id": "merchant"}) == 1
    assert await db["order_review_mezan_images"].count_documents({}) == 0
    assert await db["product_option_image_preferences"].count_documents({}) == 0
    assert await learned_operational_image(
        db, user_id="merchant", source_item=source(),
        operational_name="تغليف بوكس", linked_specs=spec(),
    ) is None
    await remember_operational_image(db, user_id="merchant", actor_id="employee", image=uploaded)
    assert await db[PREFERENCES].count_documents({"user_id": "merchant"}) == 1
    assert await learned_operational_image(
        db, user_id="merchant", source_item=source(engraving="OTHER"),
        operational_name="تغليف بوكس", linked_specs=spec(name="OTHER"),
    ) == uploaded["url"]
    assert await learned_operational_image(
        db, user_id="merchant", source_item=source(gift="لا"),
        operational_name="تغليف بوكس", linked_specs=spec(gift="لا"),
    ) is None
    assert await learned_operational_image(
        db, user_id="other", source_item=source(),
        operational_name="تغليف بوكس", linked_specs=spec(),
    ) is None


@pytest.mark.asyncio
async def test_operational_asset_endpoint_authorizes_assembly_but_not_other_merchants():
    db = AsyncMongoMockClient()["isolation"]
    uploaded = await upload_operational_image(
        db, user_id="merchant", actor_id="reviewer", source_item=source(),
        operational_name="بوكس", linked_specs=spec(), payload=image(),
        order_number="123", operational_item_id="op:123",
    )
    router = make_order_review_operational_image_router(db, lambda: None)
    endpoint = next(r.endpoint for r in router.routes if "/operational-images/" in r.path)
    image_id = uploaded["id"]
    user = {
        "id": "assembler", "role": "employee", "created_by": "merchant",
        "_session_client": "amasi_mobile", "_mobile_owner_id": "merchant",
        "_mobile_app_permissions": ["app.page.assembly_shipping"],
    }
    response = await endpoint(image_id, user=user)
    assert response.media_type == "image/png"
    assert response.body.startswith(b"\x89PNG")
    with pytest.raises(HTTPException) as exc:
        await endpoint(image_id, user={**user, "_mobile_owner_id": "other"})
    assert exc.value.status_code == 404
    with pytest.raises(HTTPException) as exc:
        await endpoint(image_id, user={**user, "_mobile_app_permissions": []})
    assert exc.value.status_code == 403
    with pytest.raises(HTTPException) as exc:
        await endpoint(image_id, user={"id": "driver", "role": "store_driver", "created_by": "merchant"})
    assert exc.value.status_code == 403


def test_operational_image_is_in_virtual_assembly_card_without_touching_original():
    workflow = {
        "operational_items": [{
            "operational_item_id": "op:abcd", "name": "تغليف بوكس",
            "source_order_item_id": "source-1",
            "image_url": "/api/order-reviews-v1/operational-images/example",
            "linked_specs": spec(),
        }],
        "items": [],
    }
    pieces = _workflow_assembly_pieces(workflow, order_number="123")
    assert len(pieces) == 1
    assert pieces[0]["image_url"] == workflow["operational_items"][0]["image_url"]
    assert pieces[0]["item_type"] == "internal_operational"
    assert pieces[0]["supplier_export"] is False
    assert pieces[0]["financial_item"] is False
