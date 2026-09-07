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
        self.assertLess(source.index('preparation_provider_preflight.py'),source.index('for scenario'))
        self.assertIn('deny unavailable success',source)
        runner = (ROOT/'packaging/exit2c/run_linux.sh').read_text()
        self.assertIn('--network none --tmpfs /data/db',runner)
        self.assertIn('"$simulator" "$mongo"',runner)
        self.assertNotIn('APP_ENV=production',source+runner)
        self.assertNotIn('--publish',source+runner)
        self.assertIn('SALLA_TOKEN_ENC_KEY',runner)

    def test_runtime_guard_conflict_is_visible_not_bypassed(self):
        with patch.dict(os.environ, {'SALLA_API_BASE':sim.API,'SALLA_AUTH_BASE':sim.AUTH}, clear=True):
            with self.assertRaisesRegex(RuntimeError,'^BLOCKED_SYNTHETIC_PROVIDER_KEY_GUARD$'):
                preflight.check()
        tree = ast.parse((ROOT/'backend/independent_runtime.py').read_text(encoding='utf-8'))
        guard = next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='validate_before_import')
        self.assertTrue(any(isinstance(n,ast.Constant) and n.value=='TOKEN_ENC_KEY' for n in ast.walk(guard)))

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
