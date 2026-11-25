"""Final lease-bound capture of one original PR2123 verification after publication."""
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import os
import re
import stat
import subprocess
import sys

ROOT = Path('/app')
LEASE = ROOT / '.git/mezan-production-release.lock/lease.json'
GUARD = ROOT / 'scripts/production_release_guard.py'
EXPECTED = {
    'actor': 'chatgpt-operational-cost-order-projection-20261124-pr2123',
    'source_git_sha': 'e0726036bbfaa524d55f80dfea9f2a8f9facdbfe',
    'source_base_git_sha': 'f199a471f8bc6e05b475bb9b65a12107cbdc946b',
    'deployment_git_sha': '6323017fbf9ba160c536a94f408507435786412a',
    'release_id': 'rg5-d5df5e9536258c2d3b76d750715bafae8fa9b6b76f6971899bd7f8c6e0e54328',
    'protocol_version': 5,
}
INTENT_SHA = 'c71a0ba71e36a2306a0c4bce1822f39073e6984f'
GUARD_SHA256 = '4e56f1f60f5f79931658b8f95a0aeb2269471025ca84f7e5b651cd284806349a'
LEASE_SHA256 = '2e6824bc9a7140620e93761efd548bf77d1a09a9425bd0ccb7600394023f7f2d'
PREPARED_AT = '2026-11-25T00:37:19.949259+00:00'

# Fail before any /app read, stage write or child process while unbound.
if re.fullmatch(r'[0-9a-f]{64}', LEASE_SHA256) is None or PREPARED_AT == 'DRAFT_UNBOUND_PREPARED_AT':
    raise SystemExit('DRAFT_BLOCKED_UNBOUND_LEASE_DIGEST_OR_PREPARE_TIME')
prepared_time = datetime.fromisoformat(PREPARED_AT.replace('Z', '+00:00'))
if prepared_time.tzinfo is None or prepared_time.utcoffset() is None:
    raise SystemExit('PREPARED_AT_REQUIRES_TIMEZONE')
if sys.flags.optimize or 'PYTHONOPTIMIZE' in os.environ:
    raise SystemExit('PYTHON_OPTIMIZATION_NOT_ALLOWED')
if len(sys.argv) != 2:
    raise SystemExit('ONE_OWNED_STAGE_REQUIRED')
STAGE = Path(sys.argv[1])
stage_info = STAGE.lstat()
if (STAGE.parent != Path('/tmp') or not STAGE.name.startswith('mezan-pr2123-verify-')
        or not stat.S_ISDIR(stage_info.st_mode) or stage_info.st_uid != os.geteuid()
        or stat.S_IMODE(stage_info.st_mode) != 0o700 or STAGE.resolve() != STAGE):
    raise SystemExit('UNSAFE_OWNED_STAGE')
os.umask(0o077)
ENV = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
for name in ('PYTHONOPTIMIZE', 'PYTHONHOME', 'PYTHONPATH'):
    ENV.pop(name, None)


def now():
    return datetime.now(timezone.utc).isoformat()


receipt = {
    'started_at': now(),
    'status': 'RUNNING',
    'command': [sys.executable, '-B', 'scripts/production_release_guard.py', 'verify', '--url', 'https://mezansalla.com'],
    'expected': EXPECTED,
    'intent_sha': INTENT_SHA,
    'expected_lease_sha256': LEASE_SHA256,
    'expected_prepared_at': PREPARED_AT,
    'new_publish': False,
    'prepare': False,
    'abort': False,
    'manual_lease_clear': False,
    'automatic_retry': False,
}


def save():
    (STAGE / 'receipt.json').write_text(json.dumps(receipt, indent=2), encoding='utf-8')


def capture(argv, stem, timeout):
    receipt[stem + '_started_at'] = now()
    save()
    with (STAGE / (stem + '.stdout')).open('xb') as out, (STAGE / (stem + '.stderr')).open('xb') as err:
        result = subprocess.run(argv, cwd=ROOT, env=ENV, stdin=subprocess.DEVNULL, stdout=out, stderr=err, timeout=timeout)
    receipt[stem + '_finished_at'] = now()
    receipt[stem + '_exit_code'] = result.returncode
    for extension in ('stdout', 'stderr'):
        name = stem + '.' + extension
        data = (STAGE / name).read_bytes()
        receipt[name] = {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
    save()
    return result.returncode


# Do not overwrite an earlier invocation's receipt in this owned stage.
with (STAGE / 'receipt.json').open('x', encoding='utf-8') as stream:
    stream.write(json.dumps(receipt, indent=2))
try:
    assert not LEASE.is_symlink() and not LEASE.parent.is_symlink()
    assert not GUARD.is_symlink()
    raw = LEASE.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == LEASE_SHA256
    assert raw == (STAGE / 'original-lease-before.json').read_bytes()
    lease_data = json.loads(raw)
    assert all(lease_data.get(key) == value for key, value in EXPECTED.items())
    assert lease_data.get('prepared_at') == PREPARED_AT
    assert hashlib.sha256(GUARD.read_bytes()).hexdigest() == GUARD_SHA256
    frontend = lease_data['runtime_identity']['frontend_build']
    assert lease_data['frontend_build'] == frontend
    expected_artifact_tree = frontend['artifact_tree_sha256']
    assert re.fullmatch(r'[0-9a-f]{64}', expected_artifact_tree) is not None
    request_paths = {'/build-meta.json', '/', '/snapchat-accounts'} | {
        '/' + row['path'] for row in frontend['public_files']}
    # Exact original _verify_public_frontend request-count rule, from the pinned lease.
    expected_request_count = sum(4 if path == '/build-meta.json' else 2 for path in request_paths)
    receipt['expected_frontend_artifact_tree_sha256'] = expected_artifact_tree
    receipt['expected_frontend_checked_requests_count'] = expected_request_count
    code = capture(receipt['command'], 'original-verify', 3600)
    if code != 0:
        receipt['status'] = 'BLOCKED_ORIGINAL_VERIFY'
    else:
        verified = json.loads((STAGE / 'original-verify.stdout').read_text())
        assert verified.get('verified') is True and verified.get('checks') == 3
        assert all(verified.get(key) == value for key, value in EXPECTED.items() if key != 'actor')
        assert verified.get('git_sha') == EXPECTED['source_git_sha']
        assert verified.get('url') == 'https://mezansalla.com'
        assert verified.get('frontend_artifact_tree_sha256') == expected_artifact_tree
        assert len(verified.get('frontend_checked_requests', [])) == expected_request_count
        receipt['verification'] = {key: verified.get(key) for key in ('verified', 'checks', 'source_git_sha', 'source_base_git_sha', 'deployment_git_sha', 'release_id', 'protocol_version', 'boot_started_at', 'frontend_artifact_tree_sha256')}
        receipt['frontend_checked_requests_count'] = len(verified['frontend_checked_requests'])
        code = capture([sys.executable, '-B', 'scripts/production_release_guard.py', 'status'], 'original-status', 45)
        assert code == 0
        state = json.loads((STAGE / 'original-status.stdout').read_text())
        receipt['original_status'] = state
        assert state.get('active') is False and not state.get('incomplete_lease_directory')
        assert not LEASE.parent.exists()
        receipt['status'] = 'PASS'
except Exception as exc:
    receipt['status'] = 'BLOCKED_WRAPPER'
    receipt['error_type'] = type(exc).__name__
    receipt['error_message'] = str(exc)
finally:
    receipt['finished_at'] = now()
    save()
raise SystemExit(0 if receipt['status'] == 'PASS' else 2)
