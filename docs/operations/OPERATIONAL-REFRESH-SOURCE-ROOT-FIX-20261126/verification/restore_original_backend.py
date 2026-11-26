"""Restore only the Backend stopped by PR2133 after verified publication.

Retain the original failed receipt and preservation baseline unchanged. Record
the observed credential inode replacement and append-only platform bookkeeping
as explicit variances; do not read, hash, copy or modify credential contents.
"""
import datetime
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys

ROOT = Path('/app')
STAGE = ROOT / '.git/operational-refresh-2133-50cbe07d'
OUTPUT = STAGE / 'restore-continuation.json'
SOURCE = '50cbe07da4e29fa41c7a184addffdeb8d2169f1a'
DEPLOYMENT = '317dc183485566ba9e05c5afca5d4ea1b84e291e'
TREE = 'a8aedd1be45cf7dcb26b31078a6c5e580709e30f'
RELEASE = 'rg5-5064eab0115d74d39ba29eeed04f13511f5a27156260c0cc5a3a4bc2045e1fb5'
PROOF_SHA = '108220774d22e0074127aa10d63b1dbd5e35afbaaf87998ac14c6bed37da6c4b'
BOOKKEEPING = '.emergent/stripped_env_names'
EXPECTED_ENV = {'path': 'backend/.env', 'device': 66341, 'inode': 139036,
                'mode': 33152, 'uid': 0, 'gid': 0}
EXPECTED_ENV_METADATA = {**EXPECTED_ENV, 'size': 4226,
                         'mtime_ns': 1795659981226239871}


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def run(argv, allowed=(0,), timeout=45):
    result = subprocess.run(argv, cwd=ROOT, capture_output=True, text=True,
                            timeout=timeout, check=False)
    assert result.returncode in allowed, (argv, result.returncode)
    return result.stdout.strip(), result.returncode


