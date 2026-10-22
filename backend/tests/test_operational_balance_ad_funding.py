"""Operational setup preserves owner scope and never creates financial accounts."""
import asyncio
from copy import deepcopy
import hashlib
import json

import pytest
from fastapi import HTTPException

import operational_ad_funding as funding
import operational_balance_sources as sources
import operational_balance_service as service
from operational_balance_store import initial
from backend.tests.test_operational_balance_sources import DB, row, START, NOW


def account(**extra):
    return row(mezan_integration_account_id='google', external_account_id='external',
               provider='google_ads', display_name='Google', connection_status='connected',
               currency='SAR', timezone='UTC', mezan_selected=True, **extra)


def payload(**extra):
    return dict(request_id='funding-1', version=0, integration_account_id='google',
                currency='SAR', mode='wallet_first', evidence='owner decision', **extra)


@pytest.fixture
def setup(monkeypatch):
    state = initial('owner')
    db = DB({'mezan_integration_accounts_v2': [account()]})
    async def mutate(db, owner, change):
        candidate = deepcopy(state)
        result = await change(candidate)
        state.clear(); state.update(candidate)
        return result
    async def read(db, owner):
        return deepcopy(state)
    async def inactive(db, owner):
        return None
    monkeypatch.setattr(funding, 'mutate', mutate)
    monkeypatch.setattr(funding, 'read', read)
    monkeypatch.setattr(service, 'accounting_inactive', inactive)
    return state, db


def test_confirmed_setup_exposes_selected_account_without_financial_accounts(setup):
    state, db = setup
    native = deepcopy(db.data)
    result = asyncio.run(funding.save(db, 'owner', 'owner', payload()))
    identity = result['party_id']
    assert identity == funding.party_id('owner', 'google')
    assert asyncio.run(funding.save(db, 'owner', 'owner', payload())) == result
    assert len(state['audit']) == 1 and db.data == native
    assert state['openings'] == {} and state['movements'] == [] and state['status'] == 'draft'
    choices = asyncio.run(sources.entities(db, 'owner', 'ad_account', operational_state=state))
    assert len(choices) == 1 and choices[0]['id'] == identity
    assert choices[0]['settings_complete'] is True and choices[0]['funding_policy'] == 'wallet_first'
    entry = asyncio.run(funding.catalog(db, 'owner'))['items'][0]
    assert entry['configured'] and entry['editable'] and entry['version'] == 1


@pytest.mark.parametrize('change', ['staff', 'active', 'unselected', 'disconnected', 'currency', 'timezone', 'stale', 'bound', 'used'])
def test_invalid_setup_rejected_atomically(setup, change):
    state, db = setup
    actor = 'owner'
    request = payload()
    if change == 'staff': actor = 'staff'
    if change == 'active': state['status'] = 'active'
    if change == 'unselected': db.data['mezan_integration_accounts_v2'][0]['mezan_selected'] = False
    if change == 'disconnected': db.data['mezan_integration_accounts_v2'][0]['connection_status'] = 'disconnected'
    if change == 'currency': request['currency'] = 'USD'
    if change == 'timezone': db.data['mezan_integration_accounts_v2'][0]['timezone'] = 'unknown'
    if change == 'stale': request['version'] = 9
    if change == 'bound': db.data['mz2_ad_account_bindings_v2'] = [row(integration_account_id='google', confirmed_by='owner', confirmed_at=START)]
    if change == 'used': state['openings']['saved'] = {'party_id': funding.party_id('owner', 'google'), 'amount': '50'}
    before = deepcopy(state)
    with pytest.raises(HTTPException):
        asyncio.run(funding.save(db, 'owner', actor, request))
    assert state == before


def test_replay_payload_conflict_and_owner_scope_are_rejected(setup):
    state, db = setup
    asyncio.run(funding.save(db, 'owner', 'owner', payload()))
    with pytest.raises(HTTPException):
        asyncio.run(funding.save(db, 'owner', 'owner', {**payload(), 'evidence': 'different'}))
    with pytest.raises(ValueError, match='owner_mismatch'):
        funding.bindings(state, 'other', [])


