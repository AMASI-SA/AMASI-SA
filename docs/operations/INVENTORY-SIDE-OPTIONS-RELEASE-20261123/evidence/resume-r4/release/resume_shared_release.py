"""Resume only the verified post-preservation phase; never import the first helper."""
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import time
from pathlib import Path, PurePosixPath

APP = Path('/app')
ROOT = Path('/tmp/mezan-inventory-side-options-r4-20261124')
STAGE = ROOT / 'shared-stage'
RESUME = ROOT / 'shared-stage-resume-1'
ORIGINAL = ROOT / 'stage_shared_release.py'
ORIGINAL_SHA256 = 'd3206d932cd34dfdced81c7ffec89c1fdf8bfcb6c92d7ca59e0f454756c35bae'
A = 'deeefceffd9a1e37968513ac4f3833314bb415fd'
B = 'a92283678a25494c7d1870cd1675b8f30847aa6a'
J = '28f6d4ca0fc2b7ea7f140432d8e32c768823b6a4'
TREE = '868b8595dfeba299bf1f2dfaf1fc32b82d357033'
RELEASE = 'rg5-ffe0d4437c779b1e455f084be407f8f2d24735f5bf8e994f77c5bf4cbeb1c55f'
ARTIFACT = 'df85c5736ec64571f44a5db506cb0daf34c1ac584ca3115e4e3ce86ebe80898c'
BRANCH = 'hotfix/prod-snap-meta-final'
GENERATED = ('frontend/node_modules', 'frontend/build', 'frontend/.release', 'backend/release_identity.json')
MONGO_ROOT = '.worktrees/p01-acceptance-state-20260919/mongo'
VOLATILE_FILES = frozenset({MONGO_ROOT + '.log', '.emergent/stripped_env_names'})
MONGO_PID = 311
MONGO_START_TICKS = 9793546
ENV = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', GIT_TERMINAL_PROMPT='0',
           REACT_APP_BACKEND_URL='https://mezansalla.com')


class ResumeRefused(RuntimeError):
    pass


def require(condition, code):
    if not condition:
        raise ResumeRefused(code)


def under(relative, roots):
    return any(relative == root or relative.startswith(root + '/') for root in roots)


def volatile(relative):
    return relative in VOLATILE_FILES or relative.startswith(MONGO_ROOT + '/')


def metadata(info):
    return {'mode': stat.S_IMODE(info.st_mode), 'type': stat.S_IFMT(info.st_mode),
            'inode': info.st_ino, 'device': info.st_dev, 'uid': info.st_uid, 'gid': info.st_gid}


def real_directory(path):
    info = path.lstat()
    require(stat.S_ISDIR(info.st_mode) and not stat.S_ISLNK(info.st_mode), 'directory-type-refused')
    require(path.absolute() == path.resolve(strict=True), 'directory-parent-indirection-refused')
    return metadata(info)


def regular_bytes(path):
    info = path.lstat()
    require(stat.S_ISREG(info.st_mode) and not stat.S_ISLNK(info.st_mode), 'regular-file-required')
    require(not stat.S_IMODE(info.st_mode) & 0o7022, 'unsafe-control-file-mode')
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, 'rb') as source:
        require(metadata(os.fstat(source.fileno())) == metadata(info), 'control-file-raced')
        data = source.read()
        after = os.fstat(source.fileno())
    require(metadata(path.lstat()) == metadata(info), 'control-file-replaced')
    require((info.st_size, info.st_mtime_ns) == (after.st_size, after.st_mtime_ns), 'control-file-changed')
    return data


