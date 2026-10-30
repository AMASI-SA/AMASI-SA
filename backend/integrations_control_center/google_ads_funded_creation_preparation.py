"""Internal owner admission -> durable prepared slot; no route or provider send.

An original positive commit ACK alone may mint the one-use initiation ACK.
Neither a readback nor a new process reconstructs an admission/continuation.
"""
from copy import deepcopy
from dataclasses import dataclass
from datetime import timedelta
import asyncio
import uuid
import re
from . import google_ads_management as management
from . import google_ads_funded_creation_grants as grants
from . import google_ads_funded_creation_authority as authority
from . import google_ads_funded_creation_contract as pure
from . import google_ads_automation_budget_guard as guard
from .google_ads_paused_creation_budget import SessionDatabase,account_exposure
from .google_ads_client import GoogleAdsClient

_ADMISSION=object()
_PREPARATION=object()
MAX_ADMITTED_SLOTS=128  # Process resource bound, never a financial allowance.
_admitted_slots=set()  # Never evicted/retried during this process lifetime.
_metadata_unconfirmed=False
_ACK_TEXT_VERSION=grants.AVERAGE_ACK_VERSION
_metadata_lock=asyncio.Lock()


@dataclass
class _OwnerAdmission:
    db: object
    user: dict
    account_id: str
    request_key: str
    review_digest: str
    revision: int
    owner_generation: int
    operation_id: str
    proof: object
    used: bool=False


@dataclass(frozen=True)
class _Preparation:
    db: object
    payload: dict
    proof: object


def _fail(code):authority._fail(code)


def begin_google_funded_owner_admission(db,*,authenticated_user,account_id,request_key,
        expected_review_digest,expected_revision,expected_owner_generation,spend_acknowledgment):
    """Trusted fresh explicit owner action only; NEVER called by GET/recovery.

    authenticated_user is the server auth dependency result, not request JSON.
    This synchronous admission cannot prove a never-persisted prior attempt
    after process loss. Callers must not reissue it on retries/restarts; a new
    explicit owner action must use a newly reviewed request identity.
    """
    if (not isinstance(authenticated_user,dict) or authenticated_user.get('role')!='owner'
        or not isinstance(authenticated_user.get('id'),str)
        or not isinstance(request_key,str) or not re.fullmatch(r'[A-Za-z0-9_-]{8,128}',request_key)
        or not isinstance(expected_review_digest,str) or not re.fullmatch(r'[a-f0-9]{64}',expected_review_digest)
        or type(expected_revision) is not int or expected_revision<1
        or type(expected_owner_generation) is not int or expected_owner_generation<1
        or spend_acknowledgment!={'semantics':grants.SEMANTICS,'version':_ACK_TEXT_VERSION,'text_digest':grants.AVERAGE_ACK_DIGEST,'acknowledged':True}):
        _fail('google_funded_owner_admission_unproved')
    scope=guard._scope(authenticated_user['id'],account_id)
    slot=(id(db),scope['user_id'],scope['account_id'],request_key)
    if slot in _admitted_slots or len(_admitted_slots)>=MAX_ADMITTED_SLOTS:
        _fail('google_funded_owner_admission_consumed')
    _admitted_slots.add(slot) # Before any possible await; no automatic retry.
    # Retain only the permitted nonsecret dependency projection.
    user={key:deepcopy(authenticated_user[key]) for key in grants.AUTH_FIELDS if key in authenticated_user}
    return _OwnerAdmission(db,user,scope['account_id'],request_key,expected_review_digest,
        expected_revision,expected_owner_generation,'funded_prepare_'+uuid.uuid4().hex,_ADMISSION)


def _namespaces():
    # Internal names never leave this module in diagnostics.
    return {
        guard.AUTOMATION_POLICIES:{'find','insert','update'},guard.CREATION_POLICIES:{'find','insert','update'},
        guard.BUDGET_GUARDS:{'find','insert','update'},guard.TECHNICAL_PROBES:{'find','insert'},
        grants.GRANTS:{'find','insert','update'},grants.RUNS:{'find','insert','update'},
        management.PROPOSALS:{'find','insert','update'},management.FENCES:{'find','insert','update'},
        management.DRAFTS:{'find'},'users':{'find','update'},
        authority.PRODUCT_COLLECTION:{'find'},management.GOOGLE_CREDENTIALS_COLLECTION:{'find'},
        'mezan_integrations_v2':{'find'},'mezan_integration_accounts_v2':{'find'}}


