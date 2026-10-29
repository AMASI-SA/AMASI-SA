"""Storage-only funded-new claim/floor/settlement; NEVER provider dispatch.

Configured grants cannot activate here. Runtime remains the existing false guard
capability flag. Actual metadata/native/product/validate-only readers mint an
in-process bundle; caller JSON flags cannot stand in for those reads. Read
clients close BEFORE storage work; this helper does not provide a prepared
paid-dispatch client or permission. A future gateway must own that lifecycle.
"""
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime,timedelta,timezone
from decimal import Decimal
import re
from product_inventory_proof import inventory_readiness
from . import google_ads_management as management
from . import google_ads_funded_creation_contract as pure
from . import google_ads_funded_creation_grants as grants
from . import google_ads_automation_budget_guard as guard
from .google_ads_automation_policy import _owner
from .google_ads_ai import PRODUCT_COLLECTION
from .google_ads_creation_inventory import PRODUCT_PROJECTION
from .google_ads_automation_creation_contract import _canonical_url
from .google_ads_automation_gateway import _inventory
from .google_ads_workspace import compatible_read
from .google_ads_client import GoogleAdsClient,mutation_resource_names
from .google_ads_paused_creation_budget import SessionDatabase,account_exposure

_PROOF=object()

@dataclass(frozen=True)
class _Prepared:
    db: object
    payload: dict
    proof: object


def _fail(code):grants.fail(code)


def _scope(user_id,account_id):return guard._scope(user_id,account_id)


def _date(value):
    # Only known BSON clock leaves use Mongo's naive-UTC convention.
    if isinstance(value,datetime):return (value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)).isoformat()
    return value


def _active(row,current):
    grant=grants.validate_record(row)
    if (row.get('state')!='active' or row.get('consumed') is True
        or row.get('activation_version')!=1 or type(row.get('activation_version')) is not int
        or type(row.get('activation_revision')) is not int or row.get('activation_revision')!=row['revision'] or row.get('activated_by')!=row['user_id']
        or row.get('activation_review_digest')!=row['review_digest']
        or pure._instant(row.get('activated_at')) is None or pure._instant(row['activated_at'])>current):
        _fail('google_funded_grant_inactive_or_consumed')
    pure.prepare_google_funded_creation_intent(grant=row['grant'],draft=row['review']['draft'],current=current)
    return grant


def policy_binding(snapshot):
    return management.digest({'standing':guard.thaw_snapshot(snapshot.standing_policy),'creation':guard.thaw_snapshot(snapshot.creation_policy)})


async def _products(db,record,current):
    draft=record['review']['draft'];ids=draft.get('product_ids')
    maximum=record['review'].get('catalog_max_age_seconds')
    if (not isinstance(ids,list) or len(ids)!=1 or type(maximum) is not int or not 60<=maximum<=86400):
        _fail('google_funded_product_scope_unproved')
    rows=await db[PRODUCT_COLLECTION].find({'user_id':record['user_id'],'$or':[
        {'salla_product_id':ids[0]},{'mezan_product_id':ids[0]}]},PRODUCT_PROJECTION).to_list(length=2)
    if len(rows)!=1:_fail('google_funded_product_scope_unproved')
    product=deepcopy(rows[0])
    for key in ('last_synced_at','details_synced_at'):product[key]=_date(product.get(key))
    if isinstance(product.get('inventory_evidence_v1'),dict):
        product['inventory_evidence_v1']['observed_at']=_date(product['inventory_evidence_v1'].get('observed_at'))
    stamp=pure._instant(product.get('last_synced_at'))
    state,stock_digest=inventory_readiness(product,current,maximum)
    url=_canonical_url(product)
    if (product.get('status')!='active' or product.get('archived') is not False or state!='available'
        or stamp is None or not 0<=(current-stamp).total_seconds()<=maximum
        or not isinstance(product.get('source_revision'),str) or not re.fullmatch(r'[a-f0-9]{64}',product['source_revision'])
        or any(group.get('final_urls')!=[url] for group in draft['groups'])):
        _fail('google_funded_product_stale_or_changed')
    return {'product_id':ids[0],'source_revision':product['source_revision'],'inventory_digest':stock_digest,
        'name':product.get('name'),'sku':product.get('sku'),'canonical_url':url,'last_synced_at':product['last_synced_at']}


