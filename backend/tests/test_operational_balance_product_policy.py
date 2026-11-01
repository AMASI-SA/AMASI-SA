"""Owner acceptance: exact order status, per-piece receipt and SAR COST evidence."""
from copy import deepcopy
from decimal import Decimal
import pytest
from operational_product_cost_policy import (
    COST_CURRENCY, order_unreceived_stage, unreceived_stage, require_sar_cost,
)
from operational_balance_sources import cost_components, append_components
from test_operational_open_cost_exposure import (
    seeded_db, native_order, piece, preview, operating_state, group, codes, NOW,
)
from test_operational_balance_sources import row

ELIGIBLE = ('under_review', 'waiting_review', 'pending_review', 'awaiting_review',
            'انتظار المراجعة', 'بانتظار المراجعة', 'بإنتظار المراجعة',
            'in_progress', 'قيد التنفيذ', 'جاري التنفيذ')
EXCLUDED = ('reviewed', 'تم المراجعة', 'تمت المراجعة', 'completed', 'تم التنفيذ',
            'shipped', 'تم الشحن', 'delivered', 'تم التوصيل', 'cancelled',
            'refunded', 'pending', 'processing', 'unknown', '', None)


@pytest.mark.parametrize('status', ELIGIBLE)
def test_exact_allowed_statuses(status):
    assert unreceived_stage(status) in {'awaiting_review', 'under_fulfillment'}


@pytest.mark.parametrize('status', EXCLUDED)
def test_no_other_state_is_unreceived_work(status):
    assert unreceived_stage(status) is None
    result = preview(seeded_db(order_row=native_order(status=status)))
    assert not group(result)


def test_customized_or_conflicting_status_must_not_inherit_eligible_parent():
    assert unreceived_stage({'slug':'under_review', 'name':'بانتظار المراجعة'}) == 'awaiting_review'
    assert unreceived_stage({'slug':'under_review', 'name':'تمت المراجعة'}) is None
    assert unreceived_stage({'slug':'under_review', 'customized':{'name':'تم المراجعة'}}) is None
    assert order_unreceived_stage({'status':'in_progress', 'status_slug':'completed'}) is None


@pytest.mark.parametrize('status', ['in_progress', 'completed', 'delivered', 'cancelled', 'reviewed'])
@pytest.mark.parametrize('new', [False, True])
def test_partial_receipt_keeps_only_eligible_unreceived_remainder(status, new):
    kwargs = {'created': '2026-11-01T01:00:00+00:00'} if new else {}
    db = seeded_db(order_row=native_order(qty=5, status=status, **kwargs), physical=piece(received=True))
    db.data['mezan_preparation_pieces_v1'] = [
        dict(piece(received=i < 2), id='piece'+str(i), unit_index=i+1) for i in range(5)]
    result = preview(db)
    prefix = 'poststart' if new else 'prestart'
    amounts = result['currencies']['SAR']
    assert amounts[prefix+'_supplier_received_uninvoiced'] == '80.00'
    assert amounts.get(prefix+'_supplier_unreceived', '0.00') == ('120.00' if status == 'in_progress' else '0.00')
    rows = group(result, period=prefix)
    received = [r for r in rows if r['stage']=='received_without_approved_invoice']
    assert len(received)==1 and received[0]['party_id']=='supplier1' and received[0]['units']==2
    assert all(r['party_id']=='' for r in rows if r not in received)


@pytest.mark.parametrize('currency', ['SAR', 'QAR', 'KWD', 'AED', 'BHD', 'OMR', 'USD'])
@pytest.mark.parametrize('new', [False, True])
def test_cost_uses_native_sar_for_all_payment_currencies_without_fx(currency, new):
    kwargs = {'created':'2026-11-01T01:00:00+00:00'} if new else {}
    source = native_order(qty=1, **kwargs)
    raw = source['raw_by_source']['salla_direct']
    raw['currency']=currency
    raw['amounts']['total']={'amount':'987.65','currency':currency}
    db=seeded_db(order_row=source)
    before=deepcopy(db.data)
    result=preview(db)
    prefix='poststart' if new else 'prestart'
    assert result['currencies']['SAR'][prefix+'_supplier_unreceived']=='40.00'
    assert all(r['currency']=='SAR' for r in group(result,period=prefix))
    assert 'historical_cost_currency_requires_review' not in codes(result)
    assert db.data==before
    assert result['product_cost_currency']=='SAR'


def test_quote_recomputes_sar_cost_instead_of_relabelling_saved_kwd_amount():
    source=native_order(number='NEW',created='2026-11-01T01:00:00+00:00')
    source['raw_by_source']['salla_direct']['currency']='KWD'
    db=seeded_db(order_row=source,physical=piece(number='NEW'))
    old={'id':'supplier:salla-NEW:physical1','kind':'supplier','currency':'KWD',
         'expected':'9999.00','confirmed':'0','party_id':None}
    state=operating_state({old['id']:old}); before=deepcopy(state)
    result=preview(db,state)
    assert result['currencies']['SAR']['poststart_supplier_unreceived']=='40.00'
    assert 'KWD' not in result['currencies']
    assert state==before


def test_foreign_cost_record_is_incomplete_not_relabelled_or_converted():
    db=seeded_db()
    db.data['mezan_product_cost_profiles_v2'][0]['currency']='KWD'
    result=preview(db)
    assert not group(result)
    assert result['component_complete']['unreceived_products'] is False
    with pytest.raises(ValueError, match='currency_evidence'):
        require_sar_cost({'amount':'50','currency':'KWD'})


