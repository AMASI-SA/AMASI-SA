import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path

APP = Path('/app')
ROOT = Path('/tmp/mezan-inventory-side-options-r4-20261124')
STAGE = ROOT / 'shared-stage'
A = 'deeefceffd9a1e37968513ac4f3833314bb415fd'
B = 'a92283678a25494c7d1870cd1675b8f30847aa6a'
J = '28f6d4ca0fc2b7ea7f140432d8e32c768823b6a4'
TREE = '868b8595dfeba299bf1f2dfaf1fc32b82d357033'
RELEASE = 'rg5-ffe0d4437c779b1e455f084be407f8f2d24735f5bf8e994f77c5bf4cbeb1c55f'
ARTIFACT = 'df85c5736ec64571f44a5db506cb0daf34c1ac584ca3115e4e3ce86ebe80898c'
GENERATED = ('frontend/node_modules', 'frontend/build', 'frontend/.release', 'backend/release_identity.json')
ENV = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', GIT_TERMINAL_PROMPT='0', REACT_APP_BACKEND_URL='https://mezansalla.com')
if sys.flags.optimize:
    raise SystemExit('Refuse optimized Python: staging safety assertions must remain enabled')
D = sys.argv[1]
assert re.fullmatch(r'[0-9a-f]{40}', D)

def output(args, cwd=APP, timeout=180):
    return subprocess.check_output(args, cwd=cwd, env=ENV, text=True, timeout=timeout).strip()

def git(*args):
    return output(['git', *args])

def inactive():
    value = json.loads(output(['python', '-B', 'scripts/production_release_guard.py', 'status'], timeout=15))
    assert value.get('active') is False, 'foreign or active release guard'
    return value

def is_under(path, roots):
    return any(path == root or path.startswith(root + '/') for root in roots)

def snapshot():
    raw = subprocess.check_output(['git', 'ls-files', '--others', '-z'], cwd=APP, env=ENV)
    records = {}
    for item in raw.split(b'\0'):
        if not item:
            continue
        relative = item.decode('utf-8', 'surrogateescape')
        if is_under(relative, GENERATED):
            continue
        p = APP / relative
        s = p.lstat()
        record = {'mode': stat.S_IMODE(s.st_mode), 'type': stat.S_IFMT(s.st_mode)}
        if stat.S_ISREG(s.st_mode):
            h = hashlib.sha256()
            with p.open('rb') as f:
                for chunk in iter(lambda: f.read(1024 * 1024), b''):
                    h.update(chunk)
            record['sha256'] = h.hexdigest()
            record['size'] = s.st_size
        elif stat.S_ISLNK(s.st_mode):
            record['link'] = os.readlink(p)
        else:
            record['inode'] = s.st_ino
            record['device'] = s.st_dev
        records[relative] = record
    return records

def preserved_move(relative, destination):
    source = APP / relative
    assert not git('ls-files', '--', relative), 'preservation target contains tracked files'
    assert not destination.exists(), 'preservation destination already exists'
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(source), str(destination))
    assert not source.exists() and destination.exists()

def run(args, cwd=APP, timeout=1800):
    print('COMMAND', json.dumps(args), flush=True)
    subprocess.run(args, cwd=cwd, env=ENV, timeout=timeout, check=True)

