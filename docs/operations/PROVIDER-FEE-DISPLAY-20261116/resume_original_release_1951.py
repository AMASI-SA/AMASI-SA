#!/usr/bin/env python3
"""Resume the reviewed PR1951 rehearsal at M after its bytecode-only preflight failure.

This explicit cross-filesystem recovery preserves two reviewed generated files
outside /app before removing their original cache directory. It never fetches,
merges, creates a lease, publishes, or relaxes the official source validator.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import sys

ROOT = Path('/app')
MERGE = '2589f440106a7c5d463ee03eac3617d113319673'
OPERATOR_SHA256 = '40479a58a63004bd6ba9b3257f775a5110eace755c20a05656dbc7143e5a1140'
PRIOR = Path('/tmp/provider1951-original-20261116')
FAILED_AT = '2026-11-15T23:28:21.532261+00:00'
INVENTORY = Path('/tmp/provider1951-bytecode-inventory-20261116.json')
INVENTORY_SHA256 = '449a2329eb6ad4aa463e6b03e669ccd245aaa4c7a939ed110addeb4912bf528d'
PRIOR_PINS = {
    'official-status-before.stdout': '595219792dd54a38a8ace80a6a7fc33b928ea74f0dd1bdfac6a2b708dbe0dd1e',
    'status-before.json': '2a9b41be0bb9d037f82764a3883d0f1673ece64da8151ad66ea904b37934b962',
    'fast-forward.json': '2c807c7ed7801abad8818393d1e4cd47a14a55620699c38b6a6d344097355bfd',
    'fast-forward.stdout': 'b7ece7e7efbd02bc45041deb134860baf732645cb3041f597f9e0e76ac766928',
    'intent-preflight.json': '87ec907e5396429a8516f53b8bb29ca470ddbee619b39831b8fb63b83cc1c4f8',
    'intent-preflight.stderr': '628bfcf9d18a1e939ca55afd565edf69b1a284d1342a858436c8d5067b1c3105',
    'failed.json': '6494b83c42a8db546a55f76e17b3aeeef5c3c759a5bf05d9cbefb189009a914e',
    'untracked-before.json': 'd5b7cd62d608b95b56f1e31614c4da8e65fdd61809fb6328116ffc3567fe450c',
    'untracked-after-sync.json': 'd5b7cd62d608b95b56f1e31614c4da8e65fdd61809fb6328116ffc3567fe450c',
}
CACHE = 'backend/__pycache__'
CACHE_MODE = 493
CACHE_MTIME_NS = 1794782590950187103
CACHE_DEVICE = 66333
CACHE_FILES = (
    {'path': CACHE + '/frontend_build_identity.cpython-311.pyc', 'bytes': 39622,
     'mode': 420, 'mtime_ns': 1794782590938187103, 'regular': True,
     'symlink': False, 'tracked': False, 'ignored': True, 'suffix': '.pyc'},
    {'path': CACHE + '/release_protocol_v5.cpython-311.pyc', 'bytes': 80941,
     'mode': 420, 'mtime_ns': 1794782590950187103, 'regular': True,
     'symlink': False, 'tracked': False, 'ignored': True, 'suffix': '.pyc'},
)


def require(condition, reason):
    if not condition:
        raise RuntimeError(reason)


def regular_bytes(path):
    require(path.is_absolute() and path.resolve(strict=True) == path
            and stat.S_ISREG(path.lstat().st_mode), 'receipt/operator path is not a real regular file')
    return path.read_bytes()


def prior_receipts(op):
    require(PRIOR.resolve(strict=True) == PRIOR and stat.S_ISDIR(PRIOR.lstat().st_mode),
            'prior evidence directory differs')
    raw = {name: regular_bytes(PRIOR / name) for name in PRIOR_PINS}
    require(all(op.digest(raw[name]) == pin for name, pin in PRIOR_PINS.items()),
            'prior attempt receipt bytes changed')
    require(json.loads(raw['official-status-before.stdout']) == {'active': False},
            'prior official status was not inactive')
    status = json.loads(raw['status-before.json'])
    require(status == {'active': False, 'lease_directory_exists': False,
                      'observed_under_official_operation_mutex': True}, 'prior mutex status differs')
    ff, failed_preflight = (json.loads(raw[name]) for name in ('fast-forward.json', 'intent-preflight.json'))
    require(ff['exit_code'] == 0 and ff['stdout_sha256'] == op.digest(raw['fast-forward.stdout']),
            'prior fast-forward was not successful')
    require(failed_preflight['exit_code'] == 1
            and failed_preflight['stderr_sha256'] == op.digest(raw['intent-preflight.stderr'])
            and ff['finished_at'] <= failed_preflight['started_at'], 'prior failed stage differs')
    require(b'Backend runtime source contains prepackaged bytecode: __pycache__'
            in raw['intent-preflight.stderr'], 'prior failure is not the reviewed cache blocker')
    failure = json.loads(raw['failed.json'])
    require(failure == {'ok': False, 'error_type': 'RuntimeError',
            'reason': 'intent-preflight failed; inspect retained evidence before retry',
            'observed_at': FAILED_AT}, 'prior wrapper failure differs')
    require(not any(path.name.startswith(('adapter.', 'packages.')) or path.name == 'result.json'
                    for path in PRIOR.iterdir()), 'prior attempt reached an unexpected later stage')
    op.save('prior-attempt.json', {'directory': str(PRIOR), 'receipt_sha256': PRIOR_PINS,
                                 'failed_at': FAILED_AT, 'successful_fast_forward_verified': True})
    before, synced = (json.loads(raw[name]) for name in ('untracked-before.json', 'untracked-after-sync.json'))
    op.assert_preserved(before, synced)
    return synced


def quarantine(op, guard):
    raw = regular_bytes(INVENTORY)
    expected_inventory = [{'device': CACHE_DEVICE, 'path': CACHE, 'mode': CACHE_MODE,
                           'mtime_ns': CACHE_MTIME_NS, 'items': list(CACHE_FILES)}]
    require(op.digest(raw) == INVENTORY_SHA256
            and guard.exact_json_equal(json.loads(raw), expected_inventory), 'reviewed cache inventory differs')
    source = ROOT / CACHE
    require(source.resolve(strict=True) == source and stat.S_ISDIR(source.lstat().st_mode),
            'cache path contains a symlink or is not a directory')
    require(not op.git_bytes('ls-files', '-z', '--', CACHE), 'cache contains tracked paths')
    for path in [CACHE, *(item['path'] for item in CACHE_FILES)]:
        require(subprocess.run(['git', 'check-ignore', '-q', '--', path], cwd=ROOT,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0,
                'cache entry is not ignored')
    cache_fd = os.open(source, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    opened = []
    try:
        directory = os.fstat(cache_fd)
        require((directory.st_dev, stat.S_IMODE(directory.st_mode), directory.st_mtime_ns)
                == (CACHE_DEVICE, CACHE_MODE, CACHE_MTIME_NS), 'cache directory metadata changed')
        names = [Path(item['path']).name for item in CACHE_FILES]
        require(sorted(os.listdir(cache_fd)) == sorted(names), 'cache members changed')
        for item, name in zip(CACHE_FILES, names):
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=cache_fd)
            opened.append(fd)
            info = os.fstat(fd)
            require(stat.S_ISREG(info.st_mode) and info.st_dev == CACHE_DEVICE
                    and (info.st_size, stat.S_IMODE(info.st_mode), info.st_mtime_ns)
                    == (item['bytes'], item['mode'], item['mtime_ns']), 'cache file metadata changed')
            current = os.stat(name, dir_fd=cache_fd, follow_symlinks=False)
            require((current.st_dev, current.st_ino) == (info.st_dev, info.st_ino), 'cache file path changed')
        private = op.EVIDENCE / 'bytecode-quarantine'
        private.mkdir(mode=0o700)
        (private / 'backend').mkdir(mode=0o700)
        destination = private / CACHE
        destination.mkdir(mode=0o700)
        require(destination.stat().st_dev != CACHE_DEVICE, 'reviewed recovery requires its explicit cross-filesystem path')
        copies = []
        for item, fd in zip(CACHE_FILES, opened):
            info = os.fstat(fd)
            data = os.read(fd, item['bytes'] + 1)
            require(len(data) == item['bytes'], 'source bytecode size changed during copy')
            target = destination / Path(item['path']).name
            with target.open('xb') as output:
                output.write(data)
                output.flush()
                os.fchmod(output.fileno(), item['mode'])
                os.utime(output.fileno(), ns=(info.st_atime_ns, info.st_mtime_ns))
                os.fsync(output.fileno())
            require(op.digest(target.read_bytes()) == op.digest(data), 'quarantine copy digest differs')
            copied = target.lstat()
            require(stat.S_ISREG(copied.st_mode)
                    and (copied.st_size, stat.S_IMODE(copied.st_mode), copied.st_mtime_ns)
                    == (item['bytes'], item['mode'], item['mtime_ns']), 'quarantine copy metadata differs')
            copies.append({'source': item['path'], 'destination': str(target), 'bytes': len(data),
                           'sha256': op.digest(data), 'mode': item['mode'], 'mtime_ns': item['mtime_ns'],
                           'source_device': info.st_dev, 'source_inode': info.st_ino})
        os.chmod(destination, CACHE_MODE)
        os.utime(destination, ns=(directory.st_atime_ns, directory.st_mtime_ns), follow_symlinks=False)
        for path in (destination, private / 'backend', private, op.EVIDENCE):
            fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        # All copies are durable before the first original entry is removed.
        require(sorted(os.listdir(cache_fd)) == sorted(names), 'cache members changed before removal')
        for item, fd, copied in zip(CACHE_FILES, opened, copies):
            os.lseek(fd, 0, os.SEEK_SET)
            require(op.digest(os.read(fd, item['bytes'] + 1)) == copied['sha256'], 'source bytecode changed after copy')
            current = os.stat(Path(item['path']).name, dir_fd=cache_fd, follow_symlinks=False)
            require(stat.S_ISREG(current.st_mode)
                    and (current.st_dev, current.st_ino, current.st_size, stat.S_IMODE(current.st_mode), current.st_mtime_ns)
                    == (copied['source_device'], copied['source_inode'], item['bytes'], item['mode'], item['mtime_ns']),
                    'source bytecode metadata/path changed after copy')
        op.save('quarantine-prepared.json', {'method': 'explicit_cross_filesystem_copy_verify_then_remove',
                'inventory_sha256': INVENTORY_SHA256, 'directory': str(destination), 'copies': copies,
                'only_two_reviewed_generated_file_contents_read': True})
        for index, (item, fd, copied) in enumerate(zip(CACHE_FILES, opened, copies)):
            require(sorted(os.listdir(cache_fd)) == sorted(names[index:]), 'new cache member appeared during removal')
            name = Path(item['path']).name
            current = os.stat(name, dir_fd=cache_fd, follow_symlinks=False)
            require((current.st_dev, current.st_ino, current.st_size, stat.S_IMODE(current.st_mode), current.st_mtime_ns)
                    == (copied['source_device'], copied['source_inode'], item['bytes'], item['mode'], item['mtime_ns']),
                    'source entry changed immediately before removal')
            os.lseek(fd, 0, os.SEEK_SET)
            require(op.digest(os.read(fd, item['bytes'] + 1)) == copied['sha256'], 'source bytes changed immediately before removal')
            os.unlink(name, dir_fd=cache_fd)
            op.save('quarantine-removed-' + str(index + 1) + '.json', {'path': item['path'], 'copy_sha256': copied['sha256']})
        require(not os.listdir(cache_fd), 'cache directory gained a new entry')
        current = source.lstat()
        require(stat.S_ISDIR(current.st_mode) and (current.st_dev, current.st_ino)
                == (directory.st_dev, directory.st_ino), 'cache directory path changed')
        os.rmdir(source)
        backend_fd = os.open(source.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(backend_fd)
        finally:
            os.close(backend_fd)
        require(not os.path.lexists(source), 'cache directory was recreated')
        op.save('quarantine-completed.json', {'directory': str(destination), 'files': copies,
                                             'source_directory_removed': True})
        return destination, copies
    finally:
        for fd in opened:
            os.close(fd)
        os.close(cache_fd)


def resume_under_lock(op, guard):
    op.inactive_under_lock('status-before', guard)
    require(op.git('branch', '--show-current') == op.BRANCH and op.git('rev-parse', 'HEAD') == MERGE
            and op.git('rev-parse', 'HEAD^{tree}') == op.TREE, 'resume requires exact already-installed M')
    op.clean_tracked()
    previous = prior_receipts(op)
    before = op.snapshot()
    require(len(before) == 120 and before.get(op.APPEND_ONLY, {}).get('kind') == 'file', 'protected inventory differs')
    op.assert_preserved(previous, before)
    op.save('untracked-before.json', before)
    require(op.git('remote', 'get-url', 'origin') in {'https://github.com/AMASI-SA/AMASI-SA.git',
            'https://github.com/AMASI-SA/AMASI-SA', 'git@github.com:AMASI-SA/AMASI-SA.git'}, 'unexpected origin')
    remote = op.command('remote-head', ['git', 'ls-remote', '--exit-code', 'origin', 'refs/heads/' + op.BRANCH])
    require(remote.split() == [MERGE, 'refs/heads/' + op.BRANCH], 'live production ref differs from M')
    intent = op.validate_fetched_release()
    require(op.digest((ROOT / op.INTENT_PATH).read_bytes()) == op.INTENT_SHA256, 'workspace intent differs')
    op.cleanup_preflight(before)
    destination, copies = quarantine(op, guard)
    op.clean_tracked()
    op.assert_preserved(before, op.snapshot())
    op.inactive_under_lock('status-before-build', guard)
    environment = dict(os.environ, REACT_APP_BACKEND_URL='https://mezansalla.com', PYTHONDONTWRITEBYTECODE='1')
    removed = sorted(name for name in op.CHILD_CONTEXT_MARKERS if name in environment)
    for name in op.CHILD_CONTEXT_MARKERS:
        environment.pop(name, None)
    op.save('child-environment-removals.json', {'removed_names': removed})
    op.command('intent-preflight', [sys.executable, '-B', '-c',
        "import sys; sys.path.insert(0, 'scripts'); from emergent_deployment_adapter import load_release_intent; load_release_intent(); print('release intent verified')"], environment=environment)
    op.command('adapter', ['yarn', 'build'], ROOT / 'frontend', environment)
    proof = json.loads(op.command('packages', [sys.executable, '-B',
        'scripts/emergent_deployment_adapter.py', 'verify-packages'], environment=environment))
    require(op.digest(op.EXPECTED_PROOF.read_bytes()) == op.PROOF_SHA256
            and guard.exact_json_equal(proof, json.loads(op.EXPECTED_PROOF.read_text())), 'package proof differs from accepted B')
    after = op.snapshot()
    op.save('untracked-after.json', after)
    op.assert_preserved(previous, after)
    op.assert_preserved(before, after)
    op.clean_tracked()
    require(op.git('rev-parse', 'HEAD') == MERGE and op.git('rev-parse', 'HEAD^{tree}') == op.TREE
            and op.digest((ROOT / op.INTENT_PATH).read_bytes()) == op.INTENT_SHA256, 'source or intent moved during resume')
    require(not os.path.lexists(ROOT / CACHE), 'backend cache was recreated during rehearsal')
    for copied in copies:
        require(op.digest(regular_bytes(Path(copied['destination']))) == copied['sha256'], 'quarantine copy changed')
    op.inactive_under_lock('status-after', guard)
    result = {'ok': True, 'source': op.SOURCE_A, 'intent_commit': op.INTENT_B, 'merge': MERGE,
        'tree': op.TREE, 'release_id': intent['runtime_identity']['release_id'], 'prior_evidence': str(PRIOR),
        'quarantine_directory': str(destination), 'quarantined_generated_files': 2, 'quarantined_bytes': 120563,
        'quarantine_method': 'explicit_cross_filesystem_copy_verify_then_remove',
        'generated_cache_copies_sha256_verified': True, 'untracked_count': len(before),
        'unrelated_untracked_contents_read': False, 'untracked_metadata_preserved': True,
        'untracked_byte_identity_proven': False, 'bookkeeping_byte_prefix_proven': False,
        'package_proof_matches_accepted_B': True, 'active_lease': False, 'lease_created': False,
        'publication_performed': False, 'evidence_directory': str(op.EVIDENCE),
        'finished_at': datetime.now(timezone.utc).isoformat()}
    op.save('result.json', result)
    print(json.dumps(result, sort_keys=True), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reviewed-operator', type=Path,
                        default=Path(__file__).with_name('original_release_1951.py'))
    parser.add_argument('--merge', required=True)
    parser.add_argument('--package-proof', required=True, type=Path)
    parser.add_argument('--evidence-directory', required=True, type=Path)
    args = parser.parse_args()
    require(args.merge == MERGE, 'explicit merge differs')
    require(ROOT not in args.reviewed_operator.parents
            and hashlib.sha256(regular_bytes(args.reviewed_operator)).hexdigest() == OPERATOR_SHA256,
            'reviewed operator bytes or location differ')
    require(ROOT not in Path(__file__).resolve().parents, 'resume helper must remain outside /app')
    require(str(args.evidence_directory).startswith('/tmp/provider1951-original-resume-'), 'resume evidence path must be fresh and distinct')
    sys.dont_write_bytecode = True
    os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
    os.umask(0o077)
    spec = importlib.util.spec_from_file_location('reviewed_provider1951_operator', args.reviewed_operator)
    op = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(op)
    require(op.ROOT == ROOT and op.MERGE == MERGE, 'reviewed operator identity differs')
    op.EVIDENCE, op.EXPECTED_PROOF = args.evidence_directory, args.package_proof
    op.validate_bindings()
    require(ROOT.resolve() == ROOT and stat.S_ISDIR(ROOT.lstat().st_mode), '/app must be a real directory')
    for path, expected in op.CONTROL_PINS.items():
        require(op.digest(regular_bytes(ROOT / path)) == expected, 'operation control bytes differ')
    op.EVIDENCE.mkdir(mode=0o700, exist_ok=False)
    try:
        sys.path.insert(0, str(ROOT / 'scripts'))
        import production_release_guard as guard
        require(guard.REPO_ROOT == ROOT, 'guard repository root differs')
        status = json.loads(op.command('official-status-before', [sys.executable, '-B', 'scripts/production_release_guard.py', 'status']))
        require(status == {'active': False}, 'official guard is not inactive')
        with guard._release_operation_lock():
            resume_under_lock(op, guard)
    except Exception as error:
        failed = {'ok': False, 'error_type': type(error).__name__, 'reason': str(error),
                  'observed_at': datetime.now(timezone.utc).isoformat()}
        op.save('failed.json', failed)
        print(json.dumps(failed, sort_keys=True), flush=True)
        raise


if __name__ == '__main__':
    main()
