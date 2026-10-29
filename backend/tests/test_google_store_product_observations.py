from copy import deepcopy
from datetime import datetime, timezone
import pytest
from integrations_control_center.google_store_product_observations import observe_product_lines

NOW=datetime(2026,10,29,12,tzinfo=timezone.utc)
def order(**changes):
    raw={'id':10,'reference_id':20,'date':'2026-10-25T21:30:00Z','status':{'slug':'completed'},'payment_status':'paid','currency':'SAR',
         'items':[{'id':30,'product':{'id':123},'quantity':'2'}]}
    value={'user_id':'owner','order_id':'10','order_number':'20','order_date':'2026-10-25','order_status_slug':'completed','payment_status':'paid','currency':'SAR','raw_by_source':{'salla_direct':raw}}
    value.update(changes);return value

def run(rows,ids=['123']):return observe_product_lines(rows,owner_id='owner',product_ids=ids,as_of=NOW)
def group(result):return result['products'][0]['currency_groups'][0]

def test_matched_raw_lines_are_useful_partial_observations_not_net_sales():
    out=run([order()]);w=group(out)['windows']['last_14_days']
    assert w=={'observed_orders':1,'observed_lines':1,'observed_units':'2'}
    assert group(out)['platform_account_id'] is None
    assert group(out)['currency_code']=='SAR'
    assert out['coverage_complete'] is False and out['refunds_reconciled'] is False
    assert out['products'][0]['sales_native'] is None and out['products'][0]['profitability'] is None
    assert out['products'][0]['growth_14_days_percent'] is None
    assert out['windows']['last_14_days']['to']=='2026-10-28'
    assert '10' not in out.get('order_ids',[])

@pytest.mark.parametrize('value',[True,0,-1,'NaN','Infinity',' 2','2e1',None])
def test_invalid_raw_quantity_never_uses_canonical_defaults(value):
    row=order();row['products']=[{'product_id':'123','quantity':9}];row['raw_by_source']['salla_direct']['items'][0]['quantity']=value
    out=run([row]);assert out['products'][0]['currency_groups']==[]

@pytest.mark.parametrize('change',[{'user_id':'foreign'},{'order_id':'999'},{'order_number':'999'}])
def test_identity_binding_rejects_wrong_scope(change):
    assert run([order(**change)])['products'][0]['currency_groups']==[]

@pytest.mark.parametrize('status',['canceled','cancelled','refunded','partially_refunded','pending','unknown'])
def test_non_fulfilled_or_refunded_state_is_not_counted(status):
    row=order();row['order_status_slug']=status;row['raw_by_source']['salla_direct']['status']['slug']=status
    assert run([row])['products'][0]['currency_groups']==[]

def test_duplicate_order_identity_rejects_all_copies_not_first_writer():
    out=run([order(),deepcopy(order())]);assert out['products'][0]['currency_groups']==[]
    assert out['diagnostics']['duplicate_order_documents']==2

def test_duplicate_or_conflicting_line_rejects_whole_order():
    row=order();raw=row['raw_by_source']['salla_direct'];raw['items'].append(deepcopy(raw['items'][0]));assert run([row])['products'][0]['currency_groups']==[]
    raw['items'][1]['id']=31;raw['items'][1]['product_id']=456;assert run([row])['products'][0]['currency_groups']==[]

def test_no_sku_name_or_parent_fallback_and_distinct_currency_groups():
    a=order();a['raw_by_source']['salla_direct']['items'][0]={'id':30,'sku':'123','name':'123','parent_product_id':123,'quantity':2}
    assert run([a])['products'][0]['currency_groups']==[]
    a=order();b=deepcopy(a);b.update(order_id='11',order_number='21',currency='USD');b['raw_by_source']['salla_direct'].update(id=11,reference_id=21,currency='USD')
    assert [g['currency_code'] for g in run([a,b])['products'][0]['currency_groups']]==['SAR','USD']

def test_empty_and_truncated_do_not_claim_zero_sales_or_partial_counts():
    assert run([])['products'][0]['currency_groups']==[]
    out=observe_product_lines([order()],owner_id='owner',product_ids=['123'],as_of=NOW,truncated=True)
    assert out['products'][0]['currency_groups']==[] and out['diagnostics']['observed_documents'] is None

def test_exact_source_timezone_and_overlapping_windows_are_independent():
    out=run([order()]);assert group(out)['windows']['last_14_days']['observed_units']=='2'
    assert group(out)['windows']['last_30_days']['observed_units']=='2'
    assert group(out)['windows']['last_90_days']['observed_units']=='2'
    assert group(out)['windows']['previous_14_days']['observed_units'] is None
    row=order();row['raw_by_source']['salla_direct']['date']='2026-10-25T21:30:00';assert run([row])['products'][0]['currency_groups']==[]

def test_canonical_conflicts_and_known_refund_markers_reject():
    for mutate in ('currency','state','refund'):
        row=order()
        if mutate=='currency':row['currency']='USD'
        if mutate=='state':row['order_status_slug']='cancelled'
        if mutate=='refund':row['raw_by_source']['salla_direct']['refunded_at']='2026-10-26T00:00:00Z'
        assert run([row])['products'][0]['currency_groups']==[]

def test_real_sync_display_status_is_checked_against_source_label_not_guessed_translation():
    row=order(order_status='مكتمل');row['raw_by_source']['salla_direct']['status']['name']='مكتمل'
    assert group(run([row]))['windows']['last_14_days']['observed_units']=='2'
    row['order_status']='ملغي';assert run([row])['products'][0]['currency_groups']==[]

@pytest.mark.parametrize('mutation',[lambda raw:raw.update(items=None),lambda raw:raw.update(items=[]),lambda raw:raw.update(currency='ZZZ'),lambda raw:raw['items'][0].update(parent_product_id=999),lambda raw:raw.update(date={'date':'2026-10-25T21:30:00Z','timezone':'Asia/Riyadh'})])
def test_ambiguous_or_missing_raw_source_stays_unknown(mutation):
    row=order();mutation(row['raw_by_source']['salla_direct']);assert run([row])['products'][0]['currency_groups']==[]

def test_multiple_lines_same_product_are_one_observed_order_but_two_lines():
    row=order();row['raw_by_source']['salla_direct']['items'].append({'id':31,'product_id':123,'quantity':'3.5'})
    assert group(run([row]))['windows']['last_14_days']=={'observed_orders':1,'observed_lines':2,'observed_units':'5.5'}

def test_canonical_payment_or_currency_alone_cannot_replace_missing_raw_proof():
    for missing in ('payment','currency'):
        row=order();raw=row['raw_by_source']['salla_direct']
        raw.pop('payment_status' if missing=='payment' else 'currency')
        assert run([row])['products'][0]['currency_groups']==[]
