"""PR1920 verify-only continuation after one evidenced platform-file append.

Does not prepare, publish, rebuild, clear a lease, edit /app, or retry a phase.
Retains the original failed phase and frozen gate. Only the one named untracked
file may grow, with every previously witnessed prefix preserved exactly.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import types

sys.dont_write_bytecode = True

FROZEN_GATE_SHA256 = 'd072a7a0d92f8f6f79ade613094ebe0dda4c123563341b907f6134f85b8a9aff'
PHASE = 'verify-after-bookkeeping-review'
BOOKKEEPING = '.emergent/stripped_env_names'
ORIGINAL_BYTES = 48418
ORIGINAL_SHA256 = '041a33859b1bc209fce7c42a64d1c2210a3f3cb49ea4c78522bc5a6b4a7db3a8'
FAILED_BYTES = 48590
FAILED_SHA256 = '07d8050b846f6c552b49024b6fedfebd3c4f54ad340fd272066c852f7f4d3be1'
FAILED_MTIME_NS = 1794671030322505846
FOLLOWUP_BYTES = 48676
FOLLOWUP_SHA256 = '1a715333f8f54e8fe579f3a9ed0ef1b742fe25ddad15053d2975afda430b9d05'
FAILED_AT = '2026-11-14T15:55:57.683782Z'
INTENT_SHA256 = '9980404a8cd44b0b9a687923202e428bbb2ccec7482e0c4a411ffe75fd68d02b'
IDENTITY_SHA256 = 'dccfab7d9d59cc5670969744606dcc2f97f1351401b9b60c0a4563aba6eb622c'
PREPARE_STDOUT_SHA256 = '20d5b57fe2faf3213a50ed1d5dae6725d5a318cc7b4ffce48d250a71d0fa1694'
PREPUBLISH_STDOUT_SHA256 = 'e405edb4c8bf68a8e8f0d3db93b4d1ddc71e6cd3fc7a817729f5b0efa22386f9'
PREPARED_AT = '2026-11-14T15:39:37.177675+00:00'
UI_TEXT = 'Deployment finished Nov 14, 06:51 PM'
UI_FINISHED_AT = '2026-11-14T15:51:00Z'
UI_VERSION = '1b4c4f6'


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def load_frozen_gate(path):
    require(path.is_file() and not path.is_symlink(), 'Frozen gate must be a regular file')
    content = path.read_bytes()
    require(hashlib.sha256(content).hexdigest() == FROZEN_GATE_SHA256, 'Frozen gate bytes differ')
    gate = types.ModuleType('pr1920_frozen_release_gate')
    gate.__file__ = str(path)
    exec(compile(content, str(path), 'exec'), gate.__dict__)
    return gate


def file_record(g, path):
    metadata = path.lstat()
    require(stat.S_ISREG(metadata.st_mode), 'Evidence must be a regular file')
    return {'bytes': metadata.st_size, 'sha256': g.sha256(path),
            'mode': metadata.st_mode, 'inode': metadata.st_ino,
            'mtime_ns': metadata.st_mtime_ns}


def evidence_snapshot(g, gate_root):
    # These frozen phase directories contain flat receipts only. Never write in them.
    result = {}
    for phase in ('prepare', 'prepublish', 'verify'):
        directory = gate_root / phase
        require(directory.is_dir() and not directory.is_symlink(), 'Missing original phase directory')
        result[phase] = {path.name: file_record(g, path) for path in sorted(directory.iterdir())}
    return result


def failed_preflight(g, gate_root, original):
    failed_dir = gate_root / 'verify'
    failure = g.read_json(failed_dir / 'failed.json')
    require(failure == {'phase': 'verify', 'finished_at': FAILED_AT,
                        'exception': 'RuntimeError: Preservation baseline changed'},
            'Original failure is not the reviewed preservation preflight failure')
    for name in ('guard.started.json', 'guard.json', 'guard.stdout', 'guard.stderr',
                 'result.json', 'status-before.started.json', 'status-before.json',
                 'status-before.stdout', 'status-before.stderr', 'before-identity.json'):
        require(not (failed_dir / name).exists() and not (failed_dir / name).is_symlink(),
                'Original verification progressed beyond the reviewed failure: ' + name)
    started = g.read_json(failed_dir / 'started.json')
    require(started == {
        'phase': 'verify', 'started_at': '2026-11-14T15:55:57.632919Z',
        'argv': ['/usr/local/bin/python', '-c', 'verify', str(g.ORIGINAL),
                 '--ui-event-text', UI_TEXT, '--ui-finished-at', UI_FINISHED_AT,
                 '--ui-published-version', UI_VERSION],
        'cwd': '/app', 'original_evidence': str(g.ORIGINAL),
    }, 'Original failed start receipt differs from the actual reviewed observation')
    failed = g.read_json(failed_dir / 'before-untracked.json')
    require(set(failed) == set(original) and len(failed) == 120, 'Original failed path set differs')
    require(all(failed[name] == original[name] for name in original if name != BOOKKEEPING),
            'Original failure included another changed file')
    known = dict(original[BOOKKEEPING], bytes=FAILED_BYTES, sha256=FAILED_SHA256,
                 mtime_ns=FAILED_MTIME_NS)
    require(failed[BOOKKEEPING] == known, 'Original failed bookkeeping observation differs')
    return failure


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


def append_snapshot(g, baseline, previous=None):
    """Read hashes only; reject replacement, truncation, edits, or unstable capture."""
    required = [(ORIGINAL_BYTES, ORIGINAL_SHA256, 'original-build'),
                (FAILED_BYTES, FAILED_SHA256, 'failed-preflight'),
                (FOLLOWUP_BYTES, FOLLOWUP_SHA256, 'root-followup-observation')]
    if previous is not None:
        required.append((previous['bytes'], previous['sha256'], 'before-official-verify'))
    path = g.ROOT / BOOKKEEPING
    witnessed = []
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    with os.fdopen(descriptor, 'rb') as handle:
        for attempt in range(1, 4):
            first = os.fstat(handle.fileno())
            require(stat.S_ISREG(first.st_mode) and first.st_mode == baseline['mode']
                    and first.st_ino == baseline['inode'], 'Bookkeeping file kind, mode, or inode changed')
            require(first.st_mtime_ns >= baseline['mtime_ns'], 'Bookkeeping timestamp moved backwards')
            if previous is not None:
                require(first.st_mtime_ns >= previous['mtime_ns'], 'Bookkeeping timestamp predates before snapshot')
            require(first.st_size >= max(item[0] for item in required), 'Bookkeeping file was truncated')
            full_hash = prefix_sha256(handle, first.st_size)
            proofs = []
            for count, expected, label in required:
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
            if (first.st_size, first.st_mtime_ns) == (last.st_size, last.st_mtime_ns) == (current_path.st_size, current_path.st_mtime_ns):
                record = {'bytes': first.st_size, 'sha256': full_hash, 'mode': first.st_mode,
                          'inode': first.st_ino, 'mtime_ns': first.st_mtime_ns}
                return record, {'file': BOOKKEEPING, 'original_bytes': ORIGINAL_BYTES,
                                'appended_bytes_since_original': first.st_size - ORIGINAL_BYTES,
                                'capture_attempts': attempt, 'required_prefixes': proofs,
                                'intermediate_witnessed_prefixes': witnessed,
                                'attribution': 'Unknown; no writer or publication attribution asserted'}
            # A concurrent extension is not ignored: its whole witnessed prefix
            # becomes an additional immutable requirement for the next bounded read.
            required.append((first.st_size, full_hash, 'capture-' + str(attempt)))
            witnessed.append({'bytes': first.st_size, 'sha256': full_hash})
    raise RuntimeError('Bookkeeping file did not yield a stable bounded snapshot')


def preserved_snapshot(g, audit, prefix, original, previous=None):
    raw = audit.run(prefix + '-untracked-list', ['git', 'ls-files', '--others', '--exclude-standard', '-z'])
    names = [os.fsdecode(item) for item in raw.split(b'\0') if item]
    require(len(names) == 120 and len(set(names)) == 120 and set(names) == set(original),
            'Untracked paths added or removed')
    current = {}
    for name in names:
        if name == BOOKKEEPING:
            continue
        path = g.ROOT / name
        metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode):
            payload = {'link': os.readlink(path)}
        else:
            require(stat.S_ISREG(metadata.st_mode), 'Unexpected untracked file kind: ' + name)
            payload = {'bytes': metadata.st_size, 'sha256': g.sha256(path)}
        current[name] = dict(payload, mode=metadata.st_mode, inode=metadata.st_ino, mtime_ns=metadata.st_mtime_ns)
    record, proof = append_snapshot(g, original[BOOKKEEPING], previous)
    current[BOOKKEEPING] = record
    g.write_json(audit.directory / (prefix + '-untracked.json'), current)
    g.write_json(audit.directory / (prefix + '-bookkeeping-prefix-proof.json'), proof)
    require(all(current[name] == original[name] for name in names if name != BOOKKEEPING),
            'An unapproved preserved file changed')
    return current


def workspace(g, audit, prefix, original, previous=None):
    # Same source/branch/tree/ancestry/tracked-clean checks as the pinned gate.
    require(audit.git(prefix + '-branch', 'branch', '--show-current') == g.BRANCH, 'Wrong branch')
    require(audit.git(prefix + '-head', 'rev-parse', 'HEAD') == g.DEPLOYMENT, 'Wrong deployment HEAD')
    require(audit.git(prefix + '-origin', 'rev-parse', 'origin/' + g.BRANCH) == g.DEPLOYMENT, 'Wrong production tracking ref')
    require(audit.git(prefix + '-tree', 'rev-parse', 'HEAD^{tree}') == g.TREE, 'Wrong reviewed tree')
    require(audit.git(prefix + '-parents', 'rev-list', '--parents', '-n', '1', g.DEPLOYMENT).split()[1:] == [g.BASE, g.INTENT_COMMIT], 'Wrong merge parents')
    require(audit.git(prefix + '-intent-parent', 'rev-list', '--parents', '-n', '1', g.INTENT_COMMIT).split()[1:] == [g.SOURCE], 'Wrong intent commit parent')
    require(audit.git(prefix + '-intent-diff', 'diff', '--name-only', g.SOURCE, g.DEPLOYMENT) == 'release/release-intent-v5.json', 'Governed source changed after A')
    require(not audit.git(prefix + '-tracked-status', 'status', '--porcelain', '--untracked-files=no'), 'Tracked worktree is dirty')
    current = preserved_snapshot(g, audit, prefix, original, previous)
    intent_path = g.ROOT / 'release/release-intent-v5.json'
    identity_path = g.ROOT / 'backend/release_identity.json'
    require(g.sha256(intent_path) == INTENT_SHA256, 'Intent bytes differ from independently reviewed B')
    require(g.sha256(identity_path) == IDENTITY_SHA256, 'Runtime identity bytes differ from reviewed build')
    intent = g.read_json(intent_path)
    identity = g.read_json(identity_path)
    for payload in (intent, identity):
        for field, expected in {'source_git_sha': g.SOURCE, 'source_base_git_sha': g.BASE,
                                'branch': g.BRANCH, 'protocol_version': 5}.items():
            require(payload.get(field) == expected, 'Wrong local identity field: ' + field)
    require(identity.get('release_id') == g.RELEASE and identity == intent.get('runtime_identity'), 'Local identity differs from intent')
    g.write_json(audit.directory / (prefix + '-identity.json'), {
        'intent_sha256': INTENT_SHA256, 'runtime_identity_sha256': IDENTITY_SHA256,
        'source_git_sha': g.SOURCE, 'source_base_git_sha': g.BASE,
        'deployment_git_sha': g.DEPLOYMENT, 'release_id': g.RELEASE,
    })
    return identity, current


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--frozen-gate', type=Path, required=True)
    parser.add_argument('--ui-event-text', required=True)
    parser.add_argument('--ui-finished-at', required=True)
    parser.add_argument('--ui-published-version', required=True)
    args = parser.parse_args()
    frozen_path = args.frozen_gate.resolve(strict=True)
    require(not frozen_path.is_relative_to(Path('/app')), 'Pinned gate reference copy must be outside /app')
    require(Path(__file__).is_file() and not Path(__file__).is_symlink()
            and not Path(__file__).resolve(strict=True).is_relative_to(Path('/app')),
            'Execute the reviewed recovery runner as a real regular file outside /app')
    g = load_frozen_gate(frozen_path)
    require(args.ui_event_text == UI_TEXT and args.ui_published_version == UI_VERSION
            and g.timestamp(args.ui_finished_at) == g.timestamp(UI_FINISHED_AT), 'UI evidence differs from reviewed actual observation')
    require(g.ORIGINAL.resolve(strict=True) == g.ORIGINAL, 'Original evidence is redirected')
    gate_root = g.ORIGINAL / 'release-gate'
    require(gate_root.is_dir() and not gate_root.is_symlink(), 'Missing original release-gate evidence')
    os.umask(0o077)
    directory = gate_root / PHASE
    directory.mkdir(mode=0o700)  # Exclusive, new name; no reuse, overwrite, or retry.
    g.write_json(directory / 'started.json', {
        'phase': PHASE, 'started_at': g.now(), 'argv': [sys.executable, *sys.argv],
        'cwd': os.getcwd(), 'original_evidence': str(g.ORIGINAL),
        'frozen_gate_reference_copy': str(frozen_path), 'frozen_gate_sha256': FROZEN_GATE_SHA256,
        'original_gate_invocation': 'python -c; its source string is not recorded in argv; no original source file is asserted',
        'recovery_runner_sha256': g.sha256(Path(__file__)),
    })
    audit = g.Audit(directory)
    try:
        original = g.original_evidence(g.ORIGINAL, audit)
        require(BOOKKEEPING in original and original[BOOKKEEPING].get('bytes') == ORIGINAL_BYTES
                and original[BOOKKEEPING].get('sha256') == ORIGINAL_SHA256,
                'Original bookkeeping baseline differs')
        require(stat.S_ISREG(original[BOOKKEEPING]['mode']), 'Original bookkeeping was not a regular file')
        failure = failed_preflight(g, gate_root, original)
        prior_files = evidence_snapshot(g, gate_root)
        g.write_json(directory / 'original-phases-before.json', prior_files)
        original_hashes = g.read_json(directory / 'original-evidence.json')
        for phase in ('prepare', 'prepublish', 'verify'):
            require(g.read_json(gate_root / phase / 'original-evidence.json') == original_hashes,
                    'Original evidence no longer matches prior phase: ' + phase)
        g.write_json(directory / 'preservation-review.json', {
            'original_failed_phase': str(gate_root / 'verify'), 'failure': failure,
            'official_guard_never_started_in_failed_phase': True,
            'policy': '119 exact original records; one specific regular file may only extend witnessed prefixes',
            'file': BOOKKEEPING, 'original_bytes': ORIGINAL_BYTES, 'original_sha256': ORIGINAL_SHA256,
            'failed_bytes': FAILED_BYTES, 'failed_sha256': FAILED_SHA256,
            'followup_bytes': FOLLOWUP_BYTES, 'followup_sha256': FOLLOWUP_SHA256,
            'frozen_phase_and_source_unchanged': True, 'writer_attribution': 'Unknown',
            'reference_provenance': 'Exact d072 frozen source from reviewed GitHub commit; original -c source is not in argv',
        })
        identity, before = workspace(g, audit, 'before', original)
        _, prepared, prepare_status = g.completed(gate_root, 'prepare')
        g.own_lease(prepared, identity)
        g.exact_active(prepare_status, prepared)
        require(prepared['prepared_at'] == PREPARED_AT, 'Different prepare instance')
        require(g.sha256(gate_root / 'prepare' / 'guard.stdout') == PREPARE_STDOUT_SHA256, 'Prepare output differs')
        prior, prepublish, prepublish_status = g.completed(gate_root, 'prepublish')
        g.common_identity(prepublish)
        require(prepublish.get('ready_to_publish') is True, 'No successful prepublish receipt')
        g.exact_active(prepublish_status, prepared)
        require(g.sha256(gate_root / 'prepublish' / 'guard.stdout') == PREPUBLISH_STDOUT_SHA256, 'Prepublish output differs')
        require(g.timestamp(prior['finished_at']) < g.timestamp(args.ui_finished_at) <= g.timestamp(g.now()), 'Invalid actual UI completion time')
        g.write_json(directory / 'ui-event.json', {
            'text': args.ui_event_text, 'finished_at': args.ui_finished_at,
            'current_published_version': args.ui_published_version,
            'previous_published_version': g.BASELINE_UI_VERSION,
            'deployment_git_sha': g.DEPLOYMENT,
            'declaration': 'Explicit root observation; official verify supplies runtime proof',
        })
        before_status = audit.json('status-before', g.GUARD + ['status'])
        g.exact_active(before_status, prepared)
        # Exactly one official verify. Its CLI owns the operation mutex; no outer lock.
        # Audit.run persists its actual exit and streams before any postcheck executes.
        errors = []
        raw = None
        try:
            raw = audit.run('guard', g.PHASE_ARGV['verify'])
        except BaseException as exc:
            errors.append({'check': 'official-verify', 'exception': type(exc).__name__ + ': ' + str(exc)})
        after_status = None
        after_identity = None
        after = None
        try:
            # Always obtain the actual post-status, even if another postcheck fails.
            after_status = audit.json('status-after', g.GUARD + ['status'])
        except BaseException as exc:
            errors.append({'check': 'status-after', 'exception': type(exc).__name__ + ': ' + str(exc)})
        try:
            after_identity, after = workspace(g, audit, 'after', original, before[BOOKKEEPING])
            require(after_identity == identity, 'Runtime identity changed during verification')
        except BaseException as exc:
            errors.append({'check': 'workspace-after', 'exception': type(exc).__name__ + ': ' + str(exc)})
        try:
            after_files = evidence_snapshot(g, gate_root)
            g.write_json(directory / 'original-phases-after.json', after_files)
            require(after_files == prior_files, 'Original prepare/prepublish/failed receipts changed')
            require(g.sha256(frozen_path) == FROZEN_GATE_SHA256, 'Frozen gate changed during verification')
        except BaseException as exc:
            errors.append({'check': 'frozen-evidence-after', 'exception': type(exc).__name__ + ': ' + str(exc)})
        payload = None
        try:
            require(raw is not None, 'No successful official verification receipt')
            payload = json.loads(raw)
            g.common_identity(payload)
            require(payload.get('verified') is True and payload.get('checks') == 3 and payload.get('url') == g.ORIGIN,
                    'Official verify did not complete all three production checks')
            require(payload.get('source_base_git_sha') == g.BASE, 'Wrong verified base')
            for field in ('backend_runtime_source', 'release_control_source'):
                require(payload.get(field) == prepared.get(field), 'Wrong verified source binding: ' + field)
            require(payload.get('frontend_artifact_tree_sha256') == identity['frontend_build']['artifact_tree_sha256'], 'Wrong verified frontend artifact')
            require(after_status is not None, 'No successful actual post-status')
            g.inactive(after_status)
        except BaseException as exc:
            errors.append({'check': 'official-result-and-closure', 'exception': type(exc).__name__ + ': ' + str(exc)})
        guard_receipt_path = directory / 'guard.json'
        guard_receipt = g.read_json(guard_receipt_path) if guard_receipt_path.exists() else None
        g.write_json(directory / 'outcomes.json', {
            'official_guard_started': (directory / 'guard.started.json').exists(),
            'actual_guard_exit_code': guard_receipt.get('exit_code') if guard_receipt else None,
            'guard_receipt_sha256': g.sha256(guard_receipt_path) if guard_receipt else None,
            'guard_reported_verified': payload.get('verified') if isinstance(payload, dict) else None,
            'guard_reported_checks': payload.get('checks') if isinstance(payload, dict) else None,
            'status_after': after_status, 'postcheck_errors': errors,
            'instruction': 'Do not rerun verification because of a subsequent preservation or receipt error',
        })
        require(not errors, 'Recovery checks failed; inspect persisted outcomes and actual official receipts; do not rerun verify')
        result = {
            'success': True, 'phase': PHASE, 'finished_at': g.now(),
            'original_evidence': str(g.ORIGINAL), 'original_failed_phase_preserved': True,
            'source_git_sha': g.SOURCE, 'git_sha': g.SOURCE, 'source_base_git_sha': g.BASE,
            'intent_git_sha': g.INTENT_COMMIT, 'deployment_git_sha': g.DEPLOYMENT,
            'tree': g.TREE, 'release_id': g.RELEASE, 'protocol_version': 5, 'actor': g.ACTOR,
            'official_verify_invocations': 1, 'checks': payload['checks'], 'verified': payload['verified'],
            'preserved_untracked_files': 120, 'exact_original_records': 119,
            'append_only_file': BOOKKEEPING, 'before_bytes': before[BOOKKEEPING]['bytes'],
            'after_bytes': after[BOOKKEEPING]['bytes'], 'before_sha256': before[BOOKKEEPING]['sha256'],
            'after_sha256': after[BOOKKEEPING]['sha256'], 'all_witnessed_prefixes_preserved': True,
            'status_after_active': after_status['active'],
            'guard_receipt_sha256': g.sha256(guard_receipt_path), 'evidence': str(directory),
        }
        g.write_json(directory / 'result.json', result)
        print(json.dumps(result, indent=2), flush=True)
    except BaseException as exc:
        receipt_path = directory / 'guard.json'
        receipt = g.read_json(receipt_path) if receipt_path.exists() else None
        g.write_json(directory / 'failed.json', {
            'phase': PHASE, 'finished_at': g.now(), 'exception': type(exc).__name__ + ': ' + str(exc),
            'official_guard_started': (directory / 'guard.started.json').exists(),
            'actual_guard_exit_code': receipt.get('exit_code') if receipt else None,
            'guard_receipt_sha256': g.sha256(receipt_path) if receipt else None,
            'do_not_retry_existing_phase': True,
        })
        raise


if __name__ == '__main__':
    main()
