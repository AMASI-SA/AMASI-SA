"""Synthetic source-to-settings regressions for MZ2 fee calculations."""
from copy import deepcopy

import pytest
from mongomock_motor import AsyncMongoMockClient

from accounting_onboarding_ssot import POLICIES, _key
from bnpl.config_store import save_settings
from bnpl.settlements_service import _merchant_fee_rates, compute_settlement_for_provider
from payment_gateway_metrics import compute_metrics
from provider_fee_sources import load_statement_plans
from test_provider_fee_source_terms import policy, statement_fixture


async def database(*, policies=None, mode='mz2_policies'):
    db = AsyncMongoMockClient().fee_test
    await db[POLICIES].insert_one({'user_id': 'owner', 'provider': 'tabby', 'policies': policies or [policy()]})
    await db[POLICIES].insert_one({'_id': _key('owner', 'fee_source_mode'), 'user_id': 'owner', 'fee_source_mode': mode})
    await db.settings.insert_one({'user_id': 'owner', 'payment_methods': [
        {'name': 'Tabby', 'commission_percent': 3.25, 'fixed_fee': 0.80, 'vat_percent': 17},
    ], 'report_included_statuses': []})
    return db


async def capture(db, *, include_product=True):
    await db.payment_transactions.insert_one({'user_id': 'owner', 'provider': 'tabby', 'provider_id': 'tx-a',
        'order_number': 'order-a', 'amount': 40, 'currency': 'SAR', 'created_at_provider': '2026-06-10T12:00:00Z'})
    if include_product:
        entry, document = statement_fixture()
        await db.settlement_entries.insert_one(entry)
        await db.settlement_files.insert_one(document)


@pytest.mark.asyncio
async def test_bnpl_uses_canonical_settings_in_auto_mode_and_save_mirrors_them():
    db = await database(mode='legacy_settings')
    await db.users.insert_one({'id': 'owner', 'settings': {'payment_methods': [
        {'name': 'تابي', 'commission_percent': 9.5, 'fixed_fee': 9, 'vat_percent': 2},
    ]}})
    rates = await _merchant_fee_rates(db, 'owner', 'tabby')
    assert (rates['commission_pct'], rates['fixed_fee_per_order'], rates['vat_pct']) == (3.25, 0.8, 17)
    assert rates['fee_source'] == 'payment_methods_settings'
    before = await db.users.find_one({'id': 'owner'})
    await save_settings(db, 'owner', 'tabby', {'commission_mode': 'manual', 'mdr_percent': 0.031,
                                             'fixed_fee_per_order': 0.70, 'vat_on_fees_percent': 0.18})
    saved = await db.settings.find_one({'user_id': 'owner'})
    assert saved['payment_methods'][0] == {'name': 'Tabby', 'commission_percent': 3.1, 'fixed_fee': 0.7, 'vat_percent': 18}
    assert await db.users.find_one({'id': 'owner'}) == before


@pytest.mark.asyncio
@pytest.mark.parametrize('include_product', [True, False])
async def test_bnpl_binds_statement_product_and_keeps_gross_when_plan_is_missing(include_product):
    db = await database()
    await capture(db, include_product=include_product)
    result = await compute_settlement_for_provider(db, 'owner', 'tabby', '2026-06-10', '2026-06-10')
    assert result['totals']['gross_sales'] == 40
    assert result['fees_complete'] is include_product
    assert result['totals']['commission'] == (2.5 if include_product else None)
    assert result['totals']['commission_vat'] == (0.42 if include_product else None)
    assert result['totals']['net_payable'] == (37.08 if include_product else None)
    if not include_product:
        assert result['fee_review_reasons'] == ['provider_payment_plan_required']
        assert result['bank']['remaining_with_provider'] is None


@pytest.mark.asyncio
async def test_bnpl_official_amounts_override_an_unresolved_product_estimate():
    db = await database()
    await capture(db, include_product=False)
    await db.settlement_files.insert_one({'user_id': 'owner', 'provider': 'tabby', 'id': 'official-example',
        'header': {'statement_id': 'example-cycle', 'period_start': '2026-06-10', 'period_end': '2026-06-10'},
        'totals': {'transactions_count': 1, 'gross': 40, 'fees': 0.80, 'fees_vat': 0.12, 'net': 39.08}})
    result = await compute_settlement_for_provider(db, 'owner', 'tabby', '2026-06-10', '2026-06-10')
    assert result['data_source'] == 'provider_official_file'
    assert result['fees_complete'] is True
    assert result['totals']['commission'] == 0.80
    assert result['totals']['net_payable'] == 39.08


