"""Owner-requested all-linked-Google analytical proof; offline transport only."""
import asyncio
from copy import deepcopy
from datetime import datetime,timezone,timedelta
import httpx
import pytest
from fastapi import HTTPException
from integrations_control_center import google_ads_reporting as report
from integrations_control_center import google_ads_reporting_recovery as recovery
from tests.test_google_ads_automation_creation_gateway import DB

NOW=datetime(2026,10,28,10,tzinfo=timezone.utc)
BODY=recovery.GoogleReportingRecoveryInput(account_id='123',request_id='recovery_request_0001')


class OfflineClient:
    def __init__(self,*args,**kwargs):pass
    async def __aenter__(self):return self
    async def __aexit__(self,*args):pass


def setup(monkeypatch):
    db=DB({'users':[{'id':'owner','role':'owner'}],
        'mezan_integrations_v2':[{'user_id':'owner','provider':'google_ads','connection_status':'error',
            'connection_provenance':'api_connection','updated_at':(NOW-timedelta(minutes=10)).isoformat(),'permission_observation_id':'oauth-observation'}],
        'mezan_integration_accounts_v2':[{'user_id':'owner','provider':'google_ads','ad_account_id':str(account),
            'external_account_id':str(account),'connection_status':'connected','connection_provenance':'api_connection',
            'mezan_selected':account==123,'currency':'SAR','timezone':'Asia/Riyadh'} for account in [123,234,345]]})
    calls=[]
    async def credential(*args):return 'offline-test'
    async def read(client,*,access_token,account,query):
        calls.append((account['ad_account_id'],query))
        if 'LIMIT 1' in query:
            return report.StreamRows([{'customer':{'id':account['ad_account_id'],'currencyCode':'SAR','timeZone':'Asia/Riyadh','manager':False}}],True)
        return report.StreamRows([],True)
    monkeypatch.setattr(report.httpx,'AsyncClient',OfflineClient)
    monkeypatch.setattr(report,'google_oauth_configured',lambda:True)
    monkeypatch.setattr(report,'google_ads_reporting_enabled',lambda:True)
    monkeypatch.setattr(report,'_credential',credential)
    monkeypatch.setattr(report,'_search_stream',read)
    return db,calls,read


async def recover(db,body=BODY,**kwargs):
    return await recovery.recover_google_reporting(db,{'id':'owner'},body,clock=lambda:NOW,**kwargs)


@pytest.mark.asyncio
async def test_one_explicit_request_proves_all_three_google_accounts_and_idempotent_ack(monkeypatch):
    db,calls,_=setup(monkeypatch)
    result=await recover(db)
    assert result['status']=='read_verified' and result['accounts_checked']==3
    assert result['provider_write_reached'] is False and result['campaign_write_reached'] is False
    assert {account for account,_ in calls}=={'123','234','345'} and len(calls)==6
    assert all('FROM customer' in query for _,query in calls)
    assert db.mezan_integrations_v2.rows[0]['connection_status']=='error'
    assert (await recover(db))==result and len(calls)==6
    assert db[report.GOOGLE_ADS_DAILY_COLLECTION].rows and all(row['provider']=='google_ads' for row in db[report.GOOGLE_ADS_DAILY_COLLECTION].rows)


@pytest.mark.asyncio
@pytest.mark.parametrize('invalid',['foreign','missing_connection','disconnected','unproved','not_owner'])
async def test_unproved_scope_never_reaches_google(monkeypatch,invalid):
    db,calls,_=setup(monkeypatch);body=BODY
    if invalid=='foreign':body=BODY.model_copy(update={'account_id':'999'})
    if invalid=='missing_connection':db.mezan_integrations_v2.rows=[]
    if invalid=='disconnected':db.mezan_integrations_v2.rows[0]['connection_status']='disconnected'
    if invalid=='unproved':db.mezan_integrations_v2.rows[0]['connection_provenance']='manual'
    if invalid=='not_owner':db.users.rows[0]['role']='viewer'
    with pytest.raises(HTTPException):await recover(db,body)
    assert calls==[]


