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
