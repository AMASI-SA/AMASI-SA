"""Bank ledger evidence. Other rails and customer balances keep independent readers."""
from copy import deepcopy
from decimal import Decimal
import unittest
from unittest.mock import AsyncMock, Mock

from operational_balance_bank_orders import (
    bank_ledger_paid_amount, frozen_bank_provider_conflict, is_bank_split, is_closed_bank_underpayment, is_mada_bank_split,
    is_original_mada_split, is_original_wallet_split, native_paid_amount,
)
from operational_payment_evidence import _instant
from operational_provider_transaction_evidence import COLLECTION, load_transaction_rows, normalize_operational_payment


CREATED = '2026-11-12T10:00:00+03:00'
UPDATED = '2026-11-13T10:00:00+03:00'
SOURCE = {'user_id': 'merchant', 'order_number': 'n', 'order_id': 'oid',
          'g47_salla_snapshot': {'source_updated_at': UPDATED}}
CONTEXT = dict(owner='merchant', order_number='n', provider_order_id='oid', currency='SAR', as_of=UPDATED)
MISSING = 'operational_order_bank_receipt_evidence_missing'


def money(amount):
    return {'amount': amount, 'currency': 'SAR'}


def order(mixed=False):
    paid = '1464.81' if mixed else '202.50'
    legs = [{'payment_method': 'bank', 'amount': '150.66' if mixed else paid, 'transaction_reference': None}]
    if mixed:
        legs.append({'payment_method': 'mada', 'amount': '1314.15', 'transaction_reference': 'card-ref'})
    return {
        'id': 'oid', 'reference_id': 'n', 'date': CREATED, 'updated_at': UPDATED, 'status': {'slug': 'in_progress'},
        'payment_method': 'mada' if mixed else 'bank', 'currency': 'SAR', 'amounts': {'total': money(paid if mixed else '202.56')},
        'payment_methods': legs, 'payment_actions': {
            'remaining_action': {'has_remaining_amount': False, 'remaining_amount': None, 'paid_amount': money(paid)},
            'refund_action': {'refund_amount': money('0'), 'refunded_transactions': [], 'paid_amount': money(paid)}},
        'receipt_image': 'https://cdn.salla.sa/example/receipt.png',
    }


def read(raw, source=SOURCE, **kwargs):
    return bank_ledger_paid_amount(raw, source, total=raw['amounts']['total'], **CONTEXT, **kwargs)


