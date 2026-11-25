"""Capture one fixed original guard phase; retain its original output and exit code."""
from pathlib import Path
import datetime
import hashlib
import json
import os
import stat
import subprocess
import sys
import tempfile

CONTROL = Path('/tmp/mezan-pr2129-operator-o1l3bv1c')
ROOT = Path('/app')
GUARD = ROOT / 'scripts' / 'production_release_guard.py'
ENVELOPE = CONTROL / 'execution-envelope.json'
GUARD_SHA = '4e56f1f60f5f79931658b8f95a0aeb2269471025ca84f7e5b651cd284806349a'
GUARD_BYTES = 68995
ENVELOPE_SHA = 'bbeb52d3573a8be9f1b7af30234e03121e9ac9dad1846616a5d8f5da517054a9'
D = 'b651eacf5ded173c4f3224d195ef874b8bcea312'
PHASE_ARGUMENTS = {
    'status-before-prepare': ('status',),
    'prepare': ('prepare', '--actor', 'mezan-dashboard-profit-pr2129-20261125'),
    'prepublish': ('prepublish',),
    'verify': ('verify', '--url', 'https://mezansalla.com'),
    'status-after-verify': ('status',),
}

def require(value, code):
    if not value:
        raise RuntimeError(code)

def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()

require(sys.flags.optimize == 0 and 'PYTHONOPTIMIZE' not in os.environ, 'OPTIMIZATION_NOT_ALLOWED')
require(len(sys.argv) == 2, 'EXACTLY_ONE_PHASE_REQUIRED')
phase = sys.argv[1]
require(phase in PHASE_ARGUMENTS, 'PHASE_NOT_ALLOWED')
stem = 'guard-' + phase
command = [sys.executable, '-B', str(GUARD), *PHASE_ARGUMENTS[phase]]
require(CONTROL.is_dir() and not CONTROL.is_symlink() and CONTROL.resolve(strict=True) == CONTROL, 'UNSAFE_CONTROL_ROOT')
require(stat.S_IMODE(CONTROL.stat().st_mode) == 0o700, 'CONTROL_MODE_CHANGED')
for parent in (ROOT, GUARD.parent):
    require(parent.is_dir() and not parent.is_symlink() and parent.resolve(strict=True) == parent, 'UNSAFE_GUARD_ROOT')
for path, expected, expected_bytes, expected_mode in [(GUARD, GUARD_SHA, GUARD_BYTES, None), (ENVELOPE, ENVELOPE_SHA, 1589, 0o600)]:
    info = path.lstat()
    require(stat.S_ISREG(info.st_mode) and not path.is_symlink(), 'UNSAFE_INPUT')
    require(expected_mode is None or stat.S_IMODE(info.st_mode) == expected_mode, 'INPUT_MODE_CHANGED')
    contents = path.read_bytes()
    require(len(contents) == expected_bytes and hashlib.sha256(contents).hexdigest() == expected, 'INPUT_HASH_CHANGED')
require(json.loads(ENVELOPE.read_bytes())['deployment_sha'] == D, 'DEPLOYMENT_CHANGED')
state = {'status': 'PREPARING', 'started_at': now(), 'wrapper_pid': os.getpid(), 'phase': phase, 'command': command, 'deployment_sha': D, 'guard_sha256': GUARD_SHA, 'envelope_sha256': ENVELOPE_SHA, 'guard_invocations': 0, 'guard_may_be_running': False}
state_path = CONTROL / (stem + '-state.json')
fd = os.open(state_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
with os.fdopen(fd, 'w') as stream:
    json.dump(state, stream, indent=2)
    stream.write('\n')
    stream.flush()
    os.fsync(stream.fileno())

def save_state():
    descriptor, temporary_name = tempfile.mkstemp(prefix=stem + '-state.', suffix='.tmp', dir=CONTROL)
    temporary = Path(temporary_name)
    with os.fdopen(descriptor, 'w') as stream:
        json.dump(state, stream, indent=2)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, state_path)
    descriptor = os.open(CONTROL, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)

try:
    output_fd = os.open(CONTROL / (stem + '.stdout'), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    error_fd = os.open(CONTROL / (stem + '.stderr'), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    environment = os.environ.copy()
    environment.pop('PYTHONPATH', None)
    environment.pop('PYTHONOPTIMIZE', None)
    environment['PYTHONDONTWRITEBYTECODE'] = '1'
    with os.fdopen(output_fd, 'wb') as output, os.fdopen(error_fd, 'wb') as error:
        state.update(status='LAUNCH_ARMED', guard_invocations=None, guard_may_be_running=True)
        save_state()
        process = subprocess.Popen(command, cwd='/app', env=environment, stdin=subprocess.DEVNULL, stdout=output, stderr=error)
        state.update(status='RUNNING', guard_invocations=1, guard_pid=process.pid)
        save_state()
        returncode = process.wait()
        output.flush()
        error.flush()
        os.fsync(output.fileno())
        os.fsync(error.fileno())
    state.update(status='COMPLETED', exit_code=returncode, guard_may_be_running=False, completed_at=now())
    save_state()
    raise SystemExit(returncode)
except SystemExit:
    raise
except BaseException as exc:
    state.update(status='WRAPPER_FAILED', exception_type=type(exc).__name__, completed_at=now())
    save_state()
    raise
