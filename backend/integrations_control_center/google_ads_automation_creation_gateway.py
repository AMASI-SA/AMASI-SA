"""Explicit standing authority for native PAUSED SEARCH/RSA creation.

The common native gateway owns validation, account fencing, submission floors
and independent readback. Only creation authority may reserve quota and record
ownership. An unknown result is retained for read-only recovery, never resent.
"""
from __future__ import annotations

from datetime import timedelta
import os
from pydantic import ValidationError
from pymongo.errors import DuplicateKeyError

from . import google_ads_management as management
from . import google_ads_automation_creation_contract as contract
from .google_ads_ai import PRODUCT_COLLECTION
from .google_ads_creation_inventory import PRODUCT_PROJECTION
from .google_ads_automation_creation_policy import CREATION_POLICIES, creation_policy_snapshot
from .google_ads_automation_gateway import _actor
from .google_ads_automation_policy import _owner, _account_scope
from .google_ads_campaign_contract import GoogleCampaignActionInput
from .google_ads_client import GoogleAdsClient, identifier, problem

CREATION_INTENTS = 'mezan_google_ads_automation_creation_intents_v1'
_SOURCE_KEYS = {'kind','creation_identity','intent_digest','policy_revision','policy_digest','draft_digest'}


def creation_dispatch_enabled():
    from .google_ads_automation_authority import execution_enabled
    from .google_ads_automation_worker import worker_enabled
    return (os.getenv('GOOGLE_ADS_AUTOMATION_CREATION_ENABLED','false').strip().lower() in {'true','1','yes','on'}
        and worker_enabled() and execution_enabled())


def _enabled():
    if not creation_dispatch_enabled():
        raise problem('google_creation_execution_disabled','الإنشاء الآلي لجوجل غير مفعّل.',503)


def _source(intent):
    binding=intent['binding']
    return {'kind':'standing_creation','creation_identity':intent['creation_identity'],'intent_digest':intent['intent_digest'],
        'policy_revision':binding['policy_revision'],'policy_digest':binding['policy_digest'],'draft_digest':binding['draft_digest']}


async def _policy(db,user_id,account_id,revision=None):
    _enabled()
    actor=await _owner(db,{'id':user_id})
    scope=await _account_scope(db,actor,account_id)
    row=await db[CREATION_POLICIES].find_one(scope,{'_id':0})
    if (not row or row.get('enabled') is not True or type(row.get('revision')) is not int
        or revision is not None and (type(revision) is not int or row['revision']!=revision)):
        raise problem('google_creation_policy_changed','تفويض إنشاء جوجل تغير أو غير متاح.',409)
    return row


async def _product(db,user_id,product_id):
    rows=await db[PRODUCT_COLLECTION].find({'user_id':user_id,
        '$or':[{'salla_product_id':product_id},{'mezan_product_id':product_id}]},PRODUCT_PROJECTION).to_list(length=2)
    if len(rows)>1:
        raise problem('google_creation_product_identity_ambiguous','هوية المنتج مرتبطة بأكثر من سجل.',409)
    if not rows:
        raise problem('google_creation_product_not_owned','المنتج غير متاح في كتالوج المتجر.',404)
    return rows[0]


async def _native_context(client,*,row,policy,metadata,read_started_at):
    from .google_ads_automation_creation_plan import read_google_creation_native_context
    return await read_google_creation_native_context(client,user_id=row['user_id'],account_id=row['account_id'],
        policy=creation_policy_snapshot(policy),metadata=metadata,read_started_at=contract._instant(read_started_at))


async def _inputs(db,*,user_id,account_id,policy,choice,native_context):
    from .google_ads_automation_creation_authority import read_google_creation_state
    state=await read_google_creation_state(db,user_id=user_id,account_id=account_id,
        native_context=native_context,current=management.now())
    return dict(policy=creation_policy_snapshot(policy),template=policy['template'],
        product=await _product(db,user_id,choice['product_id']),native_context=native_context,
        creation_state=state,choice=choice,current=management.now())