@pytest.mark.asyncio
@pytest.mark.parametrize('failure',['rate_limit','identity','incomplete','foreign_row'])
async def test_partial_or_unproved_read_cannot_restore_global_connection(monkeypatch,failure):
    db,calls,original=setup(monkeypatch)
    async def read(client,*,access_token,account,query):
        if account['ad_account_id']=='234':
            if failure=='rate_limit':raise report.GoogleAdsReportingError('google_ads_rate_limited','limited',status_code=429,retryable=True)
            if failure=='identity' and 'LIMIT 1' in query:return report.StreamRows([{'customer':{'id':'999','currencyCode':'SAR','timeZone':'Asia/Riyadh'}}],True)
            if failure=='incomplete' and 'LIMIT 1' not in query:return report.StreamRows([],False)
            if failure=='foreign_row' and 'LIMIT 1' not in query:return report.StreamRows([{'customer':{'id':'999'}}],True)
        return await original(client,access_token=access_token,account=account,query=query)
    monkeypatch.setattr(report,'_search_stream',read)
    with pytest.raises(HTTPException):await recover(db)
    assert db.mezan_integrations_v2.rows[0]['connection_status']=='error'
    assert db[recovery.RECOVERIES].rows[0]['status']=='running'


@pytest.mark.asyncio
@pytest.mark.parametrize('race',['disconnect','reauth','generation','group','owner'])
async def test_concurrent_state_change_is_never_overwritten(monkeypatch,race):
    db,calls,original=setup(monkeypatch)
    async def read(client,*,access_token,account,query):
        result=await original(client,access_token=access_token,account=account,query=query)
        if account['ad_account_id']=='345' and 'LIMIT 1' not in query:
            if race=='disconnect':db.mezan_integrations_v2.rows[0]['connection_status']='disconnected'
            if race=='reauth':db.mezan_integrations_v2.rows[0]['permission_observation_id']='new-oauth'
            if race=='generation':db.mezan_integrations_v2.rows[0]['google_reporting_recovery_generation']=5
            if race=='group':db.mezan_integration_accounts_v2.rows.append({**db.mezan_integration_accounts_v2.rows[0],'ad_account_id':'456','external_account_id':'456'})
            if race=='owner':db.users.rows[0]['disabled']=True
        return result
    monkeypatch.setattr(report,'_search_stream',read)
    with pytest.raises(HTTPException):await recover(db)
    assert db.mezan_integrations_v2.rows[0]['connection_status']!='connected'


@pytest.mark.asyncio
async def test_concurrent_repeated_and_unknown_requests_stay_bounded(monkeypatch):
    db,calls,original=setup(monkeypatch);entered,resume=asyncio.Event(),asyncio.Event()
    async def read(*args,**kwargs):
        entered.set();await resume.wait();return await original(*args,**kwargs)
    monkeypatch.setattr(report,'_search_stream',read)
    task=asyncio.create_task(recover(db))
    try:
        await entered.wait()
        with pytest.raises(HTTPException) as duplicate:await recover(db)
        assert duplicate.value.status_code==409
        with pytest.raises(HTTPException) as rival:await recover(db,BODY.model_copy(update={'request_id':'recovery_request_0002'}))
        assert rival.value.status_code==429
    finally:
        resume.set();await task
    assert len(calls)==6


@pytest.mark.asyncio
async def test_missing_adwords_scope_is_not_bypassed(monkeypatch):
    original=report._credential
    db,calls,_=setup(monkeypatch)
    monkeypatch.setattr(report,'_credential',original)
    with pytest.raises(HTTPException) as error:await recover(db)
    assert error.value.detail['code']=='google_ads_scope_missing' and calls==[]


