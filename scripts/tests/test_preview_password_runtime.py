"""Acceptance of the isolated Preview policy; no actual account/database calls."""
import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace

import jwt
import pytest
from fastapi import HTTPException
from starlette.responses import Response

import auth
import email_otp_policy as policy
import email_otp_security as email
import mfa_security as mfa
import passkey_security as passkey
import preview_password_runtime as preview

spec = importlib.util.spec_from_file_location(
    'existing_auth_acceptance', '/app/backend/tests/test_auth_security_replica_startup.py')
helpers = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helpers)


@pytest.fixture
def password_only(monkeypatch):
    monkeypatch.setenv('JWT_SECRET', 'preview-test-only-session-key-of-at-least-64-characters-independent')
    monkeypatch.setattr(preview, 'verify_boundary', lambda: None)
    affected = [(auth, 'PRIVILEGED_MFA_ROLES'), (mfa, 'PRIVILEGED_ROLES'),
                (policy, 'requires_email_otp'), (policy, 'email_otp_enabled'),
                (email, 'requires_email_otp'), (email, 'email_otp_enabled'),
                (mfa, 'install_mfa_security'), (email, 'install_email_otp_security'),
                (passkey, 'install_passkey_security')]
    for module, name in affected:
        monkeypatch.setattr(module, name, getattr(module, name))
    preview.apply_password_only_policy()


@pytest.mark.asyncio
@pytest.mark.parametrize('role', ['owner', 'admin', 'accountant', 'viewer'])
async def test_login_me_and_refresh_need_no_second_factor_and_keep_role(password_only, role):
    user = {'id': role + '-1', 'email': role + '@example.test', 'role': role}
    db = helpers._AuthStackDb(user)
    route = mfa.MfaSecurityMiddleware(
        email.EmailOtpSecurityMiddleware(helpers._canonical_login_route(user), db=db), db=db)
    start, body = await helpers._asgi_request(route, '/api/auth/login',
                                             {'email': user['email'], 'password': 'valid'})
    assert start['status'] == 200
    assert not any(key in body for key in ['mfa_required', 'mfa_setup_required',
                                          'mfa_bootstrap_required', 'passkey_required'])
    claims = jwt.decode(body['access_token'], os.environ['JWT_SECRET'], algorithms=['HS256'])
    assert claims['mfa'] is False
    current = await auth.get_current_user_from_db(helpers._Request(body['access_token']), db)
    assert current['role'] == role
    request = SimpleNamespace(cookies={'refresh_token': auth.create_refresh_token(user['id'])}, headers={})
    response = Response()
    assert await auth.refresh_browser_session(request, response, db) == {'ok': True}
    assert len(response.headers.getlist('set-cookie')) == 2
    with pytest.raises(jwt.InvalidSignatureError):
        jwt.decode(body['access_token'], 'unrelated-live-runtime-signing-key', algorithms=['HS256'])


@pytest.mark.asyncio
@pytest.mark.parametrize('case', ['wrong_password', 'disabled', 'inactive'])
async def test_invalid_login_still_rejected(password_only, case):
    user = {'id': 'owner-1', 'email': 'owner@example.test', 'role': 'owner'}
    if case == 'disabled':
        user['disabled'] = True
    if case == 'inactive':
        user['is_active'] = False
    with pytest.raises(HTTPException) as exc:
        await helpers._asgi_request(helpers._canonical_login_route(user), '/api/auth/login',
                                   {'email': user['email'], 'password': 'wrong' if case == 'wrong_password' else 'valid'})
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_disabled_and_revoked_sessions_stay_denied(password_only):
    token = auth.create_access_token('owner-1', 'owner@example.test')
    for extra in [{'disabled': True}, {'password_updated_at': '2099-01-01T00:00:00+00:00'}]:
        user = {'id': 'owner-1', 'email': 'owner@example.test', 'role': 'owner', **extra}
        with pytest.raises(HTTPException) as exc:
            await auth.get_current_user_from_db(helpers._Request(token), helpers._Db(user))
        assert exc.value.status_code == 401
        refresh = SimpleNamespace(cookies={'refresh_token': auth.create_refresh_token(user['id'])})
        with pytest.raises(HTTPException) as exc:
            await auth.refresh_browser_session(refresh, Response(), helpers._Db(user))
        assert exc.value.status_code == 401


