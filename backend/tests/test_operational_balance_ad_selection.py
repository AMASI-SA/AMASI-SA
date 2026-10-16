"""Catalog selection cannot enable financial operations or select foreign accounts."""
import asyncio
from copy import deepcopy
import pytest
from fastapi import HTTPException
import operational_ad_selection as service
from operational_balance_store import initial


def account(identity, provider='google_ads', **extra):
    return dict(mezan_integration_account_id=identity, provider=provider,
                connection_status='connected', display_name=identity, **extra)


def test_native_selection_is_strict_and_preferences_cannot_override_snap_meta():
    records = [account('snap', 'snapchat_ads', mezan_selected=True),
               account('meta', 'meta_ads', mezan_selected=False),
               account('google', mezan_selected='true'), account('tiktok', 'tiktok_ads')]
    assert [r['id'] for r in service.choices(records, {}) if r['selected']] == ['snap']
    state = {'ad_catalog_selection': {'account_ids': ['meta', 'google', 'tiktok']}}
    assert {r['id'] for r in service.choices(records, state) if r['selected']} == {'snap', 'google', 'tiktok'}


def test_disconnected_and_duplicate_identities():
    row = account('offline'); row['connection_status'] = 'disconnected'
    assert service.choices([row], {}) == []
    with pytest.raises(HTTPException):
        service.choices([account('same'), account('same')], {})


@pytest.fixture
def isolated(monkeypatch):
    state = initial('owner')
    state['openings'] = {'unchanged': {'amount': '100'}}
    accounts = [account('google'), account('tiktok', 'tiktok_ads'), account('snap', 'snapchat_ads', mezan_selected=True)]
    async def rows(db, owner, collection):
        assert owner == 'owner' and collection == 'mezan_integration_accounts_v2'
        return deepcopy(accounts)
    async def mutate(db, owner, change):
        candidate = deepcopy(state)
        result = await change(candidate)
        state.clear(); state.update(candidate)
        return result
    monkeypatch.setattr(service, 'rows', rows)
    monkeypatch.setattr(service, 'mutate', mutate)
    return state, accounts


def test_save_is_idempotent_and_does_not_change_financial_or_integration_data(isolated):
    state, accounts = isolated
    before = deepcopy(state); native = deepcopy(accounts)
    payload = dict(request_id='selection-1', version=0, account_ids=['google', 'tiktok'])
    assert asyncio.run(service.save(None, 'owner', 'owner', payload)) == {'version': 1}
    assert asyncio.run(service.save(None, 'owner', 'owner', payload)) == {'version': 1}
    for key in ('openings', 'movements', 'engine', 'snapshot', 'status', 'started_at'):
        assert state[key] == before[key]
    assert accounts == native and len(state['audit']) == 1
    with pytest.raises(HTTPException):
        asyncio.run(service.save(None, 'owner', 'owner', dict(payload, account_ids=[])))


@pytest.mark.parametrize('ids', [['foreign'], ['snap'], ['google', 'google']])
def test_invalid_selection_is_atomic(isolated, ids):
    state, _ = isolated; before = deepcopy(state)
    with pytest.raises(HTTPException):
        asyncio.run(service.save(None, 'owner', 'owner', dict(request_id='selection-2', version=0, account_ids=ids)))
    assert state == before


def test_staff_and_stale_revision_rejected(isolated):
    state, _ = isolated; before = deepcopy(state)
    for actor, version in [('staff', 0), ('owner', 9)]:
        with pytest.raises(HTTPException):
            asyncio.run(service.save(None, 'owner', actor, dict(request_id='selection-3', version=version, account_ids=[])))
    assert state == before
