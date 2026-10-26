"""Diagnostic Google-only analytical reads; NEVER restore management connection.

A verified historical read snapshot cannot atomically prove the current account
group. Global connection health remains untouched; verified OAuth relinking or
an atomic group-writer contract is still required to restore native management.
"""
from __future__ import annotations

import asyncio
from datetime import datetime,timedelta,timezone
import re
import uuid

from pydantic import BaseModel,ConfigDict,Field
from pymongo.errors import DuplicateKeyError

from .google_ads_automation_policy import _owner
from .google_ads_client import identifier,problem
from .google_ads_reporting import GoogleAdsReportingError,run_google_ads_reporting_sync,_recovery_owned_accounts

RECOVERIES='mezan_google_ads_reporting_recovery_v1'
LEASE=timedelta(minutes=3)
COOLDOWN=timedelta(minutes=5)


class GoogleReportingRecoveryInput(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    account_id:str=Field(pattern=r'^[0-9]{1,30}$')
    request_id:str=Field(pattern=r'^[A-Za-z0-9_-]{16,128}$')


def now():return datetime.now(timezone.utc)


async def recover_google_reporting(db,user,body,*,sync=run_google_ads_reporting_sync,clock=now):
    actor=await _owner(db,user)
    user_id=actor['user_id'];account_id=identifier(body.account_id)
    scope={'user_id':user_id,'provider':'google_ads'}
    connection=await db.mezan_integrations_v2.find_one(scope,{'_id':0})
    if (not connection or connection.get('connection_provenance')!='api_connection'
        or connection.get('connection_status') not in {'error','connected'}):
        raise problem('google_reporting_recovery_unproved','الربط غير مثبت؛ أعد ربط جوجل من إعدادات المتجر.',409)
    try:
        rows,_=await _recovery_owned_accounts(db,user_id)
    except GoogleAdsReportingError as exc:
        raise problem(exc.code,'لم تثبت حسابات جوجل المرتبطة بهذا المتجر.',409) from None
    if account_id not in {row['ad_account_id'] for row in rows}:
        raise problem('google_reporting_recovery_account_unproved','الحساب المحدد غير متاح ضمن حسابات جوجل المثبتة لمتجرك.',409)
    collection=db[RECOVERIES]
    await collection.create_index([('user_id',1)],unique=True,name='google_reporting_recovery_owner')
    prior=await collection.find_one({'user_id':user_id},{'_id':0})
    instant=clock()
    if prior and prior.get('request_id')==body.request_id:
        if prior.get('status')=='completed':return prior['result']
        raise problem('google_reporting_recovery_already_requested','هذا الطلب بدأ سابقًا؛ حدّث العرض قبل تقديم طلب جديد.',409)
    if connection['connection_status']!='error':
        raise problem('google_reporting_recovery_not_needed','ربط جوجل متاح؛ حدّث بيانات الحملات.',409)
    if prior and (prior.get('lease_until','')>instant.isoformat() or prior.get('next_allowed_at','')>instant.isoformat()):
        raise problem('google_reporting_recovery_wait','توجد قراءة حديثة أو معلّقة؛ انتظر خمس دقائق قبل طلب جديد.',429)
    claim=uuid.uuid4().hex
    record={'user_id':user_id,'account_id':account_id,'request_id':body.request_id,'claim_id':claim,'status':'running',
        'started_at':instant.isoformat(),'lease_until':(instant+LEASE).isoformat(),'next_allowed_at':(instant+COOLDOWN).isoformat()}
    try:
        if prior:
            changed=await collection.update_one({'user_id':user_id,'claim_id':prior.get('claim_id'),
                'request_id':prior.get('request_id'),'lease_until':prior.get('lease_until')},{'$set':record,'$unset':{'result':''}})
            if not changed.matched_count:raise problem('google_reporting_recovery_busy','بدأ طلب استعادة آخر؛ حدّث العرض.',409)
        else:await collection.insert_one(record)
    except DuplicateKeyError:
        raise problem('google_reporting_recovery_busy','بدأ طلب استعادة آخر؛ حدّث العرض.',409) from None
    # The same request is never automatically replayed after an unknown ACK.
    # A later explicit request has a new identity and obeys the durable cooldown.
    day=(instant.date()-timedelta(days=1)).isoformat()
    try:
        await _owner(db,user)
        result=await asyncio.wait_for(sync(db,user_id,date_from=day,date_to=day,account_id=account_id,
            recovery_mode=True,now=clock),timeout=90)
        if result.get('read_verified') is not True:
            raise problem('google_reporting_recovery_read_unconfirmed','لم تثبت قراءة جوجل كاملة؛ بقي الربط بحاجة للمراجعة.',409)
        public={'provider':'google_ads','account_id':account_id,'request_id':body.request_id,'status':'read_verified','connection_restored':False,'verified_account_ids':result.get('verified_account_ids',[]),
            'report_date':day,'source_only':True,'provider_write_reached':False,'campaign_write_reached':False,
            'accounting_write_reached':False,'rows_saved':result.get('rows_saved',0),'accounts_checked':result.get('accounts_complete',0)}
        changed=await collection.update_one({'user_id':user_id,'claim_id':claim,'status':'running'},
            {'$set':{'status':'completed','result':public,'finished_at':clock().isoformat(),'lease_until':clock().isoformat()}})
        if not changed.matched_count:raise problem('google_reporting_recovery_ack_unconfirmed','إقرار القراءة غير مؤكد؛ حدّث العرض دون تكرار الطلب.',503)
        return public
    except BaseException as exc:
        # Keep the lease/cooldown on every unknown outcome, including cancellation.
        if isinstance(exc,GoogleAdsReportingError):
            raise problem(exc.code,'تعذر إثبات قراءة جوجل؛ راجع الربط ثم اطلب الاستعادة بعد المهلة.',exc.status_code) from None
        if isinstance(exc,asyncio.CancelledError):raise
        if hasattr(exc,'status_code'):raise
        raise problem('google_reporting_recovery_unconfirmed','لم يثبت اكتمال القراءة؛ حدّث العرض قبل طلب جديد.',503) from None
