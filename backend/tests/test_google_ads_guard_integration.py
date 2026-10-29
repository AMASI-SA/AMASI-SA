"""Mandatory policy storage and native budget evidence, without provider writes."""
from copy import deepcopy

from fastapi import HTTPException
import pytest

from integrations_control_center import google_ads_automation_budget_guard as guard
from integrations_control_center import google_ads_automation_gateway as gateway
from integrations_control_center import google_ads_automation_authority as authority
from integrations_control_center.google_ads_automation_policy_mutation_adapter import mutate_google_automation_policy
from tests.test_google_ads_automation_budget_guard import DB


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['standing', 'creation'])
async def test_default_policy_path_is_transactional_without_optional_writer(kind):
    db = DB()
    result = await mutate_google_automation_policy(db, policy_kind=kind,
        scope={'user_id':'owner', 'account_id':'123'}, expected_revision=0,
        values={'enabled':False, 'max_account_daily_budget_native':'100'})
    assert result['committed'] is True and result['provider_dispatch_authorized'] is False
    assert db.rows[guard.BUDGET_GUARDS][0]['epoch'] == 1
    assert db.sessions[-1].commit_calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['standing', 'creation'])
async def test_revoke_preserves_private_claim_and_needs_no_google_connection(kind):
    collection = guard.AUTOMATION_POLICIES if kind == 'standing' else guard.CREATION_POLICIES
    private = '_dispatch_claim' if kind == 'standing' else '_creation_claim'
    db = DB({collection:[{'user_id':'owner','account_id':'123','revision':1,'enabled':True,
        private:{'claim_id':'held'}}]})
    await mutate_google_automation_policy(db, policy_kind=kind,
        scope={'user_id':'owner','account_id':'123'}, expected_revision=1, values={'enabled':False})
    assert db.rows[collection][0][private] == {'claim_id':'held'}
    assert db.rows[collection][0]['enabled'] is False
    assert db.rows[guard.BUDGET_GUARDS][0]['epoch'] == 1


@pytest.mark.asyncio
async def test_unsupported_transactions_never_use_legacy_write():
    db = DB(); db.transactional = False
    with pytest.raises(HTTPException) as error:
        await mutate_google_automation_policy(db, policy_kind='standing',
            scope={'user_id':'owner','account_id':'123'}, expected_revision=0, values={'enabled':False})
    assert error.value.detail['code'] == 'google_budget_guard_transactions_unavailable'
    assert db.rows == {}


@pytest.mark.asyncio
async def test_unknown_commit_preserves_policy_and_exposes_no_retry():
    db = DB(); db.lose_commit_ack = True
    with pytest.raises(HTTPException) as error:
        await mutate_google_automation_policy(db, policy_kind='standing',
            scope={'user_id':'owner','account_id':'123'}, expected_revision=0, values={'enabled':False})
    assert error.value.detail['automatic_retry_allowed'] is False
    assert error.value.detail['preserve_execution_fence'] is True
    assert error.value.detail['operation_id'].startswith('google_policy_')
    assert db.rows[guard.AUTOMATION_POLICIES][0]['revision'] == 1
    assert sum(session.commit_calls for session in db.sessions) == 1


@pytest.mark.asyncio
async def test_known_aborted_write_conflict_requests_refresh_without_retry(monkeypatch):
    from pymongo.errors import OperationFailure
    async def conflicting(*args, **kwargs):
        raise OperationFailure('test write conflict', code=112)
    monkeypatch.setattr(guard, '_policy_write', conflicting)
    db = DB()
    with pytest.raises(HTTPException) as error:
        await mutate_google_automation_policy(db, policy_kind='standing',
            scope={'user_id':'owner','account_id':'123'}, expected_revision=0, values={'enabled':False})
    assert error.value.status_code == 409
    assert error.value.detail['code'] == 'google_automation_policy_changed'
    assert error.value.detail['automatic_retry_allowed'] is False
    assert error.value.detail['commit_attempted'] is False
    assert db.rows == {}


@pytest.mark.asyncio
@pytest.mark.parametrize('shared, references, expected', [(False,1,False),(False,'1',False),(False,True,None),(True,1,None),(False,2,None),(False,None,None),(None,1,None)])
async def test_native_inventory_proves_unshared_budget_identity(monkeypatch, shared, references, expected):
    captured = {}
    async def read(client, fields, resource, where, *, required, limit):
        captured.update(fields=fields, required=required)
        return [{'campaign':{'id':'1','status':'ENABLED'}, 'campaignBudget':{'period':'DAILY',
            'resourceName':'customers/123/campaignBudgets/1', 'amountMicros':'10000000',
            'explicitlyShared':shared, 'referenceCount':references}}], True, []
    monkeypatch.setattr(gateway, 'compatible_read', read)
    result = await gateway._inventory(object(), {'account_id':'123','currency_code':'SAR','time_zone':'Asia/Riyadh'})
    row = result['campaigns'][0]
    assert row['budget_resource'] == 'customers/123/campaignBudgets/1'
    assert row['shared_budget'] is expected
    assert {'campaign_budget.resource_name','campaign_budget.explicitly_shared','campaign_budget.reference_count'} <= set(captured['required'])


def test_flags_alone_cannot_activate_unaccepted_guard_runtime(monkeypatch):
    monkeypatch.setenv('GOOGLE_ADS_AUTOMATION_EXECUTION_ENABLED','true')
    assert authority.execution_enabled() is False
    assert guard.budget_guard_runtime_available() is False


def test_claim_rechecks_full_policy_binding_from_transaction_snapshot():
    from datetime import datetime, timezone
    from tests.test_google_ads_budget_guard_standing import case, NOW
    snapshot, command = case()
    snapshot.standing_policy['max_account_daily_budget_native'] = '999'
    with pytest.raises(HTTPException) as error:
        guard.standing_commitment(snapshot, command, NOW)
    assert error.value.detail['code'] == 'google_budget_guard_policy_changed'