class BankTenderProofTests(unittest.TestCase):
    def test_confirmed_closed_partial_posts_only_observed_tender(self):
        raw = order()
        original = deepcopy(raw)
        self.assertTrue(is_closed_bank_underpayment(raw, 'SAR', raw['amounts']['total']))
        self.assertEqual(read(raw), Decimal('202.50'))
        self.assertEqual(raw, original)
        with self.assertRaises(ValueError):
            native_paid_amount(raw, SOURCE, total=raw['amounts']['total'], **CONTEXT)

    def test_mixed_order_finds_bank_even_when_primary_is_mada(self):
        raw = order(mixed=True)
        self.assertTrue(is_bank_split(raw))
        for primary in ('mada', 'bank'):
            raw['payment_method'] = primary
            original = deepcopy(raw)
            for code in ('provider_payment_reference_missing', 'provider_split_payment_review_required'):
                self.assertEqual(read(raw, payment_issues=[{'code':code}]), Decimal('150.66'))
            self.assertEqual(raw, original)

    def test_evidence_required_for_each_new_tender_shape(self):
        for mixed in (False, True):
            raw = order(mixed)
            del raw['receipt_image']
            with self.assertRaisesRegex(ValueError, MISSING):
                read(raw)
            raw['payment_methods'][0]['transaction_reference'] = 'distinct-bank-transfer'
            self.assertEqual(read(raw), Decimal('150.66' if mixed else '202.50'))

    def test_mixed_refuses_frozen_or_contradictory_sources(self):
        raw = order(True)
        for args in ({'frozen_provider':True},
                     {'payment_issues':[{'code':'provider_payment_amount_conflict'}]},
                     {'payment_issues':[{'code':'provider_payment_method_conflict'}]}):
            with self.subTest(args=args), self.assertRaises(ValueError):
                read(raw, **args)
        for field, value in [('amount','0.00'), ('amount','1200'), ('transaction_reference',None)]:
            bad = deepcopy(raw)
            bad['payment_methods'][1][field] = value
            with self.assertRaises(ValueError):
                read(bad)
        raw['payment_methods'][0]['transaction_reference'] = 'card-ref'
        with self.assertRaises(ValueError):
            read(raw)

    def test_only_pending_component_gate_can_be_resolved_by_a_current_corroborated_bank_snapshot(self):
        raw = order(True)
        source = deepcopy(SOURCE)
        source['g47_salla_snapshot']['component_pending'] = True
        issues = [{'code':'provider_order_page_identity_conflict'}]
        self.assertEqual(read(raw, source, payment_issues=issues), Decimal('150.66'))
        self.assertTrue(source['g47_salla_snapshot']['component_pending'])
        with self.assertRaises(ValueError):
            read(raw, source, payment_issues=issues + [{'code':'provider_payment_amount_conflict'}])
        for field, value in [('order_number','foreign'), ('order_id','foreign'), ('user_id','foreign'),
                             ('last_salla_direct_status_source_updated_at','2026-12-01T00:00:00Z')]:
            with self.subTest(field=field), self.assertRaises(ValueError):
                read(raw, {**source, field:value}, payment_issues=issues)
        with self.assertRaises(ValueError):
            read(raw, SOURCE, payment_issues=issues)
        with self.assertRaises(ValueError):
            read(raw, {**source, 'g47_salla_snapshot':{'component_pending':True,'source_updated_at':CREATED}},
                 payment_issues=issues)
        raw['payment'] = {'method':'tabby_installment'}
        with self.assertRaises(ValueError):
            read(raw, source, payment_issues=issues)

    def test_primary_card_receipt_does_not_substitute_for_a_bank_receipt(self):
        raw = order(True)
        del raw['receipt_image']
        raw['payment'] = {'method':'mada', 'receipt_url':'https://example.test/card/receipt'}
        raw['payment_receipt_url'] = raw['payment']['receipt_url']
        with self.assertRaisesRegex(ValueError, MISSING):
            read(raw)
        raw['payment_methods'][0]['receipt_image'] = {'url':'https://example.test/bank/receipt'}
        self.assertEqual(read(raw), Decimal('150.66'))

    def test_neither_shape_can_skip_refund_status_or_source_checks(self):
        for mixed in (False, True):
            for change in ('stale', 'refund', 'disagreement', 'currency', 'pending', 'authorization', 'reused_types'):
                raw = order(mixed)
                source = deepcopy(SOURCE)
                if change == 'stale':
                    source['g47_salla_snapshot']['source_updated_at'] = CREATED
                elif change == 'refund':
                    raw['payment_actions']['refund_action']['pending_refund_amount'] = money('1.00')
                elif change == 'disagreement':
                    raw['payment_actions']['refund_action']['paid_amount'] = money('111.00')
                elif change == 'currency':
                    raw['payment_methods'][0]['currency'] = 'QAR'
                elif change == 'pending':
                    raw['status']['slug'] = 'payment_pending'
                elif change == 'authorization':
                    raw['payment_status'] = 'authorized'
                else:
                    raw['payment_methods'].append(dict(raw['payment_methods'][0]))
                with self.subTest(mixed=mixed, change=change), self.assertRaises(ValueError):
                    read(raw,source)

    def test_any_primary_and_co_tender_credit_only_explicit_bank_share(self):
        for name in ('apple_pay', 'visa', 'tabby_installment', 'tamara_installment', 'paypal',
                     'emkan', 'customer_wallet', 'cod', 'another-tender'):
            for primary in (name, 'bank'):
                for reverse in (False, True):
                    with self.subTest(name=name, primary=primary, reverse=reverse):
                        raw = order(True)
                        raw['payment_method'] = primary
                        raw['payment_methods'][1]['payment_method'] = name
                        if reverse:
                            raw['payment_methods'].reverse()
                        original = deepcopy(raw)
                        scope = {k: CONTEXT[k] for k in ('owner', 'order_number', 'provider_order_id')}
                        payment, issues = normalize_operational_payment(raw, 'SAR', raw['amounts']['total'],
                            _instant(CREATED), _instant(UPDATED), rows=[], source=SOURCE, **scope)
                        self.assertIsNone(payment)
                        self.assertTrue(is_bank_split(raw))
                        self.assertFalse(is_mada_bank_split(raw))
                        self.assertEqual(read(raw, payment_issues=issues), Decimal('150.66'))
                        self.assertEqual(raw, original)

    def test_outstanding_cod_is_distinct_from_paid_bank_receipt(self):
        raw = order(True)
        raw['payment_method'] = 'cod'
        raw['payment_methods'][1].update(payment_method='cod', transaction_reference=None)
        raw['payment_actions']['remaining_action'].update(
            paid_amount=money('150.66'), remaining_amount=money('1314.15'), has_remaining_amount=True)
        raw['payment_actions']['refund_action']['paid_amount'] = money('150.66')
        self.assertEqual(read(raw), Decimal('150.66'))
        raw['payment_methods'][1]['amount'] = '1300.00'
        with self.assertRaises(ValueError):
            read(raw)

    def test_original_wallet_allocation_retains_its_customer_balance_contract(self):
        raw = order(True)
        raw['payment_method'] = 'bank'
        raw['payment_methods'][1]['payment_method'] = 'customer_wallet'
        self.assertFalse(is_original_wallet_split(raw))
        raw['payment_methods'][0]['transaction_reference'] = 'bank-transfer'
        self.assertTrue(is_original_wallet_split(raw))
        del raw['payment_actions']['refund_action']
        del raw['receipt_image']
        self.assertEqual(read(raw), Decimal('150.66'))
        self.assertEqual(native_paid_amount(raw, SOURCE, total=raw['amounts']['total'], **CONTEXT), Decimal('150.66'))
        raw['payment_methods'][1]['transaction_reference'] = 'bank-transfer'
        self.assertFalse(is_original_wallet_split(raw))
        with self.assertRaises(ValueError):
            read(raw)

    def test_bank_reference_can_disambiguate_an_unreferenced_co_tender(self):
        raw = order(True)
        self.assertTrue(is_original_mada_split(raw, 'SAR', raw['amounts']['total']))
        raw['payment_methods'][1]['transaction_reference'] = None
        self.assertFalse(is_original_mada_split(raw, 'SAR', raw['amounts']['total']))
        with self.assertRaises(ValueError):
            read(raw)
        raw['payment_methods'][0]['transaction_reference'] = 'proved-bank-only'
        del raw['receipt_image']
        self.assertEqual(read(raw), Decimal('150.66'))
        # Competing method identities or amounts cannot be explained by a missing reference.
        raw['payment'] = {'method':'bank'}
        with self.assertRaises(ValueError):
            read(raw)

    def test_multiple_co_tenders_and_wrong_gateway_binding(self):
        raw = order(True)
        raw['payment_methods'][1].update(payment_method='paypal', amount='314.15')
        raw['payment_methods'].append({'payment_method':'tamara_installment', 'amount':'1000', 'transaction_reference':'bnpl'})
        raw['payment_method'] = 'tamara_installment'
        tx = {'user_id':'merchant', 'order_number':'n', 'provider_order_id':'oid', 'source':'salla_transactions',
              'transaction_id':'tx', 'native_transaction_evidence':[
                  {'schema':'salla_transaction_evidence_v1', 'reference':'bnpl', 'transaction_id':'tx',
                   'provider_order_id':'oid', 'currency':'SAR', 'provider':'tamara', 'status':'paid', 'amount':'1000'}]}
        self.assertEqual(read(raw, transaction_rows=[tx]), Decimal('150.66'))
        for key,value in (('provider','paypal'), ('amount','1464.81'), ('reference','other')):
            evidence = deepcopy(tx)
            evidence['native_transaction_evidence'][0][key] = value
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, 'provider_transaction_binding_conflict'):
                read(raw, transaction_rows=[evidence])

    def test_unverified_image_and_total_cannot_replace_itemized_bank_amount(self):
        raw = order(True)
        raw['payment_methods'][0].pop('amount')
        raw['paid_amount'] = money('1464.81')
        with self.assertRaises(ValueError):
            read(raw)
        raw['payment_methods'][0]['amount'] = raw['amounts']['total']
        with self.assertRaises(ValueError):
            read(raw)
        raw = order(True)
        raw['payment_method'] = 'paypal'
        with self.assertRaises(ValueError):
            read(raw)
        raw['payment_methods'][0]['payment_method'] = 'visa'
        self.assertFalse(is_bank_split(raw))
        with self.assertRaises(ValueError):
            read(raw)

    def test_a_frozen_provider_must_explain_only_its_referenced_share(self):
        raw = order(True)
        scope = {k:CONTEXT[k] for k in ('owner', 'order_number', 'provider_order_id', 'currency')}
        capture = {'order_id':'oid', 'provider':'salla', 'amount':'1314.15'}
        page = {'owner_id':'merchant', 'order_number':'n', 'order_id':'oid', 'currency':'SAR',
                'provider':'salla', 'transaction_reference':'card-ref', 'paid':'1314.15'}
        claim = {'id':'provider:oid', 'kind':'provider', 'party_id':'salla', 'party_type':'provider',
                 'currency':'SAR', 'confirmed':'1314.15', 'expected':'0.00', 'direction':'receivable'}

        def conflict(facts, claims=None):
            return frozen_bank_provider_conflict(raw, facts=facts, obligations=(
                {'provider:oid':claim} if claims is None else claims), **scope)

        self.assertFalse(conflict({'capture:card-ref':capture}))
        self.assertFalse(conflict({'provider_order_balance:oid':page}))
        self.assertFalse(conflict({'provider_order_balance:oid':dict(page, transaction_reference=None),
                                   'capture:card-ref':capture}))
        for facts in ({'capture:wrong':capture}, {'capture:card-ref':dict(capture, amount='1464.81')},
                      {'capture:card-ref':dict(capture, provider='paypal')}, {},
                      {'provider_order_balance:oid':dict(page, paid='1464.81')},
                      {'provider_order_balance:oid':dict(page, transaction_reference=None)},
                      {'provider_order_balance:oid':dict(page, owner_id='other')},
                      {'capture:card-ref':capture, 'refund:salla:ref':{'order_id':'oid','amount':'1'}}):
            with self.subTest(facts=facts):
                self.assertTrue(conflict(facts))
        for change in ({'expected':'150.66'}, {'confirmed':'1464.81'}, {'direction':'payable'},
                       {'incomplete':True}, {'currency':'EUR'}):
            self.assertTrue(conflict({'capture:card-ref':capture}, {'provider:oid':dict(claim, **change)}))
        self.assertTrue(conflict({'capture:card-ref':capture}, {}))

    def test_underpayment_cannot_resolve_conflicting_remaining(self):
        raw = order()
        raw['payment_actions']['remaining_action']['remaining_amount'] = money('3')
        with self.assertRaises(ValueError):
            read(raw)


