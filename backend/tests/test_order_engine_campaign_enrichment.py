from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone

import pytest

from order_engine.campaign_enrichment import enrich_order_campaigns
from order_engine.models import OrderDTO, OrderSourceDTO


class FakeCollection:
    def __init__(self, name, db):
        self.name = name
        self.db = db

    async def find_one(self, query, projection=None, sort=None):
        self.db.reads.append(
            {
                "collection": self.name,
                "query": deepcopy(query),
                "projection": deepcopy(projection),
                "sort": deepcopy(sort),
            }
        )
        rows = self.db.rows.get(self.name, [])
        for row in rows:
            if all(row.get(key) == value for key, value in query.items()):
                return deepcopy(row)
        return None


class FakeDB:
    def __init__(self, rows=None):
        self.rows = deepcopy(rows or {})
        self.reads = []

    def __getitem__(self, name):
        return FakeCollection(name, self)


def make_order(campaign_id: str) -> OrderDTO:
    return OrderDTO(
        order_id="order-1",
        order_number="277000001",
        created_at=datetime(2026, 8, 14, tzinfo=timezone.utc),
        source=OrderSourceDTO(
            source="meta",
            utm_campaign=campaign_id,
            campaign_name=campaign_id,
        ),
    )


@pytest.mark.asyncio
async def test_numeric_salla_campaign_uses_meta_v2_name():
    campaign_id = "120248818886810420"
    db = FakeDB(
        {
            "mezan_meta_campaign_performance_daily_v2": [
                {
                    "user_id": "owner-1",
                    "campaign_id": campaign_id,
                    "campaign_name": "حملة ميتا للمبيعات",
                    "date": "2026-08-14",
                    "updated_at": "2026-08-14T10:00:00+00:00",
                }
            ],
            "meta_ads_daily": [
                {
                    "user_id": "owner-1",
                    "campaign_id": campaign_id,
                    "campaign_name": "اسم قديم",
                    "date": "2026-07-01",
                }
            ],
        }
    )

    result = await enrich_order_campaigns(
        db,
        user_id="owner-1",
        orders=[make_order(campaign_id)],
    )

    assert result[0].source.campaign_id == campaign_id
    assert result[0].source.campaign_name == "حملة ميتا للمبيعات"
    assert any(
        read["collection"] == "mezan_meta_campaign_performance_daily_v2"
        and read["query"] == {"user_id": "owner-1", "campaign_id": campaign_id}
        for read in db.reads
    )
    assert all(read["collection"] != "meta_ads_daily" for read in db.reads)
    assert not hasattr(db, "writes")


@pytest.mark.asyncio
async def test_missing_catalog_keeps_campaign_identity_without_fake_name():
    campaign_id = "120248818886810420"
    result = await enrich_order_campaigns(
        FakeDB(),
        user_id="owner-1",
        orders=[make_order(campaign_id)],
    )

    assert result[0].source.campaign_id == campaign_id
    assert result[0].source.campaign_name == campaign_id


@pytest.mark.asyncio
@pytest.mark.parametrize("name_present", [False, True])
async def test_long_salla_label_stays_a_label_through_canonical_order_enrichment(name_present):
    label = "حملة الشتاء التجريبية للفروات والهدايا"
    order = OrderDTO(
        order_id="label-order",
        order_number="label-order",
        created_at=datetime(2026, 11, 13, tzinfo=timezone.utc),
        source=OrderSourceDTO(
            source="tiktok",
            utm_source="tiktok",
            utm_campaign=label,
            campaign_name=label if name_present else None,
        ),
    )
    db = FakeDB()

    result = await enrich_order_campaigns(db, user_id="owner-1", orders=[order])

    assert result[0].source.campaign_id is None
    assert result[0].source.campaign_name == label
    assert result[0].source.utm_campaign == label
    assert result[0].source.utm_source == "tiktok"
    assert db.reads == []
    assert order.source.campaign_id is None


@pytest.mark.asyncio
@pytest.mark.parametrize("campaign_id", [
    "120248818886810420",
    "11111111-1111-4111-8111-111111111111",
    "trusted-explicit-provider-id",
])
async def test_explicit_campaign_id_keeps_its_long_readable_name(campaign_id):
    label = "حملة الشتاء التجريبية للفروات والهدايا"
    order = OrderDTO(
        order_id="identified-order",
        order_number="identified-order",
        created_at=datetime(2026, 11, 13, tzinfo=timezone.utc),
        source=OrderSourceDTO(
            source="tiktok",
            utm_campaign=campaign_id,
            campaign_id=campaign_id,
            campaign_name=label,
        ),
    )
    db = FakeDB()

    result = await enrich_order_campaigns(db, user_id="owner-1", orders=[order])

    assert result[0].source.campaign_id == campaign_id
    assert result[0].source.campaign_name == label
    assert result[0].source.utm_campaign == campaign_id
    assert db.reads == []


@pytest.mark.asyncio
async def test_uuid_salla_campaign_preserves_inferred_identity():
    campaign_id = "11111111-1111-4111-8111-111111111111"

    result = await enrich_order_campaigns(
        FakeDB(), user_id="owner-1", orders=[make_order(campaign_id)],
    )

    assert result[0].source.campaign_id == campaign_id
    assert result[0].source.utm_campaign == campaign_id