async def _cursor_command(db,command,*,collection=None,maximum):
    """Only one bounded batch; retain uncertainty latch if cursor ACK is lost."""
    global _metadata_unconfirmed
    try:
        result=await db.command({**command,'cursor':{'batchSize':maximum+1},'maxTimeMS':2000})
        cursor=result.get('cursor') if isinstance(result,dict) else None
        if not isinstance(cursor,dict):raise ValueError('unproved cursor')
        rows=cursor.get('firstBatch');cursor_id=cursor.get('id')
        if isinstance(cursor_id,bool) or not isinstance(cursor_id,int) or not isinstance(rows,list):raise ValueError('unproved cursor')
        if cursor_id:
            # Kill only this returned cursor, on its exact server collection.
            own=collection or '$cmd.listCollections'
            killed=await db.command({'killCursors':own,'cursors':[cursor_id],'maxTimeMS':2000})
            if not isinstance(killed,dict) or cursor_id not in killed.get('cursorsKilled',[]):
                raise ValueError('cursor cleanup unconfirmed')
            _fail('google_funded_storage_inventory_incomplete')
        if result.get('ok')!=1 or len(rows)>maximum:_fail('google_funded_storage_inventory_incomplete')
        return rows
    except asyncio.CancelledError:
        _metadata_unconfirmed=True
        raise
    except Exception as error:
        # Unknown response may hide a live server cursor. No repeated probes
        # accumulate resources; recovery requires process/operator lifecycle.
        _metadata_unconfirmed=True
        if isinstance(getattr(error,'detail',None),dict) and error.detail.get('code')=='google_funded_storage_inventory_incomplete':raise
        _fail('google_funded_storage_metadata_unproved')


def _indexes():
    return {
        guard.AUTOMATION_POLICIES:[('google_automation_tenant_account',[('user_id',1),('account_id',1)])],
        guard.CREATION_POLICIES:[('google_creation_tenant_account',[('user_id',1),('account_id',1)])],
        grants.GRANTS:[('google_funded_grant_scope',[('user_id',1),('account_id',1),('request_key',1)])],
        grants.RUNS:[('google_funded_run_slot',[('user_id',1),('account_id',1),('grant_key',1)]),
                    ('google_funded_run_identity',[('user_id',1),('account_id',1),('creation_identity',1)])],
        management.PROPOSALS:[('google_proposal_identity',[('user_id',1),('proposal_id',1)]),
                            ('google_proposal_idempotency',[('user_id',1),('idempotency_key',1)])],
        management.FENCES:[('google_account_fence',[('user_id',1),('account_id',1)])],
        management.DRAFTS:[('google_draft_identity',[('user_id',1),('draft_id',1)])]}


def _metadata_available():
    if _metadata_unconfirmed:_fail('google_funded_storage_metadata_pending')


async def _storage_contract(db):
    async with _metadata_lock:
        _metadata_available()
        return await _read_storage_contract(db)


async def _read_storage_contract(db):
    required=_namespaces()
    try:
        status=await db.command({'connectionStatus':1,'showPrivileges':True})
        info=status.get('authInfo',{})
        privileges=info.get('authenticatedUserPrivileges')
        if (not info.get('authenticatedUsers') or not isinstance(privileges,list) or not 0<len(privileges)<=2000
            or any(not isinstance(p,dict) or not isinstance(p.get('actions'),list) or len(p['actions'])>512 for p in privileges)):
            _fail('google_funded_storage_privileges_unproved')
        for name,actions in required.items():
            allowed=set()
            for privilege in info['authenticatedUserPrivileges']:
                resource=privilege.get('resource',{})
                if resource.get('anyResource') is True or (resource.get('db') in ('',db.name)
                    and resource.get('collection') in ('',name)):
                    allowed.update(privilege.get('actions',[]))
            if 'anyAction' not in allowed and not actions<=allowed:_fail('google_funded_storage_privileges_unproved')
        rows=await _cursor_command(db,{'listCollections':1,'filter':{'name':{'$in':list(required)}},'nameOnly':False},maximum=len(required))
        if {row.get('name') for row in rows}!=set(required):_fail('google_funded_storage_namespace_unproved')
        for row in rows:
            if row.get('type')!='collection' or row.get('options'):
                _fail('google_funded_storage_schema_unproved')
        fingerprint=[]
        for row in rows:
            generation=row.get('info',{}).get('uuid')
            if isinstance(generation,uuid.UUID):generation=generation.bytes
            if not isinstance(generation,bytes) or len(generation)!=16:_fail('google_funded_storage_generation_unproved')
            fingerprint.append({'collection':row['name'],'generation':generation.hex()})
        for name,contracts in _indexes().items():
            indexes=await _cursor_command(db,{'listIndexes':name},collection=name,maximum=64)
            for index_name,keys in contracts:
                matches=[index for index in indexes if index.get('name')==index_name]
                if (len(matches)!=1 or list(matches[0].get('key',{}).items())!=keys
                    or matches[0].get('unique') is not True
                    or any(key in matches[0] for key in ('partialFilterExpression','collation','expireAfterSeconds'))
                    or matches[0].get('sparse') or matches[0].get('hidden')):
                    _fail('google_funded_storage_index_unproved')
            fingerprint.append({'collection':name,'indexes':indexes})
        return management.digest(guard._plain(fingerprint))
    except Exception as error:
        if isinstance(getattr(error,'detail',None),dict):raise
        _fail('google_funded_storage_contract_unproved')


