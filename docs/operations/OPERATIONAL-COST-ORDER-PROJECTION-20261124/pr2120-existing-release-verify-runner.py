"""Capture one authorized original verification of the existing PR2120 lease."""
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import os
import subprocess
import sys

STAGE = Path(sys.argv[1])
ROOT = Path('/app')
LEASE = ROOT / '.git/mezan-production-release.lock/lease.json'
GUARD = ROOT / 'scripts/production_release_guard.py'
EXPECTED = {
    'actor': 'chatgpt-inventory-cabinet-archive-20261124-pr2120',
    'source_git_sha': 'b236f51bb6c52e6f5d14a136db92f7474e4f5873',
    'source_base_git_sha': 'c6d05c7814e27e2f22a8c82b5bfe4cf177c17fbe',
    'deployment_git_sha': 'f199a471f8bc6e05b475bb9b65a12107cbdc946b',
    'release_id': 'rg5-6cfb0e514bb3c65c229a9d224c329f24136bd0ce483bed1af3b74b05e848ca42',
    'protocol_version': 5,
}
GUARD_SHA256 = '4e56f1f60f5f79931658b8f95a0aeb2269471025ca84f7e5b651cd284806349a'
LEASE_SHA256 = '3fbb8fa9e9be4fad154b15dbe9df88162da5ff6c6455641f3d807c4a833b7061'
ENV = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')

def now():
    return datetime.now(timezone.utc).isoformat()

receipt = {
    'started_at': now(),
    'status': 'RUNNING',
    'command': ['python', 'scripts/production_release_guard.py', 'verify', '--url', 'https://mezansalla.com'],
    'expected': EXPECTED,
    'new_publish': False,
    'prepare': False,
    'abort': False,
    'manual_lease_clear': False,
    'cabinet_mutation': False,
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

save()
try:
    raw = LEASE.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == LEASE_SHA256
    assert raw == (STAGE / 'original-lease-before.json').read_bytes()
    lease_data = json.loads(raw)
    assert all(lease_data.get(key) == value for key, value in EXPECTED.items())
    assert hashlib.sha256(GUARD.read_bytes()).hexdigest() == GUARD_SHA256
    code = capture(receipt['command'], 'original-verify', 3600)
    if code != 0:
        receipt['status'] = 'BLOCKED_ORIGINAL_VERIFY'
    else:
        verified = json.loads((STAGE / 'original-verify.stdout').read_text())
        assert verified.get('verified') is True and verified.get('checks') == 3
        assert all(verified.get(key) == value for key, value in EXPECTED.items() if key != 'actor')
        assert verified.get('git_sha') == EXPECTED['source_git_sha']
        assert verified.get('url') == 'https://mezansalla.com'
        assert verified.get('frontend_artifact_tree_sha256') == '7bfb3895de006c8ba651b0a64b4c0c3522c1117f733f57a8d9772531b5b76a6b'
        assert len(verified.get('frontend_checked_requests', [])) == 770
        receipt['verification'] = {key: verified.get(key) for key in ('verified', 'checks', 'source_git_sha', 'source_base_git_sha', 'deployment_git_sha', 'release_id', 'protocol_version', 'boot_started_at', 'frontend_artifact_tree_sha256')}
        receipt['frontend_checked_requests_count'] = len(verified['frontend_checked_requests'])
        code = capture(['python', 'scripts/production_release_guard.py', 'status'], 'original-status', 45)
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
