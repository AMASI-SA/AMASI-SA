from integrations_control_center import snapchat_campaign_profitability as profit
from campaign_ai_product_intelligence_v3 import _variant_sales


def test_options_remain_separate_without_changing_product_totals():
    bucket = profit._new_campaign_bucket()
    lines = [dict(identity='p',salla_product_id='p',variant_id=v,quantity=q,units=q,allocated_sales_sar=q*price,cost_sar=q*50,base_complete=True) for v,q,price in [('child',2,139),('adult',1,149),('child',1,139),(None,1,139)]]
    profit._add_order_to_campaign(bucket, dict(order_sales_sar=705,product_cost_sar=250,allocated_product_sales_sar=705,lines=lines))
    product = profit._finalize_campaign(bucket,spend_sar=50)['products'][0]
    assert product['units'] == 5
    assert product['sales_sar'] == 705
    assert product['contribution_profit_sar'] == 405
    facts = product['variant_sales']
    assert facts['rows'] == [dict(variant_id='adult',ordered_units=1.0,orders=1),dict(variant_id='child',ordered_units=3.0,orders=1)]
    assert facts['unresolved_ordered_units'] == 1
    assert facts['conversion_rate_available'] is False
    assert facts['price_cause_verified'] is False


def test_old_cache_is_missing_not_zero_sales():
    evidence = _variant_sales({'entity_level':'campaign','campaign_profitability':{'products':[{'salla_product_id':'p','units':20}]}},'p')
    assert evidence['status'] == 'unavailable'
    assert evidence['rows'] == []


def test_subentity_sales_are_explicitly_parent_campaign_scope():
    facts = {'rows':[{'variant_id':'child','ordered_units':3,'orders':2}], 'unresolved_ordered_units':1,'status':'observed'}
    evidence = _variant_sales({'entity_level':'ad','parent_campaign_profitability':{'products':[{'salla_product_id':'p','variant_sales':facts}]}},'p')
    assert evidence['scope'] == 'parent_campaign_not_ad_or_ad_group'
    assert evidence['rows'][0]['ordered_units'] == 3
    assert evidence['conversion_rate_available'] is False
    assert evidence['price_cause_verified'] is False


def test_variant_identity_must_match_catalog_and_not_conflict():
    product = {'variants':[{'id':'child'},{'id':'adult'}]}
    assert profit._verified_variant_id({'variant_id':'child'},product) == 'child'
    assert profit._verified_variant_id({'variant_id':'other'},product) is None
    assert profit._verified_variant_id({'variant_id':'child','product_variant_id':'adult'},product) is None
    assert profit._verified_variant_id({'name':'child','sku':'child'},product) is None


def test_variant_sales_survives_model_evidence_compaction():
    from campaign_ai_evidence_v3 import _compact_product
    facts = _variant_sales({'entity_level':'campaign'},'p')
    assert _compact_product({'product_id':'p','variant_sales':facts})['variant_sales'] == facts


def test_variant_count_is_bounded_and_overflow_is_unresolved():
    bucket = profit._new_campaign_bucket()
    lines = [dict(identity='p',variant_id=str(v),quantity=1,units=1) for v in range(102)]
    profit._add_order_to_campaign(bucket,{'lines':lines})
    product = profit._finalize_campaign(bucket,spend_sar=0)['products'][0]
    assert len(product['variant_sales']['rows']) == 100
    assert product['variant_sales']['unresolved_ordered_units'] == 2
    evidence = _variant_sales({'entity_level':'campaign','campaign_profitability':{'products':[dict(product,salla_product_id='p')]}},'p')
    assert len(evidence['rows']) == 16
    assert evidence['rows_truncated'] is True