async def _read_preparation(db,admission,phase,client_factory):
    if not guard.budget_guard_runtime_available():_fail('google_funded_runtime_unavailable')
    started=management.now();scope=guard._scope(admission.user['id'],admission.account_id)
    actor,owner=await grants.capture_owner_auth(db,admission.user)
    key=grants.grant_key(**scope,request_key=admission.request_key)
    record=await db[grants.GRANTS].find_one({**scope,'grant_key':key},{'_id':0})
    typed=grants.validate_record(record)
    if (record.get('state')!='configured' or record.get('consumed') is not False or record.get('claim') is not None
        or (record['review_digest'],record['revision'],typed.owner_generation)!=(admission.review_digest,admission.revision,admission.owner_generation)
        or record['review'].get('_owner_auth_binding')!=owner
        or record['review'].get('average_daily_acknowledgment')!={'acknowledged':True,'version':_ACK_TEXT_VERSION,'text_digest':grants.AVERAGE_ACK_DIGEST}):_fail('google_funded_preparation_review_changed')
    pure.prepare_google_funded_creation_intent(grant=record['grant'],draft=record['review']['draft'],current=started)
    if await db[grants.RUNS].find_one({**scope,'grant_key':key},{'_id':1}):_fail('google_funded_slot_consumed_or_pending')
    contract=await _storage_contract(db)
    standing=await guard._read_policy(db,scope,'standing',None)
    creation=await guard._read_policy(db,scope,'creation',None)
    policies=authority.policy_binding(guard.BudgetGuardSnapshot(**scope,guard={},standing_policy=standing,creation_policy=creation))
    # Real read collaborators mint proof; no client-supplied booleans accepted.
    native=await authority._read_native_bundle(db,scope=scope,record=record,started=started,client_factory=client_factory)
    if await _storage_contract(db)!=contract:_fail('google_funded_storage_contract_changed')
    _metadata_available()
    if phase.get('stop_requested') or guard._operation_clock()>=phase['deadline']:_fail('google_funded_preparation_expired')
    return _Preparation(db,guard._plain({'scope':scope,'grant_key':key,'record_digest':management.digest(record),
        'owner_auth':owner,'policy_binding':policies,'native':native.payload,'storage_contract_digest':contract,
        'proposal_id':'funded_proposal_'+uuid.uuid4().hex,'execution_claim_id':'funded_claim_'+uuid.uuid4().hex,
        'operation_id':admission.operation_id,'spend_acknowledgment':{'semantics':grants.SEMANTICS,'version':_ACK_TEXT_VERSION,'text_digest':grants.AVERAGE_ACK_DIGEST,'acknowledged':True}}),_PREPARATION)


