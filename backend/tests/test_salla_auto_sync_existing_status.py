"""Regression tests for Salla light-list status reconciliation on existing orders."""
from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from salla_integration import auto_sync


class FakeCollection:
    def __init__(self, doc: dict):
        self.doc = deepcopy(doc)
        self.updates: list[tuple[dict, dict]] = []

    async def find_one(self, query, projection=None):
        if (
            str(query.get("user_id")) == str(self.doc.get("user_id"))
            and str(query.get("order_number")) == str(self.doc.get("order_number"))
        ):
            return deepcopy(self.doc)
        return None

    async def update_one(self, query, update):
        self.updates.append((deepcopy(query), deepcopy(update)))
        return SimpleNamespace(matched_count=1, modified_count=1)


class FakeDB:
    def __init__(self, doc: dict):
        self.unified_orders = FakeCollection(doc)


def reviewed_light_order() -> dict:
    return {
        "id": 901,
        "reference_id": "289000001",
        "updated_at": "2026-09-27T19:00:00+03:00",
        "status": {
            "id": 566146469,
            "name": "بإنتظار المراجعة",
            "slug": "under_review",
            "customized": {
                "id": 1539366206,
                "name": "تم المراجعة",
            },
        },
    }


def test_status_values_prefers_salla_customized_child_over_parent_name():
    name, slug, raw = auto_sync._status_values(reviewed_light_order())

    assert name == "تم المراجعة"
    assert slug == "under_review"
    assert raw["name"] == "بإنتظار المراجعة"
    assert raw["customized"]["name"] == "تم المراجعة"


def test_status_values_preserves_actual_waiting_child():
    row = reviewed_light_order()
    row["status"]["customized"] = {
        "id": 780246489,
        "name": "بإنتظار المراجعة",
    }

    name, slug, _ = auto_sync._status_values(row)

    assert name == "بإنتظار المراجعة"
    assert slug == "under_review"


@pytest.mark.asyncio
async def test_recent_existing_order_reconciles_status_only(monkeypatch):
    db = FakeDB({
        "user_id": "merchant",
        "order_number": "289000001",
        "order_status": "بإنتظار المراجعة",
        "order_status_slug": "under_review",
        "products": [{"sku": "KEEP-ME"}],
        "total_amount": 777.0,
    })

    fetch_items = AsyncMock(side_effect=AssertionError(
        "existing-order status reconciliation must not fetch/replace items"
    ))
    snapshot = AsyncMock()
    monkeypatch.setattr(auto_sync, "_fetch_salla_order_items", fetch_items)
    monkeypatch.setattr(auto_sync, "_refresh_plan_b_status_snapshot", snapshot)

    assert await auto_sync._sync_light_order(
        db,
        "merchant",
        reviewed_light_order(),
    ) is True

    fetch_items.assert_not_awaited()
    snapshot.assert_awaited_once()

    assert len(db.unified_orders.updates) == 1
    query, update = db.unified_orders.updates[0]
    assert query == {
        "user_id": "merchant",
        "order_number": "289000001",
    }

    patch = update["$set"]
    assert patch["order_status"] == "تم المراجعة"
    assert patch["order_status_slug"] == "under_review"
    assert patch["raw_by_source.salla_direct.status"]["customized"]["name"] == "تم المراجعة"
    assert patch["raw_by_source.salla_direct.updated_at"] == "2026-09-27T19:00:00+03:00"
    assert "last_salla_direct_status_reconciled_at" in patch

    # The reduced light payload is forbidden from touching business facts.
    forbidden = {
        "products",
        "items",
        "total_amount",
        "payment_status",
        "shipping_company",
        "shipping_method",
        "shipping_address",
        "preparation_pieces",
        "assignments",
    }
    assert forbidden.isdisjoint(patch)


@pytest.mark.asyncio
async def test_unchanged_existing_status_is_noop(monkeypatch):
    db = FakeDB({
        "user_id": "merchant",
        "order_number": "289000001",
        "order_status": "تم المراجعة",
        "order_status_slug": "under_review",
    })

    fetch_items = AsyncMock(side_effect=AssertionError(
        "unchanged existing order must not fetch items"
    ))
    snapshot = AsyncMock()
    monkeypatch.setattr(auto_sync, "_fetch_salla_order_items", fetch_items)
    monkeypatch.setattr(auto_sync, "_refresh_plan_b_status_snapshot", snapshot)

    assert await auto_sync._sync_light_order(
        db,
        "merchant",
        reviewed_light_order(),
    ) is True

    assert db.unified_orders.updates == []
    fetch_items.assert_not_awaited()
    snapshot.assert_not_awaited()


@pytest.mark.asyncio
async def test_other_under_review_custom_child_replaces_stale_waiting(monkeypatch):
    db = FakeDB({
        "user_id": "merchant",
        "order_number": "289000001",
        "order_status": "بإنتظار المراجعة",
        "order_status_slug": "under_review",
    })
    row = reviewed_light_order()
    row["status"]["customized"] = {
        "id": 2099877798,
        "name": "بانتظار تأكيد العميل",
    }

    monkeypatch.setattr(auto_sync, "_fetch_salla_order_items", AsyncMock())
    monkeypatch.setattr(auto_sync, "_refresh_plan_b_status_snapshot", AsyncMock())

    assert await auto_sync._sync_light_order(db, "merchant", row) is True
    patch = db.unified_orders.updates[0][1]["$set"]
    assert patch["order_status"] == "بانتظار تأكيد العميل"
    assert patch["order_status_slug"] == "under_review"
