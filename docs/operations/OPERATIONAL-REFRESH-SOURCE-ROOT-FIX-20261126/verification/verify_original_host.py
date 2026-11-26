"""Run the unchanged original production verifier once, then restore Backend.

This task-only wrapper never publishes or creates/clears a lease. It consumes
the original-host receipt written after the explicit publication result.
"""
from pathlib import Path
import datetime
import hashlib
import json
import os
import signal
import subprocess
import sys
import traceback


ROOT = Path('/app')
STAGE = ROOT / '.git/operational-refresh-2133-50cbe07d'
ACTOR = 'operational-refresh-2133-20261126'


def main():
    receipt = json.loads((STAGE / 'receipt.json').read_text())

    def now():
        return datetime.datetime.now(datetime.timezone.utc).isoformat()

    def save():
        path = STAGE / 'receipt.json.tmp'
        path.write_text(json.dumps(receipt, indent=2) + '\n')
        os.replace(path, STAGE / 'receipt.json')

    def status():
        return json.loads(subprocess.check_output(
            [sys.executable, '-B', 'scripts/production_release_guard.py', 'status'],
            cwd=ROOT, text=True,
        ))

    try:
        assert receipt['phase'] == 'publication_succeeded'
        assert receipt['publication_action_count'] == 1
        assert not receipt.get('verify_invocations'), 'Verification already started'
        assert receipt.get('publication_version')
        assert receipt['publication_version'] != '1f3e141'
        expected = {
            'source_git_sha': receipt['source'],
            'deployment_git_sha': receipt['deployment'],
            'release_id': receipt['release_id'],
            'actor': ACTOR,
            'protocol_version': 5,
        }
        guard = status()
        assert guard['active'] is True
        for key, value in expected.items():
            assert guard.get(key) == value, (key, guard.get(key), value)
        for arguments, wanted in [
            (['rev-parse', 'HEAD'], receipt['deployment']),
            (['rev-parse', 'HEAD^{tree}'], receipt['tree']),
            (['status', '--porcelain', '--untracked-files=no'], ''),
        ]:
            got = subprocess.check_output(['git'] + arguments, cwd=ROOT, text=True).strip()
            assert got == wanted, (arguments, got, wanted)
        receipt.update(phase='verify_running', verify_runner_pid=os.getpid(),
                       verify_started_at=now(), verify_invocations=1)
        save()
        with (STAGE / 'guard-verify.json').open('x') as out, \
                (STAGE / 'guard-verify.stderr').open('x') as err:
            process = subprocess.Popen(
                [sys.executable, '-B', 'scripts/production_release_guard.py',
                 'verify', '--url', 'https://mezansalla.com'],
                cwd=ROOT, stdout=out, stderr=err, start_new_session=True,
            )
            try:
                receipt['guard_verify_pid'] = process.pid
                save()
                exit_code = process.wait()
            except BaseException:
                # Stop and join only the verifier group owned by this wrapper.
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    process.wait()
                receipt['interrupted_guard_exit_code'] = process.returncode
                save()
                raise
        receipt.update(verify_exit_code=exit_code, verify_finished_at=now())
        save()
        assert exit_code == 0, {'exit_code': exit_code,
                                'stderr': (STAGE / 'guard-verify.stderr').read_text()[-2000:]}
        proof = json.loads((STAGE / 'guard-verify.json').read_text())
        assert proof.get('verified') is True and proof.get('checks') == 3
        for key in ('source_git_sha', 'deployment_git_sha', 'release_id', 'protocol_version'):
            assert proof.get(key) == expected[key], key
        guard = status()
        assert guard['active'] is False
        receipt.update(
            phase='production_verified', verified_at=now(), guard_verify=proof,
            final_guard_status=guard,
            original_proof_sha256=hashlib.sha256((STAGE / 'guard-verify.json').read_bytes()).hexdigest(),
        )
        save()
        from prepare_original_host import check_foreign
        receipt['final_foreign_check'] = check_foreign(receipt, repo=ROOT)
        save()
        if receipt.get('development_backend_stopped_by_task'):
            service = subprocess.run(['supervisorctl', 'status', 'backend'],
                                     capture_output=True, text=True)
            assert service.returncode == 3 and service.stdout.split()[1] == 'STOPPED', \
                'Local Backend state changed; inspect before restore'
            subprocess.run(['supervisorctl', 'start', 'backend'],
                           capture_output=True, text=True, check=True)
            service = subprocess.run(['supervisorctl', 'status', 'backend'],
                                     capture_output=True, text=True)
            assert service.returncode == 0 and service.stdout.split()[1] == 'RUNNING'
            receipt.update(development_backend_restored=True,
                           development_backend_restored_at=now(),
                           backend_restore_required=False)
            save()
        print('SOURCE_FIX_2133_PRODUCTION_VERIFIED', json.dumps({
            key: receipt[key] for key in (
                'phase', 'source', 'deployment', 'release_id', 'verify_started_at',
                'verify_finished_at', 'verified_at', 'original_proof_sha256',
            )
        }, sort_keys=True), flush=True)
    except BaseException as error:
        receipt.update(
            phase=('verify_failed' if receipt.get('phase') != 'production_verified'
                   else 'production_verified_local_restore_pending'),
            verify_error=str(error)[:2000], verify_failure_at=now(),
        )
        save()
        traceback.print_exc()
        raise


if __name__ == '__main__':
    def interrupted(_signum, _frame):
        raise KeyboardInterrupt('Verification wrapper interrupted')
    signal.signal(signal.SIGTERM, interrupted)
    main()
