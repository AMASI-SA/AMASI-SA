"""Real child exit and parent-pipe lifetime, without a database or network."""
from contextlib import contextmanager
from pathlib import Path
import selectors
import subprocess
import sys

import pytest


BACKEND = Path(__file__).resolve().parents[1]


@contextmanager
def watched_child(body):
    code = (
        "import resource, sys, time\n"
        "resource.setrlimit(resource.RLIMIT_CORE, (0, 0))\n"
        "from demand_read_runner import watch_parent_pipe\n"
        "watch_parent_pipe()\n"
        "print('watching', flush=True)\n"
        + body
    )
    process = subprocess.Popen(
        [sys.executable, "-B", "-c", code], cwd=BACKEND,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    try:
        with selectors.DefaultSelector() as ready:
            ready.register(process.stdout, selectors.EVENT_READ)
            assert ready.select(timeout=5), "child did not start its parent watcher"
            assert process.stdout.readline() == b"watching\n"
        yield process
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)
        for pipe in (process.stdin, process.stdout, process.stderr):
            pipe.close()


@pytest.mark.parametrize("exit_code", [0, 1])
def test_finished_child_keeps_its_exit_status_with_parent_pipe_open(exit_code):
    with watched_child(f"time.sleep(.05)\nsys.exit({exit_code})\n") as process:
        # Do not communicate(): it closes stdin and would hide a shutdown lock.
        process.wait(timeout=5)
        stderr = process.stderr.read().decode(errors="replace")
        assert process.returncode == exit_code, stderr
        assert stderr == ""


def test_parent_pipe_eof_stops_a_busy_child_but_data_does_not():
    with watched_child("while True:\n    pass\n") as process:
        process.stdin.write(b"still connected")
        process.stdin.flush()
        with pytest.raises(subprocess.TimeoutExpired):
            process.wait(timeout=.1)
        process.stdin.close()
        process.wait(timeout=5)
        stderr = process.stderr.read().decode(errors="replace")
        assert process.returncode == 2, stderr
        assert stderr == ""
