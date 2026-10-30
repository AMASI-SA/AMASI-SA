"""Authenticated configured funded grants; no activation or provider dispatch."""
from datetime import datetime,timedelta,timezone
from decimal import Decimal
import re
import uuid
from typing import Literal
from pydantic import BaseModel,ConfigDict,Field,ValidationError
from . import google_ads_management as management
from . import google_ads_funded_creation_contract as pure
from .google_ads_automation_policy import _account_scope
from .google_ads_client import GoogleAdsClient,identifier,problem

GRANTS='mezan_google_ads_funded_creation_grants_v1'
RUNS='mezan_google_ads_funded_creation_runs_v1'
SEMANTICS='AVERAGE_DAILY_COMMITMENT'
AVERAGE_ACK_VERSION='average-daily-commitment-v1'
AVERAGE_ACK_TEXT='هذه ميزانية جوجل اليومية المتوسطة وحدود التزامات الميزانيات، وليست سقفًا صارمًا للإنفاق الفعلي يوميًا أو إجمالًا'
AVERAGE_ACK_DIGEST=management.digest({'version':AVERAGE_ACK_VERSION,'text':AVERAGE_ACK_TEXT,'semantics':SEMANTICS})
AUTH_FIELDS=('id','role','disabled','is_active','deleted_at','password_updated_at')


def _auth_fields(user):
    result={}
    for key in AUTH_FIELDS:
        value=user.get(key)
        if key in {'password_updated_at','deleted_at'} and isinstance(value,datetime):
            value=(value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)).isoformat()
        if key=='password_updated_at' and value not in (None,''):
            stamp=pure._instant(value)
            if stamp is None:fail('google_funded_owner_auth_unproved')
            value=stamp.isoformat()
        if value is not None and type(value) not in (str,bool):fail('google_funded_owner_auth_unproved')
        result[key]={'present':key in user,'value':value}
    return result


def _document_id(value):
    from bson import ObjectId
    if isinstance(value,ObjectId):return {'kind':'objectid','value':str(value)}
    if isinstance(value,str) and 0<len(value)<=128:return {'kind':'string','value':value}
    fail('google_funded_owner_auth_unproved')


async def read_owner_auth(db,*,user_id,expected=None,authenticated_user=None):
    # Only nonsecret auth projection. The caller's authenticated_user must be
    # the server auth dependency result, NEVER HTTP JSON or a worker-built ID.
    if expected is None and authenticated_user is None:fail('google_funded_owner_auth_unproved')
    rows=await db.users.find({'id':user_id},{'_id':1,**{key:1 for key in AUTH_FIELDS}}).limit(2).max_time_ms(2000).to_list(length=2)
    if len(rows)!=1 or '_id' not in rows[0]:fail('google_funded_owner_auth_unproved')
    owner=rows[0]
    actor=await management.principal(db,owner,write=True)
    if not actor.get('owner') or actor['actor_id']!=user_id:fail('google_ads_owner_required')
    fields=_auth_fields(owner)
    if authenticated_user is not None and _auth_fields(authenticated_user)!=fields:
        fail('google_funded_authenticated_owner_changed')
    binding={'user_id':user_id,'document_id':_document_id(owner['_id']),'fields':fields}
    if expected is not None and expected!=binding:fail('google_funded_owner_auth_changed')
    return actor,binding,owner


async def capture_owner_auth(db,user):
    if not isinstance(user,dict) or not isinstance(user.get('id'),str):fail('google_ads_authentication_required')
    actor,binding,_=await read_owner_auth(db,user_id=user['id'],authenticated_user=user)
    return actor,binding


def public_google_funded_review(review):
    return {key:value for key,value in review.items() if not key.startswith('_')}