def main():
    sys.path.insert(0, str(STAGE))
    import prepare_original_host as helper

    assert not OUTPUT.exists(), 'Continuation already exists; inspect, do not replay'
    original_bytes = (STAGE / 'receipt.json').read_bytes()
    receipt = json.loads(original_bytes)
    assert receipt['phase'] == 'production_verified_local_restore_pending'
    assert receipt['verify_error'] == 'foreign root metadata changed'
    assert receipt['source'] == SOURCE and receipt['deployment'] == DEPLOYMENT
    assert receipt['tree'] == TREE and receipt['release_id'] == RELEASE
    assert receipt['verify_exit_code'] == 0 and receipt['verify_invocations'] == 1
    assert receipt['publication_action_count'] == 1
    assert receipt['publication_version'] == 'bad38ff'
    assert receipt['development_backend_stopped_by_task'] is True
    assert receipt['backend_restore_required'] is True
    assert receipt['supervisor_before'].split()[1] == 'RUNNING'
    proof_bytes = (STAGE / 'guard-verify.json').read_bytes()
    assert hashlib.sha256(proof_bytes).hexdigest() == PROOF_SHA
    assert receipt['original_proof_sha256'] == PROOF_SHA
    proof = json.loads(proof_bytes)
    assert receipt['guard_verify'] == proof
    assert proof['verified'] is True and proof['checks'] == 3
    assert proof['source_git_sha'] == SOURCE
    assert proof['deployment_git_sha'] == DEPLOYMENT
    assert proof['release_id'] == RELEASE and proof['protocol_version'] == 5
    status, _ = run([sys.executable, '-B', 'scripts/production_release_guard.py', 'status'])
    assert json.loads(status) == {'active': False}
    for arguments, wanted in (
        (['rev-parse', 'HEAD'], DEPLOYMENT),
        (['rev-parse', 'HEAD^{tree}'], TREE),
        (['status', '--porcelain', '--untracked-files=no'], ''),
    ):
        assert run(['git'] + arguments)[0] == wanted

    files = helper.git_read(ROOT, 'ls-files', '--others', '--exclude-standard').splitlines()
    assert files == receipt['untracked_files'] and len(files) == 432
    roots = helper.fresh_foreign_roots(ROOT, receipt['foreign_roots'])
    root_differences = [(a, b) for a, b in zip(receipt['foreign_roots'], roots) if a != b]
    assert len(roots) == 59 and len(root_differences) == 1
    old_env, current_env = root_differences[0]
    assert old_env == {**EXPECTED_ENV, 'inode': 138897}
    assert current_env == EXPECTED_ENV
    assert helper.metadata(ROOT / 'backend/.env', 'backend/.env') == EXPECTED_ENV_METADATA
    current = [helper.fresh_untracked_record(ROOT, row)
               for row in receipt['untracked_manifest']]
    file_differences = [(a, b) for a, b in zip(receipt['untracked_manifest'], current) if a != b]
    assert len(current) == 432 and len(file_differences) == 1
    before, after = file_differences[0]
    assert before['path'] == after['path'] == BOOKKEEPING
    assert before['size'] == 123606
    assert before['sha256'] == 'd5b80825f11475ab020a337db56a98990ccfe4c1a8c034f96fbeca064bb5807d'
    assert after['size'] == 123984
    assert after['sha256'] == '494d31d20be48ca6fa02bf9134fd31b2ab7ebf561a1914e475516a6829edff12'
    assert all(before[k] == after[k] for k in helper.META_KEYS)
    descriptor = os.open(ROOT / BOOKKEEPING, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        assert helper.same_stat(os.fstat(descriptor), after)
        with os.fdopen(descriptor, 'rb', closefd=False) as stream:
            prefix = stream.read(before['size'])
        assert len(prefix) == before['size']
        assert hashlib.sha256(prefix).hexdigest() == before['sha256']
        assert helper.same_stat(os.fstat(descriptor), after)
    finally:
        os.close(descriptor)
    service, code = run(['supervisorctl', 'status', 'backend'], allowed=(3,))
    assert service.split()[1] == 'STOPPED'
    assert (STAGE / 'receipt.json').read_bytes() == original_bytes

    result = {
        'phase': 'restore_ready', 'created_at': now(), 'source': SOURCE,
        'deployment': DEPLOYMENT, 'release_id': RELEASE,
        'original_receipt_sha256': hashlib.sha256(original_bytes).hexdigest(),
        'original_receipt_unchanged': True, 'original_guard_proof_sha256': PROOF_SHA,
        'original_guard_active': False,
        'foreign_roots_unchanged': 58, 'untracked_cohort_unchanged': 432,
        'untracked_records_identical': 431,
        'credential_metadata_variance': {'before': old_env, 'after': current_env},
        'credential_current_metadata_only': EXPECTED_ENV_METADATA,
        'credential_contents_read_hashed_copied_or_modified': False,
        'bookkeeping_variance': {'before': before, 'after': after,
                                'original_prefix_preserved': True, 'appended_bytes': 378},
        'variance_actor_attribution': 'not established',
        'preservation_checked_before_restore_at': now(),
        'backend_stopped_by_this_task': True, 'backend_restore_required': True,
        'supervisor_before_task': 'RUNNING',
        'supervisor_before_restore': {'state': 'STOPPED', 'exit_code': code},
    }
    descriptor = os.open(OUTPUT, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'w') as stream:
        json.dump(result, stream, indent=2)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    try:
        result.update(phase='restore_start_requested', start_requested_at=now())
        helper.private_json(OUTPUT, result)
        run(['supervisorctl', 'start', 'backend'])
        service, status_code = run(['supervisorctl', 'status', 'backend'])
        assert service.split()[1] == 'RUNNING'
        result.update(development_backend_restored=True, restored_at=now(),
                      backend_restore_required=False,
                      supervisor_after_restore={'state': 'RUNNING', 'exit_code': status_code})
        assert (STAGE / 'receipt.json').read_bytes() == original_bytes
        assert helper.fresh_foreign_roots(ROOT, receipt['foreign_roots']) == roots
        assert helper.metadata(ROOT / 'backend/.env', 'backend/.env') == EXPECTED_ENV_METADATA
        result['phase'] = 'production_verified_local_backend_restored'
    except BaseException as error:
        result.update(phase='restore_requires_inspection', error_type=type(error).__name__)
        raise
    finally:
        helper.private_json(OUTPUT, result)
    print('SOURCE_FIX_RESTORE_COMPLETE', json.dumps(result, sort_keys=True), flush=True)


if __name__ == '__main__':
    def interrupted(_signal, _frame):
        raise KeyboardInterrupt('Restore continuation interrupted')
    signal.signal(signal.SIGTERM, interrupted)
    main()
