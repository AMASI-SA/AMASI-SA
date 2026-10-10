"""Native TikTok attribution integration against isolated local Mongo."""
from copy import deepcopy
import pytest
from mezan_attribution_ledger_sync import sync_order_to_attribution_ledger
from integrations_control_center.tiktok_native_hierarchy import ENTITY_COLLECTION
from tests.test_tiktok_hierarchy_mongo_performance import mongo_db
from tests.test_tiktok_native_hierarchy import ACCOUNT


async def seed(db, *, tenant="owner", account="70001"):
    await db.mezan_integration_accounts_v2.insert_one({**deepcopy(ACCOUNT), "user_id": tenant, "ad_account_id": account})
    await db[ENTITY_COLLECTION].insert_one({"user_id": tenant, "ad_account_id": account,
        "entity_type": "campaign", "complete": True, "entity_count": 1,
        "entities": [{"entity_id": "campaign-1", "campaign_id": "campaign-1", "entity_name": "Native campaign"}]})


@pytest.mark.asyncio
async def test_native_exact_campaign_id_confirms_order_without_manual_product_link(mongo_db):
    db, evidence = mongo_db
    await seed(db)
    result = await sync_order_to_attribution_ledger(db, user_id="owner",
        order={"id": "order-1", "created_at": "2026-10-03T21:30:00+00:00",
               "source_details": {"source": "tiktok", "campaign_id": "campaign-1"}})
    assert result["attribution_quality"] == "confirmed"
    assert result["decision_safe"] is True
    row = await db.mezan_attribution_order_ledger_v1.find_one({"user_id": "owner", "order_key": "order-1"})
    assert row["attribution"]["account_id"] == "70001"
    assert row["profit"]["net_profit_sar"] is None


@pytest.mark.asyncio
async def test_other_tenant_native_campaign_never_confirms_owner_order(mongo_db):
    db, evidence = mongo_db
    await seed(db, tenant="other")
    result = await sync_order_to_attribution_ledger(db, user_id="owner",
        order={"id": "order-2", "source_details": {"source": "tiktok", "campaign_id": "campaign-1"}})
    assert result["decision_safe"] is False
    assert result["attribution_quality"] == "unattributed"
