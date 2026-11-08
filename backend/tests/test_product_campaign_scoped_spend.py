"""Real stored-fact boundary regressions for product campaign spend."""
from datetime import date, datetime, timedelta, timezone

import pytest
from mongomock_motor import AsyncMongoMockClient

import product_promotion_estimates as service
from snapchat_v2.facts import normalize_hourly_fact
from snapchat_v2.models import DEFAULT_SWIPE_ATTRIBUTION_WINDOW, DEFAULT_VIEW_ATTRIBUTION_WINDOW
from tests.test_product_promotion_estimates import ReadDB, seeded, order, ledger, build

DAY = date(2026, 11, 8)
FIRST = datetime(2026, 11, 7, 21, tzinfo=timezone.utc)  # Riyadh midnight, LA afternoon.
NOW = FIRST + timedelta(hours=18, minutes=5)
ACCOUNT = {'user_id': 'owner', 'provider': 'snapchat_ads', 'ad_account_id': 'account',
           'currency': 'USD', 'timezone': 'America/Los_Angeles',
           'active': True, 'selected': True, 'connection_status': 'connected'}


def fact(hour, cid='campaign', *, provisional=False, windows=None):
    return normalize_hourly_fact({
        'user_id': 'owner', 'ad_account_id': 'account', 'campaign_id': cid,
        'hour_start_utc': FIRST + timedelta(hours=hour), 'spend_native': 2,
        'account_timezone': ACCOUNT['timezone'], 'currency': 'USD',
        'action_report_time': 'conversion', 'provisional': provisional,
        'attribution_windows': windows or {'swipe': DEFAULT_SWIPE_ATTRIBUTION_WINDOW,
                                         'view': DEFAULT_VIEW_ATTRIBUTION_WINDOW},
    }, now=NOW)


async def setup(monkeypatch, *, fx=3.75, coverage=True):
    from snapchat_v2 import routes
    db = AsyncMongoMockClient().db
    await db.mezan_snapchat_accounts_v2.insert_one(dict(ACCOUNT))
    monkeypatch.setattr(service, '_utcnow', lambda: NOW)
    async def costs(*args, **kwargs):
        return {'exchange_rate_to_sar': fx, 'cost_coverage': {'complete': coverage}}
    monkeypatch.setattr(routes, 'calculate_cost_components', costs)
    return db


async def read(db, ids=('campaign',), start=DAY, end=DAY):
    return await service._stored_campaigns(ReadDB(db), 'owner', 'snapchat', 'account', start, end, ids)


@pytest.mark.asyncio
async def test_1097_unrelated_campaigns_do_not_block_target_or_expand_fact_query(monkeypatch):
    db = await setup(monkeypatch)
    await db.mezan_snapchat_entity_facts_v2.insert_many([
        {'user_id': 'owner', 'provider': 'snapchat_ads', 'ad_account_id': 'account',
         'entity_type': 'campaign', 'external_id': f'old-{i}'} for i in range(1097)])
    await db.mezan_snapchat_hourly_facts_v2.insert_many(
        [fact(i) for i in range(17)] + [fact(17, provisional=True)] +
        [fact(i, 'unrelated') for i in range(18)])
    scoped = ReadDB(db)
    row = (await service._stored_campaigns(scoped, 'owner', 'snapchat', 'account', DAY, DAY, ['campaign']))['campaign']
    assert row == {'spend': 127.5, 'provisional': True,
                   'spend_as_of': '2026-11-08T14:00:00+00:00', 'reason': None}
    queries = [q for name, q in scoped.calls if name == 'mezan_snapchat_hourly_facts_v2']
    assert queries and all(q['external_id'] == {'$in': ['campaign']} for q in queries)
    assert not any(name == 'mezan_snapchat_entity_facts_v2' for name, _ in scoped.calls)


@pytest.mark.asyncio
@pytest.mark.parametrize('defect', ['internal_gap', 'duplicate', 'currency', 'timezone', 'unconfirmed'])
async def test_lag_prefix_never_hides_internal_or_conflicting_evidence(monkeypatch, defect):
    db = await setup(monkeypatch)
    rows = [fact(i) for i in range(17)]
    if defect == 'internal_gap': rows.pop(3)
    if defect == 'duplicate': rows.append(dict(rows[3]))
    if defect == 'currency': rows[3]['currency'] = 'SAR'
    if defect == 'timezone': rows[3]['account_timezone'] = 'Asia/Riyadh'
    if defect == 'unconfirmed': rows[3]['coverage']['status'] = 'partial'
    await db.mezan_snapchat_hourly_facts_v2.insert_many(rows)
    row = (await read(db))['campaign']
    assert row['spend'] is None and row['reason'] == 'campaign_hourly_coverage_incomplete'


