"""Tariff and statement values are distinct, comparable only on identical scope."""
from copy import deepcopy
from decimal import Decimal
import pytest

from operational_provider_summary import provider_balances
from operational_financial_position_core import _provider_table


def snapshot(*, applied='0', actual='0', tariff='10', opening='50', refund='0', currency='SAR', valuation=None):
    claim={'id':'provider:order','kind':'provider','party_type':'provider','party_id':'tamara',
        'currency':currency,'confirmed':str(Decimal('100')-Decimal(refund)),'expected':'0','direction':'receivable'}
    state={'engine':{'facts':{},'provider_fee_inputs':{}},'movements':[]}
    if refund!='0':
        state['engine']['facts']['refund:tamara:refund']={'order_id':'order','amount':refund}
    if applied!='0':
        state['movements'].append({'party_type':'provider','party_id':'tamara','kind':'settlement',
            'direction':'incoming','currency':currency,'amount':str(Decimal(applied)-Decimal(actual)),
            'actual_fee_amount':actual,'allocations':[{'obligation_id':claim['id'],'amount':applied}]})
    details={'provider_reports':{} if tariff is None else {claim['id']:{'gross':'100','estimated_fees_original':tariff}}}
    parties={'provider':{'party_type':'provider','party_id':'tamara','currency':currency,'opening':Decimal(opening),
        'outstanding_receivable':Decimal(opening)+Decimal(claim['confirmed'])-Decimal(applied), 'outstanding_payable':Decimal(0)}}
    valuations={}
    if valuation:
        claim['native_balance']={'currency':currency,'confirmed':claim['confirmed']}
        claim['currency']='SAR'; parties['provider']['currency']='SAR'
        valuations[claim['id']]=valuation
        details['provider_reports'][claim['id']]['gross']=valuation['sar_paid']
    return state,details,[claim],parties,valuations


def project(args):
    before=deepcopy(args[:3])
    result,_=provider_balances(*args)
    assert args[:3]==before
    return next(row for values in result.values() for row in values)


def test_unsettled_tariff_net_excludes_opening_and_payout_and_actual_is_unknown():
    row=project(snapshot())
    assert (row['tariff_fees'],row['tariff_net'])==('10.00','90.00')
    assert row['net']=='140.00' and row['fees']=='10.00'
    assert row['actual_settlement_fees'] is None and row['known_actual_settlement_fees'] is None
    assert row['fee_variance'] is None


def test_fully_settled_same_scope_compares_actual_minus_tariff():
    row=project(snapshot(applied='100',actual='12'))
    assert row['tariff_fees']=='10.00' and row['tariff_net']=='90.00'
    assert row['actual_settlement_fees']=='12.00' and row['fee_variance']=='2.00'
    assert row['net']=='50.00' and row['fees']=='12.00'
    assert row['fee_comparison_basis']=='fully_settled_same_paid_order_scope'


def test_partial_settlement_does_not_compare_full_tariff_to_partial_actual():
    row=project(snapshot(applied='50',actual='6'))
    assert row['tariff_fees']=='10.00' and row['actual_settlement_fees']=='6.00'
    assert row['fees']=='11.00' and row['net']=='95.00'
    assert row['fee_variance'] is None


def test_missing_tariff_after_full_settlement_is_not_fabricated_zero():
    row=project(snapshot(applied='100',actual='12',tariff=None))
    assert row['complete'] and row['fees']=='12.00' and row['net']=='50.00'
    assert row['tariff_fees'] is None and row['tariff_net'] is None and row['fee_variance'] is None
    assert row['actual_settlement_fees']=='12.00'


@pytest.mark.parametrize('explicit', [False,True])
def test_zero_actual_fee_requires_an_explicit_recorded_field(explicit):
    args=snapshot(applied='100',actual='0')
    if not explicit: args[0]['movements'][0].pop('actual_fee_amount')
    row=project(args)
    assert row['actual_settlement_fees']==('0.00' if explicit else None)
    assert row['fee_variance']==('-10.00' if explicit else None)


def test_missing_fee_in_one_settlement_preserves_only_known_subtotal():
    args=snapshot(applied='50',actual='6')
    second=deepcopy(args[0]['movements'][0]);second.pop('actual_fee_amount');second['amount']='50'
    args[0]['movements'].append(second)
    row=project(args)
    assert row['actual_settlement_fees'] is None and not row['actual_settlement_fees_complete']
    assert row['known_actual_settlement_fees']=='6.00' and row['fee_variance'] is None


@pytest.mark.parametrize('case', ['refund','outgoing','unallocated','opening_allocation'])
def test_nonmatching_scope_never_reports_fee_variance(case):
    args=snapshot(applied='100',actual='12',refund='20' if case=='refund' else '0')
    if case=='outgoing':
        outgoing=deepcopy(args[0]['movements'][0]);outgoing.update(direction='outgoing',amount='10',actual_fee_amount='0')
        outgoing['allocations']=[{'obligation_id':'provider:order','amount':'10'}]
        args[0]['movements'].append(outgoing)
    elif case=='unallocated': args[0]['movements'][0]['amount']='90'
    elif case=='opening_allocation': args[0]['movements'][0]['allocations'][0]['obligation_id']='opening:tamara'
    row=project(args)
    assert row['fee_variance'] is None and row['fee_comparison_basis']=='not_comparable'
    if case=='refund': assert row['tariff_net']=='70.00'


def test_foreign_fee_proof_uses_saved_conversion_not_currency_relabeling():
    args=snapshot(applied='100',actual='10',currency='QAR',tariff='10.30',
        valuation={'native_paid':'100','sar_paid':'103'})
    row=project(args)
    assert row['tariff_net']=='92.70' and row['actual_settlement_fees']=='10.30' and row['fee_variance']=='0.00'
    native=project(snapshot(applied='100',actual='10',currency='QAR'))
    assert native['tariff_net']=='90.00' and native['actual_settlement_fees']=='10.00'


def test_columns_and_totals_keep_tariff_and_statement_values_separate():
    row=project(snapshot(applied='50',actual='6'))
    table=_provider_table([row],[])
    assert table['totals']['tariff_fees']=='10.00'
    assert table['totals']['actual_settlement_fees']=='6.00'
    assert table['totals']['fee_variance'] is None
    assert {'tariff_fees','tariff_net','actual_settlement_fees','fee_variance'} <= {c['key'] for c in table['columns']}


def test_unallocated_foreign_fee_remainder_is_not_a_complete_sar_actual_fee():
    args=snapshot(applied='100',actual='12',currency='QAR',tariff='10.30',
        valuation={'native_paid':'100','sar_paid':'103'})
    args[0]['movements'][0]['amount']='90'
    row=project(args)
    assert row['actual_settlement_fees'] is None
    assert row['known_actual_settlement_fees'] is not None
    assert row['fee_variance'] is None
