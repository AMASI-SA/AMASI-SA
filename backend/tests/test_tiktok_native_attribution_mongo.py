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


@pytest.mark.asyncio
async def test_disconnected_native_account_is_not_an_attribution_source(mongo_db):
    db, evidence = mongo_db
    await seed(db)
    await db.mezan_integration_accounts_v2.update_one({"user_id": "owner"}, {"$set": {"connection_status": "disconnected"}})
    result = await sync_order_to_attribution_ledger(db, user_id="owner",
        order={"id": "order-3", "source_details": {"source": "tiktok", "campaign_id": "campaign-1"}})
    assert result["decision_safe"] is False


@pytest.mark.asyncio
async def test_same_campaign_id_in_two_connected_accounts_is_ambiguous(mongo_db):
    db, evidence = mongo_db
    await seed(db)
    await seed(db, account="70002")
    result = await sync_order_to_attribution_ledger(db, user_id="owner",
        order={"id": "order-4", "source_details": {"source": "tiktok", "campaign_id": "campaign-1"}})
    assert result["attribution_quality"] == "ambiguous"
    assert result["decision_safe"] is False


@pytest.mark.asyncio
async def test_campaign_name_or_provider_conversions_do_not_confirm_order(mongo_db):
    db, evidence = mongo_db
    await seed(db)
    result = await sync_order_to_attribution_ledger(db, user_id="owner",
        order={"id": "order-5", "source_details": {"source": "tiktok", "campaign_name": "Native campaign"},
               "provider_purchases": 100})
    assert result["decision_safe"] is False


@pytest.mark.asyncio
async def test_native_identity_lookup_transports_one_match_from_5000_campaigns(mongo_db):
    import gc, tracemalloc
    from integrations_control_center.tiktok_native_attribution import load_tiktok_order_evidence
    db, evidence = mongo_db
    await seed(db)
    entities = [{"entity_id": f"c{i:05d}", "entity_name": f"Campaign {i}"} for i in range(5000)]
    await db[ENTITY_COLLECTION].update_one({"user_id": "owner"}, {"$set": {"entity_count": 5000, "entities": entities}})
    del entities
    gc.collect(); evidence.clear(); tracemalloc.start()
    try:
        identities, links = await load_tiktok_order_evidence(db, "owner",
            {"source_details": {"source": "tiktok", "campaign_id": "c00001"}})
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert identities == [{"provider": "tiktok", "account_id": "70001",
                          "campaign_id": "c00001", "campaign_name": "Campaign 1"}]
    assert links == []
    assert evidence.catalogue_arrays == 0
    assert peak < 2 * 1024 * 1024
    print({"case": "native order identity from 5000 campaigns", "matches": len(identities),
           "python_peak_bytes": peak, "full_catalogue_arrays": evidence.catalogue_arrays})


@pytest.mark.asyncio
async def test_conflicting_native_campaign_candidates_fail_open_for_ingestion_not_truth(mongo_db):
    from mezan_attribution_ledger_sync import safe_sync_order_to_attribution_ledger
    db, evidence = mongo_db
    await seed(db)
    await db[ENTITY_COLLECTION].update_one({"user_id": "owner"}, {"$push": {"entities":
        {"entity_id": "campaign-2", "entity_name": "Other native campaign"}}})
    result = await safe_sync_order_to_attribution_ledger(db, user_id="owner",
        order={"id": "order-6", "campaign_id": "campaign-1",
               "source_details": {"source": "tiktok", "campaign_id": "campaign-2"}})
    assert result["synced"] is False
    assert await db.mezan_attribution_order_ledger_v1.count_documents({"user_id": "owner"}) == 0


@pytest.mark.asyncio
async def test_ai_ledger_evidence_uses_riyadh_dates_exact_scope_and_projection(mongo_db):
    from integrations_control_center.tiktok_native_insights import ledger_record_evidence
    db, evidence = mongo_db
    base = {"user_id": "owner", "order_created_at": "2026-10-02T21:00:00+00:00",
        "attribution": {"provider": "tiktok", "account_id": "70001", "campaign_id": "campaign-1",
            "quality": "confirmed", "decision_safe": True, "match_method": "exact_campaign_id"},
        "customer_phone": "PRIVATE_PHONE", "line_items": [{"private": "PRIVATE_ORDER"}]}
    foreign = {**deepcopy(base), "user_id": "other"}
    right = {**deepcopy(base), "order_created_at": "2026-10-03T21:00:00+00:00"}
    before = {**deepcopy(base), "order_created_at": "2026-10-02T20:59:59+00:00"}
    wrong_account = deepcopy(base); wrong_account["attribution"]["account_id"] = "70002"
    inferred = deepcopy(base); inferred["attribution"]["quality"] = "inferred"
    await db.mezan_attribution_order_ledger_v1.insert_many([base, foreign, right, before, wrong_account, inferred])
    result = await ledger_record_evidence(db, "owner", "70001", "campaign-1", "2026-10-03", "2026-10-03")
    assert result == {"exact_campaign_id_records": 1, "financial_orders": None,
        "sales_sar": None, "profit_sar": None, "financial_coverage": "not_verified",
        "record_coverage": "available_ledger_records_only"}
    assert "PRIVATE" not in str(result)


@pytest.mark.asyncio
async def test_ai_ledger_evidence_refuses_over_limit_before_generation(mongo_db):
    from integrations_control_center.tiktok_native_insights import ledger_record_evidence
    from integrations_control_center.tiktok_native_reporting import TikTokReportingError
    db, evidence = mongo_db
    await db.mezan_attribution_order_ledger_v1.insert_many([
        {"user_id": "owner", "order_created_at": "2026-10-03T22:00:00+00:00",
         "attribution": {"provider": "tiktok", "account_id": "70001", "campaign_id": "campaign-1",
            "quality": "confirmed", "decision_safe": True, "match_method": "exact_campaign_id"}}
        for _ in range(501)])
    with pytest.raises(TikTokReportingError) as error:
        await ledger_record_evidence(db, "owner", "70001", "campaign-1", "2026-10-03", "2026-10-09")
    assert error.value.code == "tiktok_ai_order_evidence_limit"
