from datetime import datetime, timezone, timedelta
import pytest
from integrations_control_center.google_product_catalog_reader import read_catalog_products

NOW = datetime(2026, 10, 27, tzinfo=timezone.utc)
def row(**kw):
    return {'user_id':'owner', 'salla_product_id':'123', 'mezan_product_id':'mpv2_123', 'source':'salla', 'source_revision':'a'*64, 'last_synced_at':NOW.replace(tzinfo=None), 'status':'active', 'archived':False, 'quantity':3, 'unlimited_quantity':False, 'variants_count':0, **kw}
class Cursor:
    def __init__(self, rows): self.rows=rows
    async def to_list(self, length): return self.rows[:length]
class Collection:
    def __init__(self, rows): self.rows=rows; self.query=None
    def find(self, query, projection): self.query=query; return Cursor(self.rows)
class Db:
    def __init__(self, rows): self.c=Collection(rows)
    def __getitem__(self, key): assert key=='mezan_products_v2'; return self.c
async def read(rows, ids=['123']):
    db=Db(rows); result=await read_catalog_products(db, owner_id='owner', product_ids=ids, as_of=NOW, max_age_seconds=3600); assert db.c.query['user_id']=='owner'; return result
@pytest.mark.asyncio
async def test_valid_owner_product_and_unknown_cost():
    out=await read([row()]); p=out['products'][0]; assert p.status=='unknown' and p.cost_quality=='unknown' and out['execution_authorized'] is False and out['category_membership_verified'] is False
@pytest.mark.asyncio
@pytest.mark.parametrize('changes,expected',[({'status':'inactive'},'hidden'),({'quantity':0},'unknown'),({'variants_count':2},'unknown'),({'archived':True},'hidden'),({'quantity':None},'unknown')])
async def test_fail_closed_inventory(changes,expected):
    out=await read([row(**changes)]); assert out['products'][0].status==expected
@pytest.mark.asyncio
async def test_stale_missing_and_wrong_owner():
    out=await read([row(last_synced_at=NOW-timedelta(days=2))]); assert out['products']==[] and out['missing_or_unverified_product_ids']==['123']
    with pytest.raises(ValueError): await read([row(user_id='other')])
@pytest.mark.asyncio
async def test_duplicate_and_bad_revision_fail_closed():
    with pytest.raises(ValueError): await read([row(),row()])
    assert (await read([row(source_revision='bad')]))['products']==[]

def proved(**changes):
    from product_inventory_proof import build_inventory_evidence
    value=row(variants=[], options_count=0, details_loaded=True, details_synced_at=NOW, **changes)
    raw={'quantity':value['quantity'],'unlimited_quantity':value['unlimited_quantity'],'variants':value['variants'],'options':[]}
    value['inventory_evidence_v1']=build_inventory_evidence(raw,value)
    return value

@pytest.mark.asyncio
async def test_details_proof_reports_available_with_bounded_timestamp_not_profit():
    out=await read([proved()]); assert out['products'][0].status=='available'
    proof=out['inventory_reviews'][0]
    assert proof['state']=='available' and proof['observed_at']==NOW.isoformat()
    assert proof['scope']=='stored_details_snapshot' and proof['remote_variant_coverage_complete'] is False
    assert proof['maximum_supported_variants']==50
    assert out['products'][0].cost_quality=='unknown' and out['execution_authorized'] is False

@pytest.mark.asyncio
@pytest.mark.parametrize('change',[{'source_revision':'b'*64},{'quantity':4},{'details_synced_at':NOW-timedelta(days=2)},{'details_synced_at':NOW+timedelta(seconds=1)},{'variants_count':51}])
async def test_invalidated_details_proof_remains_unknown(change):
    value=proved();value.update(change);out=await read([value])
    assert out['products'][0].status=='unknown' and out['inventory_reviews'][0]['state']=='unproved'
    assert out['inventory_reviews'][0]['observed_at'] is None

@pytest.mark.asyncio
async def test_hidden_wins_even_when_details_stock_proof_is_available():
    out=await read([proved(status='inactive')]);assert out['products'][0].status=='hidden'
    assert out['inventory_reviews'][0]['state']=='available'

@pytest.mark.asyncio
@pytest.mark.parametrize('quantities,expected', [([0,2],'unknown'),([0,0],'unknown'),([None,2],'unknown')])
async def test_variant_stock_uses_all_bounded_rows_not_parent_alone(quantities,expected):
    from product_inventory_proof import build_inventory_evidence
    variants=[{'id':str(i+1),'quantity':q,'unlimited_quantity':False} for i,q in enumerate(quantities)]
    value=proved();value.update(variants=variants,variants_count=len(variants))
    value['inventory_evidence_v1']=build_inventory_evidence({'quantity':3,'unlimited_quantity':False,'variants':variants,'options':[]},value)
    result=await read([value]);product=result['products'][0]
    assert product.status==expected and product.available_quantity is None and product.unlimited_quantity is False
    assert result['inventory_reviews'][0]['state']==('unproved' if None in quantities else 'available' if any(quantities) else 'unavailable')

@pytest.mark.asyncio
async def test_explicit_zero_and_unlimited_proofs_are_not_truthiness():
    assert (await read([proved(quantity=0)]))['products'][0].status=='unknown'
    assert (await read([proved(quantity=None, unlimited_quantity=True)]))['products'][0].status=='available'
