"""Memory-only acceptance state; pipes expose fixed control messages only.

No state/metadata file, encryption key or serialized session crosses phases.
EOF, phase failure and cancellation fail closed and release state references.
Process teardown is the lifetime boundary, not a claim of memory zeroization.
"""
from contextlib import redirect_stderr, redirect_stdout
import signal
import sys

PHASES = ("setup", "http", "mongo-down", "after-restart")


class Discard:
    def write(self, text):
        return len(text)

    def flush(self):
        pass


def serve(phases, commands, replies):
    state = {}
    name = "setup"
    reason = "UNCLASSIFIED_FAILURE"
    in_phase = False
    try:
        for name in (*PHASES, "finish"):
            command = commands.readline(64)
            if command == "":
                reason = "CHANNEL_CLOSED"
                raise RuntimeError
            if command != name + "\n":
                reason = "PHASE_ORDER"
                raise RuntimeError
            if name == "finish":
                state.clear()
            else:
                # Never format exception text/locals or echo phase output.
                in_phase = True
                with redirect_stdout(Discard()), redirect_stderr(Discard()):
                    phases[name](state)
                in_phase = False
            replies.write("PASS " + name + "\n")
            replies.flush()
        return 0
    except BaseException as error:
        # Classify only observed types/context, never messages or external fields.
        if isinstance(error, KeyboardInterrupt):
            reason = "CANCELLED"
        elif isinstance(error, TimeoutError):
            reason = "TIMEOUT"
        elif in_phase and isinstance(error, AssertionError):
            reason = "ASSERTION_FAILED"
        elif not in_phase and isinstance(error, (BrokenPipeError, EOFError)):
            reason = "CHANNEL_CLOSED"
        try:
            replies.write("FAIL " + name + " " + reason + "\n")
            replies.flush()
        except BaseException:
            pass  # Disconnected output cannot carry a diagnostic; still fail.
        return 1
    finally:
        state.clear()


def run(phases):
    def cancelled(signum, frame):
        raise KeyboardInterrupt

    previous = signal.signal(signal.SIGTERM, cancelled)
    try:
        return serve(phases, sys.stdin, sys.stdout)
    finally:
        signal.signal(signal.SIGTERM, previous)
