from copy import deepcopy
from operational_balance_engine import reconcile

NOW = '2026-10-08T12:00:00+00:00'

def state():
    return {'status':'active','started_at':'2026-10-05T00:00:00+00:00',
            'ad_funding':{'ad':{'mode':'wallet_first','currency':'SAR'}}}

def snapshot(**changes):
    return {'account_id':'ad','date':'2026-10-06','observed_at':NOW,
            'amount':'80','currency':'SAR','timezone':'UTC','funding_type':'hybrid',
            'funding_policy':'wallet_first','closed':True,'complete':True,'day_ended':True,**changes}

def test_wallet_first_preserves_daily_spend_once_without_fixed_split():
    original = state()
    result = reconcile(original, {'ad_snapshots':[snapshot()]}, NOW)
    costs = result['engine']['obligations']
    assert len(costs) == 1
    cost = costs['advertising:ad:2026-10-06']
    assert (cost['confirmed'],cost['expected'],cost['funding_type']) == ('80.00','0.00','wallet_first')
    assert reconcile(result, {'ad_snapshots':[snapshot()]}, NOW) == result
    assert 'engine' not in original

def test_wallet_first_does_not_confirm_open_day():
    result = reconcile(state(), {'ad_snapshots':[snapshot(closed=False)]}, NOW)
    cost = result['engine']['obligations']['advertising:ad:2026-10-06']
    assert (cost['confirmed'],cost['expected']) == ('0.00','80.00')

def test_wallet_first_requires_explicit_matching_operational_policy():
    for policy in ({}, {'ad':{'mode':'wallet_first','currency':'USD'}}):
        source = state(); source['ad_funding'] = policy
        result = reconcile(source, {'ad_snapshots':[snapshot()]}, NOW)
        assert not result['engine']['obligations']
        assert 'advertising_wallet_policy_incomplete' in str(result['engine'])

def test_confirmed_day_cannot_be_reinterpreted_as_explicit_split():
    first = reconcile(state(), {'ad_snapshots':[snapshot()]}, NOW)
    result = reconcile(first, {'ad_snapshots':[snapshot(funding_policy=None,wallet_fraction='0.5')]}, NOW)
    assert result['engine']['obligations'] == first['engine']['obligations']
    assert 'advertising_closed_funding_changed' in str(result['engine'])

def test_unapproved_spend_change_keeps_original_debt_evidence():
    first = reconcile(state(), {'ad_snapshots':[snapshot()]}, NOW)
    result = reconcile(first, {'ad_snapshots':[snapshot(amount='90')]}, NOW)
    assert result['engine']['obligations'] == first['engine']['obligations']
    assert 'advertising_closed_day_correction_required' in str(result['engine'])
