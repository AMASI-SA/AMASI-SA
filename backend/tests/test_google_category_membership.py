from copy import deepcopy
from datetime import datetime,timezone,timedelta
import pytest
from product_v2_routes import normalize_salla_product
from integrations_control_center.google_category_membership import category_membership

NOW=datetime(2026,10,29,12,tzinfo=timezone.utc)
def product(**changes):
    raw={'id':123,'name':'shawl','status':'sale','categories':[{'id':8,'name':'Winter','url':'https://store.example/c/winter'}]}
    raw.update(changes);return normalize_salla_product(raw,user_id='owner',synced_at=NOW)
def check(row,url=None):return category_membership(row,owner_id='owner',product_id='123',category_ids=['8'],category_url=url,as_of=NOW,max_age_seconds=86400)

def test_positive_current_raw_membership_binds_mezan_salla_ids_and_time_not_remote_coverage():
    out=check(product(),url='https://store.example/c/winter')
    assert out['membership']=='observed_in_stored_source' and out['matched_category_ids']==['8']
    assert out['mezan_product_id']=='mpv2_123' and out['salla_product_id']=='123'
    assert out['observed_at']==NOW.isoformat() and out['category_url_matched_ids']==['8']
    assert out['remote_category_coverage_complete'] is False and out['execution_authorized'] is False
    assert out['platform_catalog_mapping_verified'] is False

def test_url_optional_and_never_inferred_from_id_or_slug():
    assert check(product())['category_url_status']=='not_requested'
    assert check(product(),url='https://store.example/c/other')['category_url_status']=='not_verified'
    raw=product();raw['raw_salla']['categories'][0].pop('url')
    assert check(raw,url='https://store.example/c/winter')['category_url_matched_ids']==[]

@pytest.mark.parametrize('mutation',[lambda r:r.update(user_id='foreign'),lambda r:r.update(mezan_product_id='other'),lambda r:r['raw_salla'].update(id=999),lambda r:r.update(source_revision='bad'),lambda r:r.update(last_synced_at=NOW-timedelta(days=2)),lambda r:r.update(last_synced_at=NOW+timedelta(seconds=1)),lambda r:r['raw_salla'].pop('categories'),lambda r:r.update(categories=[{'id':'9','name':'other'}]),lambda r:r['raw_salla'].update(category=[{'id':9}]),lambda r:r['raw_salla']['categories'].append({'id':8})])
def test_identity_missing_conflict_and_freshness_fail_closed(mutation):
    row=product();mutation(row);out=check(row)
    assert out['membership']=='unproved' and out['matched_category_ids']==[] and out['observed_at'] is None

def test_explicit_empty_or_other_category_does_not_prove_remote_nonmembership():
    for row in (product(categories=[]),product(categories=[{'id':9}])):
        out=check(row);assert out['membership']=='not_observed_in_stored_source' and out['matched_category_ids']==[]
        assert out['remote_category_coverage_complete'] is False

def test_old_details_snapshot_cannot_supply_missing_current_categories():
    row=product();row['raw_salla_details']=deepcopy(row['raw_salla']);row['raw_salla'].pop('categories')
    assert check(row)['membership']=='unproved'

@pytest.mark.parametrize('changes,visibility',[({'archived':True},'hidden'),({'status':'inactive'},'hidden'),({'status':'out_of_stock'},'out_of_stock')])
def test_hidden_and_out_of_stock_are_not_eligible_despite_positive_membership(changes,visibility):
    row=product();row.update(changes);out=check(row)
    assert out['catalog_visibility']==visibility and out['eligible_for_ad_selection'] is False

def test_bson_naive_datetime_is_utc_but_iso_string_is_not_writer_proof():
    row=product();row['last_synced_at']=NOW.replace(tzinfo=None)
    assert check(row)['observed_at']==NOW.isoformat()
    row['last_synced_at']=NOW.isoformat();assert check(row)['membership']=='unproved'