async def _configuration(client,record,started):
    draft=record['review']['draft'];account=record['account_id'];geos=draft['geo_target_ids']
    rows,complete,_=await compatible_read(client,['geo_target_constant.id','geo_target_constant.status'],
        'geo_target_constant',"geo_target_constant.id IN ("+','.join(geos)+")",
        required=('geo_target_constant.id','geo_target_constant.status'),limit=101)
    found=[row.get('geoTargetConstant') or {} for row in rows]
    if (not complete or len(found)!=len(geos) or {str(row.get('id')) for row in found}!=set(geos)
        or any(row.get('status')!='ENABLED' for row in found)):_fail('google_funded_native_settings_unproved')
    rows,complete,_=await compatible_read(client,['customer_conversion_goal.category','customer_conversion_goal.origin','customer_conversion_goal.biddable'],
        'customer_conversion_goal',"customer_conversion_goal.category = 'PURCHASE' AND customer_conversion_goal.origin = 'WEBSITE'",
        required=('customer_conversion_goal.category','customer_conversion_goal.origin','customer_conversion_goal.biddable'),limit=2)
    goal=(rows[0].get('customerConversionGoal') or {}) if len(rows)==1 else {}
    if not complete or goal!={'category':'PURCHASE','origin':'WEBSITE','biddable':True}:_fail('google_funded_native_settings_unproved')
    fields=('conversion_action.resource_name','conversion_action.status','conversion_action.category','conversion_action.type','conversion_action.primary_for_goal')
    rows,complete,_=await compatible_read(client,list(fields),'conversion_action',
        "conversion_action.status = 'ENABLED' AND conversion_action.category = 'PURCHASE' AND conversion_action.primary_for_goal = TRUE AND conversion_action.type = 'WEBPAGE'",required=fields,limit=101)
    names=[]
    if not complete or not 0<len(rows)<=100:_fail('google_funded_native_settings_unproved')
    for raw in rows:
        action=raw.get('conversionAction') or {};name=action.get('resourceName')
        if (not isinstance(name,str) or not re.fullmatch(rf'customers/{account}/conversionActions/[0-9]+',name)
            or action.get('status')!='ENABLED' or action.get('category')!='PURCHASE' or action.get('type')!='WEBPAGE'
            or action.get('primaryForGoal') is not True or name in names):_fail('google_funded_native_settings_unproved')
        names.append(name)
    # Native semantic validate-only covers the exact new ENABLED graph, including
    # campaign goal overrides. It does NOT establish actual return/profitability.
    return {'geo_target_ids':geos,'purchase_goal':goal,'purchase_actions':sorted(names)}


def _semantic(payload):
    value=deepcopy(payload)
    value.pop('observed_at',None)
    if 'inventory'in value:
        value['inventory'].pop('observed_at',None)
        for row in value['inventory'].get('campaigns',[]):row.pop('observed_at',None)
    return management.digest(value)


async def _prepare(db,*,scope,grant_key,client_factory=GoogleAdsClient,allow_consumed=False):
    if not guard.budget_guard_runtime_available():_fail('google_funded_runtime_unavailable')
    await _owner(db,{'id':scope['user_id']})
    record=await db[grants.GRANTS].find_one({**scope,'grant_key':grant_key},{'_id':0})
    started=management.now()
    check=deepcopy(record)
    if allow_consumed and check:check['consumed']=False
    _active(check,started)
    intent=pure.prepare_google_funded_creation_intent(grant=record['grant'],draft=record['review']['draft'],current=started)
    product=await _products(db,record,started)
    if {key:product[key] for key in ('product_id','canonical_url','name','sku')}!=record['review'].get('product_review_binding'):
        _fail('google_funded_product_review_changed')
    client,epoch=await management.account_client(db,**scope,client_factory=client_factory)
    try:
        metadata=await client.account_metadata()
        if (metadata.get('identity_verified') is not True or metadata.get('account_id')!=scope['account_id']
            or any(metadata.get(key)!=record['grant'][key] for key in ('currency_code','time_zone'))):
            _fail('google_funded_native_account_unproved')
        inventory=await _inventory(client,metadata)
        # Bind all proof to the earliest read, including credentials/catalog.
        inventory['observed_at']=started.isoformat()
        for row in inventory['campaigns']:row['observed_at']=started.isoformat()
        settings=await _configuration(client,record,started)
        validation=await client.mutate(intent['request']['mutateOperations'],validate_only=True)
        if not isinstance(validation,dict) or validation.get('error') or validation.get('partialFailureError'):
            _fail('google_funded_provider_validation_unproved')
    finally:await client.close()
    if not 0<=(management.now()-started).total_seconds()<90:_fail('google_funded_native_stale')
    payload={'grant_key':grant_key,'grant_revision':record['revision'],'review_digest':record['review_digest'],
        'intent':intent,'product':product,'metadata':{key:metadata[key] for key in ('account_id','currency_code','time_zone')},
        'auth_epoch':epoch,'inventory':inventory,'settings':settings,'observed_at':started.isoformat()}
    return _Prepared(db,guard._plain(payload),_PROOF)


