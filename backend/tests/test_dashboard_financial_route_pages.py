import asyncio
from copy import deepcopy
from inspect import unwrap

import pytest
from fastapi import HTTPException

from dashboard_financial_pages import financial_page_request, _pagination
from dashboard_v2_routes import _finalize_financial_payment_page, make_dashboard_v2_router
from dashboard_v2_ad_costs import merge_ad_bank_fees_into_dashboard


def ads(count=1):
    return dict(bank_commissions=dict(total_fee_sar=12.335,fee_subject_spend_sar=100,
        accounts=[dict(external_account_id=str(i),spend_sar=100,bank_commission_fee_sar=.125) for i in range(count)]))

@pytest.mark.parametrize('total,offset,limit',[(0,0,50),(49,0,50),(50,0,50),(50,50,50),(52,50,2),(52,52,2),(52,99,50)])
def test_synthetic_row_is_single_final_position_without_changing_totals(total,offset,limit):
    regular=[dict(key='p'+str(i)) for i in range(total)]
    response=dict(totals=dict(total_payment_fees=15.55,net_profit=100,net_sales=200),
        payment_breakdown=regular[offset:offset+limit],financial_pagination={'payments':_pagination(total,str(offset),limit)})
    merge_ad_bank_fees_into_dashboard(response,ads())
    totals=deepcopy(response['totals'])
    with financial_page_request('payments',str(offset),limit):
        _finalize_financial_payment_page(response)
    all_keys=[row['key'] for row in regular]+['ad_bank_commissions']
    assert [row['key'] for row in response['payment_breakdown']]==all_keys[offset:offset+limit]
    assert response['financial_pagination']['payments']==_pagination(total+1,str(offset),limit)
    assert response['totals']==totals


def test_synthetic_children_page_does_not_change_parent_or_global_totals():
    response=dict(totals=dict(total_payment_fees=15.55,net_profit=100,net_sales=200),
        payment_breakdown=[],financial_pagination={'payments':_pagination(0)})
    merge_ad_bank_fees_into_dashboard(response,ads(73))
    totals=deepcopy(response['totals'])
    with financial_page_request('payment_methods','50',23,'ad_bank_commissions'):
        _finalize_financial_payment_page(response)
    assert len(response['financial_detail'])==23
    assert response['financial_detail'][0]['key']=='ad_bank:50'
    assert response['financial_pagination']['payment_methods']==dict(_pagination(73,'50',23),parent_key='ad_bank_commissions')
    assert response['payment_breakdown'][0]['fee_amount']==12.34
    assert response['totals']==totals


def router():
    async def auth():return {'id':'owner','role':'owner'}
    async def legacy(**kwargs):raise AssertionError('validation must precede reads')
    return make_dashboard_v2_router(None,auth,legacy,lambda user:user)


def test_financial_route_requires_auth_and_has_distinct_page_arguments():
    route=next(r for r in router().routes if r.path.endswith('/financial-details'))
    assert route.dependant.dependencies
    assert set(route.endpoint.__annotations__) >= {'kind','cursor','limit','parent_key','user'}

@pytest.mark.parametrize('kwargs',[dict(kind='bad'),dict(cursor='-1'),dict(cursor='01'),dict(limit=51),dict(kind='payment_methods',parent_key=None)])
def test_invalid_pages_rejected_before_reads(kwargs):
    route=next(r for r in router().routes if r.path.endswith('/financial-details'))
    args=dict(kind='payments',cursor=None,limit=50,parent_key=None,user={'id':'owner','role':'owner'},from_date=None,to_date=None,payment_methods=None,shipping_companies=None)
    args.update(kwargs)
    async def check():
        with pytest.raises(HTTPException) as error:
            await unwrap(route.endpoint)(**args)
        assert error.value.status_code==400
    asyncio.run(check())



def test_coalescing_distinguishes_kind_cursor_and_parent(monkeypatch):
    import inspect
    from dashboard_financial_pages import current_financial_page_request
    original=inspect.unwrap
    calls=[]
    async def check():
        release=asyncio.Event()
        async def summary(**kwargs):
            kind,cursor,limit,parent=current_financial_page_request()
            calls.append((kind,cursor,parent))
            await release.wait()
            return dict(totals={'unchanged':1},payment_breakdown=[],shipping_breakdown=[],
                        financial_detail=[],financial_pagination={kind:_pagination(0,cursor,limit)})
        monkeypatch.setattr(inspect,'unwrap',lambda function,*args,**kwargs:
                            summary if function.__name__=='dashboard_v2' else original(function,*args,**kwargs))
        route=next(r for r in router().routes if r.path.endswith('/financial-details'))
        # Keep the real endpoint coordinator; skip only the independently
        # governed heavy admission wrapper so this is deterministic offline.
        from dashboard_read_coordinator import DashboardReadCoordinator
        coordinated=DashboardReadCoordinator().endpoint(lambda user:user)(original(route.endpoint))
        base=dict(kind='payment_methods',cursor=None,limit=50,parent_key='a',
                  user={'id':'owner','role':'owner'},from_date=None,to_date=None,payment_methods=None,shipping_companies=None)
        variants=[base,dict(base),dict(base,parent_key='b'),dict(base,cursor='50'),dict(base,kind='shipping')]
        tasks=[asyncio.create_task(coordinated(**args)) for args in variants]
        for _ in range(12):await asyncio.sleep(0)
        assert len(calls)==4
        release.set()
        await asyncio.gather(*tasks)
    asyncio.run(check())
