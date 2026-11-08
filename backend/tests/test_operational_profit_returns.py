from copy import deepcopy
from datetime import datetime, timezone
import pytest
from operational_profit_returns import current_total_is_net

NOW = datetime(2026,11,8,tzinfo=timezone.utc)


def fixture(amount='100', net='200'):
    raw = {'id':'sale', 'currency':'SAR', 'amounts':{'total':{'amount':net,'currency':'SAR'},
        'tax':{'amount':'26.09','currency':'SAR'}}, 'payment_method':'emkan',
        'payment_actions':{'refund_action':{'refunded_transactions':[{'amount':{'amount':amount,'currency':'SAR'}}]}}}
    case = {'id':'return', 'order_id':'sale','currency':'SAR','case_type':'order_compensation',
        'status':'salla_refund_confirmed','manually_confirmed_source':True,
        'refund_source_type':'bank','refund_source_id':'chosen-bank',
        'financial_effect':'confirmed_bank_refund_debit','refund_reference':'bank-proof',
        'created_at':'2026-11-07T12:00:00Z','refunded_at':'2026-11-07T11:00:00Z',
        'amount':amount,'order_total_after_refund_snapshot':net}
    return raw,[case]


@pytest.mark.parametrize('amount,net',[('100','200'),('300','0')])
def test_partial_and_full_already_net_sales_are_not_reduced_again(amount,net):
    raw,cases=fixture(amount,net)
    before=deepcopy((raw,cases))
    for _ in range(4):
        assert current_total_is_net(raw,cases,NOW)
        assert (raw,cases)==before


@pytest.mark.parametrize('key,value',[('status','refunded'),('case_type','item_return'),
    ('manually_confirmed_source',False),('order_total_after_refund_snapshot','250'),
    ('amount','50'),('currency','QAR'),('created_at','2026-11-09T00:00:00Z')])
def test_missing_or_conflicting_net_sale_basis_stays_unknown(key,value):
    raw,cases=fixture();cases[0][key]=value
    assert not current_total_is_net(raw,cases,NOW)


def test_salla_decrease_without_confirmed_source_does_not_choose_bank_or_reduce_twice():
    raw,cases=fixture()
    assert not current_total_is_net(raw,[],NOW)
    assert not current_total_is_net(raw,cases+deepcopy(cases),NOW)


@pytest.mark.parametrize('refund,net,tax,expected',[('100','200','26.09','173.91'),('300','0','0','0.00')])
def test_profit_collector_uses_current_tax_adjusted_sale_once(refund,net,tax,expected):
    from test_operational_profit_period import collect_one
    from test_operational_balance_open_customer_cod import order
    doc=order(status='delivered')
    raw=doc['raw_by_source']['salla_direct']
    example,cases=fixture(refund,net)
    raw['amounts'].update(example['amounts'])
    raw['amounts']['tax']={'amount':tax,'currency':'SAR'}
    raw['payment_actions']=example['payment_actions']
    raw['payment_method']='emkan'
    cases[0].update(order_id=str(raw['id']),created_at='2026-11-01T12:00:00Z',refunded_at='2026-11-01T11:00:00Z')
    result=collect_one(doc,lambda db,state:state.update(customer_returns=cases))
    assert result['by_currency']['SAR']['net_sales']['amount']==expected
