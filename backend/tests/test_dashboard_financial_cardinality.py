"""Dashboard label cardinality: full reductions, bounded response pages."""
import asyncio
from copy import deepcopy
from itertools import islice

import pytest

from dashboard_order_accumulator import DashboardOrderAccumulator
from dashboard_order_reads import dashboard_order_read_scope, dashboard_spill
from dashboard_financial_pages import FinancialRows, financial_response_pages, financial_page_request, rollup_payments
from payment_methods import normalize_payment_method, PARENT_LABELS

SETTINGS = [dict(name='مدى',commission_percent=2.15,fixed_fee=.123,vat_percent=15),
            dict(name='mada',commission_percent=3.19,fixed_fee=.005,vat_percent=15)]


def reduce(rows):
    accumulator = DashboardOrderAccumulator(SETTINGS, [], {})
    for row in rows:accumulator.observe(row)
    accumulator.begin_fee_pass()
    iterator=iter(rows)
    while batch:=list(islice(iterator,128)):accumulator.observe_fee_batch(batch)
    return accumulator,accumulator.finish()


def plain(result):
    result=dict(result)
    result['parsed']=dict(result['parsed'])
    for key in ('payment_methods','shipping_companies','order_sources'):
        result['parsed'][key]=[dict(row) for row in result['parsed'][key]]
    result['matched']=dict(result['matched'])
    for key in ('payment_breakdown','shipping_breakdown'):
        result['matched'][key]=[dict(row) for row in result['matched'][key]]
    result['shipping']=dict(result['shipping'])
    result['shipping']['per_company']=dict(result['shipping']['per_company'])
    return result


@pytest.mark.parametrize('count',[7,131])
def test_bounded_accumulator_exact_alias_rounding_and_stable_sort(count):
    rows=[dict(order_number=str(i),payment_method=['مدى','mada','unknown-'+str(i)][i%3],
               shipping_company='carrier-'+str(i%9),data_source='source-'+str(i%4),
               total_amount=[-.005,.005,12.335,100.995][i%4],shipping_cost=2.675) for i in range(count)]
    _,expected=reduce(rows)
    async def check():
        async with dashboard_order_read_scope(bounded=True):
            _,actual=reduce(rows)
            assert plain(actual)==expected
    asyncio.run(check())


def test_many_financial_labels_remain_spilled_and_all_pages_keep_totals():
    count=1027
    rows=[dict(order_number=str(i),payment_method='customlabel'+str(i),shipping_company='carrier'+str(i),
               data_source='source'+str(i),total_amount=(i%7)+.005,shipping_cost=1.335) for i in range(count)]
    _,expected=reduce(rows)
    async def check():
        async with dashboard_order_read_scope(bounded=True):
            state,result=reduce(rows)
            assert plain(result)==expected
            for mapping in (state.payments,state.shippings,state.sources,state.fees,state.shipping['per_company']):
                assert mapping.cache_entries<=128
            payments=rollup_payments(dashboard_spill(),result['matched']['payment_breakdown'],normalize_payment_method,PARENT_LABELS)
            sources=FinancialRows(dashboard_spill(),(dict(name='source'+str(i),count=1) for i in range(count)))
            months=FinancialRows(dashboard_spill(),(dict(month=str(i),sales=1) for i in range(count)),text=lambda row:row['month'])
            shipping=FinancialRows(dashboard_spill(),result['shipping']['per_company'].values())
            pages,metadata=financial_response_pages(payments,shipping,sources,months)
            for kind in ('payments','shipping','sources','months'):
                assert len(pages[kind])==50
                assert metadata[kind]['total']==count
                assert metadata[kind]['has_more']
            collected=[];cursor=None
            while True:
                with financial_page_request('shipping',cursor=cursor,limit=37):
                    page,meta=financial_response_pages(payments,shipping,sources,months)
                collected.extend(page['shipping'])
                cursor=meta['shipping']['next_cursor']
                if cursor is None:break
            assert len(collected)==count
            assert sum(row['orders_count'] for row in collected)==count
            assert [row['name'] for row in collected]==list(expected['shipping']['per_company'])
    asyncio.run(check())


def test_disk_ordered_rows_persist_currency_and_refund_mutations():
    async def check():
        async with dashboard_order_read_scope(bounded=True):
            rows=FinancialRows(dashboard_spill(),(dict(name=str(i),sales=i%5) for i in range(257)),
                               number=lambda row:row['sales'],descending=True)
            expected=sorted([dict(name=str(i),sales=i%5) for i in range(257)],key=lambda row:-row['sales'])
            for row in rows:
                row['known_sales']=row['sales'];row['sales']=None;row['refund']=.005
            actual=list(rows)
            assert [r['name'] for r in actual]==[r['name'] for r in expected]
            assert all(row['sales'] is None and row['refund']==.005 for row in actual)
    asyncio.run(check())


