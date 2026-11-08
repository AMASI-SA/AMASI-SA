from datetime import date, datetime, timedelta, timezone

import pytest
from mongomock_motor import AsyncMongoMockClient

import product_promotion_estimates as service
from tests.test_product_promotion_estimates import seeded, order, ledger, build, ReadDB


@pytest.mark.asyncio
async def test_unknown_ad_merges_campaign_once_including_known_ads(monkeypatch):
    db = await seeded(monkeypatch, orders=[order(), order('o2')],
                      ledgers=[ledger(), ledger('o2', ad_id=None)])
    async def campaigns(*args):
        return {'campaign': {'spend': 100, 'reason': None}}
    monkeypatch.setattr(service, '_stored_campaigns', campaigns)
    rows = (await build(db))['rows']
    assert len(rows) == 1
    row = rows[0]
    assert row['allocation_level'] == 'campaign' and row['units_sold'] == 4
    assert row['ad_spend_sar'] is None and row['campaign_spend_sar'] == 100
    assert row['estimated_product_spend_sar'] == 60
    assert row['estimated_unit_promotion_cost_sar'] == 15
    assert row['ad_id'] is None and row['ad_group_id'] is None
    q = (await build(db, product_id='q'))['rows'][0]
    assert q['estimated_product_spend_sar'] == 40


@pytest.mark.asyncio
async def test_incomplete_campaign_never_becomes_zero_or_partial_ad_allocation(monkeypatch):
    db = await seeded(monkeypatch, orders=[order(), order('o2')],
                      ledgers=[ledger(), ledger('o2', ad_id=None)])
    async def campaigns(*args):
        return {'campaign': {'spend': None, 'reason': 'campaign_spend_incomplete'}}
    monkeypatch.setattr(service, '_stored_campaigns', campaigns)
    row = (await build(db))['rows'][0]
    assert row['estimated_product_spend_sar'] is None
    assert row['campaign_spend_sar'] is None and row['ad_spend_sar'] is None
    assert row['units_sold'] == 4 and row['reason'] == 'campaign_spend_incomplete'


@pytest.mark.asyncio
@pytest.mark.parametrize('complete,fx,expected', [(True, 3.75, 75), (False, 3.75, None), (True, None, None)])
async def test_campaign_adapter_retains_real_native_and_fx_guards(monkeypatch, complete, fx, expected):
    from snapchat_v2 import accounts, routes
    async def account(*args):
        return {'ad_account_id': 'account', 'timezone': 'Asia/Riyadh', 'currency': 'SAR'}
    async def performance(db, **kwargs):
        assert kwargs['entity_type'] == 'campaign' and kwargs['timezone_name'] == 'Asia/Riyadh'
        return {'rows': [{'account_id': 'account', 'campaign_id': 'campaign',
            'source_fact_count': 1, 'spend_native': 20, 'performance_sync_status': 'complete',
            'source_period_complete': complete, 'native_amount_complete': True}], 'totals': {}}
    async def costs(*args, **kwargs):
        return {'exchange_rate_to_sar': fx, 'cost_coverage': {'complete': True}}
    monkeypatch.setattr(accounts, 'resolve_account', account)
    monkeypatch.setattr(routes, '_entity_performance_report', performance)
    monkeypatch.setattr(routes, 'calculate_cost_components', costs)
    result = await service._stored_campaigns(None, 'owner', 'snapchat', 'account', date(2026,11,7), date(2026,11,7))
    assert result['campaign']['spend'] == expected


@pytest.mark.asyncio
@pytest.mark.parametrize('defect', [None, 'missing', 'duplicate', 'currency', 'provisional'])
async def test_riyadh_hourly_campaign_proof_is_exact_and_fail_closed(defect):
    from snapchat_v2.models import build_attribution_key, DEFAULT_SWIPE_ATTRIBUTION_WINDOW, DEFAULT_VIEW_ATTRIBUTION_WINDOW
    db = AsyncMongoMockClient().db
    start = datetime(2026,11,6,21,tzinfo=timezone.utc)
    rows = [{'user_id':'owner', 'provider':'snapchat_ads', 'ad_account_id':'account',
        'entity_type':'campaign', 'external_id':'campaign', 'hour_start_utc':start+timedelta(hours=i),
        'hour_end_utc':start+timedelta(hours=i+1), 'spend_native':1,
        'currency':'USD', 'account_timezone':'America/Los_Angeles', 'action_report_time':'conversion',
        'attribution_key':build_attribution_key('conversion', {'swipe':DEFAULT_SWIPE_ATTRIBUTION_WINDOW,'view':DEFAULT_VIEW_ATTRIBUTION_WINDOW}),
        'coverage':{'status':'complete', 'data_state':'confirmed_data'}, 'provisional':False} for i in range(24)]
    if defect == 'missing': rows.pop()
    if defect == 'duplicate': rows.append(dict(rows[0]))
    if defect == 'currency': rows[0]['currency']='SAR'
    if defect == 'provisional': rows[0]['provisional']=True
    await db.mezan_snapchat_hourly_facts_v2.insert_many(rows)
    result = await service._snapchat_campaign_hourly_proof(ReadDB(db), 'owner',
        {'ad_account_id':'account','currency':'USD','timezone':'America/Los_Angeles'},
        ['campaign'], date(2026,11,7), date(2026,11,7))
    assert result == ({'campaign': {'spend': 24, 'provisional': False,
        'spend_as_of': '2026-11-07T21:00:00+00:00', 'fact_count': 24}} if defect is None else {})


