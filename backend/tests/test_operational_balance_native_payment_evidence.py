"""Synthetic native/canonical payment evidence contracts; no live API or writes."""
from copy import deepcopy
from decimal import Decimal
import pytest
from operational_payment_evidence import normalize_payment, provider_for, PAYMENT_METHODS
from operational_balance_engine import reconcile
from test_operational_balance_sources import DB, row, order, run, START, NOW
import operational_balance_sources as src

CREATED = '2026-10-06T01:00:00+00:00'
PAID = '2026-10-06T01:02:00+00:00'


def native(method='tamara_installment', **changes):
    data = {'payment_method': method, 'status': {'slug': 'under_review'},
            'payment_methods': [{'payment_method': method, 'amount': '300.00', 'transaction_reference': 'synthetic-ref'}],
            'payment_actions': {'refund_action': {'paid_amount': {'amount': '300.00', 'currency': 'SAR'},
                'refund_amount': {'amount': 0, 'currency': 'SAR'}, 'refunded_transactions': []},
                'remaining_action': {'paid_amount': {'amount': '300.00', 'currency': 'SAR'}, 'remaining_amount': None}},
            'is_pending_payment': False}
    data.update(changes)
    return data


def normalize(raw, total='300.00', currency='SAR'):
    before=deepcopy(raw)
    result=normalize_payment(raw,currency,total,CREATED,NOW)
    assert raw==before
    return result


def policy(provider='tamara'):
    return {'id':'test-policy','provider':provider,'currency':'SAR','status':'active',
            'effective_from':'2026-01-01','percentage':'2','fixed_amount':'0',
            'vat_treatment':'exempt','refund_fee_treatment':'retain','cancellation_fee_treatment':'retain'}


def source(raw, **extra):
    db=DB({'unified_orders':[order(**raw)],'mezan_product_cost_profiles_v2':[row(salla_product_id='p1',base_cost='20')],
           'mz2_provider_fee_policies_v2':[row(policies=[policy(provider_for(raw.get('payment_method')) or raw.get('payment',{}).get('method'))])],**extra})
    before=deepcopy(db.data)
    value=run(src.collect_sources(db,'owner',START,NOW))
    assert db.data==before and set(db.reads)<=src.ALLOWED_COLLECTIONS
    return value


def state():
    return {'owner_id':'owner','status':'active','started_at':START,'openings':{},'movements':[]}


@pytest.mark.parametrize('method,provider', [('tamara_installment','tamara'),('tabby_installment','tabby'),
    ('credit_card','salla'),('debit_card','salla'),('mada','salla'),('apple_pay','salla'),('stcpay','salla'),
    ('google_pay','salla'),('تمارا','tamara'),('تابي','tabby'),('مدى','salla'),(' TAMARA_INSTALLMENT ','tamara')])
def test_native_alias_is_known_but_observation_is_not_a_dated_capture(method,provider):
    p,issues=normalize(native(method))
    assert p['provider']==provider and p['gross']=='300.00' and 'captures' not in p
    assert p['paid_observation']=={'amount':'300.00','source':'canonical_salla_order','is_capture':False}
    assert issues[0]['code']=='provider_capture_evidence_incomplete'
    assert issues[0]['missing_fields']==['capture_status','capture_timestamp']


@pytest.mark.parametrize('method', ['tamara_installment','tabby_installment','credit_card'])
def test_real_native_page_amount_reaches_confirmed_balance_without_invented_transaction(method):
    s=source(native(method));assert s['orders'][0]['payment']['provider']==provider_for(method)
    assert not any(i['code']=='provider_capture_evidence_incomplete' for i in s['issues'])
    assert s['orders'][0]['payment']['order_page_balance']['paid']=='300.00'
    result=reconcile(state(),s,NOW)
    obligation=result['engine']['obligations']['provider:100']
    assert obligation['expected']=='0.00' and obligation['confirmed']=='300.00'
    assert not any(k.startswith('capture:') for k in result['engine']['facts'])
    assert result['openings']=={} and result['movements']==[]
    assert reconcile(result,s,NOW)['engine']['obligations']==result['engine']['obligations']