@pytest.mark.asyncio
async def test_registered_owner_route_accepts_only_explicit_scoped_recovery(monkeypatch):
    from fastapi import FastAPI,APIRouter
    from integrations_control_center.google_ads_routes import attach_google_ads_management_routes
    from httpx import AsyncClient as Client
    db,calls,_=setup(monkeypatch)
    router=APIRouter()
    async def user():return {'id':'owner'}
    attach_google_ads_management_routes(router,db,user)
    app=FastAPI();app.include_router(router,prefix='/integrations-v2')
    # Resolve the real class from before the offline Google context replacement.
    from httpx._client import AsyncClient as RealClient
    async with RealClient(transport=httpx.ASGITransport(app=app),base_url='http://test',trust_env=False) as client:
        invalid=await client.post('/integrations-v2/google_ads/reporting-read-verification',json={**BODY.model_dump(),'campaign_id':'unsafe'})
        assert invalid.status_code==422 and calls==[]
        result=await client.post('/integrations-v2/google_ads/reporting-read-verification',json=BODY.model_dump())
        assert result.status_code==200 and result.json()['accounts_checked']==3
    assert len(calls)==6


@pytest.mark.asyncio
async def test_unknown_ack_or_crash_keeps_durable_cooldown_and_no_auto_retry(monkeypatch):
    db,calls,_=setup(monkeypatch)
    async def unknown(*args,**kwargs):raise TimeoutError('unknown read acknowledgement')
    with pytest.raises(HTTPException) as failed:await recover(db,sync=unknown)
    assert failed.value.detail['code']=='google_reporting_recovery_unconfirmed'
    state=db[recovery.RECOVERIES].rows[0]
    assert state['status']=='running' and state['lease_until']>NOW.isoformat()
    with pytest.raises(HTTPException):await recover(db)
    with pytest.raises(HTTPException) as cooldown:await recover(db,BODY.model_copy(update={'request_id':'recovery_request_0002'}))
    assert cooldown.value.status_code==429 and calls==[]


@pytest.mark.asyncio
async def test_lost_completed_ack_replays_cached_result_without_new_google_read(monkeypatch):
    db,calls,_=setup(monkeypatch);collection=db[recovery.RECOVERIES];original=collection.update_one
    async def lost(query,update,**kwargs):
        result=await original(query,update,**kwargs)
        if update.get('$set',{}).get('status')=='completed':raise TimeoutError('lost durable completion ACK')
        return result
    monkeypatch.setattr(collection,'update_one',lost)
    with pytest.raises(HTTPException):await recover(db)
    assert len(calls)==6 and db.mezan_integrations_v2.rows[0]['connection_status']=='error'
    monkeypatch.setattr(collection,'update_one',original)
    result=await recover(db)
    assert result['status']=='read_verified' and len(calls)==6


@pytest.mark.asyncio
async def test_slow_native_group_proof_cannot_restore_connection(monkeypatch):
    db,calls,original=setup(monkeypatch);clock={'now':NOW}
    async def slow(*args,**kwargs):
        result=await original(*args,**kwargs)
        if kwargs['account']['ad_account_id']=='345' and 'LIMIT 1' not in kwargs['query']:clock['now']+=timedelta(seconds=91)
        return result
    monkeypatch.setattr(report,'_search_stream',slow)
    with pytest.raises(HTTPException) as error:
        await recovery.recover_google_reporting(db,{'id':'owner'},BODY,clock=lambda:clock['now'])
    assert error.value.detail['code']=='google_reporting_recovery_proof_expired'
    assert db.mezan_integrations_v2.rows[0]['connection_status']=='error'


from tests.test_google_ads_automation_authority_mongo import authority_db
from tests.test_google_ads_automation_policy_mongo import automation_mongo_uri

async def seed_real(monkeypatch,real_db):
    fake,calls,read=setup(monkeypatch)
    for name,collection in fake.collections.items():
        if collection.rows:await real_db[name].insert_many(deepcopy(collection.rows))
    return calls,read


