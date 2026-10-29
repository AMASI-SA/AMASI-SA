"""Decision support lanes from trusted internal evidence; never a winner score."""
from copy import deepcopy

def _products(value,key):
    rows=value.get('products')
    if not isinstance(rows,list):raise ValueError('product evidence unproved')
    out={}
    for row in rows:
        product=row.get(key)
        if product in out:raise ValueError('duplicate product evidence')
        out[product]=row
    return out

def build_product_test_review(*,plan_id,plan_revision,category_id,selected_ids,frozen_as_of,owner_reviews,inventory,current,prior,linked):
    native=_products(inventory,'salla_product_id');store=_products(current,'product_id');history=_products(prior,'product_id') if prior else {}
    campaigns=_products(linked,'product_id')
    if (inventory.get('selected_product_ids')!=selected_ids or set(native)!=set(selected_ids) or set(store)!=set(selected_ids)
        or set(campaigns)!=set(selected_ids) or prior and set(history)!=set(selected_ids) or current.get('coverage_complete') is not False):raise ValueError('selected evidence scope changed')
    contexts=linked.get('campaign_contexts',[]);context_ids=[c['context_id'] for c in contexts]
    if len(set(context_ids))!=len(context_ids):raise ValueError('duplicate context')
    result=[]
    for product in selected_ids:
        item=native[product];fit=owner_reviews.get(product);refs=campaigns[product].get('context_refs',[])
        if len(set(refs))!=len(refs) or not set(refs)<=set(context_ids):raise ValueError('context reference unproved')
        for ref in refs:
            context=contexts[context_ids.index(ref)]
            if not any(p.get('product_id')==product for p in context.get('association_proofs',[])):raise ValueError('product context unproved')
        blocked=item.get('status') in {'hidden','out'} or item.get('is_available') is False or 'category_hidden_or_visibility_unproved' in item.get('blocking_reasons',[]) or fit and fit['decision']=='not_suitable'
        stock_positive=(item.get('simple_product_observed') is True and item.get('parent_stock',{}).get('state')=='positive') or any(
            v.get('stock',{}).get('state')=='positive' and not any(x.get('is_out_of_stock') is True for x in (v.get('source_option_labels') or [])) for v in item.get('variants',[]))
        stock_positive=stock_positive and item.get('status')=='sale' and item.get('is_available') is True
        observed=lambda row:any(v.get('observed_lines') is not None and v['observed_lines']>0 for g in row.get('currency_groups',[]) for v in g.get('windows',{}).values())
        reasons=[]
        if observed(store[product]):reasons.append('qualifying_saved_current_gross_lines')
        if product in history and observed(history[product]):reasons.append('qualifying_saved_prior_gross_lines_alignment_unknown')
        if refs:reasons.append('effective_shared_campaign_context_not_product_performance')
        decision=('blocked' if blocked else 'needs_owner_fit_review' if not fit or fit['decision']!='suitable_for_test' else
            'needs_stock_review' if not stock_positive else 'supported_test_review' if reasons else 'exploration_test_review')
        result.append({'product_id':product,'decision':decision,'season_fit':deepcopy(fit) if fit else {'decision':'unknown','meaning':'no_current_owner_review'},
            'inventory_observation':deepcopy(item),'evidence_reasons':reasons,'current_store_observations':deepcopy(store[product]),
            'prior_store_observations':deepcopy(history.get(product)),'campaign_context_refs':list(refs),
            'history_status':'qualified_observations_only_not_complete_history' if reasons else 'no_qualified_observations_not_absence_of_sales',
            'historical_cost_quality':'unknown','profitability':None,'growth':None,'preparation_time':None,'size_fit':'unknown',
            'approved_test_budget':'not_checked_requires_separate_owner_grant','paid_launch_eligible':False,'execution_authorized':False,
            'missing':['complete_order_coverage','historical_cost_completeness','preparation_duration','size_suitability','approved_test_budget','merchant_mapping']})
    lanes=['supported_test_review','exploration_test_review','needs_owner_fit_review','needs_stock_review','blocked']
    return {'plan_id':plan_id,'plan_revision':plan_revision,'category_id':category_id,'as_of':frozen_as_of.isoformat(),
        'products':result,'review_lanes':[{'decision':lane,'product_ids':[r['product_id'] for r in result if r['decision']==lane]} for lane in lanes],
        'ordering':'review_readiness_lanes_owner_selection_order_not_performance_ranking','campaign_contexts':deepcopy(contexts),
        'campaign_windows':deepcopy(linked.get('campaign_windows',[])),'current_windows':deepcopy(current.get('windows',{})),
        'prior_windows':deepcopy(prior.get('windows',{})) if prior else None,'calendar_alignment_verification':'unknown',
        'source_snapshot_atomic':False,'source_coverage_complete':False,'platform_conversions_added':False,'profitability':None,'growth':None,
        'review_writes':0,'source_writes':0,'provider_writes':0,'paid_launch_eligible':False,'execution_authorized':False}
