from copy import deepcopy
from unittest.mock import AsyncMock, patch
import test_operational_instruction_notifications as fixtures
import order_tracking_notes_routes as routes
import operational_instruction_notifications as notifications
from order_tracking_notes import ORDER_TRACKING_INSTRUCTIONS
from instruction_edit_flow import snapshot, revision

class EditFlowTests(fixtures.InstructionNotificationTests):
    # Reuse only the fixture, not the inherited legacy cases.
    def setUp(self):
        super().setUp()
        self.items = [{"order_item_id":"i1", "name":"hoodie", "quantity":1, "options_normalized":{"size":"M"}}, {"order_item_id":"i2", "name":"hoodie", "quantity":1, "options_normalized":{"size":"S"}}]
        self.before = snapshot(self.items, ['i1','i2'])
        self.instruction.update(action_type='edit_order', edit_flow=1, edit_phase='awaiting_stop', edit_baseline=self.before, approval_required=False, required_action='write_result')
        self.db.raw[ORDER_TRACKING_INSTRUCTIONS].replace_one({'id':'n'}, deepcopy(self.instruction))
        self.order_patch = patch.object(routes, 'get_order', AsyncMock(side_effect=lambda *a, **k:{'items':deepcopy(self.items)}))
        self.order_patch.start()
    def tearDown(self): self.order_patch.stop(); super().tearDown()
    def post(self, path, body): return self.client.post('/api/order-tracking-notes/instructions/n/'+path, json=body)
    async def stops(self):
        tasks=await notifications.ensure_instruction_tasks(self.db,'owner',self.instruction)
        for task in tasks:
            self.select(task['assignee_id'])
            r=self.post('stop-acknowledgment',{'task_ids':[task['id']],'note':'Stopped, send revision'})
            self.assertEqual(r.status_code,200,r.text)
        return tasks
    async def sent(self):
        tasks=await self.stops()
        self.items[0]['options_normalized']['size']='L'
        self.select('cs'); digest=revision(snapshot(self.items,['i1','i2']))
        r=self.post('send-edit',{'revision':digest});self.assertEqual(r.status_code,200,r.text)
        return tasks,digest
    async def test_stop_then_send_then_all_workers_execute_without_second_approval(self):
        tasks,digest=await self.sent()
        self.select('a')
        detail=self.client.get('/api/order-tracking-notes/instructions/n').json()
        self.assertNotIn('edit_baseline',detail['instruction'])
        self.assertEqual(detail['product_cards'][0]['before_options']['size'],'M')
        self.assertEqual(detail['product_cards'][0]['requested_options']['size'],'L')
        for index,task in enumerate(tasks):
            self.select(task['assignee_id'])
            r=self.post('tasks/submit',{'task_ids':[task['id']],'confirmation':'modified','note':'Done','revision':digest})
            self.assertEqual(r.status_code,200,r.text)
            row=self.db.raw[ORDER_TRACKING_INSTRUCTIONS].find_one({'id':'n'})
            self.assertEqual(row['status'],'completed' if index==len(tasks)-1 else 'active')
        self.assertEqual(row['completed_by'],tasks[-1]['assignee_id'])
    async def test_cannot_send_before_stop_or_without_actual_change(self):
        self.select('cs'); digest=revision(self.before)
        self.assertEqual(self.post('send-edit',{'revision':digest}).status_code,409)
        await self.stops();self.select('cs')
        self.assertEqual(self.post('send-edit',{'revision':digest}).status_code,409)
    async def test_employee_cannot_send_or_acknowledge_other_employee_task(self):
        tasks=await self.stops();self.items[0]['options_normalized']['size']='L'
        self.select('a')
        self.assertEqual(self.post('send-edit',{'revision':revision(snapshot(self.items,['i1','i2']))}).status_code,403)
        foreign=next(t for t in tasks if t['assignee_id']=='b')
        self.assertEqual(self.post('stop-acknowledgment',{'task_ids':[foreign['id']],'note':'Stop'}).status_code,403)
    async def test_stale_or_missing_revision_cannot_confirm(self):
        tasks,digest=await self.sent();self.select(tasks[0]['assignee_id'])
        payload={'task_ids':[tasks[0]['id']],'confirmation':'modified','note':'Done'}
        self.assertEqual(self.post('tasks/submit',payload).status_code,409)
        self.items[0]['options_normalized']['size']='XL'
        self.assertEqual(self.post('tasks/submit',{**payload,'revision':digest}).status_code,409)
        self.assertEqual(self.db.raw[notifications.TASKS].count_documents({'status':'submitted'}),0)
    async def test_new_flow_releases_only_its_physical_hold_after_execution(self):
        self.db.raw[ORDER_TRACKING_INSTRUCTIONS].update_one({'id':'n'}, {'$set':{'operational_hold':True,'hold_id':'hold-n','activation_state':'active','activated_piece_stages':{'p1':'preparation','p2':'preparation'}}})
        self.db.raw[routes.FULFILLMENT_HOLDS].insert_one({'id':'hold-n','user_id':'owner','status':'active','piece_ids':['p1','p2'],'before_states':[{'piece_id':'p1','status':'in_progress','execution_status':'in_progress'},{'piece_id':'p2','status':'in_progress','execution_status':'in_progress'}]})
        self.db.raw.mezan_preparation_pieces_v1.update_many({}, {'$set':{'active_hold_id':'hold-n','status':'blocked'}})
        tasks,digest=await self.sent()
        for index,task in enumerate(tasks):
            self.select(task['assignee_id'])
            r=self.post('tasks/submit',{'task_ids':[task['id']],'confirmation':'modified','note':'Done','revision':digest})
            self.assertEqual(r.status_code,200,r.text)
            if index==0:
                self.assertEqual(self.db.raw.mezan_preparation_pieces_v1.count_documents({'active_hold_id':'hold-n'}),2)
        self.assertEqual(self.db.raw.mezan_preparation_pieces_v1.count_documents({'active_hold_id':'hold-n'}),0)
        self.assertEqual(self.db.raw.mezan_preparation_pieces_v1.count_documents({'status':'in_progress'}),2)
        self.assertEqual(self.db.raw[routes.FULFILLMENT_HOLDS].find_one({'id':'hold-n'})['status'],'released')

    async def test_quantity_change_is_not_silently_applied(self):
        await self.stops();self.items[0]['quantity']=2;self.select('cs')
        self.assertEqual(self.post('send-edit',{'revision':revision(snapshot(self.items,['i1','i2']))}).status_code,409)

    async def test_new_order_item_invalidates_sent_order_revision(self):
        tasks, digest = await self.sent()
        self.items.append({'order_item_id': 'i3', 'name': 'added', 'quantity': 1})
        self.select(tasks[0]['assignee_id'])
        response = self.post('tasks/submit', {'task_ids': [tasks[0]['id']],
            'confirmation': 'modified', 'note': 'Done', 'revision': digest})
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(self.db.raw[notifications.TASKS].count_documents({'status': 'submitted'}), 0)

    async def test_creator_can_resend_changed_revision_before_execution(self):
        tasks, first = await self.sent()
        self.items[0]['options_normalized']['size'] = 'XL'
        second = revision(snapshot(self.items, ['i1', 'i2']))
        self.select('cs')
        response = self.post('send-edit', {'revision': second})
        self.assertEqual(response.status_code, 200, response.text)
        event_count = self.db.raw[notifications.NOTIFICATIONS].count_documents({})
        self.assertEqual(self.post('send-edit', {'revision': second}).status_code, 200)
        self.assertEqual(self.db.raw[notifications.NOTIFICATIONS].count_documents({}), event_count)
        self.select(tasks[0]['assignee_id'])
        payload = {'task_ids': [tasks[0]['id']], 'confirmation': 'modified', 'note': 'Done'}
        self.assertEqual(self.post('tasks/submit', {**payload, 'revision': first}).status_code, 409)
        self.assertEqual(self.post('tasks/submit', {**payload, 'revision': second}).status_code, 200)

    async def test_submission_fence_wins_race_against_resend(self):
        tasks, first = await self.sent()
        self.items[0]['options_normalized']['size'] = 'XL'
        second = revision(snapshot(self.items, ['i1', 'i2']))
        original = fixtures.Collection.find_one_and_update
        async def interleave(collection, query, update, **kwargs):
            if update.get('$set', {}).get('edit_revision') == second:
                self.db.raw[ORDER_TRACKING_INSTRUCTIONS].update_one({'id': 'n', 'edit_revision': first},
                    {'$set': {'edit_submission_started': True}})
            return await original(collection, query, update, **kwargs)
        self.select('cs')
        with patch.object(fixtures.Collection, 'find_one_and_update', interleave):
            response = self.post('send-edit', {'revision': second})
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(self.db.raw[ORDER_TRACKING_INSTRUCTIONS].find_one({'id': 'n'})['edit_revision'], first)

    async def test_resend_wins_before_submission_fence_without_task_writes(self):
        tasks, first = await self.sent()
        original = fixtures.Collection.__getattr__
        def interleave(collection, name):
            method = original(collection, name)
            if name != 'update_one':
                return method
            async def update(query, value, **kwargs):
                if value.get('$set', {}).get('edit_submission_started') is True:
                    self.db.raw[ORDER_TRACKING_INSTRUCTIONS].update_one({'id': 'n', 'edit_revision': first},
                        {'$set': {'edit_revision': 'f' * 64}})
                return await method(query, value, **kwargs)
            return update
        self.select(tasks[0]['assignee_id'])
        with patch.object(fixtures.Collection, '__getattr__', interleave):
            response = self.post('tasks/submit', {'task_ids': [tasks[0]['id']],
                'confirmation': 'modified', 'note': 'Done', 'revision': first})
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(self.db.raw[notifications.TASKS].count_documents({'status': 'submitted'}), 0)

    async def test_partial_execution_cannot_be_overwritten_by_resend(self):
        tasks, first = await self.sent()
        self.select(tasks[0]['assignee_id'])
        self.assertEqual(self.post('tasks/submit', {'task_ids': [tasks[0]['id']],
            'confirmation': 'modified', 'note': 'Done', 'revision': first}).status_code, 200)
        self.items[0]['options_normalized']['size'] = 'XL'
        self.select('cs')
        self.assertEqual(self.post('send-edit', {'revision': revision(snapshot(self.items, ['i1', 'i2']))}).status_code, 409)
        self.assertEqual(self.db.raw[ORDER_TRACKING_INSTRUCTIONS].find_one({'id': 'n'})['edit_revision'], first)

    async def test_reassigned_pending_worker_can_acknowledge_sent_revision(self):
        tasks, digest = await self.sent()
        task = next(t for t in tasks if t['assignee_id'] == 'a')
        self.db.raw.users.insert_one({'id': 'c', 'role': 'employee', 'created_by': 'owner'})
        self.db.raw.mezan_preparation_pieces_v1.update_one({'piece_id': task['piece_id']}, {'$set': {'responsible_employee_id': 'c'}})
        self.select('c')
        response = self.post('stop-acknowledgment', {'task_ids': [task['id']], 'note': 'Stopped'})
        self.assertEqual(response.status_code, 200, response.text)
        payload = {'task_ids': [task['id']], 'confirmation': 'modified', 'note': 'Done', 'revision': digest}
        self.assertEqual(self.post('tasks/submit', payload).status_code, 200)
        self.select('a')
        self.assertIn(self.post('tasks/submit', payload).status_code, (403, 404, 409))

    async def test_completed_submission_retry_is_read_only_and_actor_scoped(self):
        tasks, digest = await self.sent()
        for task in tasks:
            self.select(task['assignee_id'])
            payload = {'task_ids': [task['id']], 'confirmation': 'modified', 'note': 'Done', 'revision': digest}
            self.assertEqual(self.post('tasks/submit', payload).status_code, 200)
        before = list(self.db.raw[notifications.NOTIFICATIONS].find())
        response = self.post('tasks/submit', payload)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.post('complete', {'note': 'Done'}).status_code, 200)
        self.assertEqual(list(self.db.raw[notifications.NOTIFICATIONS].find()), before)
        self.select('owner')
        self.assertIn(self.post('tasks/submit', payload).status_code, (403, 404))
        self.assertEqual(self.post('complete', {'note': 'Done'}).status_code, 403)

    async def test_recovery_capabilities_are_actor_and_revision_scoped(self):
        tasks, digest = await self.sent()
        self.select('cs')
        caps = self.client.get('/api/order-tracking-notes/instructions/n').json()['capabilities']
        self.assertEqual(caps['edit_flow_version'], 1)
        self.assertFalse(caps['can_send_edit_revision'])
        self.items[0]['options_normalized']['size'] = 'XL'
        self.assertTrue(self.client.get('/api/order-tracking-notes/instructions/n').json()['capabilities']['can_send_edit_revision'])
        self.select('a')
        caps = self.client.get('/api/order-tracking-notes/instructions/n').json()['capabilities']
        self.assertFalse(caps['can_send_edit_revision'])
        self.assertFalse(caps['can_acknowledge_edit_stop'])

# The legacy suite runs separately; only run this class's own test methods here.
for name in dir(fixtures.InstructionNotificationTests):
    if name.startswith('test_') and name not in EditFlowTests.__dict__:
        setattr(EditFlowTests,name,None)
