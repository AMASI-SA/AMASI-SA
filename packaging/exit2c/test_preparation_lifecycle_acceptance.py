"""Local contracts only; these never claim real HTTP/Mongo lifecycle acceptance."""
import ast
import asyncio
import contextlib
import copy
import io
from pathlib import Path
import secrets
import socket
import sys
from types import SimpleNamespace, ModuleType
import unittest
from unittest.mock import patch

from acceptance_controller import PREPARATION_PHASES, serve
import preparation_lifecycle_acceptance as prep
from test_catalog_image_fixture import CatalogImageFixtureTests
from test_snapshot_evidence import SnapshotEvidenceTests
from test_unit_contract import UnitContractTests
from test_unit_projection import UnitProjectionTests, SourceLayoutTests, synthetic_documents
from test_supplier_workspace_contract import SupplierWorkspaceTests


class PreparationContracts(unittest.TestCase):
    def test_composite_gallery_requires_exact_aliases_and_upload_addition(self):
        paths = ['/api/order-reviews-v1/mezan-images/fixture-a', '/api/order-reviews-v1/mezan-images/fixture-b']
        expected = ['http://127.0.0.1:8001' + p for p in paths] + paths
        prep.verify_gallery_links(expected, expected)
        for bad in (expected[:2], expected + ['/unknown'], expected[:-1] + [expected[0]],
                    ['https://external.example' + paths[0]] + expected[1:]):
            with self.assertRaises(AssertionError): prep.verify_gallery_links(bad, expected)
        new = '/api/order-reviews-v1/mezan-images/new-fixture'
        prep.verify_gallery_links(expected + [new], expected + [new])
        with self.assertRaises(AssertionError): prep.verify_gallery_links(expected[1:] + [new], expected + [new])

    def test_evidence_counts_are_measured_bounded_and_failure_is_unavailable(self):
        import json
        import simulator_evidence as evidence
        valid = dict(simulated_provider_calls=3, unexpected=1, status_writes=0,
                     denied=2, shipping_attempted=0, shipping_failed=0, status_write_denied=2,
                     status_discovery_unavailable=0, auth_rejected=0,
                     unexpected_by_class={"PRODUCT_DETAIL|GET|UNKNOWN_ROUTE": 1})
        marker = secrets.token_hex(24)
        for payload, good in ((valid, True), ({**valid, 'unexpected': 0, 'unexpected_by_class': {}}, True), ({**valid, marker: 1}, False),
                              ({**valid, 'denied': marker}, False),
                              ({**valid, 'denied': True}, False)):
            closed, reads = [], []
            response = SimpleNamespace(status=200, read=lambda size: reads.append(size) or json.dumps(payload).encode())
            connection = SimpleNamespace(request=lambda *a: None, getresponse=lambda: response, close=lambda: closed.append(True))
            output = io.StringIO()
            with patch.object(evidence.socket, 'if_nameindex', return_value=[(1, 'lo')]), \
                 patch.object(evidence.http.client, 'HTTPConnection', return_value=connection) as transport, \
                 contextlib.redirect_stdout(output):
                result = evidence.main()
            transport.assert_called_once_with('127.0.0.1', 8093, timeout=2)
            self.assertEqual(reads, [32768]); self.assertEqual(closed, [True])
            self.assertEqual(result, (2 if payload['unexpected'] else 0) if good else 1)
            self.assertTrue(marker not in output.getvalue(), 'evidence leaked response material')
            if good:
                parsed = json.loads(output.getvalue().splitlines()[0].removeprefix('SIMULATOR_EVIDENCE '))
                self.assertEqual(parsed, {'live_provider_calls': 0, **payload})
            else:
                self.assertEqual(output.getvalue(), 'SIMULATOR_EVIDENCE unavailable\n')
        output = io.StringIO()
        with patch.object(evidence, 'evidence', side_effect=TimeoutError(marker)), contextlib.redirect_stdout(output):
            self.assertEqual(evidence.main(), 1)
        self.assertEqual(output.getvalue(), 'SIMULATOR_EVIDENCE unavailable\n')

    def test_fixture_login_addresses_share_schema_compatible_definition(self):
        source = Path(prep.__file__).read_text(encoding='utf-8')
        tree = ast.parse(source)
        targets = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name in ('seed_inputs', 'login_otp')]
        self.assertEqual(len(targets), 2)
        for node in targets:
            self.assertTrue('@exit2d.example.test' not in ast.unparse(node), 'invalid login fixture domain')
            self.assertTrue('login_email(actor)' in ast.unparse(node), 'fixture login address not shared')
        self.assertTrue(all(prep.login_email(a).endswith('@example.com')
                            for a in ('employee', 'viewer', 'outsider')))

    def test_correct_sent_field_cannot_pass_rejection(self):
        prep.verify_review_rejected({'stage': 'pending_review'})
        for workflow in ({'stage': 'pending_review', 'salla_status_sync': 'sent'},
                         {'stage': 'reviewed'}):
            with self.assertRaises(AssertionError):
                prep.verify_review_rejected(workflow)

    def test_fixture_arabic_text_is_not_mojibake(self):
        tree = ast.parse(Path(prep.__file__).read_text(encoding="utf-8") + "\n" + Path(prep.__file__).with_name("order_fixture.py").read_text(encoding="utf-8"))
        values = [n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)]
        for expected in ("\u0627\u0644\u0644\u0648\u0646", "\u0630\u0647\u0628\u064a", "\u0641\u0636\u064a", "\u0646\u0648\u0631", "\u0623\u0645\u0644"):
            self.assertTrue(expected in values, "Arabic fixture token missing")
        self.assertTrue(all(not any(c in value for c in ("\u00b7", "\u00c2", "\u20ac")) for value in values), "Fixture encoding is corrupted")

    def test_new_profile_retains_sessions_and_clears_them(self):
        token = secrets.token_hex(24)
        seen, refs = [], []
        def setup(s):
            refs.append(s)
            s['owner_cookie'] = token
        def later(s):
            seen.append(s['owner_cookie'] is token)
            print(token)
            print(token, file=sys.stderr)
        phases = {p: setup if p == 'prep-setup' else later for p in PREPARATION_PHASES}
        output = io.StringIO()
        code = serve(phases, io.StringIO('\n'.join((*PREPARATION_PHASES, 'finish')) + '\n'), output, profile='preparation')
        self.assertEqual(code, 0)
        self.assertTrue(all(seen) and len(seen) == 4)
        self.assertTrue(all(not s for s in refs))
        if token in output.getvalue():
            self.fail('sensitive state escaped')
        self.assertEqual(output.getvalue(), ''.join('PASS ' + p + '\n' for p in (*PREPARATION_PHASES, 'finish')))

    def test_each_new_phase_failure_stops_later_phases_without_leak(self):
        for index, phase in enumerate(PREPARATION_PHASES):
            for error, reason in ((AssertionError, 'ASSERTION_FAILED'), (TimeoutError, 'TIMEOUT'),
                                  (KeyboardInterrupt, 'CANCELLED'), (ValueError, 'UNCLASSIFIED_FAILURE')):
                with self.subTest(phase=phase, reason=reason):
                    marker = secrets.token_hex(24)
                    called, refs = [], []
                    def action(name):
                        def run(s):
                            called.append(name); refs.append(s)
                            s['owner_cookie'] = marker
                            if name == phase:
                                raise error(marker)
                        return run
                    output = io.StringIO()
                    code = serve({p: action(p) for p in PREPARATION_PHASES},
                                 io.StringIO('\n'.join((*PREPARATION_PHASES, 'finish')) + '\n'), output, profile='preparation')
                    self.assertEqual(code, 1)
                    self.assertEqual(called, list(PREPARATION_PHASES[:index + 1]))
                    self.assertTrue(all(not s for s in refs))
                    if marker in output.getvalue(): self.fail('sensitive failure escaped')
                    self.assertTrue(output.getvalue().endswith('FAIL ' + phase + ' ' + reason + '\n'))

    def test_no_arbitrary_profile_phase_or_out_of_order_resume(self):
        for profile, command in (('external-input', 'prep-setup\n'), ('preparation', 'prep-resume\n'), ('preparation', '')):
            called = []
            output = io.StringIO()
            result = serve({'prep-setup': lambda s: called.append(1)}, io.StringIO(command), output, profile=profile)
            self.assertEqual(result, 1)
            self.assertFalse(called)
            self.assertNotIn('external-input', output.getvalue())

    def test_generated_business_rows_never_seeded_and_writes_confined(self):
        tree = ast.parse(Path(prep.__file__).read_text(encoding='utf-8-sig'))
        writes = {'insert_one', 'insert_many', 'update_one', 'update_many', 'replace_one', 'delete_one', 'delete_many'}
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name != 'seed_inputs':
                self.assertFalse(any(isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                                     and n.func.attr in writes for n in ast.walk(node)), 'business write outside initial fixture')
        seed = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'seed_inputs')
        forbidden = {'REGISTRY', 'BATCHES', 'ALLOCATIONS', 'PIECES'}
        for n in ast.walk(seed):
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr in writes:
                names = {x.id for x in ast.walk(n.func.value) if isinstance(x, ast.Name)}
                self.assertFalse(names & forbidden, 'ready business fixture attempted')
        # No file/session serialization in the new helper.
        self.assertFalse(any(isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                             and n.func.attr in {'write_text', 'write_bytes', 'dump', 'dumps'} for n in ast.walk(tree)))

    def test_exact_units_detect_duplicate_missing_wrong_line_options_and_employee(self):
        data = synthetic_documents()
        prep.verify_units(**data)
        for change in ({'unit_index': 1}, {'order_item_id': 'wrong'}, {'responsible_employee_id': 'wrong'},
                       {'product_options_snapshot': {'invalid': 'changed'}}):
            altered = copy.deepcopy(data)
            altered['pieces'][1].update(change)
            with self.assertRaises(AssertionError): prep.verify_units(**altered)
        altered = copy.deepcopy(data)
        altered['pieces'] = altered['pieces'][:1]
        with self.assertRaises(AssertionError): prep.verify_units(**altered)

    def test_guard_refuses_existing_provider_dependent_routes(self):
        for kind in ('review', 'assembly'):
            with self.assertRaises(AssertionError): prep.provider_barrier(kind)

    def test_preparation_shell_exits_before_any_legacy_fixture_or_worker(self):
        source = Path(__file__).with_name('run_linux.sh').read_text()
        branch = source[source.index('if test "$mode" != runtime; then\n  #'):source.index('\nfi\n\ndocker run --rm', source.index('if test "$mode" != runtime; then\n  #'))]
        self.assertNotIn('MEZAN_WORKER_ENABLED=1', branch)
        self.assertNotIn('worker_shutdown.py', branch)
        self.assertNotIn('accept setup', branch)
        self.assertEqual(branch.count('stop_webs'), 2)
        self.assertEqual(branch.count('start_webs'), 3)
        self.assertLess(branch.index('accept prep-create'), branch.index('stop_webs'))
        self.assertIn('exit 0', branch)
        self.assertIn('wait "$accept_pid"', branch)
        self.assertIn('trap cleanup EXIT', source)

    def test_setup_refuses_dirty_generated_database_before_any_fixture_write(self):
        # Load only seed function's outer source dependencies with inert imports;
        # use a collection sentinel to show an existing batch aborts immediately.
        fake_auth = ModuleType('auth'); fake_auth.hash_password = lambda _: None
        fake_mfa = ModuleType('mfa_security'); fake_mfa.encrypt_totp_secret = lambda _: None
        fake_accept = ModuleType('acceptance')
        class Database:
            def __getitem__(self, name): return SimpleNamespace(count_documents=lambda q: 1)
        with patch.dict(sys.modules, auth=fake_auth, mfa_security=fake_mfa, acceptance=fake_accept):
            with self.assertRaises(AssertionError): prep.seed_inputs(Database(), {})

    def test_finalize_fallback_only_when_registration_not_complete(self):
        for ready in (True, False):
            obj = prep.Lifecycle.__new__(prep.Lifecycle)
            obj.state = {"files": []}
            calls = []
            result = {"batch_id": "actual-batch", "file_number": "actual-file",
                      "file_registered": ready, "registry_status": "ready" if ready else "draft",
                      "piece_registry_status": "ready" if ready else "pending"}
            def call(method, path, **kwargs):
                calls.append(path)
                return SimpleNamespace(json=lambda: copy.deepcopy(result))
            obj.call = call
            obj.draft = lambda *args: None
            obj.snapshot = lambda: {"identity": (), "documents": {prep.EVENTS: []}}
            obj.identity = lambda: ()
            obj.rows = lambda name: []
            obj.build("request-0001", [{"group_key": "actual-key", "revision": "a" * 64, "quantity": 1}])
            self.assertEqual(sum('/finalize/' in path for path in calls), 0 if ready else 1)
            self.assertEqual(len(obj.state["files"]), 1)

    def test_phase_closes_database_on_failure(self):
        closed = []
        class FailedLifecycle:
            def __init__(self, state):
                self.database = SimpleNamespace(client=SimpleNamespace(close=lambda: closed.append(True)))
            def review(self): raise AssertionError('synthetic sensitive failure')
        with patch.object(prep, 'Lifecycle', FailedLifecycle):
            with self.assertRaises(AssertionError): prep.phases()['prep-review']({})
        self.assertEqual(closed, [True])

    def test_current_complete_review_requires_provider_despite_experiment_flag(self):
        """Execute the repository's function body, with inert data collaborators.

        Not HTTP/Auth/Mongo proof. Reproduces the business gate on synthetic
        initial workflow; no network, server import, real DB or provider calls.
        """
        source = ast.parse((prep.backend_root() / 'order_review_routes.py').read_text(encoding='utf-8'))
        fn = copy.deepcopy(next(n for n in ast.walk(source) if isinstance(n, ast.AsyncFunctionDef) and n.name == 'complete_review'))
        fn.decorator_list = []; fn.returns = None
        for arg in fn.args.args: arg.annotation = None
        fn.args.defaults = []
        unit = ast.fix_missing_locations(ast.Module(body=[fn], type_ignores=[]))
        workflow = {'stage': 'pending_review', 'revision': 1, 'items': [], 'experiment_mode': True,
                    'salla_status_writes_allowed': False}
        events, calls = [], []
        class HTTPError(Exception):
            def __init__(self, status_code, detail): self.status_code, self.detail = status_code, detail
        class Collection:
            async def find_one(self, *args): return copy.deepcopy(workflow)
            def find(self, *args): return self
            async def to_list(self, *args): return []
            async def insert_one(self, row): events.append(row)
        async def nothing(*args, **kwargs): pass
        async def order(*args, **kwargs): return SimpleNamespace(order_number='o', order_id='o')
        async def items(*args, **kwargs): return [SimpleNamespace(order_item_id='i', quantity=2)]
        async def preferences(*args, **kwargs): return {}
        async def no_provider(*args, **kwargs):
            calls.append('confirmation-required')
            return 'pending', 'synthetic-provider-disabled'
        controls = ModuleType('order_review_export_controls')
        controls.ASSIGNMENT_DEFAULTS = 'defaults'
        controls.DIRECT_ASSEMBLY_ROUTE = 'direct_assembly'
        controls.INTERNAL_PREPARATION_ROUTE = 'internal_preparation'
        controls.SUPPLIER_FILE_ROUTE = 'supplier_file'
        controls.preparation_assignment_product_key = lambda item: 'product'
        env = {'_require_reviewer': lambda u: u, '_merchant_user_id': lambda u: u['id'],
               '_ensure_indexes': nothing, 'enforce_stage_instructions': nothing,
               'get_order': order, 'repository': None, 'OrderNotFoundError': LookupError,
               'HTTPException': HTTPError, '_review_item_identities': items,
               'db': SimpleNamespace(), 'WORKFLOWS': 'workflows', 'EVENTS': 'events',
               'REVIEW_COMPLETED_STAGES': {'reviewed'}, '_state_map': lambda w: {},
               '_preference_map': preferences, '_now': lambda: 'synthetic-time',
               '_text': lambda x: str(x or ''), 'build_image_preference_identity': lambda i: ('p','s',{}),
               '_item_view': lambda *args: dict(selected_image_url='', selected_image_source='manual', preparation_note='', internal_note=''),
               'order_item_specifications': lambda i: {}, '_sync_salla_reviewed': no_provider,
               'status': SimpleNamespace(HTTP_502_BAD_GATEWAY=502)}
        class Database:
            def __getitem__(self, name): return Collection()
        env['db'] = Database()
        exec(compile(unit, '<unchanged-complete-review-body>', 'exec'), env)
        async def invoke():
            # asyncio initializes its local wakeup pipe before denying sockets.
            with patch.object(socket, 'socket', side_effect=AssertionError('network forbidden')):
                await env['complete_review']('o', SimpleNamespace(expected_revision=1), {'id':'owner'})
        with patch.dict(sys.modules, order_review_export_controls=controls):
            with self.assertRaises(HTTPError) as caught:
                asyncio.run(invoke())
        self.assertEqual(caught.exception.status_code, 502)
        self.assertEqual(caught.exception.detail['code'], 'salla_review_status_sync_failed')
        self.assertEqual(calls, ['confirmation-required'])
        self.assertEqual(workflow['stage'], 'pending_review')
        self.assertEqual(events[0]['event_type'], 'order_review_salla_sync_failed')


if __name__ == '__main__':
    unittest.main()
