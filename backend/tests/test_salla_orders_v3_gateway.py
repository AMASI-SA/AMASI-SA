import pytest

from salla_orders_v3.config import validate_provider_read_request
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
    assert all("updated_at_gt" not in call[2] for call in calls)

    _rows, first_page = await gateway.list_light_orders_page("owner-1", page=1)
    assert first_page.continuation is True


@pytest.mark.asyncio
async def test_list_orders_uses_only_documented_bounded_date_window():
    calls = []

    async def provider(_db, _user_id, method, path, params=None, json=None):
        calls.append((method, path, dict(params or {})))
        return {
            "data": [],
            "pagination": {
                "currentPage": 1,
                "totalPages": 1,
                "links": {},
            },
        }

    gateway = SallaOrdersGateway(object(), call_provider=provider, sleep=lambda _: None)
    pages = [
        page
        async for page in gateway.iter_light_orders(
            "owner-1",
            from_date="2026-09-01",
            to_date="2026-09-01",
        )
    ]

    assert pages == [[]]
    assert calls == [(
        "GET",
        "/orders",
        {
            "page": 1,
            "per_page": 30,
            "format": "light",
            "from_date": "2026-09-01",
            "to_date": "2026-09-01",
        },
    )]


@pytest.mark.asyncio
async def test_empty_window_accepts_explicit_zero_total_pages_as_exhausted():
    async def provider(_db, _user_id, method, path, params=None, json=None):
        return {
            "data": [],
            "pagination": {
                "currentPage": 1,
                "totalPages": 0,
                "links": {},
            },
        }

    gateway = SallaOrdersGateway(object(), call_provider=provider, sleep=lambda _: None)

    assert [page async for page in gateway.iter_light_orders("owner-1")] == [[]]


@pytest.mark.asyncio
async def test_empty_or_short_page_does_not_end_before_pagination_metadata():
    calls = []

    async def provider(_db, _user_id, method, path, params=None, json=None):
        page = params["page"]
        calls.append(page)
        rows = [] if page == 2 else [{"id": page, "reference_id": f"R{page}"}]
        return {
            "data": rows,
            "pagination": {
                "currentPage": page,
                "totalPages": 3,
                "links": {"next": f"page={page + 1}"} if page < 3 else {},
            },
        }

    gateway = SallaOrdersGateway(object(), call_provider=provider, sleep=lambda _: None)
    pages = [page async for page in gateway.iter_light_orders("owner-1")]

    assert calls == [1, 2, 3]
    assert pages == [
        [{"id": 1, "reference_id": "R1"}],
        [],
        [{"id": 3, "reference_id": "R3"}],
    ]


@pytest.mark.asyncio
async def test_list_orders_fails_closed_without_pagination_metadata():
    async def provider(_db, _user_id, method, path, params=None, json=None):
        return {"data": [{"id": 1, "reference_id": "R1"}]}

    gateway = SallaOrdersGateway(object(), call_provider=provider, sleep=lambda _: None)

    with pytest.raises(RuntimeError, match="pagination metadata"):
        _ = [page async for page in gateway.iter_light_orders("owner-1")]


@pytest.mark.asyncio
async def test_invalid_order_member_cannot_be_skipped_before_cursor_advances():
    async def provider(_db, _user_id, method, path, params=None, json=None):
        return {
            "data": [{"id": 1, "reference_id": "R1"}, None],
            "pagination": {
                "currentPage": 1,
                "totalPages": 1,
                "links": {},
            },
        }

    gateway = SallaOrdersGateway(object(), call_provider=provider, sleep=lambda _: None)

    with pytest.raises(RuntimeError, match="invalid order"):
        _ = [page async for page in gateway.iter_light_orders("owner-1")]


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


@pytest.mark.asyncio
async def test_invalid_item_member_is_not_confused_with_authoritative_empty_list():
    async def provider(_db, _user_id, method, path, params=None, json=None):
        return {"data": [None]}

    gateway = SallaOrdersGateway(object(), call_provider=provider, sleep=lambda _: None)

    with pytest.raises(RuntimeError, match="invalid item"):
        await gateway.get_order_items("owner-1", "901")


@pytest.mark.asyncio
async def test_empty_or_identity_less_item_is_not_authoritative():
    responses = iter(({"data": [{}]}, {"data": [{"options": {"size": "M"}}]}))

    async def provider(_db, _user_id, method, path, params=None, json=None):
        return next(responses)

    gateway = SallaOrdersGateway(object(), call_provider=provider, sleep=lambda _: None)

    with pytest.raises(RuntimeError, match="identity"):
        await gateway.get_order_items("owner-1", "901")
    with pytest.raises(RuntimeError, match="identity"):
        await gateway.get_order_items("owner-1", "901")


@pytest.mark.asyncio
async def test_provider_attempt_override_is_capped_by_reviewed_budget():
    calls = 0

    async def provider(_db, _user_id, method, path, params=None, json=None):
        nonlocal calls
        calls += 1
        raise RuntimeError("temporary failure")

    gateway = SallaOrdersGateway(
        object(),
        call_provider=provider,
        sleep=lambda _: None,
        max_attempts=999,
    )

    with pytest.raises(RuntimeError, match="temporary failure"):
        await gateway.get_order_items("owner-1", "901")
    assert calls == 3


@pytest.mark.asyncio
async def test_gateway_rejects_every_non_allowlisted_provider_path_and_method():
    async def provider(*args, **kwargs):
        raise AssertionError("provider must not be reached")

    gateway = SallaOrdersGateway(object(), call_provider=provider, sleep=lambda _: None)

    with pytest.raises(RuntimeError, match="not allowlisted"):
        await gateway._get("owner-1", "/orders/1/cancel")
    with pytest.raises(RuntimeError, match="not allowlisted"):
        await gateway._get("owner-1", "/products")
    with pytest.raises(RuntimeError, match="not allowlisted"):
        await gateway._get("owner-1", "/orders/%2Fproducts")
    with pytest.raises(RuntimeError, match="not allowlisted"):
        validate_provider_read_request("POST", "/orders")