def test_adapter_rejects_every_other_runtime_host(monkeypatch):
    monkeypatch.setattr(preview.socket, 'gethostname', lambda: 'production-host')
    with pytest.raises(RuntimeError, match='forbidden outside'):
        preview.verify_boundary()


@pytest.mark.asyncio
@pytest.mark.parametrize('headers,expected', [
    ([(b'host', b'mezansalla.com')], 403),
    ([(b'host', b'salla-analytics.preview.emergentagent.com'), (b'origin', b'https://mezansalla.com')], 403),
    ([(b'host', b'salla-analytics.preview.emergentagent.com'),
      (b'origin', b'https://salla-analytics.preview.emergentagent.com')], 204),
])
async def test_http_origin_boundary(headers, expected):
    async def underlying(scope, receive, send):
        await Response(status_code=204)(scope, receive, send)
    sent = []
    async def send(message):
        sent.append(message)
    async def receive():
        return {'type': 'http.request', 'body': b''}
    await preview.PreviewHostBoundary(underlying)(
        {'type': 'http', 'method': 'GET', 'path': '/api/ready', 'headers': headers}, receive, send)
    assert next(item for item in sent if item['type'] == 'http.response.start')['status'] == expected


@pytest.mark.asyncio
async def test_base_policy_still_requires_second_factor_when_adapter_is_not_applied(monkeypatch):
    monkeypatch.setenv('JWT_SECRET', 'unmodified-base-policy-test-signing-key')
    for role in ['owner', 'admin', 'viewer']:
        user = {'id': role, 'email': role + '@example.test', 'role': role}
        token = auth.create_access_token(role, user['email'])
        with pytest.raises(HTTPException) as exc:
            await auth.get_current_user_from_db(helpers._Request(token), helpers._Db(user))
        assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_attempt_guards_unchanged_when_only_second_factor_installers_are_skipped(password_only):
    import login_security
    import progressive_login_security
    assert login_security.install_login_security.__module__ == 'login_security'
    assert progressive_login_security.install_progressive_login_security.__module__ == 'progressive_login_security'
    app = SimpleNamespace(state=SimpleNamespace())
    for install in [mfa.install_mfa_security, email.install_email_otp_security, passkey.install_passkey_security]:
        await install(app, object())
    assert app.state.mezan_preview_password_only is True


@pytest.mark.asyncio
async def test_factory_binds_cookie_csrf_trust_to_preview_before_server_import(monkeypatch, tmp_path):
    import dotenv
    from browser_security import BrowserSecurityMiddleware
    key = tmp_path / 'preview-test-signing-key'
    key.write_text('isolated-preview-test-key-' + 'x' * 64)
    key.chmod(0o600)
    monkeypatch.setattr(preview, 'KEY', key)
    monkeypatch.setattr(preview, 'verify_boundary', lambda: None)
    monkeypatch.setattr(preview, 'apply_password_only_policy', lambda: None)
    monkeypatch.setattr(dotenv, 'load_dotenv', lambda *args: None)
    for name, value in [('JWT_SECRET', 'existing-base-test-key'),
                        ('FRONTEND_URL', 'https://mezansalla.com'),
                        ('CORS_ORIGINS', 'https://mezansalla.com'),
                        ('PYTHONDONTWRITEBYTECODE', '1')]:
        monkeypatch.setenv(name, value)
    async def handler(scope, receive, send):
        await Response(status_code=204)(scope, receive, send)
    def load_server(name):
        assert name == 'server'
        return SimpleNamespace(app=BrowserSecurityMiddleware(
            handler, trusted_origins={os.environ['FRONTEND_URL']}))
    monkeypatch.setattr(preview.importlib, 'import_module', load_server)
    app = preview.create_app()
    sent = []
    async def send(message):
        sent.append(message)
    async def receive():
        return {'type': 'http.request', 'body': b''}
    await app({'type': 'http', 'method': 'POST', 'path': '/api/auth/refresh',
               'headers': [(b'host', b'salla-analytics.preview.emergentagent.com'),
                           (b'origin', preview.ORIGIN.encode()),
                           (b'cookie', b'refresh_token=test-fixture'),
                           (b'sec-fetch-site', b'same-origin')]}, receive, send)
    assert next(item for item in sent if item['type'] == 'http.response.start')['status'] == 204