def validate_command(db,command):
    prepared=getattr(command,'prepared',None)
    if not isinstance(prepared,_Prepared) or prepared.db is not db or prepared.proof is not _PROOF:
        _fail('google_funded_native_bundle_unproved')
    bounded=_Prepared(db,guard._plain(prepared.payload),_PROOF)
    if isinstance(command,guard.FundedCreationSettlement):
        return type(command)(command.grant_key,command.execution_claim_id,bounded)
    return type(command)(command.grant_key,command.proposal_id,command.execution_claim_id,bounded)


def allocation_lineage(entry):
    return management.digest({key:entry.get(key) for key in ('user_id','account_id','allocation_id','operation_id','kind',
        'grant_key','creation_identity','proposal_id','execution_claim_id','currency_code','budget_native',
        'account_cap_native','review_digest','created_at')})


async def _bound(view,snapshot,command,current,*,allow_consumed=False):
    scope={'user_id':snapshot.user_id,'account_id':snapshot.account_id}
    record=await view[grants.GRANTS].find_one({**scope,'grant_key':command.grant_key},{'_id':0})
    if allow_consumed:
        if not record or record.get('state')!='active':_fail('google_funded_grant_inactive_or_consumed')
        copy=deepcopy(record);copy['consumed']=False;_active(copy,current)
    else:_active(record,current)
    await _owner(view,{'id':scope['user_id']})
    row=await view[management.PROPOSALS].find_one({**scope,'proposal_id':command.proposal_id,
        'execution_claim_id':command.execution_claim_id,'status':'approved','submission_started':False},{'_id':0})
    fence=await view[management.FENCES].find_one({**scope,'proposal_id':command.proposal_id,
        'execution_claim_id':command.execution_claim_id,'status':'claimed'},{'_id':0})
    bundle=command.prepared.payload
    pure.revalidate_google_funded_creation_intent(bundle['intent'],grant=record['grant'],draft=record['review']['draft'],current=current)
    if (not row or not fence or row.get('actor_id')!='mezan_google_automation_v1'
        or row.get('automation_source')!={'kind':'funded_new_creation','grant_key':command.grant_key,
            'creation_identity':bundle['intent']['creation_identity']}
        or row.get('provider')!='google_ads' or row.get('action')!='create_campaign' or row.get('campaign_id') is not None
        or row.get('before_digest') is not None or row.get('auth_epoch')!=bundle['auth_epoch']
        or row.get('currency')!=record['grant']['currency_code'] or row.get('time_zone')!=record['grant']['time_zone']
        or row.get('idempotency_key')!=bundle['intent']['creation_identity'] or row.get('planned')!=record['review']['draft']
        or row.get('confirmation_digest')!=management.digest(management.immutable(row))
        or row.get('operations')!=bundle['intent']['request']['mutateOperations']
        or row.get('draft_id')!=record['review']['draft_id'] or row.get('draft_revision')!=record['review']['draft_revision']
        or bundle['review_digest']!=record['review_digest'] or bundle['grant_revision']!=record['revision']
        or (current-pure._instant(bundle['observed_at'])).total_seconds()<0
        or (current-pure._instant(bundle['observed_at'])).total_seconds()>=90
        or await _products(view,record,current)!=bundle['product']):_fail('google_funded_binding_changed')
    draft=await view[management.DRAFTS].find_one({'user_id':scope['user_id'],'draft_id':row['draft_id'],
        'revision':row['draft_revision'],'account_id':scope['account_id']},{'_id':0})
    if not draft or pure.funded_creation_draft_digest(draft['draft'])!=record['grant']['draft_digest']:_fail('google_funded_draft_changed')
    return scope,record,row,bundle


