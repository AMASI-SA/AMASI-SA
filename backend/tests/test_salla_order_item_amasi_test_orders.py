"""Contracts for explicit Amasi test-order execution; all I/O is synthetic."""
from __future__ import annotations

import contextlib
import copy
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import test_salla_order_item_contract_runner_p0 as base

runner = base.runner


def live_seed():
    seed = base.seed_data()
    seed.update(classification='AMASI_TEST_ORDER_MANIFEST', store_id='amasi-test-store', downstream_reviewed=True)
    seed['orders'] = [dict(seed['orders'][0], payment_method='bank', test_customer_id='test-customer', disposable_test_order=True, preserve_item_id='i0')]
    seed['products'] = [seed['products'][0]]
    return seed


def live_case(**updates):
    case = base.SallaP0RunnerTests._valid_case(
        classification='AMASI_TEST_ORDER_CASE', disposable_order_confirmed=True,
        client_request_id='amasi-case-1',
    )
    case.update(updates)
    if 'assertions' not in updates:
        before, after = {'POST': (100, 110), 'PUT': (110, 120), 'DELETE': (110, 100)}[case['method']]
        if case['method'] == 'PUT' and 'quantity' not in case['body']:
            after = before
        case['assertions'] = [{'path': 'before.order_total', 'equals': before}, {'path': 'after.order_total', 'equals': after}]
    return case


def unpaid_order():
    return {
        'id': 'o0', 'reference_id': 'N0', 'status': {'slug': 'pending'},
        'customer': {'id': 'test-customer'}, 'payment_method': 'bank',
        'amounts': {'total': {'amount': 100, 'currency': 'SAR'}},
        'payment_actions': {
            'remaining_action': {'has_remaining_amount': True,
                                 'paid_amount': {'amount': 0, 'currency': 'SAR'},
                                 'remaining_amount': {'amount': 100, 'currency': 'SAR'}},
            'refund_action': {'paid_amount': {'amount': 0, 'currency': 'SAR'},
                              'refund_amount': {'amount': 0, 'currency': 'SAR'}},
        },
        'payment_methods': [],
    }


def courier_seed():
    seed = live_seed()
    seed['orders'][0]['pending_store_courier'] = {
        'shipment_id': 'shipment-1', 'courier_id': 'courier-1',
        'not_dispatched_confirmed': True,
    }
    return seed


def configure_pending_courier(http):
    http.order['shipping_status'] = 'shipping_ready'
    http.order['payment_methods'] = [
        {'payment_method': 'bank', 'amount': '0.00', 'provider': None,
         'transaction_reference': None},
    ]
    http.shipments['body'].update(data=[{
        'id': 'shipment-1', 'courier_id': 'courier-1', 'order_id': 'o0',
        'order_reference_id': 'N0', 'status': 'pending', 'type': 'shipment',
        'source': 'dashboard', 'payment_method': 'bank', 'trackable': False,
        'label': None, 'shipping_number': None, 'tracking_number': None,
        'tracking_link': None, 'driver_info': None, 'pickup_id': None,
        'shipping_route': None,
    }], pagination={'total': 1, 'count': 1, 'currentPage': 1, 'totalPages': 1, 'links': []})


