from datetime import datetime, timezone
from copy import deepcopy
import pytest
from integrations_control_center.google_product_test_review import build_product_test_review

NOW=datetime(2026,10,29,12,tzinfo=timezone.utc)
def inputs():
    return dict(plan_id='winter',plan_revision=1,category_id='8',selected_ids=['123'],frozen_as_of=NOW,
        owner_reviews={'123':{'decision':'suitable_for_test','reason':'ملائم لهدايا الشتاء','revision':1}},
        inventory={'selected_product_ids':['123'],'products':[{'salla_product_id':'123','status':'sale','is_available':True,'parent_stock':{'state':'positive'},'variants':[], 'simple_product_observed':True,'blocking_reasons':[]}]},
        current={'products':[{'product_id':'123','currency_groups':[]}],'coverage_complete':False},prior=None,
        linked={'products':[{'product_id':'123','context_refs':[]}],'campaign_contexts':[],'campaign_windows':[]})

def test_no_history_is_exploration_not_new_or_zero_sales():
    result=build_product_test_review(**inputs()); row=result['products'][0]
    assert row['decision']=='exploration_test_review' and row['history_status']=='no_qualified_observations_not_absence_of_sales'
    assert row['profitability'] is row['growth'] is None and row['paid_launch_eligible'] is False

@pytest.mark.parametrize('kind',['fit','hidden','variant_parent','unknown'])
def test_owner_fit_and_variant_stock_are_not_inferred(kind):
    data=inputs()
    if kind=='fit':data['owner_reviews']={}
    if kind=='hidden':data['inventory']['products'][0]['status']='hidden'
    if kind=='variant_parent':data['inventory']['products'][0].update(simple_product_observed=False,variants=[{'variant_id':'1','stock':{'state':'zero'},'source_option_labels':None}])
    if kind=='unknown':data['inventory']['products'][0]['is_available']=None
    row=build_product_test_review(**data)['products'][0]
    assert row['decision']=={'fit':'needs_owner_fit_review','hidden':'blocked','variant_parent':'needs_stock_review','unknown':'needs_stock_review'}[kind]

def test_separate_native_currencies_and_shared_campaign_context_never_allocate_or_sum_metrics():
    data=inputs();data['selected_ids']=['123','456']
    for key,identity in [('inventory','salla_product_id'),('current','product_id'),('linked','product_id')]:
        second=deepcopy(data[key]['products'][0]);second[identity]='456';data[key]['products'].append(second)
    data['inventory']['selected_product_ids']=['123','456']
    data['owner_reviews']['456']=deepcopy(data['owner_reviews']['123'])
    data['current']['products'][0]['currency_groups']=[{'currency_code':currency,'windows':{'last_14_days':{'observed_orders':1,'observed_lines':1,'observed_units':'2'}}} for currency in ['SAR','USD']]
    context={'context_id':'ctx','provider':'snapchat_ads','account_id':'account','campaign_id':'campaign','currency':'SAR','date':'2026-10-28','association_proofs':[{'product_id':p,'event_id':'event'+p} for p in ['123','456']],'metrics':{'provider_reported_purchases':10}}
    data['linked']['campaign_contexts']=[context]
    for row in data['linked']['products']:row['context_refs']=['ctx']
    out=build_product_test_review(**data)
    assert len(out['campaign_contexts'])==1 and all(p['campaign_context_refs']==['ctx'] for p in out['products'])
    assert all('metrics' not in p for p in out['products'])
    assert [g['currency_code'] for g in out['products'][0]['current_store_observations']['currency_groups']]==['SAR','USD']
    assert out['platform_conversions_added'] is False and out['profitability'] is None

@pytest.mark.parametrize('state',['zero','zero_unlimited_unknown','unknown'])
def test_nonpositive_stock_never_infers_global_purchasability(state):
    data=inputs();data['inventory']['products'][0]['parent_stock']['state']=state
    assert build_product_test_review(**data)['products'][0]['decision']=='needs_stock_review'
