"""Local stdlib contracts; real Auth/web restart acceptance still requires Linux."""
import contextlib
import io
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import tempfile
import unittest

from acceptance_controller import PHASES, CHECK_IDS, CheckFailure, HTTPStatusFailure, check, serve


class ControllerTests(unittest.TestCase):
    def test_review_snapshots_reach_shell_before_failure_without_values(self):
        from salla_http_simulator import COUNTER_KEYS
        counts = {**dict.fromkeys(COUNTER_KEYS, 0), 'unexpected': 1, 'simulated_provider_calls': 1,
                  'unexpected_by_class': {'PRODUCT_DETAIL|GET|UNKNOWN_ROUTE': 1}}
        marker = secrets.token_hex(24)
        references = []
        def review(state):
            references.append(state)
            state['owner_cookie'] = marker
            state['_review_evidence'] = [('BEFORE_REVIEW', None), ('AFTER_IMAGES', counts)]
            with check('PROVIDER_COUNTERS'): raise AssertionError(marker)
        output = io.StringIO()
        self.assertEqual(serve({'prep-setup': lambda s: None, 'prep-review': review},
            io.StringIO('prep-setup\nprep-review\n'), output, profile='preparation-denied'), 1)
        reply = output.getvalue().removeprefix('PASS prep-setup\n')
        shell = self.shell_review(reply)
        self.assertEqual(shell.returncode, 1)
        self.assertTrue('EVIDENCE BEFORE_REVIEW unavailable\n' in shell.stdout)
        self.assertTrue('EVIDENCE AFTER_IMAGES PRODUCT_DETAIL GET UNKNOWN_ROUTE 1\n' in shell.stdout)
        self.assertTrue(shell.stdout.endswith('FAIL prep-review ASSERTION_FAILED PROVIDER_COUNTERS\n'))
        self.assertTrue(marker not in shell.stdout + shell.stderr + output.getvalue(), 'sensitive snapshot escaped')
        self.assertTrue(all(not s for s in references))
        for reply in ('EVIDENCE ' + marker + '\nPASS prep-review\n',
                      'EVIDENCE AFTER_REVIEW ' + marker + '\n',
                      'EVIDENCE AFTER_REVIEW OTHER GET ' + marker + ' 1\n',
                      'EVIDENCE AFTER_REVIEW unexpected 1001\n'):
            shell = self.shell_review(reply)
            self.assertTrue(shell.returncode == 1 and marker not in shell.stdout + shell.stderr)
            self.assertTrue(shell.stdout in ('FAIL controller UNCLASSIFIED_FAILURE\n',
                                              'FAIL prep-review UNCLASSIFIED_FAILURE\n'))

    def test_real_request_helper_reports_only_numeric_status(self):
        import ast
        from types import SimpleNamespace
        tree = ast.parse(Path(__file__).with_name('acceptance.py').read_text(encoding='utf-8'))
        node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'request')
        unit = ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[]))
        marker = secrets.token_hex(24)
        class Client:
            def __init__(self, **kwargs): pass
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def request(self, *args, **kwargs):
                return SimpleNamespace(status_code=422, json=lambda: self.fail_body())
            def fail_body(self): raise AssertionError('response body must not be read')
        scope = {'httpx': SimpleNamespace(Client=Client), 'ORIGIN': 'synthetic', 'URLS': ('http://127.0.0.1',)}
        exec(compile(unit, '<extracted-acceptance-request>', 'exec'), scope)
        with self.assertRaises(HTTPStatusFailure) as raised:
            scope['request'](0, 'POST', '/api/' + marker, cookie=marker, expected=202)
        self.assertEqual((raised.exception.expected, raised.exception.actual), (202, 422))
        self.assertTrue(marker not in str(raised.exception), 'request material escaped')

    def shell_review(self, reply, source=None):
        import shlex
        bash = 'C:/Program Files/Git/bin/bash.exe' if os.name == 'nt' else shutil.which('bash')
        source = source or Path(__file__).with_name('run_linux.sh').read_text()
        start = source.index('accept() {')
        function = source[start:source.index('\nstart_webs() {', start)]
        program = function + '\nexec {accept_in}>/dev/null\nexec {accept_out}< <(printf %s ' + shlex.quote(reply) + ')\naccept prep-review\n'
        return subprocess.run([bash, '-s'], input=program, text=True, capture_output=True, timeout=5)

    def test_each_harness_check_reaches_shell_and_clears_sessions(self):
        for check_id in CHECK_IDS:
            references = []
            def phase(state):
                references.append(state)
                state['owner_cookie'] = secrets.token_hex(24)
                with check(check_id):
                    raise HTTPStatusFailure(202, 422)
            output = io.StringIO()
            result = serve({'prep-setup': lambda s: None, 'prep-review': phase},
                io.StringIO('prep-setup\nprep-review\n'), output, profile='preparation-denied')
            self.assertEqual(result, 1)
            self.assertTrue(all(not s for s in references))
            shell = self.shell_review(output.getvalue().splitlines()[-1] + '\n')
            self.assertTrue(shell.stdout == 'FAIL prep-review HTTP_STATUS_MISMATCH ' + check_id + ' expected=202 actual=422\n',
                            'safe check did not reach shell')
            self.assertTrue(shell.returncode == 1 and not shell.stderr)

    def test_check_failure_types_and_hostile_metadata_never_escape(self):
        marker = secrets.token_hex(24)
        for error, reason in ((AssertionError(marker), 'ASSERTION_FAILED'),
                              (TimeoutError(marker), 'TIMEOUT'),
                              (KeyboardInterrupt(marker), 'CANCELLED'),
                              (ValueError(marker), 'UNCLASSIFIED_FAILURE')):
            def phase(state):
                with check('IMAGE_UPLOAD'):
                    print(marker); print(marker, file=sys.stderr)
                    raise error
            output = io.StringIO()
            self.assertEqual(serve({'prep-setup': lambda s: None, 'prep-review': phase},
                io.StringIO('prep-setup\nprep-review\n'), output, profile='preparation-denied'), 1)
            shell = self.shell_review(output.getvalue().splitlines()[-1] + '\n')
            self.assertTrue(shell.stdout == 'FAIL prep-review ' + reason + ' IMAGE_UPLOAD\n')
            self.assertTrue(marker not in shell.stdout + shell.stderr + output.getvalue(), 'sensitive diagnostic escaped')
        # Do not trust fields even from a typed exception; no line injection.
        for error in (CheckFailure(marker + '\nPASS prep-review', 'ASSERTION_FAILED'),
                      CheckFailure('OWNER_LOGIN', marker),
                      CheckFailure('OWNER_LOGIN', 'HTTP_STATUS_MISMATCH', 202, marker),
                      CheckFailure('OWNER_LOGIN', 'HTTP_STATUS_MISMATCH', True, 422)):
            def phase(state): raise error
            output = io.StringIO()
            serve({'prep-setup': lambda s: None, 'prep-review': phase},
                  io.StringIO('prep-setup\nprep-review\n'), output, profile='preparation-denied')
            self.assertTrue(output.getvalue().endswith('FAIL prep-review UNCLASSIFIED_FAILURE\n'))
            self.assertTrue(marker not in output.getvalue(), 'untrusted diagnostic escaped')
        for reply in ('FAIL prep-review ASSERTION_FAILED ' + marker + '\nPASS prep-review\n',
                      'FAIL prep-review ' + marker + ' OWNER_LOGIN\n',
                      'FAIL prep-review HTTP_STATUS_MISMATCH OWNER_LOGIN 202 999\n',
                      'FAIL prep-review ASSERTION_FAILED OWNER_LOGIN 202 422\n'):
            shell = self.shell_review(reply)
            self.assertTrue(shell.stdout == 'FAIL prep-review UNCLASSIFIED_FAILURE\n' and not shell.stderr)
            self.assertEqual(shell.returncode, 1)

    def test_failure_evidence_is_bounded_before_cleanup_and_keeps_first_error(self):
        bash = 'C:/Program Files/Git/bin/bash.exe' if os.name == 'nt' else shutil.which('bash')
        source = Path(__file__).with_name('run_linux.sh').read_text()
        functions = source[source.index('collect_simulator_evidence() {'):source.index('trap cleanup EXIT')]
        for evidence_result in (0, 1, 124):
            # Shell collaborators only; no Docker, network or host mutation.
            program = 'PATH=/usr/bin:$PATH\n' + functions + '''
mode=--preparation-denied
probe=probe; mongo=mongo; web1=web1; web2=web2; worker=worker; simulator=simulator
timeout() { echo EVIDENCE_ATTEMPT; return ''' + str(evidence_result) + '''; }
docker() { return 0; }
trap cleanup EXIT
echo 'FAIL prep-review ASSERTION_FAILED REVIEW_STORED_STATE'
exit 19
'''
            result = subprocess.run([bash, '-s'], input=program, text=True, capture_output=True, timeout=5)
            self.assertEqual(result.returncode, 19)
            self.assertTrue(result.stdout.startswith('FAIL prep-review ASSERTION_FAILED REVIEW_STORED_STATE\nEVIDENCE_ATTEMPT\n'))
            self.assertEqual(result.stdout.count('EVIDENCE_ATTEMPT'), 1)
            self.assertEqual('SIMULATOR_EVIDENCE unavailable' in result.stdout, evidence_result != 0)
            self.assertFalse(result.stderr)
        self.assertLess(functions.index('collect_simulator_evidence || true'), functions.index('docker rm -f'))
        self.assertIn('timeout --kill-after=1s 6s', functions)

    def exercise(self, mode):
        issued = [secrets.token_hex(24), secrets.token_hex(24)]
        references, consumed = [], []
        output, errors, protocol = io.StringIO(), io.StringIO(), io.StringIO()

        def setup(state):
            references.append(state)
            state.update(owner="synthetic-owner", employee="synthetic-employee")

        def http(state):
            state.update(owner_cookie=issued[0], employee_cookie=issued[1])
            # Fault injection: even dependency output/assertions cannot echo state.
            print(issued[0])
            print(issued[1], file=sys.stderr)
            if mode == "error":
                raise AssertionError(issued[0])
            if mode == "cancel":
                raise KeyboardInterrupt(issued[1])

        def outage(state):
            consumed.append(state["owner_cookie"])

        def restarted(state):
            consumed.extend([state["owner_cookie"], state["employee_cookie"]])
            if not (consumed[0] is issued[0] and consumed[1] is issued[0]
                    and consumed[2] is issued[1]):
                raise AssertionError("original session identity not retained")

        commands = "setup\nhttp\n" if mode == "eof" else "\n".join((*PHASES, "finish")) + "\n"
        phases = dict(zip(PHASES, (setup, http, outage, restarted)))
        with tempfile.TemporaryDirectory() as directory:
            previous = Path.cwd()
            try:
                os.chdir(directory)
                with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
                    result = serve(phases, io.StringIO(commands), protocol)
                files = list(Path(directory).rglob("*"))
                if files:
                    self.fail("controller created persistent state/artifact files")
            finally:
                os.chdir(previous)
        combined = output.getvalue() + errors.getvalue() + protocol.getvalue()
        if any(value in combined for value in issued):
            self.fail("session material escaped into controller output")
        self.assertTrue(all(not state for state in references), "state references not cleared")
        self.assertEqual(result, 0 if mode == "success" else 1)
        if mode == "success":
            self.assertEqual(protocol.getvalue(), "".join("PASS " + p + "\n" for p in (*PHASES, "finish")))
        else:
            expected = {"error": "FAIL http ASSERTION_FAILED\n",
                        "cancel": "FAIL http CANCELLED\n",
                        "eof": "FAIL mongo-down CHANNEL_CLOSED\n"}[mode]
            if not protocol.getvalue().endswith(expected):
                self.fail("missing safe phase/reason diagnostic")

    def test_original_sessions_survive_phase_boundary_without_files_or_output(self):
        self.exercise("success")

    def test_phase_exception_is_sanitized_and_state_cleared(self):
        self.exercise("error")

    def test_cancellation_is_failure_and_state_cleared(self):
        self.exercise("cancel")

    def test_premature_eof_is_failure_and_state_cleared(self):
        self.exercise("eof")

    def test_out_of_order_commands_never_start_a_phase(self):
        called = []
        phases = {name: lambda state: called.append(True) for name in PHASES}
        self.assertEqual(serve(phases, io.StringIO("http\n"), io.StringIO()), 1)
        self.assertFalse(called)

    def test_closed_output_does_not_expose_exception_or_retain_state(self):
        references = []
        def setup(state):
            references.append(state)
            state["owner_cookie"] = secrets.token_hex(24)
        output = io.StringIO()
        output.close()
        self.assertEqual(serve({"setup": setup}, io.StringIO("setup\n"), output), 1)
        if references[0]:
            self.fail("state survived output disconnection")

    def test_phase_and_exception_types_are_classified_without_values(self):
        for phase in PHASES:
            for error_type, reason in ((AssertionError, "ASSERTION_FAILED"),
                    (TimeoutError, "TIMEOUT"), (KeyboardInterrupt, "CANCELLED"),
                    (ValueError, "UNCLASSIFIED_FAILURE"),
                    (BrokenPipeError, "UNCLASSIFIED_FAILURE")):
                with self.subTest(phase=phase, reason=reason):
                    secret = secrets.token_hex(24)
                    references = []
                    def fail(state):
                        references.append(state)
                        state["owner_cookie"] = secret
                        raise error_type(secret)
                    phases = {name: (fail if name == phase else lambda state: None)
                              for name in PHASES}
                    output = io.StringIO()
                    result = serve(phases, io.StringIO("\n".join((*PHASES, "finish")) + "\n"), output)
                    if secret in output.getvalue():
                        self.fail("sensitive exception value escaped")
                    if not output.getvalue().endswith("FAIL " + phase + " " + reason + "\n"):
                        self.fail("incorrect safe failure classification")
                    self.assertEqual(result, 1)
                    self.assertTrue(all(not state for state in references), "state not cleared")

    def test_control_eof_order_and_read_failure_have_fixed_diagnostics(self):
        class BrokenInput:
            def readline(self, size):
                raise BrokenPipeError("synthetic-sensitive-input")
        for commands, expected in ((io.StringIO(""), "CHANNEL_CLOSED"),
                (io.StringIO("untrusted-input\n"), "PHASE_ORDER"),
                (BrokenInput(), "CHANNEL_CLOSED")):
            output = io.StringIO()
            self.assertEqual(serve({}, commands, output), 1)
            if output.getvalue() != "FAIL setup " + expected + "\n":
                self.fail("control failure diagnostic mismatch")

    def test_failing_unittest_output_does_not_echo_sensitive_diagnostic(self):
        secret = secrets.token_hex(24)
        class FailingExpectation(unittest.TestCase):
            def runTest(inner):
                def phase(state):
                    raise AssertionError(secret)
                output = io.StringIO()
                serve({"setup": phase}, io.StringIO("setup\n"), output)
                # Deliberately fail a consumer assertion to inspect unittest output.
                inner.assertEqual(output.getvalue(), "PASS setup\n")
        output = io.StringIO()
        result = unittest.TextTestRunner(stream=output).run(FailingExpectation())
        if secret in output.getvalue():
            self.fail("sensitive value escaped into failing test output")
        self.assertEqual(len(result.failures), 1)
        self.assertFalse(result.wasSuccessful())

    def test_shell_diagnostics_validate_protocol_and_preserve_failure(self):
        bash = shutil.which("bash")
        if os.name == "nt":
            bash = "C:/Program Files/Git/bin/bash.exe"
        self.assertTrue(bash and Path(bash).is_file(), "Bash required for protocol test")
        script = Path(__file__).with_name("run_linux.sh").read_text()
        start = script.index("accept() {")
        function = script[start:script.index("\nstart_webs() {", start)]
        # Shorten only this local test's wait; production keeps its 120s bound.
        function = function.replace("-t 120", "-t 0.02")
        fixtures = [
            ("exec {accept_out}< <(printf 'PASS http\\n')", "PASS acceptance phase: http", 0),
            ("exec {accept_out}< <(printf 'FAIL http ASSERTION_FAILED\\n')", "FAIL http ASSERTION_FAILED", 1),
            ("exec {accept_out}< <(printf 'FAIL http CANCELLED\\n')", "FAIL http CANCELLED", 1),
            ("exec {accept_out}< <(printf 'FAIL http PHASE_ORDER\\n')", "FAIL http PHASE_ORDER", 1),
            ("exec {accept_out}< <(printf 'FAIL setup ASSERTION_FAILED\\n')", "FAIL http UNCLASSIFIED_FAILURE", 1),
            ("exec {accept_out}< <(printf 'untrusted-sensitive-value\\n')", "FAIL http UNCLASSIFIED_FAILURE", 1),
            ("exec {accept_out}</dev/null", "FAIL http CHANNEL_CLOSED", 1),
            ("exec {accept_out}</dev/null; exec {accept_in}> >(exit 0); wait", "FAIL http CHANNEL_CLOSED", 1),
            ("exec {accept_out}< <(sleep 0.1)", "FAIL http TIMEOUT", 1),
        ]
        for fixture, expected, status in fixtures:
            program = "PATH=/usr/bin:$PATH\n" + function + "\nexec {accept_in}>/dev/null\n" + fixture + "\naccept http\n"
            result = subprocess.run([bash, "-s"], input=program, text=True,
                                    capture_output=True, timeout=5)
            if result.stdout.strip() != expected or result.stderr:
                self.fail("shell diagnostic mismatch; expected fixed status: " + expected)
            self.assertEqual(result.returncode, status)

        for signal_name, status in (("INT", 130), ("TERM", 143)):
            trap_line = next(line for line in script.splitlines()
                             if line.startswith("trap '") and line.endswith(" " + signal_name))
            program = function + "\naccept_phase=http\n" + trap_line + "\nkill -s " + signal_name + " $$\n"
            result = subprocess.run([bash, "-s"], input=program, text=True,
                                    capture_output=True, timeout=5)
            if result.stdout.strip() != "FAIL http CANCELLED" or result.stderr:
                self.fail("shell signal diagnostic mismatch")
            self.assertEqual(result.returncode, status)

        program = function + "\naccept untrusted-sensitive-value\n"
        result = subprocess.run([bash, "-s"], input=program, text=True,
                                capture_output=True, timeout=5)
        if result.stdout.strip() != "FAIL controller PHASE_ORDER" or result.stderr:
            self.fail("untrusted phase escaped shell validation")
        self.assertEqual(result.returncode, 1)

    def test_real_process_protocol_success_failure_eof_and_termination(self):
        # A real persistent process and anonymous pipes, not a state-store double.
        helper = str(Path(__file__).resolve().parent)
        source = """import sys, secrets
sys.path.insert(0, sys.argv[1])
from acceptance_controller import run, PHASES
original = []
def setup(state): state['metadata'] = 'synthetic'
def http(state):
    state['owner_cookie'] = secrets.token_hex(24)
    original.append(state['owner_cookie'])
def outage(state):
    assert state['owner_cookie'] is original[0], 'session identity changed'
def restart(state):
    assert state['owner_cookie'] is original[0], 'session identity changed'
raise SystemExit(run(dict(zip(PHASES, (setup, http, outage, restart)))))
"""
        for outcome in ("success", "failure", "eof", "terminate"):
            with self.subTest(outcome=outcome), tempfile.TemporaryDirectory() as directory:
                child = subprocess.Popen([sys.executable, "-B", "-c", source, helper],
                    cwd=directory, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE, text=True)
                try:
                    for phase in ("setup", "http"):
                        child.stdin.write(phase + "\n")
                        child.stdin.flush()
                        # Fixed protocol only; do not include received values in failures.
                        if child.stdout.readline() != "PASS " + phase + "\n":
                            self.fail("controller process protocol mismatch")
                    if outcome == "terminate":
                        child.terminate()
                        tail = ""
                    else:
                        tail = {"success": "mongo-down\nafter-restart\nfinish\n",
                                "failure": "invalid\n", "eof": ""}[outcome]
                    stdout, stderr = child.communicate(tail, timeout=5)
                    allowed = {"PASS mongo-down", "PASS after-restart", "PASS finish", "FAIL mongo-down PHASE_ORDER", "FAIL mongo-down CHANNEL_CLOSED",
                               "FAIL mongo-down CANCELLED"}
                    if stderr or any(line not in allowed for line in stdout.splitlines()):
                        self.fail("unexpected controller process output")
                    self.assertEqual(child.returncode == 0, outcome == "success")
                    self.assertFalse(list(Path(directory).rglob("*")), "temporary state survived process exit")
                finally:
                    if child.poll() is None:
                        child.kill()
                        child.communicate()


if __name__ == "__main__":
    unittest.main()
