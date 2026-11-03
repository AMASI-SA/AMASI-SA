"""Synthetic source proof; real isolated Mongo for public ledger verification."""
import asyncio
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from pymongo.read_concern import ReadConcern

from operational_native_purchase_review import review_native_purchase, load_native_purchase_reviews
from operational_supplier_invoice_preview import supplier_invoice_preview
from test_operational_balance_supplier_invoice_preview import database, seed, contents
from accounting_ledger_v2 import post_journal_v2
from accounting_atomic import atomic_owner

START = '2026-11-01T00:00:00+03:00'
APPROVED = '2026-11-01T06:00:00+00:00'
PAID = '2026-11-02T06:00:00+00:00'
NOW = '2026-11-02T22:00:00+00:00'


def case(*, paid='25.00', tax='0.00', treatment='none'):
    from decimal import Decimal
    total = str(Decimal(80)+Decimal(tax))
    invoice = dict(id='native', user_id='owner', schema_version='g47-v1', state='approved',
        supplier_entity_id='supplier', supplier_counterparty_id='supplier', supplier_account_id='supplier',
        total=total, subtotal='80.00', tax_amount=tax, tax_treatment=treatment, currency='SAR',
        tax_evidence_ref='synthetic', tax_evidence_verified=True,
        approved_at=APPROVED, invoice_date='2026-01-01', order_created_at='2025-01-01T00:00:00Z',
        liability_id='debt', txn_group_id='journal', inventory_account_id='inventory', input_vat_account_id='vat',
        lines=[dict(id='rose', quantity=2, unit_cost=40)])
    liability = dict(id='debt', user_id='owner', schema_version='g47-v1', kind='supplier',
        source='purchase_invoice', purchase_invoice_id='native', accounting_txn_group_id='journal',
        supplier_entity_id='supplier', counterparty_id='supplier', expected_amount=total, paid_amount=paid,
        status='paid' if Decimal(paid)==Decimal(total) else 'partial' if Decimal(paid) else 'unpaid')
    rows = []
    def add(group, eid, entity_type, account, sub, typ, side, amount, date, source, meta):
        rows.append(dict(id=eid,user_id='owner',currency='SAR',status='posted',txn_group_id=group,
            entity_type=entity_type,entity_id=account,sub_account=sub,entry_type=typ,side=side,amount=amount,
            source=source,effective_at=date,metadata=deepcopy(meta)))
    meta={'invoice_id':'native','accounting_at':APPROVED}
    inventory=total if treatment=='non_deductible' else '80.00'
    add('journal','inventory-leg','asset','inventory','inventory','purchase_inventory','debit',inventory,APPROVED,'purchase_invoice',meta)
    add('journal','payable-leg','supplier','supplier','payable','purchase_payable','credit',total,APPROVED,'purchase_invoice',meta)
    if Decimal(tax) and treatment=='deductible':
        add('journal','tax-leg','tax','vat','input_vat','purchase_input_vat','debit',tax,APPROVED,'purchase_invoice',{'accounting_at':APPROVED,'evidence_ref':'synthetic'})
    if Decimal(paid):
        meta={'invoice_id':'native','liability_id':'debt','supplier_entity_id':'supplier','payment_operation_id':'payment-1','accounting_at':PAID}
        add('payment','payment-debit','supplier','supplier','payable','supplier_payment','debit',paid,PAID,'supplier_payment',meta)
        add('payment','payment-credit','bank','bank','main','supplier_payment','credit',paid,PAID,'supplier_payment',meta)
    return invoice, [liability], rows


def review(data=None,**kw):
    inv,liabs,rows=data or case()
    return review_native_purchase(inv,liabs,rows,owner='owner',started_at=START,as_of=kw.get('as_of',NOW))


@pytest.mark.parametrize('paid,remaining', [('0','80.00'),('25','55.00'),('80','0.00')])
def test_exact_purchase_payment_proof_is_not_new_operational_debt(paid,remaining):
    c=case(paid=paid); before=deepcopy(c)
    r=review(c)
    assert r['source_verified'],r
    assert r['native_invoice_gross_sar']=='80.00' and r['native_outstanding_sar']==remaining
    assert not r['posting_allowed'] and not r['operational_balance_matched']
    assert r['difference_sar'] is None and not r['opening_coverage_verified']
    assert not r['order_creation_used_for_eligibility']
    assert c==before and review(c)==r


