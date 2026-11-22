"""Independent exact-B Linux rehearsal; never create a lease or publish."""
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import tempfile
import time

ROOT = Path('/app')
SOURCE = 'cdd0a2ceceb3830c857261f4bd16b6745ad750f1'
BASE = 'c302943e330bcdd534a4a5697b8ef813dfb7cb6e'
DEPLOYMENT = '3857bf871a2a5381d3dab0be2a43df3acebb00b8'
RELEASE = 'rg5-cf7e5ab9a7144318970e0b812695a15a29a200833850e79ebce3feafd93dd661'
INTENT_HASH = '33f7a4e0d6d8c376cfebc2164a22f41df1db4058c9b5e69b6fd665747cc7d607'
ENV = {key: value for key, value in os.environ.items()
       if key not in {'GITHUB_ACTIONS', 'GITHUB_SHA', 'GITHUB_WORKSPACE'}}
ENV.update(PYTHONDONTWRITEBYTECODE='1', REACT_APP_BACKEND_URL='https://mezansalla.com', CI='false')


def run(argv, *, cwd=ROOT, timeout=120, log=None):
    result = subprocess.run(argv, cwd=cwd, env=ENV, text=True,
                            stdout=log or subprocess.PIPE,
                            stderr=subprocess.STDOUT, timeout=timeout)
    assert result.returncode == 0, 'Rehearsal command failed; inspect its task log'
    return result.stdout.strip() if result.stdout is not None else ''


def git(*argv, cwd=ROOT):
    return run(['git', *argv], cwd=cwd)


def main():
    guard = json.loads(run(['python', '-B', 'scripts/production_release_guard.py', 'status']))
    assert guard.get('active') is False, 'Preserve the active owner lease'
    assert platform.machine() in {'aarch64', 'arm64'}, 'Expected independent ARM64 host'
    shared_head = git('rev-parse', 'HEAD')
    shared_status = git('status', '--porcelain', '--untracked-files=no')
    assert shared_head == BASE and not shared_status
    pointer = Path('/tmp/aramex-pr2061-stage.txt')
    assert not pointer.exists(), 'Inspect the prior task rehearsal before retrying'
    stage = Path(tempfile.mkdtemp(prefix='aramex-pr2061-3857-', dir='/tmp'))
    pointer.write_text(str(stage) + '\n')
    report = {'source': SOURCE, 'deployment': DEPLOYMENT, 'release': RELEASE,
              'success': False, 'arch': platform.machine(), 'lease_created': False,
              'published': False}
    started = time.monotonic()
    try:
        repo = stage / 'repo'
        git('clone', '--no-hardlinks', '--no-checkout', str(ROOT), str(repo))
        git('checkout', '--detach', DEPLOYMENT, cwd=repo)
        assert git('rev-parse', 'HEAD', cwd=repo) == DEPLOYMENT
        assert git('diff', '--name-only', SOURCE, DEPLOYMENT, cwd=repo) == 'release/release-intent-v5.json'
        assert not git('status', '--porcelain', cwd=repo)
        raw = (repo / 'release/release-intent-v5.json').read_bytes()
        assert hashlib.sha256(raw).hexdigest() == INTENT_HASH
        intent = json.loads(raw)
        assert intent['source_git_sha'] == SOURCE and intent['source_base_git_sha'] == BASE
        assert intent['runtime_identity']['release_id'] == RELEASE
        for name in ('frontend/node_modules', 'frontend/build',
                     'frontend/.release/reproducible-build.json', 'backend/release_identity.json'):
            assert not (repo / name).exists(), 'Generated input unexpectedly inherited'
        report['host_node'] = run(['node', '--version'], cwd=repo)
        report['host_yarn'] = run(['yarn', '--version'], cwd=repo)
        with (stage / 'adapter.log').open('w') as log:
            run(['yarn', 'build'], cwd=repo / 'frontend', timeout=1200, log=log)
        report['adapter_exit'] = 0
        with (stage / 'verifier.log').open('w') as log:
            run(['python', '-B', 'scripts/verify_frontend_build.py',
                 '--expected-git-sha', SOURCE, '--reviewed-intent-v5'], cwd=repo, log=log)
        report['verifier_exit'] = 0
        assert json.loads((repo / 'backend/release_identity.json').read_bytes()) == intent['runtime_identity']
        assert not git('status', '--porcelain', cwd=repo)
        assert git('rev-parse', 'HEAD') == shared_head
        assert git('status', '--porcelain', '--untracked-files=no') == shared_status
        report.update(success=True, shared_unchanged=True, intent_sha256=INTENT_HASH,
                      artifact_tree_sha256=intent['frontend_build']['artifact_tree_sha256'])
    finally:
        report['elapsed_seconds'] = round(time.monotonic() - started, 3)
        (stage / 'result.json').write_text(json.dumps(report, indent=2) + '\n')
        print('ARAMEX_2061_REHEARSAL=' + json.dumps(report, sort_keys=True), flush=True)


if __name__ == '__main__':
    main()