async def metric_settings(db, owner):
    return await db.settings.find_one({'user_id': owner})


@pytest.mark.asyncio
async def test_metrics_show_product_terms_and_keep_actual_fee_priority(monkeypatch):
    monkeypatch.setattr('payment_gateway_metrics.ensure_user_settings', metric_settings)
    db = await database()
    await capture(db)
    await db.unified_orders.insert_many([
        {'user_id': 'owner', 'order_number': 'order-a', 'order_date': '2026-06-10', 'order_status': 'completed',
         'total_amount': 40, 'currency': 'SAR', 'payment_method': 'tabby', 'payment_fee_status': 'estimated'},
        {'user_id': 'owner', 'order_number': 'order-b', 'order_date': '2026-06-10', 'order_status': 'completed',
         'total_amount': 20, 'currency': 'SAR', 'payment_method': 'tabby', 'payment_fee_status': 'actual',
         'actual_payment_fee': 0.60, 'actual_payment_vat': 0.09, 'actual_net_amount': 19.31},
    ])
    result = await compute_metrics(db, 'owner')
    row = next(row for row in result['rows'] if row['key'] == 'tabby')
    assert (row['gross'], row['fees'], row['fees_vat'], row['net']) == (60, 3.1, 0.51, 56.39)
    assert row['fees_complete'] is True
    assert row['actual_orders_count'] == 1


@pytest.mark.asyncio
async def test_metrics_central_mode_never_uses_legacy_rate_for_missing_provider(monkeypatch):
    monkeypatch.setattr('payment_gateway_metrics.ensure_user_settings', metric_settings)
    db = await database()
    await db.unified_orders.insert_one({'user_id': 'owner', 'order_number': 'order-a', 'order_date': '2026-06-10',
        'order_status': 'completed', 'total_amount': 25, 'currency': 'SAR', 'payment_method': 'emkan',
        'payment_fee_status': 'estimated'})
    result = await compute_metrics(db, 'owner')
    row = next(row for row in result['rows'] if row['key'] == 'emkan')
    assert row['gross'] == 25 and row['fees'] is None and row['net'] is None
    assert result['totals']['gross'] == 25 and result['totals']['net'] is None
    assert row['fee_review_reasons'] == ['provider_fee_policy_incomplete_or_ambiguous']


@pytest.mark.asyncio
async def test_metrics_apple_pay_selects_the_saved_network_policy(monkeypatch):
    monkeypatch.setattr('payment_gateway_metrics.ensure_user_settings', metric_settings)
    db = await database(policies=[policy(provider='salla', plan_id=None, payment_method='mada')])
    await db.unified_orders.insert_one({'user_id': 'owner', 'order_number': 'order-a', 'order_date': '2026-06-10',
        'order_status': 'completed', 'total_amount': 40, 'currency': 'SAR', 'payment_method': 'applepay',
        'payment_fee_status': 'estimated', 'raw_by_source': {'salla_direct': {
            'payment_method': 'applepay', 'payment_methods': [{'payment_method': 'mada'}]}}})
    result = await compute_metrics(db, 'owner')
    row = next(row for row in result['rows'] if row['key'] == 'applepay')
    assert (row['fees'], row['fees_vat'], row['net']) == (2.5, 0.42, 37.08)


@pytest.mark.asyncio
async def test_statement_plan_query_is_scoped_bounded_and_does_not_write():
    db = await database()
    await capture(db)
    before = await db.settlement_entries.find({}).to_list(10)
    index = await load_statement_plans(db, 'owner', providers=['tabby'], order_numbers=['order-a'])
    assert index['tabby', 'order-a']['plan_id'] == 'synthetic-long-product'
    assert await load_statement_plans(db, 'other', providers=['tabby'], order_numbers=['order-a']) == {}
    assert await load_statement_plans(db, 'owner', providers=['tamara'], order_numbers=['order-a']) == {}
    assert await db.settlement_entries.find({}).to_list(10) == before
    duplicate = deepcopy(before[0])
    duplicate.pop('_id')
    duplicate['id'] = 'entry-b'
    await db.settlement_entries.insert_one(duplicate)
    with pytest.raises(ValueError, match='provider_fee_product_scope_too_large'):
        await load_statement_plans(db, 'owner', providers=['tabby'], order_numbers=['order-a'], max_rows=1)
