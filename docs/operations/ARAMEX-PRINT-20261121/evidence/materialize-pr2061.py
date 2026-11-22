"""Materialize the already verified, tree-equivalent release; create no lease."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import re
import sys
import subprocess
import tempfile

ROOT = Path('/app')
SOURCE = 'cdd0a2ceceb3830c857261f4bd16b6745ad750f1'
INTENT = '3857bf871a2a5381d3dab0be2a43df3acebb00b8'
BASE = 'c302943e330bcdd534a4a5697b8ef813dfb7cb6e'
assert len(sys.argv) == 2, 'Pass the reviewed normal merge SHA'
MERGE = sys.argv[1]
assert re.fullmatch(r'[0-9a-f]{40}', MERGE)
RELEASE = 'rg5-cf7e5ab9a7144318970e0b812695a15a29a200833850e79ebce3feafd93dd661'
ENV = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')

def git(*args, cwd=ROOT):
    result = subprocess.run(['git', *args], cwd=cwd, env=ENV,
        capture_output=True, text=True, timeout=75)
    assert result.returncode == 0, 'Git operation failed; shared files preserved'
    return result.stdout.strip()

def main():
    guard = subprocess.run(['python', '-B', 'scripts/production_release_guard.py', 'status'],
        cwd=ROOT, env=ENV, capture_output=True, text=True, timeout=45, check=True)
    assert json.loads(guard.stdout).get('active') is False
    assert git('rev-parse', '--abbrev-ref', 'HEAD') == 'hotfix/prod-snap-meta-final'
    shared_head = git('rev-parse', 'HEAD')
    assert shared_head in {BASE, MERGE}
    assert not git('status', '--porcelain', '--untracked-files=no')
    stage = Path(Path('/tmp/aramex-pr2061-stage.txt').read_text().strip())
    assert stage.parent == Path('/tmp') and stage.name.startswith('aramex-pr2061-3857-')
    result = json.JSONDecoder().raw_decode((stage / 'result.json').read_text())[0]
    assert result.get('success') is True and result.get('adapter_exit') == 0 and result.get('verifier_exit') == 0
    assert result['source'] == SOURCE and result['deployment'] == INTENT and result['release'] == RELEASE
    repo = stage / 'repo'
    assert git('rev-parse', 'HEAD', cwd=repo) == INTENT
    intent = json.loads((repo / 'release/release-intent-v5.json').read_bytes())
    assert intent['source_git_sha'] == SOURCE and intent['source_base_git_sha'] == BASE
    assert hashlib.sha256((repo / 'release/release-intent-v5.json').read_bytes()).hexdigest() == '33f7a4e0d6d8c376cfebc2164a22f41df1db4058c9b5e69b6fd665747cc7d607'
    assert json.loads((repo / 'backend/release_identity.json').read_bytes()) == intent['runtime_identity']
    outputs = ['frontend/build', 'frontend/.release/reproducible-build.json', 'backend/release_identity.json']
    required = sum(p.stat().st_size for relative in outputs
        for p in ((repo / relative).rglob('*') if (repo / relative).is_dir() else [repo / relative]) if p.is_file())
    assert shutil.disk_usage(ROOT).free > required + 8 * 1024 * 1024, 'Insufficient staging capacity; preserve unrelated files'
    git('fetch', 'origin', 'hotfix/prod-snap-meta-final')
    assert git('rev-parse', 'origin/hotfix/prod-snap-meta-final') == MERGE
    assert git('rev-parse', MERGE + '^{tree}') == git('rev-parse', INTENT + '^{tree}')
    assert git('diff', '--name-only', SOURCE, MERGE) == 'release/release-intent-v5.json'
    git('merge', '--ff-only', MERGE)
    assert git('rev-parse', 'HEAD') == MERGE
    # Only these governed ignored outputs are replaced. Preserve previous copies.
    pending = Path(tempfile.mkdtemp(prefix='.aramex-pr2061-materialize-', dir=ROOT))
    assert pending.resolve().parent == ROOT.resolve() and not pending.is_symlink()
    backup = stage / 'previous-generated'
    for relative in outputs:
        source, temporary = repo / relative, pending / relative
        assert source.exists() and not source.is_symlink()
        temporary.parent.mkdir(parents=True, exist_ok=True)
        if source.is_dir():
            shutil.copytree(source, temporary)
        else:
            shutil.copy2(source, temporary)
    for relative in outputs:
        target = ROOT / relative
        assert target.resolve().is_relative_to(ROOT.resolve()) and not target.is_symlink()
        if target.exists():
            previous = backup / relative
            assert not previous.exists(), 'Previous materialization backup already exists; inspect before retry'
            previous.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(target), str(previous))
        target.parent.mkdir(parents=True, exist_ok=True)
        os.replace(pending / relative, target)
    assert pending.resolve().parent == ROOT.resolve() and pending.name.startswith('.aramex-pr2061-materialize-')
    shutil.rmtree(pending)  # Own empty staging hierarchy, verified within /app.
    with (stage / 'shared-verifier.log').open('w') as log:
        verified = subprocess.run(['python', '-B', 'scripts/verify_frontend_build.py',
            '--expected-git-sha', SOURCE, '--reviewed-intent-v5'], cwd=ROOT, env=ENV,
            stdout=log, stderr=subprocess.STDOUT, timeout=120)
    assert verified.returncode == 0, 'Shared verifier failed; no lease or publish performed'
    assert json.loads((ROOT / 'backend/release_identity.json').read_bytes()) == intent['runtime_identity']
    assert git('rev-parse', 'HEAD') == MERGE and not git('status', '--porcelain', '--untracked-files=no')
    summary = {'source': SOURCE, 'deployment': MERGE, 'release': RELEASE,
        'shared_verifier_exit': verified.returncode, 'shared_tracked_clean': True,
        'previous_outputs_preserved': str(backup), 'lease_created': False, 'published': False}
    (stage / 'materialization-result.json').write_text(json.dumps(summary, indent=2) + '\n')
    print('ARAMEX_2061_MATERIALIZATION=' + json.dumps(summary, sort_keys=True), flush=True)

if __name__ == '__main__':
    main()
