cd /app && PYTHONDONTWRITEBYTECODE=1 GIT_TERMINAL_PROMPT=0 python -B - <<'PY'
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

APP = Path('/app')
BASE = '06763ffd6e3c3f7c861769b61d2c4e6b9b2d5f35'
EVIDENCE = '916e5f5f37e1651e2379174c1afa96e2a0204202'
FILE = 'docs/operations/CUSTOMER-INVENTORY-UNIFIED-20261122/MANUAL-HOST-ISOLATED-REHEARSAL-2074.py'
SHA256 = 'b11576e193ecd80994a3127474433e50a75fa624eb78e22e4bbeca5e5e03c9d6'

def run(label, argv, cwd=APP):
    try:
        result = subprocess.run(
            argv, cwd=cwd, capture_output=True,
            stdin=subprocess.DEVNULL, timeout=300,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise SystemExit('STOP: ' + label + ' could not complete') from None
    if result.returncode:
        raise SystemExit('STOP: ' + label + ' exit=' + str(result.returncode))
    return result.stdout

if any(key in os.environ for key in (
    'GITHUB_ACTIONS', 'GITHUB_SHA', 'GITHUB_WORKSPACE', 'GIT_DIR',
    'GIT_WORK_TREE', 'GIT_INDEX_FILE', 'GIT_OBJECT_DIRECTORY',
    'GIT_ALTERNATE_OBJECT_DIRECTORIES',
)):
    raise SystemExit('STOP: unexpected CI or Git routing environment')

state = json.loads(run('original release guard', [
    sys.executable, '-B', 'scripts/production_release_guard.py', 'status',
]))
if state != {'active': False}:
    raise SystemExit('STOP: release guard is not fully inactive; lease preserved')
head = run('shared HEAD', ['git', '--no-optional-locks', 'rev-parse', 'HEAD']).decode().strip()
dirty = run('shared source status', [
    'git', '--no-optional-locks', 'status', '--porcelain', '--untracked-files=no',
])
if head != BASE or dirty.strip():
    raise SystemExit('STOP: shared source changed; nothing updated')

work = Path(tempfile.mkdtemp(prefix='mezan-pr2074-launcher-', dir='/tmp'))
repo = work / 'repo'
run('isolated helper checkout', [
    'git', 'clone', '--local', '--no-hardlinks', '--no-checkout', str(APP), str(repo),
], cwd=work)
remote = run('origin lookup', ['git', 'remote', 'get-url', 'origin']).decode().strip()
run('fetch pinned helper', [
    'git', '--no-optional-locks', 'fetch', '--no-tags', '--no-write-fetch-head',
    '--no-auto-maintenance', remote, EVIDENCE,
], cwd=repo)
del remote
content = run('read pinned helper', ['git', 'show', EVIDENCE + ':' + FILE], cwd=repo)
if hashlib.sha256(content).hexdigest() != SHA256:
    raise SystemExit('STOP: helper checksum mismatch')
helper = work / 'rehearse.py'
helper.write_bytes(content)
print('HELPER_VERIFIED', SHA256, flush=True)
sys.path.insert(0, str(APP))
exec(compile(content, str(helper), 'exec'), {
    '__name__': '__main__', '__file__': str(helper),
})
PY
