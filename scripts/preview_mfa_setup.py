#!/usr/bin/env python3
"""User-operated first-enrollment secret setup for the authorized Preview host.

No secret is printed, put in argv, committed, or written under /app.
Configure requires an interactive TTY; existing secrets are never replaced.
"""
import configparser
import getpass
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import socket
import stat
import subprocess
import sys
import tempfile
import time
import urllib.request
from urllib.parse import urlsplit

ROOT = Path('/opt/mezan-preview-runtime-20260916')
SECRET = ROOT / 'mfa-bootstrap.secret'
SCRIPT = ROOT / 'preview_mfa_setup.py'
CONF = Path('/etc/supervisor/conf.d/supervisord.conf')
HOST = 'agent-env-f5e6b93a-68a2-4155-84ad-e55b4fa936d3'
ORIGIN = 'https://salla-analytics.preview.emergentagent.com'
HEAD = '9f8b16998f62cbdb08dc9361bc2e443223d27d17'
MFA_HASH = '9dc25dfc20c08093d0becfdf7ed6a66748824d7e1ba9ce00c41e9172b0eaefda'
ARGV = ['/root/.venv/bin/uvicorn', 'server:app', '--host', '0.0.0.0',
        '--port', '8001', '--workers', '1', '--reload']
OLD = '/usr/bin/env PYTHONDONTWRITEBYTECODE=1 ' + ' '.join(ARGV)
NEW = '/root/.venv/bin/python -B ' + str(SCRIPT) + ' run'
SECTION = re.compile(r'(?ms)^\[program:backend\][^\n]*\n.*?(?=^\[|\Z)')
FLAGS = ['verified_identity_available', 'critical_file_hashes_match',
         'frontend_build_verified', 'backend_runtime_source_verified',
         'release_control_source_bound']


def require(ok, message):
    if not ok:
        raise RuntimeError(message)


def private_write(path, content):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as target:
        target.write(content)


def replace_section(text, expected, replacement):
    matches = list(SECTION.finditer(text))
    require(len(matches) == 1 and matches[0].group() == expected,
            'Backend configuration changed; inspect before continuing')
    match = matches[0]
    return text[:match.start()] + replacement + text[match.end():]


def replace_conf(text):
    mode = stat.S_IMODE(CONF.stat().st_mode)
    fd, name = tempfile.mkstemp(prefix='.preview-mfa-', dir=CONF.parent)
    try:
        with os.fdopen(fd, 'w') as target:
            target.write(text)
            target.flush()
            os.fsync(target.fileno())
        os.chmod(name, mode)
        os.replace(name, CONF)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def json_get(url):
    with urllib.request.urlopen(url, timeout=5) as response:
        require(response.status == 200, 'Unexpected service HTTP status')
        return json.load(response)


def process_env():
    pid = int(subprocess.check_output(['supervisorctl', 'pid', 'backend']))
    require(pid > 0, 'Preview backend must be running')
    return dict(item.split('=', 1) for item in
                Path(f'/proc/{pid}/environ').read_text().split('\0') if '=' in item)


def preflight():
    from dotenv import dotenv_values
    require(socket.gethostname() == HOST, 'This script is only for the verified Preview host')
    require(ROOT.is_dir() and stat.S_IMODE(ROOT.stat().st_mode) == 0o700,
            'Expected private Preview runtime directory')
    require(Path(__file__).resolve() == SCRIPT, 'Install this script in the Preview runtime first')
    meta = json_get('http://127.0.0.1:3000/preview-meta.json')
    require(ORIGIN in meta.values(), 'Expected isolated Preview frontend origin')
    require(subprocess.check_output(['git', '-C', '/app', 'rev-parse', 'HEAD'], text=True).strip() == HEAD,
            'Shared source changed; inspect before setup')
    require(hashlib.sha256(Path('/app/backend/mfa_security.py').read_bytes()).hexdigest() == MFA_HASH,
            'MFA source changed; inspect before setup')
    lease = json.loads(subprocess.check_output(
        ['/root/.venv/bin/python', '-B', '/app/scripts/production_release_guard.py', 'status'],
        cwd='/app', text=True))
    require(lease.get('active') is False, 'Another task owns an active release; preserve its runtime')
    values = dict(dotenv_values('/app/backend/.env'))
    values.update(process_env())
    require(not (values.get('MFA_BOOTSTRAP_CODE') or '').strip(),
            'Existing bootstrap configuration must not be replaced')
    require(bool(values.get('JWT_SECRET')), 'Existing JWT configuration is required')
    mongo = values.get('MONGO_URL') or values.get('MONGODB_URI') or ''
    require(urlsplit(mongo).hostname in {'localhost', '127.0.0.1', '::1'},
            'Expected isolated local Preview database')
    cfg = configparser.RawConfigParser()
    original = CONF.read_text()
    cfg.read_string(original)
    require(cfg.get('program:backend', 'command') == OLD, 'Unexpected backend command')
    require(cfg.get('program:backend', 'directory') == '/app/backend', 'Unexpected backend directory')
    for path in [SECRET, ROOT / 'mfa-backend-before.txt', ROOT / 'mfa-backend-after.txt']:
        require(not path.exists(), 'Prior setup exists; inspect instead of overwriting')
    health = json_get('http://127.0.0.1:8001/api/health')
    require(all(health.get('release', {}).get(key) is True for key in FLAGS),
            'Release verification must pass before setup')
    return original


