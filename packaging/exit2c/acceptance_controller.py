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
    try:
        for name in PHASES:
            if commands.readline(64) != name + "\n":
                raise RuntimeError("invalid phase order or closed control pipe")
            # Dependencies and failed assertions must not echo session values.
            # Only the fixed successful phase status crosses the output pipe.
            with redirect_stdout(Discard()), redirect_stderr(Discard()):
                phases[name](state)
            replies.write("PASS " + name + "\n")
            replies.flush()
        if commands.readline(64) != "finish\n":
            raise RuntimeError("missing completion acknowledgement")
        state.clear()
        replies.write("PASS finish\n")
        replies.flush()
        return 0
    except BaseException:
        # Never format exception text/locals: either may contain session values.
        try:
            replies.write("FAIL acceptance controller\n")
            replies.flush()
        except BaseException:
            pass  # A disconnected reader must not trigger a secret-bearing traceback.
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