start = time.monotonic()
phase = 'boundary'
result = {'source_git_sha': A, 'deployment_git_sha': D, 'intent_git_sha': B}
try:
    guard = inactive()
    assert git('branch', '--show-current') == 'hotfix/prod-snap-meta-final'
    assert git('rev-parse', 'HEAD') == J
    assert not git('status', '--porcelain', '--untracked-files=no')
    assert not STAGE.exists(), 'stage directory already exists; inspect prior attempt'
    STAGE.mkdir()
    before = snapshot()
    (STAGE / 'private-untracked-before.json').write_text(json.dumps(before, sort_keys=True))
    for relative in GENERATED:
        p = APP / relative
        if p.exists() or p.is_symlink():
            assert not p.is_symlink(), 'unexpected generated-root symlink; preserve only after review'
            assert p.is_file() if relative.endswith('.json') else p.is_dir()
            assert not git('ls-files', '--', relative)
    phase = 'fetch-and-fast-forward'
    run(['git', 'fetch', 'origin', 'hotfix/prod-snap-meta-final'])
    assert git('rev-parse', 'origin/hotfix/prod-snap-meta-final') == D
    assert git('show', '-s', '--format=%P', D) == J + ' ' + B
    assert git('rev-parse', D + '^{tree}') == TREE
    assert git('diff', '--name-only', A, D) == 'release/release-intent-v5.json'
    inactive()
    assert git('rev-parse', 'HEAD') == J and not git('status', '--porcelain', '--untracked-files=no')
    run(['git', 'merge', '--ff-only', '--no-autostash', '--no-overwrite-ignore', D])
    phase = 'preserve-generated-and-derived-cache'
    inactive()
    preserved = []
    for relative in GENERATED:
        if (APP / relative).exists():
            preserved_move(relative, STAGE / 'preserved-generated' / relative)
            preserved.append(relative)
    cache_roots = []
    for scan_root in ('backend', 'frontend', 'scripts', '.github/workflows', '.github/actions'):
        if not (APP / scan_root).exists():
            continue
        for directory, dirs, files in os.walk(APP / scan_root, followlinks=False):
            dirs[:] = [name for name in dirs if not is_under(str((Path(directory) / name).relative_to(APP)), GENERATED)]
            if '__pycache__' not in dirs:
                continue
            p = Path(directory) / '__pycache__'
            assert not p.is_symlink(), 'unexpected cache symlink'
            assert all(x.is_file() and not x.is_symlink() and x.suffix == '.pyc' for x in p.iterdir()), 'cache contains non-derived files'
            relative = str(p.relative_to(APP))
            preserved_move(relative, STAGE / 'preserved-bytecode' / relative)
            cache_roots.append(relative)
            dirs.remove('__pycache__')
    for relative, record in before.items():
        if is_under(relative, cache_roots):
            destination = STAGE / 'preserved-bytecode' / relative
            assert destination.is_file() and not destination.is_symlink()
            assert stat.S_IMODE(destination.stat().st_mode) == record['mode']
            assert destination.stat().st_size == record['size']
            assert hashlib.sha256(destination.read_bytes()).hexdigest() == record['sha256']
    bytecode_backup = STAGE / 'preserved-bytecode'
    backup_files = {str(p.relative_to(bytecode_backup)) for p in bytecode_backup.rglob('*') if not p.is_dir()} if bytecode_backup.exists() else set()
    assert backup_files == {p for p in before if is_under(p, cache_roots)}
    expected = {p: v for p, v in before.items() if not is_under(p, cache_roots)}
    assert snapshot() == expected, 'unrelated files changed during preservation'
    phase = 'original-shared-adapter'
    run(['yarn', 'build'], APP / 'frontend')
    phase = 'original-retained-build-verification'
    run(['python', '-B', 'scripts/frontend_release_toolchain.py', 'exec', '--', 'python', '-B', 'scripts/verify_frontend_build.py', '--expected-git-sha', A, '--reviewed-intent-v5'])
    intent = json.loads((APP / 'release/release-intent-v5.json').read_text())
    identity = json.loads((APP / 'backend/release_identity.json').read_text())
    assert identity == intent['runtime_identity']
    assert identity['release_id'] == RELEASE
    assert intent['frontend_build']['artifact_tree_sha256'] == ARTIFACT
    assert git('rev-parse', 'HEAD') == D and git('rev-parse', 'HEAD^{tree}') == TREE
    assert not git('status', '--porcelain', '--untracked-files=no')
    after = snapshot()
    assert after == expected, 'unrelated ignored or untracked files changed during build'
    (STAGE / 'private-untracked-after.json').write_text(json.dumps(after, sort_keys=True))
    final_guard = inactive()
    result.update(exit_code=0, release_id=RELEASE, artifact_tree_sha256=ARTIFACT,
                  runtime_identity_matches=True, unrelated_files_preserved=True,
                  unrelated_file_count=len(after), preserved_generated=preserved,
                  preserved_bytecode_directory_count=len(cache_roots), tracked_clean=True,
                  guard=final_guard)
except Exception as error:
    result.update(exit_code=1, phase=phase, error_type=type(error).__name__, error=str(error))
finally:
    result['elapsed_seconds'] = round(time.monotonic() - start, 3)
    (ROOT / 'shared-stage-result.json').write_text(json.dumps(result, indent=2))
    print('INVENTORY_R4_SHARED_STAGE_RESULT', json.dumps(result), flush=True)
sys.exit(result['exit_code'])
