"""Run the immutable reviewed PR2086 launcher; never bypass its original gates."""
import hashlib
import json
import subprocess
import sys
import urllib.request
from pathlib import Path

APP = Path('/app')
LAUNCHER_URL = 'https://raw.githubusercontent.com/AMASI-SA/AMASI-SA/ec9c28cf7cd0829cb5f394ccb343f368a394b879/docs/operations/TABBY-DASHBOARD-FEES-20261122/RUN-HOST-DEPLOY-2086.sh'
LAUNCHER_SHA256 = 'e02d53474616d64fc42f565f4889493af795421599976006295fd5e76539be8b'
LAUNCHER_BYTES = 5532
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