@pytest.mark.parametrize('kind,cursor,limit',[('bad',None,50),('payments','-1',50),('payments','01',50),('shipping',None,51)])
def test_bad_pagination_rejected(kind,cursor,limit):
    with pytest.raises(ValueError):
        with financial_page_request(kind,cursor,limit):pass


def test_nested_page_parent_outside_first_page():
    async def check():
        async with dashboard_order_read_scope(bounded=True):
            store=dashboard_spill()
            rows=FinancialRows(store,(dict(key=str(i),sub_methods=[dict(key=str(i),total_sales=i)]) for i in range(75)))
            empty=FinancialRows(store)
            with financial_page_request('payment_methods',parent_key='74',limit=1):
                pages,meta=financial_response_pages(rows,empty,empty,empty)
            assert pages['payment_methods']==[dict(key='74',total_sales=74)]
            assert meta['payment_methods']['total']==1
    asyncio.run(check())

@pytest.mark.parametrize('filters',[{}, {'payment_methods':'mada'}, {'shipping_companies':'smsa'}])
def test_actual_dashboard_small_cohort_full_response_parity_except_additive_metadata(filters):
    import os
    import uuid
    from motor.motor_asyncio import AsyncIOMotorClient
    from dashboard_summary_fixture import BASE_SHA,extract_dashboard,seed_dashboard
    uri=os.environ.get('DASHBOARD_TEST_MONGO_URI')
    if not uri:pytest.skip('isolated local Mongo URI required')
    assert uri=='mongodb://127.0.0.1:27261'
    async def check():
        client=AsyncIOMotorClient(uri,serverSelectionTimeoutMS=3000)
        database=client['dashboard_financial_labels_'+uuid.uuid4().hex[:16]]
        try:
            await seed_dashboard(database,count=265)
            args=dict(user={'id':'owner'},from_date='2026-09-01',to_date='2026-09-30',
                      include_legacy_analyses=False,allow_self_heal=False,**filters)
            expected=await extract_dashboard(database,revision=BASE_SHA)(**args)
            current=extract_dashboard(database)
            assert await current(**args)==expected  # Unscoped default path unchanged.
            async with dashboard_order_read_scope(bounded=True):
                actual=await current(**args)
            metadata=actual.pop('financial_pagination')
            assert set(metadata)=={'payments','shipping','sources','months'}
            for row in actual['payment_breakdown']:
                child_metadata=row.pop('sub_methods_pagination')
                assert child_metadata['total']==len(row['sub_methods'])
                assert not child_metadata['has_more']
            assert actual==expected
        finally:
            await client.drop_database(database.name)
            client.close()
    asyncio.run(check())


def test_actual_dashboard_many_labels_pages_without_changing_full_totals():
    import os
    import uuid
    from motor.motor_asyncio import AsyncIOMotorClient
    from pymongo import UpdateOne
    from dashboard_summary_fixture import BASE_SHA,extract_dashboard,seed_dashboard
    uri=os.environ.get('DASHBOARD_TEST_MONGO_URI')
    if not uri:pytest.skip('isolated local Mongo URI required')
    assert uri=='mongodb://127.0.0.1:27261'
    async def check():
        client=AsyncIOMotorClient(uri,serverSelectionTimeoutMS=3000)
        database=client['dashboard_financial_many_'+uuid.uuid4().hex[:16]]
        try:
            await seed_dashboard(database,count=131)
            for offset in range(0,131,128):
                await database.unified_orders.bulk_write([UpdateOne({'user_id':'owner','order_number':str(i)},
                    {'$set':{'payment_method':'customlabel'+str(i),'shipping_company':'carrier'+str(i),
                             'data_source':'source'+str(i),'order_status':'completed','currency':'SAR','order_date_inferred':False}})
                    for i in range(offset,min(offset+128,131))])
            args=dict(user={'id':'owner'},from_date='2026-09-01',to_date='2026-09-30',
                      include_legacy_analyses=False,allow_self_heal=False)
            expected=await extract_dashboard(database,revision=BASE_SHA)(**args)
            async with dashboard_order_read_scope(bounded=True):
                actual=await extract_dashboard(database)(**args)
            metadata=actual.pop('financial_pagination')
            for row in actual['payment_breakdown']:row.pop('sub_methods_pagination')
            for kind,field in [('payments','payment_breakdown'),('shipping','shipping_breakdown'),('sources','source_breakdown'),('months','monthly')]:
                assert metadata[kind]['total']==len(expected[field])
                assert actual[field]==expected[field][:50]
                expected[field]=expected[field][:50]
            assert metadata['shipping']['total']==131
            assert metadata['payments']['total']==131
            assert actual==expected  # Every full-period total remains unchanged.
        finally:
            await client.drop_database(database.name)
            client.close()
    asyncio.run(check())