def _prepare(**values):
    try:
        return contract.prepare_google_creation_intent(**values)
    except contract.CreationContractError as exc:
        raise problem(exc.code,'لم تثبت أهلية إنشاء الحملة المتوقفة ضمن التفويض.',409) from None


async def _intent(db,row):
    source=row.get('automation_source')
    if (not isinstance(source,dict) or set(source)!=_SOURCE_KEYS or source.get('kind')!='standing_creation'
        or row.get('actor_id')!='mezan_google_automation_v1' or row.get('provider')!='google_ads'
        or row.get('action')!='create_campaign' or row.get('campaign_id') is not None or row.get('before_digest') is not None
        or management.digest(management.immutable(row))!=row.get('confirmation_digest')):
        raise problem('google_creation_proposal_binding_invalid','لم يثبت ربط عملية الإنشاء بتفويضها.',409)
    saved=await db[CREATION_INTENTS].find_one({'user_id':row['user_id'],'account_id':row['account_id'],
        'creation_identity':source['creation_identity']},{'_id':0})
    intent=(saved or {}).get('intent')
    try:
        valid=(isinstance(intent,dict) and source==_source(intent)
            and intent['creation_identity']=='google_create_'+contract._digest(intent['binding']['identity'])
            and intent['intent_digest']==contract._digest(intent['binding'])
            and intent['binding']['draft_digest']==contract._digest(intent['draft'])
            and intent['binding']['identity']['user_id']==row['user_id']
            and intent['binding']['identity']['account_id']==row['account_id']
            and saved.get('proposal_id') in (None,row['proposal_id'])
            and intent.get('initial_status')=='PAUSED' and intent.get('auto_enable_new') is False
            and row.get('idempotency_key')==intent['creation_identity'])
        from .google_ads_campaign_builder import build_campaign_operations
        operations=build_campaign_operations(intent['draft'],row['account_id']) if valid else []
        campaign=next(op['campaignOperation']['create']['resourceName'] for op in operations if 'campaignOperation' in op)
        label=f"customers/{row['account_id']}/labels/-9000000000000"
        operations += [{'labelOperation':{'create':{'resourceName':label,'name':'MEZAN-'+row['proposal_id']}}},
            {'campaignLabelOperation':{'create':{'campaign':campaign,'label':label}}}]
        valid=(valid and operations==row.get('operations') and row.get('correlation_label')=='MEZAN-'+row['proposal_id']
            and isinstance(row.get('planned'),dict) and row['planned'].get('initial_status')=='PAUSED'
            and all(row['planned'].get(k)==v for k,v in intent['draft'].items()))
    except (KeyError,TypeError,ValueError,StopIteration):
        valid=False
    if not valid:
        raise problem('google_creation_proposal_binding_invalid','لم يثبت سجل اختيار الإنشاء الأصلي.',409)
    return saved