@pytest.mark.asyncio
async def test_real_mongo_diagnostic_read_preserves_global_state_and_durable_replay(monkeypatch,authority_db):
    calls,_=await seed_real(monkeypatch,authority_db)
    result=await recover(authority_db)
    assert result['accounts_checked']==3 and len(calls)==6
    stored=await authority_db.mezan_integrations_v2.find_one({'user_id':'owner','provider':'google_ads'})
    assert stored['connection_status']=='error' and 'google_reporting_recovery_generation' not in stored
    assert (await recover(authority_db))==result and len(calls)==6


@pytest.mark.asyncio
async def test_real_mongo_recovery_lease_excludes_concurrent_requests(monkeypatch,authority_db):
    calls,read=await seed_real(monkeypatch,authority_db);entered,resume=asyncio.Event(),asyncio.Event()
    async def blocked(*args,**kwargs):
        entered.set();await resume.wait();return await read(*args,**kwargs)
    monkeypatch.setattr(report,'_search_stream',blocked)
    task=asyncio.create_task(recover(authority_db))
    try:
        await entered.wait()
        with pytest.raises(HTTPException):await recover(authority_db,BODY.model_copy(update={'request_id':'rival_recovery_request_02'}))
    finally:
        resume.set();await task
    assert len(calls)==6


@pytest.mark.asyncio
@pytest.mark.parametrize('segments',[{'date':'1999-01-01','hour':2},{'date':'2026-10-27','hour':99},
    {'date':'2026-10-27','hour':-1},{'date':'2026-10-27','hour':True},{'date':'2026-10-27','hour':2.5},{}])
async def test_malformed_or_out_of_day_rows_never_become_zero_facts(monkeypatch,segments):
    db,calls,original=setup(monkeypatch)
    async def malformed(client,*,access_token,account,query):
        if 'LIMIT 1' in query:return await original(client,access_token=access_token,account=account,query=query)
        return report.StreamRows([{'customer':{'id':account['ad_account_id'],'currencyCode':'SAR','timeZone':'Asia/Riyadh'},
            'segments':segments,'metrics':{'costMicros':'1000000'}}],True)
    monkeypatch.setattr(report,'_search_stream',malformed)
    with pytest.raises(HTTPException):await recover(db)
    assert db.mezan_integrations_v2.rows[0]['connection_status']=='error'
    assert not db[report.GOOGLE_ADS_DAILY_COLLECTION].rows


@pytest.mark.asyncio
async def test_late_group_insert_can_never_promote_global_connection_health(monkeypatch):
    db,calls,_=setup(monkeypatch);before=deepcopy(db.mezan_integrations_v2.rows[0])
    original=report._recovery_owned_accounts;reads={'count':0}
    async def late(database,user_id):
        rows,signature=await original(database,user_id);reads['count']+=1
        # The wrapper holds the original helper; this is the reporting final read.
        if reads['count']==2:
            database.mezan_integration_accounts_v2.rows.append({**database.mezan_integration_accounts_v2.rows[0],
                'ad_account_id':'456','external_account_id':'456'})
        return rows,signature
    monkeypatch.setattr(report,'_recovery_owned_accounts',late)
    result=await recover(db)
    assert db.mezan_integrations_v2.rows[0]==before
    assert result['status']=='read_verified' and result['connection_restored'] is False
    assert set(result['verified_account_ids'])=={'123','234','345'} and '456' not in {account for account,_ in calls}
    assert len(db.mezan_integration_accounts_v2.rows)==4


@pytest.mark.asyncio
async def test_diagnostic_read_preserves_all_global_health_fields_exactly(monkeypatch):
    db,calls,_=setup(monkeypatch)
    db.mezan_integrations_v2.rows[0].update(health_score=40,data_quality='unavailable',checked_at='previous-check')
    before=deepcopy(db.mezan_integrations_v2.rows[0])
    result=await recover(db)
    assert result['connection_restored'] is False and result['status']=='read_verified'
    assert db.mezan_integrations_v2.rows[0]==before
