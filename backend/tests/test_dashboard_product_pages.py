from dashboard_spill import DashboardSpill
from dashboard_product_pages import finalize_product_pages, page_rows
from dashboard_v2_routes import _finalize_product_profit_rows


def raw(i, **changes):
    return dict(identity=str(i),name='p'+str(i),units_sold=2.005,orders_count=1,
                total_sales=3.335,total_cost=1.2345,mezan_cost_complete=True,
                sales_conversion_complete=True,cost_sources={'mezan_v2_base'},**changes)


def test_full_totals_are_not_page_totals_and_all_pages_complete():
    data={str(i):raw(i) for i in range(201)}
    expected,summary=_finalize_product_profit_rows(data)
    with DashboardSpill() as store:
        grouped=store.map('raw')
        for key,value in data.items(): grouped[key]=value
        rows,result,meta=finalize_product_pages(store,grouped,store.map('missing'),_finalize_product_profit_rows)
        assert len(rows)==50
        assert {key:result[key] for key in summary}==summary
        identities=[]
        cursor=None
        while True:
            page,metadata=page_rows(store,meta['namespace'],'products',cursor=cursor,limit=50)
            identities.extend(row['identity'] for row in page)
            cursor=metadata['next_cursor']
            if cursor is None: break
        assert len(identities)==201
        assert len(set(identities))==201
        assert set(identities)==set(data)


def test_zero_units_and_missing_fx_partial_cost_totals_match():
    data={'0':raw(0),'1':raw(1),'2':raw(2)}
    data['0']['units_sold']=0
    data['1']['sales_conversion_complete']=False
    data['2']['missing_everywhere']=True
    expected,summary=_finalize_product_profit_rows(data)
    with DashboardSpill() as store:
        grouped=store.map('raw')
        for key,value in data.items(): grouped[key]=value
        rows,result,meta=finalize_product_pages(store,grouped,store.map('missing'),_finalize_product_profit_rows)
        assert {key:result[key] for key in summary}==summary
        assert rows==expected

import asyncio
from copy import deepcopy
from dashboard_product_pages import load_product_context
from dashboard_order_reads import dashboard_order_read_scope
from dashboard_v2_routes import (build_mezan_v2_product_cost, PRODUCTS, COST_PROFILES,
                                BINDINGS, PRODUCT_RESOURCE_BINDINGS, RESOURCES,
                                PRODUCT_COST_CATALOG_PROJECTION, _index_products, _line_product)

class Cursor:
    def __init__(self,rows,loads): self.rows=iter(rows); self.loads=loads; self.closed=False
    def batch_size(self,size): assert size==128; return self
    async def to_list(self,length):
        from itertools import islice
        self.loads.append(length)
        return list(islice(self.rows,length))
    def __aiter__(self): return self
    async def __anext__(self):
        try: return next(self.rows)
        except StopIteration: raise StopAsyncIteration
    async def close(self): self.closed=True
class Collection:
    def __init__(self,rows): self.rows=rows; self.loads=[]
    def find(self,query,projection):
        def matches(row):
            return all(row.get(key) in value['$in'] if isinstance(value,dict) and '$in' in value
                       else row.get(key)==value for key,value in query.items())
        return Cursor((deepcopy(row) for row in self.rows if matches(row)),self.loads)
class DB:
    def __init__(self,data): self.collections={key:Collection(rows) for key,rows in data.items()}
    def __getitem__(self,key): return self.collections.setdefault(key,Collection([]))
    def __getattr__(self,key): return self[key]

def test_bounded_catalog_preserves_global_alias_ambiguity_across_evictions():
    async def check():
        products=[dict(user_id='u',salla_product_id=str(i),name='p'+str(i),variants=[dict(id='v'+str(i),sku='s'+str(i))]) for i in range(300)]
        products[0]['name']='duplicate'; products[-1]['name']='duplicate'
        products.append(dict(user_id='u',salla_product_id='one',name='unique',raw_salla_details={'skus':[{'id':'historical','cost_price':12}]}))
        db=DB({PRODUCTS:products})
        legacy=_index_products(products)
        with DashboardSpill() as store:
            context=await load_product_context(db,'u',store,(PRODUCTS,COST_PROFILES,BINDINGS,PRODUCT_RESOURCE_BINDINGS,RESOURCES),PRODUCT_COST_CATALOG_PROJECTION)
            for item in [dict(name='duplicate'),dict(name='unique'),dict(variant_id='historical'),dict(sku='s200')]:
                def resolve(indexes): return _line_product(item,products_by_id=indexes[0],products_by_variant=indexes[1],products_by_sku=indexes[2])
                assert resolve(context)==resolve(legacy)
            assert max(db[PRODUCTS].loads)==128
    asyncio.run(check())