class FundedGrantReviewInput(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    average_daily_acknowledged: Literal[True]|None=None
    average_daily_ack_version: str|None=None
    average_daily_ack_digest: str|None=None
    mode: Literal['create_new_enabled_search']
    spend_semantics: Literal['AVERAGE_DAILY_COMMITMENT']
    request_key: str=Field(pattern=r'^[A-Za-z0-9_-]{8,128}$')
    expected_revision: int=Field(ge=0,le=2**63-3)
    owner_generation: int=Field(ge=1,le=2**63-2)
    draft_id: str=Field(min_length=1,max_length=160)
    draft_revision: int=Field(ge=1,le=2**63-2)
    catalog_max_age_seconds: int=Field(ge=60,le=86400)
    valid_from: str=Field(max_length=64)
    expires_at: str=Field(max_length=64)
    budget_native: str=Field(pattern=pure._MONEY)
    max_campaign_daily_budget_native: str=Field(pattern=pure._MONEY)
    max_account_daily_budget_native: str=Field(pattern=pure._MONEY)


def fail(code):
    raise problem(code,'تعذر إثبات تفويض إنشاء جوجل؛ لم يُمنح إذن إنفاق.',409)


def grant_key(user_id,account_id,request_key):
    return 'funded_grant_'+management.digest({'user_id':user_id,'account_id':account_id,'request_key':request_key})


async def ensure_indexes(db):
    await db[GRANTS].create_index([('user_id',1),('account_id',1),('request_key',1)],unique=True,name='google_funded_grant_scope')
    await db[RUNS].create_index([('user_id',1),('account_id',1),('grant_key',1)],unique=True,name='google_funded_run_slot')
    await db[RUNS].create_index([('user_id',1),('account_id',1),('creation_identity',1)],unique=True,name='google_funded_run_identity')


def review_digest(review):
    return management.digest({key:value for key,value in review.items() if key not in {'review_digest','grant'}})


def validate_record(row):
    if (not isinstance(row,dict) or row.get('spend_semantics')!=SEMANTICS
        or row.get('state') not in {'configured','active','revoked'} or not isinstance(row.get('review'),dict)
        or row.get('review_digest')!=review_digest(row['review'])):
        fail('google_funded_grant_unproved')
    try:grant=pure.FundedCreationGrant.model_validate(row['grant'])
    except (ValidationError,KeyError,TypeError):fail('google_funded_grant_unproved')
    if ((type(row.get('revision')) is not int or row['revision']<grant.revision
            or row.get('state')!='revoked' and row['revision']!=grant.revision) or row.get('user_id')!=grant.user_id or row.get('account_id')!=grant.account_id
        or row.get('request_key')!=grant.request_key or row.get('grant_key')!=grant_key(grant.user_id,grant.account_id,grant.request_key)
        or grant.owner_review_digest!=row['review_digest'] or row['review'].get('spend_semantics')!=SEMANTICS
        or pure.funded_creation_draft_digest(row['review']['draft'])!=grant.draft_digest):
        fail('google_funded_grant_unproved')
    for key,value in grant.model_dump(mode='json').items():
        if key not in {'owner_review_digest','draft_digest'} and row['review'].get(key)!=value:
            fail('google_funded_grant_unproved')
    return grant


async def review_google_funded_grant(db,*,user,account_id,body,current=None,client_factory=GoogleAdsClient):
    actor,owner_auth=await capture_owner_auth(db,user)
    try:body=FundedGrantReviewInput.model_validate(body)
    except ValidationError:fail('google_funded_grant_input_invalid')
    if body.mode!='create_new_enabled_search' or body.spend_semantics!=SEMANTICS:
        fail('google_funded_spend_semantics_unsupported')
    acknowledgment=(body.average_daily_acknowledged,body.average_daily_ack_version,body.average_daily_ack_digest)
    if acknowledgment not in ((None,None,None),(True,AVERAGE_ACK_VERSION,AVERAGE_ACK_DIGEST)):
        fail('google_funded_spend_acknowledgment_unproved')
    scope=await _account_scope(db,actor,account_id)
    row=await db[management.DRAFTS].find_one({'user_id':actor['user_id'],'draft_id':body.draft_id,
        'revision':body.draft_revision,'account_id':scope['account_id']},{'_id':0})
    if not row:fail('google_funded_draft_changed')
    draft=pure._draft(row['draft']).model_dump(mode='json')
    started=current or management.now()
    client,_=await management.account_client(db,**scope,client_factory=client_factory)
    try:metadata=await client.account_metadata()
    finally:await client.close()
    canonical=metadata.get('account_id')
    if (not isinstance(canonical,str) or not re.fullmatch(r'[1-9][0-9]{0,19}',canonical)
        or canonical!=scope['account_id'] or metadata.get('identity_verified') is not True
        or draft['account_id']!=canonical):
        fail('google_funded_native_account_unproved')
    grant={'schema_version':1,'mode':body.mode,**scope,'revision':body.expected_revision+1,
        'owner_generation':body.owner_generation,'reviewed_by':actor['actor_id'],'owner_review_digest':'0'*64,
        'valid_from':body.valid_from,'expires_at':body.expires_at,'currency_code':metadata.get('currency_code'),
        'time_zone':metadata.get('time_zone'),'budget_native':body.budget_native,
        'max_campaign_daily_budget_native':body.max_campaign_daily_budget_native,
        'max_account_daily_budget_native':body.max_account_daily_budget_native,
        'draft_digest':pure.funded_creation_draft_digest(draft),'request_key':body.request_key}
    try:
        typed=pure.FundedCreationGrant.model_validate(grant)
        from zoneinfo import ZoneInfo
        ZoneInfo(typed.time_zone)
    except (ValidationError,ValueError,KeyError):fail('google_funded_grant_input_invalid')
    start,end=pure._instant(body.valid_from),pure._instant(body.expires_at)
    if (start is None or end is None or not start<end<=start+timedelta(days=30) or end<=started
        or not 0<Decimal(body.budget_native)<=Decimal(body.max_campaign_daily_budget_native)<=Decimal(body.max_account_daily_budget_native)
        or Decimal(draft['budget_native'])!=Decimal(body.budget_native)
        or body.request_key.startswith(('google_create_','google_funded_create_'))):
        fail('google_funded_grant_input_invalid')
    review={'schema_version':1,'mode':body.mode,'_owner_auth_binding':owner_auth,**scope,'request_key':body.request_key,'revision':grant['revision'],
        'owner_generation':body.owner_generation,'reviewed_by':actor['actor_id'],'spend_semantics':SEMANTICS,
        'draft_id':body.draft_id,'draft_revision':body.draft_revision,'catalog_max_age_seconds':body.catalog_max_age_seconds,'draft':draft,
        'valid_from':body.valid_from,'expires_at':body.expires_at,'currency_code':grant['currency_code'],
        'time_zone':grant['time_zone'],'budget_native':body.budget_native,
        'max_campaign_daily_budget_native':body.max_campaign_daily_budget_native,
        'max_account_daily_budget_native':body.max_account_daily_budget_native}
    if acknowledgment[0] is True:
        review['average_daily_acknowledgment']={'acknowledged':True,'version':AVERAGE_ACK_VERSION,'text_digest':AVERAGE_ACK_DIGEST}
    from .google_ads_funded_creation_authority import _products
    product=await _products(db,{'user_id':scope['user_id'],'review':review},started)
    review['product_review_binding']={key:product[key] for key in ('product_id','canonical_url','name','sku')}
    digest=review_digest(review);grant['owner_review_digest']=digest
    return {**review,'review_digest':digest,'grant':grant}


async def _mutate_in_transaction(db,*,session,snapshot,command,current):
    from .google_ads_paused_creation_budget import SessionDatabase
    scope={'user_id':snapshot.user_id,'account_id':snapshot.account_id}
    view=SessionDatabase(db,session)
    old=await view[GRANTS].find_one({**scope,'grant_key':command.grant_key},{'_id':0})
    if (old or {}).get('revision',0)!=command.expected_revision:fail('google_funded_grant_changed')
    if command.action=='save':
        review=command.values
        if (review.get('user_id')!=scope['user_id'] or review.get('account_id')!=scope['account_id']
            or review.get('reviewed_by')!=scope['user_id'] or review.get('spend_semantics')!=SEMANTICS
            or review.get('review_digest')!=review_digest(review)
            or command.grant_key!=grant_key(scope['user_id'],scope['account_id'],review.get('request_key'))):
            fail('google_funded_review_changed')
        draft=await view[management.DRAFTS].find_one({'user_id':scope['user_id'],'draft_id':review['draft_id'],
            'revision':review['draft_revision'],'account_id':scope['account_id']},{'_id':0})
        actor,_,_=await read_owner_auth(view,user_id=scope['user_id'],expected=review.get('_owner_auth_binding'))
        if (not draft or pure.funded_creation_draft_digest(draft['draft'])!=review['grant']['draft_digest']
            or actor['actor_id']!=review['reviewed_by'] or (old or {}).get('consumed') is True
            or await view[RUNS].find_one({**scope,'grant_key':command.grant_key},{'_id':1})):
            fail('google_funded_slot_consumed_or_changed')
        from .google_ads_funded_creation_authority import _products
        product=await _products(view,{'user_id':scope['user_id'],'review':review},current)
        if {key:product[key] for key in ('product_id','canonical_url','name','sku')}!=review.get('product_review_binding'):
            fail('google_funded_product_review_changed')
        record={**scope,'grant_key':command.grant_key,'request_key':review['request_key'],
            'revision':command.expected_revision+1,'grant':review['grant'],'review':{key:value for key,value in review.items() if key not in {'grant','review_digest'}},
            'review_digest':review['review_digest'],'state':'configured','spend_semantics':SEMANTICS,
            'consumed':False,'claim':None,'updated_at':current.isoformat()}
        validate_record(record)
        if old and record['grant']['owner_generation']<old['grant']['owner_generation']:fail('google_funded_generation_decreased')
        if old:
            result=await view[GRANTS].update_one({**scope,'grant_key':command.grant_key,'revision':command.expected_revision}, {'$set':record})
            if result.matched_count!=1:fail('google_funded_grant_changed')
        else:await view[GRANTS].insert_one(record)
    elif command.action=='revoke':
        if not old:fail('google_funded_grant_missing')
        # Revocation preserves the original grant/review binding and all private
        # claims. Operational revision differs from immutable approval revision.
        result=await view[GRANTS].update_one({**scope,'grant_key':command.grant_key,'revision':command.expected_revision},
            {'$set':{'state':'revoked','revision':command.expected_revision+1,'updated_at':current.isoformat()}})
        if result.matched_count!=1:fail('google_funded_grant_changed')
    else:fail('google_funded_activation_unavailable')
    return {'kind':'funded_grant_mutation','state':'configured' if command.action=='save' else 'revoked',
        'grant_key':command.grant_key,'revision':command.expected_revision+1,'funded_dispatch_authorized':False}


async def save_google_funded_grant(db,*,user,account_id,body,expected_review_digest,current=None,client_factory=GoogleAdsClient):
    review=await review_google_funded_grant(db,user=user,account_id=account_id,body=body,current=current,client_factory=client_factory)
    if expected_review_digest!=review['review_digest']:fail('google_funded_review_changed')
    await ensure_indexes(db)
    from . import google_ads_automation_budget_guard as guard
    capability=await guard.probe_google_budget_guard_transactions(db)
    return await guard.run_google_budget_guard_transaction(db,user_id=review['user_id'],account_id=review['account_id'],
        operation_id='funded_grant_'+uuid.uuid4().hex,capability=capability,
        planner=lambda snapshot:guard.FundedGrantMutation(grant_key(review['user_id'],review['account_id'],review['request_key']),
            review['revision']-1,'save',review))


async def revoke_google_funded_grant(db,*,user,account_id,request_key,expected_revision):
    actor,_=await capture_owner_auth(db,user);account_id=identifier(account_id)
    from . import google_ads_automation_budget_guard as guard
    capability=await guard.probe_google_budget_guard_transactions(db)
    return await guard.run_google_budget_guard_transaction(db,user_id=actor['user_id'],account_id=account_id,
        operation_id='funded_revoke_'+uuid.uuid4().hex,capability=capability,
        planner=lambda snapshot:guard.FundedGrantMutation(grant_key(actor['user_id'],account_id,request_key),expected_revision,'revoke',{}))
