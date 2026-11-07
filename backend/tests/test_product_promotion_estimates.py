from copy import deepcopy
from datetime import date

import pytest
from mongomock_motor import AsyncMongoMockClient

import product_promotion_estimates as service
from mezan_attribution_order_ledger import LEDGER_COLLECTION


class ReadCollection:
    def __init__(self, collection, calls):
        self.collection, self.calls = collection, calls
    def find(self, query, projection):
        assert query['user_id'] == 'owner'
        self.calls.append((self.collection.name, deepcopy(query)))
        return self.collection.find(query, projection)
    async def find_one(self, query, projection):
        assert query['user_id'] == 'owner'
        self.calls.append((self.collection.name, deepcopy(query)))
        return await self.collection.find_one(query, projection)
    def __getattr__(self, name):
        raise AssertionError('Unexpected read or mutation: ' + name)


class ReadDB:
    def __init__(self, db): self.db, self.calls = db, []
    def __getitem__(self, key): return ReadCollection(self.db[key], self.calls)


def order(key='o1', **kwargs):
    return {'user_id': 'owner', 'order_number': key, 'order_date': '2026-11-07',
        'order_status': 'تم التنفيذ', 'total_amount': 100, 'currency': 'SAR', 'utm_source': 'snapchat',
        'payment_method': 'mada', 'shipping_company': 'aramex',
        'products': [{'product_id': 'p', 'quantity': 2, 'total': 60},
                     {'product_id': 'q', 'quantity': 1, 'total': 40}], **kwargs}


def ledger(key='o1', **attrs):
    return {'user_id': 'owner', 'order_key': key, 'attribution': {
        'provider': 'snapchat', 'quality': 'confirmed', 'decision_safe': True,
        'account_id': 'account', 'campaign_id': 'campaign', 'campaign_name': 'حملة',
        'ad_group_id': 'group', 'ad_id': 'ad', **attrs}}


async def seeded(monkeypatch, *, orders=None, ledgers=None, spend=10.01):
    db = AsyncMongoMockClient().db
    await db.unified_orders.insert_many(orders or [order()])
    await db[LEDGER_COLLECTION].insert_many(ledgers or [ledger()])
    async def ads(db, owner, platform, account, start, end):
        assert (owner, platform, account, start, end) == ('owner', 'snapchat', 'account', date(2026, 11, 7), date(2026, 11, 7))
        return {'ad': {'campaign_id': 'campaign', 'ad_group_id': 'group', 'ad_group_name': 'مجموعة',
                       'ad_name': 'إعلان', 'spend': spend, 'reason': None}}
    monkeypatch.setattr(service, '_stored_ads', ads)
    return db


async def build(db, **kwargs):
    return await service.build_product_promotion_estimates(ReadDB(db), 'owner',
        **{'from_date': '2026-11-07', 'to_date': '2026-11-07', 'product_id': 'p',
           'platform': 'snapchat', 'account_id': 'account', **kwargs})


@pytest.mark.asyncio
async def test_all_products_denominator_piece_counts_and_exact_halalas(monkeypatch):
    db = await seeded(monkeypatch)
    p = (await build(db))['rows'][0]
    q = (await build(db, product_id='q'))['rows'][0]
    assert p['units_sold'] == 2 and q['units_sold'] == 1
    assert p['ad_spend_sar'] == q['ad_spend_sar'] == 10.01
    assert p['estimated_product_spend_sar'] == 6.01
    assert q['estimated_product_spend_sar'] == 4
    assert p['estimated_unit_promotion_cost_sar'] == 3.005
    assert p['status'] == 'estimated' and p['allocation_level'] == 'ad'
    assert p['ad_group_name'] == 'مجموعة'


@pytest.mark.asyncio
@pytest.mark.parametrize('attrs', [{'ad_id': None}, {'ad_id': 'unverified'}, {'ad_group_id': 'wrong'}])
async def test_unknown_ad_preserves_units_at_campaign_without_cost(monkeypatch, attrs):
    db = await seeded(monkeypatch, ledgers=[ledger(**attrs)])
    row = (await build(db))['rows'][0]
    assert row['units_sold'] == 2 and row['campaign_id'] == 'campaign'
    assert row['ad_id'] is None and row['ad_group_id'] is None
    assert row['estimated_product_spend_sar'] is None
    assert row['reason'] == 'ad_identity_unverified'
    assert row['allocation_level'] == 'campaign' and row['campaign_name'] == 'حملة'


@pytest.mark.asyncio
async def test_other_product_missing_currency_or_price_blocks_allocation(monkeypatch):
    db = await seeded(monkeypatch, orders=[order(currency='USD')])
    assert (await build(db))['rows'][0]['reason'] == 'product_revenue_unavailable'
    await db.unified_orders.update_one({}, {'$set': {'currency': 'SAR', 'products.1.total': None}})
    assert (await build(db))['rows'][0]['estimated_product_spend_sar'] is None