def output(args, timeout=180):
    completed = subprocess.run(args, cwd=APP, env=ENV, text=True,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
    require(completed.returncode == 0, 'read-only-command-failed')
    return completed.stdout.strip()


def git(*args):
    return output(['git', *args])


def inactive():
    value = json.loads(output(['python', '-B', 'scripts/production_release_guard.py', 'status'], 15))
    require(value.get('active') is False, 'active-release-guard')
    require(not value.get('incomplete_lease_directory'), 'incomplete-release-lease')
    return value


def source_boundary(deployment):
    guard = inactive()
    require(git('branch', '--show-current') == BRANCH, 'wrong-production-branch')
    require(git('rev-parse', 'HEAD') == deployment, 'deployment-head-changed')
    require(git('rev-parse', 'HEAD^{tree}') == TREE, 'deployment-tree-changed')
    require(git('show', '-s', '--format=%P', deployment) == J + ' ' + B, 'deployment-parents-changed')
    require(git('diff', '--name-only', A, deployment) == 'release/release-intent-v5.json', 'source-intent-relation-changed')
    require(not git('status', '--porcelain', '--untracked-files=no'), 'tracked-worktree-dirty')
    require(git('rev-parse', 'origin/' + BRANCH) == deployment, 'local-upstream-changed')
    remote = git('ls-remote', '--exit-code', 'origin', 'refs/heads/' + BRANCH).split()
    require(remote == [deployment, 'refs/heads/' + BRANCH], 'remote-production-changed')
    return guard


def mongo_pin():
    proc = Path('/proc') / str(MONGO_PID)

    def identity():
        raw = (proc / 'stat').read_text()
        left, right = raw.index('('), raw.rindex(')')
        require(raw[left + 1:right] == 'mongod', 'mongo-process-name-changed')
        ticks = int(raw[right + 2:].split()[19])
        require(ticks == MONGO_START_TICKS, 'mongo-process-start-changed')
        return ticks

    initial = identity()
    require(Path(os.readlink(proc / 'exe')).name == 'mongod', 'mongo-process-executable-changed')
    argv = (proc / 'cmdline').read_bytes().split(b'\0')
    dbpaths = []
    for index, arg in enumerate(argv):
        if arg == b'--dbpath':
            require(index + 1 < len(argv), 'mongo-dbpath-missing')
            dbpaths.append(os.fsdecode(argv[index + 1]))
        elif arg.startswith(b'--dbpath='):
            dbpaths.append(os.fsdecode(arg.split(b'=', 1)[1]))
    expected = str(APP / MONGO_ROOT)
    require(dbpaths == [expected], 'mongo-dbpath-changed')
    real_directory(APP / MONGO_ROOT)
    require(identity() == initial, 'mongo-process-raced')
    return {'pid': MONGO_PID, 'start_ticks': initial, 'comm': 'mongod', 'dbpath_matches_expected': True}


def validate_before(before):
    require(isinstance(before, dict) and bool(before), 'original-inventory-invalid')
    for relative, record in before.items():
        require(isinstance(relative, str) and relative and '\0' not in relative, 'inventory-path-invalid')
        path = PurePosixPath(relative)
        require(not path.is_absolute() and '..' not in path.parts and '.' not in path.parts,
                'inventory-path-invalid')
        require(path.as_posix() == relative.rstrip('/') and not under(relative, GENERATED), 'inventory-path-not-canonical')
        require(isinstance(record, dict) and type(record.get('mode')) is int and type(record.get('type')) is int,
                'inventory-record-invalid')
        kind = record['type']
        keys = {'mode', 'type'} | ({'size', 'sha256'} if kind == stat.S_IFREG else
                                 {'link'} if kind == stat.S_IFLNK else {'inode', 'device'})
        require(set(record) == keys, 'inventory-record-fields-invalid')
        if kind == stat.S_IFREG:
            require(type(record['size']) is int and record['size'] >= 0 and
                    isinstance(record['sha256'], str) and re.fullmatch(r'[0-9a-f]{64}', record['sha256']),
                    'inventory-regular-record-invalid')
        if volatile(relative):
            require(kind == stat.S_IFREG, 'volatile-original-type-refused')
    require(VOLATILE_FILES <= set(before), 'volatile-platform-or-log-file-missing')
    require(any(p.startswith(MONGO_ROOT + '/') for p in before), 'original-mongo-inventory-missing')


def inventory(before):
    raw = subprocess.check_output(['git', 'ls-files', '--others', '-z'], cwd=APP, env=ENV)
    paths = [os.fsdecode(item) for item in raw.split(b'\0') if item]
    paths = [p for p in paths if not under(p, GENERATED)]
    require(len(paths) == len(set(paths)), 'duplicate-inventory-path')
    require(set(paths) == set(before), 'inventory-membership-changed')
    static_metadata, volatile_metadata, parents = {}, {}, {}
    for relative in sorted(paths):
        path = APP / relative
        info = path.lstat()
        old = before[relative]
        actual = {'mode': stat.S_IMODE(info.st_mode), 'type': stat.S_IFMT(info.st_mode)}
        if volatile(relative):
            require(stat.S_ISREG(info.st_mode), 'volatile-file-type-changed')
            require(actual == {k: old[k] for k in ('mode', 'type')}, 'volatile-file-mode-changed')
            volatile_metadata[relative] = {**metadata(info), 'size': info.st_size}
            parent = path.parent
            while parent != APP:
                key = str(parent.relative_to(APP))
                if key not in parents:
                    parents[key] = real_directory(parent)
                parent = parent.parent
            continue
        if stat.S_ISREG(info.st_mode):
            digest = hashlib.sha256()
            descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(descriptor, 'rb') as source:
                require(metadata(os.fstat(source.fileno())) == metadata(info), 'static-file-raced')
                for chunk in iter(lambda: source.read(1024 * 1024), b''):
                    digest.update(chunk)
                after = os.fstat(source.fileno())
            require(metadata(path.lstat()) == metadata(info), 'static-file-replaced')
            require((info.st_size, info.st_mtime_ns) == (after.st_size, after.st_mtime_ns), 'static-file-changed-during-read')
            actual.update(size=info.st_size, sha256=digest.hexdigest())
        elif stat.S_ISLNK(info.st_mode):
            actual['link'] = os.readlink(path)
        else:
            actual.update(inode=info.st_ino, device=info.st_dev)
        require(actual == old, 'static-original-inventory-mismatch')
        static_metadata[relative] = metadata(info)
    return static_metadata, volatile_metadata, parents


def backup_state(require_absent):
    saved = {}
    for relative in GENERATED:
        current = APP / relative
        if require_absent:
            require(not os.path.lexists(current), 'generated-output-already-present')
        require(not git('ls-files', '--', relative), 'generated-path-is-tracked')
        backup = STAGE / 'preserved-generated' / relative
        real_directory(backup.parent)
        info = backup.lstat()
        expected_type = stat.S_IFREG if relative.endswith('.json') else stat.S_IFDIR
        require(stat.S_IFMT(info.st_mode) == expected_type, 'generated-backup-type-refused')
        saved[relative] = metadata(info)
    bytecode = STAGE / 'preserved-bytecode'
    require(not os.path.lexists(bytecode) or (not bytecode.is_symlink() and bytecode.is_dir() and
                                           not any(bytecode.iterdir())), 'unexpected-prior-bytecode-preservation')
    return saved


def write_private_json(path, value):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, 'w') as target:
        json.dump(value, target, sort_keys=True, indent=2)
        target.write('\n')


