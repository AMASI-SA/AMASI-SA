from unittest.mock import AsyncMock, patch

import pytest

from salla_integration.sync import resync_single_order


class FakeCollection:
    def __init__(self, values):
        self._values = iter(values)
        self.find_one = AsyncMock(side_effect=self._find_one)
        self.update_one = AsyncMock()

    async def _find_one(self, *args, **kwargs):
        return next(self._values, None)


class FakeInbox:
    def __init__(self):
        self.find_one = AsyncMock(return_value=None)
        self.update_one = AsyncMock()
        self.insert_one = AsyncMock()


class FakeDB:
    def __init__(self):
        self.unified_orders = FakeCollection([
            None,
            {
                "order_number": "272291728",
                "products": [],
                "total_amount": 100,
                "total_product_cost": 0,
            },
            {
                "order_number": "272291728",
                "total_amount": 100,
            },
        ])
        self.integration_inbox = FakeInbox()


@pytest.mark.asyncio
async def test_resync_returns_safe_raw_shape_only():
    db = FakeDB()

    raw = {
        "id": 1,
        "reference_id": "272291728",
        "status": {
            "name": "مراجعة",
            "slug": "under_review",
        },
        "products": [
            {
                "id": 55,
                "name": "secret product name",
                "options": [{"name": "secret", "value": "secret"}],
            },
        ],
    }

    doc = {
        "order_number": "272291728",
        "order_status": "مراجعة",
        "order_status_slug": "under_review",
        "products": [],
    }

    with (
        patch(
            "salla_integration.sync._fetch_salla_order_details",
            new=AsyncMock(return_value=raw),
        ),
        patch(
            "salla_integration.sync._salla_order_to_doc",
            return_value=doc,
        ),
        patch(
            "salla_integration.sync.upsert_order",
            new=AsyncMock(return_value={"created": False}),
        ),
        patch(
            "salla_integration.sync._refresh_plan_b_status_snapshot",
            new=AsyncMock(return_value={"updated": True}),
        ),
        patch(
            "salla_integration.sync._record_order_adjustment",
            new=AsyncMock(return_value=None),
        ),
    ):
        result = await resync_single_order(
            db,
            "owner-1",
            "272291728",
        )

    shape = result["salla_raw_shape"]

    assert result["ok"] is True
    assert "products" in shape["top_level_keys"]
    assert shape["candidate_containers"]["products"]["type"] == "list"
    assert shape["candidate_containers"]["products"]["count"] == 1
    assert shape["candidate_containers"]["products"]["first_item_keys"] == [
        "id",
        "name",
        "options",
    ]

    serialized = str(shape)
    assert "secret product name" not in serialized
    assert '"value": "secret"' not in serialized
