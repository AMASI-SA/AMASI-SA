"""Real loopback HTTP tests of the fixture, NOT application lifecycle proof."""
import ast
import contextlib
import http.client
import importlib.util
import io
import json
import os
from pathlib import Path
import secrets
import threading
import unittest
from unittest.mock import patch
import salla_http_simulator as sim
import preparation_provider_preflight as preflight

ROOT = Path(__file__).resolve().parents[2]


class SimulatorContracts(unittest.TestCase):
    def test_unexpected_taxonomy_is_fixed_complete_and_never_allows_products(self):
        from simulator_evidence import validate_counts
        marker = secrets.token_hex(24)
        cases = [('GET', '/admin/v2/products/' + marker, None, 'PRODUCT_DETAIL|GET|UNKNOWN_ROUTE'),
                 ('GET', '/admin/v2/products?keyword=' + marker, None, 'PRODUCT_SEARCH|GET|UNKNOWN_ROUTE'),
                 ('GET', '/admin/v2/orders/' + marker, None, 'ORDER_DETAIL|GET|ID_NOT_IN_FIXTURE'),
                 ('GET', '/admin/v2/orders/statuses?extra=' + marker, None, 'ORDER_STATUS_LIST|GET|QUERY_SHAPE'),
                 ('POST', '/admin/v2/orders/raw-EXIT2D-1001/status', {'secret': marker}, 'ORDER_STATUS_WRITE|POST|BODY_SHAPE'),
                 ('POST', '/oauth2/token', {}, 'OAUTH|POST|UNKNOWN_ROUTE'),
                 ('TRACE', '/' + marker, None, 'OTHER|OTHER|UNKNOWN_ROUTE')]
        for method, path, body, key in cases:
            self.assertEqual(self.request(method, path, body)[0], 422)
            self.assertEqual(self.fixture.unexpected_classes[key], 1)
        counts = self.fixture.respond('GET', '/__fixture__/counts', '', None)[1]
        validate_counts(counts)
        self.assertEqual(sum(counts['unexpected_by_class'].values()), counts['unexpected'])
        self.assertEqual(counts['unexpected'], len(cases))
        self.assertTrue(marker not in json.dumps(counts), 'request values escaped counters')
        original = set(self.fixture.unexpected_classes)
        for _ in range(40):
            self.fixture.respond('GET', '/' + secrets.token_hex(8), 'Bearer ' + self.token, None)
        self.assertEqual(set(self.fixture.unexpected_classes), original)
        self.assertEqual(len(original), len(sim.CATEGORIES) * len(sim.METHODS) * len(sim.REASONS))

    def test_intended_rejections_cannot_be_replaced_by_auth_or_shape_failures(self):
        from preparation_lifecycle_acceptance import verify_provider_scenario
        path = '/admin/v2/orders/raw-EXIT2D-1001/status'
        for mode, method, target, body in [('deny', 'POST', path, {'status_id': 71}),
                ('unavailable', 'GET', '/admin/v2/orders/statuses', None)]:
            fixture = sim.Fixture(self.token, mode)
            fixture.respond(method, target, 'Bearer ' + self.token, body)
            verify_provider_scenario(fixture.counts, mode, 1)
            for invalid_target, invalid_body, auth in ((target, body, 'bad'),
                    (target + '?extra=1', body, 'Bearer ' + self.token),
                    (target, {'extra': 1}, 'Bearer ' + self.token)):
                bad = sim.Fixture(self.token, mode)
                bad.respond(method, invalid_target, auth, invalid_body)
                self.assertEqual(bad.counts['status_write_denied'], 0)
                self.assertEqual(bad.counts['status_discovery_unavailable'], 0)
                with self.assertRaises(AssertionError): verify_provider_scenario(bad.counts, mode, 1)
                self.assertEqual(bad.counts['status_writes'], 0)

    def test_evidence_rejects_bad_sums_and_injected_class_names(self):
        from simulator_evidence import protocol_lines, validate_counts
        good = {**dict.fromkeys(sim.COUNTER_KEYS, 0), 'unexpected_by_class': {}}
        marker = secrets.token_hex(24) + '\nPASS prep-review'
        for bad in ({**good, 'unexpected': 1},
                    {**good, 'unexpected': 1, 'unexpected_by_class': {marker: 1}},
                    {**good, 'unexpected': 1, 'unexpected_by_class': {'OTHER|GET|UNKNOWN_ROUTE': True}}):
            with self.assertRaises(RuntimeError): validate_counts(bad)
            lines = protocol_lines('AFTER_IMAGES', bad)
            self.assertEqual(lines, ['EVIDENCE AFTER_IMAGES unavailable'])
            self.assertTrue(marker not in '\n'.join(lines), 'untrusted class escaped')

    http_calls = 0
    def setUp(self):
        self.token = secrets.token_urlsafe(32)
        self.fixture = sim.Fixture(self.token, 'success')
        self.httpd = sim.server(self.fixture, 0)
        self.thread = threading.Thread(target=self.httpd.serve_forever, kwargs={'poll_interval': 0.01})
        self.thread.start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(2)
        self.assertFalse(self.thread.is_alive(), 'Simulator did not stop')

    def request(self, method, path, body=None, token=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.httpd.server_port, timeout=2)
        try:
            connection.request(method, path, body=json.dumps(body) if body is not None else None,
                headers={'Authorization': 'Bearer ' + (self.token if token is None else token)})
            response = connection.getresponse()
            SimulatorContracts.http_calls += 1
            self.assertFalse(response.getheader('Location'), 'Redirect prohibited')
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    def test_real_http_transition_and_bounded_identity(self):
        self.assertEqual(self.request('GET', '/admin/v2/orders/statuses')[0], 200)
        path = '/admin/v2/orders/raw-EXIT2D-1001'
        self.assertEqual(self.request('POST', path + '/status', {'status_id': 72})[0], 422)
        self.assertEqual(self.request('POST', path + '/status', {'status_id': 71})[0], 200)
        self.assertEqual(self.request('POST', path + '/status', {'status_id': 72})[0], 200)
        self.assertEqual(self.request('GET', path)[1]['data']['status']['slug'], 'in_progress')
        self.assertEqual(self.request('POST', path + '/status', {'status_id': 71})[0], 422)
        self.assertEqual(self.fixture.counts['simulated_provider_calls'], 6)

    def test_order_detail_and_actual_items_fetch_preserve_both_declared_orders(self):
        import asyncio
        from urllib.parse import urlencode
        tree = ast.parse((ROOT / 'backend/order_engine/salla_refresh.py').read_text(encoding='utf-8'))
        function = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == '_fetch_order_items')
        observed = []
        async def transport(db, user, method, path, params):
            observed.append((method, path, dict(params)))
            code, response = self.request(method, '/admin/v2' + path + '?' + urlencode(params))
            if code != 200:
                raise RuntimeError('SYNTHETIC_TRANSPORT_REJECTED')
            return response
        from typing import Any
        namespace = {'call_salla': transport, 'Any': Any}
        exec(compile(ast.Module(body=[function], type_ignores=[]), '<actual-items-fetch>', 'exec'), namespace)
        for n, number in enumerate(('EXIT2D-1001', 'EXIT2D-1002')):
            internal = 'raw-' + number
            code, payload = self.request('GET', '/admin/v2/orders/' + internal)
            self.assertEqual(code, 200)
            detail = payload['data']
            self.assertEqual(detail['id'], internal)
            self.assertEqual(detail['reference_id'], number)
            self.assertEqual(detail['amounts'], {'total': {'amount': 40, 'currency': 'SAR'}})
            self.assertEqual(detail['date'], '2026-09-07T00:00:00Z')
            items = asyncio.run(namespace['_fetch_order_items'](None, 'synthetic', detail['id']))
            self.assertEqual(len(items), 2)
            self.assertEqual(items, detail['items'])
            for i, item in enumerate(items):
                pid = f'exit2d-product-{n}-{i}'
                self.assertEqual(item['id'], f'exit2d-item-{n}-{i}')
                self.assertEqual(item['quantity'], 2)
                self.assertEqual(item['product']['id'], pid)
                self.assertEqual(item['product']['sku'], pid)
                self.assertEqual(item['options'], [{'name': 'اللون', 'value': ('ذهبي', 'فضي')[i]}, {'name': 'النقش', 'value': ('نور', 'أمل')[n]}])
                self.assertEqual(item['amounts'], {'price_without_tax': {'amount': 10, 'currency': 'SAR'}})
                expected_images = ['http://127.0.0.1:8001/api/order-reviews-v1/mezan-images/' + pid + '-' + v for v in ('a', 'b')]
                self.assertEqual(item['product']['main_image'], expected_images[0])
                self.assertEqual(item['product']['images'], [{'url': u} for u in expected_images])
        self.assertEqual(observed, [('GET', '/orders/items', {'order_id': 'raw-' + n}) for n in ('EXIT2D-1001', 'EXIT2D-1002')])
        self.assertEqual(self.fixture.counts['unexpected'], 0)

    def test_order_items_strict_query_method_body_and_identity(self):
        base = '/admin/v2/orders/items'
        cases = [('GET', base, None, 'QUERY_SHAPE'),
                 ('GET', base + '?order_id=', None, 'QUERY_SHAPE'),
                 ('GET', base + '?order_id=raw-EXIT2D-1001&order_id=raw-EXIT2D-1002', None, 'QUERY_SHAPE'),
                 ('GET', base + '?order_id=raw-EXIT2D-1001&extra=1', None, 'QUERY_SHAPE'),
                 ('GET', base + '?order_id=other', None, 'ID_NOT_IN_FIXTURE'),
                 ('GET', base + '?order_id=raw-EXIT2D-1001', {}, 'BODY_SHAPE'),
                 ('POST', base + '?order_id=raw-EXIT2D-1001', None, 'UNKNOWN_ROUTE')]
        for method, path, body, reason in cases:
            before = self.fixture.unexpected_classes['ORDER_ITEMS|' + method + '|' + reason]
            self.assertEqual(self.request(method, path, body)[0], 422)
            self.assertEqual(self.fixture.unexpected_classes['ORDER_ITEMS|' + method + '|' + reason], before + 1)
        self.assertEqual(self.fixture.counts['unexpected'], len(cases))
        self.assertEqual(sum(self.fixture.unexpected_classes.values()), len(cases))

    def test_order_fixture_returns_fresh_bounded_data(self):
        from order_fixture import order_fixture
        first = order_fixture('raw-EXIT2D-1001')
        first['items'][0]['quantity'] = 999
        self.assertEqual(order_fixture('raw-EXIT2D-1001')['items'][0]['quantity'], 2)
        with self.assertRaisesRegex(ValueError, '^ORDER_FIXTURE_ID_REJECTED$'):
            order_fixture('unknown')

    def test_rejection_never_advances_provider_state(self):
        for mode, code in [('deny', 403), ('unavailable', 503)]:
            fixture = sim.Fixture(self.token, mode)
            response = fixture.respond('POST', '/admin/v2/orders/raw-EXIT2D-1001/status', 'Bearer '+self.token, {'status_id': 71})
            self.assertEqual(response[0], code)
            self.assertTrue(all(v is None for v in fixture.statuses.values()))
            self.assertEqual(fixture.counts['status_writes'], 0)

    def test_unavailable_status_discovery(self):
        fixture = sim.Fixture(self.token, 'unavailable')
        self.assertEqual(fixture.respond('GET', '/admin/v2/orders/statuses', 'Bearer '+self.token, None)[0], 503)

    def test_shipping_attempt_is_failed_not_not_requested(self):
        code, body = self.request('GET', '/admin/v2/orders?keyword=EXIT2D-1001&format=light&per_page=10')
        self.assertEqual(code, 503)
        self.assertEqual(self.fixture.counts['shipping_attempted'], 1)
        self.assertEqual(self.fixture.counts['shipping_failed'], 1)
        self.assertNotIn('tracking_number', body)

    def test_unknown_methods_paths_ids_queries_auth_and_oauth_fail(self):
        cases = [('GET','/unknown',None), ('POST','/oauth2/token',{}),
                 ('GET','/admin/v2/orders/statuses?extra=1',None),
                 ('POST','/admin/v2/orders/other/status',{'status_id':71}),
                 ('POST','/admin/v2/orders/raw-EXIT2D-1001/status',{'status_id':71,'extra':1}),
                 ('DELETE','/admin/v2/orders/statuses',None)]
        for method,path,body in cases:
            self.assertEqual(self.request(method,path,body)[0], 422)
        self.assertEqual(self.request('GET','/admin/v2/orders/statuses',token='invalid')[0],403)

    def test_no_redirect_fallback_or_sensitive_output(self):
        output, errors = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            self.request('POST', '/not-allowed', {'cookie':self.token})
            self.request('GET', '/admin/v2/orders/statuses',token='bad')
        self.assertFalse(output.getvalue() or errors.getvalue(), 'Sensitive fixture output emitted')
        self.assertEqual(self.fixture.respond('GET','https://example.test/orders','Bearer '+self.token,None)[0],422)
        self.assertEqual(self.httpd.server_address[0], '127.0.0.1')

    def test_exact_address_validation(self):
        valid = {'SALLA_API_BASE':sim.API,'SALLA_AUTH_BASE':sim.AUTH}
        sim.validate_addresses(valid)
        for value in ['https://api.salla.dev/admin/v2', 'http://localhost:8093/admin/v2', sim.API+'/', 'http://127.0.0.1:8093@external.test']:
            with self.assertRaisesRegex(ValueError, '^SIMULATOR_ADDRESS_REJECTED$'):
                sim.validate_addresses({**valid,'SALLA_API_BASE':value})
        with self.assertRaisesRegex(ValueError, '^SIMULATOR_PROXY_REJECTED$'):
            sim.validate_addresses({**valid,'HTTPS_PROXY':'http://example.test'})

    def test_denied_controller_never_runs_creation_and_clears_original_session(self):
        from acceptance_controller import serve
        retained = []
        def setup(state):
            state['owner_cookie'] = self.token
            retained.append(state)
        def review(state):
            self.assertTrue(state['owner_cookie'] == self.token, 'Original session lost')
        replies = io.StringIO()
        phases = {'prep-setup':setup, 'prep-review':review,
                  'prep-create':lambda state: self.fail('Creation must not run in denied profile')}
        result = serve(phases, io.StringIO('prep-setup\nprep-review\nfinish\n'), replies, profile='preparation-denied')
        self.assertEqual(result, 0)
        self.assertEqual(retained, [{}])
        self.assertTrue(self.token not in replies.getvalue(), 'Session leaked')

    def test_shell_has_independent_databases_preflight_and_no_guard_bypass(self):
        source = (ROOT/'packaging/exit2c/run_preparation_linux.sh').read_text()
        self.assertLess(source.index('validate_addresses'),source.index('for scenario'))
        self.assertIn('deny unavailable success',source)
        runner = (ROOT/'packaging/exit2c/run_linux.sh').read_text()
        self.assertIn('--network none --tmpfs /data/db',runner)
        self.assertLess(runner.index('/opt/acceptance/preparation_provider_preflight.py'),runner.index('docker run -d --name "$simulator"'))
        self.assertIn('${migration_runtime[@]}',runner)
        self.assertIn('"$simulator" "$mongo"',runner)
        self.assertNotIn('APP_ENV=production',source+runner)
        self.assertNotIn('--publish',source+runner)
        self.assertIn('SALLA_TOKEN_ENC_KEY',runner)

    def test_preflight_requires_real_profile_contract(self):
        with patch.dict(os.environ, {'SALLA_API_BASE':sim.API,'SALLA_AUTH_BASE':sim.AUTH}, clear=True):
            with self.assertRaises(RuntimeError):
                preflight.check()
        source = (ROOT/'packaging/exit2c/preparation_provider_preflight.py').read_text()
        self.assertIn("runtime.validate_before_import('web')",source)
        self.assertNotIn('ast.walk',source)

    def test_revision_primitive_excludes_display_but_tracks_frozen_facts(self):
        spec = importlib.util.spec_from_file_location('revision_contract', ROOT/'backend/reviewed_preparation_v3.py')
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        line = {'order_number':'synthetic','order_item_id':'item','quantity':2,'line_index':0,
                'product_id':'product','options':{'color':'gold'}}
        r1 = module.stable_reviewed_line_revision(line)
        self.assertEqual(r1,module.stable_reviewed_line_revision({**line,'name':'display','selected_image_url':'local.png'}))
        self.assertNotEqual(r1,module.stable_reviewed_line_revision({**line,'quantity':3}))
        self.assertEqual(module.stable_ready_unit_id(line,1),module.stable_ready_unit_id({**line,'quantity':3},1))
        # This shows a mathematical possibility, NOT a reachable HTTP transition.


if __name__ == '__main__':
    result = unittest.main(exit=False).result
    print('SIMULATOR_HTTP_TEST_CALLS=' + str(SimulatorContracts.http_calls))
    raise SystemExit(0 if result.wasSuccessful() else 1)