class MixedBankTransactionTests(unittest.IsolatedAsyncioTestCase):
    async def test_bank_primary_mixed_order_loads_and_validates_card_transaction(self):
        raw = order(True)
        raw['payment_method'] = 'bank'
        bare_bank = dict(order(), reference_id='unrelated-bank')
        tx = {'user_id':'merchant', 'order_number':'n', 'provider_order_id':'oid', 'source':'salla_transactions',
              'transaction_id':'tx', 'native_transaction_evidence':[
                  {'schema':'salla_transaction_evidence_v1', 'reference':'card-ref', 'transaction_id':'tx',
                   'provider_order_id':'oid', 'currency':'SAR', 'provider':'salla', 'status':'paid', 'amount':'1314.15'}]}
        collection = Mock()
        collection.find.return_value.to_list = AsyncMock(return_value=[tx])
        groups = await load_transaction_rows(
            {COLLECTION:collection}, 'merchant', [raw, bare_bank], _instant('2026-11-01T00:00:00Z'),
            _instant(UPDATED), to_instant=_instant, max_rows=20)
        self.assertEqual(collection.find.call_args.args[0]['order_number'], {'$in':['n']})
        self.assertEqual(read(raw, transaction_rows=groups['n']), Decimal('150.66'))
        tx['native_transaction_evidence'][0]['amount'] = '1464.81'
        with self.assertRaisesRegex(ValueError, 'provider_transaction_binding_conflict'):
            read(raw, transaction_rows=groups['n'])

    async def test_reference_already_used_on_a_bank_only_receipt_holds_the_mixed_order(self):
        raw = order(True)
        raw['payment_methods'][0]['transaction_reference'] = 'bank-transfer'
        raw['payment_methods'][1]['payment_method'] = raw['payment_method'] = 'tabby_installment'
        bare = dict(order(), reference_id='previous', id='other')
        bare['payment_methods'][0]['transaction_reference'] = 'bank-transfer'
        collection = Mock()
        collection.find.return_value.to_list = AsyncMock(return_value=[])
        groups = await load_transaction_rows(
            {COLLECTION:collection}, 'merchant', [raw, bare], _instant('2026-11-01T00:00:00Z'),
            _instant(UPDATED), to_instant=_instant, max_rows=20)
        self.assertEqual(collection.find.call_args.args[0]['order_number'], {'$in':['n']})
        with self.assertRaisesRegex(ValueError, 'provider_transaction_binding_conflict'):
            read(raw, transaction_rows=groups['n'])
        with self.assertRaisesRegex(ValueError, 'provider_transaction_binding_conflict'):
            read(bare, transaction_rows=groups['previous'])

    async def test_bank_primary_collision_does_not_change_other_payment_recognition(self):
        raw = order(True)
        raw['payment_method'] = 'bank'
        card = dict(order(True), id='different', reference_id='card-only')
        card['payment_methods'] = [card['payment_methods'][1]]
        collection = Mock()
        collection.find.return_value.to_list = AsyncMock(return_value=[])
        groups = await load_transaction_rows(
            {COLLECTION:collection}, 'merchant', [raw, card], _instant('2026-11-01T00:00:00Z'),
            _instant(UPDATED), to_instant=_instant, max_rows=20)
        self.assertFalse(groups.get('card-only'))
        self.assertEqual(groups['n'][0]['native_transaction_evidence'], [])
        with self.assertRaisesRegex(ValueError, 'provider_transaction_binding_conflict'):
            read(raw, transaction_rows=groups['n'])