@pytest.mark.parametrize('treatment',['deductible','non_deductible'])
def test_purchase_tax_is_counted_once_with_verified_financial_treatment(treatment):
    r=review(case(tax='12',treatment=treatment))
    assert r['source_verified'] and r['native_invoice_gross_sar']=='92.00'
    assert r['native_outstanding_sar']=='67.00'


@pytest.mark.parametrize('where,field,value',[
    ('invoice','user_id','other'),('invoice','schema_version','legacy'),('invoice','state','draft'),
    ('invoice','supplier_account_id','other'),('invoice','currency','KWD'),('invoice','total','90'),
    ('invoice','subtotal','70'),('invoice','liability_id','other'),('invoice','txn_group_id','other'),
    ('invoice','approved_at','2027-01-01T00:00:00Z'),('invoice','inventory_account_id','other'),
    ('invoice','lines',[]),('invoice','total',True),('invoice','total','NaN'),
    ('liability','user_id','other'),('liability','kind','customer'),('liability','schema_version','legacy'),
    ('liability','source','manual'),('liability','purchase_invoice_id','other'),
    ('liability','accounting_txn_group_id','other'),('liability','counterparty_id','other'),
    ('liability','expected_amount','70'),('liability','paid_amount','26'),('liability','status','paid'),
    ('purchase','amount','79'),('purchase','effective_at','2026-10-01T00:00:00Z'),
    ('purchase','source','manual'),('purchase','currency','QAR'),('purchase','entity_id','other'),
    ('bank','amount','24'),('bank','side','debit'),('bank','entity_type','customer'),('bank','entity_id',''),
    ('bank','effective_at','2027-01-01T00:00:00Z'),('bank','source','manual'),
])
def test_damaged_source_cannot_produce_known_balance(where,field,value):
    c=case();target={'invoice':c[0],'liability':c[1][0],'purchase':c[2][0],'bank':c[2][-1]}[where]
    target[field]=value;r=review(c)
    assert not r['source_verified'],(where,field,value,r)
    assert r['native_outstanding_sar'] is None and r['difference_sar'] is None


@pytest.mark.parametrize('field,value', [('liability_id','other'),('invoice_id','other'),('supplier_entity_id','other'),('payment_operation_id','other'),('accounting_at',APPROVED)])
def test_payment_bank_metadata_must_match_supplier_leg(field,value):
    c=case();c[2][-1]['metadata'][field]=value
    assert not review(c)['source_verified']


@pytest.mark.parametrize('damage',['missing-liability','duplicate-liability','missing-bank','duplicate-entry','second-purchase','reversal-purchase','reversal-payment','unallocated-payment'])
def test_missing_duplicate_reversed_and_unallocated_proof_remain_visible(damage):
    c=case()
    if damage=='missing-liability':c[1].clear()
    if damage=='duplicate-liability':c[1].append(deepcopy(c[1][0]))
    if damage=='missing-bank':c[2].pop()
    if damage=='duplicate-entry':c[2].append(deepcopy(c[2][0]))
    if damage=='second-purchase':
        for row in deepcopy(c[2][:2]):row.update(id=row['id']+'-dup',txn_group_id='second');c[2].append(row)
    if damage.startswith('reversal-'):
        target=c[2][0] if damage=='reversal-purchase' else c[2][-1]
        reverse=deepcopy(target);reverse.update(id='reverse',txn_group_id='reverse',entry_type='reversal',source='accounting_ledger_v2_reversal',metadata={'reverses_entry_id':target['id']});c[2].append(reverse)
    if damage=='unallocated-payment':
        for row in c[2][2:]:row['metadata'].pop('invoice_id');row['metadata'].pop('liability_id')
    assert not review(c)['source_verified'],review(c)


def test_multiple_partial_payments_do_not_recount_original_purchase():
    c=case(paid='20');p=deepcopy(c[2][2:])
    for row in p:row.update(id=row['id']+'-2',txn_group_id='payment-2',amount='5');row['metadata']['payment_operation_id']='op-2'
    c[2].extend(p);c[1][0]['paid_amount']='25'
    r=review(c)
    assert r['source_verified'] and r['native_outstanding_sar']=='55.00'
    assert len(r['payment_journals'])==2
    p[0]['metadata']['payment_operation_id']='payment-1';p[1]['metadata']['payment_operation_id']='payment-1'
    assert not review(c)['source_verified']


def test_pre_start_native_proof_does_not_replay_an_old_balance():
    c=case();c[0]['approved_at']='2026-10-01T00:00:00Z'
    for row in c[2][:2]:row['effective_at']=c[0]['approved_at'];row['metadata']['accounting_at']=c[0]['approved_at']
    r=review(c)
    assert r['source_verified'] and r['approval_period']=='before_operational_start'
    assert not r['posting_allowed'] and r['difference_sar'] is None


