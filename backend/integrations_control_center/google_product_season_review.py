"""Current owner-declared product fit. Never independent fit or paid authority."""
from datetime import datetime, timezone
import hashlib
import re
from typing import Literal
from bson import BSON
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError
from auth import account_is_disabled
from .google_ads_client import problem
from .google_ads_management import principal
from .google_commercial_plan import PLANS, CommercialPlanFields

REVIEWS='mezan_google_product_season_reviews_v1'
OWNER_PROJECTION={'_id':0,'id':1,'role':1,'disabled':1,'is_active':1,'deleted_at':1,'password_updated_at':1}
PLAN_PROJECTION={'_id':0,'revision':1,'fields':1,'updated_by':1,'updated_at':1}
def digest(value): return hashlib.sha256(BSON.encode(value)).hexdigest()
def canonical(value):
    if not isinstance(value,str) or not re.fullmatch(r'[1-9][0-9]{0,29}',value): raise ValueError('canonical identity required')
    return value

def review_id(owner,plan,revision,category,product):
    return 'product-season:'+digest(dict(owner=owner,plan=plan,revision=revision,category=category,product=product))

class ReviewInput(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    expected_plan_revision:int=Field(ge=1,le=1000000)
    expected_review_revision:int=Field(ge=0,le=1000000)
    category_id:str=Field(pattern=r'^[1-9][0-9]{0,29}$')
    decision:Literal['suitable_for_test','not_suitable','needs_review']
    reason:str=Field(min_length=1,max_length=1000)
    acknowledge_owner_review:bool
    @field_validator('reason')
    @classmethod
    def reason_valid(cls,value):
        if value!=value.strip() or not value: raise ValueError('reason required')
        return value
    @field_validator('acknowledge_owner_review')
    @classmethod
    def ack(cls,value):
        if value is not True: raise ValueError('explicit owner review required')
        return value

async def owner_snapshot(db,user):
    actor=await principal(db,user)
    if not actor.get('owner'): raise problem('google_ads_owner_required','مراجعة المنتجات متاحة للمالك فقط.',403)
    rows=await db.users.find({'id':actor['user_id']},OWNER_PROJECTION).limit(2).max_time_ms(1000).to_list(length=2)
    if (len(rows)!=1 or rows[0].get('id')!=actor['user_id'] or rows[0].get('role')!='owner' or account_is_disabled(rows[0])
        or digest({'stamp':rows[0].get('password_updated_at')})!=digest({'stamp':user.get('password_updated_at')})):
        raise problem('google_ads_owner_required','تعذر إثبات صلاحية المالك الحالية.',403)
    return actor['user_id'],rows[0]

async def capture_scope(db,user,plan_id,revision):
    if not isinstance(plan_id,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}',plan_id):raise problem('google_commercial_plan_id_invalid','معرف الخطة غير صالح.',422)
    owner,owner_row=await owner_snapshot(db,user)
    rows=await db[PLANS].find({'user_id':owner,'plan_id':plan_id},PLAN_PROJECTION).limit(2).max_time_ms(1000).to_list(length=2)
    if not rows:raise problem('google_commercial_plan_not_found','الخطة غير موجودة.',404)
    if len(rows)!=1:raise problem('google_commercial_plan_scope_unproved','تعذر إثبات نسخة خطة واحدة؛ لم تُنفذ المراجعة.',409)
    row=rows[0]
    if type(row.get('revision')) is not int or row['revision']!=revision:raise problem('google_commercial_plan_revision_conflict','تغيرت الخطة؛ أعد قراءتها.',409)
    fields=CommercialPlanFields.model_validate(row['fields'])
    return {'owner_id':owner,'owner':owner_row,'plan_id':plan_id,'revision':revision,'plan':row,'fields':fields,
        'password_revision':digest({'stamp':owner_row.get('password_updated_at')}),'fields_digest':digest(row['fields'])}

async def verify_scope(db,user,scope):
    fresh=await capture_scope(db,user,scope['plan_id'],scope['revision'])
    if BSON.encode(fresh['owner'])!=BSON.encode(scope['owner']):raise problem('google_ads_owner_required','تغيرت صلاحية المالك أثناء القراءة.',403)
    if BSON.encode(fresh['plan'])!=BSON.encode(scope['plan']):raise problem('google_commercial_plan_revision_conflict','تغيرت الخطة أثناء المراجعة.',409)
    # Last awaited fence checks current owner after the plan read. Separate
    # document reads do not serialize subsequent external writes.
    _,last_owner=await owner_snapshot(db,user)
    if BSON.encode(last_owner)!=BSON.encode(scope['owner']):raise problem('google_ads_owner_required','تغيرت صلاحية المالك أثناء المراجعة.',403)

RECORD_KEYS={'_id','user_id','plan_id','plan_revision','category_id','product_id','revision','decision','reason','reviewed_by','reviewed_at','authenticated_owner_password_revision','plan_fields_digest','review_digest'}
def valid_record(row,scope,category,product):
    expected=review_id(scope['owner_id'],scope['plan_id'],scope['revision'],category,product)
    if (not isinstance(row,dict) or set(row)!=RECORD_KEYS or len(BSON.encode(row))>8192
        or row['_id']!=expected or row['user_id']!=scope['owner_id'] or row['plan_id']!=scope['plan_id']
        or type(row['plan_revision']) is not int or row['plan_revision']!=scope['revision']
        or row['category_id']!=category or row['product_id']!=product or row['reviewed_by']!=scope['owner_id']
        or type(row['revision']) is not int or not 1<=row['revision']<=1000000
        or row['decision'] not in {'suitable_for_test','not_suitable','needs_review'}
        or not isinstance(row['reason'],str) or not 1<=len(row['reason'])<=1000 or row['reason']!=row['reason'].strip()):raise ValueError('review record unproved')
    stamp=datetime.fromisoformat(row['reviewed_at'])
    if stamp.tzinfo is None or stamp.utcoffset() is None:raise ValueError('review timestamp unproved')
    if any(not isinstance(row[k],str) or not re.fullmatch(r'[a-f0-9]{64}',row[k]) for k in ('authenticated_owner_password_revision','plan_fields_digest','review_digest')):raise ValueError('review provenance unproved')
    if digest({k:v for k,v in row.items() if k!='review_digest'})!=row['review_digest']:raise ValueError('review digest changed')
    return row

async def read_reviews(db,scope,category,products):
    ids={review_id(scope['owner_id'],scope['plan_id'],scope['revision'],category,p):p for p in products}
    rows=await db[REVIEWS].find({'_id':{'$in':list(ids)}}).limit(6).max_time_ms(1000).to_list(length=6)
    if len(rows)>len(products):raise ValueError('review row limit')
    result={}
    for row in rows:
        if row.get('_id') not in ids or ids[row['_id']] in result:raise ValueError('review scope unproved')
        product=ids[row['_id']];result[product]=valid_record(row,scope,category,product)
    return result

def current_owner_reviews(rows,scope,as_of):
    output={}
    for product,row in rows.items():
        if (row['authenticated_owner_password_revision']==scope['password_revision'] and row['plan_fields_digest']==scope['fields_digest']
            and datetime.fromisoformat(row['reviewed_at'])<=as_of):
            output[product]={'decision':row['decision'],'reason':row['reason'],'revision':row['revision'],'reviewed_at':row['reviewed_at'],
                'review_digest':row['review_digest'],'meaning':'owner_declared_fit_not_independent_verification'}
    return output

async def save_product_season_review(db,*,authenticated_user,plan_id,product_id,body,clock=lambda:datetime.now(timezone.utc)):
    product=canonical(product_id);scope=await capture_scope(db,authenticated_user,plan_id,body.expected_plan_revision)
    fields=scope['fields'];category=body.category_id
    if product in fields.excluded_product_ids:raise problem('google_product_review_excluded','المنتج مستبعد في الخطة.',409)
    if fields.category_ids and category not in fields.category_ids or not fields.category_ids and fields.category_url is None:
        raise problem('google_product_review_category_unproved','احفظ نطاق القسم أولًا.',409)
    before=await read_reviews(db,scope,category,[product]);old=before.get(product)
    revision=old['revision'] if old else 0
    if revision!=body.expected_review_revision:raise problem('google_product_review_revision_conflict','تغيرت مراجعة المنتج؛ أعد قراءتها.',409)
    now=clock()
    if now.tzinfo is None or now.utcoffset() is None:raise ValueError('aware clock required')
    row={'_id':review_id(scope['owner_id'],plan_id,scope['revision'],category,product),'user_id':scope['owner_id'],'plan_id':plan_id,
        'plan_revision':scope['revision'],'category_id':category,'product_id':product,'revision':revision+1,'decision':body.decision,'reason':body.reason,
        'reviewed_by':scope['owner_id'],'reviewed_at':now.isoformat(),'authenticated_owner_password_revision':scope['password_revision'],'plan_fields_digest':scope['fields_digest']}
    row['review_digest']=digest(row)
    try:
        if old:
            query={'_id':row['_id'],'revision':revision,'review_digest':old['review_digest'],'$expr':{'$eq':[{'$type':'$revision'},'int']}}
            changed=await db[REVIEWS].find_one_and_replace(query,row,return_document=ReturnDocument.AFTER)
            if not changed:raise problem('google_product_review_revision_conflict','تغيرت مراجعة المنتج؛ أعد قراءتها.',409)
        else:await db[REVIEWS].insert_one(row)
    except DuplicateKeyError:raise problem('google_product_review_revision_conflict','تغيرت مراجعة المنتج؛ أعد قراءتها.',409) from None
    try:
        latest=await read_reviews(db,scope,category,[product])
        if latest.get(product)!=row:raise problem('google_product_review_revision_conflict','تغيرت المراجعة أثناء الحفظ.',409)
        await verify_scope(db,authenticated_user,scope)
    except Exception as exc:
        raise problem('google_product_review_save_not_confirmed','لم يُؤكد الحفظ؛ أعد قراءة المراجعة قبل أي محاولة أخرى.',exc.status_code if isinstance(exc,HTTPException) else 503,review_may_have_persisted=True) from None
    return {'plan_id':plan_id,'plan_revision':scope['revision'],'category_id':category,'product_id':product,'review_revision':row['revision'],
        'decision':row['decision'],'reason':row['reason'],'reviewed_at':row['reviewed_at'],'review_digest':row['review_digest'],
        'meaning':'owner_declared_fit_not_independent_verification','review_writes':1,'provider_writes':0,'execution_authorized':False}

async def read_product_season_review_state(db,*,authenticated_user,plan_id,expected_revision,category_id,selected_ids,clock=lambda:datetime.now(timezone.utc)):
    category=canonical(category_id)
    if not isinstance(selected_ids,list) or not 1<=len(selected_ids)<=5 or len(set(selected_ids))!=len(selected_ids):raise ValueError('selected review bound')
    products=[canonical(p) for p in selected_ids]
    scope=await capture_scope(db,authenticated_user,plan_id,expected_revision)
    rows=await read_reviews(db,scope,category,products)
    latest=await read_reviews(db,scope,category,products)
    if BSON.encode({'rows':rows})!=BSON.encode({'rows':latest}):raise ValueError('review changed')
    await verify_scope(db,authenticated_user,scope)
    current=current_owner_reviews(rows,scope,clock())
    return {'plan_id':plan_id,'plan_revision':expected_revision,'category_id':category,
        'products':[{'product_id':p,'review_revision':rows[p]['revision'] if p in rows else 0,
            'current_owner_review':p in current,'decision':current[p]['decision'] if p in current else 'unknown',
            'reason':current[p]['reason'] if p in current else None,'meaning':'owner_declared_fit_not_independent_verification'} for p in products],
        'provider_writes':0,'review_writes':0,'execution_authorized':False}