@pytest.mark.parametrize('method', ['tamara_installment','tabby_installment','credit_card'])
def test_complete_capture_is_recognized_while_awaiting_review_once(method):
    raw=native(method,payment={'method':method,'status':'paid','paid_at':PAID})
    p,issues=normalize(raw);assert not issues
    assert p['captures']==[{'id':'synthetic-ref','amount':'300.00','captured_at':PAID}]
    s=source(raw);result=reconcile(state(),s,NOW);again=reconcile(result,s,NOW)
    assert again['engine']['obligations']['provider:100']['confirmed']=='300.00'
    assert len([k for k in again['engine']['facts'] if k.startswith('capture:')])==1
    assert again['openings']=={} and again['movements']==[]


@pytest.mark.parametrize('method', ['tamara','tabby','mada'])
def test_legacy_complete_nested_capture_contract_is_preserved(method):
    raw={'payment':{'method':method,'status':'captured','reference':'canonical','paid_amount':'100','paid_at':PAID},'status':'under_review'}
    p,issues=normalize(raw);assert not issues and p['captures'][0]['amount']=='100'
    assert p['captures'][0]['id']=='canonical'


@pytest.mark.parametrize('where', ['canonical','native','both'])
def test_explicit_plan_passes_through_without_inference(where):
    raw=native()
    if where in {'canonical','both'}:raw['payment']={'plan_id':'documented-plan'}
    if where in {'native','both'}:raw['payment_methods'][0]['plan_id']='documented-plan'
    p,issues=normalize(raw);assert p['plan_id']=='documented-plan'
    s=source(raw);assert s['orders'][0]['payment']['plan_id']=='documented-plan'


def test_installment_alias_does_not_invent_a_plan():
    p,_=normalize(native());assert 'plan_id' not in p


def test_conflicting_plans_reject_payment_not_product_costs():
    raw=native(payment={'plan_id':'p1'});raw['payment_methods'][0]['plan_id']='p2'
    p,issues=normalize(raw);assert p is None and issues[0]['code']=='provider_payment_plan_conflict'
    s=source(raw);assert 'payment' not in s['orders'][0] and len(s['orders'][0]['items'])==1
    assert s['orders'][0]['items'][0]['cost']=='20'


@pytest.mark.parametrize('plan',[True,{},[], '', '\n', 'x'*257])
def test_invalid_plan_is_not_silently_dropped(plan):
    p,issues=normalize(native(payment={'plan_id':plan}))
    assert p is None and issues[0]['code']=='provider_payment_plan_invalid'


@pytest.mark.parametrize('value', [True,{},[], '-1','NaN','Infinity','0.001','1000000000000'])
def test_malformed_amount_never_becomes_zero(value):
    raw=native();raw['payment_actions']['remaining_action']['paid_amount']={'amount':value,'currency':'SAR'}
    p,issues=normalize(raw);assert p is None and issues


@pytest.mark.parametrize('field', ['payment','payment_methods','payment_actions'])
def test_malformed_containers_are_scoped_diagnostics(field):
    p,issues=normalize(native(**{field:'invalid'}))
    assert p is None and issues[0]['code']=='provider_payment_shape_invalid'


@pytest.mark.parametrize('method', ['tamara_fake','not_tabby','other_credit_card','xmada','unknown'])
def test_no_fuzzy_provider_routing(method):
    assert provider_for(method) is None
    p,issues=normalize(native(method));assert p is None and issues[0]['code']=='provider_payment_method_unknown'


@pytest.mark.parametrize('method',['cod','cash_on_delivery','bank','تحويل بنكي'])
def test_bank_and_cod_are_not_provider_funds(method):
    p,issues=normalize({'payment_method':method});assert p is None and not issues


@pytest.mark.parametrize('method',['tabby','bank','cod','unknown'])
def test_conflicting_root_and_nested_methods_do_not_route_money(method):
    p,issues=normalize(native(payment={'method':method}))
    assert p is None and issues[0]['code']=='provider_payment_method_conflict'