class LiveHttp(base.FakeSallaHttp):
    def __init__(self, seed, *, evidence_dir, case=None):
        super().__init__(seed, evidence_dir=evidence_dir)
        self.case = case or live_case()
        self.order = unpaid_order()
        if self.case['method'] != 'POST':
            self.order['amounts']['total']['amount'] = 110
            self.order['payment_actions']['remaining_action']['remaining_amount']['amount'] = 110
        self.responses[('GET', '/store/info')] = base.ok({'id': 'amasi-test-store', 'type': 'live'})
        self.responses[('GET', '/orders/o0')] = base.ok(self.order)
        self.shipments = {'status': 200, 'body': {'success': True, 'data': [],
            'pagination': {'total': 0, 'count': 0, 'currentPage': 1, 'totalPages': 1, 'links': {}}}}
        self.item_calls = 0
        self.on_final_check = None
        self.shipment_reads = 0
        self.options_before = []
        self.order_after_total = 110 if self.case['method'] == 'POST' else (100 if self.case['method'] == 'DELETE' else (120 if 'quantity' in self.case['body'] else 110))
        self.reject_envelope_path = None
        self.http_error_path = None
        self.reject_write_envelope = False
        self.on_items_read = None

    def __call__(self, request, timeout):
        path = request.full_url.split('/admin/v2', 1)[1]
        if path == '/orders/items?order_id=o0' and self.on_items_read:
            self.on_items_read()
        if path == self.http_error_path:
            self.calls.append((request.get_method(), path))
            body = copy.deepcopy(self.responses[(request.get_method(), path)]['body'])
            body.update(success=False, status=403)
            raise runner.urllib.error.HTTPError(request.full_url, 403, 'forbidden', {}, io.BytesIO(json.dumps(body).encode()))
        if path == '/orders/o0' and self.post_seen:
            self.order['amounts']['total']['amount'] = self.order_after_total
            self.order['payment_actions']['remaining_action']['remaining_amount']['amount'] = self.order_after_total
        if path == self.reject_envelope_path:
            payload = copy.deepcopy(self.responses[(request.get_method(), path)])
            payload['body'].update(success=False, status=422)
            self.calls.append((request.get_method(), path))
            return base.FakeHttpResponse(payload)
        if path.startswith('/shipments?'):
            self.calls.append((request.get_method(), path))
            self.shipment_reads += 1
            if self.on_final_check and list(self.evidence_dir.glob('*.intent.json')):
                callback, self.on_final_check = self.on_final_check, None
                callback()
            return base.FakeHttpResponse(self.shipments)
        if request.get_method() == 'GET' and path == '/orders/items?order_id=o0' and self.case['method'] != 'POST':
            self.calls.append(('GET', path))
            items = [{'id': 'i0', 'product_id': 'p0', 'sku': 'SKU0', 'quantity': 1, 'branch_id': 'b1'},
                     {'id': 'i-new', 'product_id': 'p0', 'sku': 'SKU0', 'quantity': 1, 'branch_id': 'b1', 'options': self.options_before}]
            if self.post_seen:
                if self.after_first_hook:
                    callback, self.after_first_hook = self.after_first_hook, None
                    callback()
                if self.case['method'] == 'DELETE':
                    items.pop()
                else:
                    items[-1].update({k: v for k, v in self.case['body'].items() if k in ('quantity', 'options')})
            return base.FakeHttpResponse(base.ok(items))
        response = super().__call__(request, timeout)
        if request.get_method() != 'GET' and self.reject_write_envelope:
            return base.FakeHttpResponse({'status': 200, 'body': {'success': False, 'status': 422, 'data': {}}})
        return response