def test_daily_source_uses_operational_identity_and_preserves_close_proof(setup):
    state, db = setup
    identity = asyncio.run(funding.save(db, 'owner', 'owner', payload()))['party_id']
    proof = dict(version=1, complete=True, complete_response=True, explicit_spend_present=True,
                 account_id='external', business_date='2026-10-06', timezone='UTC', currency='SAR',
                 spend_native='80', observed_at=NOW)
    proof['fingerprint'] = hashlib.sha256(json.dumps(proof, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()
    db.data['mezan_google_ads_performance_daily_v2'] = [row(_id='day', ad_account_id='external',
        provider='google_ads', date='2026-10-06', spend_native='80', currency_native='SAR',
        observed_at=NOW, account_timezone='UTC', source_close_proof=proof)]
    result = asyncio.run(sources.collect_sources(db, 'owner', START, NOW, operational_state=state))
    snap = result['ad_snapshots'][0]
    assert snap['account_id'] == identity and snap['funding_policy'] == 'wallet_first'
    assert snap['amount'] == '80' and snap['closed'] and snap['complete']
    assert 'wallet_fraction' not in snap
    del proof['fingerprint']
    result = asyncio.run(sources.collect_sources(db, 'owner', START, NOW, operational_state=state))
    assert not result['ad_snapshots'][0]['closed']


def test_later_native_binding_does_not_double_count_operational_source(setup):
    state, db = setup
    identity = asyncio.run(funding.save(db, 'owner', 'owner', payload()))['party_id']
    native = row(_id='native', integration_account_id='google', confirmed_by='owner', confirmed_at=START)
    result, conflicts = funding.bindings(state, 'owner', [native])
    assert result == [] and conflicts == [identity]


@pytest.mark.parametrize('authority', ['operational', 'native'])
def test_reconnected_integration_cannot_configure_same_provider_account_twice(setup, authority):
    state, db = setup
    db.data['mezan_integration_accounts_v2'].append({**account(), 'mezan_integration_account_id': 'reconnected'})
    if authority == 'operational':
        asyncio.run(funding.save(db, 'owner', 'owner', payload()))
    else:
        db.data['mz2_ad_account_bindings_v2'] = [row(_id='native', integration_account_id='google',
            platform='google_ads', platform_account_id='external', confirmed_by='owner', confirmed_at=START)]
    before = deepcopy(state)
    with pytest.raises(HTTPException) as error:
        asyncio.run(funding.save(db, 'owner', 'owner', {**payload(), 'request_id': 'reconnect-setup',
                                                      'integration_account_id': 'reconnected'}))
    expected = 'operational_ad_duplicate_account' if authority == 'operational' else 'operational_ad_native_binding_exists'
    assert error.value.detail['code'] == expected and state == before
    item = next(r for r in asyncio.run(funding.catalog(db, 'owner'))['items'] if r['integration_account_id'] == 'reconnected')
    assert not item['editable'] and item['reason'] == expected


@pytest.mark.parametrize('authority', ['operational', 'native'])
def test_source_merge_rejects_reconnected_duplicate_authority(setup, authority):
    state, db = setup
    identity = asyncio.run(funding.save(db, 'owner', 'owner', payload()))['party_id']
    setting = state['ad_funding'][identity]
    duplicate = {**setting, 'integration_account_id': 'reconnected'}
    native = []
    if authority == 'operational':
        other_id = funding.party_id('owner', 'reconnected')
        state['ad_funding'][other_id] = duplicate
    else:
        native = [row(**duplicate, _id='native')]
    result, conflicts = funding.bindings(state, 'owner', native)
    assert result == []
    assert set(conflicts) == ({identity, other_id} if authority == 'operational' else {identity})


@pytest.mark.parametrize('field,value', [('currency', 'USD'), ('timezone', 'Asia/Riyadh'), ('connection_status', 'disconnected')])
def test_changed_provider_identity_disables_new_source_without_rewriting_state(setup, field, value):
    state, db = setup
    asyncio.run(funding.save(db, 'owner', 'owner', payload()))
    before = deepcopy(state)
    db.data['mezan_integration_accounts_v2'][0][field] = value
    result = asyncio.run(sources.collect_sources(db, 'owner', START, NOW, operational_state=state))
    assert not result['ad_snapshots']
    assert any(issue['code'] == 'ad_account_incomplete' for issue in result['issues'])
    assert state == before
