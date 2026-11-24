#!/usr/bin/env python3
"""Read-only /app pack preparation. All new artifacts remain in owned /tmp.

Never installs a pack, creates .keep, prunes, fetches, repacks, or changes refs.
Requires independent review before execution and before any later apply step.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import sys
import tempfile
import time

sys.dont_write_bytecode = True
ROOT = Path('/app')
GIT_DIR = ROOT / '.git'
BRANCH = 'hotfix/prod-snap-meta-final'
J = '20fc28f3432a78996f1d70f3356a38bd78b0309e'
D = 'cb98e5660380d362e78f937d60abc7139cd1c462'
TREE = 'abcdea0a41cb694cdd229492a96a7b68e25d8a3a'
EXPECTED_LOOSE = 5760
EXPECTED_FOREIGN = 432
COMMON_PATH = GIT_DIR / 'mezan-pr2113-recovery-operator-jyn9frvb/pr2113_recover_d_rehearsal.py'
COMMON_SHA256 = '4fe5070533e8f6308fc88516d1af80f8fa12f5523e7ecf8a7777317a4182b798'
OID = re.compile(r'[0-9a-f]{40}\Z')
LOOSE = re.compile(r'[0-9a-f]{2}/[0-9a-f]{38}\Z')
STAGE = None
COMMON = None
SERIAL = 0
STEP = 'startup'
ENV = dict(os.environ)
ENV.update(PYTHONDONTWRITEBYTECODE='1', GIT_TERMINAL_PROMPT='0',
           GIT_OPTIONAL_LOCKS='0', GIT_NO_REPLACE_OBJECTS='1')
RECEIPT = {'schema_version': 1, 'task': 'PR2113_prepare_loose_pack',
           'status': 'RUNNING', 'deployment_sha': D, 'source_base_sha': J,
           'common_path': str(COMMON_PATH), 'common_sha256': COMMON_SHA256,
           'expected_loose_count': EXPECTED_LOOSE,
           'expected_foreign_count': EXPECTED_FOREIGN,
           'app_writes': False, 'pack_installed': False, 'objects_deleted': False}


class Blocked(RuntimeError):
    pass


def require(condition, code):
    if not condition:
        raise Blocked(code)


def sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def save(name, value):
    require(STAGE is not None and '/' not in name, 'INVALID_EVIDENCE_PATH')
    payload = value if isinstance(value, bytes) else (
        json.dumps(value, sort_keys=True, indent=2, ensure_ascii=True) + '\n').encode()
    target = STAGE / name
    temporary = STAGE / (name + '.tmp')
    with temporary.open('xb') as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, target)
    descriptor = os.open(STAGE, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def command(label, argv, *, data=None, timeout=180):
    global SERIAL, STEP
    SERIAL += 1
    STEP = label
    stderr_path = STAGE / ('%03d-%s.stderr' % (SERIAL, label))
    with stderr_path.open('xb') as error_log:
        process = subprocess.Popen(argv, cwd=ROOT, env=ENV,
                                   stdin=subprocess.PIPE if data is not None else subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=error_log,
                                   start_new_session=True)
        try:
            output, _ = process.communicate(input=data, timeout=timeout)
        except BaseException:
            if COMMON is not None:
                COMMON.terminate_group(process)
            else:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()
            raise
        error_log.flush()
        os.fsync(error_log.fileno())
    save('%03d-%s.stdout' % (SERIAL, label), output)
    require(process.returncode == 0, label + '_FAILED')
    return output


def git(*args, data=None, timeout=180):
    return command('git-' + args[0], ['git', '-C', str(ROOT),
                   '-c', 'gc.auto=0', '-c', 'maintenance.auto=false',
                   '-c', 'core.fsmonitor=false', '-c', 'core.untrackedCache=false',
                   *args], data=data, timeout=timeout)


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    require(spec is not None and spec.loader is not None, 'MODULE_LOAD_REFUSED')
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def logical_inventory(raw):
    result = {}
    for row in raw.decode('ascii').splitlines():
        fields = row.split()
        require(len(fields) == 3 and OID.fullmatch(fields[0]) and
                fields[1] in {'blob', 'tree', 'commit', 'tag'} and
                fields[2].isdigit(), 'INVALID_LOGICAL_OBJECT_ROW')
        require(fields[0] not in result, 'DUPLICATE_LOGICAL_OID')
        result[fields[0]] = {'type': fields[1], 'size': int(fields[2])}
    require(bool(result), 'EMPTY_LOGICAL_INVENTORY')
    return result


def tree_records(path, common):
    records = {}
    if not os.path.lexists(path):
        return {'.': {'type': 'missing'}}

    def visit(current):
        relative = current.relative_to(path).as_posix()
        record = common.file_fingerprint(current)
        records[relative] = record
        if record['type'] == 'directory':
            for child in sorted(current.iterdir()):
                visit(child)
    visit(path)
    return records


def loose_paths(objects):
    result = sorted(name for name in objects if LOOSE.fullmatch(name))
    require(all(objects[name]['type'] == 'file' for name in result),
            'NON_REGULAR_LOOSE_OBJECT')
    return result


def snapshot(common, git_read):
    """Reusable read-only snapshot; git_read(*args) must return bytes."""
    head = git_read('rev-parse', 'HEAD').decode().strip()
    branch = git_read('symbolic-ref', '--short', 'HEAD').decode().strip()
    tree = git_read('rev-parse', 'HEAD^{tree}').decode().strip()
    require(head == D and branch == BRANCH and tree == TREE, 'CHECKOUT_CHANGED')
    require(not git_read('status', '--porcelain=v1', '--untracked-files=no'),
            'TRACKED_WORKTREE_DIRTY')
    for relative in ('objects/info/alternates', 'objects/info/http-alternates'):
        require(not os.path.lexists(GIT_DIR / relative), 'ALTERNATE_OBJECT_STORE')
    require(git_read('rev-parse', '--show-object-format').strip() == b'sha1',
            'UNEXPECTED_OBJECT_FORMAT')
    common_dir = Path(os.fsdecode(git_read('rev-parse', '--git-common-dir').strip()))
    require((common_dir if common_dir.is_absolute() else ROOT / common_dir).resolve()
            == GIT_DIR, 'UNEXPECTED_COMMON_DIR')
    objects = tree_records(GIT_DIR / 'objects', common)
    require(objects['.']['type'] == 'directory', 'UNSAFE_OBJECTS_ROOT')
    for name in loose_paths(objects):
        require(objects[name[:2]]['type'] == 'directory', 'UNSAFE_LOOSE_PARENT')
    metadata = {}
    for entry in sorted(GIT_DIR.iterdir()):
        if entry.name == 'objects':
            continue
        if entry.name in {'refs', 'logs', 'worktrees', 'info', 'hooks'} or not entry.is_dir():
            for suffix, record in tree_records(entry, common).items():
                key = entry.name if suffix == '.' else entry.name + '/' + suffix
                metadata[key] = record
                require(not key.endswith('.lock') or
                        key == '.mezan-production-release.lock.operation.lock',
                        'OTHER_GIT_WRITER_LOCK_PRESENT')
    worktrees = os.fsdecode(git_read('worktree', 'list', '--porcelain', '-z'))
    linked_gitfiles = {}
    for token in worktrees.split('\0'):
        if token.startswith('worktree '):
            worktree = Path(token[len('worktree '):])
            require(worktree.is_absolute(), 'NON_ABSOLUTE_WORKTREE')
            if worktree != ROOT:
                linked_gitfiles[str(worktree / '.git')] = tree_records(worktree / '.git', common)
    raw_objects = git_read('cat-file', '--batch-all-objects',
                           '--batch-check=%(objectname) %(objecttype) %(objectsize)')
    foreign = common.untracked_snapshot()
    require(len(foreign['git_entries']) == EXPECTED_FOREIGN, 'FOREIGN_COUNT_CHANGED')
    return {'head': head, 'branch': branch, 'tree': tree,
            'logical_objects': logical_inventory(raw_objects), 'objects': objects,
            'metadata': metadata, 'linked_gitfiles': linked_gitfiles,
            'existing_packs': {name: value for name, value in objects.items()
                               if name.startswith('pack/')},
            'refs': os.fsdecode(git_read('for-each-ref',
                      '--format=%(refname) %(objectname) %(objecttype)')),
            'worktrees': worktrees, 'foreign': foreign}


def verify_pack_listing(raw, expected, pack_path):
    result = {}
    bases = []
    ok = False
    for row in raw.decode('ascii').splitlines():
        fields = row.split()
        if fields and OID.fullmatch(fields[0]):
            require(len(fields) in {5, 7} and fields[1] in {'blob', 'tree', 'commit', 'tag'}
                    and all(value.isdigit() for value in fields[2:5]), 'INVALID_PACK_ROW')
            require(fields[0] not in result, 'DUPLICATE_PACK_OBJECT')
            result[fields[0]] = fields[1]
            # For delta rows verify-pack reports the delta representation size,
            # not the reconstructed logical object's size. A new-pack-only
            # object database below independently reconstructs every object.
            if len(fields) == 5:
                require(fields[0] in expected and
                        int(fields[2]) == expected[fields[0]]['size'],
                        'NON_DELTA_LOGICAL_SIZE_MISMATCH')
            if len(fields) == 7:
                require(fields[5].isdigit() and OID.fullmatch(fields[6]), 'INVALID_DELTA_ROW')
                bases.append(fields[6])
        elif row == str(pack_path) + ': ok':
            ok = True
        else:
            require(re.fullmatch(r'non delta: \d+ objects?', row) or
                    re.fullmatch(r'chain length = \d+: \d+ objects?', row),
                    'UNEXPECTED_VERIFY_PACK_OUTPUT')
    require(ok and result == {oid: row['type'] for oid, row in expected.items()},
            'PACK_OBJECT_SET_OR_TYPE_MISMATCH')
    require(all(base in result for base in bases), 'EXTERNAL_DELTA_BASE')


def verify_isolated_logical_objects(pack_path, index_path, expected):
    """Reconstruct logical objects using only the newly prepared pack pair."""
    isolated = STAGE / 'isolated.git'
    require(not os.path.lexists(isolated), 'ISOLATED_DATABASE_ALREADY_EXISTS')
    command('isolated-init', ['git', 'init', '--bare', str(isolated)])
    objects = isolated / 'objects'
    require(not any(path.is_file() or path.is_symlink() for path in objects.rglob('*')),
            'ISOLATED_DATABASE_NOT_EMPTY')
    pack_directory = objects / 'pack'
    for source, suffix in ((pack_path, '.pack'), (index_path, '.idx')):
        destination = pack_directory / ('pack-' + pack_path.stem.removeprefix('loose-') + suffix)
        require(not os.path.lexists(destination), 'ISOLATED_PACK_COLLISION')
        os.link(source, destination)
    raw = command('isolated-logical-objects',
                  ['git', '--git-dir=' + str(isolated), '-c', 'gc.auto=0',
                   '-c', 'maintenance.auto=false', 'cat-file', '--batch-all-objects',
                   '--batch-check=%(objectname) %(objecttype) %(objectsize)'])
    actual = logical_inventory(raw)
    require(actual == expected, 'ISOLATED_LOGICAL_OBJECT_MISMATCH')
    save('isolated-logical-objects.json', actual)
    return {'object_count': len(actual), 'alternates': False,
            'only_new_pack_pair': True, 'logical_types_and_sizes_match': True,
            'inventory_sha256': sha256(STAGE / 'isolated-logical-objects.json')}


def create_pack_in_private_database(prefix, oids):
    """Keep pack/index temporary writes in /tmp; read /app objects as alternate."""
    packing = STAGE / 'packing.git'
    require(not os.path.lexists(packing), 'PACKING_DATABASE_ALREADY_EXISTS')
    command('packing-init', ['git', 'init', '--bare', '--template=', str(packing)])
    objects = packing / 'objects'
    require(objects.resolve(strict=True) == objects and objects.is_dir()
            and not any(path.is_file() or path.is_symlink() for path in objects.rglob('*')),
            'PACKING_DATABASE_NOT_EMPTY_OR_REAL')
    alternate = objects / 'info/alternates'
    alternate_bytes = (str(GIT_DIR / 'objects') + '\n').encode('ascii')
    descriptor = os.open(alternate, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, 'wb') as stream:
        stream.write(alternate_bytes)
        stream.flush()
        os.fsync(stream.fileno())
    COMMON.fsync_directory(alternate.parent)
    private_git = ['git', '--git-dir=' + str(packing), '-c', 'gc.auto=0',
                   '-c', 'maintenance.auto=false', '-c', 'pack.writeReverseIndex=false']
    selected = command('packing-object-directory', private_git + ['rev-parse', '--git-path', 'objects'])
    require(Path(os.fsdecode(selected.strip())).resolve(strict=True) == objects,
            'PACKING_OBJECT_DIRECTORY_NOT_PRIVATE')
    output = command('private-pack-objects', private_git + ['pack-objects',
                     '--threads=2', '--index-version=2', '--no-reuse-delta',
                     '--no-reuse-object', str(prefix)],
                     data=('\n'.join(oids) + '\n').encode(), timeout=1200)
    require(alternate.read_bytes() == alternate_bytes, 'PACKING_ALTERNATE_CHANGED')
    return output, {'git_directory': str(packing), 'primary_object_directory': str(objects),
                    'read_only_alternate': str(GIT_DIR / 'objects'),
                    'pack_and_index_temporary_files_confined_to_private_database': True}


def interrupt(signum, frame):
    raise Blocked('INTERRUPTED')


def main():
    global STAGE, COMMON, STEP
    os.umask(0o077)
    try:
        require(len(sys.argv) == 1, 'NO_ARGUMENTS_ALLOWED')
        require(Path.cwd() == ROOT and ROOT.resolve() == ROOT, 'RUN_FROM_REAL_APP')
        for key in ('GIT_DIR', 'GIT_WORK_TREE', 'GIT_COMMON_DIR', 'GIT_INDEX_FILE',
                    'GIT_OBJECT_DIRECTORY', 'GIT_ALTERNATE_OBJECT_DIRECTORIES',
                    'GIT_CONFIG_COUNT', 'GIT_CONFIG_PARAMETERS'):
            require(key not in os.environ, 'UNSUPPORTED_GIT_ENVIRONMENT')
        require(GIT_DIR.is_dir() and not GIT_DIR.is_symlink(), 'UNSAFE_GIT_DIRECTORY')
        require(COMMON_PATH.is_file() and not COMMON_PATH.is_symlink() and
                sha256(COMMON_PATH) == COMMON_SHA256, 'COMMON_HELPER_CHANGED')
        COMMON = load_module('pr2113_pack_common', COMMON_PATH)
        require(COMMON.STAGE is None and COMMON.ROOT == ROOT, 'COMMON_HELPER_STATE_INVALID')
        COMMON.ENV.update(ENV)
        STAGE = Path(tempfile.mkdtemp(prefix='mezan-pr2113-pack-', dir='/tmp'))
        STAGE.chmod(0o700)
        RECEIPT.update(stage=str(STAGE), started_at_utc=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
                       helper_sha256=sha256(Path(__file__)))
        save('operator-helper.py', Path(__file__).read_bytes())
        save('receipt.json', RECEIPT)
        print('STAGE_PATH=' + str(STAGE), flush=True)
        require(git('rev-parse', '--show-toplevel').strip() == b'/app' and
                git('rev-parse', '--absolute-git-dir').strip() == b'/app/.git', 'GIT_ROOT_CHANGED')
        guard_path = ROOT / 'scripts/production_release_guard.py'
        require(guard_path.read_bytes() == git('show', J + ':scripts/production_release_guard.py'),
                'ORIGINAL_GUARD_CHANGED')
        lock = GIT_DIR / '.mezan-production-release.lock.operation.lock'
        require(lock.is_file() and not lock.is_symlink() and
                stat.S_IMODE(lock.stat().st_mode) == 0o600, 'EXISTING_OPERATION_LOCK_REQUIRED')
        public_status = json.loads(command('original-status',
            [sys.executable, '-B', str(guard_path), 'status'], timeout=45))
        require(public_status.get('active') is False and
                not public_status.get('incomplete_lease_directory'), 'LEASE_NOT_INACTIVE')
        guard = load_module('pr2113_pack_guard', guard_path)
        require(guard.REPO_ROOT == ROOT and guard.GIT_DIR == GIT_DIR, 'GUARD_ROOT_CHANGED')
        for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGALRM):
            signal.signal(signum, interrupt)
        STEP = 'operation-lock'
        signal.alarm(30)
        with guard._release_operation_lock():
            signal.alarm(0)
            COMMON.assert_no_lease(guard)
            before = snapshot(COMMON, git)
            paths = loose_paths(before['objects'])
            require(len(paths) == EXPECTED_LOOSE, 'LOOSE_COUNT_CHANGED')
            oids = [path.replace('/', '') for path in paths]
            expected = {oid: before['logical_objects'][oid] for oid in oids}
            save('snapshot-before.json', before)
            save('loose-oids.txt', ('\n'.join(oids) + '\n').encode())
            save('logical-objects-before.txt', ''.join(
                '%s %s %s\n' % (oid, row['type'], row['size'])
                for oid, row in sorted(before['logical_objects'].items())).encode())
            storage = {}
            for relative in paths:
                info = (GIT_DIR / 'objects' / relative).lstat()
                require(stat.S_ISREG(info.st_mode), 'LOOSE_OBJECT_TYPE_CHANGED')
                storage[relative] = {'allocated_bytes': info.st_blocks * 512,
                                     'nlink': info.st_nlink}
            save('loose-storage.json', storage)
            prefix = STAGE / 'loose'
            output, packing_scope = create_pack_in_private_database(prefix, oids)
            pack_hash = output.decode('ascii').strip()
            require(OID.fullmatch(pack_hash), 'INVALID_PACK_HASH')
            pack_path = STAGE / ('loose-' + pack_hash + '.pack')
            index_path = STAGE / ('loose-' + pack_hash + '.idx')
            for path in (pack_path, index_path):
                require(path.is_file() and not path.is_symlink(), 'PACK_OUTPUT_NOT_REGULAR')
                with path.open('rb') as stream:
                    os.fsync(stream.fileno())
            verification = git('verify-pack', '-v', str(index_path), timeout=600)
            save('verify-pack.txt', verification)
            verify_pack_listing(verification, expected, pack_path)
            isolated_proof = verify_isolated_logical_objects(pack_path, index_path, expected)
            require(pack_path.read_bytes()[-20:].hex() == pack_hash and
                    index_path.read_bytes()[-40:-20].hex() == pack_hash,
                    'PACK_INDEX_CHECKSUM_BINDING_MISMATCH')
            COMMON.assert_no_lease(guard)
            after = snapshot(COMMON, git)
            save('snapshot-after.json', after)
            require(before == after, 'APP_SNAPSHOT_CHANGED')
            COMMON.assert_no_lease(guard)
            reclaimable = sum(row['allocated_bytes'] for row in storage.values() if row['nlink'] == 1)
            block = os.statvfs(GIT_DIR).f_frsize
            new_bytes = sum(((path.stat().st_size + block - 1) // block) * block
                            for path in (pack_path, index_path)) + block
            RECEIPT.update(status='PASS', object_count=len(expected),
                           logical_object_count=len(before['logical_objects']),
                           private_packing_scope=packing_scope,
                           isolated_logical_proof=isolated_proof,
                           before_snapshot_sha256=sha256(STAGE / 'snapshot-before.json'),
                           after_snapshot_sha256=sha256(STAGE / 'snapshot-after.json'),
                           loose_allocated_bytes=sum(row['allocated_bytes'] for row in storage.values()),
                           estimated_gain_bytes=reclaimable - new_bytes,
                           estimate_scope='single-link loose allocation minus rounded new pair and one keep block; excludes directory overhead',
                           pack={'hash': pack_hash, 'pack_path': str(pack_path), 'index_path': str(index_path),
                                 'pack_sha256': sha256(pack_path), 'index_sha256': sha256(index_path),
                                 'pack_bytes': pack_path.stat().st_size, 'index_bytes': index_path.stat().st_size,
                                 'object_count': len(expected), 'verification': 'git verify-pack -v (full checksum/CRC validation)'})
            save('receipt.json', RECEIPT)
        print(json.dumps({'status': 'PASS', 'stage': str(STAGE), 'pack_hash': pack_hash,
                          'object_count': len(expected), 'pack_bytes': RECEIPT['pack']['pack_bytes'],
                          'index_bytes': RECEIPT['pack']['index_bytes'],
                          'estimated_gain_bytes': RECEIPT['estimated_gain_bytes']}, sort_keys=True), flush=True)
        return 0
    except BaseException as exc:
        signal.alarm(0)
        code = str(exc) if isinstance(exc, Blocked) else type(exc).__name__
        RECEIPT.update(status='BLOCKED', failure_step=STEP, failure_code=code)
        if STAGE is not None:
            try:
                save('receipt.json', RECEIPT)
            except BaseException:
                pass
        print('FINAL_STATUS=BLOCKED:' + code, flush=True)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
