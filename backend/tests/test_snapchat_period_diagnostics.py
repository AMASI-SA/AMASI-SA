from datetime import date

import pytest

from snapchat_v2.period_diagnostics import audit_period_partition


class Orders:
    def __init__(self, rows):
        self.rows = rows

    def find(self, query, projection):
        assert query["user_id"] == "tenant"
        assert projection["order_date"] == 1
        assert "products" not in projection
        assert "total_amount" not in projection
        assert "order_number" not in projection
        bounds = query["order_date"]
        self.selected = [row for row in self.rows
                         if bounds["$gte"] <= row["order_date"] <= bounds["$lte"]]
        return self

    async def to_list(self, length):
        return self.selected[:length]


class Settings:
    async def find_one(self, *_args):
        return {}


class DB:
    def __init__(self, rows):
        self.unified_orders = Orders(rows)
        self.settings = Settings()


async def run(rows, **kwargs):
    return await audit_period_partition(
        DB(rows), "tenant", date_from=date(2026, 8, 1),
        date_to=date(2026, 9, 17), split_on=date(2026, 8, 25),
        timezone_name="America/Los_Angeles", **kwargs,
    )


@pytest.mark.asyncio
async def test_explains_an_order_lost_only_when_window_is_partitioned():
    result = await run([{
        "order_date": "2026-08-20", "source": "snapchat",
        "raw_by_source": {"salla_direct": {"date": {
            "date": "2026-08-26 10:00:00", "timezone": "Asia/Riyadh",
        }}},
        "order_number": "must-not-appear", "total_amount": 123,
    }])
    assert result["all_orders"] == {"whole": 1, "left": 0, "right": 0, "gap": 1}
    assert result["explicit_snapchat_source"] == result["all_orders"]
    assert result["explanations"] == {"stored_date_outside_partition_query": 1}
    assert "must-not-appear" not in str(result)
    assert "123" not in str(result)


@pytest.mark.asyncio
async def test_timezone_boundary_and_date_only_fallback_reconcile():
    result = await run([
        {"order_date": "2026-08-25", "source": "snapchat", "created_at": "2026-08-25T03:00:00Z"},
        {"order_date": "2026-08-25", "source": "direct"},
    ])
    assert result["all_orders"] == {"whole": 2, "left": 1, "right": 1, "gap": 0}
    assert result["explicit_snapchat_source"] == {"whole": 1, "left": 1, "right": 0, "gap": 0}
    assert result["explanations"] == {}


@pytest.mark.asyncio
async def test_refuses_truncated_evidence():
    with pytest.raises(ValueError, match="row limit"):
        await run([{"order_date": "2026-08-20"}] * 3, max_rows=2)


@pytest.mark.asyncio
async def test_requires_nonempty_disjoint_partitions():
    with pytest.raises(ValueError, match="split"):
        await audit_period_partition(DB([]), "tenant", date_from=date(2026, 8, 1),
                                     date_to=date(2026, 8, 24), split_on=date(2026, 8, 25),
                                     timezone_name="UTC")