def run_original(args, log, cwd=APP):
    log.write('COMMAND ' + json.dumps(args) + '\n')
    log.flush()
    completed = subprocess.run(args, cwd=cwd, env=ENV, stdout=log, stderr=subprocess.STDOUT, timeout=1800)
    require(completed.returncode == 0, 'original-command-failed-see-resume-log')


def main():
    require(not sys.flags.optimize, 'optimized-python-refused')
    require(len(sys.argv) == 2 and re.fullmatch(r'[0-9a-f]{40}', sys.argv[1]), 'full-deployment-sha-required')
    deployment = sys.argv[1]
    started = time.monotonic()
    result = {'source_git_sha': A, 'intent_git_sha': B, 'deployment_git_sha': deployment,
              'original_helper_sha256': ORIGINAL_SHA256, 'exit_code': 1}
    phase = 'failed-attempt-binding'
    created = False
    try:
        real_directory(APP)
        real_directory(ROOT)
        real_directory(STAGE)
        require(hashlib.sha256(regular_bytes(ORIGINAL)).hexdigest() == ORIGINAL_SHA256, 'original-helper-digest-mismatch')
        failed_bytes = regular_bytes(ROOT / 'shared-stage-result.json')
        failed = json.loads(failed_bytes)
        require(failed.get('exit_code') == 1 and failed.get('phase') == 'preserve-generated-and-derived-cache'
                and failed.get('error_type') == 'AssertionError'
                and failed.get('error') == 'unrelated files changed during preservation'
                and failed.get('source_git_sha') == A and failed.get('intent_git_sha') == B
                and failed.get('deployment_git_sha') == deployment, 'unexpected-failed-attempt-state')
        result['failed_result_sha256'] = hashlib.sha256(failed_bytes).hexdigest()
        before = json.loads(regular_bytes(STAGE / 'private-untracked-before.json'))
        validate_before(before)
        require(not os.path.lexists(RESUME), 'resume-attempt-already-exists')
        RESUME.mkdir(mode=0o700)
        created = True
        phase = 'post-preservation-boundary'
        source_boundary(deployment)
        process = mongo_pin()
        saved = backup_state(require_absent=True)
        for scope in (MONGO_ROOT, *sorted(VOLATILE_FILES)):
            require(not git('ls-files', '--', scope), 'volatile-scope-is-tracked')
        phase = 'original-inventory-reconciliation'
        static_before, volatile_before, parents_before = inventory(before)
        write_private_json(RESUME / 'private-volatile-metadata-before.json', volatile_before)
        write_private_json(RESUME / 'private-static-metadata-before.json', static_before)
        require(mongo_pin() == process, 'mongo-process-changed-before-build')
        source_boundary(deployment)
        require(backup_state(require_absent=True) == saved, 'generated-backup-changed-before-build')
        log_fd = os.open(RESUME / 'resume.log', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(log_fd, 'w') as log:
            phase = 'original-shared-adapter'
            run_original(['yarn', 'build'], log, APP / 'frontend')
            phase = 'original-retained-build-verification'
            run_original(['python', '-B', 'scripts/frontend_release_toolchain.py', 'exec', '--', 'python', '-B',
                          'scripts/verify_frontend_build.py', '--expected-git-sha', A, '--reviewed-intent-v5'], log)
        phase = 'post-build-preservation-and-release-verification'
        intent = json.loads(regular_bytes(APP / 'release/release-intent-v5.json'))
        identity = json.loads(regular_bytes(APP / 'backend/release_identity.json'))
        require(identity == intent['runtime_identity'] and identity['release_id'] == RELEASE, 'runtime-identity-mismatch')
        require(intent['frontend_build']['artifact_tree_sha256'] == ARTIFACT, 'artifact-identity-mismatch')
        static_after, volatile_after, parents_after = inventory(before)
        require(static_after == static_before, 'static-inode-or-ownership-changed')
        require(parents_after == parents_before, 'volatile-parent-metadata-changed')
        for relative, prior in volatile_before.items():
            require({k: v for k, v in volatile_after[relative].items() if k not in {'size', 'inode'}} ==
                    {k: v for k, v in prior.items() if k not in {'size', 'inode'}}, 'volatile-ownership-or-device-changed')
        require(mongo_pin() == process, 'mongo-process-changed-after-build')
        require(backup_state(require_absent=False) == saved, 'generated-backup-changed-after-build')
        final_guard = source_boundary(deployment)
        write_private_json(RESUME / 'private-volatile-metadata-after.json', volatile_after)
        result.update(exit_code=0, release_id=RELEASE, artifact_tree_sha256=ARTIFACT,
                      runtime_identity_matches=True, original_static_hashes_preserved=True,
                      inventory_path_count=len(before), static_path_count=len(static_after),
                      volatile_regular_file_count=len(volatile_after),
                      volatile_content_hashes_exempted=len(volatile_after),
                      volatile_size_change_count=sum(volatile_after[p]['size'] != before[p]['size'] for p in volatile_after),
                      volatile_inode_change_count=sum(volatile_after[p]['inode'] != volatile_before[p]['inode'] for p in volatile_after),
                      global_path_membership_unchanged=True, type_and_mode_unchanged=True,
                      generated_backups_preserved=True, tracked_clean=True, mongo_process=process, guard=final_guard)
    except Exception as error:
        result.update(phase=phase, error_type=type(error).__name__,
                      error_code=str(error) if isinstance(error, ResumeRefused) else 'unexpected-error-details-private')
    finally:
        result['elapsed_seconds'] = round(time.monotonic() - started, 3)
        if created:
            write_private_json(RESUME / 'result.json', result)
        print('INVENTORY_R4_SHARED_STAGE_RESUME_RESULT', json.dumps(result), flush=True)
    return result['exit_code']


if __name__ == '__main__':
    raise SystemExit(main())