async def seed_native(db, *, paid='25'):
    # Use the sealed public writer in an isolated test transaction. The fixture
    # explicitly creates test-only opening metadata then returns to the original
    # operational marker. Production controls are never changed by the reader.
    from accounting_module_contract import OPERATION_ID
    invoice,liabs,rows=case(paid=paid)
    settings=await db.settings.find_one({'user_id':'owner'})
    await db.settings.update_one({'user_id':'owner'},{'$set':{'mezan2_financial_cutover':{
        'operation_id':OPERATION_ID,'status':'active','cutover_at':'2026-09-01T00:00:00.000000Z',
        'opening_active_txn_group_id':'zero:test','opening_root_txn_group_id':'zero:test'}}},upsert=True)
    await db.mz2_opening_balance_drafts.insert_one({'id':'test','user_id':'owner','status':'posted',
        'txn_group_id':'zero:test','zero_only':True,'cutover_at':'2026-09-01T00:00:00.000000Z',
        'opening_root_txn_group_id':'zero:test','preview_hash':'test','approval_hash':'test','evidence_snapshot':{'test':True}})
    await db.mz2_atomic_owners.insert_one({'_id':'owner','revision':0,'ledger_backend_state':'v2_active',
        'ledger_backend_revision':2,'ledger_backend_contract_revision':1,'ledger_backend_activation_ref':'test','writes_paused':False})
    for supplier_id in ['supplier']:
        if not await db.mezan_suppliers_v2.find_one({'user_id':'owner','id':supplier_id}):
            await db.mezan_suppliers_v2.insert_one({'user_id':'owner','id':supplier_id,'status':'active'})
    new=[]
    for group in ['journal','payment']:
        legs=[r for r in rows if r['txn_group_id']==group]
        if not legs:continue
        metadata={'invoice_id':invoice['id']} if group=='journal' else legs[0]['metadata']
        async def write(scoped):
            return await post_journal_v2(scoped._db,user_id='owner',actor_id='owner',actor_name='Synthetic',
                idempotency_key='test-'+group,txn_type='purchase_inventory' if group=='journal' else 'supplier_payment',
                source=legs[0]['source'],effective_at=legs[0]['effective_at'],metadata=metadata,
                entries=[{**{k:r[k] for k in ['entity_type','entity_id','sub_account','entry_type','side','amount','metadata']},'leg_key':r['id'], 'amount':format(Decimal(r['amount']), '.2f')} for r in legs],
                mongo_session=scoped._session)
        journal=await atomic_owner(db,'owner',write)
        if group=='journal':
            invoice['txn_group_id']=journal['group']['txn_group_id'];liabs[0]['accounting_txn_group_id']=invoice['txn_group_id']
        new.append(journal)
    await db.purchase_invoices.insert_one(invoice)
    await db.liabilities.insert_many(liabs)
    await db.mz2_atomic_owners.delete_one({'_id':'owner'})
    if settings:await db.settings.replace_one({'user_id':'owner'},settings)
    else:await db.settings.delete_one({'user_id':'owner'})
    return invoice,new


@pytest.mark.parametrize('damage',[None,'missing-legs','tampered-amount','missing-audit','liability-paid','bank-missing','cross-owner'])
def test_real_mongo_verifier_and_snapshot_are_used_without_writes(damage):
    async def check():
        async with database() as db:
            await seed(db)
            invoice,journals=await seed_native(db)
            if damage=='missing-legs':await db.accounting_general_ledger_v2.delete_many({'txn_group_id':invoice['txn_group_id']})
            if damage=='tampered-amount':await db.accounting_general_ledger_v2.update_one({'entry_type':'purchase_payable'},{'$set':{'amount':'81.00'}})
            if damage=='missing-audit':await db.accounting_audit_log_v2.delete_many({'txn_group_id':invoice['txn_group_id']})
            if damage=='liability-paid':await db.liabilities.update_one({'id':'debt'},{'$set':{'paid_amount':0}})
            if damage=='bank-missing':await db.accounting_general_ledger_v2.delete_many({'entity_type':'bank'})
            if damage=='cross-owner':await db.liabilities.update_one({'id':'debt'},{'$set':{'user_id':'other'}})
            before=await contents(db)
            r=await supplier_invoice_preview(db,'owner',as_of=NOW)
            p=r['native_purchase_review'][0]['native_ledger_review']
            assert p['source_verified'] is (damage is None),p
            if damage is None:
                assert p['native_outstanding_sar']=='55.00'
                assert p['payment_journals'][0]['bank_account_id']=='bank'
            else:assert p['native_outstanding_sar'] is None
            assert not r['native_financial_matching_complete'] and not r['proposed_financial_write_allowed']
            assert r['native_purchase_review'][0]['difference_sar'] is None
            assert r['totals']['invoice_gross']=='80.00'
            assert before==await contents(db)
    asyncio.run(check())


