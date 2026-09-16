#!/usr/bin/env python3
"""Explicit password-only adapter for one user-authorized Preview container.

This is NOT a Production release or an environment-variable MFA switch.
The adapter lives outside /app, refuses every other host, checks pinned auth
sources, uses a separate Preview signing key, and preserves account/password,
revocation, role and login-attempt enforcement. Base release health describes
the unchanged /app package; /api/preview-auth-policy identifies this adapter.
"""
import hashlib
import importlib
import json
import os
from pathlib import Path
import secrets
import socket
import stat
import subprocess
import sys
from urllib.parse import urlsplit

ROOT = Path('/opt/mezan-preview-runtime-20260916')
BACKEND = Path('/app/backend')
HOST = 'agent-env-f5e6b93a-68a2-4155-84ad-e55b4fa936d3'
ORIGIN = 'https://salla-analytics.preview.emergentagent.com'
KEY = ROOT / 'preview-session-signing.secret'
SCRIPT = ROOT / 'preview_password_runtime.py'
POLICY_PATH = '/api/preview-auth-policy'
HASHES = {
    'auth.py': '5d2ad7a3e5382e8bdd091996e4f135e70a0e3602b0e214344427f73535120f71',
    'mfa_security.py': '9dc25dfc20c08093d0becfdf7ed6a66748824d7e1ba9ce00c41e9172b0eaefda',
    'email_otp_policy.py': '489a3c40fe081774bfc9c31fd95417e80258cca1515a0d9acb0c55d0293c9494',
    'email_otp_security.py': '4c7730e41bd65b7a65f3a1605ba0fbc3aa902e29ca591f548c35fb20399f6108',
    'passkey_security.py': 'c9008b89dc599f9e48e04ebd745716cb7ce3350a4aa1e3eb8ed0e45b7f391f85',
}


def require(ok, message):
    if not ok:
        raise RuntimeError(message)


def verify_boundary():
    require(socket.gethostname() == HOST, 'Password-only policy is forbidden outside the authorized Preview host')
    require(Path(__file__).resolve() == SCRIPT, 'Preview adapter must run from its private runtime directory')
    require(ROOT.is_dir() and stat.S_IMODE(ROOT.stat().st_mode) == 0o700,
            'Expected private Preview runtime directory')
    meta = json.loads((ROOT / 'frontend/build/preview-meta.json').read_text())
    require(meta.get('environment') == 'preview' and meta.get('api_origin') == ORIGIN,
            'Preview frontend origin is not verified')
    from dotenv import dotenv_values
    values = dict(dotenv_values(BACKEND / '.env'))
    values.update(os.environ)
    mongo = values.get('MONGO_URL') or values.get('MONGODB_URI') or ''
    require(urlsplit(mongo).hostname in {'localhost', '127.0.0.1', '::1'},
            'Password-only Preview requires its local database')
    for name, digest in HASHES.items():
        require(hashlib.sha256((BACKEND / name).read_bytes()).hexdigest() == digest,
                'Auth source changed; Preview policy must be reviewed before restart')


async def no_second_factor(db, user):
    return False


def second_factor_disabled():
    return False


async def skip_second_factor_install(app, db):
    app.state.mezan_preview_password_only = True


def apply_password_only_policy():
    verify_boundary()
    auth = importlib.import_module('auth')
    mfa = importlib.import_module('mfa_security')
    policy = importlib.import_module('email_otp_policy')
    email = importlib.import_module('email_otp_security')
    passkey = importlib.import_module('passkey_security')
    # Change only second-factor policy. Token creation still truthfully emits
    # mfa:false; no session or database MFA enrollment is fabricated.
    auth.PRIVILEGED_MFA_ROLES = frozenset()
    mfa.PRIVILEGED_ROLES = frozenset()
    policy.requires_email_otp = no_second_factor
    policy.email_otp_enabled = second_factor_disabled
    email.requires_email_otp = no_second_factor
    email.email_otp_enabled = second_factor_disabled
    mfa.install_mfa_security = skip_second_factor_install
    email.install_email_otp_security = skip_second_factor_install
    passkey.install_passkey_security = skip_second_factor_install


