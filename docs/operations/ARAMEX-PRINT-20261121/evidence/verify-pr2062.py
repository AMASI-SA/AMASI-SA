"""Run the unchanged original three-probe guard; preserve its raw output."""
import argparse
import datetime
import gzip
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path('/app')
SOURCE = '23e9514b9e8a33bffb2363bb1d3afd792355ab2c'
MERGE = '9b4fb716b430c32073b5ea7abaeaff62646d9633'
RELEASE = 'rg5-3ec8529aa0398fbe08b591ca47b175f1fd41af183c6fdae0fc21da4c223c618c'
ACTOR = 'codex-aramex-pr2062-authorized'
ENV = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def status():
    p = subprocess.run(['python', '-B', 'scripts/production_release_guard.py', 'status'],
                       cwd=ROOT, env=ENV, capture_output=True, text=True, timeout=60, check=True)
    return json.loads(p.stdout)


def main(attempt):
    lease = status()
    assert lease.get('active') is True and lease.get('actor') == ACTOR
    assert lease.get('source_git_sha') == SOURCE and lease.get('deployment_git_sha') == MERGE
    assert lease.get('release_id') == RELEASE and lease.get('protocol_version') == 5
    base = ROOT / '.release-preserved/aramex-pr2062-9b4fb716-recovery'
    assert base.is_dir() and not base.is_symlink()
    stage = base / ('verify-attempt-' + str(attempt))
    assert not stage.exists(), 'Inspect the prior attempt; never start a duplicate guard'
    stage.mkdir()
    progress = {'source': SOURCE, 'deployment': MERGE, 'release': RELEASE,
                'actor': ACTOR, 'attempt': attempt, 'wrapper_pid': os.getpid(),
                'started_at_utc': now(), 'phase': 'preflight', 'verified': False}
    progress_path = stage / 'progress.json'
    progress_path.write_text(json.dumps(progress, indent=2) + '\n')
    started = time.monotonic()
    result = dict(progress)
    try:
        sys.path.insert(0, str(ROOT))
        from scripts.production_release_guard import _health_payload
        release = _health_payload('https://mezansalla.com').get('release') or {}
        result['health_before'] = {key: release.get(key) for key in (
            'source_git_sha', 'release_id', 'boot_started_at', 'verified_identity_available',
            'backend_runtime_source_verified', 'release_control_source_bound',
            'critical_file_hashes_match', 'frontend_build_verified')}
        assert release.get('source_git_sha') == SOURCE and release.get('release_id') == RELEASE
        assert all(release.get(key) is True for key in (
            'verified_identity_available', 'backend_runtime_source_verified',
            'release_control_source_bound', 'critical_file_hashes_match', 'frontend_build_verified'))
        progress.update(phase='original_guard_running', health_before=result['health_before'])
        progress_path.write_text(json.dumps(progress, indent=2) + '\n')
        raw_path = stage / 'original-verify.stdout.json'
        with raw_path.open('x') as out, (stage / 'original-verify.stderr.log').open('x') as err:
            command = ['python', '-B', 'scripts/production_release_guard.py',
                       'verify', '--url', 'https://mezansalla.com']
            p = subprocess.run(command, cwd=ROOT, env=ENV, stdout=out, stderr=err, timeout=3600)
        result['original_guard_exit'] = p.returncode
        raw = raw_path.read_bytes()
        result['raw_bytes'] = len(raw)
        result['raw_sha256'] = hashlib.sha256(raw).hexdigest()
        if p.returncode == 0:
            verified = json.loads(raw)
            assert verified.get('verified') is True and verified.get('checks') == 3
            assert verified.get('source_git_sha') == SOURCE
            assert verified.get('deployment_git_sha') == MERGE and verified.get('release_id') == RELEASE
            result.update(verified=True, checks=verified['checks'],
                          frontend_checked_requests=verified['frontend_checked_requests'],
                          boot_started_at=verified['boot_started_at'])
            packed = gzip.compress(raw, mtime=0)
            (stage / 'original-verify.stdout.json.gz').write_bytes(packed)
            result['compressed_bytes'] = len(packed)
        after = status()
        result['guard_status_after'] = {key: after.get(key) for key in (
            'active', 'actor', 'source_git_sha', 'deployment_git_sha', 'release_id')}
        assert p.returncode == 0 and result.get('verified') is True
        assert after.get('active') is False, 'Preserve any new owner lease; inspect before acting'
    except Exception as exc:
        result['error_type'] = type(exc).__name__
        result['error'] = str(exc)[:250]
    finally:
        result.update(phase='finished', finished_at_utc=now(),
                      elapsed_seconds=round(time.monotonic() - started, 3))
        (stage / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
        print('ARAMEX_2062_VERIFY_RESULT=' + json.dumps(result), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--attempt', type=int, choices=range(1, 11), default=1)
    main(parser.parse_args().attempt)