async def _guard_funded_command(db,*,session,snapshot,command,operation_id,current):
    view=SessionDatabase(db,session);old=guard.thaw_snapshot(snapshot.guard)
    if isinstance(command,guard.FundedCreationSettlement):
        return await _settle_tx(view,snapshot,command,operation_id,current)
    if not guard.budget_guard_runtime_available():_fail('google_funded_runtime_unavailable')
    floor=isinstance(command,guard.FundedCreationDispatchFloor)
    scope,record,row,bundle=await _bound(view,snapshot,command,current,allow_consumed=floor)
    run=await view[grants.RUNS].find_one({**scope,'grant_key':command.grant_key},{'_id':0})
    key='funded_new_'+management.digest({'grant_key':command.grant_key,'claim_id':command.execution_claim_id})
    if not floor:
        if (run or record.get('consumed') or old['active_claim'] is not None or len(old['allocations'])>=guard.MAX_ALLOCATIONS
            or (snapshot.standing_policy or {}).get('_dispatch_claim') is not None
            or (snapshot.creation_policy or {}).get('_creation_claim') is not None):
            _fail('google_funded_slot_consumed_or_pending')
        exposure=account_exposure(snapshot,bundle['inventory'],record['grant'],current,funded_grant=record['grant'])
        amount=guard._money(record['grant']['budget_native'])
        if exposure['total']+amount>exposure['limit']:_fail('google_budget_guard_account_limit')
        claim={'allocation_id':key,'proposal_id':command.proposal_id,'execution_claim_id':command.execution_claim_id,
            'grant_key':command.grant_key,'creation_identity':bundle['intent']['creation_identity'],
            'graph_digest':bundle['intent']['graph_digest'],'grant_revision':record['revision'],
            'review_digest':record['review_digest'],'policy_binding':policy_binding(snapshot),'native_semantic_digest':_semantic(bundle)}
        entry={**scope,**claim,'operation_id':operation_id,'kind':'funded_new_creation','state':'reserved',
            'currency_code':record['grant']['currency_code'],'budget_native':record['grant']['budget_native'],
            'account_cap_native':record['grant']['max_account_daily_budget_native'],'created_at':current.isoformat()}
        entry['reservation_lineage_digest']=allocation_lineage(entry)
        changed=await view[grants.GRANTS].update_one({**scope,'grant_key':command.grant_key,'revision':record['revision'],
            'state':'active','consumed':False},{'$set':{'consumed':True,'claim':claim}})
        if changed.matched_count!=1:_fail('google_funded_grant_changed')
        run={**scope,**claim,'phase':'reserved','consumed':True,'intent':bundle['intent'],
            'bundle':bundle,'claim':claim,'allocation_id':key,'reserved_at':current.isoformat()}
        await view[grants.RUNS].insert_one(run)
        allocations={**old['allocations'],key:entry};active={key:value for key,value in claim.items()
            if key in {'allocation_id','proposal_id','execution_claim_id'}}
        active['operation_id']=operation_id
        result={'kind':'funded_reservation','allocation_id':key,'grant_key':command.grant_key}
    else:
        entry=old['allocations'].get(key);claim=record.get('claim')
        if (not run or run.get('phase')!='reserved' or run.get('execution_claim_id')!=command.execution_claim_id
            or run.get('proposal_id')!=command.proposal_id or claim!=run.get('claim')
            or not entry or entry.get('kind')!='funded_new_creation' or entry.get('state')!='reserved'
            or entry.get('reservation_lineage_digest')!=allocation_lineage(entry)
            or (old.get('active_claim') or {}).get('allocation_id')!=key
            or entry.get('graph_digest')!=bundle['intent']['graph_digest']
            or entry.get('review_digest')!=record['review_digest'] or entry.get('budget_native')!=record['grant']['budget_native']
            or run.get('policy_binding')!=policy_binding(snapshot) or run.get('native_semantic_digest')!=_semantic(bundle)):
            _fail('google_funded_binding_changed')
        exposure=account_exposure(snapshot,bundle['inventory'],record['grant'],current,funded_grant=record['grant'])
        if exposure['total']>exposure['limit']:_fail('google_budget_guard_account_limit')
        marker={'operation_id':operation_id,'execution_claim_id':command.execution_claim_id,'graph_digest':run['graph_digest'],
            'grant_revision':record['revision'],'submitted_at':current.isoformat()}
        changed=await view[management.PROPOSALS].update_one({**scope,'proposal_id':command.proposal_id,
            'execution_claim_id':command.execution_claim_id,'status':'approved','submission_started':False},
            {'$set':{'status':'submitted','submission_started':True,'submitted_at':current.isoformat(),
                'funded_dispatch_marker':marker}})
        if changed.matched_count!=1:_fail('google_funded_binding_changed')
        changed=await view[grants.RUNS].update_one({**scope,'grant_key':command.grant_key,'phase':'reserved',
            'execution_claim_id':command.execution_claim_id},{'$set':{'phase':'submitted','dispatch_marker':marker}})
        if changed.matched_count!=1:_fail('google_funded_binding_changed')
        allocations={**old['allocations'],key:{**entry,'dispatch_marker':marker}};active=old['active_claim']
        result={'kind':'funded_dispatch_floor','allocation_id':key,'grant_key':command.grant_key,
            'authority_valid_from':record['grant']['valid_from'],'authority_valid_until':min(
                pure._instant(record['grant']['expires_at']),pure._instant(row['expires_at'])).isoformat()}
    if pure._instant(row.get('expires_at')) is None or pure._instant(row['expires_at'])<=current:_fail('google_funded_proposal_expired')
    fenced=await view[management.FENCES].update_one({**scope,'proposal_id':command.proposal_id,
        'execution_claim_id':command.execution_claim_id,'status':'claimed'},
        {'$set':{'_funded_storage_operation_id':operation_id}})
    if fenced.matched_count!=1:_fail('google_funded_binding_changed')
    result.update(funded_dispatch_authorized=False,provider_writes=0)
    return allocations,active,result


