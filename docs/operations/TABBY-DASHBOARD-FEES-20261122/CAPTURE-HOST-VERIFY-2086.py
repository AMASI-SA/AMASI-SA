"""Run only after the new PR2086 Deployment Succeeded; retain full verifier output."""
import hashlib
import json
import os
import stat
import subprocess
from pathlib import Path

APP = Path('/app')
WORK = Path('/tmp/mezan-pr2086-deploy-y1q5mxhn')
VERIFY = WORK / 'verify-after-success.sh'
EXPECTED_SHA256 = '4210c4c9a2e6d7cbc418154abb354c967dfc8fa44d4bd30d4bdb93d631ddc043'

if Path.cwd() != APP or APP.is_symlink() or APP.resolve() != APP:
    raise SystemExit('STOP: original real /app working directory required')
mode = WORK.lstat()
if not stat.S_ISDIR(mode.st_mode) or mode.st_uid != os.geteuid() or mode.st_mode & 0o077:
    raise SystemExit('STOP: original private owned release directory required')
if VERIFY.is_symlink() or not VERIFY.is_file() or hashlib.sha256(VERIFY.read_bytes()).hexdigest() != EXPECTED_SHA256:
    raise SystemExit('STOP: immutable original verification wrapper mismatch')

stdout = WORK / 'verify.stdout.json'
stderr = WORK / 'verify.stderr.txt'
flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
with os.fdopen(os.open(stdout, flags, 0o400), 'wb') as out:
    with os.fdopen(os.open(stderr, flags, 0o400), 'wb') as err:
        result = subprocess.run(['bash', str(VERIFY)], cwd=APP, stdin=subprocess.DEVNULL, stdout=out, stderr=err)

print('ORIGINAL_VERIFY_EXIT', result.returncode, flush=True)
for path in (stdout, stderr):
    value = path.read_bytes()
    print('RETAINED', json.dumps({'path': str(path), 'bytes': len(value), 'sha256': hashlib.sha256(value).hexdigest()}), flush=True)
if result.returncode:
    print(stderr.read_text(errors='replace')[-6000:], flush=True)
    raise SystemExit(result.returncode)

text = stdout.read_text()
decoder = json.JSONDecoder()
values = []
while text.strip():
    text = text.lstrip()
    value, consumed = decoder.raw_decode(text)
    values.append(value)
    text = text[consumed:]
if len(values) != 2 or values[-1] != {'active': False}:
    raise SystemExit('STOP: original verification output needs inspection; no retry attempted')
keys = ('verified', 'checks', 'url', 'deployment_git_sha', 'source_git_sha', 'source_base_git_sha', 'git_sha', 'release_id', 'protocol_version', 'frontend_artifact_tree_sha256')
print('ORIGINAL_VERIFY_RESULT', json.dumps({key: values[0][key] for key in keys if key in values[0]}, sort_keys=True), flush=True)
print('FINAL_GUARD', json.dumps(values[-1], sort_keys=True), flush=True)
