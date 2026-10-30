"""Actual owner grant helpers, configured-only; transport is a collaborator."""
from copy import deepcopy
from datetime import timedelta
import pytest
from fastapi import HTTPException
from integrations_control_center import google_ads_funded_creation_grants as grants
from integrations_control_center import google_ads_automation_budget_guard as guard
from integrations_control_center import google_ads_management as management
from integrations_control_center.google_ads_automation_creation_contract import prepare_google_creation_intent
from tests.test_google_ads_automation_creation_contract import prepared_case
from tests.test_google_ads_standing_dispatch_floor import setup as native_setup,NOW

async def setup(monkeypatch,db=None):
    db,provider=await native_setup(monkeypatch,db)
    owner=await db.users.find_one({'id':'owner'})
    if '_id' not in owner:await db.users.update_one({'id':'owner'},{'$set':{'_id':'unit-owner-doc'}})
    values=prepared_case();values['product']['last_synced_at']=NOW.isoformat()
    values['product']['details_synced_at']=NOW.isoformat()
    values['product']['inventory_evidence_v1']['observed_at']=NOW.isoformat()
    from integrations_control_center.google_ads_ai import PRODUCT_COLLECTION
    await db[PRODUCT_COLLECTION].insert_one(values['product'])
    draft=prepare_google_creation_intent(**prepared_case())['draft']
    await db[management.DRAFTS].insert_one({'user_id':'owner','account_id':'123','draft_id':'reviewdraft',
        'revision':1,'draft':draft})
    body={'mode':'create_new_enabled_search','spend_semantics':grants.SEMANTICS,'request_key':'reviewed_launch_1',
        'expected_revision':0,'owner_generation':1,'draft_id':'reviewdraft','draft_revision':1,'catalog_max_age_seconds':3600,
        'valid_from':NOW.isoformat(),'expires_at':(NOW+timedelta(days=1)).isoformat(),'budget_native':'15',
        'max_campaign_daily_budget_native':'20','max_account_daily_budget_native':'55'}
    return db,provider,body

async def review(db,body):
    return await grants.review_google_funded_grant(db,user=await db.users.find_one({'id':'owner'}),account_id='123',body=body,current=NOW)

async def save(db,body,digest):
    return await grants.save_google_funded_grant(db,user=await db.users.find_one({'id':'owner'}),account_id='123',body=body,
        expected_review_digest=digest,current=NOW)

@pytest.mark.asyncio
async def test_actual_review_save_configured_revoke_without_oauth_or_runtime(monkeypatch):
    db,provider,body=await setup(monkeypatch);preview=await review(db,body)
    assert preview['spend_semantics']=='AVERAGE_DAILY_COMMITMENT' and preview['grant']['reviewed_by']=='owner'
    result=await save(db,body,preview['review_digest'])
    assert result['committed'] is True and result['state']=='configured' and result['funded_dispatch_authorized'] is False
    row=await db[grants.GRANTS].find_one({'account_id':'123'})
    assert grants.validate_record(row).budget_native=='15' and row['claim'] is None
    async def unavailable(*args,**kwargs):raise AssertionError('OAuth/provider access during revoke')
    monkeypatch.setattr(management,'account_client',unavailable)
    monkeypatch.setattr(guard,'budget_guard_runtime_available',lambda:False)
    result=await grants.revoke_google_funded_grant(db,user=await db.users.find_one({'id':'owner'}),account_id='123',request_key=body['request_key'],expected_revision=1)
    row=await db[grants.GRANTS].find_one({'account_id':'123'})
    assert result['state']=='revoked' and row['revision']==2 and grants.validate_record(row)
    assert (await db[guard.BUDGET_GUARDS].find_one({'account_id':'123'}))['epoch']==2 and provider.writes==0

@pytest.mark.asyncio
@pytest.mark.parametrize('field,value',[('spend_semantics','HARD_DAILY_SPEND'),('spend_semantics','HARD_TOTAL_SPEND'),
    ('reviewed_by','owner'),('user_id','owner'),('currency_code','SAR'),('claim',{}),('enabled',True),('budget_native',15)])
async def test_review_rejects_unsupported_spend_and_client_authority(monkeypatch,field,value):
    db,provider,body=await setup(monkeypatch);body[field]=value
    with pytest.raises(HTTPException):await review(db,body)
    assert provider.writes==0 and not (await db[grants.GRANTS].find({}).to_list(length=1))

@pytest.mark.asyncio
async def test_omitted_semantics_and_stale_review_never_persist(monkeypatch):
    db,provider,body=await setup(monkeypatch);missing=deepcopy(body);missing.pop('spend_semantics')
    with pytest.raises(HTTPException):await review(db,missing)
    preview=await review(db,body);body['max_account_daily_budget_native']='54'
    with pytest.raises(HTTPException) as error:await save(db,body,preview['review_digest'])
    assert error.value.detail['code']=='google_funded_review_changed' and provider.writes==0

@pytest.mark.asyncio
async def test_nonowner_and_alias_native_identity_never_save(monkeypatch):
    db,provider,body=await setup(monkeypatch)
    await db.users.update_one({'id':'owner'},{'$set':{'role':'viewer'}})
    with pytest.raises(HTTPException):await review(db,body)
    await db.users.update_one({'id':'owner'},{'$set':{'role':'owner'}})
    original=provider.account_metadata
    async def alias():return {**await original(),'account_id':'0123'}
    monkeypatch.setattr(provider,'account_metadata',alias)
    with pytest.raises(HTTPException) as error:await review(db,body)
    assert error.value.detail['code']=='google_funded_native_account_unproved'

