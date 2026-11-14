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


async def native_image_fixture(*, permissions=None, enabled=True, owner="merchant", role="viewer"):
    """Exercise the native permission bridge before the real asset route.

    Authentication supplies the employee identity here; live grants, merchant
    resolution, asset ownership and the HTTP response use production code.
    """
    from fastapi import FastAPI, Request
    from mobile_app_request_context import mobile_app_request_user

    db = AsyncMongoMockClient()["native-operational-image"]
    await db.users.insert_one({"id": owner, "role": "owner"})
    user = {
        "id": "assembler", "role": role, "created_by": owner,
        "_session_client": "amasi_mobile",
    }
    await db["mezan_mobile_app_access_v1"].insert_one({
        "owner_user_id": owner, "user_id": user["id"], "enabled": enabled,
        "permissions": ["app.page.assembly_shipping"] if permissions is None else permissions,
    })
    uploaded = await upload_operational_image(
        db, user_id="merchant", actor_id="reviewer", source_item=source(),
        operational_name="بوكس", linked_specs=spec(), payload=image(),
        order_number="fixture-order", operational_item_id="op:fixture",
    )

    async def current_user(request: Request):
        return await mobile_app_request_user(
            db, dict(user), path=request.url.path, method=request.method,
        )

    app = FastAPI()
    app.include_router(make_order_review_operational_image_router(db, current_user), prefix="/api")
    return app, db, user, uploaded


@pytest.mark.asyncio
@pytest.mark.parametrize("permission", [
    "app.page.assembly_shipping", "app.page.pending_review", "app.page.reviewed_preparation",
])
async def test_native_operational_image_http_read_passes_page_bridge(permission):
    from httpx import ASGITransport, AsyncClient

    app, _, _, uploaded = await native_image_fixture(permissions=[permission])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://test.invalid") as client:
        response = await client.get(uploaded["url"])
    assert response.status_code == 200, response.text
    assert response.content == base64.b64decode(image().data_base64)
    assert response.headers["content-type"] == "image/png"
    assert response.headers["cache-control"].startswith("private")


@pytest.mark.asyncio
@pytest.mark.parametrize("options,status,code", [
    ({"permissions": ["app.page.orders"]}, 403, "mobile_app_page_permission_required"),
    ({"enabled": False}, 403, "mobile_app_page_permission_required"),
    ({"owner": "another-merchant"}, 404, "operational_image_not_found"),
    ({"role": "store_driver"}, 403, "mobile_app_route_not_allowed"),
])
async def test_native_operational_image_http_read_preserves_grants_and_tenant(options, status, code):
    from httpx import ASGITransport, AsyncClient

    app, _, _, uploaded = await native_image_fixture(**options)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://test.invalid") as client:
        response = await client.get(uploaded["url"])
    assert response.status_code == status
    assert response.json()["detail"]["code"] == code


@pytest.mark.asyncio
@pytest.mark.parametrize("method,path", [
    ("POST", "/api/order-reviews-v1/operational-images/photo"),
    ("DELETE", "/api/order-reviews-v1/operational-images/photo"),
    ("PUT", "/api/order-reviews-v1/operational-images/photo"),
    ("GET", "/api/order-reviews-v1/operational-images"),
    ("GET", "/api/order-reviews-v1/operational-images/photo/metadata"),
    ("GET", "/api/order-reviews-v1/order-1"),
    ("POST", "/api/order-reviews-v1/order-1/operational-items"),
    ("POST", "/api/order-reviews-v1/order-1/operational-items/op-1/image"),
    ("DELETE", "/api/order-reviews-v1/order-1/operational-items/op-1"),
    ("POST", "/api/order-reviews-v1/order-1/items/item-1/approve"),
])
async def test_native_assembly_image_access_does_not_grant_review_or_image_writes(method, path):
    from mobile_app_request_context import mobile_app_request_user

    _, db, user, _ = await native_image_fixture()
    with pytest.raises(HTTPException) as exc:
        await mobile_app_request_user(db, user, path=path, method=method)
    assert exc.value.status_code == 403
    assert exc.value.detail["code"] == "mobile_app_page_permission_required"


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


