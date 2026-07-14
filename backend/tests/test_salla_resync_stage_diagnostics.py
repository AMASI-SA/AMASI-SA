from unittest.mock import AsyncMock, patch

import pytest

from salla_integration.sync import resync_single_order


class FakeCollection:
    def __init__(self):
        self.find_one = AsyncMock(return_value=None)
        self.update_one = AsyncMock()


class FakeDB:
    def __init__(self):
        self.unified_orders = FakeCollection()


@pytest.mark.asyncio
async def test_resync_reports_fetch_stage_without_raising():
    db = FakeDB()

    with patch(
        "salla_integration.sync._fetch_salla_order_details",
        new=AsyncMock(side_effect=RuntimeError("fetch exploded")),
    ):
        result = await resync_single_order(
            db,
            "owner-1",
            "272291728",
        )

    assert result["ok"] is False
    assert result["error"] == "resync_stage_failed"
    assert result["stage"] == "fetch_order_details"
    assert result["exception_type"] == "RuntimeError"
    assert result["exception_message"] == "fetch exploded"


@pytest.mark.asyncio
async def test_resync_reports_map_stage_without_exposing_payload():
    db = FakeDB()

    with (
        patch(
            "salla_integration.sync._fetch_salla_order_details",
            new=AsyncMock(return_value={"reference_id": "272291728"}),
        ),
        patch(
            "salla_integration.sync._salla_order_to_doc",
            side_effect=ValueError("invalid details shape"),
        ),
    ):
        result = await resync_single_order(
            db,
            "owner-1",
            "272291728",
        )

    assert result["ok"] is False
    assert result["stage"] == "map_order"
    assert result["exception_type"] == "ValueError"
    assert "reference_id" not in str(result)