async def _settlement_prepare(db,*,scope,grant_key,execution_claim_id,client_factory):
    run=await db[grants.RUNS].find_one({**scope,'grant_key':grant_key,'execution_claim_id':execution_claim_id},{'_id':0})
    if not run:_fail('google_funded_settlement_unproved')
    row=await db[management.PROPOSALS].find_one({**scope,'proposal_id':run['proposal_id'],
        'execution_claim_id':execution_claim_id},{'_id':0})
    if not row:_fail('google_funded_settlement_unproved')
    operations=run['intent']['request']['mutateOperations']
    if row.get('operations')!=operations or row.get('confirmation_digest')!=management.digest(management.immutable(row)):
        _fail('google_funded_settlement_unproved')
    receipt=row.get('provider_receipt');names=None;report=None
    cancelled=(run.get('phase')=='reserved' and row.get('submission_started') is False and row.get('status')=='failed'
        and row.get('provider_write_reached') is False and row.get('safe_error')=='execution_cancelled_before_dispatch'
        and receipt is None and row.get('resource_names') in (None,[]))
    rejection=row.get('rejection') or {}
    rejected=(run.get('phase')=='submitted' and row.get('submission_started') is True and row.get('status')=='failed'
        and row.get('safe_error')=='google_ads_provider_rejected' and row.get('provider_write_reached') is False
        and type(rejection.get('provider_status')) is int and rejection['provider_status'] in {400,401,403,404,409,422}
        and receipt is None and row.get('resource_names') in (None,[]))
    if not (cancelled or rejected):
        if run.get('phase')!='submitted' or row.get('submission_started') is not True or not isinstance(receipt,dict):
            _fail('google_funded_settlement_unproved')
        names=mutation_resource_names(receipt,operations,scope['account_id'])
        if row.get('resource_names')!=names:_fail('google_funded_settlement_unproved')
        client,epoch=await management.account_client(db,**scope,client_factory=client_factory)
        try:verified,report=await client.verify_operations(operations,names,accepted_receipt=True)
        finally:await client.close()
        if (verified is not True or not isinstance(report,dict) or report.get('readback_independent') is not True
            or report.get('mismatches')!=[] or report.get('coverage_gaps')!=[]
            or sorted(report.get('verified_resources') or [])!=sorted(names)):
            _fail('google_funded_native_outcome_unconfirmed')
    payload={'grant_key':grant_key,'execution_claim_id':execution_claim_id,'proposal_digest':management.digest(row),
        'run_digest':management.digest(run),'outcome':'cancelled_before_http' if cancelled else 'provider_rejected' if rejected else 'created_enabled',
        'resource_names':names,'verification':report,'observed_at':management.now().isoformat()}
    return _Prepared(db,guard._plain(payload),_PROOF)