def test_native_picker_converted_jpeg_requires_actual_content_type():
    # Android picker compression can keep original WEBP metadata while exporting JPEG.
    raw = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01"
    encoded = base64.b64encode(raw).decode()
    with pytest.raises(HTTPException) as exc:
        _decode_image(OperationalImageInput(filename="source.webp", content_type="image/webp", data_base64=encoded))
    assert exc.value.detail["code"] == "operational_image_signature_invalid"
    assert _decode_image(OperationalImageInput(filename="source.jpg", content_type="image/jpeg", data_base64=encoded)) == (raw, "image/jpeg")


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


@pytest.mark.asyncio
async def test_create_operational_item_uses_independent_photo_and_next_order_learns(monkeypatch):
    """Actual review mutation must freeze a photo only in the new internal card."""
    import order_review_routes as review
    from order_review_product_defaults import ACTION_CLOCKS
    raw_db = AsyncMongoMockClient()["operational-create-route"]
    class ClockOptions:
        # mongomock_motor delegates with_options to synchronous mongomock.
        # Keep this image-only fixture async while asserting the production
        # durability options; real replica-set outbox tests exercise the write.
        def with_options(self, *, write_concern):
            assert write_concern.document == {"w": "majority", "j": True}
            return self
        async def find_one_and_update(self, *args, **kwargs):
            return await raw_db[ACTION_CLOCKS].find_one_and_update(*args, **kwargs)
    class ImageFixtureDB:
        def __getitem__(self, name):
            return ClockOptions() if name == ACTION_CLOCKS else raw_db[name]
        def __getattr__(self, name):
            return getattr(raw_db, name)
    db = ImageFixtureDB()
    current_order = {"value": SimpleNamespace(order_number="1001", order_id="salla-1001")}
    original = source()
    original.name = "هودي شتوي"
    original.custom_fields = []

    async def get_order(_repository, *, user_id, order_number):
        assert user_id == "merchant"
        return current_order["value"]

    async def identities(*_args, **_kwargs):
        return [original]

    async def allowed(*_args, **_kwargs):
        return None

    async def detail(_db, tenant, order, *, local_only=False):
        assert local_only is True
        row = await _db[review.WORKFLOWS].find_one(
            {"user_id": tenant, "order_number": order.order_number}, {"_id": 0},
        )
        return {"revision": row["revision"], "operational_items": row["operational_items"]}

    monkeypatch.setattr(review, "get_order", get_order)
    monkeypatch.setattr(review, "_review_item_identities", identities)
    monkeypatch.setattr(review, "enforce_stage_instructions", allowed)
    monkeypatch.setattr(review, "_detail", detail)
    router = review.make_order_review_router(db, lambda: None)
    endpoint = next(
        x.endpoint for x in router.routes
        if x.path.endswith("/{order_number}/operational-items") and "POST" in x.methods
    )

    first = await endpoint(
        "1001",
        review.OperationalItemCreateRequest(
            expected_revision=0, source_order_item_id="item-1",
            name="تغليف بوكس", linked_spec_keys=["هل تريد تغليف بوكس؟"],
            operational_image=image(),
        ),
        user={"id": "merchant", "role": "owner"},
    )
    first_card = first["operational_items"][0]
    assert first_card["image_source"] == "operational_upload"
    assert first_card["image_url"].startswith(IMAGE_PREFIX)
    assert first_card["supplier_export"] is False
    assert first_card["financial_item"] is False
    assert await db[IMAGES].count_documents({"user_id": "merchant"}) == 1
    assert await db["order_review_mezan_images"].count_documents({}) == 0
    assert await db["product_option_image_preferences"].count_documents({}) == 0

    current_order["value"] = SimpleNamespace(order_number="1002", order_id="salla-1002")
    second = await endpoint(
        "1002",
        review.OperationalItemCreateRequest(
            expected_revision=0, source_order_item_id="item-1",
            name="تغليف بوكس", linked_spec_keys=["هل تريد تغليف بوكس؟"],
        ),
        user={"id": "merchant", "role": "owner"},
    )
    second_card = second["operational_items"][0]
    assert second_card["image_url"] == first_card["image_url"]
    assert second_card["image_source"] == "operational_learned"
    assert await db[IMAGES].count_documents({"user_id": "merchant"}) == 1