def test_duplicate_identical_native_reference_is_not_summed():
    raw=native();raw['payment_methods']*=2
    p,issues=normalize(raw);assert p['paid_observation']['amount']=='300.00'
    assert issues[0]['code']=='provider_capture_evidence_incomplete'


def test_duplicate_reference_with_different_amount_is_rejected():
    raw=native();raw['payment_methods'].append(dict(raw['payment_methods'][0],amount='20'))
    p,issues=normalize(raw);assert p is None and issues[0]['code']=='provider_payment_reference_conflict'


@pytest.mark.parametrize('change',[{'transaction_reference':'another'},{'payment_method':'tabby_installment'},{'payment_method':'bank'}])
def test_split_payment_requires_explicit_review(change):
    raw=native();raw['payment_methods'].append(dict(raw['payment_methods'][0],**change))
    p,issues=normalize(raw);assert p is None and issues[0]['code']=='provider_split_payment_review_required'


def test_reference_conflict_across_native_and_canonical_is_rejected():
    p,issues=normalize(native(payment={'reference':'different','status':'paid','paid_at':PAID}))
    assert p is None and issues[0]['code']=='provider_payment_reference_conflict'


@pytest.mark.parametrize('date', ['2026-10-06','2026-10-06T01:02:00', 'bad','2026-10-05T00:00:00Z','2027-01-01T00:00:00Z'])
def test_capture_timestamp_is_never_replaced_by_order_dates(date):
    p,issues=normalize(native(payment={'status':'paid','paid_at':date}))
    assert p is None and issues


def test_native_timezone_object_is_used_only_when_explicitly_a_payment_time():
    p,issues=normalize(native(payment={'status':'paid','paid_at':{'date':'2026-10-06 04:02:00','timezone':'Asia/Riyadh'}}))
    assert not issues and p['captures'][0]['captured_at']==PAID


def test_unconfirmed_order_status_and_pending_flag_are_not_capture_proof():
    p,issues=normalize(native(status='delivered',updated_at=NOW,date=CREATED))
    assert 'captures' not in p and issues[0]['code']=='provider_capture_evidence_incomplete'


def test_full_capture_conflicting_with_pending_flag_is_rejected():
    p,issues=normalize(native(payment={'status':'paid','paid_at':PAID},is_pending_payment=True))
    assert p is None and issues[0]['code']=='provider_capture_status_conflict'


@pytest.mark.parametrize('currency', ['USD','QAR','KWD','AED'])
def test_foreign_paid_currency_not_treated_as_sar(currency):
    raw=native();raw['payment_actions']['remaining_action']['paid_amount']['currency']=currency
    p,issues=normalize(raw);assert p is None and issues[0]['code']=='provider_payment_currency_conflict'


def test_currency_preserved_without_conversion_when_sources_agree():
    raw=native()
    for k in ['remaining_action','refund_action']:raw['payment_actions'][k]['paid_amount']['currency']='QAR'
    raw['payment_actions']['refund_action']['refund_amount']['currency']='QAR'
    p,issues=normalize(raw,currency='QAR');assert p['currency']=='QAR' and p['gross']=='300.00'


@pytest.mark.parametrize('change',[{'refunded_amount':'20'},{'status':'restored'},{'status':'refunded'}])
def test_refund_hints_never_create_an_executed_refund(change):
    p,issues=normalize(native(**change));assert p is None and issues[0]['code']=='provider_refund_execution_evidence_required'


def test_native_refunded_transaction_hint_is_not_replayed():
    raw=native();raw['payment_actions']['refund_action']['refunded_transactions']=[{'amount':{'amount':20,'currency':'SAR'}}]
    p,issues=normalize(raw);assert p is None and issues[0]['code']=='provider_refund_execution_evidence_required'


@pytest.mark.parametrize('status',['cancelled','canceled','ملغي'])
def test_cancellation_status_is_not_a_proved_void(status):
    p,issues=normalize(native(status=status));assert p is None and issues[0]['code']=='provider_cancellation_execution_evidence_required'


