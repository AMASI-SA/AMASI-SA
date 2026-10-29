"""Pure funded-new graph claims; no DB, OAuth or provider calls."""
from copy import deepcopy
from datetime import timedelta
import pytest
from integrations_control_center import google_ads_funded_creation_contract as funded
from integrations_control_center.google_ads_automation_creation_contract import prepare_google_creation_intent
from integrations_control_center.google_ads_campaign_builder import build_campaign_operations
from tests.test_google_ads_automation_creation_contract import prepared_case,NOW


def inputs():
    draft=prepare_google_creation_intent(**prepared_case())['draft']
    grant={'schema_version':1,'mode':'create_new_enabled_search','user_id':'owner','account_id':'123',
        'revision':1,'owner_generation':1,'reviewed_by':'owner','owner_review_digest':'b'*64,
        'valid_from':NOW.isoformat(),'expires_at':(NOW+timedelta(days=1)).isoformat(),
        'currency_code':'SAR','time_zone':'Asia/Riyadh','budget_native':'15',
        'max_campaign_daily_budget_native':'20','max_account_daily_budget_native':'55',
        'draft_digest':funded.funded_creation_draft_digest(draft),'request_key':'reviewed_launch_1'}
    return {'grant':grant,'draft':draft,'current':NOW}


def test_exact_existing_paused_draft_builds_one_new_enabled_graph_without_changing_input_or_builder():
    values=inputs();before=deepcopy(values);intent=funded.prepare_google_funded_creation_intent(**values)
    operations=intent['request']['mutateOperations']
    assert intent['creation_identity'].startswith('google_funded_create_')
    assert intent['request_count']==1 and intent['request']['partialFailure'] is False
    assert intent['executable'] is intent['funded_dispatch_authorized'] is intent['persisted_owner_authorization_verified'] is False
    assert intent['native_configuration_verified'] is False and intent['provider_writes']==0
    assert next(op['campaignOperation']['create']['status'] for op in operations if 'campaignOperation' in op)=='ENABLED'
    assert next(op['campaignOperation']['create']['status'] for op in build_campaign_operations(values['draft'],'123') if 'campaignOperation'in op)=='PAUSED'
    assert values==before and funded.revalidate_google_funded_creation_intent(intent,**values)==intent
    assert funded.prepare_google_funded_creation_intent(**values)==intent


@pytest.mark.parametrize('field,value',[
    ('schema_version',True),('mode','enable_existing'),('mode','create_paused'),('account_id','999'),('revision',True),
    ('owner_generation',0),('reviewed_by','someone_else'),('owner_review_digest','invalid'),
    ('valid_from','2026-10-26T09:00:00'),('expires_at',NOW.isoformat()),
    ('valid_from',(NOW+timedelta(seconds=1)).isoformat()),
    ('expires_at',(NOW+timedelta(days=31)).isoformat()),('time_zone','invalid_zone'),('currency_code','sar'),
    ('budget_native','0'),('budget_native',15),('budget_native',True),('budget_native','NaN'),
    ('max_campaign_daily_budget_native','14'),('max_account_daily_budget_native','19'),
    ('request_key','google_create_'+'a'*64),('request_key','google_funded_create_'+'a'*64),
    ('draft_digest','0'*64),('existing_campaign_id','456'),('enabled',True),('partialFailure',True)])
def test_grant_cannot_supply_mode_scope_money_or_implicit_authority(field,value):
    values=inputs();values['grant'][field]=value
    with pytest.raises(funded.FundedCreationContractError):funded.prepare_google_funded_creation_intent(**values)


@pytest.mark.parametrize('change',['shopping','budget_total','group_paused','ad_paused','dsa','extra_ad','audience','other_account'])
def test_only_current_exact_search_rsa_scope(change):
    values=inputs();draft=values['draft']
    if change=='shopping':draft['campaign_type']='SHOPPING'
    elif change=='budget_total':draft['budget_mode']='TOTAL'
    elif change=='group_paused':draft['groups'][0]['status']='PAUSED'
    elif change=='ad_paused':draft['groups'][0]['ad_status']='PAUSED'
    elif change=='dsa':draft['groups'][0]['ad_format']='DSA'
    elif change=='extra_ad':draft['groups'][0]['ads']=[{'ad_format':'RSA'}]
    elif change=='audience':draft['groups'][0]['audience_ids']=['999']
    else:draft['account_id']='999'
    with pytest.raises(funded.FundedCreationContractError):funded.prepare_google_funded_creation_intent(**values)


