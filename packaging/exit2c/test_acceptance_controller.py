"""Local stdlib contracts; real Auth/web restart acceptance still requires Linux."""
import contextlib
import io
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
import unittest

from acceptance_controller import PHASES, serve


class ControllerTests(unittest.TestCase):
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
            self.assertTrue(protocol.getvalue().endswith("FAIL acceptance controller\n"))

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
                    allowed = {"PASS mongo-down", "PASS after-restart", "PASS finish", "FAIL acceptance controller"}
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