def test_old_order_not_reintroduced_into_opening_covered_provider_balances():
    record=order(**native());record['raw_by_source']['salla_direct']['created_at']='2026-09-01T00:00:00Z'
    s=run(src.collect_sources(DB({'unified_orders':[record]}),'owner',START,NOW))
    assert s['orders']==[]


def test_unknown_evidence_preserves_existing_confirmed_money_and_no_cash_write():
    complete=source(native(payment={'status':'paid','paid_at':PAID}))
    original=reconcile(state(),complete,NOW)
    before=deepcopy(original)
    conflict=source(native(payment={'method':'tabby'}))
    after=reconcile(original,conflict,NOW)
    assert original==before
    assert after['engine']['obligations']['provider:100']['confirmed']=='300.00'
    assert after['openings']==before['openings'] and after['movements']==before['movements']
    assert any(i['code']=='provider_payment_method_conflict' for i in after['engine']['issues'])


def test_alias_export_is_shared_with_customer_return_lookup():
    from operational_customer_returns import PAYMENT_METHODS as returns_aliases
    assert returns_aliases is PAYMENT_METHODS is src.PAYMENT_METHODS
    assert 'tabby_installment' in returns_aliases['tabby']


def test_native_partial_paid_amount_is_retained_without_claiming_full_capture():
    raw=native();raw['payment_methods'][0]['amount']='100'
    for k in ['remaining_action','refund_action']:raw['payment_actions'][k]['paid_amount']['amount']='100'
    raw['payment_actions']['remaining_action']['remaining_amount']={'amount':200,'currency':'SAR'}
    p,issues=normalize(raw);assert p['paid_observation']['amount']=='100.00' and 'captures' not in p
    raw['payment_actions']['remaining_action']['remaining_amount']['amount']=190
    p,issues=normalize(raw);assert p is None and issues[0]['code']=='provider_payment_amount_conflict'


@pytest.mark.parametrize('method,provider', [('tamara_installment','tamara'),('tabby_installment','tabby')])
def test_customer_return_read_preserves_already_net_total_and_observation(monkeypatch,method,provider):
    import operational_customer_returns as returns
    async def read_only_state(db,owner):
        assert owner=='owner'
        return {'customer_returns':[]}
    monkeypatch.setattr(returns,'read',read_only_state)
    raw=native(method,payment={'method':method,'status':'paid','reference':'synthetic-ref','paid_at':PAID,'paid_amount':'170'})
    raw['total']='150'
    raw['payment_methods'][0]['amount']='170'
    for k in ('refund_action','remaining_action'):raw['payment_actions'][k]['paid_amount']['amount']='170'
    raw['payment_actions']['refund_action']['refunded_transactions']=[{'amount':{'amount':'20','currency':'SAR'}}]
    db=DB({'unified_orders':[order(**raw)]})
    result=run(returns.order_view(db,'owner','10'))
    assert result['total']=='150.00' and result['provider']==provider
    assert result['salla_refund_observation']['amount']=='20.00'
    assert result['items'][0]['remaining']==2


def test_customer_return_read_rejects_conflicting_original_platform(monkeypatch):
    from fastapi import HTTPException
    import operational_customer_returns as returns
    db=DB({'unified_orders':[order(**native(payment={'method':'tabby'}))]})
    with pytest.raises(HTTPException) as exc:run(returns.order_view(db,'owner','10'))
    assert exc.value.detail['code']=='customer_return_payment_identity_conflict'


@pytest.mark.parametrize('container,value', [('status',[]),('status',{'slug':[]}),('payment',{'status':[]}),
    ('payment_methods',[None]),('payment_actions',{'refund_action':[]}),('payment_actions',{'refund_action':{'refunded_transactions':{}}})])
def test_unhashable_or_malformed_nested_data_cannot_abort_other_orders(container,value):
    p,issues=normalize(native(**{container:value}));assert p is None and issues[0]['code']=='provider_payment_shape_invalid'
