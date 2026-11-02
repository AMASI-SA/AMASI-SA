"""R05 pure matching: same native debt/bank payment, never a second debit."""
from copy import deepcopy
import pytest
from operational_balance_store import initial
from operational_native_purchase_link import build_link_plan, validate_saved_link, LINKS
from test_operational_balance_native_purchase_review import case, review, START, NOW, PAID

SUPPLIER={'id':'supplier','kind':'supplier','currency':'SAR','name':'Test supplier'}
BANKS=[{'id':'bank','kind':'bank','currency':'SAR','name':'Test bank'}]


def state():
    s=initial('owner');s.update(status='active',started_at=START,revision=7)
    s['openings']={'bank':{'id':'bank-opening','party_type':'bank','party_id':'bank','currency':'SAR',
        'direction':'for_us','amount':'1000.00','name':'Test bank'}}
    return s


def selection(paid=True):
    return {'invoice_id':'native','evidence_ref':'document-synthetic','existing_obligation_ids':[],
            'payment_links':[{'payment_operation_id':'payment-1','movement_id':None}] if paid else []}


def plan(s=None,c=None,choice=None,**extra):
    s=state() if s is None else s;c=case() if c is None else c
    choice=selection(bool(c[2][2:])) if choice is None else choice
    return build_link_plan(s,c[0],review(c),choice,supplier=extra.get('supplier',SUPPLIER),
        banks=extra.get('banks',BANKS),collisions=extra.get('collisions',()),as_of=NOW)


def existing(s, amount='80.00', paid='25.00'):
    s['engine']['obligations']={'existing':{'id':'existing','kind':'supplier','party_type':'supplier',
        'party_id':'supplier','currency':'SAR','direction':'payable','expected':'0.00','confirmed':amount,'invoice_id':'manual-invoice'}}
    if paid!='0.00':
        s['movements']=[{'id':'existing-payment','party_type':'supplier','party_id':'supplier','currency':'SAR',
            'amount':paid,'bank_kind':'bank','bank_id':'bank','direction':'outgoing','kind':'payment',
            'allocations':[{'obligation_id':'existing','amount':paid}]}]
    return s


@pytest.mark.parametrize('paid,balance',[('0','80.00'),('25','55.00'),('80','0.00')])
def test_new_source_gives_reviewed_debt_and_bank_delta(paid,balance):
    s=state();before=deepcopy(s);c=case(paid=paid)
    r,a=plan(s,c)
    assert r['can_approve'],r
    assert not r['posting_allowed'] and r['supplier_difference_sar']==balance
    assert r['after']['supplier_outstanding_sar']==balance
    assert s==before and a['openings']==s['openings']
    assert validate_saved_link(a,c[0],review(c))['matched']
    assert len(a['movements'])==(0 if paid=='0' else 1)


def test_existing_invoice_and_existing_payment_link_without_second_debt_or_bank_debit():
    s=existing(state());before=deepcopy(s);ch=selection();ch['existing_obligation_ids']=['existing']
    ch['payment_links'][0]['movement_id']='existing-payment'
    r,a=plan(s,choice=ch)
    assert r['can_approve'] and r['supplier_difference_sar']=='0.00'
    assert r['bank_differences_sar']=={'bank':'0.00'} and not r['new_payment_count']
    assert a['movements']==s['movements'] and a['engine']==s['engine'] and s==before
    assert validate_saved_link(a,case()[0],review())['matched']


def test_recorded_debt_missing_payment_adds_only_payment_delta():
    s=existing(state(),paid='0.00');ch=selection();ch['existing_obligation_ids']=['existing']
    r,a=plan(s,choice=ch)
    assert r['can_approve'],r
    assert r['supplier_difference_sar']=='-25.00' and r['bank_differences_sar']=={'bank':'-25.00'}
    assert a['engine']==s['engine']


def test_replayed_saved_link_has_zero_delta_and_unchanged_money():
    r,a=plan();ch=selection();ch['existing_obligation_ids']=r['obligation_ids']
    ch['payment_links'][0]['movement_id']=r['payments'][0]['movement_id']
    r2,b=plan(a,choice=ch)
    assert r2['can_approve'],r2
    assert r2['supplier_difference_sar']=='0.00' and r2['bank_differences_sar']=={'bank':'0.00'}
    assert b['movements']==a['movements'] and b['engine']==a['engine']


