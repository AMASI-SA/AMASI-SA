"""Bank ledger evidence. Other rails and customer balances keep independent readers."""
from copy import deepcopy
from decimal import Decimal
import unittest
from unittest.mock import AsyncMock, Mock

from operational_balance_bank_orders import (
    bank_ledger_paid_amount, is_closed_bank_underpayment, is_mada_bank_split, native_paid_amount,
)
from operational_payment_evidence import _instant
from operational_provider_transaction_evidence import COLLECTION, load_transaction_rows


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
        self.assertTrue(is_mada_bank_split(raw))
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

    def test_neither_shape_can_skip_refund_status_or_source_checks(self):
        for mixed in (False, True):
            for change in ('stale', 'refund', 'disagreement', 'currency', 'pending', 'reused_types'):
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
                else:
                    raw['payment_methods'].append(dict(raw['payment_methods'][0]))
                with self.subTest(mixed=mixed, change=change), self.assertRaises(ValueError):
                    read(raw,source)

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

