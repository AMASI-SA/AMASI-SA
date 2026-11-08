import pytest
from mongomock_motor import AsyncMongoMockClient
from product_campaign_identity import load_campaign_ledgers
from product_traffic_sources import load_product_traffic_sources


async def seed(db, *, owner='owner', account='account', campaign='campaign', selected=True):
    await db.mezan_snapchat_accounts_v2.insert_one({
        'user_id': owner, 'provider': 'snapchat_ads', 'ad_account_id': account,
        'active': True, 'selected': selected, 'connection_status': 'connected'})
    await db.mezan_snapchat_entity_facts_v2.insert_one({
        'user_id': owner, 'provider': 'snapchat_ads', 'ad_account_id': account,
        'entity_type': 'campaign', 'external_id': campaign, 'name': 'كتلوج بلوفرات 2026'})


@pytest.mark.asyncio
@pytest.mark.parametrize('raw', [False, True])
async def test_product_report_does_not_enrich_empty_ledger_from_campaign_catalog(raw):
    db = AsyncMongoMockClient().db
    await seed(db)
    evidence = {'utm_source': 'snapchat', 'utm_campaign': 'campaign'}
    await db.unified_orders.insert_one({'user_id': 'owner', 'order_number': '123', 'order_id': 'native',
        **({'raw_by_source': {'salla_direct': evidence}} if raw else evidence)})
    parsed = {'order_number': '123'}
    sources = await load_product_traffic_sources(db, 'owner', [parsed])
    assert sources[id(parsed)]['platform'] == 'snapchat'
    assert sources[id(parsed)]['account_id'] is None
    assert '_campaign' not in sources[id(parsed)]
    assert await db.mezan_attribution_order_ledger_v1.count_documents({}) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize('owner,selected', [('other', True), ('owner', False)])
async def test_foreign_or_disabled_account_cannot_supply_campaign(owner, selected):
    db = AsyncMongoMockClient().db
    await seed(db, owner=owner, selected=selected)
    order = {'order_id': '123', 'utm_source': 'snapchat', 'utm_campaign': 'campaign'}
    result = await load_campaign_ledgers(db, 'owner', [order], {})
    assert not result.get('123')


@pytest.mark.asyncio
async def test_multiple_explicit_campaigns_and_duplicate_accounts_fail_closed():
    db = AsyncMongoMockClient().db
    await seed(db)
    await seed(db, account='second', campaign='second-campaign')
    order = {'order_id': '123', 'utm_source': 'snapchat', 'utm_campaign': 'campaign',
             'raw_by_source': {'salla_direct': {'campaign_id': 'second-campaign'}}}
    result = await load_campaign_ledgers(db, 'owner', [order], {})
    assert result['123'][0]['attribution']['quality'] == 'ambiguous'
    await seed(db, account='third')
    order.pop('raw_by_source')
    result = await load_campaign_ledgers(db, 'owner', [order], {})
    assert result['123'][0]['attribution']['quality'] == 'ambiguous'


@pytest.mark.asyncio
async def test_native_identity_does_not_override_conflicting_ledger():
    db = AsyncMongoMockClient().db
    await seed(db)
    order = {'order_id': '123', 'utm_source': 'snapchat', 'utm_campaign': 'campaign'}
    existing = {'123': [{'attribution': {'quality': 'confirmed', 'decision_safe': True,
                                        'provider': 'snapchat', 'account_id': 'different'}}]}
    result = await load_campaign_ledgers(db, 'owner', [order], existing)
    assert result['123'][0]['attribution']['quality'] == 'ambiguous'

@pytest.mark.asyncio
async def test_unknown_conflicting_raw_uuid_is_not_ignored():
    db = AsyncMongoMockClient().db
    cid = '32d573eb-d2ce-44ef-bc32-dd55ed5178fe'
    await seed(db, campaign=cid)
    order = {'order_id': '123', 'utm_source': 'snapchat', 'utm_campaign': cid,
             'raw_by_source': {'salla_direct': {'utm_campaign': '99d573eb-d2ce-44ef-bc32-dd55ed5178fe'}}}
    result = await load_campaign_ledgers(db, 'owner', [order], {})
    assert result['123'][0]['attribution']['quality'] == 'ambiguous'

@pytest.mark.asyncio
async def test_stale_ad_identity_falls_back_to_campaign_not_wrong_ad():
    db = AsyncMongoMockClient().db
    await seed(db)
    order = {'order_id': '123', 'utm_source': 'snapchat', 'utm_campaign': 'campaign', 'ad_id': 'new-ad'}
    result = await load_campaign_ledgers(db, 'owner', [order], {
        '123': [{'attribution': {'provider': 'snapchat', 'quality': 'inferred', 'ad_id': 'old-ad'}}]})
    assert result['123'][0]['attribution']['campaign_id'] == 'campaign'
    assert result['123'][0]['attribution']['ad_id'] is None

@pytest.mark.asyncio
async def test_nested_source_remains_without_campaign_enrichment():
    db = AsyncMongoMockClient().db
    await seed(db)
    await db.unified_orders.insert_one({'user_id': 'owner', 'order_number': '123',
        'marketing': {'utm_source': 'snapchat', 'campaign_id': 'campaign'}})
    parsed = {'order_number': '123'}
    result = await load_product_traffic_sources(db, 'owner', [parsed])
    assert result[id(parsed)]['platform'] == 'snapchat'
    assert '_campaign' not in result[id(parsed)]