@pytest.mark.parametrize('damage',['unallocated','overallocated','other-bank','other-supplier','incoming','fx','fee','duplicate','other-target','missing'])
def test_old_payment_cannot_be_rewritten_or_guessed(damage):
    s=existing(state());ch=selection();ch['existing_obligation_ids']=['existing'];ch['payment_links'][0]['movement_id']='existing-payment'
    m=s['movements'][0]
    if damage=='unallocated':m['allocations']=[]
    if damage=='overallocated':m['allocations'][0]['amount']='26'
    if damage=='other-bank':m['bank_id']='another'
    if damage=='other-supplier':m['party_id']='another'
    if damage=='incoming':m['direction']='incoming'
    if damage=='fx':m['bank_currency']='KWD'
    if damage=='fee':m['bank_fee_amount']='1'
    if damage=='duplicate':s['movements'].append(deepcopy(m))
    if damage=='other-target':m['allocations'][0]['obligation_id']='other'
    if damage=='missing':ch['payment_links'][0]['movement_id']='missing'
    before=deepcopy(s);r,a=plan(s,choice=ch)
    assert not r['can_approve'] and a is None and s==before


@pytest.mark.parametrize('damage',['precutoff','wrong-owner','source-bad','supplier','bank','targets-duplicate','targets-missing',
    'targets-amount','payment-selection-missing','payment-selection-duplicate','collision','reuse-reference','link-tamper'])
def test_missing_or_conflicting_identity_never_becomes_new_finance(damage):
    s=state();c=case();ch=selection();extra={}
    if damage=='precutoff':s['started_at']='2026-11-02T00:00:00Z'
    if damage=='wrong-owner':s['owner_id']='other'
    if damage=='source-bad':c[2].pop()
    if damage=='supplier':extra['supplier']={**SUPPLIER,'id':'other'}
    if damage=='bank':extra['banks']=[]
    if damage=='targets-duplicate':ch['existing_obligation_ids']=['x','x']
    if damage=='targets-missing':ch['existing_obligation_ids']=['x']
    if damage=='targets-amount':s=existing(s,amount='70');ch['existing_obligation_ids']=['existing']
    if damage=='payment-selection-missing':ch['payment_links']=[]
    if damage=='payment-selection-duplicate':ch['payment_links']*=2
    if damage=='collision':extra['collisions']=[{'source':'preparation_invoice','id':'other'}]
    if damage=='reuse-reference':s['movements']=[{'id':'already','reference':'payment-1'}]
    if damage=='link-tamper':s[LINKS]={'native':{'fingerprint':'bad'}}
    before=deepcopy((s,c,ch));r,a=plan(s,c,ch,**extra)
    assert not r['can_approve'] and a is None and before==(s,c,ch),r


def test_multiple_native_payments_remain_distinct_and_fully_allocated_once():
    c=case(paid='25');extra=deepcopy(c[2][2:])
    for leg in extra:
        leg.update(id=leg['id']+'2',txn_group_id='payment2',amount='15')
        leg['metadata']['payment_operation_id']='payment-2'
    c[2].extend(extra);c[1][0]['paid_amount']='40'
    ch=selection();ch['payment_links'].append({'payment_operation_id':'payment-2','movement_id':None})
    r,a=plan(c=c,choice=ch)
    assert r['can_approve'] and r['after']['supplier_outstanding_sar']=='40.00'
    assert r['bank_differences_sar']=={'bank':'-40.00'} and len(a['movements'])==2
    assert validate_saved_link(a,c[0],review(c))['matched']


def test_additional_verified_payment_requires_new_preview_not_historic_rewrite():
    r,a=plan();c=case();extra=deepcopy(c[2][2:])
    for leg in extra:
        leg.update(id=leg['id']+'2',txn_group_id='payment2',amount='10');leg['metadata']['payment_operation_id']='payment-2'
    c[2].extend(extra);c[1][0]['paid_amount']='35'
    assert not validate_saved_link(a,c[0],review(c))['matched']
    ch=selection();ch['existing_obligation_ids']=r['obligation_ids'];ch['payment_links'][0]['movement_id']=r['payments'][0]['movement_id']
    ch['payment_links'].append({'payment_operation_id':'payment-2','movement_id':None})
    r2,b=plan(a,c,ch)
    assert r2['can_approve'],r2
    assert r2['supplier_difference_sar']=='-10.00' and r2['bank_differences_sar']=={'bank':'-10.00'}
    assert b['movements'][0]==a['movements'][0] and validate_saved_link(b,c[0],review(c))['matched']


@pytest.mark.parametrize('damage',['amount','bank','allocation','missing','extra','saved-link','invoice','reversal'])
def test_saved_match_detects_changes_without_auto_correcting(damage):
    _,s=plan();c=case()
    if damage=='amount':s['movements'][0]['amount']='30'
    if damage=='bank':s['movements'][0]['bank_id']='other'
    if damage=='allocation':s['movements'][0]['allocations']=[]
    if damage=='missing':s['movements']=[]
    if damage=='extra':s['movements'].append({**deepcopy(s['movements'][0]),'id':'another'})
    if damage=='saved-link':s[LINKS]['native']['gross_sar']='999'
    if damage=='invoice':c[0]['invoice_number']='changed'
    if damage=='reversal':c[2].pop()
    before=deepcopy((s,c));r=validate_saved_link(s,c[0],review(c))
    assert not r['matched'] and not r['posting_allowed'] and before==(s,c)