async def _settle_tx(view,snapshot,command,operation_id,current):
    old=guard.thaw_snapshot(snapshot.guard);scope={'user_id':snapshot.user_id,'account_id':snapshot.account_id}
    run=await view[grants.RUNS].find_one({**scope,'grant_key':command.grant_key,'execution_claim_id':command.execution_claim_id},{'_id':0})
    proof=command.prepared.payload
    if not run or proof.get('run_digest')!=management.digest(run) or run.get('phase') not in {'reserved','submitted'}:
        _fail('google_funded_settlement_unproved')
    row=await view[management.PROPOSALS].find_one({**scope,'proposal_id':run['proposal_id'],
        'execution_claim_id':command.execution_claim_id},{'_id':0})
    record=await view[grants.GRANTS].find_one({**scope,'grant_key':command.grant_key},{'_id':0})
    key=run['allocation_id'];entry=old['allocations'].get(key)
    observed=pure._instant(proof.get('observed_at'))
    if (not row or proof.get('proposal_digest')!=management.digest(row) or not record or record.get('claim')!=run.get('claim')
        or not entry or entry.get('reservation_lineage_digest')!=allocation_lineage(entry) or entry.get('state')!='reserved'
        or entry.get('execution_claim_id')!=command.execution_claim_id
        or (old.get('active_claim') or {}).get('allocation_id')!=key or observed is None
        or not 0<=(current-observed).total_seconds()<90):_fail('google_funded_settlement_unproved')
    entry={**entry,'settled_at':current.isoformat(),'settlement_proof_digest':management.digest(proof),'outcome':proof['outcome']}
    if proof['outcome']=='created_enabled':
        names=proof['resource_names'];operations=run['intent']['request']['mutateOperations']
        campaign_index=next(i for i,op in enumerate(operations) if 'campaignOperation' in op)
        budget_index=next(i for i,op in enumerate(operations) if 'campaignBudgetOperation' in op)
        campaign,budget=names[campaign_index],names[budget_index]
        if (not re.fullmatch(rf'customers/{snapshot.account_id}/campaigns/[1-9][0-9]*',campaign)
            or not re.fullmatch(rf'customers/{snapshot.account_id}/campaignBudgets/[1-9][0-9]*',budget)
            or any(other.get('campaign_id')==campaign.rsplit('/',1)[-1] or other.get('budget_resource')==budget
                for other_key,other in old['allocations'].items() if other_key!=key and other.get('kind')!='standing')):
            _fail('google_funded_native_identity_conflict')
        entry.update(state='native_enabled',campaign_id=campaign.rsplit('/',1)[-1],budget_resource=budget,
            original_resource_names=names,original_graph_digest=run['graph_digest'])
    elif proof['outcome'] in {'cancelled_before_http','provider_rejected'}:entry['state']='settled'
    else:_fail('google_funded_settlement_unproved')
    changed=await view[grants.RUNS].update_one({**scope,'grant_key':command.grant_key,'phase':run['phase'],
        'execution_claim_id':command.execution_claim_id},{'$set':{'phase':'settled','settled_at':current.isoformat(),
            'settlement':proof,'consumed':True}})
    if changed.matched_count!=1:_fail('google_funded_settlement_unproved')
    changed=await view[grants.GRANTS].update_one({**scope,'grant_key':command.grant_key,'claim':record['claim']},
        {'$set':{'claim':None,'consumed':True}})
    if changed.matched_count!=1:_fail('google_funded_settlement_unproved')
    # Read snapshots alone cannot order an owner cancellation or fence release.
    changed=await view[management.PROPOSALS].update_one({**scope,'proposal_id':run['proposal_id'],
        'execution_claim_id':command.execution_claim_id,'status':row['status'],'submission_started':row['submission_started']},
        {'$set':{'_funded_settlement_operation_id':operation_id}})
    if changed.matched_count!=1:_fail('google_funded_settlement_unproved')
    changed=await view[management.FENCES].update_one({**scope,'proposal_id':run['proposal_id'],
        'execution_claim_id':command.execution_claim_id,'status':'claimed'},
        {'$set':{'_funded_settlement_operation_id':operation_id}})
    if changed.matched_count!=1:_fail('google_funded_settlement_unproved')
    return {**old['allocations'],key:entry},None,{'kind':'funded_settlement','settled':True,'quota_refunded':False,
        'grant_key':command.grant_key,'native_fence_released':False,'funded_dispatch_authorized':False,'provider_writes':0}


