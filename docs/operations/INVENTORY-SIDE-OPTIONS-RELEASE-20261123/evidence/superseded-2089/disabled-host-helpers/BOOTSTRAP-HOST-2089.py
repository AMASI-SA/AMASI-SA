"""Run the immutable reviewed PR2089 launcher; never bypass its original gates."""
import hashlib
import json
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

APP = Path('/app')
LAUNCHER_URL = None
LAUNCHER_SHA256 = None
LAUNCHER_BYTES = None
if (
    not isinstance(LAUNCHER_URL, str)
    or re.fullmatch(
        r'https://raw\.githubusercontent\.com/AMASI-SA/AMASI-SA/[0-9a-f]{40}/'
        r'docs/operations/INVENTORY-SIDE-OPTIONS-RELEASE-20261123/RUN-HOST-DEPLOY-2089\.sh',
        LAUNCHER_URL,
    ) is None
    or not isinstance(LAUNCHER_SHA256, str)
    or re.fullmatch(r'[0-9a-f]{64}', LAUNCHER_SHA256) is None
    or type(LAUNCHER_BYTES) is not int
    or not 0 < LAUNCHER_BYTES < 20000
):
    raise SystemExit('STOP: disabled release bundle; immutable launcher pins are not configured')
if Path.cwd() != APP or APP.is_symlink() or APP.resolve() != APP:
    raise SystemExit('STOP: original real /app working directory required')
result = subprocess.run(
    [sys.executable, '-B', 'scripts/production_release_guard.py', 'status'],
    cwd=APP, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=15,
)
if result.returncode or json.loads(result.stdout) != {'active': False}:
    raise SystemExit('STOP: original guard unavailable or not inactive; no launcher action attempted')
with urllib.request.urlopen(LAUNCHER_URL, timeout=30) as response:
    payload = response.read(20000)
if len(payload) != LAUNCHER_BYTES or hashlib.sha256(payload).hexdigest() != LAUNCHER_SHA256:
    raise SystemExit('STOP: immutable launcher bytes/hash mismatch; no launcher action attempted')
print('PINNED_LAUNCHER_VERIFIED', LAUNCHER_SHA256, flush=True)
result = subprocess.run(['bash', '-c', payload.decode('utf-8')], cwd=APP, stdin=subprocess.DEVNULL)
print('RELEASE_LAUNCHER_EXIT', result.returncode, flush=True)
raise SystemExit(result.returncode)