@pytest.mark.asyncio
async def test_verified_fx_converts_weights_with_real_helper(monkeypatch):
    db = await seeded(monkeypatch, orders=[order(currency='AED', original_currency='AED',
        accounting_currency='SAR', currency_conversion_status='verified', exchange_rate_to_sar=1.02)])
    assert (await build(db))['rows'][0]['estimated_product_spend_sar'] == 6.01


@pytest.mark.asyncio
@pytest.mark.parametrize('spend,expected', [(0, 0), (None, None), (-2, None), (float('nan'), None)])
async def test_zero_is_known_but_missing_invalid_spend_unknown(monkeypatch, spend, expected):
    db = await seeded(monkeypatch, spend=spend)
    assert (await build(db))['rows'][0]['estimated_product_spend_sar'] == expected


@pytest.mark.asyncio
async def test_zero_revenue_never_equal_allocates(monkeypatch):
    db = await seeded(monkeypatch, orders=[order(products=[{'product_id': 'p', 'quantity': 1, 'total': 0}])])
    assert (await build(db))['rows'][0]['reason'] == 'positive_product_revenue_required'


@pytest.mark.asyncio
async def test_tenant_date_status_payment_shipping_scope(monkeypatch):
    db = await seeded(monkeypatch, orders=[order(), order('foreign', user_id='foreign'),
        order('old', order_date='2026-11-06'), order('pending', order_status='بانتظار المراجعة'),
        order('other-payment', payment_method='visa'), order('other-ship', shipping_company='smsa')],
        ledgers=[ledger(k) for k in ['o1', 'foreign', 'old', 'pending', 'other-payment', 'other-ship']])
    result = await build(db, statuses='reviewed', payment_methods='mada', shipping_companies='aramex')
    assert result['rows'] == []
    result = await build(db, payment_methods='mada', shipping_companies='aramex')
    assert result['rows'][0]['units_sold'] == 4  # default includes pending review
    result = await build(db, statuses='pending_review', payment_methods='mada', shipping_companies='aramex')
    assert result['rows'][0]['units_sold'] == 2


@pytest.mark.asyncio
async def test_dashboard_refund_scope_and_custom_policy_fail_closed(monkeypatch):
    db = await seeded(monkeypatch)
    await db.order_status_policy.insert_one({'user_id': 'owner', 'status': 'تم التنفيذ', 'category': 'refunded'})
    result = await build(db, report_scope='dashboard')
    assert result['status'] == 'unavailable' and result['reason'] == 'refund_quantity_scope_unverified'
    assert (await build(db))['rows'][0]['status'] == 'estimated'


@pytest.mark.asyncio
async def test_read_limits_and_period_limit(monkeypatch):
    db = await seeded(monkeypatch)
    assert (await build(db, from_date='2026-10-01'))['reason'] == 'period_exceeds_31_days'
    monkeypatch.setattr(service, 'MAX_ORDERS', 0)
    with pytest.raises(service.EvidenceUnavailable, match='read_limit'):
        await build(db)


@pytest.mark.asyncio
async def test_duplicate_and_foreign_ledger_never_exposes_campaign(monkeypatch):
    db = await seeded(monkeypatch, ledgers=[ledger(), ledger()])
    assert (await build(db))['rows'] == []
    await db[LEDGER_COLLECTION].delete_many({})
    await db[LEDGER_COLLECTION].insert_one({**ledger(), 'user_id': 'foreign'})
    assert (await build(db))['rows'] == []


@pytest.mark.asyncio
async def test_meta_google_are_not_campaign_spend_disguised_as_ads():
    for platform in ('meta', 'google'):
        with pytest.raises(service.EvidenceUnavailable, match='stored_ad_spend_unavailable'):
            await service._stored_ads(None, 'owner', platform, 'account', date(2026, 11, 7), date(2026, 11, 7))


@pytest.mark.asyncio
@pytest.mark.parametrize('complete,timezone,expected', [(True, 'Asia/Riyadh', 20), (False, 'Asia/Riyadh', None), (True, 'America/Los_Angeles', 'error')])
async def test_tiktok_readonly_adapter_checks_coverage_timezone_and_identity(monkeypatch, complete, timezone, expected):
    from integrations_control_center import tiktok_native_hierarchy
    async def workspace(db, owner, **kwargs):
        assert owner == 'owner' and kwargs['account_id'] == 'account'
        assert kwargs['entity_type'] == 'ad'
        return {'accounts': [{'account_id': 'account', 'timezone': timezone}],
            'ai_readiness': {'campaign_identity_ready': True},
            'campaign_pagination': {'total': 1, 'pages': 1},
            'entities': [{'entity_id': 'ad', 'entity_name': 'Ad', 'account_id': 'account',
                'campaign_id': 'campaign', 'adgroup_id': 'group', 'data_complete': complete, 'spend_sar': 20}]}
    monkeypatch.setattr(tiktok_native_hierarchy, 'tiktok_workspace', workspace)
    if expected == 'error':
        with pytest.raises(service.EvidenceUnavailable, match='timezone'):
            await service._tiktok_ads(None, 'owner', 'account', date(2026,11,7), date(2026,11,7))
    else:
        assert (await service._tiktok_ads(None, 'owner', 'account', date(2026,11,7), date(2026,11,7)))['ad']['spend'] == expected


