cd /app && PYTHONDONTWRITEBYTECODE=1 GIT_TERMINAL_PROMPT=0 python -B - <<'PY'
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

APP = Path('/app')
BASE = '24270ebdfdebb83282389308a2349cdb27e2b8e4'
DEPLOYMENT = '02cd99e5b2b68c95341c87590abcf572c885e697'
CANDIDATE = '5bb05036d8095a87c6635c0a431c2c19d24d8f78'
EVIDENCE = 'bc7a91bb32baff7e3a92b8020cac6df25403848b'
PREFIX = 'docs/operations/OPERATIONAL-DEMAND-LATENCY-20261122/'
FILES = (
    ('MANUAL-HOST-STAGE-2076.py', '3906f0c84731a9de511854ae0a382fd7d0755f92cc9669a03a9c2aa7cb5aac89'),
    ('MANUAL-HOST-PREPARE-PREPUBLISH-2076.py', 'a3f7ecac60f74228a7cbccd0e5a6b180424202478f8847910f2c5c74fe25aa67'),
    ('RUN-HOST-VERIFY-2076.sh', '58315f10c66f75c4c6bfb5f8d70803d33125d4cc183888f86c687584c203ff7a'),
)


def main():
    if not isinstance(EVIDENCE, str) or not re.fullmatch(r'[0-9a-f]{40}', EVIDENCE):
        raise SystemExit('STOP: reviewed command is not pinned')
    rejected = (
        'GITHUB_ACTIONS', 'GITHUB_SHA', 'GITHUB_WORKSPACE', 'GIT_DIR',
        'GIT_WORK_TREE', 'GIT_COMMON_DIR', 'GIT_INDEX_FILE', 'GIT_OBJECT_DIRECTORY',
        'GIT_ALTERNATE_OBJECT_DIRECTORIES', 'GIT_SHALLOW_FILE', 'GIT_NAMESPACE',
        'GIT_CONFIG_PARAMETERS', 'GIT_CONFIG_COUNT', 'GIT_CONFIG',
        'GIT_CONFIG_SYSTEM', 'GIT_CONFIG_GLOBAL', 'GIT_CURL_VERBOSE',
    )
    if any(key in os.environ for key in rejected) or any(
            key.startswith('GIT_TRACE') for key in os.environ):
        raise SystemExit('STOP: unexpected CI, Git routing or tracing environment')
    if Path.cwd().resolve() != APP or APP.is_symlink():
        raise SystemExit('STOP: original /app directory required')
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1',
               PYTHONOPTIMIZE='0', GIT_TERMINAL_PROMPT='0')

    def run(label, argv, *, cwd=APP, timeout=300):
        try:
            result = subprocess.run(argv, cwd=cwd, env=env, capture_output=True,
                stdin=subprocess.DEVNULL, timeout=timeout)
        except (OSError, subprocess.TimeoutExpired):
            raise SystemExit('STOP: ' + label + ' could not complete') from None
        if result.returncode:
            raise SystemExit('STOP: ' + label + ' exit=' + str(result.returncode))
        return result.stdout

    def git(*args, cwd=APP, timeout=300):
        return run('git ' + args[0], ['git', '--no-optional-locks',
            '-c', 'maintenance.auto=false', '-c', 'gc.auto=0', *args],
            cwd=cwd, timeout=timeout)

    state = json.loads(run('original guard status', [
        sys.executable, '-B', 'scripts/production_release_guard.py', 'status',
    ], timeout=5))
    if state != {'active': False} or state.get('active') is not False:
        raise SystemExit('STOP: existing or incomplete lease; preserved')
    if (git('rev-parse', 'HEAD', timeout=20).decode().strip() != BASE
            or git('status', '--porcelain', '--untracked-files=no', timeout=20)):
        raise SystemExit('STOP: shared source changed; nothing updated')
    prod_ref = 'refs/heads/hotfix/prod-snap-meta-final'
    candidate_ref = 'refs/heads/codex/operational-demand-latency-r3-20261122'
    lines = git('ls-remote', '--exit-code', 'origin', prod_ref, candidate_ref).decode().splitlines()
    rows = [line.split() for line in lines]
    if (len(rows) != 2 or any(len(row) != 2 for row in rows)
            or {row[1]: row[0] for row in rows} !=
            {prod_ref: DEPLOYMENT, candidate_ref: CANDIDATE}):
        raise SystemExit('STOP: remote release references changed')

    work = Path(tempfile.mkdtemp(prefix='mezan-pr2076-deploy-', dir='/tmp'))
    repo = work / 'repo'
    print('COMMAND_DIRECTORY', str(work), flush=True)
    git('clone', '--local', '--no-hardlinks', '--no-checkout', str(APP), str(repo), cwd=work)
    remote = git('remote', 'get-url', 'origin', timeout=20).decode().strip()
    git('fetch', '--no-tags', '--no-write-fetch-head', '--no-auto-maintenance',
        remote, EVIDENCE, cwd=repo)
    del remote
    payloads = []
    for name, expected in FILES:
        content = git('show', EVIDENCE + ':' + PREFIX + name, cwd=repo, timeout=20)
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
            cwd=APP, env=env, stdin=subprocess.DEVNULL,
        )
        if result.returncode:
            raise SystemExit('STOP: ' + name + ' failed; retain recovery details and do not publish')
    print('VERIFY_COMMAND:', 'bash ' + str(verification), flush=True)
    print('Run VERIFY_COMMAND only after the NEW Deployment Succeeded for PR2076.', flush=True)


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        raise SystemExit('STOP: deployment command could not complete ('
                         + type(error).__name__ + '); retain recovery details') from None
PY
