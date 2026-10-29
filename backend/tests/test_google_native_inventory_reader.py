from copy import deepcopy
from types import SimpleNamespace
import httpx
import pytest

from integrations_control_center.google_native_inventory_reader import selected_inventory_preview
from tests.test_google_native_inventory_proof import raw


class Integration:
    def __init__(self):
        self.row = {"store_id": 9, "store_domain": "https://store.test", "status": "connected", "token_revision": "opaque"}
    async def find_one(self, query, projection):
        assert query == {"user_id": "owner"}
        return deepcopy(self.row)


def envelope(data):
    return {"success": True, "status": 200, "data": data}


def page(rows, **changes):
    return envelope(rows) | {"pagination": {"currentPage": 1, "perPage": 60, "totalPages": 1, "count": len(rows), "total": len(rows), **changes}}


class Native:
    def __init__(self):
        self.calls = []
        self.responses = {"/store/info": envelope({"id": 9, "domain": "https://store.test"}),
            "/categories/8": envelope({"id": 8, "status": "active"}),
            "/categories/8/products": page([{"id": 1}]), "/products/1": envelope(raw()),
            "/products/1/variants": page([{"id": 7, "stock_quantity": 2}])}
    async def __call__(self, db, owner, method, path, *, params):
        assert owner == "owner" and method == "GET"
        self.calls.append((path, params))
        result = self.responses[path]
        if isinstance(result, Exception):
            raise result
        return deepcopy(result)


def setup():
    return SimpleNamespace(salla_integrations=Integration()), Native(), {"category_ids": ["8"], "excluded_product_ids": []}


async def read(db, caller, fields, ids=None):
    return await selected_inventory_preview(db, owner_id="owner", fields=fields, category_id="8", selected_ids=ids or ["1"], caller=caller)


@pytest.mark.asyncio
async def test_fresh_category_reproof_and_product_beforeafter_scope():
    db, native, fields = setup()
    result = await read(db, native, fields)
    assert result["products"][0]["variants"][0]["stock"]["state"] == "positive"
    assert result["globally_ready"] is False and result["execution_authorized"] is False
    assert result["products"][0]["simple_product_observed"] is False
    assert [p for p, _ in native.calls] == ["/store/info", "/categories/8", "/categories/8/products", "/store/info",
        "/products/1", "/products/1/variants", "/products/1", "/store/info"]


@pytest.mark.asyncio
async def test_excluded_or_unobserved_selection_never_reads_product_details():
    db, native, fields = setup()
    fields["excluded_product_ids"] = ["1"]
    with pytest.raises(ValueError):
        await read(db, native, fields)
    assert native.calls == []
    fields["excluded_product_ids"] = []
    with pytest.raises(ValueError, match="membership_unproved"):
        await read(db, native, fields, ["2"])
    assert all(not p.startswith("/products/") for p, _ in native.calls)


@pytest.mark.asyncio
async def test_explicit_simple_observation_requires_native_empty_variant_page():
    db, native, fields = setup()
    native.responses["/products/1/variants"] = page([])
    result = await read(db, native, fields)
    assert result["products"][0]["simple_product_observed"] is True
    assert result["merchant_mapping_verified"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["transport", "oversized", "wrong_id", "wrong_url", "variant_duplicate"])
async def test_unproved_native_source_fails_closed(failure):
    db, native, fields = setup()
    if failure == "transport":
        native.responses["/products/1"] = httpx.ReadTimeout("private detail")
    elif failure == "oversized":
        native.responses["/products/1"]["extra"] = "x" * 600000
    elif failure == "wrong_id":
        native.responses["/products/1"]["data"]["id"] = 2
    elif failure == "wrong_url":
        native.responses["/products/1"]["data"]["urls"]["customer"] = "https://evil.test/item"
    else:
        native.responses["/products/1/variants"] = page([{"id": 7}, {"id": 7}])
    with pytest.raises(ValueError):
        await read(db, native, fields)


@pytest.mark.asyncio
async def test_outer_connection_revision_covers_entire_product_flow():
    db, native, fields = setup()
    async def change(*args, **kwargs):
        result = await native(*args, **kwargs)
        if args[3] == "/products/1/variants":
            db.salla_integrations.row["token_revision"] = "changed"
        return result
    with pytest.raises(ValueError, match="store_connection_changed"):
        await read(db, change, fields)


@pytest.mark.asyncio
async def test_declared_three_pages_stops_after_first_and_never_claims_complete_variants():
    db, native, fields = setup()
    native.responses["/products/1/variants"] = page([{"id": i + 1, "stock_quantity": 1} for i in range(60)], total=180, totalPages=3)
    result = await read(db, native, fields)
    assert len(result["products"][0]["variants"]) == 60
    assert result["products"][0]["variant_traversal_complete"] is False
    assert len([p for p, _ in native.calls if p.endswith("/variants")]) == 1


@pytest.mark.asyncio
async def test_more_than_five_selection_is_rejected_before_native_call():
    db, native, fields = setup()
    with pytest.raises(ValueError):
        await read(db, native, fields, [str(i) for i in range(1, 7)])
    assert native.calls == []
