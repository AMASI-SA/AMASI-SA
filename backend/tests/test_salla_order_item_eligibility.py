import unittest
from order_revision_eligibility import (item_revision_decision, validate_order_state,
    validate_ready_confirmation)
from order_revision_contracts import ContractRunnerError


class EligibilityTests(unittest.TestCase):
    def test_all_operations_block_after_fulfillment(self):
        for state in ('completed', 'delivering', 'delivered', 'تم التنفيذ', 'جاري التوصيل', 'تم التوصيل'):
            for method in ('POST', 'PUT', 'DELETE'):
                with self.subTest(state=state, method=method), self.assertRaises(ContractRunnerError):
                    item_revision_decision({'status': state}, method=method, item_id='i', preparation_records=[])

    def test_blocked_name_cannot_hide_behind_custom_slug(self):
        with self.assertRaises(ContractRunnerError):
            validate_order_state({'status': {'slug': 'custom', 'name': 'تم التنفيذ'}})

    def test_shipping_does_not_influence_decision(self):
        for shipping in (None, 'pending', 'shipped', 'delivered'):
            order = {'status': 'under_review', 'shipping_status': shipping, 'tracking_number': '123'}
            result = item_revision_decision(order, method='PUT', item_id='i', preparation_records=[])
            self.assertFalse(result['confirmation_required'])

    def test_add_does_not_require_confirmation_for_other_ready_items(self):
        result = item_revision_decision({'status': 'processing'}, method='POST', item_id=None,
            preparation_records=[{'order_item_id': 'i', 'status': 'ready'}])
        self.assertFalse(result['confirmation_required'])

    def test_ready_confirmation_bound_to_actor_item_and_revision(self):
        for method in ('PUT', 'DELETE'):
            for state in ('ready_for_employee_receipt', 'received', 'ready_for_assembly'):
                decision = item_revision_decision({'status': 'processing'}, method=method, item_id='i',
                    preparation_records=[{'order_item_id': 'i', 'status': state}])
                self.assertTrue(decision['confirmation_required'])
                confirmation = {'customer_requested': True, 'item_id': 'i',
                    'preparation_revision': decision['preparation_revision'], 'reason': 'طلب العميل'}
                self.assertEqual(validate_ready_confirmation(decision, confirmation, actor_id='staff', item_id='i')['actor_id'], 'staff')
                for change in ({'customer_requested': False}, {'item_id': 'other'},
                               {'preparation_revision': 'old'}, {'reason': ' '}):
                    with self.assertRaises(ContractRunnerError):
                        validate_ready_confirmation(decision, {**confirmation, **change}, actor_id='staff', item_id='i')

    def test_unproven_or_wrong_item_rejected(self):
        with self.assertRaises(ContractRunnerError):
            validate_order_state({})
        with self.assertRaises(ContractRunnerError):
            item_revision_decision({'status': 'pending'}, method='PUT', item_id='i',
                preparation_records=[{'order_item_id': 'other', 'status': 'assigned'}])


class PreparationReadTests(unittest.IsolatedAsyncioTestCase):
    async def test_pausing_ready_item_does_not_bypass_confirmation(self):
        from test_salla_order_item_console import AsyncDb
        from order_revision_eligibility import read_preparation
        from datetime import datetime, timezone
        db = AsyncDb()
        for status in ('blocked', 'cancelled'):
            db.raw['mezan_preparation_pieces_v1'].delete_many({})
            db.raw['mezan_fulfillment_holds_v1'].delete_many({})
            db.raw['mezan_preparation_pieces_v1'].insert_one({
                'user_id': 'owner', 'order_number': '1', 'order_item_id': 'i',
                'piece_id': 'p', 'status': status, 'active_hold_id': 'h',
                'updated_at': datetime.now(timezone.utc)})
            db.raw['mezan_fulfillment_holds_v1'].insert_one({
                'id': 'h', 'user_id': 'owner', 'order_number': '1', 'status': 'active',
                'before_states': [{'piece_id': 'p', 'status': 'ready_for_employee_receipt'}]})
            records = await read_preparation(db, 'owner', '1', 'i')
            decision = item_revision_decision({'status': 'processing'}, method='DELETE',
                item_id='i', preparation_records=records)
            self.assertTrue(decision['confirmation_required'])
            self.assertEqual(len(decision['preparation_revision']), 64)

    async def test_missing_or_foreign_hold_cannot_prove_preparation(self):
        from test_salla_order_item_console import AsyncDb
        from order_revision_eligibility import read_preparation
        db = AsyncDb()
        db.raw['mezan_preparation_pieces_v1'].insert_one({
            'user_id': 'owner', 'order_number': '1', 'order_item_id': 'i',
            'piece_id': 'p', 'status': 'blocked', 'active_hold_id': 'h'})
        db.raw['mezan_fulfillment_holds_v1'].insert_one({
            'id': 'h', 'user_id': 'other', 'order_number': '1', 'status': 'active',
            'before_states': [{'piece_id': 'p', 'status': 'assigned'}]})
        with self.assertRaisesRegex(ContractRunnerError, 'item_preparation_hold_unproven'):
            await read_preparation(db, 'owner', '1', 'i')