async def preview_google_automation_creation(db,*,user_id,account_id,policy_revision,choice,client_factory=GoogleAdsClient):
    _enabled()
    account_id=identifier(account_id)
    try:
        choice=contract.GoogleCreationChoice.model_validate(choice).model_dump()
    except ValidationError:
        raise problem('google_creation_choice_invalid','اختيار المنتج والقالب غير صالح.',422) from None
    policy=await _policy(db,user_id,account_id,policy_revision)
    read_started_at=management.now().isoformat()
    client,_=await management.account_client(db,user_id,account_id,client_factory=client_factory)
    try:
        metadata=await client.account_metadata()
        native=await _native_context(client,row={'user_id':user_id,'account_id':account_id},policy=policy,
            metadata=metadata,read_started_at=read_started_at)
    finally:
        await client.close()
    policy=await _policy(db,user_id,account_id,policy_revision)
    intent=_prepare(**await _inputs(db,user_id=user_id,account_id=account_id,policy=policy,
        choice=choice,native_context=native))
    await db[CREATION_INTENTS].create_index([('user_id',1),('account_id',1),('creation_identity',1)],
        unique=True,name='google_creation_intent_identity')
    scope={'user_id':user_id,'account_id':account_id,'creation_identity':intent['creation_identity']}
    try:
        await db[CREATION_INTENTS].update_one(scope,{'$setOnInsert':{**scope,'intent':intent,'choice':choice,
            'created_at':management.now().isoformat()}},upsert=True)
    except DuplicateKeyError:
        pass
    original=await db[CREATION_INTENTS].find_one(scope,{'_id':0})
    if not original or original.get('intent')!=intent or original.get('choice')!=choice:
        raise problem('google_creation_identity_conflict','هذا الاختيار مرتبط بنية سابقة؛ يلزم جيل موافقة جديد.',409)
    await management.ensure_google_management_indexes(db)
    draft_id=intent['creation_identity']
    draft_scope={'user_id':user_id,'draft_id':draft_id}
    try:
        await db[management.DRAFTS].update_one(draft_scope,{'$setOnInsert':{**draft_scope,'account_id':account_id,
            'revision':1,'draft':intent['draft'],'idempotency_key':draft_id,'actor_id':'mezan_google_automation_v1'}},upsert=True)
    except DuplicateKeyError:
        pass
    draft=await db[management.DRAFTS].find_one(draft_scope,{'_id':0})
    if not draft or draft.get('draft')!=intent['draft'] or draft.get('revision')!=1 or draft.get('account_id')!=account_id:
        raise problem('google_creation_identity_conflict','المسودة المثبتة لا تطابق نية الإنشاء.',409)
    body=GoogleCampaignActionInput(account_id=account_id,action='create_campaign',draft_id=draft_id,draft_revision=1,
        reason='إنشاء حملة بحث متوقفة ضمن تفويض المالك',idempotency_key=draft_id)
    result=await management._preview_google_action(db,_actor(user_id),body,client_factory=client_factory,
        automation_source=_source(intent))
    linked=await db[CREATION_INTENTS].update_one({**scope,'$or':[{'proposal_id':{'$exists':False}},
        {'proposal_id':result['proposal_id']}]},{'$set':{'proposal_id':result['proposal_id']}})
    if not linked.matched_count:
        raise problem('google_creation_identity_conflict','اختيار الإنشاء مرتبط بعملية أخرى.',409)
    return result


async def _execution_preflight(db,row):
    saved=await _intent(db,row)
    await _policy(db,row['user_id'],row['account_id'],saved['intent']['binding']['policy_revision'])


async def _authorize_dispatch(db,row,client,metadata,epoch,snapshot,*,read_started_at):
    from .google_ads_automation_creation_authority import authorize_google_creation_claim
    saved=await _intent(db,row)
    policy=await _policy(db,row['user_id'],row['account_id'],saved['intent']['binding']['policy_revision'])
    native=await _native_context(client,row=row,policy=policy,metadata=metadata,read_started_at=read_started_at)
    await client.mutate(row['operations'],validate_only=True)
    reply=await authorize_google_creation_claim(db,user_id=row['user_id'],account_id=row['account_id'],
        proposal_id=row['proposal_id'],execution_claim_id=row['execution_claim_id'],
        native_evidence={'metadata':metadata,'auth_epoch':epoch,'native_context':native,'observed_at':read_started_at})
    if (not isinstance(reply,dict) or reply.get('authorized') is not True
        or reply.get('claim_id')!=row['execution_claim_id'] or reply.get('proposal_id')!=row['proposal_id']
        or reply.get('creation_identity')!=row['automation_source']['creation_identity']
        or reply.get('submission_floor_required') is not True):
        raise problem('google_creation_authorization_unconfirmed','لم يثبت إقرار حجز الإنشاء؛ تحقق دون إعادة إرسال.',503)
    return reply