def test_cost_pipeline_full_totals_and_rows_match_legacy_beyond_page():
    async def check():
        products=[dict(user_id='u',salla_product_id=str(i),name='p'+str(i),cost_price_from_salla=12.345,sku='s'+str(i)) for i in range(201)]
        profiles=[dict(user_id='u',salla_product_id=str(i),base_cost=10.123) for i in range(201)]
        options=[dict(user_id='u',salla_product_id=str(i),id='o'+str(i),option_name='Name',value_name='Yes',mode='direct',direct_amount=2.13) for i in range(201)]
        bindings=[dict(user_id='u',salla_product_id=str(i),id='b'+str(i),resource_id='r',quantity=1) for i in range(201)]
        db=DB({PRODUCTS:products,COST_PROFILES:profiles,BINDINGS:options,PRODUCT_RESOURCE_BINDINGS:bindings,RESOURCES:[dict(user_id='u',id='r',unit_cost=1.23)]})
        orders=[dict(order_number=str(i),total_amount=50,order_status='completed',products=[dict(product_id=str(i),quantity=2,price=25,options=[dict(name='Name',value='Yes')])]) for i in range(201)]
        expected=await build_mezan_v2_product_cost(db,'u',orders)
        async with dashboard_order_read_scope(bounded=True):
            actual=await build_mezan_v2_product_cost(db,'u',orders)
        for key,value in expected.items():
            if key=='product_rows':
                assert len(actual[key])==50
                assert {r['identity'] for r in actual[key]}.issubset({r['identity'] for r in value})
            elif key=='product_profit_summary':
                assert {k:actual[key][k] for k in value}==value
            else: assert actual[key]==value
        assert actual['product_pagination']['total']==201
    asyncio.run(check())

def test_details_endpoint_pages_and_rejects_invalid_request(monkeypatch):
    import dashboard_v2_routes as module
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    products=[dict(user_id='u',salla_product_id=str(i),name='p'+str(i),cost_price_from_salla=10) for i in range(60)]
    db=DB({PRODUCTS:products})
    orders=[dict(order_number=str(i),total_amount=50,products=[dict(product_id=str(i),quantity=1,price=50)]) for i in range(60)]
    captured=[]
    async def filtered(db,user_id,**kwargs):
        captured.append((user_id,kwargs))
        return orders
    async def user(): return {'id':'u','role':'owner'}
    async def legacy(**kwargs): raise AssertionError('details must not invoke legacy summary')
    monkeypatch.setattr(module,'_filtered_orders',filtered)
    app=FastAPI()
    app.include_router(module.make_dashboard_v2_router(db,user,legacy,lambda user:None))
    with TestClient(app) as client:
        first=client.get('/dashboard-v2/product-details',params={'limit':50,'from_date':'2026-10-01'})
        assert first.status_code==200,first.text
        payload=first.json()
        assert len(payload['items'])==50
        assert payload['pagination']['total']==60
        assert payload['product_profit_summary']['product_count']==60
        second=client.get('/dashboard-v2/product-details',params={'cursor':payload['pagination']['next_cursor'],'from_date':'2026-10-01'})
        assert second.status_code==200,second.text
        assert len(second.json()['items'])==10
        assert len({r['identity'] for r in payload['items']+second.json()['items']})==60
        assert client.get('/dashboard-v2/product-details?cursor=-1').status_code==400
        assert client.get('/dashboard-v2/product-details?limit=51').status_code==422
        assert client.get('/dashboard-v2/product-details?kind=unknown').status_code==422
    assert captured[0][1]['from_date']=='2026-10-01'
