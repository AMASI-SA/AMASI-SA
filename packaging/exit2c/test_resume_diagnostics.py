"""Local diagnostic integrations with synthetic transport/database adapters only."""
import copy
import io
import os
from pathlib import Path
import secrets
import shlex
import shutil
import subprocess
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from acceptance_controller import CHECKS_BY_PHASE, PREPARATION_PHASES, CheckFailure, check, serve
import preparation_lifecycle_acceptance as prep
from snapshot_evidence import COLLECTIONS, snapshot_lines
from salla_http_simulator import COUNTER_KEYS

def shell(phase, reply):
    source = Path(__file__).with_name('run_linux.sh').read_text(encoding='utf-8')
    function = source[source.index('accept() {'):source.index('\nstart_webs() {')]
    program = function + '\nexec {accept_in}>/dev/null\nexec {accept_out}< <(printf %s ' + shlex.quote(reply) + ')\naccept ' + shlex.quote(phase) + '\n'
    bash = 'C:/Program Files/Git/bin/bash.exe' if os.name == 'nt' else shutil.which('bash')
    return subprocess.run([bash, '-s'], input=program, text=True, capture_output=True, timeout=8)

def controller(phase, action):
    phases = {p: (action if p == phase else lambda s: None) for p in PREPARATION_PHASES}
    commands = '\n'.join((*PREPARATION_PHASES, 'finish')) + '\n'
    output = io.StringIO()
    result = serve(phases, io.StringIO(commands), output, profile='preparation')
    prefix = ''.join('PASS '+p+'\n' for p in PREPARATION_PHASES[:PREPARATION_PHASES.index(phase)])
    return result, output.getvalue().removeprefix(prefix)

