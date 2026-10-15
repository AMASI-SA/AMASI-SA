from __future__ import annotations

from datetime import date

import pytest

from unified_marketing.commerce_carts import load_abandoned_cart_outcomes


class Cursor:
    def __init__(self, rows):
        self.rows = rows

    def limit(self, _limit):
        return self

    async def to_list(self, *, length):
        return self.rows[:length]


class Collection:
    def __init__(self, rows):
        self.rows = rows

    def find(self, query, _projection):
        return Cursor([row for row in self.rows if row.get("user_id") == query["user_id"]])


class DB:
    def __init__(self, rows):
        self.rows = rows

    def __getitem__(self, _name):
        return Collection(self.rows)


@pytest.mark.asyncio
async def test_abandoned_carts_require_exact_campaign_and_compatible_platform():
    rows = [
        {
            "user_id": "u1",
            "cart_id": "exact",
            "purchased": False,
            "total": 250,
            "cart_updated_at": "2026-08-24T10:00:00+00:00",
            "attribution": {"platform": "snapchat", "campaign_id": "c1"},
            "items": [{"product_id": "p1", "name": "منتج", "quantity": 2, "total_price": 250}],
        },
        {
            "user_id": "u1",
            "cart_id": "recovered",
            "purchased": True,
            "total": 100,
            "cart_updated_at": "2026-08-24T11:00:00+00:00",
            "attribution": {"platform": "snapchat", "campaign_id": "c1"},
            "items": [],
        },
        {
            "user_id": "u1",
            "cart_id": "direct",
            "purchased": False,
            "total": 90,
            "cart_updated_at": "2026-08-24T12:00:00+00:00",
            "attribution": {},
            "items": [],
        },
        {
            "user_id": "u1",
            "cart_id": "foreign",
            "purchased": False,
            "total": 500,
            "cart_updated_at": "2026-08-24T13:00:00+00:00",
            "attribution": {"platform": "meta", "campaign_id": "c1"},
            "items": [],
        },
    ]
    result = await load_abandoned_cart_outcomes(
        DB(rows),
        "u1",
        provider="snapchat_ads",
        campaign_ids=["c1"],
        date_from=date(2026, 8, 24),
        date_to=date(2026, 8, 24),
    )

    campaign = result["by_campaign"]["c1"]
    assert campaign["abandoned_carts"] == 1
    assert campaign["recovered_carts"] == 1
    assert campaign["abandoned_value_sar"] == 250
    assert campaign["top_products"][0]["product_id"] == "p1"
    assert result["store_level"]["abandoned_carts"] == 1
    assert result["coverage"]["foreign_platform_counts"] == {"meta_ads": 1}
    assert result["coverage"]["store_level_is_not_campaign_revenue"] is True
