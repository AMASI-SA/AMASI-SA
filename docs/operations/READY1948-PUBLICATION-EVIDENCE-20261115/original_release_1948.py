#!/usr/bin/env python3
"""PR1948 bound review candidate: one handoff/rehearsal, never publication.

Require all exact-M gates, fresh inactive handoff, independent byte review,
and durable preservation of this version before running it. A failure retains the
workspace and evidence for diagnosis; this operator never resets or rolls back.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
from datetime import datetime, timezone

ROOT = Path('/app')
BRANCH = 'hotfix/prod-snap-meta-final'
BASE = '742b1032113730961597ea413665409d9c213f7b'
MERGE = 'c626af288d3823694b4f1d0efcc3670e4ee74d81'
TREE = '8b35611f0e66945a323b2365d7c6d456668d8b2e'
INTENT_B = 'dd1f29970506f9db2454cdf3b708818b399ee620'
SOURCE_A = 'd8a7ce0352bbe2f8858b349a32f2127c7e202343'
SOURCE_TREE = '40056c95d05b859f4bae9b6a5c5c129f8a38d142'
RELEASE_BASE_J = '742b1032113730961597ea413665409d9c213f7b'
INTENT_SHA256 = 'b767e8fe721a7031517745b1b64e7d68690f440f4f6b9f8d34935f8d81d1a680'
PROOF_SHA256 = '30899db8bab016f7a4fba67183f864a5da4ee71097b3829c2603a4c9419733b2'
EVIDENCE = Path('/tmp/mezan-ready1948-original-20261115T1945')
EXPECTED_PROOF = Path(__file__).with_name('expected-package-boundary.json')
INTENT_PATH = 'release/release-intent-v5.json'
APPEND_ONLY = '.emergent/stripped_env_names'
CHILD_CONTEXT_MARKERS = (
    'GITHUB_ACTIONS', 'GITHUB_SHA', 'GITHUB_WORKSPACE',
    'GITHUB_EVENT_NAME', 'GITHUB_EVENT_PATH',
    'MEZAN_RELEASE_BOOTSTRAP_INTENT', 'MEZAN_RELEASE_USE_REVIEWED_INTENT',
)
CONTROL_PINS = {
    'scripts/production_release_guard.py': '4e56f1f60f5f79931658b8f95a0aeb2269471025ca84f7e5b651cd284806349a',
    'backend/frontend_build_identity.py': '7896dfa2c9b4fea8c426b47c5b75e331380c0a4a3f947028aa4f5afa101f9d87',
    'backend/release_protocol_v5.py': '1961ac18a5363ec425ceec4f98f96f611d378b09e19380d4756bd6776987d8ef',
}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def validate_bindings():
    # Must run before creating evidence, importing /app code, or taking a lock.
    for name, value in {'BASE': BASE, 'MERGE': MERGE, 'TREE': TREE,
                        'INTENT_B': INTENT_B, 'SOURCE_A': SOURCE_A,
                        'SOURCE_TREE': SOURCE_TREE, 'RELEASE_BASE_J': RELEASE_BASE_J}.items():
        require(re.fullmatch(r'[0-9a-f]{40}', value), 'unbound/invalid ' + name)
    for name, value in {'INTENT_SHA256': INTENT_SHA256,
                        'PROOF_SHA256': PROOF_SHA256, **CONTROL_PINS}.items():
        require(re.fullmatch(r'[0-9a-f]{64}', value), 'unbound/invalid ' + name)
    require(re.fullmatch(r'/tmp/mezan-ready1948-original-[A-Za-z0-9_-]+', str(EVIDENCE)),
            'unbound/invalid unique EVIDENCE directory')
    require(BASE != MERGE, 'operator is not an automatic retry on an already updated workspace')
    require(not EVIDENCE.exists() and not EVIDENCE.is_symlink(), 'evidence path already exists')
    require(stat.S_ISREG(EXPECTED_PROOF.lstat().st_mode), 'accepted proof must be a regular file')
    require(digest(EXPECTED_PROOF.read_bytes()) == PROOF_SHA256, 'accepted proof bytes changed')


def save(name, value):
    (EVIDENCE / name).write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + '\n')


def command(name, args, cwd=ROOT, environment=None):
    started = datetime.now(timezone.utc).isoformat()
    save(name + '.started.json', {'args': args, 'cwd': str(cwd), 'started_at': started})
    with (EVIDENCE / (name + '.stdout')).open('xb') as out, (EVIDENCE / (name + '.stderr')).open('xb') as err:
        result = subprocess.run(args, cwd=cwd, env=environment, stdout=out, stderr=err)
    save(name + '.json', {'exit_code': result.returncode, 'started_at': started,
                         'finished_at': datetime.now(timezone.utc).isoformat(),
                         'stdout_sha256': digest((EVIDENCE / (name + '.stdout')).read_bytes()),
                         'stderr_sha256': digest((EVIDENCE / (name + '.stderr')).read_bytes())})
    require(result.returncode == 0, name + ' failed; inspect retained evidence before retry')
    return (EVIDENCE / (name + '.stdout')).read_text()


def git_bytes(*args):
    return subprocess.check_output(['git', *args], cwd=ROOT, stderr=subprocess.PIPE)


def git(*args):
    return git_bytes(*args).decode().strip()


def clean_tracked():
    for args in (['git', 'diff', '--quiet'], ['git', 'diff', '--cached', '--quiet']):
        require(subprocess.run(args, cwd=ROOT, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL).returncode == 0,
                'tracked source is not clean')


def inactive_under_lock(name, guard):
    # status() reacquires this same official mutex and would deadlock here.
    result = {'active': os.path.lexists(guard.LEASE_PATH),
              'lease_directory_exists': os.path.lexists(guard.LEASE_DIR),
              'observed_under_official_operation_mutex': True}
    save(name + '.json', result)
    require(not result['active'] and not result['lease_directory_exists'],
            'an active or incomplete release lease owns the workspace')


def snapshot():
    names = git_bytes('ls-files', '--others', '--exclude-standard', '-z').split(b'\0')
    records = {}
    for raw in filter(None, names):
        name = os.fsdecode(raw)
        path = ROOT / name
        info = path.lstat()
        metadata = {'mode': stat.S_IMODE(info.st_mode), 'inode': info.st_ino,
                    'mtime_ns': info.st_mtime_ns}
        if stat.S_ISLNK(info.st_mode):
            records[name] = {'kind': 'symlink', 'target': os.readlink(path), **metadata}
        elif stat.S_ISREG(info.st_mode):
            data = path.read_bytes()
            records[name] = {'kind': 'file', 'bytes': len(data), 'sha256': digest(data), **metadata}
        else:
            raise RuntimeError('unsupported untracked file type')
    return records


def assert_preserved(before, after):
    require(before.keys() == after.keys(), 'unrelated path set changed')
    for name, old in before.items():
        new = after[name]
        if name != APPEND_ONLY:
            require(old == new, 'unrelated path changed: ' + name)
            continue
        require(old['kind'] == new['kind'] == 'file' and new['bytes'] >= old['bytes'],
                'bookkeeping file was replaced or truncated')
        require(all(old[k] == new[k] for k in ('mode', 'inode')), 'bookkeeping identity changed')
        require(digest((ROOT / name).read_bytes()[:old['bytes']]) == old['sha256'],
                'bookkeeping prefix changed')


def cleanup_preflight(protected):
    generated = ('frontend/build', 'frontend/node_modules', 'frontend/.release',
                 'backend/release_identity.json')
    require(not any(name == root or name.startswith(root + '/')
                    for name in protected for root in generated),
            'protected untracked file lies inside an adapter cleanup root')
    for name in generated[:3]:
        path = ROOT / name
        if os.path.lexists(path):
            require(stat.S_ISDIR(path.lstat().st_mode), 'generated root is not a real directory')
    identity = ROOT / generated[3]
    if os.path.lexists(identity):
        require(stat.S_ISREG(identity.lstat().st_mode), 'generated identity is not a regular file')
    proof_root = ROOT / 'frontend/.release'
    if proof_root.exists():
        for path in proof_root.iterdir():
            require(path.name in {'reproducible-build.json', 'reproducible-build.json.tmp'}
                    and stat.S_ISREG(path.lstat().st_mode),
                    'unexpected entry in generated proof directory')


def validate_fetched_release():
    require(git('rev-parse', 'origin/' + BRANCH) == MERGE, 'production ref differs from fixed merge')
    require(git('show', '-s', '--format=%P', SOURCE_A).split() == [RELEASE_BASE_J],
            'source A does not have its reviewed sole parent J')
    require(git('rev-parse', SOURCE_A + '^{tree}') == SOURCE_TREE, 'source tree differs')
    require(git('show', '-s', '--format=%P', INTENT_B).split() == [SOURCE_A],
            'intent B does not have sole parent A')
    require(git('show', '-s', '--format=%P', MERGE).split() == [RELEASE_BASE_J, INTENT_B],
            'normal merge parent order differs')
    require(git('rev-parse', MERGE + '^{tree}') == TREE
            and git('rev-parse', INTENT_B + '^{tree}') == TREE, 'B/M tree equality failed')
    require(subprocess.run(['git', 'merge-base', '--is-ancestor', BASE, RELEASE_BASE_J],
                           cwd=ROOT).returncode == 0, 'starting HEAD is outside the reviewed base history')
    for target in (INTENT_B, MERGE):
        require(git('diff', '--name-only', SOURCE_A, target).splitlines() == [INTENT_PATH],
                'content after A changed outside the intent')
    require(git_bytes('show', SOURCE_A + ':' + INTENT_PATH)
            == git_bytes('show', RELEASE_BASE_J + ':' + INTENT_PATH), 'A changed its inherited intent')
    data = git_bytes('show', MERGE + ':' + INTENT_PATH)
    require(digest(data) == INTENT_SHA256, 'fetched merge intent differs from accepted bytes')
    intent = json.loads(data)
    require(intent.get('source_git_sha') == SOURCE_A
            and intent.get('source_base_git_sha') == RELEASE_BASE_J,
            'intent source/base identity differs')
    for path, expected in CONTROL_PINS.items():
        require(digest(git_bytes('show', MERGE + ':' + path)) == expected,
                'operation control code changes across handoff: ' + path)
    return intent


def run_under_lock(guard):
    inactive_under_lock('status-before', guard)
    require(git('branch', '--show-current') == BRANCH and git('rev-parse', 'HEAD') == BASE,
            'unexpected original branch or starting HEAD')
    clean_tracked()
    before = snapshot()
    save('untracked-before.json', before)
    origin = git('remote', 'get-url', 'origin')
    require(origin in {'https://github.com/AMASI-SA/AMASI-SA.git',
                       'https://github.com/AMASI-SA/AMASI-SA',
                       'git@github.com:AMASI-SA/AMASI-SA.git'}, 'unexpected origin URL')
    command('fetch', ['git', 'fetch', 'origin', BRANCH])
    intent = validate_fetched_release()
    command('fast-forward', ['git', 'merge', '--ff-only', MERGE])
    require(git('rev-parse', 'HEAD') == MERGE and git('rev-parse', 'HEAD^{tree}') == TREE,
            'original merge identity differs after fast-forward')
    clean_tracked()
    require(digest((ROOT / INTENT_PATH).read_bytes()) == INTENT_SHA256, 'workspace intent differs')
    after_sync = snapshot()
    save('untracked-after-sync.json', after_sync)
    assert_preserved(before, after_sync)
    inactive_under_lock('status-before-build', guard)
    cleanup_preflight(after_sync)
    environment = dict(os.environ, REACT_APP_BACKEND_URL='https://mezansalla.com', PYTHONDONTWRITEBYTECODE='1')
    # Force the existing non-GitHub adapter dispatcher in child processes only.
    # Never inherit bootstrap/review shortcuts or record environment values.
    removed_names = sorted(name for name in CHILD_CONTEXT_MARKERS if name in environment)
    for name in CHILD_CONTEXT_MARKERS:
        environment.pop(name, None)
    save('child-environment-removals.json', {'removed_names': removed_names})
    command('intent-preflight', [sys.executable, '-B', '-c',
            "import sys; sys.path.insert(0, 'scripts'); from emergent_deployment_adapter import load_release_intent; load_release_intent(); print('release intent verified')"],
            environment=environment)
    command('adapter', ['yarn', 'build'], ROOT / 'frontend', environment)
    proof = json.loads(command('packages', [sys.executable, '-B',
                       'scripts/emergent_deployment_adapter.py', 'verify-packages'], environment=environment))
    require(digest(EXPECTED_PROOF.read_bytes()) == PROOF_SHA256, 'accepted proof changed during rehearsal')
    require(guard.exact_json_equal(proof, json.loads(EXPECTED_PROOF.read_text())),
            'package proof differs from accepted B hosts')
    after = snapshot()
    save('untracked-after.json', after)
    assert_preserved(before, after)
    assert_preserved(after_sync, after)
    clean_tracked()
    require(git('rev-parse', 'HEAD') == MERGE and git('rev-parse', 'HEAD^{tree}') == TREE,
            'source moved during rehearsal')
    require(digest((ROOT / INTENT_PATH).read_bytes()) == INTENT_SHA256, 'intent moved during rehearsal')
    inactive_under_lock('status-after', guard)
    result = {'ok': True, 'base': BASE, 'source': SOURCE_A, 'intent_commit': INTENT_B,
              'merge': MERGE, 'tree': TREE, 'release_id': intent['runtime_identity']['release_id'],
              'untracked_count': len(before), 'untracked_preserved': True,
              'package_proof_matches_accepted_B': True,
              'append_only_before_bytes': before.get(APPEND_ONLY, {}).get('bytes'),
              'append_only_after_bytes': after.get(APPEND_ONLY, {}).get('bytes'),
              'active_lease': False, 'evidence_directory': str(EVIDENCE),
              'finished_at': datetime.now(timezone.utc).isoformat()}
    save('result.json', result)
    print(json.dumps(result, sort_keys=True), flush=True)


def main():
    validate_bindings()
    require(ROOT.resolve() == ROOT and stat.S_ISDIR(ROOT.lstat().st_mode), '/app must be a real directory')
    for path, expected in CONTROL_PINS.items():
        require(stat.S_ISREG((ROOT / path).lstat().st_mode)
                and digest((ROOT / path).read_bytes()) == expected,
                'unreviewed operation control code at starting HEAD: ' + path)
    EVIDENCE.mkdir(mode=0o700, exist_ok=False)
    print(json.dumps({'evidence_directory': str(EVIDENCE), 'waiting_for_official_operation_mutex': True}), flush=True)
    try:
        sys.dont_write_bytecode = True
        os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
        sys.path.insert(0, str(ROOT / 'scripts'))
        import production_release_guard as guard
        require(guard.REPO_ROOT == ROOT, 'guard repository root differs')
        with guard._release_operation_lock():
            run_under_lock(guard)
    except Exception as exc:
        failed = {'ok': False, 'error_type': type(exc).__name__, 'reason': str(exc),
                  'observed_at': datetime.now(timezone.utc).isoformat()}
        save('failed.json', failed)
        print(json.dumps(failed, sort_keys=True), flush=True)
        raise


if __name__ == '__main__':
    main()
