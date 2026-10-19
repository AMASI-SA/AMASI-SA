"""Real reader/adapter regression: raw persisted facts must carry amount proof."""
from datetime import date
from unittest.mock import AsyncMock

import pytest
from snapchat_v2 import routes
from unified_marketing.readers import snapchat_v2 as reader
from decision_intelligence.evidence_adapter import _candidate_evidence


@pytest.mark.asyncio
@pytest.mark.parametrize('case,expected', [
    ('complete', True), ('zero_native', True), ('partial', False),
    ('missing_facts', False), ('missing_native', False),
    ('missing_fx', False), ('zero_fx', False), ('negative_fx', False),
    ('nan_fx', False), ('infinite_fx', False),
    ('incomplete_fx_coverage', False), ('hourly_without_period_proof', False),
    ('sar', True), ('nan_native', False), ('negative_native', False),
])
async def test_real_entity_reader_preserves_amount_evidence(monkeypatch, case, expected):
    account = {'ad_account_id': 'snap-test', 'display_name': 'Test',
               'timezone': 'America/Los_Angeles', 'currency': 'USD'}
    if case == 'sar':
        account.update(timezone='Asia/Riyadh', currency='SAR')
    facts = [{'external_id': 'campaign-test', 'campaign_id': 'campaign-test',
              'spend_native': 0 if case == 'zero_native' else 100,
              'purchases': 2, 'purchase_value_native': 200,
              'impressions': 1000, 'swipes': 30,
              'coverage': {'status': 'complete'}}]
    if case == 'missing_facts':
        facts = []
    if case == 'missing_native':
        facts[0].pop('spend_native')
    if case in {'nan_native', 'negative_native'}:
        facts[0]['spend_native'] = float('nan') if case == 'nan_native' else -1
    identity = {'external_id': 'campaign-test', 'name': 'Campaign',
                'status': 'ACTIVE', 'active': True}
    fx = {'sar': 1, 'missing_fx': None, 'zero_fx': 0, 'negative_fx': -1,
          'nan_fx': float('nan'), 'infinite_fx': float('inf')}.get(case, 3.75)
    monkeypatch.setattr(reader, 'resolve_account', AsyncMock(return_value=account))
    monkeypatch.setattr(reader, 'list_entities', AsyncMock(return_value=[identity]))
    monkeypatch.setattr(routes, 'list_entities', AsyncMock(return_value=[identity]))
    monkeypatch.setattr(routes, '_latest_level_status', AsyncMock(return_value='complete'))
    monkeypatch.setattr(routes, 'load_hourly_facts', AsyncMock(return_value=facts if case in {'partial', 'hourly_without_period_proof'} else []))
    monkeypatch.setattr(routes, 'load_total_facts', AsyncMock(return_value=[] if case == 'hourly_without_period_proof' else facts))
    monkeypatch.setattr(routes, 'total_period_complete', AsyncMock(return_value=case != 'partial'))
    monkeypatch.setattr(routes, 'calculate_cost_components', AsyncMock(return_value={
        'exchange_rate_to_sar': fx,
        'cost_coverage': {'complete': case != 'incomplete_fx_coverage'},
    }))
    # This is the external Salla/cost-loader seam, not a prefilled unified row.
    monkeypatch.setattr(reader, 'load_salla_campaign_outcomes', AsyncMock(return_value={
        'by_campaign': {'campaign-test': {'orders': 2, 'sales_sar': 750,
            'profitability': {'orders': 2, 'sales_sar': 750,
                'product_cost_sar': 100, 'known_product_cost_sar': 100,
                'ad_spend_sar': 375, 'contribution_profit_sar': 275,
                'missing_cost_orders': 0, 'cost_status': 'complete'}}},
        'orders': [], 'summary': {'coverage_status': 'complete'},
    }))
    monkeypatch.setattr(reader, 'load_abandoned_cart_outcomes', AsyncMock(return_value={}))
    report = await reader.load_snapchat_v2_entity_report(
        None, 'owner', entity_level='campaign', date_from=date(2026, 10, 13),
        date_to=date(2026, 10, 15), timezone_name=account['timezone'],
        account_id=account['ad_account_id'], include_stale=False,
    )
    row = report['rows'][0]
    assert (row['quality']['amount_complete'] is True) is expected
    evidence = _candidate_evidence(row, global_blockers=[])
    assert evidence['decision_eligible'] is expected
    if not expected:
        assert 'financial_coverage' in evidence['blocked_by']
    if case == 'zero_native':
        assert row['delivery']['spend']['amount'] == 0
        assert row['delivery']['spend_sar']['amount'] == 0

