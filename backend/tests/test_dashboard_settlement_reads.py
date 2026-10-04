"""Dashboard opt-in streaming contract; no production database required."""
import asyncio
import gc
import tracemalloc
from types import SimpleNamespace

import pytest
from settlements_routes import aggregate_settlements_by_provider, detect_provider, PROVIDERS
from dashboard_settlement_reads import iter_dashboard_settlement_rows, iter_dashboard_wallet_adjustments

NAMES = ['مدى', 'mada', 'Visa', 'تمارا', 'tabby', 'emkan', 'bank transfer', 'COD', 'unknown']
def row(i):
    return dict(payment_method=NAMES[i % len(NAMES)], adjustment_amount=[.005,-.015,1.335,None][i%4],
                original_amount=[10.005, -2.675, None][i%3], new_amount=[0,.125,-.555][i%3],
                order_created_at='2026-09-02', ignored='x'*1024)

def expected(count):
    out={p:dict(count=0,total_adjustment=0.,total_original=0.,total_new=0.) for p in PROVIDERS}
    for i in range(count):
        d=row(i); target=out[detect_provider(d['payment_method'])];target['count']+=1
        for field in ['adjustment','original','new']:
            target['total_'+field]+=float(d[field+'_amount'] or 0)
    for target in out.values():
        for field in ['adjustment','original','new']:target['total_'+field]=round(target['total_'+field],2)
    return out

class Cursor:
    def __init__(self, collection, projection):
        self.collection=collection;self.projection=projection;self.index=0;self.closed=False
    def batch_size(self, size):self.collection.batch_sizes.append(size);return self
    async def to_list(self, cap):
        self.collection.caps.append(cap)
        return [row(i) for i in range(min(cap,self.collection.count))]
    def __aiter__(self):return self
    async def __anext__(self):
        if self.index>=self.collection.count:raise StopAsyncIteration
        d=row(self.index);self.index+=1
        return {k:v for k,v in d.items() if self.projection.get(k)}
    async def close(self):self.closed=True

class Collection:
    def __init__(self,count):self.count=count;self.calls=[];self.batch_sizes=[];self.caps=[];self.cursor=None
    def find(self,query,projection):
        self.calls.append((query,projection));self.cursor=Cursor(self,projection);return self.cursor

def db(count):return SimpleNamespace(payment_adjustments=Collection(count))
def run(awaitable):return asyncio.run(awaitable)

@pytest.mark.parametrize('count',[0,1,1025])
def test_canonical_parity_below_legacy_cap(count):
    database=db(count)
    baseline=run(aggregate_settlements_by_provider(database,'owner','2026-09-01','2026-09-30'))
    actual=run(aggregate_settlements_by_provider(database,'owner','2026-09-01','2026-09-30',row_loader=iter_dashboard_settlement_rows))
    assert baseline==actual==expected(count)
    assert database.payment_adjustments.calls[-1]==({'user_id':'owner','adjusted_at':{'$gte':'2026-09-01','$lte':'2026-09-30'}},
        {'_id':0,'payment_method':1,'adjustment_amount':1,'original_amount':1,'new_amount':1})
    assert database.payment_adjustments.caps==[50000]
    assert database.payment_adjustments.batch_sizes==[128]
    assert database.payment_adjustments.cursor.closed


def test_whole_period_above_cap_has_bounded_python_memory_and_default_unchanged():
    database=db(50129)
    gc.collect();tracemalloc.start()
    actual=run(aggregate_settlements_by_provider(database,'owner',row_loader=iter_dashboard_settlement_rows))
    _,peak=tracemalloc.get_traced_memory();tracemalloc.stop()
    assert actual==expected(50129)
    assert sum(v['count'] for v in actual.values())==50129
    assert peak<2*1024*1024
    assert database.payment_adjustments.caps==[]
    baseline=run(aggregate_settlements_by_provider(database,'owner'))
    assert baseline==expected(50000)
    assert database.payment_adjustments.calls[-1]==({'user_id':'owner'},{'_id':0})
    assert database.payment_adjustments.caps==[50000]


def test_wallet_projection_query_and_early_close():
    async def check():
        database=db(1000);query={'user_id':'owner','adjusted_at':{'$gte':'a'},'payment_method':{'$in':['mada']}}
        reader=iter_dashboard_wallet_adjustments(database,query)
        assert await anext(reader)=={'adjustment_amount':.005,'order_created_at':'2026-09-02'}
        await reader.aclose()
        assert database.payment_adjustments.cursor.closed
        assert database.payment_adjustments.calls==[(query,{'_id':0,'adjustment_amount':1,'order_created_at':1})]
        assert database.payment_adjustments.batch_sizes==[128]
    run(check())

@pytest.mark.parametrize('failure',[ValueError('bad amount'),asyncio.CancelledError()])
def test_reducer_failure_and_cancellation_close_cursor(monkeypatch,failure):
    import settlements_routes
    def fail(_):raise failure
    monkeypatch.setattr(settlements_routes,'detect_provider',fail)
    database=db(2)
    with pytest.raises(type(failure)):
        run(aggregate_settlements_by_provider(database,'owner',row_loader=iter_dashboard_settlement_rows))
    assert database.payment_adjustments.cursor.closed


@pytest.mark.parametrize('start,end,window',[('a',None,{'$gte':'a'}),(None,'z',{'$lte':'z'}),(None,None,None)])
def test_optional_date_query_unchanged(start,end,window):
    database=db(0)
    run(aggregate_settlements_by_provider(database,'owner',start,end,row_loader=iter_dashboard_settlement_rows))
    query={'user_id':'owner'}
    if window is not None:query['adjusted_at']=window
    assert database.payment_adjustments.calls[0][0]==query


def test_real_mongo_whole_period_over_50k():
    import os
    import uuid
    from motor.motor_asyncio import AsyncIOMotorClient
    uri=os.environ.get('DASHBOARD_TEST_MONGO_URI')
    if not uri:pytest.skip('isolated local Mongo URI required')
    assert uri in ('mongodb://127.0.0.1:27261','mongodb://localhost:27261')
    async def check():
        client=AsyncIOMotorClient(uri,serverSelectionTimeoutMS=3000)
        database=client['dashboard_settlement_test_'+uuid.uuid4().hex[:16]]
        try:
            for offset in range(0,50129,128):
                await database.payment_adjustments.insert_many([dict(row(i),user_id='owner',adjusted_at='2026-09-15')
                    for i in range(offset,min(offset+128,50129))])
            await database.payment_adjustments.insert_many([
                dict(row(0),user_id='other',adjusted_at='2026-09-15'),
                dict(row(0),user_id='owner',adjusted_at='2026-08-01')])
            result=await aggregate_settlements_by_provider(database,'owner','2026-09-01','2026-09-30',row_loader=iter_dashboard_settlement_rows)
            assert result==expected(50129)
            old=await aggregate_settlements_by_provider(database,'owner','2026-09-01','2026-09-30')
            assert old==expected(50000)
        finally:
            await client.drop_database(database.name)
            client.close()
    run(check())