@pytest.mark.asyncio
async def test_closed_period_cannot_use_short_prefix(monkeypatch):
    db = await setup(monkeypatch)
    monkeypatch.setattr(service, '_utcnow', lambda: NOW + timedelta(days=1))
    await db.mezan_snapchat_hourly_facts_v2.insert_many([fact(i) for i in range(23)])
    assert (await read(db))['campaign']['spend'] is None


@pytest.mark.asyncio
@pytest.mark.parametrize('fx,coverage', [(None, True), (3.75, False)])
async def test_complete_campaign_hours_still_require_verified_conversion(monkeypatch, fx, coverage):
    db = await setup(monkeypatch, fx=fx, coverage=coverage)
    await db.mezan_snapchat_hourly_facts_v2.insert_many([fact(i) for i in range(17)])
    row = (await read(db))['campaign']
    assert row['spend'] is None and row['reason'] == 'campaign_fx_incomplete'


@pytest.mark.asyncio
@pytest.mark.parametrize('conflict', [False, True])
async def test_one_prior_attribution_series_is_allowed_but_never_mixed(monkeypatch, conflict):
    db = await setup(monkeypatch)
    old = {'swipe': '7_DAY', 'view': '1_DAY'}
    rows = [fact(i, windows=old) for i in range(17)]
    if conflict:
        rows.append(fact(0, windows={'swipe': '1_DAY', 'view': '1_DAY'}))
    await db.mezan_snapchat_hourly_facts_v2.insert_many(rows)
    row = (await read(db))['campaign']
    assert row['spend'] == (None if conflict else 127.5)


@pytest.mark.asyncio
async def test_partial_current_series_does_not_borrow_missing_hours_from_old_series(monkeypatch):
    db = await setup(monkeypatch)
    rows = [fact(i) for i in (0, 2)] + [fact(i, windows={'swipe':'7_DAY','view':'1_DAY'}) for i in range(17)]
    await db.mezan_snapchat_hourly_facts_v2.insert_many(rows)
    assert (await read(db))['campaign']['spend'] is None


@pytest.mark.asyncio
async def test_target_campaign_keeps_other_product_orders_in_denominator(monkeypatch):
    db = await seeded(monkeypatch, orders=[order(), order('q-only', products=[{'product_id':'q', 'quantity':1, 'total':100}])],
                      ledgers=[ledger(ad_id=None), ledger('q-only', ad_id=None)])
    async def campaigns(db, owner, platform, account_id, start, end, campaign_ids):
        assert campaign_ids == ['campaign']
        return {'campaign': {'spend':100, 'reason':None}}
    monkeypatch.setattr(service, '_stored_campaigns', campaigns)
    row = (await build(db))['rows'][0]
    assert row['units_sold'] == 2
    assert row['estimated_product_spend_sar'] == 30  # 60 / (60 + 40 + 100).
    assert row['estimated_unit_promotion_cost_sar'] == 15


@pytest.mark.asyncio
async def test_closed_riyadh_reader_filters_real_hierarchy_before_product_safety_cap(monkeypatch):
    db = await setup(monkeypatch)
    await db.mezan_snapchat_accounts_v2.update_one({}, {'$set': {'timezone': 'Asia/Riyadh'}})
    monkeypatch.setattr(service, '_utcnow', lambda: NOW + timedelta(days=1))
    await db.mezan_snapchat_entity_facts_v2.insert_many([
        {'user_id': 'owner', 'provider': 'snapchat_ads', 'ad_account_id': 'account',
         'entity_type': 'campaign', 'external_id': f'old-{i}'} for i in range(1097)] +
        [{'user_id': 'owner', 'provider': 'snapchat_ads', 'ad_account_id': 'account',
          'entity_type': 'campaign', 'external_id': 'campaign', 'name': 'Target'}])
    # Exercise the real hierarchy reader, not a mocked report with one row.
    rows = await service._stored_campaigns(db, 'owner', 'snapchat', 'account', DAY, DAY, ['campaign'])
    assert set(rows) == {'campaign'}
    assert rows['campaign']['spend'] is None
    assert rows['campaign']['reason'] == 'campaign_spend_incomplete'