async def _write_preparation(db,*,session,snapshot,command,operation_id,current):
    proof=command.prepared
    if not isinstance(proof,_Preparation) or proof.db is not db or proof.proof is not _PREPARATION:
        _fail('google_funded_preparation_proof_unproved')
    value=guard._plain(proof.payload);scope=value['scope'];native=value['native'];view=SessionDatabase(db,session)
    _metadata_available()
    if not guard.budget_guard_runtime_available() or scope!={'user_id':snapshot.user_id,'account_id':snapshot.account_id}:
        _fail('google_funded_runtime_unavailable')
    if operation_id!=value['operation_id']:_fail('google_funded_preparation_proof_unproved')
    if value['policy_binding']!=authority.policy_binding(snapshot):_fail('google_funded_policy_changed')
    record=await view[grants.GRANTS].find_one({**scope,'grant_key':value['grant_key']},{'_id':0})
    grants.validate_record(record)
    if (management.digest(record)!=value['record_digest'] or record.get('state')!='configured'
        or record.get('consumed') is not False or record.get('claim') is not None
        or await view[grants.RUNS].find_one({**scope,'grant_key':value['grant_key']})):
        _fail('google_funded_preparation_review_changed')
    await grants.read_owner_auth(view,user_id=scope['user_id'],expected=value['owner_auth'])
    await authority._stored_google_scope(view,scope['user_id'],scope['account_id'],native['auth_epoch'])
    pure.revalidate_google_funded_creation_intent(native['intent'],grant=record['grant'],draft=record['review']['draft'],current=current)
    if not 0<=(current-pure._instant(native['observed_at'])).total_seconds()<90:_fail('google_funded_native_stale')
    if await authority._products(view,record,current)!=native['product']:_fail('google_funded_product_review_changed')
    draft=await view[management.DRAFTS].find_one({**scope,'draft_id':record['review']['draft_id'],
        'revision':record['review']['draft_revision']},{'_id':0})
    if not draft or pure.funded_creation_draft_digest(draft['draft'])!=record['grant']['draft_digest']:_fail('google_funded_draft_changed')
    old=guard.thaw_snapshot(snapshot.guard)
    if old['active_claim'] is not None or len(old['allocations'])>=guard.MAX_ALLOCATIONS:
        _fail('google_budget_guard_allocation_pending')
    if (snapshot.standing_policy or {}).get('_dispatch_claim') or (snapshot.creation_policy or {}).get('_creation_claim'):
        _fail('google_budget_guard_allocation_pending')
    exposure=account_exposure(snapshot,native['inventory'],record['grant'],current,funded_grant=record['grant'])
    if exposure['total']+guard._money(record['grant']['budget_native'])>exposure['limit']:_fail('google_budget_guard_account_limit')
    prior_fence=await view[management.FENCES].find_one(scope)
    if prior_fence and prior_fence.get('status')!='released':_fail('google_funded_account_fence_pending')
    intent=native['intent'];until=min(pure._instant(record['grant']['expires_at']),current+management.PROPOSAL_TTL)
    proposal={**scope,'proposal_id':value['proposal_id'],'execution_claim_id':value['execution_claim_id'],
        'actor_id':'mezan_google_automation_v1','provider':'google_ads','action':'create_campaign','campaign_id':None,
        'before_digest':None,'before_snapshot':None,'auth_epoch':native['auth_epoch'],
        'currency':record['grant']['currency_code'],'time_zone':record['grant']['time_zone'],
        'draft_id':record['review']['draft_id'],'draft_revision':record['review']['draft_revision'],
        'planned':record['review']['draft'],'status':'approved','submission_started':False,'provider_write_reached':False,
        'operations':intent['request']['mutateOperations'],'expires_at':until.isoformat(),'created_at':current.isoformat(),
        'idempotency_key':intent['creation_identity'],'automation_source':{'kind':'funded_new_creation',
            'grant_key':record['grant_key'],'creation_identity':intent['creation_identity']}}
    proposal['confirmation_digest']=management.digest(management.immutable(proposal))
    fence={**scope,'proposal_id':proposal['proposal_id'],'execution_claim_id':proposal['execution_claim_id'],
        'status':'claimed','claimed_at':current.isoformat()}
    await view[management.PROPOSALS].insert_one(deepcopy(proposal))
    if prior_fence:
        changed=await view[management.FENCES].update_one(prior_fence,{'$set':fence})
        if changed.matched_count!=1:_fail('google_funded_account_fence_changed')
    else:await view[management.FENCES].insert_one(deepcopy(fence))
    fence=await view[management.FENCES].find_one({**scope,'proposal_id':proposal['proposal_id'],
        'execution_claim_id':proposal['execution_claim_id'],'status':'claimed'},{'_id':0})
    if not fence:_fail('google_funded_account_fence_changed')
    claim={'proposal_id':proposal['proposal_id'],'execution_claim_id':proposal['execution_claim_id'],
        'preparation_operation_id':operation_id}
    binding={**scope,'grant_key':record['grant_key'],'grant_revision':record['revision'],'review_digest':record['review_digest'],
        'proposal':management.immutable(proposal),'fence':fence,'execution_claim_id':proposal['execution_claim_id'],
        'preparation_operation_id':operation_id,'guard_epoch':old['epoch']+1}
    digest=management.digest(binding)
    changed=await view[grants.GRANTS].update_one({**scope,'grant_key':record['grant_key'],'revision':record['revision'],
        'state':'configured','consumed':False,'claim':None},{'$set':{'state':'active','consumed':True,'claim':claim,
            'activation_version':1,'activation_revision':record['revision'],'activated_by':scope['user_id'],
            'activated_at':current.isoformat(),'activation_review_digest':record['review_digest'],
            'activation_policy_binding':authority.policy_binding(snapshot)}})
    if changed.matched_count!=1:_fail('google_funded_preparation_review_changed')
    await view[grants.RUNS].insert_one({**scope,'grant_key':record['grant_key'],'request_key':record['request_key'],
        **claim,'claim':claim,'consumed':True,'phase':'prepared','creation_identity':intent['creation_identity'],
        'preparation_binding':binding,'preparation_binding_digest':digest,'intent':intent,'graph_digest':intent['graph_digest'],
        'bundle':native,'owner_generation':record['grant']['owner_generation'],'review_digest':record['review_digest'],
        'spend_acknowledgment':value['spend_acknowledgment'],'storage_contract_digest':value['storage_contract_digest'],
        'prepared_at':current.isoformat(),'authority_valid_from':record['grant']['valid_from'],'authority_valid_until':until.isoformat()})
    await authority._owner_write_fence(view,user_id=scope['user_id'],operation_id=operation_id,expected_auth=value['owner_auth'])
    return {'kind':'funded_preparation','grant_key':record['grant_key'],'proposal_id':proposal['proposal_id'],
        'execution_claim_id':proposal['execution_claim_id'],'creation_identity':intent['creation_identity'],
        'preparation_binding_digest':digest,'authority_valid_from':record['grant']['valid_from'],
        'authority_valid_until':until.isoformat(),'observed_at':native['observed_at'],'funded_dispatch_authorized':False}