def test_replayed_refresh_of_bank_and_bnpl_waits_for_original_bank_leg_date():
    import asyncio

    from operational_balance_service import refresh
    from operational_balance_store import read
    from test_operational_balance_bank_orders import native_accounts, native_bank_order, started
    from test_operational_balance_integration import NOW, run
    from test_operational_balance_order_page_payment_flow import order_document

    async def scenario(db):
        await started(db)
        await native_accounts(db)
        pure = order_document('tabby_installment', '100.00', '0.00', number='pytest-tabby',
                              created='2026-10-06T10:00:00+00:00')
        await db.unified_orders.insert_one(pure)
        await refresh(db, 'owner', clock=NOW)
        obligations = (await read(db, 'owner'))['engine']['obligations']
        assert obligations

        doc = native_bank_order('998301')
        raw = doc['raw_by_source']['salla_direct']
        snapshot = order(True)
        raw.update({key:snapshot[key] for key in ('amounts', 'payment_actions', 'payment_methods', 'receipt_image')})
        raw.update(total=raw['amounts']['total'], payment_method='tabby_installment')
        raw['payment_methods'][1]['payment_method'] = 'tabby_installment'
        await db.unified_orders.insert_one(doc)
        await asyncio.gather(refresh(db, 'owner', clock=NOW), refresh(db, 'owner', clock=NOW))
        state = await read(db, 'owner')
        assert state['engine']['obligations'] == obligations
        assert state['movements'] == []
        pending, = [i for i in state['engine']['issues'] if i['code'] == 'operational_order_bank_occurrence_unproven']
        assert (pending['order_number'], pending['party_id'], pending['status']) == ('998301', 'account:rajhi', 'pending')
        await refresh(db, 'owner', clock=NOW)
        assert (await read(db, 'owner'))['movements'] == state['movements']

    run(scenario)

