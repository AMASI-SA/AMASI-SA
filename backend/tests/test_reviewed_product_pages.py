"""Grouped previews defer immutable selectable-piece details until Show."""
import httpx
import pytest
from fastapi import FastAPI

import reviewed_products_catalog as catalog
from test_supplier_dispatch_waiting_current_status import db, seed_order, OWNER

pytestmark = pytest.mark.asyncio


async def seed(db, count):
    for index in range(count):
        number = f"A{index:03}"
        await seed_order(db, number=number, current="reviewed")
        await db.raw[catalog.WORKFLOWS].insert_one({"user_id": OWNER["id"], "order_number": number,
            "stage": "reviewed", "reviewed_at": "2026-11-01T10:00:00+03:00", "revision": 1,
            "items": [{"order_item_id": f"salla:{number}:line-{number}", "product_id": "p",
                "product_key": "product:p", "product_name": "Synthetic product", "quantity": 1,
                "sku": "P-1", "supplier_export": True, "review_status": "reviewed"}]})


async def request(db, path, **params):
    app = FastAPI()
    app.include_router(catalog.make_reviewed_products_catalog_router(db, lambda: OWNER))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://synthetic.test") as http:
        return await http.get("/reviewed-products-v1/" + path, params=params)


async def test_group_preview_does_not_map_orders_or_expand_customer_pieces(db, monkeypatch):
    await seed(db, 23)
    async def forbidden(*_args, **_kwargs):
        raise AssertionError("preview must not map individual OrderDTOs")
    monkeypatch.setattr(catalog, "get_orders", forbidden)
    response = await request(db, "groups")
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["selection_grain"] == "group_preview"
    assert [row["key"] for row in result["groups"]] == ["product:p"]
    assert "source_lines" not in result["groups"][0] and "revision" not in result["groups"][0]


async def test_group_pages_keep_exact_unit_revisions_and_bounded_order_hydration(db, monkeypatch):
    await seed(db, 23)
    original = catalog.get_orders
    scopes = []
    async def observe(repository, **kwargs):
        scopes.append(kwargs["order_numbers"])
        assert kwargs["user_id"] == OWNER["id"] and len(kwargs["order_numbers"]) <= 10
        return await original(repository, **kwargs)
    monkeypatch.setattr(catalog, "get_orders", observe)
    after = ""; rows = []; seen_cursors = set()
    for _ in range(6):
        response = await request(db, "group-page", group_key="product:p", after=after)
        assert response.status_code == 200, response.text
        page = response.json()
        assert page["selection_grain"] == "physical_piece" and len(page["products"]) <= 10
        for row in page["products"]:
            assert row["piece_level"] is True and row["remaining_quantity"] == 1
            assert row["revision"] and row["group_key"] == row["ready_unit_id"]
            assert len(row["source_lines"]) == 1
        rows.extend(page["products"])
        after = page["next_cursor"]
        if not after:
            break
        assert after not in seen_cursors
        seen_cursors.add(after)
    assert len(rows) == len({row["ready_unit_id"] for row in rows}) == 23
    assert len(scopes) == 3


async def test_group_page_cursor_rejects_malformed_input_before_catalog_reads(db):
    response = await request(db, "group-page", group_key="product:p", after="bad-token")
    assert response.status_code == 400
