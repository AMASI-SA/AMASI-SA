"""All six actual owner handlers advance the same real Mongo guard epoch."""
from fastapi import APIRouter
import httpx
import pytest

from integrations_control_center import google_ads_automation_budget_guard as guard
from integrations_control_center import google_ads_automation_policy as standing
from integrations_control_center import google_ads_automation_activation as activation
from tests.test_google_ads_automation_policy_mongo import (
    ACCOUNT, automation_mongo_uri, mongo_db, mounted_app, request_body)


@pytest.mark.asyncio
async def test_real_standing_save_activate_revoke_use_one_epoch_even_after_oauth_loss(monkeypatch, mongo_db):
    app, state = mounted_app(monkeypatch, mongo_db)
    async def current_user(): return {'id':'owner-a'}
    monkeypatch.setattr(standing, 'automation_runtime_available', lambda: True)
    router = APIRouter()
    activation.attach_google_ads_automation_activation_routes(router, mongo_db, current_user)
    app.include_router(router)
    path = f'/google_ads/automation/policy/{ACCOUNT}'
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://test') as client:
        saved = await client.put(path, json=request_body(enabled=False))
        assert saved.status_code == 200, saved.text
        activated = await client.post(path+'/activate', json={'expected_revision':1,'acknowledge_execution':True})
        assert activated.status_code == 200, activated.text
        await mongo_db.mezan_integration_accounts_v2.delete_many({'user_id':'owner-a'})
        revoked = await client.post(path+'/revoke', json={'expected_revision':2})
        assert revoked.status_code == 200, revoked.text
    stored = await mongo_db[guard.BUDGET_GUARDS].find_one({'user_id':'owner-a','account_id':ACCOUNT})
    assert stored['epoch'] == 3 and stored['active_claim'] is None
    assert state['writes'] == 0


@pytest.mark.asyncio
async def test_real_creation_save_activate_revoke_use_same_epoch_even_after_oauth_loss(monkeypatch, mongo_db):
    from tests.test_google_ads_automation_creation_policy_mongo import mounted
    from tests.test_google_ads_automation_creation_policy import body
    app, state = await mounted(monkeypatch, mongo_db)
    path = f'/google_ads/automation/creation-policy/{ACCOUNT}'
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://test') as client:
        saved = await client.put(path, json=body())
        assert saved.status_code == 200, saved.text
        activated = await client.post(path+'/activate', json={'expected_revision':1,'acknowledge_creation':True,'acknowledge_creative':True})
        assert activated.status_code == 200, activated.text
        await mongo_db.mezan_integration_accounts_v2.delete_many({'user_id':'owner-a'})
        revoked = await client.post(path+'/revoke', json={'expected_revision':2})
        assert revoked.status_code == 200, revoked.text
    stored = await mongo_db[guard.BUDGET_GUARDS].find_one({'user_id':'owner-a','account_id':ACCOUNT})
    assert stored['epoch'] == 3 and stored['active_claim'] is None
    assert state['mutations'] == 0