@pytest.mark.asyncio
@pytest.mark.parametrize('complete,fx,wrong_parent,expected', [(True, 1, False, 20), (False, 1, False, None), (True, None, False, None), (True, 1, True, 'unverified')])
async def test_snapchat_real_sar_guard_and_persisted_parent_validation(monkeypatch, complete, fx, wrong_parent, expected):
    from snapchat_v2 import accounts, routes
    db = AsyncMongoMockClient().db
    await db.mezan_snapchat_entity_facts_v2.insert_many([
        {'user_id': 'owner', 'provider': 'snapchat_ads', 'ad_account_id': 'account',
         'entity_type': 'ad', 'external_id': 'ad', 'campaign_id': 'wrong' if wrong_parent else 'campaign',
         'ad_squad_id': 'group', 'name': 'Ad'},
        {'user_id': 'owner', 'provider': 'snapchat_ads', 'ad_account_id': 'account',
         'entity_type': 'ad_squad', 'external_id': 'group', 'campaign_id': 'campaign', 'name': 'Group'},
        {'user_id': 'foreign', 'provider': 'snapchat_ads', 'ad_account_id': 'account',
         'entity_type': 'ad', 'external_id': 'secret', 'campaign_id': 'campaign', 'ad_squad_id': 'group'}])
    async def account(db, owner, aid):
        assert (owner, aid) == ('owner', 'account')
        return {'ad_account_id': aid, 'timezone': 'Asia/Riyadh'}
    async def performance(db, **kwargs):
        assert kwargs['user_id'] == 'owner' and kwargs['timezone_name'] == 'Asia/Riyadh'
        return {'rows': [{'account_id': 'account', 'ad_id': 'ad', 'campaign_id': 'campaign',
            'ad_squad_id': 'group', 'source_fact_count': 1, 'spend_native': 20,
            'performance_sync_status': 'complete', 'source_period_complete': complete,
            'native_amount_complete': True}], 'totals': {}}
    async def costs(db, **kwargs):
        return {'exchange_rate_to_sar': fx, 'cost_coverage': {'complete': True}}
    monkeypatch.setattr(accounts, 'resolve_account', account)
    monkeypatch.setattr(routes, '_entity_performance_report', performance)
    monkeypatch.setattr(routes, 'calculate_cost_components', costs)
    result = await service._snapchat_ads(ReadDB(db), 'owner', 'account', date(2026,11,7), date(2026,11,7))
    assert 'secret' not in result
    if expected == 'unverified':
        assert result == {}
    else:
        assert result['ad']['spend'] == expected


@pytest.mark.asyncio
async def test_current_catalog_variant_maps_to_parent_product(monkeypatch):
    from product_v2_routes import PRODUCTS
    db = await seeded(monkeypatch, orders=[order(products=[{'product_id': 'variant', 'variant_id': 'variant', 'sku': 'child', 'quantity': 3, 'total': 90}])])
    await db[PRODUCTS].insert_one({'user_id': 'owner', 'salla_product_id': 'parent', 'variants': [{'id': 'variant', 'sku': 'child'}]})
    row = (await build(db, product_id='parent'))['rows'][0]
    assert row['units_sold'] == 3 and row['estimated_product_spend_sar'] == 10.01


@pytest.mark.asyncio
async def test_dashboard_default_is_distinct_from_sold_and_hides_inferred(monkeypatch):
    db = await seeded(monkeypatch, orders=[order(order_status='custom'), order('inferred', order_date_inferred=True)], ledgers=[ledger(), ledger('inferred')])
    await db.settings.insert_one({'user_id': 'owner', 'hide_inferred_date_orders': True})
    assert (await build(db))['rows'] == []
    assert (await build(db, report_scope='dashboard'))['rows'][0]['units_sold'] == 2


@pytest.mark.asyncio
async def test_same_ad_aggregates_multiple_orders_but_not_other_ad(monkeypatch):
    db = await seeded(monkeypatch, orders=[order(), order('o2'), order('unknown')],
        ledgers=[ledger(), ledger('o2'), ledger('unknown', ad_id='other')])
    rows = (await build(db))['rows']
    assert [(r['ad_id'], r['units_sold']) for r in rows] == [(None, 2), ('ad', 4)]
    assert rows[1]['estimated_product_spend_sar'] == 6.01  # spend once, never once per order