@pytest.mark.parametrize('tamper',['positive_campaign','positive_budget','positive_group','positive_label','foreign_customer',
    'hidden_operation','remove','update_existing','reused_temp','goal_existing_campaign','goal_extra_field',
    'partial_failure','extra_request','paused_identity','forged_authority','hash_only','unknown_top_level'])
def test_tampered_graph_cannot_be_rehashed_into_valid_intent(tamper):
    values=inputs();intent=funded.prepare_google_funded_creation_intent(**values);ops=intent['request']['mutateOperations']
    if tamper.startswith('positive_'):
        kind={'positive_campaign':'campaignOperation','positive_budget':'campaignBudgetOperation',
            'positive_group':'adGroupOperation','positive_label':'labelOperation'}[tamper]
        create=next(op[kind]['create'] for op in ops if kind in op);create['resourceName']=create['resourceName'].rsplit('/',1)[0]+'/456'
    elif tamper=='foreign_customer':ops[0]['campaignBudgetOperation']['create']['resourceName']='customers/999/campaignBudgets/-1'
    elif tamper=='hidden_operation':ops.append(deepcopy(ops[-1]))
    elif tamper=='remove':ops.append({'campaignOperation':{'remove':'customers/123/campaigns/456'}})
    elif tamper=='update_existing':ops.append({'campaignOperation':{'update':{'resourceName':'customers/123/campaigns/456','status':'ENABLED'},'updateMask':'status'}})
    elif tamper=='reused_temp':ops[-2]['labelOperation']['create']['resourceName']=ops[0]['campaignBudgetOperation']['create']['resourceName']
    elif tamper.startswith('goal_'):
        goal=next(op['campaignConversionGoalOperation']['update'] for op in ops if 'campaignConversionGoalOperation' in op)
        if tamper=='goal_existing_campaign':goal['resourceName']='customers/123/campaignConversionGoals/456~PURCHASE~WEBSITE'
        else:goal['extra']='hidden'
    elif tamper=='partial_failure':intent['request']['partialFailure']=True
    elif tamper=='extra_request':intent['request_count']=2
    elif tamper=='paused_identity':intent['creation_identity']='google_create_'+'a'*64
    elif tamper=='forged_authority':intent['executable']=True
    elif tamper=='hash_only':intent['graph_digest']='0'*64
    else:intent['trusted_by_owner']=True
    if tamper!='hash_only':intent['graph_digest']=funded._digest({'account_id':'123','request':intent['request']})
    with pytest.raises(funded.FundedCreationContractError) as error:funded.revalidate_google_funded_creation_intent(intent,**values)
    assert error.value.code=='funded_creation_intent_changed'


@pytest.mark.parametrize('kind',['campaignOperation','campaignBudgetOperation','adGroupOperation','labelOperation'])
def test_operation_target_validator_rejects_positive_existing_targets(kind):
    ops=funded.prepare_google_funded_creation_intent(**inputs())['request']['mutateOperations']
    create=next(op[kind]['create'] for op in ops if kind in op)
    create['resourceName']=create['resourceName'].rsplit('/',1)[0]+'/456'
    with pytest.raises(funded.FundedCreationContractError):funded._new_graph(ops,'123')


def test_identity_changes_with_owner_review_and_cannot_mutate_previous_output():
    values=inputs();one=funded.prepare_google_funded_creation_intent(**values)
    values['grant']['revision']=2;two=funded.prepare_google_funded_creation_intent(**values)
    assert one['creation_identity']!=two['creation_identity']
    one['request']['mutateOperations'].clear()
    assert funded.prepare_google_funded_creation_intent(**values)==two


@pytest.mark.parametrize('field',list(inputs()['grant']))
def test_no_grant_or_financial_field_is_invented(field):
    values=inputs();values['grant'].pop(field)
    with pytest.raises(funded.FundedCreationContractError):funded.prepare_google_funded_creation_intent(**values)


@pytest.mark.parametrize('kind',['campaignCriterionOperation','adGroupCriterionOperation','adGroupAdOperation'])
def test_server_assigned_child_cannot_instead_target_existing_resource(kind):
    ops=funded.prepare_google_funded_creation_intent(**inputs())['request']['mutateOperations']
    child=next(op[kind]['create'] for op in ops if kind in op)
    child['resourceName']='customers/123/adGroupAds/456~789'
    with pytest.raises(funded.FundedCreationContractError):funded._new_graph(ops,'123')


def test_existing_paused_identity_is_not_a_supported_input_or_status_switch():
    with pytest.raises(TypeError):funded.prepare_google_funded_creation_intent(**inputs(),initial_status='PAUSED')
    values=inputs();values['grant']['creation_identity']='google_create_'+'a'*64
    with pytest.raises(funded.FundedCreationContractError):funded.prepare_google_funded_creation_intent(**values)
