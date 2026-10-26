from datetime import datetime,timezone
import pytest
from integrations_control_center.google_product_sales_evidence_reader import read_product_sales_evidence
NOW=datetime(2026,10,27,12,tzinfo=timezone.utc)
class Cursor:
    def __init__(self,rows): self.rows=rows
    async def to_list(self,length): return self.rows[:length]
class Collection:
    def __init__(self,rows):self.rows=rows;self.query=None;self.projection=None
    def find(self,query,projection):self.query=query;self.projection=projection;return Cursor([r for r in self.rows if r.get('user_id')==query['user_id'] and query['order_date']['$gte']<=r.get('order_date','')<=query['order_date']['$lte']])
class Db:
    def __init__(self,rows):self.c=Collection(rows)
    def __getitem__(self,key):assert key=='unified_orders';return self.c
def order(owner='owner',**kw):return {'user_id':owner,'order_date':'2026-10-25','order_number':'1','products':[{'product_id':'123','quantity':2}],**kw}
async def read(db,**kw):return await read_product_sales_evidence(db,owner_id='owner',product_ids=['123'],date_from='2026-10-01',date_to='2026-10-26',as_of=NOW,limit=2,**kw)
@pytest.mark.asyncio
async def test_no_orders_is_unknown_not_zero():
    out=await read(Db([]));assert out['completeness']=='unproven' and out['products'][0]['units_sold'] is None and out['products'][0]['sales_native'] is None
@pytest.mark.asyncio
async def test_owner_and_period_scoped_without_financial_assumption():
    db=Db([order(currency='QAR',status='cancelled'),order(owner='other')]);out=await read(db)
    assert db.c.query['user_id']=='owner' and 'raw_by_source' not in db.c.projection and 'products.product_id' not in db.c.projection
    assert out['products'][0]['units_sold'] is None and out['products'][0]['profitability'] is None
    assert out['observed_documents']==1 and out['cancellation_refunds_reconciled'] is False
@pytest.mark.asyncio
async def test_truncation_unknown_and_no_partial_counts():
    out=await read(Db([order(order_number=str(i)) for i in range(3)]))
    assert out['truncated'] is True and out['observed_documents'] is None and out['products'][0]['units_sold'] is None
@pytest.mark.asyncio
async def test_invalid_bounds_rejected():
    with pytest.raises(ValueError):await read_product_sales_evidence(Db([]),owner_id='owner',product_ids=['123'],date_from='2026-10-01',date_to='2026-10-27',as_of=NOW,limit=2)
@pytest.mark.asyncio
async def test_custom_product_parent_only_is_not_guessed():
    db=Db([order(products=[{'product_id':'999','parent_product_id':'123','quantity':1}])]);out=await read(db)
    assert out['products'][0]['units_sold'] is None and out['product_attribution_verified'] is False
@pytest.mark.asyncio
async def test_invalid_normalized_date_does_not_become_verified_evidence():
    with pytest.raises(ValueError): await read(Db([order(order_date='2026-10-25junk')]))
@pytest.mark.asyncio
async def test_duplicate_ids_and_foreign_identity_rejected():
    with pytest.raises(ValueError): await read_product_sales_evidence(Db([]),owner_id='owner',product_ids=['123','123'],date_from='2026-10-01',date_to='2026-10-26',as_of=NOW,limit=2)
    class Leaky(Collection):
        def find(self,query,projection):return Cursor([order(owner='other')])
    db=Db([]);db.c=Leaky([])
    with pytest.raises(ValueError):await read(db)
