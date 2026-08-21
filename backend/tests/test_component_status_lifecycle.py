from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from component_category_required_routes import make_component_category_required_router
from component_status_policy import (
    COMPONENT_STATUS_ACTIVE,
    COMPONENT_STATUS_INACTIVE,
    active_component_selector,
    component_is_active,
    component_status,
    require_active_component,
)
from component_workspace_cost_compat_routes import validate_group_members
from product_cost_revision import PRODUCT_COST_REVISIONS
from product_fulfillment_rules import PRODUCT_RESOURCE_BINDINGS
from product_option_cost_routes import AUDIT, BINDINGS, RESOURCES


def _matches(row, query):
    return all(row.get(key) == value for key, value in query.items())


class _Collection:
    def __init__(self, rows=None):
        self.rows = [dict(row) for row in (rows or [])]

    async def find_one(self, query, _projection=None):
        return next((dict(row) for row in self.rows if _matches(row, query)), None)

    async def count_documents(self, query):
        return sum(1 for row in self.rows if _matches(row, query))

    async def insert_one(self, row):
        self.rows.append(dict(row))
        return SimpleNamespace(inserted_id=row.get("id"))

    async def update_one(self, query, update, upsert=False, **_kwargs):
        row = next((item for item in self.rows if _matches(item, query)), None)
        if row is None and upsert:
            row = dict(query)
            self.rows.append(row)
            row.update(update.get("$setOnInsert", {}))
        if row is None:
            return SimpleNamespace(matched_count=0, modified_count=0)
        row.update(update.get("$set", {}))
        for key, value in update.get("$inc", {}).items():
            row[key] = row.get(key, 0) + value
        for key in update.get("$unset", {}):
            row.pop(key, None)
        return SimpleNamespace(matched_count=1, modified_count=1)


class _Database(dict):
    def __getitem__(self, name):
        if name not in self:
            self[name] = _Collection()
        return super().__getitem__(name)


def test_legacy_components_remain_active_without_a_migration():
    assert component_status({"id": "legacy"}) == COMPONENT_STATUS_ACTIVE
    assert component_is_active({"id": "legacy"}) is True
    assert active_component_selector() == {
        "status": {"$ne": COMPONENT_STATUS_INACTIVE},
    }


def test_stopped_component_is_rejected_for_new_links():
    with pytest.raises(HTTPException) as error:
        require_active_component({"id": "stopped", "status": "inactive"})

    assert error.value.status_code == 409
    assert error.value.detail == {"code": "component_inactive"}


def test_stopped_component_is_rejected_from_new_groups_but_not_deleted():
    rows = [
        {
            "id": "active-service",
            "name": "خدمة نشطة",
            "track_inventory": False,
            "category_ids": ["finishing"],
        },
        {
            "id": "stopped-service",
            "name": "خدمة موقوفة",
            "track_inventory": False,
            "category_ids": ["finishing"],
            "status": "inactive",
        },
    ]

    with pytest.raises(HTTPException) as error:
        validate_group_members(
            rows,
            resource_ids=["active-service", "stopped-service"],
            category_id="finishing",
            group_kind="service",
        )

    assert error.value.status_code == 409
    assert error.value.detail == {
        "code": "component_inactive",
        "resource_ids": ["stopped-service"],
    }
    assert len(rows) == 2


def test_component_api_exposes_soft_status_change_and_no_delete_route():
    router = make_component_category_required_router(
        SimpleNamespace(),
        lambda: {"id": "owner-1"},
    )
    methods_by_path = {
        (route.path, method)
        for route in router.routes
        for method in route.methods
    }

    assert ("/components-v2/{resource_id}/status", "PUT") in methods_by_path
    assert not any(
        method == "DELETE" and route.path == "/components-v2/{resource_id}"
        for route in router.routes
        for method in route.methods
    )


@pytest.mark.asyncio
async def test_soft_stop_preserves_existing_links_and_can_be_reactivated():
    db = _Database({
        RESOURCES: _Collection([{
            "id": "resource-1",
            "user_id": "owner-1",
            "name": "خدمة قائمة",
            "code": "SERVICE-1",
            "status": "active",
        }]),
        BINDINGS: _Collection([
            {"id": "option-link", "user_id": "owner-1", "resource_id": "resource-1"},
        ]),
        PRODUCT_RESOURCE_BINDINGS: _Collection([
            {"id": "product-link", "user_id": "owner-1", "resource_id": "resource-1"},
        ]),
        AUDIT: _Collection(),
        PRODUCT_COST_REVISIONS: _Collection(),
    })
    router = make_component_category_required_router(
        db,
        lambda: {"id": "owner-1"},
    )
    endpoint = next(
        route.endpoint
        for route in router.routes
        if route.path == "/components-v2/{resource_id}/status"
    )

    stopped = await endpoint(
        "resource-1",
        {"status": "inactive", "reason": "اختبار"},
        {"id": "owner-1"},
    )

    assert stopped["status"] == "inactive"
    assert stopped["impacted_bindings"] == 2
    assert len(db[RESOURCES].rows) == 1
    assert len(db[BINDINGS].rows) == 1
    assert len(db[PRODUCT_RESOURCE_BINDINGS].rows) == 1
    assert db[RESOURCES].rows[0]["status"] == "inactive"
    assert db[AUDIT].rows[-1]["historical_order_snapshots_unchanged"] is True

    reactivated = await endpoint(
        "resource-1",
        {"status": "active"},
        {"id": "owner-1"},
    )

    assert reactivated["status"] == "active"
    assert db[RESOURCES].rows[0]["status"] == "active"
    assert "stopped_at" not in db[RESOURCES].rows[0]
    assert len(db[BINDINGS].rows) == 1
    assert len(db[PRODUCT_RESOURCE_BINDINGS].rows) == 1