async def prepare_google_funded_creation(db,*,admission,client_factory=GoogleAdsClient):
    if (not isinstance(admission,_OwnerAdmission) or admission.db is not db or admission.proof is not _ADMISSION or admission.used):
        _fail('google_funded_owner_admission_unproved')
    admission.used=True # Burn synchronously before FIRST await; never reset.
    scope=guard._scope(admission.user['id'],admission.account_id)
    if not guard.budget_guard_runtime_available():_fail('google_funded_runtime_unavailable')
    phase={}
    try:
        prepared=await guard._retained_operation(db,scope,admission.operation_id,'read',
            lambda:_read_preparation(db,admission,phase,client_factory),20,phase)
        if guard._operation_clock()>=phase['deadline']:_fail('google_funded_preparation_expired')
        capability=await guard.probe_google_budget_guard_transactions(db)
        remaining=phase['deadline']-guard._operation_clock()
        if remaining<=0:_fail('google_funded_preparation_expired')
        result=await guard.run_google_budget_guard_transaction(db,**scope,operation_id=admission.operation_id,
            capability=capability,planner=lambda snapshot:guard.FundedCreationPreparation(prepared),timeout_seconds=min(15,remaining),original_deadline=phase['deadline'])
        # No commit cache/readback can reach the issuer: exact original call ACK.
        if (result.get('committed') is not True or result.get('kind')!='funded_preparation'
            or result.get('operation_id')!=admission.operation_id):_fail('google_funded_preparation_ack_unproved')
        _metadata_available()
        current=management.now()
        if (guard._operation_clock()>=phase['deadline'] or not pure._instant(result['authority_valid_from'])<=current<pure._instant(result['authority_valid_until'])
            or not 0<=(current-pure._instant(result['observed_at'])).total_seconds()<90):
            _fail('google_funded_preparation_expired_after_commit')
        result['_initiation_ack']=authority._InitiationAck(db,scope['user_id'],scope['account_id'],result['grant_key'],
            result['proposal_id'],result['execution_claim_id'],result['preparation_binding_digest'],admission.operation_id,authority._INITIATION)
        return result
    except Exception as error:
        if isinstance(getattr(error,'detail',None),dict):raise
        _fail('google_funded_preparation_unconfirmed')