def configure():
    require(sys.stdin.isatty(), 'Secret setup requires an interactive terminal')
    original = preflight()
    print('Preview only: choose a NEW bootstrap secret (12+ English letters, digits or symbols).', flush=True)
    print('Input is hidden. Keep this secret for the first MFA enrollment.', flush=True)
    secret = getpass.getpass('New Preview bootstrap secret: ')
    confirmation = getpass.getpass('Repeat the secret: ')
    require(secret.isascii() and secret.isprintable(), 'Use English letters, digits or symbols')
    require(len(secret.strip()) >= 12 and secret == secret.strip(), 'Secret must contain at least 12 characters without surrounding spaces')
    require(hmac.compare_digest(secret.encode(), confirmation.encode()), 'Secrets do not match')
    require(preflight() == original, 'Runtime changed while awaiting secret entry')
    before = SECTION.search(original).group()
    after, count = re.subn(r'(?m)^command\s*=.*$', 'command=' + NEW, before)
    require(count == 1, 'Expected one backend command')
    updated = replace_section(original, before, after)
    private_write(SECRET, secret)
    private_write(ROOT / 'mfa-backend-before.txt', before)
    private_write(ROOT / 'mfa-backend-after.txt', after)
    replace_conf(updated)
    subprocess.run(['supervisorctl', 'reread'], check=True)
    subprocess.run(['supervisorctl', 'update', 'backend'], check=True)
    for _ in range(30):
        try:
            ready = json_get('http://127.0.0.1:8001/api/ready')
            if ready.get('ready') is True:
                verify()
                return
        except (OSError, ValueError):
            pass
        time.sleep(1)
    raise RuntimeError('Setup applied; service recovery is not yet verified. Inspect before retrying.')


def run():
    require(socket.gethostname() == HOST, 'Preview host only')
    require(SECRET.is_file() and not SECRET.is_symlink(), 'Missing private Preview secret')
    info = SECRET.stat()
    require(info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) == 0o600,
            'Preview secret must be owner-only')
    secret = SECRET.read_text()
    require(len(secret.strip()) >= 12, 'Invalid Preview bootstrap configuration')
    os.environ['MFA_BOOTSTRAP_CODE'] = secret
    os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
    os.execv(ARGV[0], ARGV)


def verify():
    ready = json_get('http://127.0.0.1:8001/api/ready')
    health = json_get('http://127.0.0.1:8001/api/health')
    result = {'bootstrap_configured': len(process_env().get('MFA_BOOTSTRAP_CODE', '').strip()) >= 12,
              'ready': ready.get('ready') is True,
              'release_checks': {key: health.get('release', {}).get(key) for key in FLAGS},
              'login_verified': False, 'production_changed': False}
    require(result['bootstrap_configured'] and result['ready'] and
            all(value is True for value in result['release_checks'].values()), 'Verification failed')
    private_write(ROOT / 'mfa-verification.json', json.dumps(result, indent=2) + '\n')
    print(json.dumps(result))
    print('Preview configuration verified. Continue normal password + MFA enrollment in Preview.')


def rollback():
    require(socket.gethostname() == HOST, 'Preview host only')
    before = (ROOT / 'mfa-backend-before.txt').read_text()
    after = (ROOT / 'mfa-backend-after.txt').read_text()
    replace_conf(replace_section(CONF.read_text(), after, before))
    subprocess.run(['supervisorctl', 'reread'], check=True)
    subprocess.run(['supervisorctl', 'update', 'backend'], check=True)
    print('Previous backend configuration restored; private secret retained. Original MFA setup block may return.')


if __name__ == '__main__':
    actions = {'check': preflight, 'configure': configure, 'run': run, 'rollback': rollback}
    require(len(sys.argv) == 2 and sys.argv[1] in actions, 'Use check, configure, run, or rollback')
    actions[sys.argv[1]]()
    if sys.argv[1] == 'check':
        print('Preview MFA setup preflight PASS; no configuration changed.')