async def _settle(db,row):
    from .google_ads_automation_creation_authority import settle_google_creation_claim
    if not row.get('execution_claim_id'):
        raise problem('google_creation_settlement_unconfirmed','تعذر إثبات حجز الإنشاء الأصلي.',409)
    reply=await settle_google_creation_claim(db,user_id=row['user_id'],account_id=row['account_id'],claim_id=row['execution_claim_id'])
    if (not isinstance(reply,dict) or reply.get('settled') is not True or reply.get('released') is not True
        or reply.get('creation_identity')!=row['automation_source']['creation_identity']
        or row.get('status')=='completed' and (reply.get('verified_ownership') is not True
            or reply.get('created_campaign_id')!=row.get('created_campaign_id'))):
        raise problem('google_creation_settlement_unconfirmed','لم يثبت إقرار تسوية حجز الإنشاء.',503)


async def _load(db,user_id,account_id,proposal_id):
    row=await management.get_proposal(db,user_id,proposal_id)
    if row.get('account_id')!=identifier(account_id):
        raise problem('google_creation_proposal_binding_invalid','عملية الإنشاء ليست ضمن الحساب المحدد.',403)
    await _intent(db,row)
    return row


async def execute_google_automation_creation(db,*,user_id,account_id,proposal_id,client_factory=GoogleAdsClient):
    row=await _load(db,user_id,account_id,proposal_id)
    if row.get('status') in {'completed','failed'}:
        await _settle(db,row)
        await management._release_fence(db,row)
        return management.public_proposal(row)
    _enabled()
    result=await management._execute_google_proposal(db,_actor(user_id),proposal_id,row['confirmation_digest'],
        client_factory=client_factory,automation=True)
    fresh=await _load(db,user_id,account_id,proposal_id)
    if fresh.get('status') in {'completed','failed'}:
        await _settle(db,fresh)
        await management._release_fence(db,fresh)
    return result


async def reconcile_google_automation_creation(db,*,user_id,account_id,proposal_id,client_factory=GoogleAdsClient):
    row=await _load(db,user_id,account_id,proposal_id)
    if row.get('status') in {'completed','failed'}:
        await _settle(db,row)
        await management._release_fence(db,row)
        return management.public_proposal(row)
    if not row.get('submission_started'):
        fence=await db[management.FENCES].find_one({'user_id':user_id,'account_id':row['account_id'],
            'proposal_id':proposal_id,'execution_claim_id':row.get('execution_claim_id'),'status':'claimed'},{'_id':0})
        started=contract._instant((fence or {}).get('claimed_at'))
        if not row.get('execution_claim_id') or not started or started>management.now()-timedelta(minutes=5):
            raise problem('google_creation_recovery_unproved','لم يثبت حجز إنشاء متقادم؛ لا تعِد الإرسال.',409)
        changed=await db[management.PROPOSALS].update_one({'user_id':user_id,'account_id':row['account_id'],
            'proposal_id':proposal_id,'execution_claim_id':row['execution_claim_id'],'submission_started':False,
            'status':{'$in':['executing','approved','uncertain']}},{'$set':{'status':'failed',
                'safe_error':'execution_cancelled_before_dispatch','provider_write_reached':False}})
        if not changed.matched_count:
            raise problem('google_creation_recovery_unproved','تغيرت حالة الإنشاء؛ تحقق دون إعادة إرسال.',409)
        row=await _load(db,user_id,account_id,proposal_id)
        await _settle(db,row)
        await management._release_fence(db,row)
        return management.public_proposal(row)
    result=await management._reconcile_google_proposal(db,_actor(user_id),proposal_id,client_factory=client_factory,automation=True)
    fresh=await _load(db,user_id,account_id,proposal_id)
    if fresh.get('status') in {'completed','failed'}:
        await _settle(db,fresh)
        await management._release_fence(db,fresh)
    return result