async def reserve_google_funded_creation(db,*,user_id,account_id,grant_key,proposal_id,execution_claim_id,client_factory=GoogleAdsClient):
    scope=_scope(user_id,account_id)
    if await db[grants.RUNS].find_one({**scope,'grant_key':grant_key},{'_id':1}):_fail('google_funded_slot_consumed_or_pending')
    await grants.ensure_indexes(db)
    prepared=await _prepare(db,scope=scope,grant_key=grant_key,client_factory=client_factory)
    capability=await guard.probe_google_budget_guard_transactions(db)
    return await guard.run_google_budget_guard_transaction(db,**scope,
        operation_id='funded_reserve_'+management.digest({'grant_key':grant_key,'execution_claim_id':execution_claim_id}),
        capability=capability,planner=lambda snapshot:guard.FundedCreationReservation(grant_key,proposal_id,execution_claim_id,prepared))


async def floor_google_funded_creation(db,*,user_id,account_id,grant_key,proposal_id,execution_claim_id,client_factory=GoogleAdsClient):
    scope=_scope(user_id,account_id)
    run=await db[grants.RUNS].find_one({**scope,'grant_key':grant_key,'execution_claim_id':execution_claim_id},{'_id':0})
    if not run or run.get('phase')!='reserved':_fail('google_funded_slot_consumed_or_pending')
    prepared=await _prepare(db,scope=scope,grant_key=grant_key,client_factory=client_factory,allow_consumed=True)
    capability=await guard.probe_google_budget_guard_transactions(db)
    result=await guard.run_google_budget_guard_transaction(db,**scope,
        operation_id='funded_floor_'+management.digest({'grant_key':grant_key,'execution_claim_id':execution_claim_id}),
        capability=capability,planner=lambda snapshot:guard.FundedCreationDispatchFloor(grant_key,proposal_id,execution_claim_id,prepared))
    result['authority_expired_after_commit']=not (pure._instant(result['authority_valid_from'])<=management.now()<pure._instant(result['authority_valid_until']))
    # This is storage evidence only. No live client or permission is returned.
    result['funded_dispatch_authorized']=False
    return result


async def settle_google_funded_creation(db,*,user_id,account_id,grant_key,execution_claim_id,client_factory=GoogleAdsClient):
    scope=_scope(user_id,account_id)
    prepared=await _settlement_prepare(db,scope=scope,grant_key=grant_key,execution_claim_id=execution_claim_id,client_factory=client_factory)
    capability=await guard.probe_google_budget_guard_transactions(db)
    return await guard.run_google_budget_guard_transaction(db,**scope,
        operation_id='funded_settle_'+management.digest({'grant_key':grant_key,'execution_claim_id':execution_claim_id}),
        capability=capability,planner=lambda snapshot:guard.FundedCreationSettlement(grant_key,execution_claim_id,prepared))
