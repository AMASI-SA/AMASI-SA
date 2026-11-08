from copy import deepcopy
from datetime import date
import pytest

from operational_provider_fees import provider_projection
from operational_provider_plan_scope import contract_digest
from test_operational_balance_tabby_uniform_fee import policy, payment


def terms():
    rows = [policy('three', 'three'), policy('four', 'four'), policy('long', 'long')]
    rows[2]['components'][0]['percentage'] = '9.5'
    scope = {'id': 'availability', 'provider': 'tabby', 'currency': 'SAR',
             'status': 'active', 'effective_from': '2026-06-02', 'effective_to': '2026-06-30',
             'confirmed_by': 'owner', 'confirmed_at': '2026-06-02T00:00:00Z',
             'evidence': 'owner-approved available plans, no new tariff',
             'contracts': [{'policy_id': r['id'], 'contract_digest': contract_digest(r)} for r in rows[:2]]}
    for row in rows:
        row['_approved_plan_scopes'] = [deepcopy(scope)]
    return rows


def test_missing_plan_uses_only_explicitly_available_contracts_and_does_not_mutate():
    rows = terms()
    before = deepcopy(rows)
    result = provider_projection(payment(), rows, date(2026, 6, 10))
    assert result['estimated_fees'] == '3.51'
    assert result['expected_receivable'] == '96.49'
    assert result['plan_id'] is None
    assert result['equivalent_policy_ids'] == ['four', 'three']
    assert result['fee_policy_scope_id'] == 'availability'
    assert rows == before


@pytest.mark.parametrize('day', [date(2026, 6, 1), date(2026, 7, 1)])
def test_scope_does_not_apply_outside_its_dates(day):
    with pytest.raises(ValueError, match='provider_payment_plan_required'):
        provider_projection(payment(), terms(), day)


@pytest.mark.parametrize('field,value', [('percentage', '3'), ('fixed_amount', '1'),
    ('vat_rounding', 'half_up'), ('vat_rate', '15'), ('vat_basis', 'unrounded_fee')])
def test_contract_change_invalidates_approval(field, value):
    rows = terms()
    rows[0]['components'][0][field] = value
    with pytest.raises(ValueError, match='provider_plan_scope_contract_changed'):
        provider_projection(payment(), rows, date(2026, 6, 10))


def test_missing_selected_contract_and_conflicting_scope_fail_closed():
    rows = terms()[1:]
    with pytest.raises(ValueError, match='provider_plan_scope_contract_changed'):
        provider_projection(payment(), rows, date(2026, 6, 10))
    rows = terms()
    rows[1]['_approved_plan_scopes'][0]['evidence'] = 'conflicting scope'
    with pytest.raises(ValueError, match='provider_plan_scope_conflict'):
        provider_projection(payment(), rows, date(2026, 6, 10))


def test_cumulative_undated_payment_cannot_cross_scope_start():
    with pytest.raises(ValueError, match='provider_payment_plan_required|provider_fee_transaction_date_required'):
        provider_projection(payment(), terms(), date(2026, 6, 10), earliest_date=date(2026, 6, 1))


def test_known_long_plan_keeps_own_evidence_and_is_not_repriced():
    result = provider_projection(payment(plan_id='long'), terms(), date(2026, 6, 10))
    assert result['plan_id'] == 'long'
    assert result['estimated_fees'] == '11.70'
    assert 'fee_policy_scope_id' not in result


def test_saved_contract_arithmetic_has_no_installment_dependency_1000_amounts():
    from decimal import Decimal, ROUND_HALF_UP, ROUND_HALF_EVEN
    rows = terms()
    for row in rows[:2]:
        template = row['components'][0]
        row['components'] = [{**template, 'id': name, 'percentage': rate,
                              'fixed_amount': fixed, 'vat_rate': '15'}
                             for name, rate, fixed in [('refundable', '4.99', '0'),
                                                      ('retained', '2', '0'), ('fixed', '0', '1')]]
    for row in rows:
        row['_approved_plan_scopes'][0]['contracts'] = [
            {'policy_id': p['id'], 'contract_digest': contract_digest(p)} for p in rows[:2]]
    cent = Decimal('.01')
    for i in range(1, 1001):
        gross = Decimal(i * 137) / 100
        parts = [(gross * Decimal(rate) / 100).quantize(cent, rounding=ROUND_HALF_UP)
                 for rate in ('4.99', '2')] + [Decimal('1')]
        expected = sum(p + (p * Decimal('.15')).quantize(cent, rounding=ROUND_HALF_EVEN) for p in parts)
        result = provider_projection(payment(gross=str(gross)), rows, date(2026, 6, 10))
        assert Decimal(result['estimated_fees']) == expected
        assert Decimal(result['expected_receivable']) == gross - expected
        assert result['plan_id'] is None


def test_scope_save_preserves_tariffs_and_rejects_overlap_stale_version_other_owner():
    from test_onboarding_ssot_contracts import DB, run
    from test_operational_balance_fee_schedule_setup import saved
    from operational_provider_plan_scope import PlanScopeCreate, create_plan_scope, list_plan_scopes
    from accounting_onboarding_ssot import list_fee_policies
    from fastapi import HTTPException
    db = DB()
    component = dict(id='capture', percentage='2', fixed_amount='1', vat_treatment='exclusive', vat_rate='15')
    a = saved(db, provider='tabby', plan_id='three', components=[component])
    b = saved(db, provider='tabby', plan_id='four', components=[component])
    before = run(list_fee_policies(db, 'shop'))
    payload = PlanScopeCreate(provider='tabby', currency='SAR', policy_ids=[a['id'], b['id']],
        expected_version=2, effective_from='2026-06-01', evidence='authorized enabled plan scope')
    scope = run(create_plan_scope(db, 'shop', 'owner', payload))
    after = run(list_fee_policies(db, 'shop'))
    assert [{k:v for k,v in p.items() if k != '_approved_plan_scopes'} for p in after] == before
    assert run(list_plan_scopes(db, 'shop', 'SAR')) == {'items': [scope], 'version': 3}
    assert provider_projection(payment(), after, date(2026,6,10))['estimated_fees'] == '3.45'
    with pytest.raises(HTTPException, match='version_conflict'):
        run(create_plan_scope(db, 'shop', 'owner', payload))
    with pytest.raises(HTTPException, match='overlap'):
        run(create_plan_scope(db, 'shop', 'owner', payload.model_copy(update={'expected_version': 3})))
    with pytest.raises(HTTPException, match='version_conflict'):
        run(create_plan_scope(db, 'other', 'owner', payload))
