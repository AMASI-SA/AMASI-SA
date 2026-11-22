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
EVIDENCE = 'afdbd4e293760938706849232a15f0afe6fa1edf'
PREFIX = 'docs/operations/CUSTOMER-INVENTORY-UNIFIED-20261122/'
FILES = (
    ('MANUAL-HOST-STAGE-2074.py', 'e52f11663f943d9a4ab24f917b9ccd4a83ea8526cf1dd471867029c3fda3adde'),
    ('MANUAL-HOST-PREPARE-PREPUBLISH-2074.py', 'a02f10c61751c439e38e81880aaa555bbe43ad7bc9a2906c8f6ab334d91c0bca'),
    ('RUN-HOST-VERIFY-2074.sh', 'e23d910b9c03ef8e979a7f78cc4cc8947e640ced03efdce35953c1efd88e132e'),
)

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

if any(os.environ.get(key) for key in (
    'GITHUB_ACTIONS', 'GITHUB_SHA', 'GITHUB_WORKSPACE', 'GIT_DIR',
    'GIT_WORK_TREE', 'GIT_COMMON_DIR', 'GIT_INDEX_FILE', 'GIT_OBJECT_DIRECTORY',
    'GIT_ALTERNATE_OBJECT_DIRECTORIES', 'GIT_NAMESPACE', 'GIT_CONFIG_PARAMETERS',
    'GIT_CONFIG_COUNT', 'GIT_CONFIG', 'GIT_CONFIG_SYSTEM', 'GIT_CONFIG_GLOBAL',
    'GIT_TRACE', 'GIT_TRACE_PACKET', 'GIT_TRACE_CURL', 'GIT_CURL_VERBOSE',
    'GIT_TRACE2_EVENT', 'GIT_TRACE2_PERF',
)):
    raise SystemExit('STOP: unexpected CI or Git routing environment')
state = json.loads(run('original release guard', [
    sys.executable, '-B', 'scripts/production_release_guard.py', 'status',
]))
if state != {'active': False}:
    raise SystemExit('STOP: existing or incomplete lease; preserved')
head = run('shared HEAD', ['git', '--no-optional-locks', 'rev-parse', 'HEAD']).decode().strip()
dirty = run('shared source status', [
    'git', '--no-optional-locks', 'status', '--porcelain', '--untracked-files=no',
])
if head != BASE or dirty.strip():
    raise SystemExit('STOP: shared source changed; nothing updated')

work = Path(tempfile.mkdtemp(prefix='mezan-pr2074-deploy-', dir='/tmp'))
repo = work / 'repo'
run('isolated helper checkout', [
    'git', 'clone', '--local', '--no-hardlinks', '--no-checkout', str(APP), str(repo),
], cwd=work)
remote = run('origin lookup', ['git', 'remote', 'get-url', 'origin']).decode().strip()
run('fetch pinned helpers', [
    'git', '--no-optional-locks', 'fetch', '--no-tags', '--no-write-fetch-head',
    '--no-auto-maintenance', remote, EVIDENCE,
], cwd=repo)
del remote
payloads = []
for name, expected in FILES:
    content = run('read pinned helper', ['git', 'show', EVIDENCE + ':' + PREFIX + name], cwd=repo)
    if hashlib.sha256(content).hexdigest() != expected:
        raise SystemExit('STOP: helper checksum mismatch: ' + name)
    payloads.append((name, content))
    print('HELPER_VERIFIED', name, expected, flush=True)

verification = work / 'verify-after-success.sh'
verification.write_bytes(payloads[2][1])
verification.chmod(0o400)

for name, content in payloads[:2]:
    result = subprocess.run(
        [sys.executable, '-B', '-c', content.decode('utf-8')],
        cwd=APP, stdin=subprocess.DEVNULL,
    )
    if result.returncode:
        raise SystemExit('STOP: ' + name + ' failed; retain the printed recovery details and do not publish')
print('VERIFY_COMMAND:', 'bash ' + str(verification), flush=True)
print('Run VERIFY_COMMAND only after the new Deployment Succeeded.', flush=True)
PY