def test_no_snapshot_is_not_replaced_with_unsynchronized_reads():
    with pytest.raises(ValueError,match='snapshot_required'):
        asyncio.run(load_native_purchase_reviews(None,'owner',[],session=None,started_at=START,as_of=NOW))


def test_same_supplier_other_invoice_and_same_amount_do_not_count_as_payment():
    c=case();extra=deepcopy(c[2][2:])
    for row in extra:
        row['id']+='-other';row['txn_group_id']='other-payment'
        row['metadata'].update(invoice_id='other-invoice',liability_id='other-liability',payment_operation_id='other-op')
    c[2].extend(extra)
    assert review(c)['native_settled_sar']=='25.00'


def test_evidence_digest_tracks_bank_and_liability_changes():
    c=case();a=review(c);c[2][-1]['entity_id']='another-bank';b=review(c)
    assert a['source_verified'] and b['source_verified']
    assert a['source_evidence_sha256']!=b['source_evidence_sha256']


def test_indexed_and_unindexed_proof_agree_for_many_invoices():
    from operational_native_purchase_review import _LedgerEvidence
    c=case();index=_LedgerEvidence(c[2],'owner')
    assert review(c)==review((c[0],c[1],index))
    assert not review((c[0],c[1],_LedgerEvidence([],'other')))['source_verified']


def test_native_snapshot_ignores_concurrent_paid_projection_change():
    async def check():
        async with database() as db:
            await seed(db);invoice,_=await seed_native(db)
            async with await db.client.start_session() as session:
                async with session.start_transaction(read_concern=ReadConcern('snapshot')):
                    await db.liabilities.find_one({'user_id':'owner','id':'debt'},session=session)
                    await db.liabilities.update_one({'id':'debt'},{'$set':{'paid_amount':'26'}})
                    r=await load_native_purchase_reviews(db,'owner',[invoice],session=session,started_at=START,as_of=NOW)
                    assert r['native']['source_verified'] and r['native']['native_settled_sar']=='25.00'
            r=await supplier_invoice_preview(db,'owner',as_of=NOW)
            assert not r['native_purchase_review'][0]['native_ledger_review']['source_verified']
    asyncio.run(check())


def test_bad_sealed_journal_does_not_hide_independent_preparation_invoice():
    async def check():
        async with database() as db:
            await seed(db);invoice,_=await seed_native(db)
            await db.accounting_audit_log_v2.delete_many({})
            r=await supplier_invoice_preview(db,'owner',as_of=NOW)
            assert r['arithmetic_complete'] and r['totals']['invoice_gross']=='80.00'
            n=r['native_purchase_review'][0]['native_ledger_review']
            assert n['issues'][0]=='native_purchase_ledger_verification_failed'
            assert n['native_outstanding_sar'] is None and not r['native_financial_matching_complete']
    asyncio.run(check())


@pytest.mark.parametrize('field,value',[('tax_evidence_ref','other'),('tax_evidence_verified',False),('tax_evidence_ref',None)])
def test_tax_journal_reference_must_equal_the_verified_invoice_reference(field,value):
    c=case(tax='12',treatment='deductible');c[0][field]=value
    assert not review(c)['source_verified']


def test_ledger_read_limit_is_explicit_not_an_approved_zero(monkeypatch):
    import operational_native_purchase_review as module
    from accounting_ledger_v2 import AccountingLedgerV2Error
    async def blocked(*a,**kw):
        raise AccountingLedgerV2Error('accounting_v2_report_scope_too_large','synthetic bound')
    monkeypatch.setattr(module,'read_reporting_entries_v2',blocked)
    async def check():
        async with database() as db:
            await seed(db);invoice,_=await seed_native(db)
            r=await supplier_invoice_preview(db,'owner',as_of=NOW)
            n=r['native_purchase_review'][0]['native_ledger_review']
            assert not n['source_verified'] and n['native_outstanding_sar'] is None
            assert n['issues']==['native_purchase_ledger_verification_failed','accounting_v2_report_scope_too_large']
    asyncio.run(check())
