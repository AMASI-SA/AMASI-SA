"""HTTP test bridge contracts; provider I/O is synthetic, Mongo via mongomock."""
import asyncio
import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock
from urllib.request import Request

import mongomock
import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

import test_salla_order_item_amasi_test_orders as fixture
import order_revision_console as runtime
from order_revision_console_routes import make_order_revision_console_router


class AsyncCollection:
    def __init__(self, collection):
        self.collection = collection

    def find(self, *args, **kwargs):
        cursor = self.collection.find(*args, **kwargs)
        class Cursor:
            async def to_list(self, length):
                return list(cursor.limit(length))
        return Cursor()

    def __getattr__(self, name):
        async def call(*args, **kwargs):
            return getattr(self.collection, name)(*args, **kwargs)
        return call


class AsyncDb:
    def __init__(self):
        self.raw = mongomock.MongoClient(tz_aware=True).db

    def __getitem__(self, key):
        return AsyncCollection(self.raw[key])


class ProviderAdapter:
    def __init__(self, fake):
        self.fake = fake
        self.fail_after_write = False
        self.pause_write = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        pass

    async def request(self, method, path, body=None):
        runtime.validate_request(method, path)
        request = Request(runtime.c.OFFICIAL_BASE_URL + path,
            data=json.dumps(body).encode() if body is not None else None, method=method)
        if method != 'GET' and self.pause_write:
            await self.pause_write.wait()
        if method == 'GET':
            response = self.fake(request, timeout=30)
        else:
            self.fake.calls.append((method, path))
            self.fake.post_seen = True
            response = fixture.base.FakeHttpResponse(fixture.base.ok({'created': True}))
        if method != 'GET' and self.fail_after_write:
            raise TimeoutError('secret provider message must not leak')
        result = {'status': response.status, 'body': json.loads(response.read())}
        if not runtime.c._amasi_response_success(result):
            raise runtime.c.ContractRunnerError('provider_request_rejected')
        if path == '/orders/items?order_id=o0':
            result['body']['pagination'] = {'total': len(result['body']['data']), 'count': len(result['body']['data']), 'currentPage': 1, 'totalPages': 1, 'links': []}
        return result


