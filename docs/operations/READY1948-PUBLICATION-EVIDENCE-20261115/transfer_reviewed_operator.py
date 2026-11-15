"""Transfer reviewed bytes only; never move source or execute the operator."""
if not __debug__:
    raise RuntimeError('optimized Python is forbidden')

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path('/app')
BASE = '742b1032113730961597ea413665409d9c213f7b'
EVIDENCE_COMMIT = 'a15ff05d2239ff59bca38d41dba86ec36b70184f'
EVIDENCE_BRANCH = 'codex/ready1948-publication-evidence-20261115'
PREFIX = 'docs/operations/READY1948-PUBLICATION-EVIDENCE-20261115/'
DEST = Path('/tmp/mezan-ready1948-transfer-20261115T1948')
FILES = {
    'original_release_1948.py': 'cf120e40b3431cfacb34459d424f851481d85ee2c55a33a3c241849ca20e68e5',
    'expected-package-boundary.json': '30899db8bab016f7a4fba67183f864a5da4ee71097b3829c2603a4c9419733b2',
}
PINS = {
    'scripts/production_release_guard.py': '4e56f1f60f5f79931658b8f95a0aeb2269471025ca84f7e5b651cd284806349a',
    'backend/frontend_build_identity.py': '7896dfa2c9b4fea8c426b47c5b75e331380c0a4a3f947028aa4f5afa101f9d87',
    'backend/release_protocol_v5.py': '1961ac18a5363ec425ceec4f98f96f611d378b09e19380d4756bd6776987d8ef',
}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def git(*args):
    return subprocess.check_output(['git', *args], cwd=ROOT, stderr=subprocess.PIPE)


assert not os.path.lexists(DEST), 'transfer directory already exists'
for path, expected in PINS.items():
    assert not (ROOT / path).is_symlink() and digest((ROOT / path).read_bytes()) == expected, 'control code differs'
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / 'scripts'))
import production_release_guard as guard

assert guard.REPO_ROOT == ROOT
with guard._release_operation_lock():
    assert not os.path.lexists(guard.LEASE_PATH) and not os.path.lexists(guard.LEASE_DIR), 'release lease exists'
    assert git('branch', '--show-current').decode().strip() == 'hotfix/prod-snap-meta-final'
    assert git('rev-parse', 'HEAD').decode().strip() == BASE
    assert not git('status', '--porcelain', '--untracked-files=no').strip(), 'tracked source is dirty'
    origin = git('remote', 'get-url', 'origin').decode().strip()
    assert origin in {'https://github.com/AMASI-SA/AMASI-SA.git', 'https://github.com/AMASI-SA/AMASI-SA', 'git@github.com:AMASI-SA/AMASI-SA.git'}, 'unexpected origin'
    fetched = subprocess.run(['git', 'fetch', 'origin', 'refs/heads/' + EVIDENCE_BRANCH], cwd=ROOT,
                             stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert fetched.returncode == 0, 'evidence fetch failed'
    assert git('rev-parse', 'FETCH_HEAD').decode().strip() == EVIDENCE_COMMIT, 'evidence ref moved'
    payloads = {name: git('show', EVIDENCE_COMMIT + ':' + PREFIX + name) for name in FILES}
    for name, data in payloads.items():
        assert digest(data) == FILES[name], 'reviewed payload differs: ' + name
    assert git('rev-parse', 'HEAD').decode().strip() == BASE
    assert not git('status', '--porcelain', '--untracked-files=no').strip()
    assert not os.path.lexists(guard.LEASE_PATH) and not os.path.lexists(guard.LEASE_DIR)
    DEST.mkdir(mode=0o700, exist_ok=False)
    for name, data in payloads.items():
        with (DEST / name).open('xb') as handle:
            handle.write(data)
        assert digest((DEST / name).read_bytes()) == FILES[name]
    receipt = {'transferred': True, 'evidence_commit': EVIDENCE_COMMIT,
               'source_head_unchanged': BASE, 'tracked_clean': True, 'active_lease': False,
               'files': FILES, 'destination': str(DEST), 'operator_executed': False}
    (DEST / 'transfer-receipt.json').write_text(json.dumps(receipt, indent=2) + '\n')
    print(json.dumps(receipt, sort_keys=True), flush=True)
