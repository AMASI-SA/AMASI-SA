import pytest

from salla_orders_v3.gateway import SallaOrdersGateway


@pytest.mark.asyncio
async def test_list_orders_follows_metadata_through_three_short_pages():
    calls = []

    async def provider(_db, _user_id, method, path, params=None, json=None):
        calls.append((method, path, dict(params or {})))
        page = params["page"]
        return {
            "data": [{"id": page, "reference_id": f"R{page}"}] * 30,
            "pagination": {
                "currentPage": page,
                "totalPages": 3,
                "perPage": 30,
                "links": {"next": f"page={page + 1}"} if page < 3 else {},
            },
        }

    gateway = SallaOrdersGateway(object(), call_provider=provider, sleep=lambda _: None)
    pages = [page async for page in gateway.iter_light_orders("owner-1")]

    assert len(pages) == 3
    assert [call[2]["page"] for call in calls] == [1, 2, 3]
    assert all(call[2]["per_page"] == 30 for call in calls)
    assert all(call[2]["format"] == "light" for call in calls)


@pytest.mark.asyncio
async def test_order_details_are_light_and_items_have_one_authoritative_endpoint():
    calls = []

    async def provider(_db, _user_id, method, path, params=None, json=None):
        calls.append((method, path, params))
        if path == "/orders/901":
            return {"data": {"id": 901, "reference_id": "3001"}}
        if path == "/orders/items":
            return {"data": [{"id": 7, "quantity": 1}]}
        raise AssertionError(path)

    gateway = SallaOrdersGateway(object(), call_provider=provider, sleep=lambda _: None)
    details = await gateway.get_light_order_details("owner-1", "901")
    items = await gateway.get_order_items("owner-1", "901")

    assert details["reference_id"] == "3001"
    assert items == [{"id": 7, "quantity": 1}]
    assert calls == [
        ("GET", "/orders/901", {"format": "light"}),
        ("GET", "/orders/items", {"order_id": "901"}),
    ]


@pytest.mark.asyncio
async def test_invalid_items_payload_is_not_confused_with_authoritative_empty_list():
    async def provider(_db, _user_id, method, path, params=None, json=None):
        return {"data": {"items": []}}

    gateway = SallaOrdersGateway(object(), call_provider=provider, sleep=lambda _: None)

    with pytest.raises(RuntimeError, match="invalid payload"):
        await gateway.get_order_items("owner-1", "901")
