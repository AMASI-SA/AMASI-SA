from copy import deepcopy
from types import SimpleNamespace
import pytest
from salla_integration.service import SallaError
from integrations_control_center.google_live_category_reader import preview_live_category, CategoryProofUnavailable


class Integration:
    def __init__(self):
        self.row = {"store_id": 9, "store_domain": "https://store.test", "status": "connected", "token_revision": "opaque"}

    async def find_one(self, query, projection):
        assert query == {"user_id": "owner"}
        assert not any("token_encrypted" in key or "secret" in key for key in projection)
        return deepcopy(self.row)


def envelope(data):
    return {"status": 200, "success": True, "data": data}


def paged(rows, **patch):
    return {**envelope(rows), "pagination": {"count": len(rows), "total": len(rows), "perPage": 60, "currentPage": 1, "totalPages": 1, **patch}}


class Native:
    def __init__(self):
        self.calls = []
        self.responses = {"/store/info": envelope({"id": 9, "domain": "https://store.test"}),
            "/categories/8": envelope({"id": 8, "status": "active", "urls": {"customer": "https://store.test/winter"}}),
            "/categories/8/products": paged([{"id": 1}, {"id": 2, "status": "hidden"}, {"id": 3, "status": "out"}]),
            "/categories": paged([{"id": 8, "status": "active", "urls": {"customer": "https://store.test/winter"}}])}

    async def __call__(self, db, owner, method, path, *, params):
        assert owner == "owner" and method == "GET"
        self.calls.append((path, params))
        response = self.responses[path]
        if isinstance(response, Exception):
            raise response
        return deepcopy(response)


def setup():
    return SimpleNamespace(salla_integrations=Integration()), Native(), {"category_ids": ["8"], "excluded_product_ids": ["1"]}


@pytest.mark.asyncio
async def test_missing_stored_scope_does_not_block_native_success_or_invent_inventory():
    db, caller, fields = setup()
    result = await preview_live_category(db, owner_id="owner", fields=fields, caller=caller)
    assert result["pagination_traversal_complete"] is True
    assert result["provider_revision"] is None and result["remote_snapshot_atomic"] is False
    assert result["products"][0]["excluded"] is True
    assert [p["product_status"] for p in result["products"]] == ["unknown", "hidden", "out"]
    assert all(p["inventory"] == "unproved" and p["eligible_for_ad_selection"] is False for p in result["products"])
    assert [p for p, _ in caller.calls] == ["/store/info", "/categories/8", "/categories/8/products", "/store/info"]


@pytest.mark.asyncio
async def test_only_provider_exact_owned_customer_url_can_resolve_category():
    db, caller, fields = setup()
    fields.update(category_ids=[], category_url="https://store.test/winter")
    result = await preview_live_category(db, owner_id="owner", fields=fields, caller=caller)
    assert result["category_id"] == "8"
    assert all(not path.startswith("https:") for path, _ in caller.calls)
    fields["category_url"] = "https://evil.test/winter"
    caller.calls.clear()
    with pytest.raises(ValueError):
        await preview_live_category(db, owner_id="owner", fields=fields, caller=caller)
    assert caller.calls == []


@pytest.mark.asyncio
async def test_explicit_permission_error_is_unproved_not_forced_relink():
    db, caller, fields = setup()
    caller.responses["/categories/8"] = SallaError("private upstream message", status_code=403)
    with pytest.raises(CategoryProofUnavailable, match="^permission_unproved$"):
        await preview_live_category(db, owner_id="owner", fields=fields, caller=caller)
    assert db.salla_integrations.row["status"] == "connected"


@pytest.mark.asyncio
async def test_scope_not_in_saved_plan_and_integration_change_fail_closed():
    db, caller, fields = setup()
    with pytest.raises(ValueError, match="category_not_in_saved_plan"):
        await preview_live_category(db, owner_id="owner", fields=fields, category_id="99", caller=caller)
    assert caller.calls == []
    original = caller.__call__
    async def changing(*args, **kwargs):
        result = await original(*args, **kwargs)
        if args[3] == "/categories/8/products":
            db.salla_integrations.row["token_revision"] = "changed"
        return result
    with pytest.raises(CategoryProofUnavailable, match="store_connection_changed"):
        await preview_live_category(db, owner_id="owner", fields=fields, caller=changing)


@pytest.mark.asyncio
async def test_bound_does_not_expand_declared_oversized_category_or_follow_next_url():
    db, caller, fields = setup()
    caller.responses["/categories/8/products"] = paged([{"id": i + 1} for i in range(60)], total=600, totalPages=10, links={"next": "https://evil.test"})
    result = await preview_live_category(db, owner_id="owner", fields=fields, caller=caller)
    assert result["products_observed"] == 60 and result["pagination_traversal_complete"] is False
    assert len([p for p, _ in caller.calls if p.endswith("/products")]) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["total_drift", "duplicate", "repeated_page"])
async def test_multi_page_drift_and_duplicate_membership_do_not_claim_complete(failure):
    db, caller, fields = setup()
    original = caller.__call__
    async def multiple(db, owner, method, path, *, params):
        if path.endswith("/products"):
            page = params["page"]
            return paged([{"id": 1 if failure == "duplicate" else page}], perPage=1,
                total=3 if failure == "total_drift" and page == 2 else 2,
                totalPages=3 if failure == "total_drift" and page == 2 else 2,
                currentPage=1 if failure == "repeated_page" else page)
        return await original(db, owner, method, path, params=params)
    with pytest.raises(ValueError):
        await preview_live_category(db, owner_id="owner", fields=fields, caller=multiple)


@pytest.mark.asyncio
async def test_missing_category_visibility_is_unknown_never_defaults_active():
    db, caller, fields = setup()
    caller.responses["/categories/8"]["data"].pop("status")
    result = await preview_live_category(db, owner_id="owner", fields=fields, caller=caller)
    assert result["category_status"] == "unknown"
    assert all(p["category_hidden"] is None for p in result["products"])
    assert all(p["eligible_for_ad_selection"] is False for p in result["products"])
