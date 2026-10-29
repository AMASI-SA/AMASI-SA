"""Unattached owner-only saved-plan observations route; no execution authority."""
import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import re

from fastapi import Depends, Query
from .google_ads_client import problem
from .google_ads_management import principal
from .google_commercial_plan import PLANS
from .google_product_catalog_reader import read_catalog_products
from .google_product_evidence import ProductEvidenceRequest, ProductSourceRecord, build_product_evidence
from .google_store_product_observations import observe_product_lines, windows

MAX_DOCUMENTS=1000
PROJECTION={'_id':0,'user_id':1,'order_id':1,'order_number':1,'order_date':1,
 'order_status_slug':1,'order_status':1,'payment_status':1,'currency':1,'original_currency':1,
 'cancelled_at':1,'canceled_at':1,'refunded_at':1,'refunds':1,'returns':1,'refunded_amount':1,'refund_amount':1,'returned_quantity':1}
for field in ('id','reference_id','date','created_at','status','payment_status','payment.status','currency','original_currency','amounts.total.currency',
              'cancelled_at','canceled_at','refunded_at','refunds','returns','refunded_amount','refund_amount','returned_quantity',
              'items.id','items.product.id','items.product.parent_id','items.product_id','items.parent_product_id','items.quantity','items.refunds','items.returns','items.refunded_quantity','items.returned_quantity'):
    PROJECTION['raw_by_source.salla_direct.'+field]=1


def _conflict():return problem('google_commercial_plan_revision_conflict','تغيرت الخطة؛ أعد تحميل نسختها المحفوظة.',409)


async def read_store_observations(db,*,owner_id,product_ids,as_of):
    period=windows(as_of)['last_90_days']
    from datetime import date
    lower=(date.fromisoformat(period['from'])-timedelta(days=1)).isoformat()
    upper=(date.fromisoformat(period['to'])+timedelta(days=1)).isoformat()
    if not product_ids:return observe_product_lines([],owner_id=owner_id,product_ids=[],as_of=as_of)
    query={'user_id':owner_id,'order_date':{'$gte':lower,'$lte':upper},'$expr':{'$lte':[{'$bsonSize':'$$ROOT'},32768]}}
    cursor=(db['unified_orders'].find(query,PROJECTION).hint([('user_id',1),('order_date',-1)])
            .limit(MAX_DOCUMENTS+1).max_time_ms(3000))
    rows=await cursor.to_list(length=MAX_DOCUMENTS+1)
    if any(not isinstance(r,dict) or r.get('user_id')!=owner_id or not isinstance(r.get('order_date'),str)
           or not lower<=r['order_date']<=upper for r in rows):raise ValueError('read scope unproved')
    result=observe_product_lines(rows,owner_id=owner_id,product_ids=product_ids,as_of=as_of,truncated=len(rows)>MAX_DOCUMENTS)
    if sum(len(p['currency_groups']) for p in result['products'])>200:raise ValueError('output scope limit')
    result['limitations']+=['normalized_date_plus_minus_one_is_candidate_discovery_only','oversized_documents_excluded_unquantified','index_required_no_fallback']
    return result


def _pure_context(catalog,result,plan_id,revision,owner_id,as_of):
    if not catalog['products']:return None
    records=[]
    account='owner-projection:'+hashlib.sha256(owner_id.encode()).hexdigest()[:24]
    by_id={p.product_id for p in catalog['products']}
    for product in result['products']:
        if product['product_id'] not in by_id:continue
        for group in product['currency_groups']:
            for name,observed in group['windows'].items():
                if observed['observed_lines'] is None:continue
                period=result['windows'][name]
                ref=hashlib.sha256(json.dumps([product['product_id'],account,group['currency_code'],period,observed,as_of.isoformat()],sort_keys=True).encode()).hexdigest()
                records.append(ProductSourceRecord(product_id=product['product_id'],provider='store_orders',account_id=account,
                    currency_code=group['currency_code'],timezone='Asia/Riyadh',date_from=period['from'],date_to=period['to'],
                    complete=False,verification='unverified',observed_at=as_of,evidence_ref='stored-observation:'+ref))
    return build_product_evidence(ProductEvidenceRequest(plan_id=plan_id,plan_revision=revision,max_observation_age_seconds=86400,as_of=as_of,products=catalog['products'],records=records))


def attach_google_plan_store_observation_routes(router,db,current_user,*,clock=None):
    async def owner(user=Depends(current_user)):
        actor=await principal(db,user,write=False)
        if not actor.get('owner'):raise problem('google_ads_owner_required','مراجعة أدلة المنتجات متاحة للمالك فقط.',403)
        return actor

    @router.get('/google_ads/commercial-plans/{plan_id}/store-observations')
    async def review(plan_id:str,expected_revision:int=Query(ge=1),actor=Depends(owner)):
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,128}',plan_id):raise problem('google_commercial_plan_id_invalid','معرف الخطة غير صالح.',422)
        scope={'user_id':actor['user_id'],'plan_id':plan_id}
        async def bounded_read():
            plan=await db[PLANS].find_one(scope,{'_id':0,'revision':1,'fields':1})
            if not plan:raise problem('google_commercial_plan_not_found','الخطة غير موجودة.',404)
            if type(plan.get('revision')) is not int or plan['revision']!=expected_revision or not isinstance(plan.get('fields'),dict):raise _conflict()
            fields=deepcopy(plan['fields']);pins=fields.get('pinned_product_ids');excluded=fields.get('excluded_product_ids')
            if (not isinstance(pins,list) or not isinstance(excluded,list) or len(pins)>100 or len(excluded)>100
                or any(not isinstance(p,str) or not re.fullmatch(r'[1-9][0-9]{0,29}',p) for p in pins+excluded)
                or len(set(pins))!=len(pins) or len(set(excluded))!=len(excluded)):
                raise problem('google_store_plan_products_unproved','معرفات المنتجات تحتاج مراجعة.',422)
            ids=[p for p in pins if p not in excluded]
            as_of=clock() if clock else datetime.now(timezone.utc)
            catalog=await read_catalog_products(db,owner_id=actor['user_id'],product_ids=ids,as_of=as_of,max_age_seconds=86400) if ids else {'products':[],'missing_or_unverified_product_ids':[]}
            result=await read_store_observations(db,owner_id=actor['user_id'],product_ids=ids,as_of=as_of)
            evidence=_pure_context(catalog,result,plan_id,expected_revision,actor['user_id'],as_of)
            current=await db[PLANS].find_one(scope,{'_id':0,'revision':1,'fields':1})
            if not current or current.get('revision')!=expected_revision or current.get('fields')!=fields:raise _conflict()
            return {'plan_id':plan_id,'plan_revision':expected_revision,'product_ids_reviewed':ids,'observations':result,
                'catalog_missing_or_unverified_ids':catalog['missing_or_unverified_product_ids'],'evidence':evidence,
                'provider_writes':0,'execution_authorized':False}
        try:return await asyncio.wait_for(bounded_read(),timeout=6)
        except (ValueError,TimeoutError):raise problem('google_store_observations_unavailable','تعذر إثبات قراءة محدودة؛ لم تنفذ أي تغييرات.',503) from None
        except Exception as exc:
            from fastapi import HTTPException
            if isinstance(exc,HTTPException):raise
            raise problem('google_store_observations_unavailable','تعذر إثبات قراءة محدودة؛ لم تنفذ أي تغييرات.',503) from None
