"""Instruction task isolation, lifecycle, stage arrival, and inbox authorization."""
import unittest
from unittest.mock import patch, AsyncMock
from datetime import datetime, timezone
import mongomock
from fastapi import FastAPI
from fastapi.testclient import TestClient
import order_tracking_notes_routes as routes
import operational_instruction_notifications as notifications
from order_tracking_notes import ORDER_TRACKING_INSTRUCTIONS

class Cursor:
    def __init__(self, raw): self.raw = raw
    def sort(self, *args): self.raw = self.raw.sort(*args); return self
    async def to_list(self, length): return list(self.raw.limit(length))
class Collection:
    def __init__(self, raw): self.raw = raw
    def find(self, *args, **kwargs): return Cursor(self.raw.find(*args, **kwargs))
    async def find_one_and_update(self, query, update, **kwargs):
        projection = kwargs.pop("projection", None)
        row = self.raw.find_one_and_update(query, update, **kwargs)
        if row and projection:
            row = {key: value for key, value in row.items() if projection.get(key, 1)}
        return row
    def __getattr__(self, name):
        async def invoke(*args, **kwargs): return getattr(self.raw, name)(*args, **kwargs)
        return invoke
class DB:
    def __init__(self): self.raw = mongomock.MongoClient(tz_aware=True).db
    def __getitem__(self, name): return Collection(self.raw[name])
    def __getattr__(self, name): return self[name]

class InstructionNotificationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.db = DB()
        for uid, role in [('owner','owner'), ('cs','customer_service'), ('a','employee'), ('b','employee'), ('courier-account','store_driver')]:
            self.db.raw.users.insert_one({'id':uid,'role':role, **({} if uid=='owner' else {'created_by':'owner'})})
        self.instruction = {'id':'n','user_id':'owner','created_by':'cs','order_number':'1',
            'scope':'order','action_type':'cancel_order','task_protocol':1,'status':'active',
            'target_stages':['preparation'],'note':'stop','created_at':datetime.now(timezone.utc),
            'approval_required':True,'proposed_changes':[{'field':'size','previous':'M','requested':'L','order_item_id':'i1'}, {'field':'color','previous':'red','requested':'blue','order_item_id':'i2'}]}
        self.db.raw[ORDER_TRACKING_INSTRUCTIONS].insert_one(dict(self.instruction))
        for piece, employee, item in [('p1','a','i1'),('p2','b','i2')]:
            self.db.raw.mezan_preparation_pieces_v1.insert_one({'user_id':'owner','order_number':'1',
                'piece_id':piece,'order_item_id':item,'responsible_employee_id':employee,'status':'in_progress'})
        self.db.raw.order_review_workflows.insert_one({'user_id':'owner','order_number':'1','stage':'in_progress'})
        self.user = self.db.raw.users.find_one({'id':'a'})
        async def current(): return self.user
        async def context(db, user): return {'actor_id':user['id'],'merchant_id':user.get('created_by',user['id']),
            'is_owner':user['id']=='owner','permissions':set(),'responsibilities':set(),'warehouse_ids':None}
        self.context_patch = patch.object(routes, '_actor_context', context); self.context_patch.start()
        self.push_patch = patch.object(notifications, '_enqueue', AsyncMock()); self.push_patch.start()
        self.router = routes.make_order_tracking_notes_router(self.db, current)
        self.app = FastAPI(); self.app.include_router(self.router, prefix='/api')
        self.client = TestClient(self.app)
    def tearDown(self): self.context_patch.stop(); self.push_patch.stop(); self.client.close()
    def select(self, uid): self.user = self.db.raw.users.find_one({'id':uid})
    async def test_detail_cards_follow_employee_scope_and_creator_order_scope(self):
        self.db.raw.mezan_preparation_pieces_v1.update_one({'piece_id':'p1'}, {'$set': {'product_options_snapshot': {'size':'M'}, 'image_url':'first.png'}})
        self.db.raw.mezan_preparation_pieces_v1.update_one({'piece_id':'p2'}, {'$set': {'product_options_snapshot': {'size':'S'}, 'image_url':'second.png'}})
        with patch.object(routes, 'get_order', AsyncMock(side_effect=routes.OrderNotFoundError('missing'))):
            response = self.client.get('/api/order-tracking-notes/instructions/n')
            self.assertEqual(response.status_code, 200, response.text)
            cards = response.json()['product_cards']
            self.assertEqual([card['piece_id'] for card in cards], ['p1'])
            self.assertEqual(cards[0]['image_url'], 'first.png')
            self.assertEqual(cards[0]['requested_options']['size'], 'L')
            self.select('cs')
            response = self.client.get('/api/order-tracking-notes/instructions/n')
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual({card['piece_id'] for card in response.json()['product_cards']}, {'p1', 'p2'})

    async def test_same_actor_cannot_submit_another_piece_before_target_stage(self):
        self.db.raw[ORDER_TRACKING_INSTRUCTIONS].update_one({'id':'n'}, {'$set': {'target_stages':['assembly_labeling']}})
        self.db.raw.mezan_preparation_pieces_v1.update_one({'piece_id':'p1'}, {'$set': {'status':'ready_for_assembly'}})
        self.db.raw.mezan_preparation_pieces_v1.update_one({'piece_id':'p2'}, {'$set': {'responsible_employee_id':'a'}})
        self.db.raw.order_review_workflows.update_one({'order_number':'1'}, {'$set': {'claimed_by':'a'}})
        row = self.db.raw[ORDER_TRACKING_INSTRUCTIONS].find_one({'id':'n'})
        tasks = await notifications.ensure_instruction_tasks(self.db, 'owner', row)
        future = next(task for task in tasks if task['piece_id']=='p2')
        response = self.client.post('/api/order-tracking-notes/instructions/n/tasks/submit', json={
            'task_ids':[future['id']], 'confirmation':'removed_from_preparation', 'note':'not arrived'})
        self.assertEqual(response.status_code, 403, response.text)
        visible = self.client.get('/api/order-tracking-notes/instructions/n').json()['instruction']['tasks']
        self.assertEqual([task['piece_id'] for task in visible], ['p1'])
    async def test_own_piece_projection_and_cross_actor_submission(self):
        r=self.client.get('/api/order-tracking-notes/instructions/n'); self.assertEqual(r.status_code,200,r.text)
        task=r.json()['instruction']['tasks'][0]
        self.assertEqual(task['piece_id'],'p1')
        self.assertEqual([x['order_item_id'] for x in r.json()['instruction']['proposed_changes']],['i1'])
        self.select('b')
        r=self.client.post('/api/order-tracking-notes/instructions/n/tasks/submit',json={'task_ids':[task['id']],'confirmation':'removed_from_preparation'})
        self.assertEqual(r.status_code,403)
    async def test_each_assignee_must_submit_and_manager_cannot_bypass(self):
        tasks=await notifications.ensure_instruction_tasks(self.db,'owner',self.instruction)
        self.select('owner')
        r=self.client.post('/api/order-tracking-notes/instructions/n/complete',json={'note':'override'})
        self.assertEqual(r.status_code,403)
        self.select("cs")
        r=self.client.post("/api/order-tracking-notes/instructions/n/complete",json={"note":"override"})
        self.assertEqual(r.status_code,409)
        for employee in ['a','b']:
            self.select(employee); task=next(t for t in tasks if t['assignee_id']==employee)
            r=self.client.post('/api/order-tracking-notes/instructions/n/tasks/submit',json={'task_ids':[task['id']],'confirmation':'removed_from_preparation'})
            self.assertEqual(r.status_code,200,r.text)
            self.assertEqual(r.json()['waiting_customer_service_approval'],employee=='b')
        self.assertEqual(self.db.raw[ORDER_TRACKING_INSTRUCTIONS].find_one({'id':'n'})['status'],'waiting_customer_service_approval')
        self.assertFalse(self.db.raw.order_review_workflows.find_one({}).get('released_at'))
    async def test_wrong_confirmation_does_not_submit(self):
        tasks=await notifications.ensure_instruction_tasks(self.db,'owner',self.instruction)
        r=self.client.post('/api/order-tracking-notes/instructions/n/tasks/submit',json={'task_ids':[tasks[0]['id']],'confirmation':'modified'})
        self.assertEqual(r.status_code,422)
        self.assertEqual(self.db.raw[notifications.TASKS].count_documents({'status':'submitted'}),0)
    async def test_future_stage_no_alert_until_arrival_and_driver_identity(self):
        row={**self.instruction,'target_stages':['store_courier']}
        self.db.raw.store_drivers.insert_one({'id':'driver-record','user_id':'owner','account_user_id':'courier-account','status':'active'})
        self.db.raw.store_delivery_assignments.insert_one({'user_id':'owner','order_number':'1','driver_id':'driver-record','active':True,'status':'assigned'})
        self.assertEqual(await notifications.recipients(self.db,'owner',row),set())
        self.db.raw.order_review_workflows.update_one({}, {'$set':{'stage':'delivering','carrier_label_type':'store_courier'}})
        targets=await notifications.recipients(self.db,'owner',row)
        self.assertIn('courier-account',targets); self.assertNotIn('driver-record',targets)
        self.assertNotIn('a',targets)
    async def test_reassignment_transfers_pending_not_other_employee_confirmation(self):
        tasks=await notifications.ensure_instruction_tasks(self.db,'owner',self.instruction)
        self.db.raw.mezan_preparation_pieces_v1.update_one({'piece_id':'p1'},{'$set':{'responsible_employee_id':'b'}})
        projected=await notifications.scoped_instruction(self.db,'owner',self.instruction,'a')
        self.assertEqual(projected['tasks'],[])
        self.assertEqual(projected['proposed_changes'],[])
    async def test_notification_read_cannot_cross_account_and_does_not_confirm_task(self):
        await notifications.emit_instruction_notification(self.db,merchant_id='owner',instruction=self.instruction,event_type='created')
        own=self.db.raw[notifications.NOTIFICATIONS].find_one({'recipient_id':'a'})
        self.select('b'); self.assertEqual(self.client.post('/api/order-tracking-notes/notifications/'+own['id']+'/read').status_code,404)
        self.select('a'); self.assertEqual(self.client.post('/api/order-tracking-notes/notifications/'+own['id']+'/read').status_code,200)
        self.assertEqual(self.db.raw[ORDER_TRACKING_INSTRUCTIONS].find_one({'id':'n'})['status'],'active')
    async def test_reject_only_selected_employee_task(self):
        tasks=await notifications.ensure_instruction_tasks(self.db,'owner',self.instruction)
        self.db.raw[notifications.TASKS].update_many({}, {'$set':{'status':'submitted'}})
        self.db.raw[ORDER_TRACKING_INSTRUCTIONS].update_one({'id':'n'},{'$set':{'status':'waiting_customer_service_approval'}})
        self.select('cs')
        r=self.client.post('/api/order-tracking-notes/instructions/n/reject',json={'note':'redo','task_ids':[tasks[0]['id']]})
        self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(self.db.raw[notifications.TASKS].count_documents({'status':'pending'}),1)
        self.assertEqual(self.db.raw[notifications.TASKS].count_documents({'status':'submitted'}),1)

    async def test_only_creator_can_resume_even_owner_cannot(self):
        await notifications.ensure_instruction_tasks(self.db,'owner',self.instruction)
        self.db.raw[notifications.TASKS].update_many({}, {'$set':{'status':'submitted'}})
        self.db.raw[ORDER_TRACKING_INSTRUCTIONS].update_one({'id':'n'},{'$set':{'status':'waiting_customer_service_approval'}})
        self.select('owner')
        for action in ['approve','complete']:
            r=self.client.post('/api/order-tracking-notes/instructions/n/'+action,json={'note':'resume'})
            self.assertEqual(r.status_code,403,r.text)
        self.assertEqual(self.db.raw[ORDER_TRACKING_INSTRUCTIONS].find_one({'id':'n'})['status'],'waiting_customer_service_approval')
        self.select('cs')
        r=self.client.post('/api/order-tracking-notes/instructions/n/approve',json={'note':'resume'})
        self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(r.json()['instruction']['status'],'completed')
        self.assertEqual(self.db.raw[notifications.NOTIFICATIONS].count_documents({'event_type':'approved','recipient_id':{'$in':['a','b']}}),2)

    async def test_native_bridge_cannot_impersonate_owner(self):
        self.user={'id':'owner','role':'owner','_session_client':'amasi_mobile',
            '_mobile_actor_id':'a','_mobile_owner_id':'owner'}
        r=self.client.get('/api/order-tracking-notes/instructions/n')
        self.assertEqual(r.status_code,200,r.text)
        self.assertFalse(r.json()['capabilities']['can_manage'])
        self.assertEqual([task['piece_id'] for task in r.json()['instruction']['tasks']],['p1'])

    async def test_future_stage_cannot_be_spoofed_or_submitted(self):
        self.db.raw[ORDER_TRACKING_INSTRUCTIONS].update_one({'id':'n'},{'$set':{'target_stages':['assembly_labeling'],'activation_state':'scheduled'}})
        self.select('cs')
        r=self.client.get('/api/order-tracking-notes/instructions/n')
        self.assertFalse(r.json()['capabilities']['can_submit_tasks'])
        self.assertEqual(r.json()['instruction']['activation_state'],'scheduled')
        self.select('a')
        r=self.client.get('/api/order-tracking-notes/orders/1/instructions/active?stage=assembly_labeling')
        self.assertEqual(r.json()['items'],[])

    async def test_background_refresh_scoped_and_idempotent(self):
        await notifications.refresh_actor_instructions(self.db,merchant_id='owner',actor_id='a')
        await notifications.refresh_actor_instructions(self.db,merchant_id='owner',actor_id='a')
        self.assertEqual(self.db.raw[notifications.NOTIFICATIONS].count_documents({'recipient_id':'a'}),1)
        self.assertEqual(self.db.raw[notifications.NOTIFICATIONS].count_documents({'recipient_id':'b'}),0)
        feed=await notifications.notification_feed(self.db,merchant_id='owner',actor_id='a')
        self.assertEqual([task['piece_id'] for task in feed['items'][0]['instruction']['tasks']],['p1'])

    async def test_general_ack_tracks_piece_stage_without_submission(self):
        self.db.raw[ORDER_TRACKING_INSTRUCTIONS].update_one({'id':'n'}, {'$set':{
            'action_type':'general','task_protocol':None,'thread_protocol':1,'approval_required':False,
            'target_stages':['preparation','assembly_labeling'],'enforcement':'notice'}})
        r=self.client.post('/api/order-tracking-notes/instructions/n/acknowledge')
        self.assertEqual(r.status_code,200,r.text)
        task=self.db.raw[notifications.TASKS].find_one({'assignee_id':'a'})
        self.assertEqual(task['acknowledged_by'],'a')
        self.assertEqual(task['stage'],'preparation')
        self.assertEqual(task['status'],'pending')
        self.assertEqual(self.db.raw[ORDER_TRACKING_INSTRUCTIONS].find_one({'id':'n'})['status'],'active')
        self.assertEqual(self.db.raw[notifications.NOTIFICATIONS].count_documents({'event_type':'acknowledged','recipient_id':'cs'}),1)

    async def test_generic_hold_release_cannot_bypass_creator_protocol(self):
        from fulfillment_experiment_routes import make_fulfillment_experiment_router, FULFILLMENT_HOLDS
        self.db.raw[FULFILLMENT_HOLDS].insert_one({'id':'h','user_id':'owner','source':'customer_service_order_tracking','instruction_id':'n','status':'active'})
        async def owner(): return {'id':'owner','role':'owner'}
        app=FastAPI(); app.include_router(make_fulfillment_experiment_router(self.db,owner),prefix='/api')
        with TestClient(app) as client:
            r=client.post('/api/fulfillment-experiments-v1/holds/h/release',json={'note':'bypass'})
        self.assertEqual(r.status_code,409,r.text)
        self.assertEqual(self.db.raw[FULFILLMENT_HOLDS].find_one({'id':'h'})['status'],'active')

    async def test_other_customer_service_cannot_read_evidence(self):
        self.db.raw.users.insert_one({'id':'cs-other','created_by':'owner','role':'customer_service'})
        self.db.raw[routes.ORDER_TRACKING_EVIDENCE].insert_one({'id':'e','user_id':'owner','instruction_id':'n','created_by':'a','content':b'private','content_type':'image/jpeg'})
        self.select('cs-other')
        r=self.client.get('/api/order-tracking-notes/instructions/n')
        self.assertEqual(r.status_code,404,r.text)
        self.assertEqual(self.client.get('/api/order-tracking-notes/evidence/e').status_code,404)
        self.select('cs')
        self.assertEqual(self.client.get('/api/order-tracking-notes/evidence/e').status_code,200)

    async def test_create_general_note_cannot_request_execution_gate(self):
        from types import SimpleNamespace
        self.select('cs')
        order=SimpleNamespace(items=[SimpleNamespace(order_item_id='i1'),SimpleNamespace(order_item_id='i2')])
        with patch.object(routes,'get_order',AsyncMock(return_value=order)):
            r=self.client.post('/api/order-tracking-notes/orders/1/instructions',json={
                'scope':'order','action_type':'general','note':'urgent note',
                'target_stages':['current_stage'],'enforcement':'completion_required',
                'approval_required':True,'required_action':'write_result','priority':'urgent'})
        self.assertEqual(r.status_code,201,r.text)
        row=r.json()['instruction']
        self.assertEqual(row['enforcement'],'notice')
        self.assertFalse(row['approval_required'])
        self.assertFalse(row['operational_hold'])
        self.assertEqual(row['required_action'],'none')
        self.assertEqual(self.db.raw.mezan_preparation_pieces_v1.count_documents({'status':'in_progress'}),2)

    async def test_create_future_stop_preserves_selected_stage_and_does_not_pause_now(self):
        from types import SimpleNamespace
        self.select('cs')
        order=SimpleNamespace(items=[SimpleNamespace(order_item_id='i1'),SimpleNamespace(order_item_id='i2')])
        with patch.object(routes,'get_order',AsyncMock(return_value=order)):
            r=self.client.post('/api/order-tracking-notes/orders/1/instructions',json={
                'scope':'order','action_type':'cancel_order','note':'stop later',
                'target_stages':['assembly_labeling']})
        self.assertEqual(r.status_code,201,r.text)
        row=r.json()['instruction']
        self.assertEqual(row['target_stages'],['assembly_labeling'])
        self.assertEqual(row['activation_state'],'scheduled')
        self.assertFalse(row['operational_hold'])
        self.assertEqual(self.db.raw.mezan_preparation_pieces_v1.count_documents({'status':'in_progress'}),2)
        self.assertEqual(self.db.raw[notifications.NOTIFICATIONS].count_documents({'instruction_id':row['id']}),0)

    async def test_pending_review_claim_is_explicit_and_does_not_release(self):
        self.db.raw.mezan_preparation_pieces_v1.delete_many({})
        self.db.raw.order_review_workflows.update_one({}, {'$set':{'stage':'pending_review'}})
        self.db.raw[ORDER_TRACKING_INSTRUCTIONS].update_one({'id':'n'},{'$set':{'target_stages':['pending_review'],'activation_state':'active'}})
        self.select('owner')
        r=self.client.get('/api/order-tracking-notes/instructions/n')
        self.assertTrue(r.json()['capabilities']['can_claim_task'])
        self.assertEqual(self.db.raw[notifications.TASKS].find_one({})['assignee_id'],'')
        r=self.client.post('/api/order-tracking-notes/instructions/n/tasks/claim')
        self.assertEqual(r.status_code,200,r.text)
        task=r.json()['instruction']['tasks'][0]
        self.assertEqual(task['assignee_id'],'owner')
        self.assertEqual(self.client.post('/api/order-tracking-notes/instructions/n/tasks/claim').status_code,409)
        r=self.client.post('/api/order-tracking-notes/instructions/n/tasks/submit',json={'task_ids':[task['id']],'confirmation':'removed_from_preparation'})
        self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(self.db.raw[ORDER_TRACKING_INSTRUCTIONS].find_one({'id':'n'})['status'],'waiting_customer_service_approval')
        self.assertEqual(self.client.post('/api/order-tracking-notes/instructions/n/approve',json={}).status_code,403)

    async def test_same_employee_gets_fresh_note_notification_at_next_stage(self):
        self.db.raw[ORDER_TRACKING_INSTRUCTIONS].update_one({'id':'n'},{'$set':{
            'action_type':'urgent_preparation','task_protocol':None,'thread_protocol':1,
            'target_stages':['preparation','assembly_labeling'],'enforcement':'notice','approval_required':False}})
        await notifications.refresh_actor_instructions(self.db,merchant_id='owner',actor_id='a')
        self.db.raw.mezan_preparation_pieces_v1.update_one({'piece_id':'p1'},{'$set':{'status':'ready_for_assembly'}})
        self.db.raw.order_review_workflows.update_one({}, {'$set':{'claimed_by':'a'}})
        await notifications.refresh_actor_instructions(self.db,merchant_id='owner',actor_id='a')
        await notifications.refresh_actor_instructions(self.db,merchant_id='owner',actor_id='a')
        self.assertEqual(self.db.raw[notifications.NOTIFICATIONS].count_documents({'recipient_id':'a'}),2)
        active=self.db.raw[notifications.TASKS].find_one({'assignee_id':'a','current':True})
        self.assertEqual(active['stage'],'assembly_labeling')
        self.assertNotIn('acknowledged_at',active)

    async def test_inbox_classification_unique_and_read_does_not_close(self):
        await notifications.ensure_instruction_tasks(self.db,'owner',self.instruction)
        for event in ['created','submitted']:
            await notifications.emit_instruction_notification(self.db,merchant_id='owner',instruction=self.instruction,event_type=event)
        r=self.client.get('/api/order-tracking-notes/instructions/inbox?status=active')
        self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(r.json()['active_count'],1)
        self.assertEqual([row['id'] for row in r.json()['items']],['n'])
        self.db.raw[notifications.NOTIFICATIONS].update_many({}, {'$set':{'read_at':datetime.now(timezone.utc)}})
        r=self.client.get('/api/order-tracking-notes/instructions/inbox?status=active')
        self.assertEqual(r.json()['active_count'],1)
        self.assertEqual(self.client.get('/api/order-tracking-notes/instructions/inbox?status=completed').json()['items'],[])

    async def test_completed_inbox_pagination_does_not_repeat(self):
        self.select('cs')
        for number in range(105):
            self.db.raw[ORDER_TRACKING_INSTRUCTIONS].insert_one({'id':f'old-{number:03d}', 'user_id':'owner', 'created_by':'cs', 'status':'completed', 'created_at':datetime(2025,1,1,tzinfo=timezone.utc)})
        first=self.client.get('/api/order-tracking-notes/instructions/inbox?status=completed').json()
        self.assertEqual(len(first['items']),100)
        second=self.client.get('/api/order-tracking-notes/instructions/inbox',params={'status':'completed','cursor':first['next_cursor']}).json()
        self.assertEqual(len(second['items']),5)
        self.assertFalse({row['id'] for row in first['items']} & {row['id'] for row in second['items']})
        self.assertIsNone(second['next_cursor'])

    async def test_resolution_intent_blocks_interleaved_reject_and_recovers_same_creator(self):
        from fastapi import HTTPException
        tasks=await notifications.ensure_instruction_tasks(self.db,'owner',self.instruction)
        self.db.raw[notifications.TASKS].update_many({}, {'$set':{'status':'submitted'}})
        self.db.raw[ORDER_TRACKING_INSTRUCTIONS].update_one({'id':'n'},{'$set':{'status':'waiting_customer_service_approval'}})
        self.select('cs')
        reject=next(route.endpoint for route in self.router.routes if getattr(route,'path','').endswith('/instructions/{instruction_id}/reject'))
        saw_conflict=[]
        async def interleaved_release(*args,**kwargs):
            try:
                await reject('n',routes.InstructionApprovalRequest(note='competing rejection',task_ids=[tasks[0]['id']]),self.user)
            except HTTPException as exc:
                saw_conflict.append(exc.status_code)
            raise RuntimeError('simulated release interruption')
        with patch.object(routes,'_release_linked_hold',side_effect=interleaved_release):
            with self.assertRaises(RuntimeError):
                self.client.post('/api/order-tracking-notes/instructions/n/approve',json={})
        self.assertEqual(saw_conflict,[409])
        current=self.db.raw[ORDER_TRACKING_INSTRUCTIONS].find_one({'id':'n'})
        self.assertEqual(current['status'],'waiting_customer_service_approval')
        self.assertEqual(current['resolution_intent']['kind'],'resume')
        self.assertEqual(self.db.raw[notifications.TASKS].count_documents({'status':'pending'}),0)
        r=self.client.post('/api/order-tracking-notes/instructions/n/approve',json={})
        self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(self.db.raw[ORDER_TRACKING_INSTRUCTIONS].find_one({'id':'n'})['status'],'completed')

    async def test_submit_cannot_mutate_during_resolution(self):
        tasks=await notifications.ensure_instruction_tasks(self.db,'owner',self.instruction)
        self.db.raw[ORDER_TRACKING_INSTRUCTIONS].update_one({'id':'n'},{'$set':{'resolution_intent':{'kind':'reject','actor_id':'cs','id':'operation'}}})
        own=next(task for task in tasks if task['assignee_id']=='a')
        r=self.client.post('/api/order-tracking-notes/instructions/n/tasks/submit',json={'task_ids':[own['id']],'confirmation':'removed_from_preparation'})
        self.assertEqual(r.status_code,409,r.text)
        self.assertEqual(self.db.raw[notifications.TASKS].count_documents({'status':'submitted'}),0)

    async def test_creator_closes_note_only_after_acknowledgments(self):
        self.db.raw[ORDER_TRACKING_INSTRUCTIONS].update_one({'id':'n'}, {'$set':{'action_type':'general','task_protocol':None,'thread_protocol':1,'approval_required':False,'enforcement':'notice'}})
        self.select('cs')
        self.assertEqual(self.client.post('/api/order-tracking-notes/instructions/n/complete',json={}).status_code,409)
        for actor in ['a','b']:
            self.select(actor)
            self.assertEqual(self.client.post('/api/order-tracking-notes/instructions/n/acknowledge').status_code,200)
            self.assertEqual(self.client.post('/api/order-tracking-notes/instructions/n/complete',json={}).status_code,403)
        self.select('cs')
        r=self.client.post('/api/order-tracking-notes/instructions/n/complete',json={})
        self.assertEqual(r.status_code,200,r.text)
        result=self.client.get('/api/order-tracking-notes/instructions/inbox?status=completed').json()
        self.assertEqual([row['id'] for row in result['items']],['n'])
        self.assertEqual(result['active_count'],0)

if __name__=='__main__': unittest.main()