class ResumeDiagnosticTests(unittest.TestCase):
    def test_label_diagnostic_pass_is_emitted_only_when_label_contract_was_checked(self):
        import ast
        tree = ast.parse(Path(prep.__file__).read_text(encoding='utf-8'))
        node = next(n for n in ast.walk(tree) if isinstance(n,ast.If)
                    and 'order_completed' in ast.unparse(n.test))
        code = compile(ast.Module(body=[node],type_ignores=[]), '<actual-label-check>', 'exec')
        for completed in (False, True):
            def action(state):
                exec(code, {'check':check,'require':prep.require,'result':{
                    'progress':{'order_completed':completed},
                    'carrier_label':{'ready':False,'error_code':'salla_shipping_unavailable'}}})
            result, reply = controller('prep-resume',action)
            self.assertTrue(result==0)
            self.assertEqual('CHECK prep-resume SIMULATED_LABEL_FAILURE PASS\n' in reply, completed)

    def test_all_remaining_phase_checks_reach_real_shell_and_clear_state(self):
        for phase in ('prep-create', 'prep-resume', 'prep-finish'):
            for identifier in CHECKS_BY_PHASE[phase]:
                refs = []
                marker = secrets.token_hex(24)
                def action(state):
                    refs.append(state); state['owner_cookie'] = marker
                    with check(identifier):
                        print(marker)
                        raise AssertionError(marker)
                result, reply = controller(phase, action)
                parsed = shell(phase, reply)
                self.assertTrue(result == parsed.returncode == 1, 'FAILURE_NOT_PRESERVED')
                self.assertTrue(parsed.stdout == 'FAIL '+phase+' ASSERTION_FAILED '+identifier+'\n', 'CHECK_NOT_DELIVERED')
                self.assertTrue(marker not in reply+parsed.stdout+parsed.stderr, 'SENSITIVE_OUTPUT')
                self.assertTrue(all(not state for state in refs), 'STATE_NOT_CLEARED')

    def test_phase_check_cross_product_rejects_wrong_phase_in_both_boundaries(self):
        for phase in ('prep-review','prep-create','prep-resume','prep-finish'):
            identifier = next(c for other, ids in CHECKS_BY_PHASE.items() for c in ids if c not in CHECKS_BY_PHASE[phase])
            def action(state):
                with check(identifier): raise AssertionError('synthetic')
            result, reply = controller(phase, action)
            self.assertTrue(result == 1 and reply.endswith('FAIL '+phase+' UNCLASSIFIED_FAILURE\n'))
            for prefix in ('FAIL '+phase+' ASSERTION_FAILED ', 'CHECK '+phase+' '):
                suffix = '\n' if prefix.startswith('FAIL') else ' PASS\nPASS '+phase+'\n'
                parsed = shell(phase, prefix+identifier+suffix)
                self.assertTrue(parsed.returncode == 1 and identifier not in parsed.stdout, 'CROSS_PHASE_ACCEPTED')

    def test_real_resume_and_finish_persistence_fail_before_mutation_with_safe_evidence(self):
        for phase in ('prep-resume','prep-finish'):
            marker = secrets.token_hex(24)
            before = {'identity': (), 'documents': {name: [] for name in COLLECTIONS.values()}}
            before['documents'][prep.WORKFLOWS] = [{'updated_at':'before', 'sensitive':marker}]
            after = copy.deepcopy(before)
            after['documents'][prep.WORKFLOWS][0]['updated_at'] = 'after'
            consumed, refs, mutations = [], [], []
            def action(state):
                refs.append(state)
                state['checkpoint'] = before
                state['other_workflows'] = []
                for actor in ('owner','employee','viewer','outsider'):
                    state[actor] = actor
                    state[actor+'_cookie'] = marker+actor
                life = prep.Lifecycle.__new__(prep.Lifecycle)
                life.state = state
                # Explicit local adapters: controller and Lifecycle methods under test are real.
                def request(port, method, path, *, cookie, expected, **kwargs):
                    consumed.append(cookie)
                    return SimpleNamespace(json=lambda:{'id':cookie.removeprefix(marker)})
                life.a = SimpleNamespace(request=request, httpx=SimpleNamespace(get=lambda *a,**k: SimpleNamespace(status_code=200,json=lambda:{**dict.fromkeys(COUNTER_KEYS,0),'unexpected_by_class':{}})))
                life.invariant = lambda: None
                life.snapshot = lambda: copy.deepcopy(after)
                life.recover = lambda: mutations.append(True)
                life.pdf_and_images = lambda: mutations.append(True)
                getattr(life, 'resume' if phase=='prep-resume' else 'finish')()
            result, reply = controller(phase, action)
            parsed = shell(phase, reply)
            self.assertTrue(result == parsed.returncode == 1, 'FAILURE_NOT_PRESERVED')
            self.assertTrue(parsed.stdout.endswith('FAIL '+phase+' ASSERTION_FAILED SNAPSHOT_MATCH\n'), 'SNAPSHOT_FAILURE_LOST')
            self.assertTrue('CHECK '+phase+' OUTSIDER_SESSION PASS\n' in parsed.stdout)
            self.assertTrue('CHECK '+phase+' SNAPSHOT_IDENTITY PASS\n' in parsed.stdout)
            self.assertTrue('SNAPSHOT WORKFLOWS FIELD updated_at\n' in parsed.stdout)
            self.assertTrue('SNAPSHOT WORKFLOWS UNKNOWN_FIELDS_CHANGED false\n' in parsed.stdout)
            self.assertTrue(marker not in parsed.stdout+parsed.stderr+reply, 'SENSITIVE_OUTPUT')
            self.assertTrue(consumed == [marker+a for a in ('owner','employee','viewer','outsider')], 'ORIGINAL_SESSION_CHANGED')
            self.assertTrue(not mutations and all(not s for s in refs), 'POST_FAILURE_MUTATION')
            self.assertTrue(before['documents'][prep.WORKFLOWS][0]['updated_at']=='before')

    def test_snapshot_collection_failure_does_not_hide_first_assertion(self):
        def action(state):
            state['_snapshot_evidence'] = ['SNAPSHOT unavailable']
            state['_review_evidence'] = [('BEFORE_RESUME',None)]
            with check('SNAPSHOT_MATCH'): raise AssertionError('must never print')
        result, reply = controller('prep-resume',action)
        parsed = shell('prep-resume',reply)
        self.assertTrue(result == parsed.returncode == 1)
        self.assertTrue('SNAPSHOT unavailable\n' in parsed.stdout and 'EVIDENCE BEFORE_RESUME unavailable\n' in parsed.stdout)
        self.assertTrue(parsed.stdout.endswith('FAIL prep-resume ASSERTION_FAILED SNAPSHOT_MATCH\n'))

    def test_hostile_snapshot_check_and_counter_protocol_is_rejected(self):
        marker = secrets.token_hex(24)
        bad = [
            'SNAPSHOT '+marker, 'SNAPSHOT WORKFLOWS FIELD '+marker,
            'SNAPSHOT WORKFLOWS COUNT 1 10001', 'SNAPSHOT WORKFLOWS COUNT 1 -1',
            'SNAPSHOT WORKFLOWS ORDER_CHANGED '+marker,
            'CHECK prep-resume '+marker+' PASS',
            'EVIDENCE AFTER_CREATE unexpected 0',
            'FAIL prep-resume ASSERTION_FAILED '+marker,
            'FAIL prep-resume ASSERTION_FAILED SNAPSHOT_MATCH '+marker,
            'FAIL prep-finish ASSERTION_FAILED SNAPSHOT_MATCH',
        ]
        for line in bad:
            parsed = shell('prep-resume',line+'\nPASS prep-resume\n')
            self.assertTrue(parsed.returncode==1 and marker not in parsed.stdout+parsed.stderr,'HOSTILE_PROTOCOL_ESCAPED')
            self.assertTrue('PASS acceptance phase' not in parsed.stdout)

    def test_completed_check_precedes_failure_and_normal_phases_still_succeed(self):
        for phase in ('prep-create','prep-resume','prep-finish'):
            identifier = CHECKS_BY_PHASE[phase][0]
            def action(state):
                with check(identifier): pass
            result, reply = controller(phase,action)
            # parser reads only this phase; remove subsequent phase replies.
            reply = reply[:reply.index('PASS '+phase+'\n')+len('PASS '+phase+'\n')]
            parsed = shell(phase,reply)
            self.assertTrue(result==parsed.returncode==0)
            self.assertTrue(parsed.stdout.startswith('CHECK '+phase+' '+identifier+' PASS\n'))

    def test_original_full_snapshot_predicate_still_present(self):
        source = Path(prep.__file__).read_text(encoding='utf-8')
        self.assertIn('require(current == self.state["checkpoint"])',source)
        self.assertNotIn("checkpoint'].pop",source)

    def test_remaining_phase_cancellation_timeout_and_corrupt_evidence_keep_first_failure(self):
        for phase in ('prep-create','prep-resume','prep-finish'):
            for error, reason in ((KeyboardInterrupt, 'CANCELLED'), (TimeoutError, 'TIMEOUT')):
                marker = secrets.token_hex(24)
                references = []
                identifier = CHECKS_BY_PHASE[phase][0]
                def action(state):
                    references.append(state); state['owner_cookie'] = marker
                    state['_review_evidence'] = [object()]  # emitter must not replace primary error
                    with check(identifier): raise error(marker)
                result, reply = controller(phase,action)
                parsed = shell(phase,reply)
                self.assertTrue(result == parsed.returncode == 1)
                self.assertTrue(parsed.stdout.endswith('FAIL '+phase+' '+reason+' '+identifier+'\n'))
                self.assertTrue(marker not in reply+parsed.stdout+parsed.stderr)
                self.assertTrue(all(not s for s in references))

if __name__ == '__main__':
    unittest.main()
