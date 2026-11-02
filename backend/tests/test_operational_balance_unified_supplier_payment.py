"""Combined supplier/payment/return regressions; synthetic read-only input only."""
from copy import deepcopy
from decimal import Decimal
import pytest
from test_operational_balance_sources import DB, order, run, START, NOW
from test_operational_supplier_invoice_cutover import native_data, collect, active, NEW, NOW as SUPPLIER_NOW
from operational_balance_engine import reconcile


@pytest.mark.parametrize('method', ['tamara_installment', 'tabby_installment'])
def test_reduced_order_keeps_original_party_and_compensation_information(method, monkeypatch):
    import operational_customer_returns as returns
    async def read_only_state(db, owner):
        return {'customer_returns': []}
    monkeypatch.setattr(returns, 'read', read_only_state)
    row = order(payment_method=method, total='150', amounts={'total': {'amount':'150','currency':'SAR'}},
        payment={'method':method, 'status':'paid', 'paid_amount':'170',
                 'reference':'synthetic-capture', 'paid_at':'2026-10-06T10:00:00+00:00'},
        payment_actions={'refund_action': {'refunded_transactions':[
            {'amount':{'amount':'20','currency':'SAR'}}]}})
    before=deepcopy(row)
    result=run(returns.order_view(DB({'unified_orders':[row]}), 'owner', '10'))
    assert result['provider']==method.split('_')[0]
    assert result['total']=='150.00'
    assert result['salla_refund_observation']['amount']=='20.00'
    assert result['payment_snapshot'] is None
    assert result['payment_evidence_issues']
    assert row==before


@pytest.mark.parametrize('method', ['tamara_installment', 'tabby_installment'])
def test_missing_capture_does_not_hide_or_double_supplier_invoice(method):
    data=native_data(created=NEW)
    raw=data['unified_orders'][0]['raw_by_source']['salla_direct']
    total=raw.get('total', (raw.get('amounts') or {}).get('total'))
    total=total.get('amount') if isinstance(total,dict) else total
    raw['payment_method']=method
    raw['payment']={'method':method}
    raw['payment_methods']=[{'payment_method':method,'amount':str(total),
                            'transaction_reference':'synthetic-reference'}]
    raw['payment_actions']={'remaining_action':{'paid_amount':{'amount':str(total),'currency':raw['currency']}}}
    sources=collect(data)
    assert any(r['code']=='provider_capture_evidence_incomplete' for r in sources['issues'])
    first=reconcile(active(),sources,SUPPLIER_NOW)
    second=reconcile(first,sources,SUPPLIER_NOW)
    assert first==second
    supplier=[r for r in first['engine']['obligations'].values() if r['kind']=='supplier']
    assert sum(Decimal(r['confirmed']) for r in supplier)==80
    assert sum(Decimal(r['expected']) for r in supplier)==0
    assert not any(k.startswith('capture:') for k in first['engine']['facts'])
