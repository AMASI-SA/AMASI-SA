"""Run the reviewed recovery once and retain its original output and exit code."""
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
HELPER = CONTROL / 'pr2129_bytecode_preservation_recovery.py'
ENVELOPE = CONTROL / 'execution-envelope.json'
HELPER_SHA = '82d89dd2f9bae77a967f299f452e25271424a53936dc2f27570a2104115b0fc0'
ENVELOPE_SHA = 'bbeb52d3573a8be9f1b7af30234e03121e9ac9dad1846616a5d8f5da517054a9'
D = 'b651eacf5ded173c4f3224d195ef874b8bcea312'

def require(value, code):
    if not value:
        raise RuntimeError(code)

def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()

require(sys.flags.optimize == 0 and 'PYTHONOPTIMIZE' not in os.environ, 'OPTIMIZATION_NOT_ALLOWED')
require(CONTROL.is_dir() and not CONTROL.is_symlink() and CONTROL.resolve(strict=True) == CONTROL, 'UNSAFE_CONTROL_ROOT')
require(stat.S_IMODE(CONTROL.stat().st_mode) == 0o700, 'CONTROL_MODE_CHANGED')
for path, expected in [(HELPER, HELPER_SHA), (ENVELOPE, ENVELOPE_SHA)]:
    info = path.lstat()
    require(stat.S_ISREG(info.st_mode) and not path.is_symlink(), 'UNSAFE_INPUT')
    require(stat.S_IMODE(info.st_mode) == 0o600, 'INPUT_MODE_CHANGED')
    require(hashlib.sha256(path.read_bytes()).hexdigest() == expected, 'INPUT_HASH_CHANGED')
require(json.loads(ENVELOPE.read_bytes())['deployment_sha'] == D, 'DEPLOYMENT_CHANGED')
state = {'status': 'PREPARING', 'started_at': now(), 'wrapper_pid': os.getpid(), 'deployment_sha': D, 'helper_sha256': HELPER_SHA, 'envelope_sha256': ENVELOPE_SHA, 'helper_invocations': 0, 'helper_may_be_running': False}
state_path = CONTROL / 'bytecode-recovery-state.json'
fd = os.open(state_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
with os.fdopen(fd, 'w') as stream:
    json.dump(state, stream, indent=2)
    stream.write('\n')
    stream.flush()
    os.fsync(stream.fileno())

def save_state():
    descriptor, temporary_name = tempfile.mkstemp(prefix='bytecode-recovery-state.', suffix='.tmp', dir=CONTROL)
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
    output_fd = os.open(CONTROL / 'bytecode-recovery.stdout', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    error_fd = os.open(CONTROL / 'bytecode-recovery.stderr', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    environment = os.environ.copy()
    environment.pop('PYTHONPATH', None)
    environment.pop('PYTHONOPTIMIZE', None)
    environment['PYTHONDONTWRITEBYTECODE'] = '1'
    with os.fdopen(output_fd, 'wb') as output, os.fdopen(error_fd, 'wb') as error:
        state.update(status='LAUNCH_ARMED', helper_invocations=None, helper_may_be_running=True)
        save_state()
        process = subprocess.Popen([sys.executable, '-B', str(HELPER), str(ENVELOPE)], cwd='/app', env=environment, stdin=subprocess.DEVNULL, stdout=output, stderr=error)
        state.update(status='RUNNING', helper_invocations=1, helper_pid=process.pid)
        save_state()
        returncode = process.wait()
        output.flush()
        error.flush()
        os.fsync(output.fileno())
        os.fsync(error.fileno())
    state.update(status='COMPLETED', exit_code=returncode, helper_may_be_running=False, completed_at=now())
    save_state()
    raise SystemExit(returncode)
except SystemExit:
    raise
except BaseException as exc:
    state.update(status='WRAPPER_FAILED', exception_type=type(exc).__name__, completed_at=now())
    save_state()
    raise
