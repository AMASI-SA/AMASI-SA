"""UNPINNED PR1924 original-build template. Does not run until final pins are reviewed."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
import time

ROOT = Path('/app')
SOURCE = 'c484b549e8bca017627ce80749c7895198f04bf8'
BASE = '94f6dfb8c1f46bcb7d2dcba6ca0feeea628dc894'
INTENT_COMMIT = 'UNPINNED_B3_COMMIT'
DEPLOYMENT = 'UNPINNED_M3_COMMIT'
TREE = 'UNPINNED_M3_TREE'
RELEASE = 'UNPINNED_RELEASE_ID'
INTENT_SHA256 = 'UNPINNED_INTENT_FILE_SHA256'
IDENTITY_SHA256 = 'UNPINNED_RUNTIME_IDENTITY_FILE_SHA256'
PINS_REVIEWED = False
BRANCH = 'hotfix/prod-snap-meta-final'
ORIGIN = 'https://mezansalla.com'
BOOKKEEPING = '.emergent/stripped_env_names'
PRESERVED_COUNT = 120
GUARD = ['python', '-B', 'scripts/production_release_guard.py']
ENV = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', REACT_APP_BACKEND_URL=ORIGIN)
sys.dont_write_bytecode = True


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def require_pins():
    require(PINS_REVIEWED is True, 'Unpinned template: final identity and runner bytes require independent review')
    for value in (SOURCE, BASE, INTENT_COMMIT, DEPLOYMENT, TREE):
        require(re.fullmatch(r'[0-9a-f]{40}', value) is not None, 'Unpinned commit or tree')
    for value in (INTENT_SHA256, IDENTITY_SHA256):
        require(re.fullmatch(r'[0-9a-f]{64}', value) is not None, 'Unpinned artifact checksum')
    require(re.fullmatch(r'rg5-[0-9a-f]{64}', RELEASE) is not None, 'Unpinned release ID')


def now():
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8'))


def write_json(path, value):
    with path.open('x', encoding='utf-8') as handle:
        handle.write(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


class Audit:
    def __init__(self, directory):
        self.directory = directory

    def run(self, name, argv, cwd=ROOT):
        record = {'argv': argv, 'cwd': str(cwd), 'started_at': now(), 'exit_code': None}
        write_json(self.directory / (name + '.started.json'), record)
        started = time.monotonic()
        stdout = self.directory / (name + '.stdout')
        stderr = self.directory / (name + '.stderr')
        try:
            with stdout.open('xb') as out, stderr.open('xb') as err:
                result = subprocess.run(argv, cwd=cwd, env=ENV, stdout=out, stderr=err)
            record['exit_code'] = result.returncode
        except BaseException as exc:
            record['exception'] = type(exc).__name__ + ': ' + str(exc)
            raise
        finally:
            record.update(finished_at=now(), seconds=time.monotonic() - started,
                          stdout_sha256=sha256(stdout) if stdout.exists() else None,
                          stderr_sha256=sha256(stderr) if stderr.exists() else None)
            write_json(self.directory / (name + '.json'), record)
        require(record['exit_code'] == 0, name + ' failed; inspect recorded actual exit and private streams')
        return stdout.read_bytes()

    def json(self, name, argv):
        return json.loads(self.run(name, argv))

    def git(self, name, *argv):
        return self.run(name, ['git', *argv]).decode().strip()


def prefix_sha256(handle, count):
    handle.seek(0)
    remaining = count
    digest = hashlib.sha256()
    while remaining:
        chunk = handle.read(min(remaining, 1024 * 1024))
        require(bool(chunk), 'Bookkeeping file was truncated while reading a witnessed prefix')
        digest.update(chunk)
        remaining -= len(chunk)
    return digest.hexdigest()


def append_snapshot(baseline=None, witnesses=()):
    """Hash only; preserve the fresh baseline and every witnessed complete prefix."""
    required = ([('original-build', baseline)] if baseline is not None else []) + list(witnesses)
    required = [(label, record['bytes'], record['sha256']) for label, record in required]
    path = ROOT / BOOKKEEPING
    witnessed = []
    original_metadata = baseline
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    with os.fdopen(descriptor, 'rb') as handle:
        for attempt in range(1, 4):
            first = os.fstat(handle.fileno())
            require(stat.S_ISREG(first.st_mode), 'Bookkeeping must remain a regular file')
            if original_metadata is None:
                original_metadata = {'mode': first.st_mode, 'inode': first.st_ino}
            require(first.st_mode == original_metadata['mode'] and first.st_ino == original_metadata['inode'],
                    'Bookkeeping file mode or inode changed')
            require(first.st_size >= max((count for _, count, _ in required), default=0),
                    'Bookkeeping file was truncated')
            full_hash = prefix_sha256(handle, first.st_size)
            proofs = []
            for label, count, expected in required:
                observed = prefix_sha256(handle, count)
                require(observed == expected, 'Bookkeeping witnessed prefix changed: ' + label)
                proofs.append({'label': label, 'bytes': count, 'sha256': observed})
            require(prefix_sha256(handle, first.st_size) == full_hash, 'Bookkeeping bytes changed during capture')
            last = os.fstat(handle.fileno())
            current_path = path.lstat()
            require((last.st_dev, last.st_ino, last.st_mode) == (first.st_dev, first.st_ino, first.st_mode)
                    and (current_path.st_dev, current_path.st_ino, current_path.st_mode)
                    == (first.st_dev, first.st_ino, first.st_mode), 'Bookkeeping path was replaced')
            require(last.st_size >= first.st_size and current_path.st_size >= last.st_size,
                    'Bookkeeping file shrank during capture')
            # mtime is a capture-stability signal, not a cross-phase equality gate
            # for this one append-only file. All other records remain exact.
            if (first.st_size, first.st_mtime_ns) == (last.st_size, last.st_mtime_ns) == (current_path.st_size, current_path.st_mtime_ns):
                return ({'bytes': first.st_size, 'sha256': full_hash, 'mode': first.st_mode,
                         'inode': first.st_ino, 'mtime_ns': first.st_mtime_ns},
                        {'file': BOOKKEEPING, 'capture_attempts': attempt, 'required_prefixes': proofs,
                         'intermediate_witnessed_prefixes': witnessed,
                         'attribution': 'Unknown; no writer or publication attribution asserted'})
            required.append(('capture-' + str(attempt), first.st_size, full_hash))
            witnessed.append({'bytes': first.st_size, 'sha256': full_hash})
    raise RuntimeError('Bookkeeping file did not yield a stable bounded snapshot')


def validate_preserved_record(current, baseline):
    require(isinstance(current, dict) and len(current) == PRESERVED_COUNT and set(current) == set(baseline),
            'Untracked paths added or removed')
    require(all(current[name] == baseline[name] for name in baseline if name != BOOKKEEPING),
            'An unapproved preserved file changed')
    record, original = current[BOOKKEEPING], baseline[BOOKKEEPING]
    require(stat.S_ISREG(record['mode']) and record['mode'] == original['mode']
            and record['inode'] == original['inode'], 'Bookkeeping kind, mode or inode differs')
    require(type(record.get('bytes')) is int and record['bytes'] >= original['bytes']
            and re.fullmatch(r'[0-9a-f]{64}', record.get('sha256', '')) is not None,
            'Invalid bookkeeping snapshot record')


def preserved_snapshot(audit, prefix, baseline=None, witnesses=()):
    raw = audit.run(prefix + '-untracked-list', ['git', 'ls-files', '--others', '--exclude-standard', '-z'])
    names = [os.fsdecode(item) for item in raw.split(b'\0') if item]
    require(len(names) == PRESERVED_COUNT and len(set(names)) == PRESERVED_COUNT and BOOKKEEPING in names,
            'Wrong untracked preservation path set or count')
    if baseline is not None:
        require(set(names) == set(baseline), 'Untracked paths added or removed')
    current = {}
    for name in names:
        if name == BOOKKEEPING:
            continue
        path = ROOT / name
        metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode):
            payload = {'link': os.readlink(path)}
        else:
            require(stat.S_ISREG(metadata.st_mode), 'Unexpected untracked file kind: ' + name)
            payload = {'bytes': metadata.st_size, 'sha256': sha256(path)}
        current[name] = dict(payload, mode=metadata.st_mode, inode=metadata.st_ino, mtime_ns=metadata.st_mtime_ns)
    record, proof = append_snapshot(baseline[BOOKKEEPING] if baseline is not None else None, witnesses)
    current[BOOKKEEPING] = record
    write_json(audit.directory / (prefix + '-untracked.json'), current)
    write_json(audit.directory / (prefix + '-bookkeeping-prefix-proof.json'), proof)
    if baseline is not None:
        validate_preserved_record(current, baseline)
    return current


def preservation_hashes(directory, prefixes):
    return {prefix + suffix: sha256(directory / (prefix + suffix))
            for prefix in prefixes for suffix in ('-untracked.json', '-bookkeeping-prefix-proof.json')}


def main():
    require_pins()  # Fail before /app imports, subprocesses, evidence creation or locks.
    require(len(sys.argv) == 2 and sys.argv[1] == DEPLOYMENT, 'Pass the exact reviewed M3 deployment SHA')
    os.umask(0o077)
    evidence = Path(tempfile.mkdtemp(prefix='mezan-pr1924-original-', dir='/tmp'))
    audit = Audit(evidence)
    write_json(evidence / 'started.json', {'started_at': now(), 'argv': [sys.executable, *sys.argv],
                                         'cwd': os.getcwd(), 'source': SOURCE, 'deployment': DEPLOYMENT})
    print('EVIDENCE=' + str(evidence), flush=True)
    sys.path.insert(0, str(ROOT))
    from scripts.production_release_guard import _release_operation_lock, LEASE_DIR, LEASE_PATH

    def locked_inactive(name):
        require(not LEASE_PATH.exists() and not LEASE_DIR.exists(), 'Active or incomplete release lease')
        write_json(evidence / (name + '.json'), {'active': False, 'under_operation_lock': True})

    try:
        status = audit.json('01-guard-before', GUARD + ['status'])
        require(status.get('active') is False and not status.get('incomplete_lease_directory'), 'Guard is active or incomplete')
        with _release_operation_lock():
            # No status CLI inside this non-reentrant official mutex.
            locked_inactive('01b-guard-under-lock')
            require(audit.git('before-branch', 'branch', '--show-current') == BRANCH, 'Wrong branch')
            require(audit.git('before-head', 'rev-parse', 'HEAD') == BASE, 'Original workspace is not at J3')
            require(not audit.git('before-tracked-status', 'status', '--porcelain', '--untracked-files=no'), 'Tracked worktree is dirty')
            before = preserved_snapshot(audit, 'before')
            audit.run('02-fetch', ['git', 'fetch', 'origin', BRANCH])
            require(audit.git('fetched-origin', 'rev-parse', 'origin/' + BRANCH) == DEPLOYMENT, 'Fetched branch is not M3')
            require(audit.git('fetched-tree', 'rev-parse', DEPLOYMENT + '^{tree}') == TREE, 'Wrong reviewed tree')
            require(audit.git('merge-parents', 'rev-list', '--parents', '-n', '1', DEPLOYMENT).split()[1:] == [BASE, INTENT_COMMIT], 'Wrong merge parents')
            require(audit.git('intent-parent', 'rev-list', '--parents', '-n', '1', INTENT_COMMIT).split()[1:] == [SOURCE], 'Wrong intent parent')
            require(audit.git('source-diff', 'diff', '--name-only', SOURCE, DEPLOYMENT) == 'release/release-intent-v5.json', 'Governed source changed after A3')
            locked_inactive('03-guard-before-fast-forward')
            audit.run('04-fast-forward', ['git', 'merge', '--ff-only', DEPLOYMENT])
            require(not audit.git('fast-forward-tracked-status', 'status', '--porcelain', '--untracked-files=no'), 'Tracked worktree is dirty')
            forwarded = preserved_snapshot(audit, 'after-fast-forward', before)
            locked_inactive('05-guard-before-build')
            audit.run('06-governed-adapter', ['yarn', 'build'], ROOT / 'frontend')
            audit.run('07-package-proof', ['python', '-B', 'scripts/emergent_deployment_adapter.py', 'verify-packages'])
            intent_path = ROOT / 'release/release-intent-v5.json'
            identity_path = ROOT / 'backend/release_identity.json'
            require(sha256(intent_path) == INTENT_SHA256 and sha256(identity_path) == IDENTITY_SHA256, 'Intent or runtime identity bytes differ from reviewed pins')
            intent, identity = read_json(intent_path), read_json(identity_path)
            require(identity == intent.get('runtime_identity'), 'Runtime identity differs from intent')
            for payload in (intent, identity):
                for field, expected in {'source_git_sha': SOURCE, 'source_base_git_sha': BASE, 'branch': BRANCH, 'protocol_version': 5}.items():
                    require(payload.get(field) == expected, 'Wrong local identity field: ' + field)
            require(identity.get('release_id') == RELEASE, 'Wrong runtime release ID')
            require(audit.git('after-head', 'rev-parse', 'HEAD') == DEPLOYMENT, 'Deployment HEAD changed')
            require(not audit.git('after-tracked-status', 'status', '--porcelain', '--untracked-files=no'), 'Tracked worktree is dirty')
            after = preserved_snapshot(audit, 'after', before, [('after-fast-forward', forwarded[BOOKKEEPING])])
            locked_inactive('08-guard-after-build')
        summary = {'source': SOURCE, 'base': BASE, 'intent_commit': INTENT_COMMIT, 'deployment': DEPLOYMENT,
                   'tree': TREE, 'release_id': RELEASE, 'intent_sha256': INTENT_SHA256, 'identity_sha256': IDENTITY_SHA256,
                   'adapter_exit': 0, 'package_proof_exit': 0, 'preserved_untracked_files': PRESERVED_COUNT,
                   'exact_original_records': PRESERVED_COUNT - 1, 'append_only_file': BOOKKEEPING,
                   'before_bookkeeping_bytes': before[BOOKKEEPING]['bytes'], 'after_bookkeeping_bytes': after[BOOKKEEPING]['bytes'],
                   'preservation_sha256': preservation_hashes(evidence, ('before', 'after-fast-forward', 'after')),
                   'guard_inactive': True, 'finished_at': now(), 'evidence': str(evidence)}
        write_json(evidence / 'result.json', summary)
        print(json.dumps({key: value for key, value in summary.items() if key != 'preservation_sha256'}, indent=2), flush=True)
    except BaseException as exc:
        write_json(evidence / 'failed.json', {'finished_at': now(), 'exception': type(exc).__name__ + ': ' + str(exc)})
        raise


if __name__ == '__main__':
    main()
