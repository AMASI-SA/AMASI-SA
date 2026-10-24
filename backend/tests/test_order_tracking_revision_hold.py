"""Ready-product stop contracts using real hold helpers and Mongo semantics."""
import unittest
from copy import deepcopy
from datetime import datetime, timezone
from fastapi import HTTPException
from test_salla_order_item_console import AsyncDb
from order_tracking_notes_routes import (
    TrackingInstructionCreate, _revision_hold_confirmations,
    _create_operational_hold, _release_linked_hold,
)


class RevisionHoldTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.db = AsyncDb()
        self.piece = {'piece_id': 'p', 'order_number': '1', 'order_item_id': 'i',
            'user_id': 'owner', 'status': 'ready_for_employee_receipt',
            'execution_status': 'ready', 'updated_at': datetime.now(timezone.utc)}
        self.db.raw['mezan_preparation_pieces_v1'].insert_one(deepcopy(self.piece))
        self.actor = {'id': 'staff', 'name': 'Customer service'}
        self.instruction = {'id': 'n', 'created_by': 'staff', 'scope': 'item', 'target_id': 'i',
            'action_type': 'delete_product', 'note': 'Customer requested removal'}

    def payload(self, **patch):
        return TrackingInstructionCreate(scope='item', target_ids=['i'], action_type='delete_product',
            note='Customer requested removal', target_stages=['preparation'], **patch)

    async def confirm(self, payload, status='processing'):
        return await _revision_hold_confirmations(self.db, merchant_id='owner', order_number='1',
            item_ids=['i'], order={'status': status}, workflow={'stage': 'reviewed'},
            payload=payload, actor_id='staff')

    async def test_ready_requires_explicit_confirmation_and_records_actor(self):
        with self.assertRaises(HTTPException) as caught:
            await self.confirm(self.payload())
        self.assertEqual(caught.exception.status_code, 409)
        decision = caught.exception.detail['items']['i']
        confirmation = {'customer_requested': True, 'item_id': 'i',
            'preparation_revision': decision['preparation_revision'], 'reason': 'Customer insists'}
        accepted = await self.confirm(self.payload(ready_item_confirmations={'i': confirmation}))
        self.assertEqual(accepted[0]['actor_id'], 'staff')
        self.db.raw['mezan_preparation_pieces_v1'].update_one({'piece_id': 'p'}, {'$set': {'assembly_status': 'ready'}})
        with self.assertRaises(HTTPException):
            await self.confirm(self.payload(ready_item_confirmations={'i': confirmation}))

    async def test_fulfilled_order_is_blocked_without_shipment_input(self):
        for state in ('completed', 'delivering', 'delivered'):
            with self.assertRaises(HTTPException) as caught:
                await self.confirm(self.payload(), state)
            self.assertEqual(caught.exception.detail['code'], 'order_fulfillment_blocks_revision')

    async def test_delete_stop_pauses_target_and_release_restores_ready(self):
        self.db.raw['mezan_preparation_pieces_v1'].insert_one({**self.piece, 'piece_id': 'other', 'order_item_id': 'j'})
        hold_id, _ = await _create_operational_hold(self.db, merchant_id='owner', order_number='1',
            pieces=[self.piece], instruction=self.instruction, actor=self.actor)
        pieces = self.db.raw['mezan_preparation_pieces_v1']
        self.assertEqual(pieces.find_one({'piece_id': 'p'})['status'], 'blocked')
        self.assertEqual(pieces.find_one({'piece_id': 'other'})['status'], 'ready_for_employee_receipt')
        await _release_linked_hold(self.db, merchant_id='owner',
            instruction={**self.instruction, 'hold_id': hold_id}, actor=self.actor, note='Cancelled change request')
        self.assertEqual(pieces.find_one({'piece_id': 'p'})['status'], 'ready_for_employee_receipt')
        self.assertNotIn('active_hold_id', pieces.find_one({'piece_id': 'p'}))

    async def test_changed_piece_cannot_be_paused_using_stale_snapshot(self):
        pieces = self.db.raw['mezan_preparation_pieces_v1']
        pieces.update_one({'piece_id': 'p'}, {'$set': {'status': 'ready_for_assembly'}})
        with self.assertRaises(HTTPException) as caught:
            await _create_operational_hold(self.db, merchant_id='owner', order_number='1',
                pieces=[self.piece], instruction=self.instruction, actor=self.actor)
        self.assertEqual(caught.exception.detail['code'], 'fulfillment_stop_piece_conflict')
        self.assertEqual(pieces.find_one({'piece_id': 'p'})['status'], 'ready_for_assembly')
        self.assertEqual(self.db.raw['mezan_fulfillment_holds_v1'].count_documents({}), 0)

    async def test_partial_piece_write_failure_restores_all_owned_pieces(self):
        from unittest.mock import patch
        from test_salla_order_item_console import AsyncCollection
        original = AsyncCollection.__getattr__
        for after_write in (False, True):
            with self.subTest(after_write=after_write):
                self.setUp()
                other = {**self.piece, 'piece_id': 'q'}
                pieces = self.db.raw['mezan_preparation_pieces_v1']
                pieces.insert_one(deepcopy(other))
                failed = False
                def intercept(collection, name):
                    call = original(collection, name)
                    if name != 'update_one':
                        return call
                    async def update(query, change, *args, **kwargs):
                        nonlocal failed
                        should_fail = (not failed and query.get('piece_id') == 'q'
                            and change.get('$set', {}).get('active_hold_id'))
                        if should_fail:
                            failed = True
                            if after_write:
                                await call(query, change, *args, **kwargs)
                            raise RuntimeError('synthetic transient database failure')
                        return await call(query, change, *args, **kwargs)
                    return update
                with patch.object(AsyncCollection, '__getattr__', intercept):
                    with self.assertRaisesRegex(RuntimeError, 'synthetic transient'):
                        await _create_operational_hold(self.db, merchant_id='owner',
                            order_number='1', pieces=[self.piece, other],
                            instruction=self.instruction, actor=self.actor)
                for piece_id in ('p', 'q'):
                    stored = pieces.find_one({'piece_id': piece_id})
                    self.assertEqual(stored['status'], 'ready_for_employee_receipt')
                    self.assertNotIn('active_hold_id', stored)
                self.assertEqual(self.db.raw['mezan_fulfillment_holds_v1'].count_documents({}), 0)