class AmasiTestOrderTests(unittest.TestCase):
    def test_synthetic_receipt_allows_bound_fixture_operations(self):
        receipt = 'https://test.invalid/synthetic-receipt.png'
        for method in ('POST', 'PUT', 'DELETE'):
            with self.subTest(method=method), tempfile.TemporaryDirectory() as d:
                seed = courier_seed()
                seed['orders'][0]['synthetic_receipt'] = {
                    'owner_confirmed': True, 'receipt_image_sha256': runner.canonical_digest(receipt)}
                case = live_case()
                if method != 'POST':
                    seed['orders'][0]['item_id'] = 'i-new'
                    case = live_case(method=method, path='/orders/items/i-new',
                        body={'order_id': 'o0', 'quantity': 2} if method == 'PUT' else {})
                def configure(http):
                    configure_pending_courier(http)
                    http.order['receipt_image'] = receipt
                rc, output, http, evidence, _ = self.run_cli(
                    Path(d), seed=seed, case=case, change_http=configure, webhook=True)
                self.assertEqual(rc, 0, output)
                self.assertEqual([m for m, _ in http.calls if m != 'GET'], [method])
                self.assertEqual(evidence['verdict'], 'PASS')
                self.assertEqual(http.order['receipt_image'], receipt)
                self.assertNotIn(receipt, json.dumps(evidence))

    def test_synthetic_receipt_rejects_unreviewed_binding_and_payment(self):
        receipt = 'https://test.invalid/synthetic-receipt.png'
        valid = {'owner_confirmed': True, 'receipt_image_sha256': runner.canonical_digest(receipt)}
        cases = [(None, {}), ({}, {}), ({**valid, 'owner_confirmed': False}, {}),
                 ({**valid, 'receipt_image_sha256': '0'*64}, {}),
                 ({**valid, 'receipt_image_sha256': True}, {}),
                 ({**valid, 'allow_paid': True}, {}), (valid, {'receipt_image': None}),
                 (valid, {'receipt_image': {'url': receipt}}), (valid, {'paid_amount': 1}),
                 (valid, {'receipt_url': receipt}), (valid, {'bank': {'receipt_image': receipt}}),
                 (valid, {'transaction_id': 'transaction'})]
        for policy, updates in cases:
            with self.subTest(policy=policy, updates=updates), tempfile.TemporaryDirectory() as d:
                seed = live_seed()
                if policy is not None:
                    seed['orders'][0]['synthetic_receipt'] = policy
                def configure(http):
                    http.order.update(receipt_image=receipt)
                    http.order.update(updates)
                rc, _, http, _, _ = self.run_cli(Path(d), seed=seed, change_http=configure)
                self.assertEqual(rc, 2)
                self.assertFalse(any(m != 'GET' for m, _ in http.calls))

    def test_synthetic_receipt_drift_before_write_blocks_operation(self):
        receipt = 'https://test.invalid/synthetic-receipt.png'
        seed = courier_seed()
        seed['orders'][0]['synthetic_receipt'] = {
            'owner_confirmed': True, 'receipt_image_sha256': runner.canonical_digest(receipt)}
        def configure(http):
            configure_pending_courier(http)
            http.order['receipt_image'] = receipt
            http.on_final_check = lambda: http.order.update(receipt_image='https://test.invalid/replaced.png')
        with tempfile.TemporaryDirectory() as d:
            rc, _, http, _, _ = self.run_cli(Path(d), seed=seed, change_http=configure)
            self.assertEqual(rc, 2)
            self.assertFalse(any(m != 'GET' for m, _ in http.calls))

    def run_cli(self, tmp, *, seed=None, case=None, change_http=None, env_updates=None, args=None, webhook=False):
        seed = seed or live_seed()
        case = case or live_case()
        manifest = tmp / 'seed.json'
        manifest.write_text(json.dumps(seed))
        case_file = tmp / 'case.json'
        case_file.write_text(json.dumps(case))
        env = base.SallaP0RunnerTests._cli_env(tmp, seed)
        env = {k: v for k, v in env.items() if not k.startswith(('SALLA_DEMO_', 'SALLA_SANDBOX_'))}
        env.update({
            'SALLA_AMASI_TEST_STORE_ID': 'amasi-test-store',
            'SALLA_AMASI_TEST_STORE_TYPE': 'live',
            'SALLA_AMASI_TEST_TOKEN_SCOPES': 'orders.read_write,products.read,shipping.read',
            'SALLA_AMASI_TEST_MANIFEST': str(manifest),
            'SALLA_AMASI_TEST_EVIDENCE_DIR': str(tmp / 'evidence'),
            'SALLA_AMASI_TEST_RUN_WRITES': 'true',
        })
        env.update(env_updates or {})
        client = base.FakeMongoClient(base.FakeDb([{'user_id': 'test-owner'}]))
        async def resolver(*_args, **_kwargs):
            return 'secret-live-test-token'
        fake = LiveHttp(seed, evidence_dir=tmp / 'evidence', case=case)
        extra_args = []
        if webhook:
            events_file = tmp / 'events.json'
            events_file.write_text('[]')
            fake.after_first_hook = lambda: events_file.write_text(json.dumps([
                base.SallaP0RunnerTests._webhook_event(store_id='amasi-test-store', correlation_id='amasi-case-1')
            ]))
            extra_args = ['--webhook-events', str(events_file), '--webhook-wait-seconds', '0']
        if change_http:
            change_http(fake)
        output = io.StringIO()
        with (
            mock.patch.dict(os.environ, env, clear=True),
            mock.patch.object(runner, '_canonical_state_root', return_value=tmp / 'state'),
            mock.patch.object(runner, '_runtime_credential_dependencies', return_value=(lambda _: client, resolver)) as dependencies,
            mock.patch.object(runner.urllib.request, 'build_opener') as opener,
            mock.patch.object(runner.urllib.request, 'urlopen', side_effect=AssertionError('live mode must disable redirects')),
            contextlib.redirect_stdout(output),
        ):
            opener.return_value.open.side_effect = fake
            result = runner.main(args or ['run', '--environment', 'amasi-test-orders', '--case-file', str(case_file), *extra_args])
        evidence_files = list((tmp / 'evidence').glob('*.terminal.json'))
        evidence = json.loads(evidence_files[0].read_text()) if evidence_files else None
        return result, output.getvalue(), fake, evidence, dependencies.call_count

    def test_explicit_live_add_uses_one_write_and_distinct_evidence(self):
        with tempfile.TemporaryDirectory() as d:
            rc, output, http, evidence, _ = self.run_cli(Path(d))
            self.assertEqual(rc, 0, output)
            self.assertEqual([m for m, _ in http.calls if m != 'GET'], ['POST'])
            self.assertEqual(evidence['classification'], 'SALLA_LIVE_TEST_ORDER_EVIDENCE')
            self.assertEqual(evidence['observed_verdict'], 'PASS')
            self.assertEqual(evidence['verdict'], 'INCONCLUSIVE')
            self.assertNotIn('secret-live-test-token', json.dumps(evidence))
            schema = json.loads((base.ROOT / 'docs/operations/MZ-ORDER-REVISION-SALLA-001/schemas/evidence-record.schema.json').read_text())
            base.require_draft202012_validator()(schema).validate(evidence)

    def test_reviewed_pending_store_courier_supports_add_update_delete(self):
        for method in ('POST', 'PUT', 'DELETE'):
            with self.subTest(method=method), tempfile.TemporaryDirectory() as d:
                seed = courier_seed()
                case = live_case()
                if method != 'POST':
                    seed['orders'][0]['item_id'] = 'i-new'
                    case = live_case(method=method, path='/orders/items/i-new',
                        body={'order_id': 'o0', 'quantity': 2} if method == 'PUT' else {})
                rc, output, http, evidence, _ = self.run_cli(
                    Path(d), seed=seed, case=case, change_http=configure_pending_courier, webhook=True)
                self.assertEqual(rc, 0, output)
                self.assertEqual([m for m, _ in http.calls if m != 'GET'], [method])
                self.assertEqual(evidence['verdict'], 'PASS', evidence)
                self.assertEqual(evidence['before']['shipment_count'], 1)
                self.assertEqual(evidence['after']['shipment_count'], 1)
                self.assertEqual(evidence['before']['shipment_review']['mode'], 'pending_store_courier')
                self.assertNotIn('shipment-1', json.dumps(evidence['before']['shipment_review']))
                schema = json.loads((base.ROOT / 'docs/operations/MZ-ORDER-REVISION-SALLA-001/schemas/evidence-record.schema.json').read_text())
                base.require_draft202012_validator()(schema).validate(evidence)

    def test_pending_courier_requires_explicit_valid_bound_manifest(self):
        policies = [None, {}, {'shipment_id': 'shipment-1', 'courier_id': 'courier-1', 'not_dispatched_confirmed': False},
                    {'shipment_id': '', 'courier_id': 'courier-1', 'not_dispatched_confirmed': True},
                    {'shipment_id': 'shipment-1', 'courier_id': True, 'not_dispatched_confirmed': True},
                    {**courier_seed()['orders'][0]['pending_store_courier'], 'allow_shipped': True}]
        for policy in policies:
            with self.subTest(policy=policy), tempfile.TemporaryDirectory() as d:
                seed = live_seed()
                if policy is not None:
                    seed['orders'][0]['pending_store_courier'] = policy
                rc, _, http, _, _ = self.run_cli(Path(d), seed=seed, change_http=configure_pending_courier)
                self.assertEqual(rc, 2)
                self.assertFalse(any(m != 'GET' for m, _ in http.calls))

    def test_pending_courier_rejects_identity_dispatch_and_incomplete_page(self):
        fields = [('id', 'other-shipment'), ('courier_id', 'other-courier'), ('order_id', 'other-order'),
                  ('order_reference_id', 'other-reference'), ('status', 'delivering'), ('status', 'delivered'),
                  ('status', 'unknown'), ('type', 'return'), ('source', 'api'), ('payment_method', 'cod'),
                  ('trackable', True), ('trackable', 0), ('label', []), ('label', 'label-url'),
                  ('shipping_number', '123'), ('tracking_number', '123'), ('tracking_link', 'tracking-url'),
                  ('driver_info', {}), ('pickup_id', 'pickup'), ('shipping_route', 'route'),
                  ('shipped_at', 'now'), ('dispatched_at', 'now'), ('handed_over_at', 'now'), ('delivered_at', 'now'),
                  ('waybill_number', '123'), ('awb', '123'), ('label_url', 'url'), ('pdf_label', 'url'),
                  ('pdf_url', 'url'), ('documents', ['url']), ('tracking_url', 'url')]
        def shipment_field(key, value):
            return lambda h: h.shipments['body']['data'][0].update({key: value})
        mutations = [shipment_field(*pair) for pair in fields]
        mutations += [
            lambda h: h.shipments['body']['data'][0].pop('label'),
            lambda h: h.shipments['body'].update(data=[]),
            lambda h: h.shipments['body']['data'].append(copy.deepcopy(h.shipments['body']['data'][0])),
            lambda h: h.shipments['body']['pagination'].update(total=2, totalPages=2),
            lambda h: h.shipments['body']['pagination'].update(total=True),
            lambda h: h.shipments['body']['pagination'].update(links={'next': 'url'}),
            lambda h: h.shipments['body']['pagination'].update(links=['url']),
            lambda h: h.shipments['body'].update(success=False),
            lambda h: h.order.update(shipping_status='shipped'),
        ]
        for mutation in mutations:
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as d:
                def configure(http):
                    configure_pending_courier(http)
                    mutation(http)
                rc, _, http, _, _ = self.run_cli(Path(d), seed=courier_seed(), change_http=configure)
                self.assertEqual(rc, 2)
                self.assertFalse(any(m != 'GET' for m, _ in http.calls))

    def test_zero_bank_method_is_not_a_payment_but_other_rows_are_rejected(self):
        good = {'payment_method': 'bank', 'amount': '0.00', 'provider': None, 'transaction_reference': None}
        with tempfile.TemporaryDirectory() as d:
            rc, output, _, evidence, _ = self.run_cli(Path(d), change_http=lambda h: h.order.update(payment_methods=[good]))
            self.assertEqual(rc, 0, output)
            self.assertEqual(evidence['observed_verdict'], 'PASS')
        rows = [None, {}, [good, good], [dict(good, amount=1)], [dict(good, amount=False)],
                [dict(good, amount='NaN')], [dict(good, payment_method='cod')],
                [dict(good, provider='bank-provider')], [dict(good, transaction_reference='transfer')],
                [dict(good, paid_at='now')], [{'payment_method': 'bank', 'amount': 0}]]
        for methods in rows:
            with self.subTest(methods=methods), tempfile.TemporaryDirectory() as d:
                rc, _, http, _, _ = self.run_cli(Path(d), change_http=lambda h: h.order.update(payment_methods=methods))
                self.assertEqual(rc, 2)
                self.assertFalse(any(m != 'GET' for m, _ in http.calls))

    def test_pending_courier_does_not_override_receipt_or_paid_balance(self):
        for updates in ({'receipt_image': 'https://test.invalid/receipt'}, {'paid_amount': 1}):
            with self.subTest(updates=updates), tempfile.TemporaryDirectory() as d:
                def configure(http):
                    configure_pending_courier(http)
                    http.order.update(updates)
                rc, _, http, _, _ = self.run_cli(Path(d), seed=courier_seed(), change_http=configure)
                self.assertEqual(rc, 2)
                self.assertFalse(any(m != 'GET' for m, _ in http.calls))

    def test_pending_courier_dispatch_during_final_items_read_blocks_write(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            def configure(http):
                configure_pending_courier(http)
                def drift():
                    if list((tmp / 'evidence').glob('*.intent.json')):
                        http.shipments['body']['data'][0]['status'] = 'delivering'
                http.on_items_read = drift
            rc, _, http, evidence, _ = self.run_cli(tmp, seed=courier_seed(), change_http=configure)
            self.assertEqual(rc, 2)
            self.assertFalse(any(m != 'GET' for m, _ in http.calls))
            self.assertEqual(evidence['attempt_outcome'], 'UNKNOWN')

    def test_pending_courier_postwrite_dispatch_is_not_a_pass_or_retried(self):
        with tempfile.TemporaryDirectory() as d:
            def configure(http):
                configure_pending_courier(http)
                http.after_first_hook = lambda: http.shipments['body']['data'][0].update(status='delivering')
            _, _, http, evidence, _ = self.run_cli(Path(d), seed=courier_seed(), change_http=configure)
            self.assertNotEqual(evidence['observed_verdict'], 'PASS')
            self.assertEqual([m for m, _ in http.calls if m != 'GET'], ['POST'])

    def test_empty_pagination_links_array_does_not_imply_another_page(self):
        with tempfile.TemporaryDirectory() as d:
            rc, output, _, evidence, _ = self.run_cli(Path(d),
                change_http=lambda h: h.shipments['body']['pagination'].update(links=[]))
            self.assertEqual(rc, 0, output)
            self.assertEqual(evidence['observed_verdict'], 'PASS')

    def test_live_update_and_delete_preserve_original_line(self):
        for method in ('PUT', 'DELETE'):
            with self.subTest(method=method), tempfile.TemporaryDirectory() as d:
                seed = live_seed()
                seed['orders'][0]['item_id'] = 'i-new'
                body = {'order_id': 'o0', 'quantity': 2} if method == 'PUT' else {}
                case = live_case(method=method, path='/orders/items/i-new', body=body)
                rc, output, http, evidence, _ = self.run_cli(Path(d), seed=seed, case=case)
                self.assertEqual(rc, 0, output)
                self.assertEqual([m for m, _ in http.calls if m != 'GET'], [method])
                self.assertEqual(evidence['observed_verdict'], 'PASS', evidence)
                self.assertEqual(evidence['after']['items'][0]['item_id'], 'i0')

    def test_live_invalid_case_is_rejected_before_credentials(self):
        invalid = [
            live_case(order_id='another-order'),
            live_case(retry_once=True),
            live_case(disposable_order_confirmed=False),
            live_case(classification='SANDBOX_CASE_TEMPLATE'),
            live_case(body={'order_id': 'o0', 'product_id': 'p0', 'branch_id': 'b1', 'quantity': 3}),
            live_case(body={'order_id': 'o0', 'product_id': 'p0', 'branch_id': 'b1', 'quantity': 1, 'price': 0}),
            live_case(method='DELETE', path='/orders/items/i0', body={}),
            live_case(method='PUT', path='/orders/items/i0', body={'order_id': 'o0', 'quantity': 2}),
            live_case(assertions=[]),
        ]
        for case in invalid:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as d:
                rc, _, http, evidence, deps = self.run_cli(Path(d), case=case)
                self.assertEqual(rc, 2)
                self.assertEqual(deps, 0)
                self.assertEqual(http.calls, [])
                self.assertIsNone(evidence)

    def test_missing_live_config_never_falls_back_to_demo(self):
        with tempfile.TemporaryDirectory() as d:
            rc, output, http, _, deps = self.run_cli(Path(d), env_updates={'SALLA_AMASI_TEST_STORE_ID': '', 'SALLA_DEMO_STORE_ID': 'demo-1'})
            self.assertEqual(rc, 2)
            self.assertIn('AMASI_TEST_NOT_CONFIGURED', output)
            self.assertEqual(deps, 0)
            self.assertEqual(http.calls, [])

    def test_live_rejects_mismatched_store_order_customer_paid_cod_and_shipments(self):
        def mutate_order(**fields):
            return lambda h: h.order.update(fields)
        mutations = [
            lambda h: h.responses[('GET', '/store/info')]['body']['data'].update(id='other-store'),
            lambda h: h.responses[('GET', '/store/info')]['body']['data'].update(type='demo'),
            mutate_order(reference_id='not-the-reviewed-order'),
            mutate_order(customer={'id': 'a-real-customer'}),
            mutate_order(status={'slug': 'completed'}),
            mutate_order(payment_method='cod'),
            mutate_order(paid_amount=1),
            mutate_order(paid_amount=True),
            mutate_order(payment_status={'slug': 'paid'}),
            mutate_order(payment_collection_status='paid'),
            mutate_order(payment_receipt_url='https://test.invalid/receipt'),
            mutate_order(payment={'proof_url': 'https://test.invalid/receipt'}),
            mutate_order(payment={'paid_at': '2026-09-16T00:00:00Z'}),
            mutate_order(payment={'reference': 'transaction-reference'}),
            mutate_order(shipping_status='shipped'),
            mutate_order(bank={'receipt_image': 'https://test.invalid/receipt'}),
            mutate_order(remaining_amount=0),
            mutate_order(payment_actions={}),
            lambda h: h.order['payment_actions']['remaining_action']['paid_amount'].update(amount=1),
            lambda h: h.order['payment_actions']['remaining_action']['paid_amount'].update(amount='NaN'),
            lambda h: h.order['payment_actions']['remaining_action']['paid_amount'].update(currency='QAR'),
            lambda h: h.order['payment_actions']['refund_action']['paid_amount'].update(amount=1),
            lambda h: h.order['payment_actions']['refund_action'].update(has_refund_amount=True),
            lambda h: h.order['payment_actions']['refund_action'].update(pending_refund_amount=1),
            lambda h: h.order['payment_actions']['remaining_action']['remaining_amount'].update(amount=99),
            lambda h: h.shipments['body'].update(data=[{'id': 'shipment-1'}]),
            lambda h: h.shipments['body'].pop('pagination'),
            lambda h: h.shipments['body']['pagination'].update(total=True),
            lambda h: h.shipments['body']['pagination'].update(links={'next': 'https://test.invalid/page2'}),
        ]
        for mutation in mutations:
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as d:
                rc, _, http, _, _ = self.run_cli(Path(d), change_http=mutation)
                self.assertEqual(rc, 2)
                self.assertEqual([m for m, _ in http.calls if m != 'GET'], [])

    def test_live_collects_new_correlated_webhook(self):
        with tempfile.TemporaryDirectory() as d:
            rc, output, _, evidence, _ = self.run_cli(Path(d), webhook=True)
            self.assertEqual(rc, 0, output)
            self.assertEqual(evidence['verdict'], 'PASS')
            self.assertEqual(len(evidence['webhook_events']), 1)

    def test_live_updates_text_options_with_real_postcondition_check(self):
        with tempfile.TemporaryDirectory() as d:
            seed = live_seed()
            seed['orders'][0]['item_id'] = 'i-new'
            seed['products'][0].update(kind='text_option', option_ids=['custom-name'],
                                       option_value_tuples=[{'option_id': 'custom-name', 'value_kind': 'text'}])
            case = live_case(method='PUT', path='/orders/items/i-new',
                             body={'order_id': 'o0', 'options': [{'option_id': 'custom-name', 'value': 'TEST NEW'}]})
            def configure(http):
                http.options_before = [{'option_id': 'custom-name', 'value': 'TEST OLD'}]
            rc, output, _, evidence, _ = self.run_cli(Path(d), seed=seed, case=case, change_http=configure)
            self.assertEqual(rc, 0, output)
            self.assertEqual(evidence['observed_verdict'], 'PASS', evidence)
            self.assertEqual(evidence['after']['items'][1]['options'][0]['value'], 'TEST NEW')

    def test_live_requires_reviewed_manifest_scope_and_opt_in(self):
        scenarios = [('review', None), ('customer', None), ('unknown', None),
                     ('disabled', {'SALLA_AMASI_TEST_RUN_WRITES': 'false'}),
                     ('scope', {'SALLA_AMASI_TEST_TOKEN_SCOPES': 'orders.read_write,products.read'})]
        for scenario, env in scenarios:
            with self.subTest(scenario=scenario), tempfile.TemporaryDirectory() as d:
                seed = live_seed()
                if scenario == 'review':
                    seed['downstream_reviewed'] = False
                elif scenario == 'customer':
                    seed['orders'][0].pop('test_customer_id')
                elif scenario == 'unknown':
                    seed['allow_any_order'] = True
                rc, _, http, _, deps = self.run_cli(Path(d), seed=seed, env_updates=env)
                self.assertEqual(rc, 2)
                self.assertEqual([m for m, _ in http.calls if m != 'GET'], [])
                if scenario != 'scope':
                    self.assertEqual(deps, 0)

    def test_final_check_under_lock_rejects_drift_before_write(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            def configure(http):
                def drift():
                    self.assertEqual(len(list((tmp / 'evidence').glob('*.intent.json'))), 1)
                    http.order['reference_id'] = 'changed-after-readiness'
                http.on_final_check = drift
            rc, _, http, evidence, _ = self.run_cli(tmp, change_http=configure)
            self.assertEqual(rc, 2)
            self.assertEqual([m for m, _ in http.calls if m != 'GET'], [])
            self.assertEqual(evidence['attempt_outcome'], 'UNKNOWN')

    def test_postwrite_eligibility_change_is_not_a_pass(self):
        with tempfile.TemporaryDirectory() as d:
            def configure(http):
                http.after_first_hook = lambda: http.order.update(paid_amount=100)
            rc, _, http, evidence, _ = self.run_cli(Path(d), change_http=configure)
            # A payment arriving while fetching items must be detected by a
            # fresh eligibility read after the mutation's item response.
            self.assertNotEqual(evidence['observed_verdict'], 'PASS')
            self.assertEqual([m for m, _ in http.calls if m != 'GET'], ['POST'])

    def test_live_readiness_exits_nonzero_on_unknown_shipments(self):
        with tempfile.TemporaryDirectory() as d:
            rc, _, http, _, _ = self.run_cli(Path(d), args=['readiness', '--environment', 'amasi-test-orders'],
                                            change_http=lambda h: h.shipments['body'].pop('pagination'))
            self.assertEqual(rc, 2)
            self.assertEqual([m for m, _ in http.calls if m != 'GET'], [])

    def test_unsuccessful_provider_read_envelopes_prevent_mutation(self):
        for path in ('/store/info', '/orders/o0', '/products/p0', '/orders/items?order_id=o0'):
            with self.subTest(path=path), tempfile.TemporaryDirectory() as d:
                rc, _, http, _, _ = self.run_cli(Path(d), change_http=lambda h: setattr(h, 'reject_envelope_path', path))
                self.assertEqual(rc, 2)
                self.assertEqual([m for m, _ in http.calls if m != 'GET'], [])

    def test_wrong_total_does_not_pass_despite_correct_items_and_webhook(self):
        with tempfile.TemporaryDirectory() as d:
            rc, _, http, evidence, _ = self.run_cli(Path(d), webhook=True, change_http=lambda h: setattr(h, 'order_after_total', 999))
            self.assertEqual(rc, 0)
            self.assertEqual(evidence['verdict'], 'FAIL')
            self.assertEqual([m for m, _ in http.calls if m != 'GET'], ['POST'])

    def test_http_error_with_fixture_data_cannot_authorize_write(self):
        for path in ('/store/info', '/orders/o0', '/products/p0', '/orders/items?order_id=o0'):
            with self.subTest(path=path), tempfile.TemporaryDirectory() as d:
                rc, _, http, _, _ = self.run_cli(Path(d), change_http=lambda h: setattr(h, 'http_error_path', path))
                self.assertEqual(rc, 2)
                self.assertEqual([m for m, _ in http.calls if m != 'GET'], [])

    def test_null_pending_refund_is_not_a_positive_refund(self):
        with tempfile.TemporaryDirectory() as d:
            rc, output, _, evidence, _ = self.run_cli(Path(d), change_http=lambda h: h.order['payment_actions']['refund_action'].update(pending_refund_amount=None))
            self.assertEqual(rc, 0, output)
            self.assertEqual(evidence['observed_verdict'], 'PASS')

    def test_rejected_mutation_envelope_is_not_pass_or_retried(self):
        with tempfile.TemporaryDirectory() as d:
            rc, _, http, evidence, _ = self.run_cli(Path(d), webhook=True, change_http=lambda h: setattr(h, 'reject_write_envelope', True))
            self.assertEqual(rc, 0)
            self.assertEqual(evidence['verdict'], 'FAIL')
            self.assertEqual(evidence['observed_outcome_reason'], 'SALLA_WRITE_REJECTED')
            self.assertEqual([m for m, _ in http.calls if m != 'GET'], ['POST'])

    def test_timeout_never_retries_and_quarantines_order(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            rc, _, http, evidence, _ = self.run_cli(tmp, change_http=lambda h: setattr(h, 'post_error', True))
            self.assertEqual(rc, 0)
            self.assertEqual(evidence['attempt_outcome'], 'UNKNOWN')
            self.assertEqual([m for m, _ in http.calls if m != 'GET'], ['POST'])
            rc, _, http, _, _ = self.run_cli(tmp, env_updates={'SALLA_P0_WRITE_APPROVAL_ID': 'second-approval'})
            self.assertEqual(rc, 2)
            self.assertEqual([m for m, _ in http.calls if m != 'GET'], [])

    def test_redirect_handler_refuses_replay(self):
        with self.assertRaises(runner.urllib.error.HTTPError):
            runner._NoLiveTestRedirect().redirect_request(
                runner.urllib.request.Request('https://api.salla.dev/admin/v2/orders/items', method='POST'),
                None, 307, 'redirect', {}, 'https://elsewhere.invalid/orders/items',
            )


if __name__ == '__main__':
    unittest.main()