class ConsoleContracts(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env = mock.patch.dict(os.environ, {'SALLA_ORDER_REVISION_CONSOLE_ENABLED': 'true'})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.db = AsyncDb()
        self.seed, self.case = fixture.live_seed(), fixture.live_case()
        self.http = fixture.LiveHttp(self.seed, evidence_dir=Path(self.tmp.name))
        self.provider = ProviderAdapter(self.http)
        self.service = runtime.ConsoleTests(self.db, lambda *_: self.provider)

    async def prepare(self):
        return await self.service.prepare('owner', self.seed, self.case)

    def writes(self):
        return [method for method, _ in self.http.calls if method != 'GET']

    async def test_add_and_replay_are_one_write_with_incomplete_webhooks(self):
        plan = await self.prepare()
        self.assertEqual(self.writes(), [])
        first = await self.service.execute('owner', plan['plan_id'])
        second = await self.service.execute('owner', plan['plan_id'])
        self.assertEqual(first, second)
        self.assertEqual(self.writes(), ['POST'])
        self.assertEqual(first['state'], 'observed')
        self.assertEqual(first['result']['observed_verdict'], 'PASS')
        self.assertEqual(first['result']['verdict'], 'INCONCLUSIVE')
        self.assertEqual(self.db.raw[runtime.LOCKS].count_documents({}), 0)
        self.assertNotIn('test-customer', json.dumps(first, default=str))

    async def test_shipment_endpoint_is_never_read_and_shipping_does_not_block(self):
        self.http.order['shipping_status'] = 'shipping_ready'
        self.http.order['shipping_number'] = 'synthetic-tracking'
        original = self.provider.request
        async def no_shipments(method, path, body=None):
            self.assertFalse(path.startswith('/shipments'))
            return await original(method, path, body)
        self.provider.request = no_shipments
        plan = await self.prepare()
        self.http.order['shipping_status'] = 'shipped'
        result = await self.service.execute('owner', plan['plan_id'])
        self.assertEqual(result['state'], 'observed', result)
        self.assertEqual(self.writes(), ['POST'])

    async def test_fulfilled_order_drift_blocks_before_write(self):
        for state in ('completed', 'delivering', 'delivered'):
            self.http.order['status'] = {'slug': self.seed['orders'][0]['state']}
            plan = await self.prepare()
            self.http.order['status'] = {'slug': state}
            result = await self.service.execute('owner', plan['plan_id'])
            self.assertEqual(result['state'], 'blocked')
            self.assertEqual(result['result']['code'], 'order_fulfillment_blocks_revision')
            self.assertEqual(self.writes(), [])

    async def test_unknown_outcome_retains_lock_and_cannot_replay(self):
        plan = await self.prepare()
        self.provider.fail_after_write = True
        result = await self.service.execute('owner', plan['plan_id'])
        self.assertEqual(result['state'], 'unknown')
        self.assertNotIn('secret provider', json.dumps(result, default=str))
        await self.service.execute('owner', plan['plan_id'])
        self.assertEqual(self.writes(), ['POST'])
        with self.assertRaisesRegex(runtime.c.ContractRunnerError, 'order_attempt_locked'):
            await self.prepare()
        self.assertEqual(self.db.raw[runtime.LOCKS].count_documents({}), 1)

    async def test_concurrent_execute_and_second_plan_cannot_double_write(self):
        one, two = await self.prepare(), await self.prepare()
        pause = self.provider.pause_write = asyncio.Event()
        task = asyncio.create_task(self.service.execute('owner', one['plan_id']))
        await asyncio.sleep(0)
        repeated = await self.service.execute('owner', one['plan_id'])
        blocked = await self.service.execute('owner', two['plan_id'])
        self.assertEqual(repeated['state'], 'in_flight')
        self.assertEqual(blocked['state'], 'blocked')
        pause.set()
        await task
        self.assertEqual(self.writes(), ['POST'])

    async def test_disabled_is_no_provider_io(self):
        with mock.patch.dict(os.environ, {'SALLA_ORDER_REVISION_CONSOLE_ENABLED': 'false'}):
            with self.assertRaisesRegex(runtime.c.ContractRunnerError, 'disabled'):
                await self.prepare()
        self.assertEqual(self.http.calls, [])

    async def test_wrong_owner_cannot_read_or_execute(self):
        plan = await self.prepare()
        for action in (self.service.read, self.service.execute):
            with self.assertRaisesRegex(runtime.c.ContractRunnerError, 'plan_not_found'):
                await action('other', plan['plan_id'])
        self.assertEqual(self.writes(), [])

    async def test_expired_plan_is_not_executed(self):
        plan = await self.prepare()
        self.db.raw[runtime.PLANS].update_one({'_id': plan['plan_id']}, {'$set': {'expires_at': runtime.now() - runtime.timedelta(seconds=1)}})
        await self.service.execute('owner', plan['plan_id'])
        self.assertEqual(self.writes(), [])

    async def test_changed_payment_blocks_before_write(self):
        plan = await self.prepare()
        self.http.order['paid_amount'] = 1
        result = await self.service.execute('owner', plan['plan_id'])
        self.assertEqual(result['state'], 'blocked')
        self.assertEqual(self.writes(), [])
        self.assertEqual(self.db.raw[runtime.LOCKS].count_documents({}), 0)

    async def test_original_line_cannot_be_mutated(self):
        await self.prepare()
        self.case = fixture.live_case(method='PUT', path='/orders/items/i0', body={'order_id': 'o0', 'quantity': 2})
        with self.assertRaises(runtime.c.ContractRunnerError):
            await self.prepare()
        self.assertEqual(self.writes(), [])

    async def test_fixture_cannot_switch_order_or_receipt(self):
        await self.prepare()
        self.seed['orders'][0]['test_customer_id'] = 'other'
        with self.assertRaisesRegex(runtime.c.ContractRunnerError, 'fixture_policy_changed'):
            await self.prepare()
        self.assertEqual(self.writes(), [])

    async def test_failed_postconditions_quarantine(self):
        plan = await self.prepare()
        self.http.order_after_total = 115
        result = await self.service.execute('owner', plan['plan_id'])
        self.assertEqual(result['state'], 'quarantined')
        self.assertEqual(result['result']['observed_verdict'], 'FAIL')
        self.assertEqual(self.db.raw[runtime.LOCKS].count_documents({}), 1)

    async def test_quantity_and_delete_of_recorded_added_line(self):
        for method in ('PUT', 'DELETE'):
            with self.subTest(method=method):
                self.db = AsyncDb()
                self.seed = fixture.live_seed()
                self.seed['orders'][0]['item_id'] = 'i-new'
                self.case = fixture.live_case(method=method, path='/orders/items/i-new', body={'order_id': 'o0', 'quantity': 2} if method == 'PUT' else {})
                self.http = fixture.LiveHttp(self.seed, evidence_dir=Path(self.tmp.name), case=self.case)
                self.provider = ProviderAdapter(self.http)
                self.service = runtime.ConsoleTests(self.db, lambda *_: self.provider)
                self.db.raw[runtime.FIXTURES].insert_one({'_id': 'owner', 'policy_digest': runtime.policy_digest(self.seed), 'original_item_ids': ['i0'], 'created_item_ids': ['i-new']})
                plan = await self.prepare()
                result = await self.service.execute('owner', plan['plan_id'])
                self.assertEqual(result['state'], 'observed', result)
                self.assertEqual(self.writes(), [method])

    async def test_ready_piece_requires_confirmation_and_cannot_dispatch(self):
        self.seed['orders'][0]['item_id'] = 'i-new'
        self.case = fixture.live_case(method='DELETE', path='/orders/items/i-new', body={})
        self.http = fixture.LiveHttp(self.seed, evidence_dir=Path(self.tmp.name), case=self.case)
        self.provider = ProviderAdapter(self.http)
        self.service = runtime.ConsoleTests(self.db, lambda *_: self.provider)
        self.db.raw[runtime.FIXTURES].insert_one({'_id': 'owner',
            'policy_digest': runtime.policy_digest(self.seed),
            'original_item_ids': ['i0'], 'created_item_ids': ['i-new']})
        self.db.raw['mezan_preparation_pieces_v1'].insert_one({
            'user_id': 'owner', 'order_number': self.seed['orders'][0]['order_number'],
            'order_item_id': 'i-new', 'piece_id': 'piece1', 'status': 'ready_for_employee_receipt'})
        plan = await self.prepare()
        self.assertTrue(plan['preparation']['confirmation_required'])
        result = await self.service.execute('owner', plan['plan_id'])
        self.assertEqual(result['result']['code'], 'ready_item_customer_confirmation_required')
        self.assertEqual(self.writes(), [])

    async def test_text_options_of_recorded_added_line(self):
        self.seed['orders'][0]['item_id'] = 'i-new'
        self.seed['products'][0].update(kind='text_option', option_ids=['custom-name'],
            option_value_tuples=[{'option_id': 'custom-name', 'value_kind': 'text'}])
        self.case = fixture.live_case(method='PUT', path='/orders/items/i-new',
            body={'order_id': 'o0', 'options': [{'option_id': 'custom-name', 'value': 'TEST NEW'}]})
        self.http = fixture.LiveHttp(self.seed, evidence_dir=Path(self.tmp.name), case=self.case)
        self.http.options_before = [{'option_id': 'custom-name', 'value': 'TEST OLD'}]
        self.provider = ProviderAdapter(self.http)
        self.service = runtime.ConsoleTests(self.db, lambda *_: self.provider)
        self.db.raw[runtime.FIXTURES].insert_one({'_id': 'owner',
            'policy_digest': runtime.policy_digest(self.seed),
            'original_item_ids': ['i0'], 'created_item_ids': ['i-new']})
        plan = await self.prepare()
        result = await self.service.execute('owner', plan['plan_id'])
        self.assertEqual(result['state'], 'observed', result)
        self.assertEqual(result['result']['observed_verdict'], 'PASS')
        self.assertEqual(self.writes(), ['PUT'])

    async def test_courier_and_synthetic_receipt_are_checked_in_http_workflow(self):
        self.seed = fixture.courier_seed()
        receipt = 'https://test.invalid/synthetic.png'
        self.seed['orders'][0]['synthetic_receipt'] = {'owner_confirmed': True, 'receipt_image_sha256': runtime.c.canonical_digest(receipt)}
        fixture.configure_pending_courier(self.http)
        self.http.order['receipt_image'] = receipt
        plan = await self.prepare()
        self.http.order['receipt_image'] = 'https://test.invalid/other.png'
        result = await self.service.execute('owner', plan['plan_id'])
        self.assertEqual(result['state'], 'blocked')
        self.assertEqual(self.writes(), [])

    async def test_immutable_intent_is_persisted_before_dispatch(self):
        plan = await self.prepare()
        original_request = self.provider.request
        async def request(method, path, body=None):
            if method != 'GET':
                record = self.db.raw[runtime.PLANS].find_one({'_id': plan['plan_id']})
                self.assertEqual(record['state'], 'in_flight')
                self.assertEqual(record['case'], self.case)
                self.assertEqual(self.db.raw[runtime.LOCKS].count_documents({'plan_id': plan['plan_id']}), 1)
            return await original_request(method, path, body)
        self.provider.request = request
        result = await self.service.execute('owner', plan['plan_id'])
        self.assertEqual(result['state'], 'observed')

    async def test_provider_rejects_failure_envelopes_and_redirect_without_retry(self):
        for status, body in [(401, {'success': False}), (200, {'success': False}), (307, {'success': True})]:
            with self.subTest(status=status):
                requests = []
                def handler(request):
                    requests.append(request)
                    return httpx.Response(status, json=body, headers={'location': 'https://test.invalid'})
                provider = runtime.Provider(None, 'owner', 'store')
                provider.token = 'synthetic-token'
                async with httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False) as client:
                    provider.client = client
                    with self.assertRaises(runtime.c.ContractRunnerError):
                        await provider.request('POST', '/orders/items', {})
                self.assertEqual(len(requests), 1)

    async def test_pagination_missing_or_incomplete_rejected(self):
        for page in (None, {}, {'total': 2, 'count': 1, 'currentPage': 1, 'totalPages': 2}):
            with self.subTest(page=page), self.assertRaises(runtime.c.ContractRunnerError):
                runtime.complete_items({'body': {'data': [{'id': 'a'}], 'pagination': page}})