class PreviewHostBoundary:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            await self.app(scope, receive, send)
            return
        from starlette.responses import JSONResponse
        headers = dict(scope.get('headers', []))
        host = headers.get(b'host', b'').decode('ascii', errors='ignore').lower()
        hostname = urlsplit('http://' + host).hostname
        allowed = {urlsplit(ORIGIN).hostname, 'salla-analytics.cluster-12.preview.emergentcf.cloud',
                   '127.0.0.1', 'localhost', '::1'}
        origin = headers.get(b'origin', b'').decode('ascii', errors='ignore')
        if hostname not in allowed or (origin and origin != ORIGIN):
            await JSONResponse({'detail': 'Preview origin required'}, status_code=403)(scope, receive, send)
            return
        if scope.get('path') == POLICY_PATH:
            await JSONResponse({'environment': 'preview', 'authentication': 'email_password',
                                'second_factor_required': False, 'session_signing': 'preview_only',
                                'base_release_unchanged': True,
                                'adapter_sha256': hashlib.sha256(SCRIPT.read_bytes()).hexdigest()},
                               headers={'Cache-Control': 'no-store'})(scope, receive, send)
            return
        await self.app(scope, receive, send)


def create_app():
    verify_boundary()
    from dotenv import load_dotenv
    load_dotenv(BACKEND / '.env')
    require(KEY.is_file() and not KEY.is_symlink(), 'Preview session key is missing')
    require(KEY.stat().st_uid == os.getuid() and stat.S_IMODE(KEY.stat().st_mode) == 0o600,
            'Preview session key must be owner-only')
    key = KEY.read_text().strip()
    require(len(key) >= 64 and key != os.environ.get('JWT_SECRET'), 'Independent Preview session key required')
    os.environ['JWT_SECRET'] = key
    os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
    sys.path.insert(0, str(BACKEND))
    apply_password_only_policy()
    server = importlib.import_module('server')
    print('PREVIEW ONLY: email/password authentication; independent sessions; password, roles and attempt guards preserved.', flush=True)
    return PreviewHostBoundary(server.app)


def activate():
    import preview_mfa_setup as service
    verify_boundary()
    original = service.preflight()
    require(not KEY.exists(), 'Prior Preview policy state exists; inspect before activation')
    before = service.SECTION.search(original).group()
    command = '/root/.venv/bin/python -B ' + str(SCRIPT) + ' serve'
    after = before.replace('command=' + service.OLD, 'command=' + command)
    require(before != after, 'Expected one exact backend command')
    updated = service.replace_section(original, before, after)
    service.private_write(ROOT / 'password-backend-before.txt', before)
    service.private_write(ROOT / 'password-backend-after.txt', after)
    service.private_write(KEY, secrets.token_urlsafe(64))
    require(service.CONF.read_text() == original, 'Supervisor changed during activation')
    service.replace_conf(updated)
    subprocess.run(['supervisorctl', 'reread'], check=True)
    subprocess.run(['supervisorctl', 'update', 'backend'], check=True)
    print('Preview-only password policy activated; runtime/login verification pending.')


def rollback():
    import preview_mfa_setup as service
    require(socket.gethostname() == HOST, 'Preview host only')
    before = (ROOT / 'password-backend-before.txt').read_text()
    after = (ROOT / 'password-backend-after.txt').read_text()
    service.replace_conf(service.replace_section(service.CONF.read_text(), after, before))
    subprocess.run(['supervisorctl', 'reread'], check=True)
    subprocess.run(['supervisorctl', 'update', 'backend'], check=True)
    print('Previous Preview authentication restored. Private Preview signing key retained.')


if __name__ == '__main__':
    require(len(sys.argv) == 2, 'Use check, activate, serve, or rollback')
    action = sys.argv[1]
    if action == 'serve':
        import uvicorn
        # Import the base server inside Uvicorn's running event loop. Creating
        # it before uvicorn.run binds Motor to a different, inactive loop.
        uvicorn.run('preview_password_runtime:create_app', factory=True,
                    host='0.0.0.0', port=8001, workers=1)
    elif action == 'activate':
        activate()
    elif action == 'rollback':
        rollback()
    elif action == 'check':
        verify_boundary()
        import preview_mfa_setup as service
        service.preflight()
        print('Preview password-only preflight PASS; no service change.')
    else:
        raise RuntimeError('Unknown action')