def test_existing_provider_share_can_coexist_only_when_proved_same_payment():
    import asyncio

    from operational_balance_service import refresh
    from operational_balance_store import read
    from test_operational_balance_bank_orders import native_accounts, native_bank_order, started
    from test_operational_balance_integration import NOW, run

    async def scenario(db):
        await started(db)
        await native_accounts(db)
        for number, split in (('998311', False), ('998312', True), ('998313', False)):
            doc = native_bank_order(number, paid='1314.15')
            raw = doc['raw_by_source']['salla_direct']
            raw['total'] = money('1314.15')
            raw['payment_method'] = 'tabby_installment'
            raw['payment_methods'] = [{'payment_method':'tabby_installment', 'transaction_reference':number+'-bnpl',
                                       'amount':'1000' if split else '1314.15'}]
            if split:
                raw['payment_methods'].append({'payment_method':'customer_wallet',
                                              'transaction_reference':number+'-wallet', 'amount':'314.15'})
            await db.unified_orders.insert_one(doc)
        await refresh(db, 'owner', clock=NOW)
        prior = await read(db, 'owner')
        assert len(prior['engine']['obligations']) == 3
        assert 'provider_split:51998312' in prior['engine']['facts']
        assert prior['engine']['facts']['provider_order_balance:51998311']['transaction_reference'] == '998311-bnpl'

        for number in ('998311', '998312', '998313'):
            doc = await db.unified_orders.find_one({'order_number':number})
            raw = doc['raw_by_source']['salla_direct']
            raw['payment_methods'].append({'payment_method':'bank', 'transaction_reference':number+'-bank', 'amount':'150.66'})
            if number == '998313':
                raw['payment_methods'][0]['transaction_reference'] = 'different-tender'
            raw['total'] = money('1464.81')
            raw['payment_actions']['refund_action']['paid_amount'] = money('1464.81')
            raw['payment_actions']['remaining_action']['paid_amount'] = money('1464.81')
            raw['updated_at'] = '2026-10-06T11:00:00+00:00'
            await db.unified_orders.update_one({'order_number':number}, {'$set':{
                'raw_by_source.salla_direct':raw, 'g47_salla_snapshot.source_updated_at':raw['updated_at']}})
        await asyncio.gather(refresh(db, 'owner', clock=NOW), refresh(db, 'owner', clock=NOW))
        result = await read(db, 'owner')
        assert result['movements'] == []
        assert sorted(i['order_number'] for i in result['engine']['issues']
                      if i['code'] == 'operational_order_bank_occurrence_unproven') == ['998311', '998312']
        assert any(i['code'] == 'operational_order_bank_credit_conflict' and i.get('order_number') == '998313'
                   for i in result['engine']['issues'])
        assert result['engine']['obligations'] == prior['engine']['obligations']
        assert result['engine']['facts'] == prior['engine']['facts']

    run(scenario)