class RouteContracts(unittest.TestCase):
    def client(self, user):
        app = FastAPI()
        self.service = mock.Mock()
        self.service.prepare = mock.AsyncMock(return_value={'state': 'prepared'})
        self.service.read = mock.AsyncMock(side_effect=runtime.c.ContractRunnerError('plan_not_found'))
        self.service.execute = mock.AsyncMock(return_value={'state': 'observed'})
        app.include_router(make_order_revision_console_router(None, lambda: user, service_factory=lambda _: self.service), prefix='/api')
        return TestClient(app)

    def test_staff_denied_on_all_routes(self):
        client = self.client({'id': 'staff', 'role': 'employee'})
        for method, path, body in [('GET', '/status', None), ('POST', '/plans', {'manifest': {}, 'case': {}}), ('GET', '/plans/a', None), ('POST', '/plans/a/execute', None)]:
            response = client.request(method, '/api/order-revision-tests' + path, json=body)
            self.assertEqual(response.status_code, 403)
        self.service.prepare.assert_not_called()
        self.service.execute.assert_not_called()

    def test_owner_no_store_and_strict_input(self):
        client = self.client({'id': 'owner', 'role': 'owner'})
        response = client.get('/api/order-revision-tests/status')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers['cache-control'], 'no-store')
        response = client.post('/api/order-revision-tests/plans', json={'manifest': {}, 'case': {}, 'token': 'forbidden'})
        self.assertEqual(response.status_code, 422)
        self.service.prepare.assert_not_called()

    def test_provider_error_not_exposed(self):
        client = self.client({'id': 'owner', 'role': 'owner'})
        self.service.prepare.side_effect = RuntimeError('secret token')
        response = client.post('/api/order-revision-tests/plans', json={'manifest': {}, 'case': {}})
        self.assertEqual(response.status_code, 503)
        self.assertNotIn('secret token', response.text)
