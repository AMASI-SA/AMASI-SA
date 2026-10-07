from dashboard_spill import DashboardSpill, SpillSequence
from dashboard_v2_ad_costs import apply_cost_settings_to_fact_rows
from dashboard_v2_ads_executive import build_salla_ads_executive_breakdown


def test_output_sink_preserves_whole_account_rounding_and_all_rows():
    rows={'meta':[dict(ad_account_id='a',currency='USD',spend_native=0.001337,date='2026-10-01',provider='meta_ads',metrics={'impressions':1}) for _ in range(5000)]}
    accounts=[dict(provider='meta_ads',external_account_id='a',mezan_integration_account_id='internal',currency='USD')]
    settings=[dict(provider='meta_ads',external_account_id='a',exchange_rate_to_sar=3.754321,bank_commission_pct=2.335,apply_bank_commission=True,native_currency='USD')]
    expected=apply_cost_settings_to_fact_rows(rows,accounts,settings)
    with DashboardSpill() as store:
        actual=apply_cost_settings_to_fact_rows(rows,accounts,settings,output_rows_factory=lambda provider:store.sequence(provider))
        assert isinstance(actual['platform_rows']['meta'],SpillSequence)
        assert len(actual['platform_rows']['meta'])==5000
        assert list(actual['platform_rows']['meta'])==expected['platform_rows']['meta']
        assert {k:v for k,v in actual.items() if k!='platform_rows'}=={k:v for k,v in expected.items() if k!='platform_rows'}


def test_missing_fx_count_keeps_only_first100_identifiers():
    orders=[dict(order_number=str(i),currency='USD',total_amount=10) for i in range(501)]
    result=build_salla_ads_executive_breakdown(orders,{})
    assert result['coverage']['unverified_currency_orders']==501
    assert result['coverage']['unverified_currency_order_numbers']==[str(i) for i in range(100)]
    assert result['coverage']['sales_currency_conversion_complete'] is False

def test_complete_ads_response_matches_with_bounded_fact_reads(monkeypatch):
    import asyncio
    from copy import deepcopy
    from itertools import islice
    import dashboard_v2_routes as routes
    from dashboard_v2_ad_costs import ACCOUNT_COLLECTION, COST_SETTINGS_COLLECTION
    from dashboard_order_reads import dashboard_order_read_scope
    class Cursor:
        def __init__(self,rows,loads): self.rows=iter(rows); self.loads=loads
        def batch_size(self,size): assert size==128; return self
        async def to_list(self,length): self.loads.append(length); return list(islice(self.rows,length))
        async def close(self): pass
    class Collection:
        def __init__(self,rows): self.rows=rows; self.loads=[]
        def find(self,query,projection):
            def match(row):
                for key,value in query.items():
                    actual=row.get(key)
                    if isinstance(value,dict):
                        if '$in' in value and actual not in value['$in']: return False
                        if '$gte' in value and actual<value['$gte']: return False
                        if '$lte' in value and actual>value['$lte']: return False
                    elif actual!=value: return False
                return True
            return Cursor((deepcopy(row) for row in self.rows if match(row)),self.loads)
    class DB:
        def __init__(self,data): self.data={key:Collection(value) for key,value in data.items()}
        def __getitem__(self,key): return self.data.setdefault(key,Collection([]))
        def __getattr__(self,key): return self[key]
    async def snapchat(*args,**kwargs):
        return dict(rows=[],total_sar=0,daily_sar={'2026-10-01':0},quality=dict(amount_complete=True),bank_commissions={})
    monkeypatch.setattr(routes,'load_unified_marketing_dashboard_spend',snapchat)
    async def check():
        account=dict(user_id='u',provider='meta_ads',connection_provenance='api_connection',connection_status='connected',mezan_selected=True,ad_account_id='a',external_account_id='a',currency='USD')
        facts=[dict(user_id='u',provider='meta_ads',ad_account_id='a',currency='USD',spend_native=0.001337,date='2026-10-01',purchases=0.125,impressions=1,clicks=1) for _ in range(2001)]
        google=[dict(user_id='u',date='2026-10-01',google_ads=0.001337) for _ in range(2001)]
        db=DB({ACCOUNT_COLLECTION:[account],routes.META_FACTS:facts,'daily_costs':google,COST_SETTINGS_COLLECTION:[dict(user_id='u',provider='meta_ads',external_account_id='a',native_currency='USD',exchange_rate_to_sar=3.754321,bank_commission_pct=2.335,apply_bank_commission=True)]})
        expected=await routes.build_mezan_v2_ads(db,'u',from_date='2026-10-01',to_date='2026-10-01')
        db[routes.META_FACTS].loads.clear(); db['daily_costs'].loads.clear()
        async with dashboard_order_read_scope(bounded=True):
            actual=await routes.build_mezan_v2_ads(db,'u',from_date='2026-10-01',to_date='2026-10-01')
        assert actual==expected
        assert max(db[routes.META_FACTS].loads)==128
        assert max(db['daily_costs'].loads)==128
    asyncio.run(check())