@pytest.mark.asyncio
async def test_missing_ledger_uses_explicit_native_campaign_for_spend(monkeypatch):
    from tests.test_product_campaign_identity import seed
    from mezan_attribution_order_ledger import LEDGER_COLLECTION
    db = await seeded(monkeypatch, orders=[order(utm_campaign='campaign')])
    await db[LEDGER_COLLECTION].delete_many({})
    await seed(db)
    async def campaigns(*args):
        return {'campaign': {'spend': 100, 'reason': None}}
    monkeypatch.setattr(service, '_stored_campaigns', campaigns)
    row = (await build(db))['rows'][0]
    assert row['campaign_name'] == 'كتلوج بلوفرات 2026'
    assert row['campaign_spend_sar'] == 100 and row['estimated_product_spend_sar'] == 60
    assert row['ad_spend_sar'] is None and row['allocation_level'] == 'campaign'
    assert await db[LEDGER_COLLECTION].count_documents({}) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize('missing', [False, True])
async def test_today_provisional_proves_every_completed_hour_and_reports_cutoff(missing):
    from snapchat_v2.models import build_attribution_key, DEFAULT_SWIPE_ATTRIBUTION_WINDOW, DEFAULT_VIEW_ATTRIBUTION_WINDOW
    db = AsyncMongoMockClient().db
    first = datetime(2026,11,7,21,tzinfo=timezone.utc)
    # 03:25 Riyadh: exactly three complete hours, no current/future zeros.
    now = datetime(2026,11,8,0,25,tzinfo=timezone.utc)
    rows = [{'user_id':'owner', 'provider':'snapchat_ads', 'ad_account_id':'account',
        'entity_type':'campaign', 'external_id':'campaign', 'hour_start_utc':first+timedelta(hours=i),
        'hour_end_utc':first+timedelta(hours=i+1), 'spend_native':2,
        'currency':'USD', 'account_timezone':'America/Los_Angeles', 'action_report_time':'conversion',
        'attribution_key':build_attribution_key('conversion', {'swipe':DEFAULT_SWIPE_ATTRIBUTION_WINDOW,'view':DEFAULT_VIEW_ATTRIBUTION_WINDOW}),
        'coverage':{'status':'complete', 'data_state':'confirmed_data'}, 'provisional':False} for i in range(3)]
    if missing: rows.pop(1)
    await db.mezan_snapchat_hourly_facts_v2.insert_many(rows)
    result = await service._snapchat_campaign_hourly_proof(ReadDB(db), 'owner',
        {'ad_account_id':'account','currency':'USD','timezone':'America/Los_Angeles'},
        ['campaign'], date(2026,11,8), date(2026,11,8), now=now)
    assert result == ({} if missing else {'campaign': {'spend': 6, 'provisional': True,
        'spend_as_of':'2026-11-08T00:00:00+00:00', 'fact_count': 3}})


@pytest.mark.asyncio
async def test_campaign_provisional_status_and_cutoff_survive_allocation(monkeypatch):
    db = await seeded(monkeypatch, ledgers=[ledger(ad_id=None)])
    async def campaigns(*args):
        return {'campaign': {'spend': 50, 'reason': None, 'provisional': True,
                             'spend_as_of':'2026-11-08T00:00:00+00:00'}}
    monkeypatch.setattr(service, '_stored_campaigns', campaigns)
    row = (await build(db))['rows'][0]
    assert row['status'] == 'estimated_provisional' and row['provisional'] is True
    assert row['spend_as_of'] == '2026-11-08T00:00:00+00:00'
    assert row['campaign_spend_sar'] == 50 and row['estimated_product_spend_sar'] == 30


@pytest.mark.asyncio
@pytest.mark.parametrize('proved', [True, False])
async def test_today_adapter_uses_elapsed_proof_not_full_day_total(monkeypatch, proved):
    from snapchat_v2 import accounts, routes
    now = datetime(2026,11,8,0,25,tzinfo=timezone.utc)
    monkeypatch.setattr(service, '_utcnow', lambda: now)
    async def account(*args):
        return {'ad_account_id':'account','timezone':'America/Los_Angeles','currency':'USD'}
    async def performance(*args, **kwargs):
        return {'rows':[{'campaign_id':'campaign', 'account_id':'account',
            'spend_native':999, 'source_period_complete':True, 'native_amount_complete':True,
            'source_fact_count':24, 'performance_sync_status':'complete'}]}
    async def hourly(*args, **kwargs):
        assert kwargs['now'] == now
        return {'campaign': {'spend':6, 'provisional':True, 'fact_count':3,
                             'spend_as_of':'2026-11-08T00:00:00+00:00'}} if proved else {}
    async def costs(*args, **kwargs):
        return {'exchange_rate_to_sar':3.75, 'cost_coverage':{'complete':True}}
    monkeypatch.setattr(accounts, 'resolve_account', account)
    monkeypatch.setattr(routes, '_entity_performance_report', performance)
    monkeypatch.setattr(service, '_snapchat_campaign_hourly_proof', hourly)
    monkeypatch.setattr(routes, 'calculate_cost_components', costs)
    row = (await service._stored_campaigns(None, 'owner', 'snapchat', 'account',
                                          date(2026,11,8), date(2026,11,8)))['campaign']
    assert row['spend'] == (22.5 if proved else None)
    assert row['provisional'] is proved
    if proved: assert row['spend_as_of'] == '2026-11-08T00:00:00+00:00'
