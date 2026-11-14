"""UNPINNED PR1922 single-attempt official guard phases. Never publishes or repairs a lease."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import time

ROOT = Path('/app')
SOURCE = '0f5bca7427fed7851008d0f7d64e23414746bdc1'
BASE = '29ec1e07eb7d1a23647cadff5bac26149accfa99'
INTENT_COMMIT = 'UNPINNED_B2_COMMIT'
DEPLOYMENT = 'UNPINNED_M2_COMMIT'
TREE = 'UNPINNED_M2_TREE'
RELEASE = 'UNPINNED_RELEASE_ID'
INTENT_SHA256 = 'UNPINNED_INTENT_FILE_SHA256'
IDENTITY_SHA256 = 'UNPINNED_RUNTIME_IDENTITY_FILE_SHA256'
PINS_REVIEWED = False
ORIGINAL = Path('UNPINNED_EXACT_ORIGINAL_BUILD_EVIDENCE_DIRECTORY')
ACTOR = 'UNPINNED_PR1922_ACTOR'
BASELINE_UI_VERSION = 'UNPINNED_PREPUBLICATION_UI_VERSION'
BRANCH = 'hotfix/prod-snap-meta-final'
ORIGIN = 'https://mezansalla.com'
BOOKKEEPING = '.emergent/stripped_env_names'
PRESERVED_COUNT = 120
GUARD = ['python', '-B', 'scripts/production_release_guard.py']
ENV = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', REACT_APP_BACKEND_URL=ORIGIN)
PHASE_ARGV = {'prepare': GUARD + ['prepare', '--actor', ACTOR],
              'prepublish': GUARD + ['prepublish'],
              'verify': GUARD + ['verify', '--url', ORIGIN]}
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


def timestamp(value):
    point = datetime.fromisoformat(value.replace('Z', '+00:00'))
    require(point.tzinfo is not None and point.utcoffset().total_seconds() == 0, 'Timestamp must be UTC')
    return point


def receipt(directory, name, argv, cwd=ROOT):
    metadata = read_json(directory / (name + '.json'))
    require(metadata.get('argv') == argv and metadata.get('cwd') == str(cwd), 'Prior receipt command mismatch')
    require(type(metadata.get('exit_code')) is int and metadata['exit_code'] == 0, 'Prior command did not succeed')
    require(timestamp(metadata['started_at']) <= timestamp(metadata['finished_at']), 'Invalid receipt times')
    for stream in ('stdout', 'stderr'):
        require(sha256(directory / (name + '.' + stream)) == metadata.get(stream + '_sha256'), 'Prior stream checksum mismatch')
    return (directory / (name + '.stdout')).read_bytes(), metadata


def append_evidence(directory, prefixes, baseline, witnesses, expected_hashes):
    require(preservation_hashes(directory, prefixes) == expected_hashes, 'Preservation evidence bytes changed')
    for prefix in prefixes:
        snapshot = read_json(directory / (prefix + '-untracked.json'))
        validate_preserved_record(snapshot, baseline)
        proof = read_json(directory / (prefix + '-bookkeeping-prefix-proof.json'))
        require(proof.get('file') == BOOKKEEPING and proof.get('capture_attempts') in (1, 2, 3), 'Invalid bookkeeping proof')
        expected = [('original-build', baseline[BOOKKEEPING]), *witnesses]
        if prefix == 'before' and directory == ORIGINAL:
            expected = []  # Fresh baseline has no earlier release's size/hash assumptions.
        for label, record in expected:
            require({'label': label, 'bytes': record['bytes'], 'sha256': record['sha256']} in proof['required_prefixes'],
                    'Missing prior witnessed prefix evidence: ' + label)
        require(snapshot[BOOKKEEPING]['bytes'] >= max((record['bytes'] for _, record in expected), default=0),
                'Bookkeeping evidence shrank')
        if directory == ORIGINAL and prefix == 'before':
            continue  # Already represented by the immutable baseline.
        label = prefix if directory == ORIGINAL else directory.name + '-' + prefix
        witnesses.append((label, snapshot[BOOKKEEPING]))


def original_evidence(directory, audit):
    summary = read_json(directory / 'result.json')
    require(not (directory / 'failed.json').exists(), 'Original build recorded a failure')
    expected = {'source': SOURCE, 'base': BASE, 'intent_commit': INTENT_COMMIT, 'deployment': DEPLOYMENT,
                'tree': TREE, 'release_id': RELEASE, 'intent_sha256': INTENT_SHA256, 'identity_sha256': IDENTITY_SHA256,
                'adapter_exit': 0, 'package_proof_exit': 0, 'preserved_untracked_files': PRESERVED_COUNT,
                'exact_original_records': PRESERVED_COUNT - 1, 'append_only_file': BOOKKEEPING,
                'guard_inactive': True, 'evidence': str(directory)}
    require(all(summary.get(key) == value for key, value in expected.items()), 'Original build result differs from this release')
    names = ['result.json', 'started.json']
    for name, argv, cwd in (
        ('06-governed-adapter', ['yarn', 'build'], ROOT / 'frontend'),
        ('07-package-proof', ['python', '-B', 'scripts/emergent_deployment_adapter.py', 'verify-packages'], ROOT),
    ):
        _, metadata = receipt(directory, name, argv, cwd)
        require(timestamp(metadata['finished_at']) <= timestamp(summary['finished_at']), 'Original completion precedes command receipt')
        require((directory / (name + '.stdout')).stat().st_size + (directory / (name + '.stderr')).stat().st_size > 0,
                'Missing original command output: ' + name)
        names.extend(name + suffix for suffix in ('.started.json', '.json', '.stdout', '.stderr'))
    for name in ('01b-guard-under-lock', '03-guard-before-fast-forward', '05-guard-before-build', '08-guard-after-build'):
        require(read_json(directory / (name + '.json')) == {'active': False, 'under_operation_lock': True},
                'Original locked guard evidence missing: ' + name)
        names.append(name + '.json')
    raw, _ = receipt(directory, '01-guard-before', GUARD + ['status'])
    inactive(json.loads(raw))
    names.extend('01-guard-before' + suffix for suffix in ('.started.json', '.json', '.stdout', '.stderr'))
    baseline = read_json(directory / 'before-untracked.json')
    require(isinstance(baseline, dict) and len(baseline) == PRESERVED_COUNT and BOOKKEEPING in baseline, 'Wrong original preservation baseline')
    require(stat.S_ISREG(baseline[BOOKKEEPING]['mode']), 'Original bookkeeping was not regular')
    witnesses = []
    append_evidence(directory, ('before', 'after-fast-forward', 'after'), baseline, witnesses, summary['preservation_sha256'])
    names.extend(summary['preservation_sha256'])
    hashes = {name: sha256(directory / name) for name in names}
    write_json(audit.directory / 'original-evidence.json', hashes)
    return baseline, witnesses, hashes


def workspace(audit, prefix, baseline, witnesses):
    require(audit.git(prefix + '-branch', 'branch', '--show-current') == BRANCH, 'Wrong branch')
    require(audit.git(prefix + '-head', 'rev-parse', 'HEAD') == DEPLOYMENT, 'Wrong deployment HEAD')
    require(audit.git(prefix + '-origin', 'rev-parse', 'origin/' + BRANCH) == DEPLOYMENT, 'Wrong production tracking ref')
    require(audit.git(prefix + '-tree', 'rev-parse', 'HEAD^{tree}') == TREE, 'Wrong reviewed tree')
    require(audit.git(prefix + '-parents', 'rev-list', '--parents', '-n', '1', DEPLOYMENT).split()[1:] == [BASE, INTENT_COMMIT], 'Wrong merge parents')
    require(audit.git(prefix + '-intent-parent', 'rev-list', '--parents', '-n', '1', INTENT_COMMIT).split()[1:] == [SOURCE], 'Wrong intent parent')
    require(audit.git(prefix + '-intent-diff', 'diff', '--name-only', SOURCE, DEPLOYMENT) == 'release/release-intent-v5.json', 'Governed source changed after A2')
    require(not audit.git(prefix + '-tracked-status', 'status', '--porcelain', '--untracked-files=no'), 'Tracked worktree is dirty')
    current = preserved_snapshot(audit, prefix, baseline, witnesses)
    intent_path, identity_path = ROOT / 'release/release-intent-v5.json', ROOT / 'backend/release_identity.json'
    require(sha256(intent_path) == INTENT_SHA256 and sha256(identity_path) == IDENTITY_SHA256, 'Intent or runtime identity bytes differ from reviewed pins')
    intent, identity = read_json(intent_path), read_json(identity_path)
    for payload in (intent, identity):
        for field, expected in {'source_git_sha': SOURCE, 'source_base_git_sha': BASE, 'branch': BRANCH, 'protocol_version': 5}.items():
            require(payload.get(field) == expected, 'Wrong local identity field: ' + field)
    require(identity.get('release_id') == RELEASE and identity == intent.get('runtime_identity'), 'Local runtime identity differs from intent')
    write_json(audit.directory / (prefix + '-identity.json'), {
        'intent_sha256': INTENT_SHA256, 'runtime_identity_sha256': IDENTITY_SHA256,
        'source_git_sha': SOURCE, 'source_base_git_sha': BASE, 'deployment_git_sha': DEPLOYMENT, 'release_id': RELEASE})
    return identity, current


def common_identity(payload):
    for field, expected in {'source_git_sha': SOURCE, 'git_sha': SOURCE, 'deployment_git_sha': DEPLOYMENT,
                            'release_id': RELEASE, 'protocol_version': 5}.items():
        require(payload.get(field) == expected, 'Wrong guard identity field: ' + field)


def own_lease(payload, identity):
    common_identity(payload)
    require(payload.get('actor') == ACTOR and payload.get('branch') == BRANCH, 'Not this actor/branch lease')
    require(payload.get('source_base_git_sha') == BASE and payload.get('runtime_identity') == identity, 'Lease identity differs')
    timestamp(payload['prepared_at'])


def inactive(status):
    require(status.get('active') is False and not status.get('incomplete_lease_directory'), 'Guard is active or incomplete')


def exact_active(status, lease):
    require(status.get('active') is True and {key: value for key, value in status.items() if key != 'active'} == lease,
            'Active lease does not exactly match this prepare receipt')


def completed(gate, phase, baseline, witnesses, original_hashes):
    directory = gate / phase
    started, result = read_json(directory / 'started.json'), read_json(directory / 'result.json')
    require(not (directory / 'failed.json').exists(), 'Prior phase recorded a failure')
    require(started.get('phase') == phase and result.get('phase') == phase and result.get('success') is True,
            'Prior phase has not completed successfully')
    require(result.get('original_evidence') == str(ORIGINAL) and result.get('preserved_untracked_files') == PRESERVED_COUNT,
            'Prior phase used different original evidence')
    common_identity(result)
    require(read_json(directory / 'original-evidence.json') == original_hashes, 'Original evidence changed since prior phase')
    require(sha256(directory / 'guard.json') == result.get('guard_receipt_sha256'), 'Prior guard receipt bytes changed')
    raw, metadata = receipt(directory, 'guard', PHASE_ARGV[phase])
    require(timestamp(metadata['finished_at']) <= timestamp(result['finished_at']), 'Invalid prior completion time')
    status, _ = receipt(directory, 'status-after', GUARD + ['status'])
    append_evidence(directory, ('before', 'after'), baseline, witnesses, result['preservation_sha256'])
    return result, json.loads(raw), json.loads(status)


def main():
    require_pins()  # Fail before /app access, subprocesses or phase claims.
    require(ORIGINAL.is_absolute() and str(ORIGINAL).startswith('/tmp/mezan-pr1922-original-'), 'Unpinned original evidence directory')
    require('UNPINNED' not in ACTOR and re.fullmatch(r'[A-Za-z0-9_-]{3,128}', ACTOR) is not None, 'Unpinned actor')
    require('UNPINNED' not in BASELINE_UI_VERSION and re.fullmatch(r'[A-Za-z0-9_-]{3,128}', BASELINE_UI_VERSION) is not None, 'Unpinned current UI version')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase', choices=tuple(PHASE_ARGV))
    parser.add_argument('original_evidence', type=Path)
    parser.add_argument('--ui-event-text')
    parser.add_argument('--ui-finished-at')
    parser.add_argument('--ui-published-version')
    args = parser.parse_args()
    require(args.original_evidence.resolve(strict=True) == ORIGINAL and ORIGINAL.resolve(strict=True) == ORIGINAL,
            'Explicit original evidence path is not the reviewed run')
    require(args.phase == 'verify' or not any((args.ui_event_text, args.ui_finished_at, args.ui_published_version)),
            'UI event arguments belong only to verify')
    os.umask(0o077)
    sys.path.insert(0, str(ROOT))
    gate = ORIGINAL / 'release-gate'
    gate.mkdir(mode=0o700, exist_ok=True)
    directory = gate / args.phase
    directory.mkdir(mode=0o700)  # Exclusive phase claim; no overwrite or retry.
    write_json(directory / 'started.json', {'phase': args.phase, 'started_at': now(),
        'argv': [sys.executable, *sys.argv], 'cwd': os.getcwd(), 'original_evidence': str(ORIGINAL)})
    audit = Audit(directory)
    try:
        baseline, witnesses, original_hashes = original_evidence(ORIGINAL, audit)
        prepared = prepare_status = prepublish = prepublish_status = prior = None
        if args.phase != 'prepare':
            _, prepared, prepare_status = completed(gate, 'prepare', baseline, witnesses, original_hashes)
        if args.phase == 'verify':
            prior, prepublish, prepublish_status = completed(gate, 'prepublish', baseline, witnesses, original_hashes)
        identity, before = workspace(audit, 'before', baseline, witnesses)
        if prepared is not None:
            own_lease(prepared, identity)
            exact_active(prepare_status, prepared)
        if args.phase == 'verify':
            common_identity(prepublish)
            require(prepublish.get('ready_to_publish') is True, 'No actual successful prepublish receipt')
            exact_active(prepublish_status, prepared)
            require(bool(args.ui_event_text) and args.ui_event_text.startswith('Deployment finished '), 'Actual deployment-finished UI text is required')
            require(bool(args.ui_published_version) and re.fullmatch(r'[A-Za-z0-9_-]{3,128}', args.ui_published_version)
                    and not args.ui_published_version.startswith(BASELINE_UI_VERSION)
                    and not BASELINE_UI_VERSION.startswith(args.ui_published_version), 'A new current published UI version is required')
            require(bool(args.ui_finished_at) and timestamp(prior['finished_at']) < timestamp(args.ui_finished_at) <= timestamp(now()),
                    'Explicit UI completion must be newer than prepublish and not in the future')
            write_json(directory / 'ui-event.json', {'text': args.ui_event_text, 'finished_at': args.ui_finished_at,
                'current_published_version': args.ui_published_version, 'previous_published_version': BASELINE_UI_VERSION,
                'deployment_git_sha': DEPLOYMENT, 'declaration': 'Explicit root observation; official verify supplies runtime proof'})
        before_status = audit.json('status-before', GUARD + ['status'])
        if args.phase == 'prepare':
            inactive(before_status)
        else:
            exact_active(before_status, prepared)

        # Exactly one official command. Its CLI owns the mutex; no outer lock.
        # Audit.run persists the exit and full streams before any postcheck.
        errors = []
        raw = None
        try:
            raw = audit.run('guard', PHASE_ARGV[args.phase])
        except BaseException as exc:
            errors.append({'check': 'official-' + args.phase, 'exception': type(exc).__name__ + ': ' + str(exc)})
        after_status = after_identity = after = payload = None
        try:
            # Always attempt actual post-status, including after an official failure.
            after_status = audit.json('status-after', GUARD + ['status'])
        except BaseException as exc:
            errors.append({'check': 'status-after', 'exception': type(exc).__name__ + ': ' + str(exc)})
        try:
            after_identity, after = workspace(audit, 'after', baseline, [*witnesses, (args.phase + '-before', before[BOOKKEEPING])])
            require(after_identity == identity, 'Runtime identity changed during the guard phase')
        except BaseException as exc:
            errors.append({'check': 'workspace-after', 'exception': type(exc).__name__ + ': ' + str(exc)})
        try:
            require(raw is not None, 'No successful official command receipt')
            payload = json.loads(raw)
            common_identity(payload)
            require(after_status is not None, 'No successful actual post-status')
            if args.phase == 'prepare':
                own_lease(payload, identity)
                exact_active(after_status, payload)
            elif args.phase == 'prepublish':
                require(payload.get('ready_to_publish') is True, 'Guard did not authorize prepublish')
                exact_active(after_status, prepared)
            else:
                require(payload.get('verified') is True and payload.get('checks') == 3 and payload.get('url') == ORIGIN,
                        'Guard did not complete the three production checks')
                require(payload.get('source_base_git_sha') == BASE, 'Wrong verified source base')
                for field in ('backend_runtime_source', 'release_control_source'):
                    require(payload.get(field) == prepared.get(field), 'Wrong verified source binding: ' + field)
                require(payload.get('frontend_artifact_tree_sha256') == identity['frontend_build']['artifact_tree_sha256'], 'Wrong verified frontend artifact')
                inactive(after_status)
        except BaseException as exc:
            errors.append({'check': 'official-result-and-status', 'exception': type(exc).__name__ + ': ' + str(exc)})
        guard_path = directory / 'guard.json'
        guard_receipt = read_json(guard_path) if guard_path.exists() else None
        write_json(directory / 'outcomes.json', {
            'phase': args.phase, 'official_guard_started': (directory / 'guard.started.json').exists(),
            'actual_guard_exit_code': guard_receipt.get('exit_code') if guard_receipt else None,
            'guard_receipt_sha256': sha256(guard_path) if guard_receipt else None,
            'guard_reported_verified': payload.get('verified') if isinstance(payload, dict) else None,
            'guard_reported_checks': payload.get('checks') if isinstance(payload, dict) else None,
            'status_after_active': after_status.get('active') if isinstance(after_status, dict) else None,
            'postcheck_errors': errors,
            'instruction': 'Never rerun an official phase because a later preservation or receipt check failed'})
        require(not errors, 'Phase checks failed; inspect actual persisted receipts and outcomes; do not retry the phase')
        result = {'success': True, 'phase': args.phase, 'finished_at': now(), 'original_evidence': str(ORIGINAL),
            'source_git_sha': SOURCE, 'git_sha': SOURCE, 'source_base_git_sha': BASE,
            'intent_git_sha': INTENT_COMMIT, 'deployment_git_sha': DEPLOYMENT, 'tree': TREE,
            'release_id': RELEASE, 'protocol_version': 5, 'actor': ACTOR,
            'preserved_untracked_files': PRESERVED_COUNT, 'exact_original_records': PRESERVED_COUNT - 1,
            'append_only_file': BOOKKEEPING, 'before_bookkeeping_bytes': before[BOOKKEEPING]['bytes'],
            'after_bookkeeping_bytes': after[BOOKKEEPING]['bytes'], 'all_witnessed_prefixes_preserved': True,
            'preservation_sha256': preservation_hashes(directory, ('before', 'after')),
            'status_after_active': after_status['active'], 'guard_receipt_sha256': sha256(guard_path),
            'evidence': str(directory)}
        write_json(directory / 'result.json', result)
        print(json.dumps({key: value for key, value in result.items() if key != 'preservation_sha256'}, indent=2), flush=True)
    except BaseException as exc:
        path = directory / 'guard.json'
        recorded = read_json(path) if path.exists() else None
        write_json(directory / 'failed.json', {'phase': args.phase, 'finished_at': now(),
            'exception': type(exc).__name__ + ': ' + str(exc),
            'official_guard_started': (directory / 'guard.started.json').exists(),
            'actual_guard_exit_code': recorded.get('exit_code') if recorded else None,
            'guard_receipt_sha256': sha256(path) if path.exists() else None,
            'do_not_retry_existing_phase': True})
        raise


if __name__ == '__main__':
    main()