@pytest.mark.asyncio
async def test_unknown_save_ack_no_auto_retry_or_activation(monkeypatch):
    db,provider,body=await setup(monkeypatch);preview=await review(db,body)
    async def lost():raise TimeoutError('postcommit grant ACK lost')
    db.after_commit=lost
    with pytest.raises(HTTPException) as error:await save(db,body,preview['review_digest'])
    assert error.value.detail['preserve_execution_fence'] is True and error.value.detail['automatic_retry_allowed'] is False
    db.after_commit=None
    row=await db[grants.GRANTS].find_one({'account_id':'123'})
    assert row['state']=='configured' and row['revision']==1 and row['consumed'] is False
    with pytest.raises(HTTPException):await save(db,body,preview['review_digest'])
    assert (await db[guard.BUDGET_GUARDS].find_one({'account_id':'123'}))['epoch']==1 and provider.writes==0

async def authenticated_user(db,monkeypatch,*,issued_at=NOW):
    # Actual auth dependency, signed TEST-only token; no real credential/env read.
    import auth
    import jwt
    from starlette.requests import Request
    secret='test-only-funded-auth-key-not-production-0123456789'
    monkeypatch.setattr(auth,'get_jwt_secret',lambda:secret)
    token=jwt.encode({'sub':'owner','type':'access','mfa':True,'iat':issued_at.timestamp(),
        'exp':(NOW+timedelta(days=90)).timestamp()},secret,algorithm=auth.JWT_ALGORITHM)
    request=Request({'type':'http','path':'/api/integrations/v2/google_ads/automation/funded-grants',
        'headers':[(b'authorization',('Bearer '+token).encode())]})
    return await auth.get_current_user_from_db(request,db)

@pytest.mark.asyncio
async def test_actual_auth_password_revocation_and_stale_dependency_result_rejected(monkeypatch):
    db,provider,body=await setup(monkeypatch)
    user=await authenticated_user(db,monkeypatch,issued_at=NOW-timedelta(minutes=1))
    assert 'password_hash' not in user
    preview=await grants.review_google_funded_grant(db,user=user,account_id='123',body=body,current=NOW)
    public=grants.public_google_funded_review(preview)
    assert '_owner_auth_binding' not in public and 'password_updated_at' not in public
    await db.users.update_one({'id':'owner'},{'$set':{'password_updated_at':NOW-timedelta(seconds=1),
        'password_hash':'test-only-changed-not-a-secret'}})
    with pytest.raises(HTTPException) as error:
        await authenticated_user(db,monkeypatch,issued_at=NOW-timedelta(minutes=1))
    assert error.value.status_code==401 and error.value.detail=='Session revoked'
    # The dependency already returned this dict before the write. A fresh DB
    # owner lookup alone would not reject it; exact auth projection must do so.
    with pytest.raises(HTTPException) as error:
        await grants.save_google_funded_grant(db,user=user,account_id='123',body=body,
            expected_review_digest=preview['review_digest'],current=NOW)
    assert error.value.detail['code']=='google_funded_authenticated_owner_changed'
    assert provider.writes==0 and not await db[grants.GRANTS].find({}).to_list(length=1)

@pytest.mark.asyncio
async def test_auth_binding_matches_bson_stamp_and_is_not_public_review_data(monkeypatch):
    db,provider,body=await setup(monkeypatch)
    await db.users.update_one({'id':'owner'},{'$set':{'password_updated_at':(NOW-timedelta(minutes=1)).replace(tzinfo=None)}})
    user=await authenticated_user(db,monkeypatch,issued_at=NOW)
    preview=await grants.review_google_funded_grant(db,user=user,account_id='123',body=body,current=NOW)
    assert preview['_owner_auth_binding']['fields']['password_updated_at']['value']==(NOW-timedelta(minutes=1)).isoformat()
    assert all(not key.startswith('_') for key in grants.public_google_funded_review(preview))
    await grants.save_google_funded_grant(db,user=user,account_id='123',body=body,
        expected_review_digest=preview['review_digest'],current=NOW)
    assert provider.writes==0

@pytest.mark.asyncio
async def test_owner_read_has_server_limit_and_time_bound_through_session_wrapper(monkeypatch):
    from integrations_control_center.google_ads_paused_creation_budget import SessionDatabase
    calls=[]
    class BoundedCursor:
        def limit(self,value): calls.append(('limit',value)); return self
        def max_time_ms(self,value): calls.append(('max_time_ms',value)); return self
        async def to_list(self,*,length):
            calls.append(('to_list',length)); return [{'_id':'test-owner','id':'owner','role':'owner'}]
    class Owners:
        def find(self,query,projection,*,session):
            assert query=={'id':'owner'} and session is session_marker
            assert set(projection)=={'_id',*grants.AUTH_FIELDS}
            return BoundedCursor()
    class Database:
        def __getitem__(self,name):
            assert name=='users'; return Owners()
    async def owner(*args,**kwargs):return {'owner':True,'actor_id':'owner'}
    monkeypatch.setattr(grants.management,'principal',owner)
    session_marker=object()
    await grants.read_owner_auth(SessionDatabase(Database(),session_marker),user_id='owner',
        authenticated_user={'id':'owner','role':'owner'})
    assert calls==[('limit',2),('max_time_ms',2000),('to_list',2)]