def test_missing_shipping_does_not_poison_known_products_or_empty_receiving():
    result=preview(seeded_db())
    assert 'historical_shipping_unassigned' in codes(result)
    assert result['component_complete']=={
        'unreceived_products':True,'received_products':True,'shipping':False}
    assert result['currencies']['SAR']['prestart_supplier_unreceived']=='40.00'


def test_missing_product_cost_does_not_poison_known_shipping():
    from test_operational_open_cost_exposure import shipping_setup
    db=seeded_db(order_row=native_order(shipping={'company_code':'native'}),setup=[shipping_setup()])
    db.data['mezan_product_cost_profiles_v2']=[]
    result=preview(db)
    assert result['component_complete']['shipping'] is True
    assert result['component_complete']['unreceived_products'] is False
    assert result['currencies']['SAR']['prestart_shipping_expected']=='17.25'


def test_invoice_covered_piece_never_returns_to_unreceived_when_receiving_status_changes():
    invoice=row(id='approved',supplier_id='supplier1',approved_at='2026-11-01T09:00:00+00:00',
        lines=[{'piece_ids':['physical1'],'product_price_authority':'mezan_v2',
                'product_unit_price_halalas':2000}])
    result=preview(seeded_db(invoices=[invoice]))
    assert not group(result)
    assert 'prestart_order_postcutover_invoice_needs_reconciliation' in codes(result)


def test_no_reads_can_mutate_finance_or_openings():
    state=operating_state();state['openings']=[{'id':'opening','amount':'12345','currency':'SAR'}]
    state['movements']=[{'id':'paid','amount':'200'}]
    before=deepcopy(state);db=seeded_db();db_before=deepcopy(db.data)
    first=preview(db,state);second=preview(db,state)
    assert first==second and state==before and db.data==db_before


@pytest.mark.parametrize('status', ['in_progress','under_review','reviewed','completed','delivered','cancelled'])
def test_engine_keeps_received_supplier_cost_and_filters_only_unreceived(status):
    from operational_balance_engine import reconcile
    from test_operational_balance_engine import initial, order, NOW as ENGINE_NOW
    items=[{'id':'received','cost':'50','quantity':'2','supplier_id':'s1','supplier_receipt_proven':True},
           {'id':'remaining','cost':'50','quantity':'3','supplier_id':'s1','supplier_receipt_proven':False}]
    sources={'orders':[order(status=status,currency='KWD',items=items,carrier=None)]}
    before=deepcopy(sources);state=initial();state['openings']=[{'amount':'999','currency':'SAR'}]
    out=reconcile(state,sources,ENGINE_NOW)
    obs=out['engine']['obligations']
    assert obs['supplier:o1:received']['expected']=='100.00'
    assert obs['supplier:o1:received']['party_id']=='s1'
    assert obs['supplier:o1:received']['currency']=='SAR'
    assert obs['supplier:o1:remaining']['expected']==('150.00' if status in ('in_progress','under_review') else '0.00')
    assert obs['supplier:o1:remaining']['party_id'] is None
    assert sources==before and out['openings']==state['openings']
    assert reconcile(out,sources,ENGINE_NOW)==out


def test_engine_full_piece_invoice_at_lower_price_does_not_leave_unreceived_difference():
    from operational_balance_engine import reconcile
    from test_operational_balance_engine import initial, order, NOW as ENGINE_NOW
    item={'id':'p','cost':'80','quantity':'1','supplier_id':'s1','supplier_receipt_proven':False}
    rec={'id':'r','order_id':'o1','item_id':'p','supplier_id':'s1','amount':'50','accepted_at':ENGINE_NOW}
    out=reconcile(initial(),{'orders':[order(currency='QAR',items=[item])],'supplier_receipts':[rec]},ENGINE_NOW)
    assert out['engine']['obligations']['supplier:o1:p']['expected']=='0.00'
    confirmed=out['engine']['obligations']['supplier:o1:p:receipt:r']
    assert confirmed['confirmed']=='50.00' and confirmed['currency']=='SAR'


def test_engine_valid_native_invoice_survives_missing_cost_profile_or_quote():
    from operational_balance_engine import reconcile
    from test_operational_balance_engine import initial, order, NOW as ENGINE_NOW
    rec={'id':'r','order_id':'o1','item_id':'p','supplier_id':'s1','amount':'50','accepted_at':ENGINE_NOW}
    out=reconcile(initial(),{'orders':[order(items=[],status='completed')],'supplier_receipts':[rec]},ENGINE_NOW)
    assert out['engine']['obligations']['supplier:o1:p:receipt:r']['confirmed']=='50.00'
    assert out['engine']['obligations']['supplier:o1:p']['expected']=='0.00'


def test_engine_never_rewrites_confirmed_foreign_liability_or_settlement_silently():
    from operational_balance_engine import reconcile
    from test_operational_balance_engine import initial, order, NOW as ENGINE_NOW
    item={'id':'p','cost':'80','quantity':'1','supplier_id':'s1'}
    rec={'id':'r','order_id':'o1','item_id':'p','supplier_id':'s1','amount':'50','accepted_at':ENGINE_NOW}
    sources={'orders':[order(currency='KWD',items=[item])],'supplier_receipts':[rec]}
    state=reconcile(initial(),sources,ENGINE_NOW)
    key='supplier:o1:p:receipt:r';state['engine']['obligations'][key]['currency']='KWD'
    state['movements']=[{'id':'m','currency':'KWD','amount':'20',
                         'allocations':[{'obligation_id':key,'amount':'20'}]}]
    before=deepcopy(state);out=reconcile(state,sources,ENGINE_NOW)
    assert out['engine']['obligations'][key]==before['engine']['obligations'][key]
    assert out['movements']==before['movements']
    assert any(x['code']=='supplier_cost_currency_reconciliation_required' for x in out['engine']['issues'])
